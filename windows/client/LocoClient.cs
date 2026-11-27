// loco-client : agent loco des postes Windows (service Windows « loco-client », compte SYSTEM).
//
// Contacte le serveur loco toutes les deux minutes avec son identifiant et son jeton (propres au poste,
// indépendants de son adresse IP), envoie l'inventaire (Windows Update, logiciels installés) et exécute les
// actions demandées depuis le portail : inventaire, mise à jour (Windows Update et logiciels du catalogue),
// installation d'un logiciel du catalogue, redémarrage, retrait de l'agent. Terminal interactif séparé, réservé aux administrateurs du portail.
// L'autorité HTTPS de loco est épinglée (empreinte SHA-256) sans modifier le magasin de certificats du poste.
//
// Réglages d'inscription (installeur MSI ou GPO) : HKLM\SOFTWARE\Policies\loco\Client puis HKLM\SOFTWARE\loco\Client,
// valeurs Server, Key, CaSha256. Après inscription : %ProgramData%\loco-client\client.json (SYSTEM, Administrateurs).
//   loco-client.exe              service
//   loco-client.exe run-once     un contact, en console (administrateur)
//   loco-client.exe status

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Diagnostics.Eventing.Reader;
using System.IO;
using System.Linq;
using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Security;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

[assembly: AssemblyTitle("loco-client")]
[assembly: AssemblyDescription("Agent du parc loco")]
[assembly: AssemblyProduct("loco")]

namespace Loco
{
    public static class Program
    {
        public static int Main(string[] args)
        {
            string cmd = args.Length > 0 ? args[0] : "";
            if (cmd == "run-once") return Agent.RunOnce(null) ? 0 : 1;
            if (cmd == "status") { Agent.PrintStatus(); return 0; }
            if (cmd != "") { Console.Error.WriteLine("Usage : loco-client.exe [run-once | status]"); return 2; }
            ServiceBase.Run(new ClientService());
            return 0;
        }
    }

    public class ClientService : ServiceBase
    {
        readonly ManualResetEvent stopping = new ManualResetEvent(false);
        Thread worker;
        RemoteControl remote;

        public ClientService()
        {
            ServiceName = "loco-client";
            CanStop = true;
            CanShutdown = true;
        }

        protected override void OnStart(string[] args)
        {
            // Redémarrage automatique du service s'il s'arrête anormalement.
            try { Agent.Detached("sc.exe", "failure loco-client reset= 86400 actions= restart/60000/restart/60000/restart/300000"); }
            catch (Exception) { }
            Agent.PruneMsiCacheSoon();
            worker = new Thread(Loop) { IsBackground = true, Name = "loco-client" };
            worker.Start();
            remote = new RemoteControl(stopping);
        }

        protected override void OnStop()
        {
            stopping.Set();
            DesktopIndicator.StopSessions();
            RequestAdditionalTime(30000);
            if (remote != null) remote.Join();
            if (worker != null) worker.Join(10000);
        }

        protected override void OnShutdown() { OnStop(); }

        void Loop()
        {
            // Premier contact peu après le démarrage (réseau prêt), puis à l'intervalle donné par le serveur — ou tout
            // de suite si le canal distant signale une action en attente (réveil). 0 = arrêt du service → sortir.
            int delay = 15000;
            var waits = new WaitHandle[] { stopping, RemoteControl.Checkin };
            while (WaitHandle.WaitAny(waits, delay) != 0)
            {
                try { DesktopIndicator.StartSessions(); Agent.RunOnce(stopping); }
                catch (Exception e) { Agent.Log(Lang.T("erreur : ", "error: ", "error: ") + e.Message); }
                delay = Agent.Interval * 1000;
            }
        }
    }

    public class LocoHttpException : Exception
    {
        public int Status;
        public LocoHttpException(int status) : base("HTTP " + status) { Status = status; }
    }

    public static class Agent
    {
        public static readonly string Version = typeof(Agent).Assembly.GetName().Version.ToString(3);
        static readonly string Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "loco-client");
        static readonly string ConfigPath = Path.Combine(Root, "client.json");
        static readonly string StatePath = Path.Combine(Root, "state.json");
        static readonly string CaPath = Path.Combine(Root, "ca.crt");
        static readonly string LogStatePath = Path.Combine(Root, "logs.json");
        static readonly string LogPath = Path.Combine(Root, "loco-client.log");
        const string SecurityCategory = "0fa1201d-4330-4fa8-8ae9-b877473b6441";   // Windows Update : Security Updates
        // Windows Update : « Upgrades », les nouvelles versions de Windows (Windows 11 depuis Windows 10, 24H2 → 25H2…).
        // Elles ne partent jamais avec les mises à jour : loco les annonce à part et ne les pose que sur demande.
        const string UpgradesCategory = "3689bdc8-b205-4af4-8d4a-a63924c5e9d5";
        const int OutputLimit = 60000;
        const int InstallerTimeout = 1800;
        public static int Interval = 120;
        // Premier contact depuis le démarrage du service (démarrage du poste, mise à jour de l'agent) :
        // inventaire refait, pour ne pas garder l'état d'avant un redémarrage (mises à jour installées depuis).
        static bool firstRun = true;
        static X509Certificate2 ca;                     // autorité loco (premier certificat du faisceau)
        static X509Certificate2Collection trust;        // faisceau : autorité loco + autorité d'entreprise éventuelle
        static readonly object runLock = new object();
        static readonly ThreadLocal<JavaScriptSerializer> Serializers = new ThreadLocal<JavaScriptSerializer>(
            () => new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 });
        static JavaScriptSerializer Json { get { return Serializers.Value; } }

        // ------------------------------------------------------------ journal et fichiers

        public static void Log(string text)
        {
            string line = DateTime.Now.ToString("s") + " " + text;
            try
            {
                Directory.CreateDirectory(Root);
                if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 1024 * 1024)
                    File.Copy(LogPath, LogPath + ".1", true);
                if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 1024 * 1024) File.Delete(LogPath);
                File.AppendAllText(LogPath, line + Environment.NewLine, new UTF8Encoding(false));
            }
            catch (Exception) { }
            if (Environment.UserInteractive) Console.WriteLine("loco-client : " + text);
        }

        static Dictionary<string, object> ReadJson(string path)
        {
            try { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)); }
            catch (Exception) { return null; }
        }

        // Écrit jusqu'au disque avant de remplacer l'ancien fichier : après une coupure de courant, on retrouve l'ancien
        // contenu ou le nouveau, jamais un fichier rempli de zéros (l'identité du poste est dans client.json).
        static void SaveJson(string path, object data)
        {
            string tmp = path + ".tmp";
            byte[] bytes = new UTF8Encoding(false).GetBytes(Json.Serialize(data));
            using (var f = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                f.Write(bytes, 0, bytes.Length);
                f.Flush(true);
            }
            if (File.Exists(path)) File.Replace(tmp, path, null); else File.Move(tmp, path);
        }

        public static void PruneMsiCacheSoon()
        {
            new Thread(() => { Thread.Sleep(60000); PruneMsiCache(); }) { IsBackground = true, Name = "loco-msi-cache" }.Start();
        }

        // MSI des versions précédentes, gardés jusqu'ici à chaque mise à jour (des dizaines) : seuls restent celui de la
        // version en marche et ceux d'une version plus récente (mise à jour en cours). Windows Installer garde sa propre
        // copie pour désinstaller ou réparer.
        static void PruneMsiCache()
        {
            try
            {
                var running = typeof(Agent).Assembly.GetName().Version;
                int removed = 0;
                foreach (var file in Directory.GetFiles(Root, "loco-client-*.msi"))
                {
                    System.Version v;
                    string name = Path.GetFileNameWithoutExtension(file).Substring("loco-client-".Length);
                    if (!System.Version.TryParse(name, out v) || Normalize(v) >= Normalize(running)) continue;
                    try { File.Delete(file); removed++; } catch (Exception) { }
                }
                if (removed > 0) Log(Lang.T("anciens installeurs retirés : ", "old installers removed: ", "instaladores antiguos eliminados: ") + removed);
            }
            catch (Exception) { }
        }

        static System.Version Normalize(System.Version v)
        {
            return new System.Version(v.Major, v.Minor, Math.Max(v.Build, 0), Math.Max(v.Revision, 0));
        }

        static string Str(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v != null ? Convert.ToString(v) : "";
        }

        // Dossier des réglages : SYSTEM et Administrateurs uniquement (le jeton du poste y est stocké).
        static void SecureRoot()
        {
            Directory.CreateDirectory(Root);
            var sec = new DirectorySecurity();
            sec.SetAccessRuleProtection(true, false);
            foreach (var sid in new[] { WellKnownSidType.LocalSystemSid, WellKnownSidType.BuiltinAdministratorsSid })
                sec.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null), FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            Directory.SetAccessControl(Root, sec);
        }

        // ------------------------------------------------------------ HTTPS avec autorité épinglée

        static bool CheckCertificate(object sender, X509Certificate cert, X509Chain chain, SslPolicyErrors errors)
        {
            if (trust == null) return errors == SslPolicyErrors.None;
            if ((errors & (SslPolicyErrors.RemoteCertificateNotAvailable | SslPolicyErrors.RemoteCertificateNameMismatch)) != 0) return false;
            using (var c = new X509Chain())
            {
                c.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                c.ChainPolicy.VerificationFlags = X509VerificationFlags.AllowUnknownCertificateAuthority;
                c.ChainPolicy.ExtraStore.AddRange(trust);
                if (!c.Build(new X509Certificate2(cert))) return false;
                // Ancre de la chaîne : l'une des autorités du faisceau épinglé (loco, ou l'autorité d'entreprise choisie).
                string anchor = c.ChainElements[c.ChainElements.Count - 1].Certificate.Thumbprint;
                foreach (X509Certificate2 t in trust) if (t.Thumbprint == anchor) return true;
                return false;
            }
        }

        // Faisceau PEM (un ou plusieurs certificats) ; repli sur un certificat DER isolé.
        static X509Certificate2Collection ReadCertificates(byte[] raw)
        {
            var col = new X509Certificate2Collection();
            string text = Encoding.ASCII.GetString(raw);
            foreach (Match m in Regex.Matches(text, "-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", RegexOptions.Singleline))
                col.Add(new X509Certificate2(Convert.FromBase64String(Regex.Replace(m.Groups[1].Value, "\\s", ""))));
            if (col.Count == 0) col.Add(new X509Certificate2(raw));
            return col;
        }

        static HttpWebRequest Request(string method, string url, string token, int timeout, RemoteCertificateValidationCallback check)
        {
            var r = (HttpWebRequest)WebRequest.Create(url);
            r.Method = method;
            r.Timeout = timeout;
            r.ReadWriteTimeout = timeout;
            r.ServicePoint.Expect100Continue = false;
            r.ServicePoint.UseNagleAlgorithm = false;
            r.Proxy = null;                      // serveur loco sur le réseau local
            r.UserAgent = "loco-client/" + Version;
            r.ServerCertificateValidationCallback = check ?? CheckCertificate;
            if (!string.IsNullOrEmpty(token)) r.Headers["Authorization"] = "Bearer " + token;
            return r;
        }

        static HttpWebResponse Send(HttpWebRequest r)
        {
            try { return (HttpWebResponse)r.GetResponse(); }
            catch (WebException e)
            {
                var resp = e.Response as HttpWebResponse;
                if (resp != null) throw new LocoHttpException((int)resp.StatusCode);
                throw;
            }
        }

        static Dictionary<string, object> Post(string server, string path, object data, string token, int timeout = 120000)
        {
            var r = Request("POST", server + path, token, timeout, null);
            r.ContentType = "application/json; charset=utf-8";
            byte[] body = new UTF8Encoding(false).GetBytes(Json.Serialize(data));
            r.ContentLength = body.Length;
            using (Stream s = r.GetRequestStream()) s.Write(body, 0, body.Length);
            using (var resp = Send(r))
            using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return Json.Deserialize<Dictionary<string, object>>(rd.ReadToEnd());
        }

        static readonly Regex ServerPattern = new Regex(@"^https://[A-Za-z0-9.\[\]:-]+(:[0-9]{1,5})?$");

        // Adresse habituelle du serveur, puis celles qu'il a annoncées (changement d'adresse prévu).
        static List<string> KnownServers(Dictionary<string, object> conf)
        {
            var urls = new List<string> { Str(conf, "server") };
            object v;
            if (conf.TryGetValue("servers", out v) && v is ArrayList) foreach (object u in (ArrayList)v) urls.Add(Convert.ToString(u));
            var known = new List<string>();
            foreach (string u in urls)
            {
                string url = (u ?? "").TrimEnd('/');
                if (ServerPattern.IsMatch(url) && !known.Contains(url) && known.Count < 8) known.Add(url);
            }
            return known;
        }

        // Contact du serveur. Si son adresse habituelle ne répond plus (réseau, certificat), les adresses annoncées sont
        // essayées ; la première qui répond avec un certificat de l'autorité loco épinglée devient l'adresse du poste.
        static Dictionary<string, object> Checkin(Dictionary<string, object> conf, Dictionary<string, object> data)
        {
            var servers = KnownServers(conf);
            data["servers"] = servers;
            Exception failure = null;
            foreach (string server in servers)
            {
                Dictionary<string, object> answer;
                try { answer = Post(server, "/agent/api/checkin", data, Str(conf, "token")); }
                catch (LocoHttpException) { throw; }            // le serveur a répondu : ce n'est pas une question d'adresse
                catch (Exception e) { if (failure == null) failure = e; continue; }
                var announced = new ArrayList();
                object v;
                if (answer.TryGetValue("servers", out v) && v is ArrayList)
                    foreach (object u in (ArrayList)v)
                    {
                        string url = Convert.ToString(u).TrimEnd('/');
                        if (ServerPattern.IsMatch(url) && !announced.Contains(url) && announced.Count < 8) announced.Add(url);
                    }
                bool moved = server != Str(conf, "server");
                object previous;
                conf.TryGetValue("servers", out previous);
                bool listChanged = announced.Count > 0 && !(previous is ArrayList && ((ArrayList)previous).Cast<object>().Select(Convert.ToString).SequenceEqual(announced.Cast<string>()));
                if (moved) Log(Lang.T("serveur joint à sa nouvelle adresse : ", "server reached at its new address: ", "servidor contactado en su nueva dirección: ") + server + Lang.T(" (au lieu de ", " (instead of ", " (en lugar de ") + Str(conf, "server") + ")");
                if (moved || listChanged)
                {
                    conf["server"] = server;
                    if (announced.Count > 0) conf["servers"] = announced;
                    SaveJson(ConfigPath, conf);
                }
                return answer;
            }
            throw failure ?? new Exception(Lang.T("aucune adresse de serveur connue", "no known server address", "ninguna dirección de servidor conocida"));
        }

        static void Download(string url, string path, RemoteCertificateValidationCallback check = null, Action<int> percent = null)
        {
            using (var resp = Send(Request("GET", url, null, 600000, check)))
            using (Stream s = resp.GetResponseStream())
            using (FileStream f = File.Create(path))
            {
                long length = resp.ContentLength, done = 0;
                var buffer = new byte[81920];
                int n;
                while ((n = s.Read(buffer, 0, buffer.Length)) > 0)
                {
                    f.Write(buffer, 0, n); done += n;
                    if (percent != null && length > 0) percent((int)Math.Min(100, 100 * done / length));
                }
            }
        }

        static string Sha256File(string path)
        {
            using (var sha = SHA256.Create())
            using (var f = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
        }

        static void LoadCa()
        {
            trust = null; ca = null;
            if (!File.Exists(CaPath)) return;
            try { trust = ReadCertificates(File.ReadAllBytes(CaPath)); }
            catch (Exception e) { Log(Lang.T("autorités de confiance illisibles : ", "unreadable trusted authorities: ", "autoridades de confianza ilegibles: ") + e.Message); return; }
            if (trust.Count > 0) ca = trust[0]; else trust = null;
        }

        // Faisceau d'autorités envoyé par le serveur (au fil du canal déjà épinglé) : remplace ca.crt s'il a changé.
        // Agent installé sans autorité épinglée (magasin du système) : rien à faire.
        static void ApplyTrust(Dictionary<string, object> answer)
        {
            object v;
            if (!answer.TryGetValue("trust", out v) || !(v is Dictionary<string, object>) || !File.Exists(CaPath)) return;
            var d = (Dictionary<string, object>)v;
            string pem = Str(d, "pem"), sha = Str(d, "sha256").ToLowerInvariant();
            if (pem == "" || sha == "" || sha == TrustSha()) return;
            byte[] raw = Encoding.UTF8.GetBytes(pem);
            string digest;
            using (var h = SHA256.Create()) digest = BitConverter.ToString(h.ComputeHash(raw)).Replace("-", "").ToLowerInvariant();
            if (digest != sha) return;
            try
            {
                var col = ReadCertificates(raw);
                if (col.Count == 0) return;
                string tmp = CaPath + ".new";
                File.WriteAllBytes(tmp, raw);
                File.Copy(tmp, CaPath, true);
                File.Delete(tmp);
                LoadCa();
                Log(Lang.T("autorités de confiance mises à jour (", "trusted authorities updated (", "autoridades de confianza actualizadas (") + col.Count + ")");
            }
            catch (Exception e) { Log(Lang.T("faisceau d'autorités refusé : ", "authority bundle refused: ", "conjunto de autoridades rechazado: ") + e.Message); }
        }

        static string TrustSha()
        {
            try { return File.Exists(CaPath) ? Sha256File(CaPath) : ""; } catch (Exception) { return ""; }
        }

        internal static Dictionary<string, object> RemoteExchange(object data)
        {
            var conf = ReadJson(ConfigPath);
            if (Str(conf, "token") == "") throw new IOException(Lang.T("Inscription du poste en attente", "Machine enrollment pending", "Inscripción del equipo pendiente"));
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            if (ca == null) LoadCa();
            return Post(Str(conf, "server"), "/agent/api/remote", data, Str(conf, "token"), 10000);
        }

        // Canal persistant (RemoteChannel) : même serveur, même jeton et même vérification du certificat que ci-dessus.
        internal static string RemoteKey()
        {
            var conf = ReadJson(ConfigPath);
            return Str(conf, "token") == "" ? "" : Str(conf, "server") + "\n" + Str(conf, "token");
        }
        internal static RemoteChannel RemoteConnect()
        {
            var conf = ReadJson(ConfigPath);
            if (Str(conf, "token") == "") throw new IOException(Lang.T("Inscription du poste en attente", "Machine enrollment pending", "Inscripción del equipo pendiente"));
            if (ca == null) LoadCa();
            return new RemoteChannel(Str(conf, "server"), Str(conf, "token"), CheckCertificate, Version);
        }
        internal static string ToJson(object data) { return Json.Serialize(data); }
        internal static Dictionary<string, object> FromJson(string text) { return Json.Deserialize<Dictionary<string, object>>(text); }

        // ------------------------------------------------------------ inscription

        const string InstallerSettings = @"SOFTWARE\loco\Client";

        static string Setting(string name, bool installerOnly = false)
        {
            foreach (var key in installerOnly ? new[] { InstallerSettings } : new[] { @"SOFTWARE\Policies\loco\Client", InstallerSettings })
            {
                using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(key))
                {
                    var v = k == null ? null : k.GetValue(name) as string;
                    if (!string.IsNullOrEmpty(v)) return v.Trim();
                }
            }
            return "";
        }

        // Autorité du serveur téléchargée une fois et vérifiée par l'empreinte fournie à l'installation.
        static void FetchCa(string server, string pin)
        {
            pin = pin.Replace(":", "").Replace(" ", "").ToLowerInvariant();
            string tmp = CaPath + ".new";
            Download(server + "/ca.crt", tmp, (s, c, ch, e) => true);   // contenu vérifié ci-dessous par l'empreinte
            // Faisceau : l'autorité loco en tête (celle de l'empreinte), puis l'autorité d'entreprise éventuelle.
            var cert = ReadCertificates(File.ReadAllBytes(tmp))[0];
            string digest;
            using (var sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(cert.RawData)).Replace("-", "").ToLowerInvariant();
            if (digest != pin) { File.Delete(tmp); throw new Exception(Lang.T("empreinte de l'autorité différente de celle attendue : inscription refusée", "authority fingerprint differs from the expected one: enrollment refused", "huella de la autoridad distinta de la esperada: inscripción rechazada")); }
            if (File.Exists(CaPath)) File.Delete(CaPath);
            File.Move(tmp, CaPath);
        }

        static Dictionary<string, object> Enroll(Dictionary<string, object> conf, bool installerOnly = false)
        {
            string key = Setting("Key", installerOnly), pin = Setting("CaSha256", installerOnly);
            // Server : une adresse, ou plusieurs séparées par des virgules, essayées dans l'ordre (nom DNS du portail, puis
            // IP). Un nom peut présenter le certificat d'une autorité d'entreprise absente du faisceau ; l'IP, celui de loco.
            var servers = Setting("Server", installerOnly).Split(',').Select(u => u.Trim().TrimEnd('/')).Where(u => ServerPattern.IsMatch(u)).Distinct().Take(4).ToList();
            if (servers.Count == 0 || key == "") return null;
            SecureRoot();
            string uid = Str(conf, "uid");
            if (uid == "") uid = Guid.NewGuid().ToString("N");
            var data = Identity();
            data["key"] = key;
            data["uid"] = uid;
            Dictionary<string, object> answer = null;
            string server = "";
            Exception failure = null;
            foreach (string candidate in servers)
            {
                try
                {
                    if (pin != "") FetchCa(candidate, pin); else if (File.Exists(CaPath)) File.Delete(CaPath);
                    LoadCa();
                    answer = Post(candidate, "/agent/api/enroll", data, null);
                    server = candidate;
                    break;
                }
                catch (LocoHttpException) { throw; }        // le serveur a répondu (clé refusée…) : pas une question d'adresse
                catch (Exception e)
                {
                    Log(Lang.T("serveur injoignable à ", "server unreachable at ", "servidor inaccesible en ") + candidate + " : " + e.Message);
                    failure = e;
                }
            }
            if (answer == null) throw failure ?? new Exception(Lang.T("aucune adresse de serveur", "no server address", "ninguna dirección de servidor"));
            // Identité retenue par le serveur (loco 2.144.0 et plus) : il en attribue une neuve si l'identifiant
            // présenté est déjà celui d'un poste inscrit dont on n'a pas le jeton.
            if (Str(answer, "uid") != "") uid = Str(answer, "uid");
            conf = new Dictionary<string, object> {
                { "server", server }, { "uid", uid }, { "token", Str(answer, "token") }, { "enrolled", DateTime.Now.ToString("s") } };
            var others = new ArrayList(servers.Where(u => u != server).ToList());
            if (others.Count > 0) conf["servers"] = others;      // en secours, essayées si l'adresse retenue ne répond plus
            SaveJson(ConfigPath, conf);
            SaveJson(StatePath, new Dictionary<string, object> { { "last_scan", 0 }, { "scan_hours", 6 }, { "rescan", true } });
            Log(Lang.T("poste inscrit auprès de ", "machine enrolled with ", "equipo inscrito en ") + server);
            ClearInstallerKey();
            return conf;
        }

        static void ClearInstallerKey()
        {
            // Ne conserver aucune clé locale une fois que le serveur accepte l'identité du poste.
            try
            {
                using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(InstallerSettings, true))
                    if (k != null && k.GetValue("Key") != null) k.DeleteValue("Key");
            }
            catch (Exception) { }
        }

        // ------------------------------------------------------------ poste

        // ------------------------------------------------------------ supervision

        [StructLayout(LayoutKind.Sequential)]
        struct FileTime { public uint Low, High; public ulong Value { get { return ((ulong)High << 32) | Low; } } }
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetSystemTimes(out FileTime idle, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll")]
        static extern ulong GetTickCount64();

        [StructLayout(LayoutKind.Sequential)]
        struct MemoryStatus { public uint Length, Load; public ulong TotalPhys, AvailPhys, TotalPageFile, AvailPageFile, TotalVirtual, AvailVirtual, AvailExtendedVirtual; }
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);

        // Processeur et réseau en moyenne depuis l'appel précédent ; une instance par usage (relevé des 2 min, direct),
        // chacune dans son propre fil : leurs états ne se mélangent pas.
        internal sealed class UsageSampler
        {
            ulong lastIdle, lastTotal;
            long lastRx = -1, lastTx = -1;
            DateTime last = DateTime.MinValue;

            internal void Reset() { last = DateTime.MinValue; lastRx = -1; lastTx = -1; }

            internal void Sample(Dictionary<string, object> m)
            {
                DateTime now = DateTime.UtcNow;
                double elapsed = (now - last).TotalSeconds;
                bool recent = elapsed > 0 && elapsed < 1800;
                try
                {
                    FileTime idle, kernel, user;
                    if (GetSystemTimes(out idle, out kernel, out user))
                    {
                        ulong total = kernel.Value + user.Value;       // le temps noyau inclut le temps inactif
                        if (recent && total > lastTotal)
                            m["cpu"] = Math.Round(100.0 * (1 - (double)(idle.Value - lastIdle) / (total - lastTotal)), 1);
                        lastIdle = idle.Value; lastTotal = total;
                    }
                }
                catch (Exception) { }
                try
                {
                    long rx = 0, tx = 0;
                    foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
                    {
                        if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                        var stats = nic.GetIPv4Statistics();
                        rx += stats.BytesReceived; tx += stats.BytesSent;
                    }
                    if (recent && lastRx >= 0 && rx >= lastRx && tx >= lastTx)
                    {
                        m["net_rx"] = (long)((rx - lastRx) / elapsed);
                        m["net_tx"] = (long)((tx - lastTx) / elapsed);
                    }
                    lastRx = rx; lastTx = tx;
                }
                catch (Exception) { }
                last = now;
            }
        }

        static readonly UsageSampler checkinSampler = new UsageSampler();

        // Relevé léger du mode direct (fiche du poste regardée) : processeur, mémoire, réseau.
        internal static Dictionary<string, object> LiveMetrics(UsageSampler sampler)
        {
            var m = new Dictionary<string, object>();
            sampler.Sample(m);
            var status = new MemoryStatus { Length = (uint)Marshal.SizeOf(typeof(MemoryStatus)) };
            if (GlobalMemoryStatusEx(ref status))
            {
                m["mem_total"] = (long)status.TotalPhys;
                m["mem_used"] = (long)(status.TotalPhys - status.AvailPhys);
            }
            return m;
        }

        // Relevé joint à chaque contact : processeur et réseau en moyenne depuis le contact précédent, mémoire, fichier
        // d'échange, disques fixes et durée depuis le démarrage.
        // Cartes graphiques et leur usage : NVIDIA par nvidia-smi (nom, occupation, mémoire, température), sinon le nom par
        // WMI et l'occupation par le compteur de performance « GPU Engine » (toutes marques). Liste vide si rien de pertinent.
        static List<object> Gpus()
        {
            var cards = new List<object>();
            try
            {
                string output;
                int code = RunCapture("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits", 8, out output, null);
                if (code == 0)
                    foreach (string line in (output ?? "").Replace("\r", "").Split('\n'))
                    {
                        string[] p = line.Split(',');
                        if (p.Length < 5 || p[0].Trim() == "") continue;
                        var card = new Dictionary<string, object> { { "name", p[0].Trim() } };
                        int v;
                        if (int.TryParse(p[1].Trim(), out v)) card["usage"] = v;
                        if (int.TryParse(p[2].Trim(), out v)) card["mem_used"] = (long)v * 1048576;
                        if (int.TryParse(p[3].Trim(), out v)) card["mem_total"] = (long)v * 1048576;
                        if (int.TryParse(p[4].Trim(), out v)) card["temp"] = v;
                        cards.Add(card);
                        if (cards.Count >= 8) break;
                    }
            }
            catch (Exception) { }
            if (cards.Count > 0) return cards;
            double usage = GpuEngineUsage();                 // -1 si indisponible
            try
            {
                using (var q = new ManagementObjectSearcher("SELECT Name FROM Win32_VideoController"))
                    foreach (ManagementObject o in q.Get())
                    {
                        string name = Convert.ToString(o["Name"]);
                        if (string.IsNullOrEmpty(name) || name.IndexOf("Basic Display", StringComparison.OrdinalIgnoreCase) >= 0
                            || name.IndexOf("Remote Display", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                        var card = new Dictionary<string, object> { { "name", name } };
                        if (usage >= 0) card["usage"] = (int)Math.Round(usage);
                        cards.Add(card);
                        if (cards.Count >= 8) break;
                    }
            }
            catch (Exception) { }
            return cards;
        }

        // Occupation globale du GPU par le compteur « GPU Engine \ Utilization Percentage » (deux relevés à 150 ms, moteur
        // 3D sommé puis plafonné à 100). -1 si le compteur n'existe pas (pas de carte ou compteurs absents).
        static double GpuEngineUsage()
        {
            try
            {
                var category = new System.Diagnostics.PerformanceCounterCategory("GPU Engine");
                var counters = new List<System.Diagnostics.PerformanceCounter>();
                foreach (string inst in category.GetInstanceNames())
                    if (inst.IndexOf("engtype_3D", StringComparison.OrdinalIgnoreCase) >= 0)
                        counters.Add(new System.Diagnostics.PerformanceCounter("GPU Engine", "Utilization Percentage", inst, true));
                if (counters.Count == 0) return -1;
                foreach (var c in counters) { try { c.NextValue(); } catch (Exception) { } }
                System.Threading.Thread.Sleep(150);
                double sum = 0;
                foreach (var c in counters) { try { sum += c.NextValue(); } catch (Exception) { } c.Dispose(); }
                return Math.Min(100, sum);
            }
            catch (Exception) { return -1; }
        }

        static Dictionary<string, object> Metrics()
        {
            var m = new Dictionary<string, object> { { "cores", Environment.ProcessorCount } };
            checkinSampler.Sample(m);
            try
            {
                using (var q = new ManagementObjectSearcher("SELECT TotalVisibleMemorySize, FreePhysicalMemory FROM Win32_OperatingSystem"))
                    foreach (ManagementObject o in q.Get())
                    {
                        long total = Convert.ToInt64(o["TotalVisibleMemorySize"]) * 1024, free = Convert.ToInt64(o["FreePhysicalMemory"]) * 1024;
                        m["mem_total"] = total; m["mem_used"] = Math.Max(0, total - free);
                    }
                long swapTotal = 0, swapUsed = 0;
                using (var q = new ManagementObjectSearcher("SELECT AllocatedBaseSize, CurrentUsage FROM Win32_PageFileUsage"))
                    foreach (ManagementObject o in q.Get())
                    {
                        swapTotal += Convert.ToInt64(o["AllocatedBaseSize"]) * 1024 * 1024;
                        swapUsed += Convert.ToInt64(o["CurrentUsage"]) * 1024 * 1024;
                    }
                m["swap_total"] = swapTotal; m["swap_used"] = swapUsed;
            }
            catch (Exception) { }
            try { m["uptime"] = (long)(GetTickCount64() / 1000); } catch (Exception) { }
            var disks = new List<object>();
            try
            {
                foreach (var d in DriveInfo.GetDrives())
                {
                    if (disks.Count >= 16 || d.DriveType != DriveType.Fixed || !d.IsReady) continue;
                    disks.Add(new Dictionary<string, object> { { "mount", d.Name.TrimEnd('\\') }, { "fs", d.DriveFormat },
                        { "total", d.TotalSize }, { "used", d.TotalSize - d.TotalFreeSpace } });
                }
            }
            catch (Exception) { }
            m["disks"] = disks;
            try { var gpus = Gpus(); if (gpus.Count > 0) m["gpus"] = gpus; } catch (Exception) { }
            return m;
        }

        static Dictionary<string, object> Identity()
        {
            string caption = "Windows";
            try
            {
                using (var s = new ManagementObjectSearcher("SELECT Caption, Version FROM Win32_OperatingSystem"))
                    foreach (ManagementObject o in s.Get())
                        caption = Convert.ToString(o["Caption"]).Trim() + " (" + Convert.ToString(o["Version"]) + ")";
            }
            catch (Exception) { }
            // Carte de la route par défaut d'abord (Hyper-V, WSL ou Docker Desktop passent sinon devant), IPv4 avant IPv6.
            var found = new List<KeyValuePair<int, string>>();
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                var props = nic.GetIPProperties();
                bool routed = props.GatewayAddresses.Any(g => !g.Address.Equals(IPAddress.Any) && !g.Address.Equals(IPAddress.IPv6Any));
                foreach (var a in props.UnicastAddresses)
                {
                    var ip = a.Address;
                    if (IPAddress.IsLoopback(ip) || ip.IsIPv6LinkLocal || ip.ToString().StartsWith("169.254.")) continue;
                    found.Add(new KeyValuePair<int, string>((routed ? 0 : 2) + (ip.AddressFamily == AddressFamily.InterNetwork ? 0 : 1), ip.ToString().Split('%')[0]));
                }
            }
            var addresses = found.OrderBy(f => f.Key).Select(f => f.Value).Take(16).ToList();
            var networks = new List<string>();
            var cards = NetworkCards(networks);
            return new Dictionary<string, object> {
                { "hostname", Environment.MachineName.ToLowerInvariant() }, { "agent_version", Version },
                { "addresses", addresses }, { "trust", TrustSha() }, { "cards", cards }, { "networks", networks.Take(16).ToList() }, { "wake_error", wakeError },
                { "os", new Dictionary<string, object> { { "family", "windows" }, { "name", caption } } }, { "wazuh", WazuhInfo() } };
        }

        static readonly Regex VirtualNic = new Regex("virtual|vpn|tap-|wintun|wireguard|tailscale|zerotier|bluetooth|npcap|loopback|wsl|^vethernet", RegexOptions.IgnoreCase);
        static readonly Regex WakeMac = new Regex("^[0-9a-f]{2}(:[0-9a-f]{2}){5}$");

        // Cartes physiques (Ethernet, Wi-Fi), branchées ou non : le réveil à distance dépend de ce qu'elles sont à l'extinction.
        static List<Dictionary<string, object>> NetworkCards(List<string> networks)
        {
            var wake = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var usb = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                // MSFT_NetAdapterPowerManagementSettingData.WakeOnMagicPacket : 0 non géré, 1 désactivé, 2 activé, 3 inactif.
                using (var s = new ManagementObjectSearcher(@"root\StandardCimv2", "SELECT Name, WakeOnMagicPacket FROM MSFT_NetAdapterPowerManagementSettingData"))
                    foreach (ManagementObject o in s.Get())
                    {
                        uint state = Convert.ToUInt32(o["WakeOnMagicPacket"]);
                        wake[Convert.ToString(o["Name"])] = state == 2 ? "on" : state == 0 ? "none" : "off";
                    }
            }
            catch (Exception) { }
            try
            {
                using (var s = new ManagementObjectSearcher("SELECT GUID, PNPDeviceID FROM Win32_NetworkAdapter WHERE GUID IS NOT NULL"))
                    foreach (ManagementObject o in s.Get())
                        if (Convert.ToString(o["PNPDeviceID"]).StartsWith("USB\\", StringComparison.OrdinalIgnoreCase)) usb.Add(Convert.ToString(o["GUID"]));
            }
            catch (Exception) { }
            var cards = new List<Dictionary<string, object>>();
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                var type = nic.NetworkInterfaceType;
                bool wifi = type == NetworkInterfaceType.Wireless80211;
                bool wired = type == NetworkInterfaceType.Ethernet || type == NetworkInterfaceType.Ethernet3Megabit || type == NetworkInterfaceType.FastEthernetFx
                    || type == NetworkInterfaceType.FastEthernetT || type == NetworkInterfaceType.GigabitEthernet;
                if ((!wifi && !wired) || VirtualNic.IsMatch(nic.Name + " " + nic.Description)) continue;
                var bytes = nic.GetPhysicalAddress().GetAddressBytes();
                if (bytes.Length != 6) continue;
                bool up = nic.OperationalStatus == OperationalStatus.Up;
                var addresses = new List<string>();
                if (up)
                    foreach (var a in nic.GetIPProperties().UnicastAddresses)
                        if (a.Address.AddressFamily == AddressFamily.InterNetwork && !a.Address.ToString().StartsWith("169.254."))
                            addresses.Add(a.Address + "/" + a.PrefixLength);
                networks.AddRange(addresses);
                string state;
                if (!wake.TryGetValue(nic.Name, out state)) state = "";
                cards.Add(new Dictionary<string, object> {
                    { "name", nic.Name }, { "mac", string.Join(":", bytes.Select(b => b.ToString("x2"))) }, { "kind", wifi ? "wifi" : "ethernet" },
                    { "up", up }, { "usb", usb.Contains(nic.Id) }, { "wol", wifi ? "" : state }, { "networks", addresses } });
            }
            return cards.Take(16).ToList();
        }

        static string wakeError = "";
        static DateTime wakeTried = DateTime.MinValue;
        static bool wakeTriedFor;

        // Réveil demandé depuis le portail. Sous Windows le réglage persiste, et la commande peut relancer la carte : une tentative par jour au plus.
        static void ApplyWake(Dictionary<string, object> answer)
        {
            object v;
            if (!answer.TryGetValue("wake", out v) || !(v is Dictionary<string, object>)) { wakeError = ""; return; }
            bool enabled = Convert.ToBoolean(((Dictionary<string, object>)v)["enabled"]);
            var todo = NetworkCards(new List<string>()).Where(c => Convert.ToString(c["kind"]) == "ethernet" && Convert.ToString(c["wol"]) == (enabled ? "off" : "on")).ToList();
            if (todo.Count == 0) { wakeError = ""; wakeTried = DateTime.MinValue; return; }
            if (enabled == wakeTriedFor && (DateTime.UtcNow - wakeTried).TotalHours < 24) return;
            wakeTried = DateTime.UtcNow;
            wakeTriedFor = enabled;
            var errors = new List<string>();
            foreach (var card in todo)
            {
                string name = Convert.ToString(card["name"]), output;
                string script = "Set-NetAdapterPowerManagement -Name '" + name.Replace("'", "''") + "' -WakeOnMagicPacket " + (enabled ? "Enabled" : "Disabled") + " -ErrorAction Stop";
                string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
                if (RunCapture("powershell.exe", "-NoProfile -NonInteractive -EncodedCommand " + encoded, 90, out output) != 0)
                    errors.Add(name + " : " + (output ?? "").Trim());
            }
            string all = string.Join(" ; ", errors);
            wakeError = all.Length > 200 ? all.Substring(0, 200) : all;
        }

        // Diffuse des paquets magiques pour des postes éteints voisins, seulement sur un réseau de ce poste.
        static bool RelayWake(Dictionary<string, object> cmd, out string output)
        {
            object value;
            var order = cmd.TryGetValue("wake", out value) ? value as Dictionary<string, object> : null;
            string broadcast = order != null && order.ContainsKey("broadcast") ? Convert.ToString(order["broadcast"]) : "";
            var macs = new List<string>();
            if (order != null && order.ContainsKey("macs") && order["macs"] is IEnumerable)
                foreach (object one in (IEnumerable)order["macs"])
                    if (WakeMac.IsMatch(Convert.ToString(one)) && macs.Count < 64) macs.Add(Convert.ToString(one));
            IPAddress source = null;
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up) continue;
                foreach (var a in nic.GetIPProperties().UnicastAddresses)
                {
                    if (a.Address.AddressFamily != AddressFamily.InterNetwork || a.IPv4Mask == null) continue;
                    byte[] ip = a.Address.GetAddressBytes(), mask = a.IPv4Mask.GetAddressBytes(), edge = new byte[4];
                    for (int i = 0; i < 4; i++) edge[i] = (byte)(ip[i] | ~mask[i]);
                    if (new IPAddress(edge).ToString() == broadcast) source = a.Address;
                }
            }
            if (source == null || macs.Count == 0)
            {
                output = Lang.T("Ce poste n'est plus sur ce réseau, ou l'adresse à réveiller est invalide.", "This machine is no longer on this network, or the address to wake is invalid.",
                                "Este equipo ya no está en esta red, o la dirección que despertar no es válida.");
                return false;
            }
            using (var udp = new UdpClient(new IPEndPoint(source, 0)))
            {
                udp.EnableBroadcast = true;
                foreach (string mac in macs)
                {
                    var packet = new byte[102];
                    for (int i = 0; i < 6; i++) packet[i] = 0xff;
                    var parts = mac.Split(':');
                    for (int i = 0; i < 96; i++) packet[6 + i] = Convert.ToByte(parts[i % 6], 16);
                    foreach (string target in new[] { broadcast, "255.255.255.255" })
                        for (int round = 0; round < 3; round++) udp.Send(packet, packet.Length, target, 9);
                }
            }
            output = Lang.T("Paquet de réveil envoyé depuis ", "Wake packet sent from ", "Paquete de activación enviado desde ") + source + " (" + string.Join(", ", macs) + ").";
            return true;
        }

        static List<Dictionary<string, object>> InstalledApps()
        {
            var apps = new List<Dictionary<string, object>>();
            var seen = new HashSet<string>();
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view).OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
                {
                    if (root == null) continue;
                    foreach (string name in root.GetSubKeyNames())
                    {
                        using (var k = root.OpenSubKey(name))
                        {
                            var display = k == null ? null : k.GetValue("DisplayName") as string;
                            if (string.IsNullOrEmpty(display) || Convert.ToString(k.GetValue("SystemComponent")) == "1" || k.GetValue("ParentKeyName") != null) continue;
                            string version = Convert.ToString(k.GetValue("DisplayVersion") ?? "");
                            if (!seen.Add(display + "|" + version)) continue;
                            apps.Add(new Dictionary<string, object> { { "name", display }, { "version", version },
                                { "publisher", Convert.ToString(k.GetValue("Publisher") ?? "") } });
                        }
                    }
                }
            }
            return apps;
        }

        static bool RebootRequired()
        {
            try
            {
                dynamic info = Activator.CreateInstance(Type.GetTypeFromProgID("Microsoft.Update.SystemInfo"));
                if ((bool)info.RebootRequired) return true;
            }
            catch (Exception) { }
            using (var k = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"))
                return k != null;
        }

        static dynamic NewSession()
        {
            dynamic session = Activator.CreateInstance(Type.GetTypeFromProgID("Microsoft.Update.Session"));
            session.ClientApplicationID = "loco-client";
            return session;
        }

        static bool IsSecurity(dynamic u)
        {
            string sev = Convert.ToString(u.MsrcSeverity);
            if (sev == "Critical" || sev == "Important") return true;
            foreach (dynamic c in u.Categories)
                if (string.Equals(Convert.ToString(c.CategoryID), SecurityCategory, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        static bool IsUpgrade(dynamic u)
        {
            try
            {
                foreach (dynamic c in u.Categories)
                    if (string.Equals(Convert.ToString(c.CategoryID), UpgradesCategory, StringComparison.OrdinalIgnoreCase)) return true;
            }
            catch (Exception) { }
            return false;
        }

        static readonly Regex UpgradeVersion = new Regex(@"\b(\d{2}H\d)\b", RegexOptions.IgnoreCase);

        // Nouvelle version de Windows proposée par Windows Update, pour l'inventaire : la plus récente s'il y en a plusieurs.
        static Dictionary<string, object> ReleaseEntry(dynamic u)
        {
            string title = Convert.ToString(u.Title);
            Match m = UpgradeVersion.Match(title);
            return new Dictionary<string, object> { { "kind", "windows" }, { "name", title }, { "id", Convert.ToString(u.Identity.UpdateID) },
                { "version", m.Success ? m.Groups[1].Value.ToUpperInvariant() : "" } };
        }

        static bool IsDriver(dynamic u)
        {
            try { return Convert.ToInt32(u.Type) == 2; }    // UpdateType : 1 = logiciel, 2 = pilote
            catch (Exception) { return false; }
        }

        // Logiciels ET pilotes manquants : les pilotes (carte graphique NVIDIA/AMD, chipset…) distribués par Windows Update
        // sont de type « Driver », exclus d'une recherche « Type='Software' ». On les ajoute pour qu'ils soient vus et posés.
        const string UpdateCriteria = "IsInstalled=0 and IsHidden=0 and (Type='Software' or Type='Driver')";
        // Certains postes (build récente, pilotes désactivés par stratégie, source WU redirigée…) refusent le critère
        // « Driver » avec 0x80240032 (WU_E_INVALID_CRITERIA). On retombe alors sur les logiciels seuls, pour inventorier
        // et poser quand même les mises à jour logicielles au lieu de ne rien remonter.
        const string UpdateCriteriaSoftware = "IsInstalled=0 and IsHidden=0 and Type='Software'";

        static dynamic SearchUpdates(dynamic searcher)
        {
            try { return searcher.Search(UpdateCriteria); }
            catch (Exception e)
            {
                if (unchecked((uint)e.HResult) != 0x80240032) throw;   // seule l'erreur « critère invalide » déclenche le repli
                return searcher.Search(UpdateCriteriaSoftware);
            }
        }

        static Dictionary<string, object> UpdateEntry(dynamic u)
        {
            var kbs = new List<string>();
            foreach (object kb in u.KBArticleIDs) kbs.Add("KB" + kb);
            return new Dictionary<string, object> { { "name", Convert.ToString(u.Title) },
                { "version", string.Join(" ", kbs) }, { "security", IsSecurity(u) }, { "driver", IsDriver(u) },
                { "id", Convert.ToString(u.Identity.UpdateID) } };
        }

        // ------------------------------------------------------------ mises à jour bloquées dans loco

        // Motifs reçus du serveur et mises à jour masquées par loco, gardés sur disque : un service qui redémarre refait
        // son inventaire avant son premier contact, et ne doit pas démasquer ce qui reste bloqué.
        static readonly string BlocksPath = Path.Combine(Root, "update-blocks.json");
        static List<string> blockPatterns;
        static List<Regex> blockRules;
        static HashSet<string> hiddenUpdates;

        static void SetBlockRules(List<string> patterns)
        {
            blockPatterns = patterns;
            blockRules = patterns.Select(p => new Regex("^" + string.Join(".*", p.Split('*').Select(Regex.Escape)) + "$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)).ToList();
        }

        static void LoadBlocks()
        {
            if (blockRules != null) return;
            var patterns = new List<string>();
            hiddenUpdates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                if (File.Exists(BlocksPath))
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(BlocksPath));
                    foreach (object one in (data["patterns"] as IEnumerable) ?? new ArrayList()) patterns.Add(Convert.ToString(one));
                    foreach (object one in (data["hidden"] as IEnumerable) ?? new ArrayList()) hiddenUpdates.Add(Convert.ToString(one));
                }
            }
            catch (Exception e) { Log(Lang.T("blocages illisibles : ", "unreadable blocks: ", "bloqueos ilegibles: ") + e.Message); }
            SetBlockRules(patterns);
        }

        static void SaveBlocks()
        {
            try
            {
                File.WriteAllText(BlocksPath, new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                    { "patterns", blockPatterns }, { "hidden", hiddenUpdates.ToList() } }));
            }
            catch (Exception e) { Log(Lang.T("blocages non enregistrés : ", "blocks not saved: ", "bloqueos no guardados: ") + e.Message); }
        }

        // Réappliqués à chaque contact. Vrai si la liste a changé : l'inventaire suivant masque ou démasque ce qu'il faut.
        static bool ApplyUpdateBlocks(Dictionary<string, object> answer)
        {
            object v;
            if (!answer.TryGetValue("update_blocks", out v) || !(v is Dictionary<string, object>)) return false;   // serveur plus ancien : ne rien changer
            LoadBlocks();
            var patterns = new List<string>();
            object list;
            if (((Dictionary<string, object>)v).TryGetValue("patterns", out list) && list is IEnumerable)
                foreach (object one in (IEnumerable)list)
                {
                    string pattern = Convert.ToString(one);
                    if (pattern.Length > 0 && pattern.Length <= 200) patterns.Add(pattern);
                }
            if (patterns.SequenceEqual(blockPatterns)) return false;
            SetBlockRules(patterns);
            SaveBlocks();
            return true;
        }

        static bool IsBlocked(dynamic u)
        {
            LoadBlocks();
            if (blockRules.Count == 0) return false;
            var texts = new List<string>();
            texts.Add(Convert.ToString(u.Title));
            foreach (object kb in u.KBArticleIDs) texts.Add("KB" + kb);
            return texts.Any(text => blockRules.Any(rule => rule.IsMatch(text)));
        }

        // Masquée (comme l'outil « Afficher ou masquer des mises à jour » de Microsoft) : Windows ne l'installe plus seul.
        // Windows refuse de masquer certaines mises à jour imposées : loco ne les installe pas pour autant.
        static void HideUpdate(dynamic u)
        {
            try
            {
                string id = Convert.ToString(u.Identity.UpdateID);
                if (!(bool)u.IsHidden) u.IsHidden = true;
                if (hiddenUpdates.Add(id)) SaveBlocks();
            }
            catch (Exception e) { Log(Lang.T("mise à jour bloquée non masquée : ", "blocked update not hidden: ", "actualización bloqueada no ocultada: ") + e.Message); }
        }

        // Les masquées n'apparaissent plus dans la recherche habituelle : le portail doit les voir pour les signaler bloquées.
        // Celles qui ne sont plus bloquées sont rendues à Windows ; celles masquées à la main sur le poste ne sont jamais touchées.
        static void AddHiddenUpdates(dynamic searcher, List<Dictionary<string, object>> updates)
        {
            LoadBlocks();
            if (hiddenUpdates.Count == 0) return;
            try
            {
                var seen = new HashSet<string>(updates.Select(entry => Convert.ToString(entry["id"])), StringComparer.OrdinalIgnoreCase);
                var kept = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                dynamic hidden = searcher.Search("IsInstalled=0 and IsHidden=1");
                foreach (dynamic u in hidden.Updates)
                {
                    string id = Convert.ToString(u.Identity.UpdateID);
                    if (!hiddenUpdates.Contains(id)) continue;
                    if (IsBlocked(u)) kept.Add(id);
                    else u.IsHidden = false;
                    if (seen.Add(id)) updates.Add(UpdateEntry(u));
                }
                hiddenUpdates = kept;
                SaveBlocks();
            }
            catch (Exception e) { Log(Lang.T("mises à jour masquées illisibles : ", "cannot read hidden updates: ", "actualizaciones ocultas ilegibles: ") + e.Message); }
        }

        static void ReleaseHiddenUpdates()
        {
            LoadBlocks();
            if (hiddenUpdates.Count == 0) return;
            dynamic hidden = NewSession().CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=1");
            foreach (dynamic u in hidden.Updates)
                if (hiddenUpdates.Contains(Convert.ToString(u.Identity.UpdateID))) u.IsHidden = false;
        }

        static Dictionary<string, object> Inventory()
        {
            var updates = new List<Dictionary<string, object>>();
            Dictionary<string, object> release = null;
            string error = "";
            try
            {
                dynamic searcher = NewSession().CreateUpdateSearcher();
                dynamic result = SearchUpdates(searcher);
                foreach (dynamic u in result.Updates)
                {
                    if (IsUpgrade(u) && !IsBlocked(u))
                    {
                        var entry = ReleaseEntry(u);
                        if (release == null || string.CompareOrdinal(Convert.ToString(entry["version"]), Convert.ToString(release["version"])) > 0) release = entry;
                        continue;
                    }
                    updates.Add(UpdateEntry(u));
                    if (IsBlocked(u)) HideUpdate(u);
                }
                AddHiddenUpdates(searcher, updates);
            }
            catch (Exception e) { error = Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + e.Message; }
            var inv = new Dictionary<string, object> { { "updates", updates }, { "apps", InstalledApps() },
                { "reboot_required", RebootRequired() }, { "error", error } };
            if (release != null) inv["release"] = release;
            // Caractéristiques et configuration matérielle (fiche du poste), comme l'agent Linux.
            try { inv["system"] = HardwareInfo.SystemInfo(); } catch (Exception e) { Log("caractéristiques : " + e.Message); }
            try { inv["hardware"] = HardwareInfo.Hardware(); } catch (Exception e) { Log("matériel : " + e.Message); }
            string note;
            inv["software"] = WingetInstalled(out note);
            if (note != "") inv["software_note"] = note;
            return inv;
        }

        // ------------------------------------------------------------ logiciels hors catalogue (winget)

        static readonly Regex WingetIdRe = new Regex(@"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$");

        // Logiciels que winget relève mais ne sait pas mettre à jour sur ce poste (introuvable pour la mise à jour, ou rien
        // d'applicable) : retenus avec la version alors installée, et plus remontés tant que cette version ne change pas.
        static readonly string WingetSkipPath = Path.Combine(Root, "winget-skip.json");
        static Dictionary<string, string> lastExport = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        static Dictionary<string, string> LoadWingetSkip()
        {
            var skip = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                var data = File.Exists(WingetSkipPath) ? new JavaScriptSerializer().DeserializeObject(File.ReadAllText(WingetSkipPath)) as Dictionary<string, object> : null;
                if (data != null) foreach (var kv in data) skip[kv.Key] = Convert.ToString(kv.Value);
            }
            catch (Exception) { }
            return skip;
        }

        static void SaveWingetSkip(Dictionary<string, string> skip)
        {
            try { File.WriteAllText(WingetSkipPath, new JavaScriptSerializer().Serialize(skip)); }
            catch (Exception e) { Log("winget-skip.json : " + e.Message); }
        }

        // Fermer de force un logiciel pour qu'une mise à jour winget puisse remplacer ses fichiers verrouillés (sans confirmation).
        const int PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(int access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder name, ref int size);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        // Dossier d'installation d'un paquet winget : son nom (winget list) retrouvé dans la base des programmes installés.
        static string InstallDir(string wingetId, string winget, string libraries)
        {
            try
            {
                string outp;
                RunCapture(winget, "list --id \"" + wingetId + "\" --exact --disable-interactivity --accept-source-agreements", 60, out outp, libraries);
                string name = null;
                foreach (string raw in (outp ?? "").Split('\n'))
                {
                    string line = raw.Replace("\r", "");
                    int at = line.IndexOf(wingetId, StringComparison.OrdinalIgnoreCase);
                    if (at > 0) { name = line.Substring(0, at).Trim(); break; }
                }
                if (string.IsNullOrEmpty(name)) return "";
                // 1) InstallLocation de la base des programmes, quand elle est renseignée.
                foreach (var vw in new[] { RegistryView.Registry64, RegistryView.Registry32 })
                    using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, vw).OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
                    {
                        if (root == null) continue;
                        foreach (string sub in root.GetSubKeyNames())
                            using (var k = root.OpenSubKey(sub))
                            {
                                if (k == null) continue;
                                string dn = k.GetValue("DisplayName") as string;
                                string loc = k.GetValue("InstallLocation") as string;
                                if (dn != null && loc != null && string.Equals(dn.Trim(), name, StringComparison.OrdinalIgnoreCase))
                                {
                                    string d = loc.Trim().TrimEnd('\\');
                                    if (SafeAppDir(d)) return d;
                                }
                            }
                    }
                // 2) Beaucoup de ces logiciels n'ont pas d'InstallLocation (installés dans un profil) : le raccourci du menu
                //    Démarrer pointe sur leur exécutable, ce qui donne leur dossier.
                string exe = ShortcutTarget(name);
                if (!string.IsNullOrEmpty(exe) && File.Exists(exe))
                {
                    string d = Path.GetDirectoryName(exe);
                    if (SafeAppDir(d)) return d;
                }
            }
            catch (Exception) { }
            return "";
        }

        // Cible (exécutable) du raccourci « <name>*.lnk » des menus Démarrer (tous les utilisateurs et chaque profil), ou "".
        static string ShortcutTarget(string name)
        {
            var menus = new List<string>();
            try { menus.Add(Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu)); } catch (Exception) { }
            try { menus.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), @"System32\config\systemprofile\AppData\Roaming\Microsoft\Windows\Start Menu")); } catch (Exception) { }
            try
            {
                string users = Path.Combine(Environment.GetEnvironmentVariable("SystemDrive") ?? "C:", "Users");
                if (Directory.Exists(users))
                    foreach (string u in Directory.GetDirectories(users))
                        menus.Add(Path.Combine(u, @"AppData\Roaming\Microsoft\Windows\Start Menu"));
            }
            catch (Exception) { }
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return "";
                object shell = Activator.CreateInstance(shellType);
                foreach (string menu in menus)
                {
                    if (string.IsNullOrEmpty(menu) || !Directory.Exists(menu)) continue;
                    string[] links;
                    try { links = Directory.GetFiles(menu, name + "*.lnk", SearchOption.AllDirectories); }
                    catch (Exception) { continue; }
                    foreach (string lnk in links)
                        try
                        {
                            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnk });
                            string target = shortcut.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, null, shortcut, null) as string;
                            if (!string.IsNullOrEmpty(target) && target.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) && File.Exists(target))
                                return target;
                        }
                        catch (Exception) { }
                }
            }
            catch (Exception) { }
            return "";
        }

        // Un dossier d'application assez précis pour qu'on puisse y fermer des processus sans risque : au moins deux
        // niveaux sous la racine du disque, et jamais un dossier racine partagé (Program Files, Windows, C:\\...).
        static bool SafeAppDir(string d)
        {
            if (string.IsNullOrEmpty(d) || d.Length < 6 || d.IndexOf(':') != 1) return false;
            if (d.TrimEnd('\\').Split('\\').Length < 3) return false;
            foreach (var bad in new[] {
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                Environment.GetFolderPath(Environment.SpecialFolder.System) })
                if (!string.IsNullOrEmpty(bad) && string.Equals(d.TrimEnd('\\'), bad.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)) return false;
            return true;
        }

        // Tue les processus lancés depuis ce dossier (et ses sous-dossiers) ; renvoie le nombre fermé. Chemin obtenu par
        // QueryFullProcessImageName (fiable quelle que soit l'architecture du processus, en SYSTEM).
        static int CloseApp(string dir)
        {
            if (string.IsNullOrEmpty(dir)) return 0;
            string prefix = dir.EndsWith("\\") ? dir : dir + "\\";
            int killed = 0;
            int self = Process.GetCurrentProcess().Id;
            foreach (var proc in Process.GetProcesses())
            {
                IntPtr h = IntPtr.Zero;
                try
                {
                    if (proc.Id == self) continue;
                    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, proc.Id);
                    if (h == IntPtr.Zero) continue;
                    var sb = new StringBuilder(1024);
                    int len = sb.Capacity;
                    if (!QueryFullProcessImageName(h, 0, sb, ref len)) continue;
                    if (sb.ToString().StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    {
                        proc.Kill();
                        proc.WaitForExit(5000);
                        killed++;
                    }
                }
                catch (Exception) { }
                finally { if (h != IntPtr.Zero) CloseHandle(h); try { proc.Dispose(); } catch (Exception) { } }
            }
            return killed;
        }

        // winget n'est pas dans le PATH du compte SYSTEM : son exécutable est pris dans le paquet App Installer
        // (WindowsApps), dans sa version la plus récente pour l'architecture du poste. Lancé hors de toute session, il ne
        // trouve pas seul les bibliothèques Visual C++ de son paquet VCLibs (code 0xC0000135) : leur dossier va dans son PATH.
        static string WingetPath(out string libraries)
        {
            libraries = "";
            try
            {
                string root = Path.Combine(Environment.GetEnvironmentVariable("ProgramW6432") ?? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WindowsApps");
                string cpu = Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432") ?? Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "";
                string arch = cpu.Equals("ARM64", StringComparison.OrdinalIgnoreCase) ? "arm64" : "x64";
                string best = NewestPackage(root, "Microsoft.DesktopAppInstaller_*_" + arch + "__8wekyb3d8bbwe", "winget.exe");
                if (best == null) return null;
                var dirs = new List<string>();
                foreach (string name in new[] { "Microsoft.VCLibs.140.00.UWPDesktop_*_" + arch + "__8wekyb3d8bbwe", "Microsoft.VCLibs.140.00_*_" + arch + "__8wekyb3d8bbwe" })
                {
                    string lib = NewestPackage(root, name, null);
                    if (lib != null) dirs.Add(lib);
                }
                dirs.Add(Path.GetDirectoryName(best));
                libraries = string.Join(";", dirs);
                return best;
            }
            catch (Exception) { return null; }
        }

        // Dossier (ou fichier « file » dans ce dossier) du paquet WindowsApps le plus récent qui suit ce motif.
        static string NewestPackage(string root, string pattern, string file)
        {
            string best = null; System.Version bestVersion = null;
            foreach (string dir in Directory.GetDirectories(root, pattern))
            {
                string path = file == null ? dir : Path.Combine(dir, file);
                string[] parts = Path.GetFileName(dir).Split('_');
                System.Version v;
                if ((file != null && !File.Exists(path)) || parts.Length < 2 || !System.Version.TryParse(parts[1], out v)) continue;
                if (bestVersion == null || v > bestVersion) { best = path; bestVersion = v; }
            }
            return best;
        }

        // Dernière ligne utile d'une sortie de winget (sans barres de progression ni sablier), avec le code de sortie.
        static string WingetMessage(string output, int code)
        {
            string last = "";
            foreach (string raw in (output ?? "").Split('\n'))
            {
                string line = Regex.Replace(raw, @"[\u2580-\u259F]+|\r", "").Trim();
                if (line.Length > 1 && !Regex.IsMatch(line, @"^[-\\|/ ]+$") && !Regex.IsMatch(line, @"\d ?[KMG]?B / \d")) last = line;
            }
            if (last.Length > 200) last = last.Substring(0, 200);
            return (last + " (0x" + unchecked((uint)code).ToString("X8") + ")").Trim();
        }

        // Logiciels installés que winget sait suivre (identifiant winget et version installée), relevés par
        // « winget export » : loco compare ces versions à son index winget. Rien n'est installé ni changé ici.
        // note : pourquoi ce relevé n'a pas pu se faire (winget absent, échec), vide sinon.
        static List<Dictionary<string, object>> WingetInstalled(out string note)
        {
            note = "";
            var list = new List<Dictionary<string, object>>();
            string libraries;
            string winget = WingetPath(out libraries);
            if (winget == null)
            {
                note = Lang.T("winget absent du poste (paquet App Installer de Microsoft)", "winget is not on this machine (Microsoft App Installer package)", "winget no está en el equipo (paquete App Installer de Microsoft)");
                return list;
            }
            string file = Path.Combine(Path.GetTempPath(), "loco-winget-" + Guid.NewGuid().ToString("N") + ".json");
            try
            {
                string output;
                int code = RunCapture(winget, "export -o \"" + file + "\" --include-versions --source winget --accept-source-agreements --disable-interactivity", 300, out output, libraries);
                if (!File.Exists(file)) { note = "winget export : " + WingetMessage(output, code); return list; }
                var doc = new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024 }.DeserializeObject(File.ReadAllText(file, Encoding.UTF8)) as Dictionary<string, object>;
                object sources;
                if (doc == null || !doc.TryGetValue("Sources", out sources) || !(sources is IEnumerable)) return list;
                foreach (object source in (IEnumerable)sources)
                {
                    var src = source as Dictionary<string, object>;
                    object packages;
                    if (src == null || !src.TryGetValue("Packages", out packages) || !(packages is IEnumerable)) continue;
                    foreach (object one in (IEnumerable)packages)
                    {
                        var pkg = one as Dictionary<string, object>;
                        if (pkg == null) continue;
                        string pid = Str(pkg, "PackageIdentifier"), version = Str(pkg, "Version");
                        if (WingetIdRe.IsMatch(pid) && version != "" && list.Count < 3000)
                            list.Add(new Dictionary<string, object> { { "id", pid }, { "version", version.Length > 60 ? version.Substring(0, 60) : version } });
                    }
                }
                // Ceux que winget n'a pas su mettre à jour restent de côté tant que leur version n'a pas changé.
                var skip = LoadWingetSkip();
                int before = skip.Count;
                lastExport = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (var item in list) lastExport[Str(item, "id")] = Str(item, "version");
                foreach (string pid in skip.Keys.ToList())
                {
                    string now;
                    if (!lastExport.TryGetValue(pid, out now) || now != skip[pid]) skip.Remove(pid);
                }
                if (skip.Count != before) SaveWingetSkip(skip);
                list = list.Where(item => !skip.ContainsKey(Str(item, "id"))).ToList();
            }
            catch (Exception e) { note = "winget : " + e.Message; }
            finally { try { File.Delete(file); } catch (Exception) { } }
            return list;
        }

        // ---- Repli à winget : installeur officiel de l'éditeur, quand winget ne sait pas mettre à jour -----------
        // Trois cas rencontrés sur un vrai parc : (1) le fichier du manifeste winget a été retiré par l'éditeur (HWiNFO,
        // 0x80190194) → le serveur résout l'installeur courant sur un miroir officiel et l'agent le pose après avoir
        // vérifié sa signature Authenticode ; (2) un installeur qui se termine brutalement hors session interactive
        // (iCUE, 0xC0000409) → rejoué en SYSTEM dans la session console active ; (3) un logiciel que l'éditeur tient
        // lui-même à jour et dont winget ne verra jamais la version bouger (Google Play Games) → mis de côté « à jour ».

        const int TokenSessionId = 12;
        const uint MaximumAllowed = 0x02000000;
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct STARTUPINFO { public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError; }
        [StructLayout(LayoutKind.Sequential)]
        struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
        [DllImport("kernel32.dll")] static extern uint WTSGetActiveConsoleSessionId();
        [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError = true)] static extern bool DuplicateTokenEx(IntPtr token, uint access, IntPtr attr, int impersonation, int type, out IntPtr dup);
        [DllImport("advapi32.dll", SetLastError = true)] static extern bool SetTokenInformation(IntPtr token, int cls, ref uint value, int length);
        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcessAsUser(IntPtr token, string app, string cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
        [DllImport("userenv.dll", SetLastError = true)] static extern bool CreateEnvironmentBlock(out IntPtr env, IntPtr token, bool inherit);
        [DllImport("userenv.dll", SetLastError = true)] static extern bool DestroyEnvironmentBlock(IntPtr env);
        [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint ms);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr handle, out uint code);

        // Lance une ligne de commande en tant que SYSTEM mais DANS la session console active (bureau interactif) :
        // certains installeurs (Corsair iCUE) se terminent brutalement en session 0 et n'aboutissent que là. Renvoie le
        // code de sortie, ou null si aucune session console n'est ouverte (personne de connecté) ou si le lancement échoue.
        static int? RunInActiveSession(string commandLine, int timeoutMs)
        {
            uint session = WTSGetActiveConsoleSessionId();
            if (session == 0xFFFFFFFF) return null;                      // aucune session console active
            IntPtr token = IntPtr.Zero, dup = IntPtr.Zero, env = IntPtr.Zero;
            try
            {
                if (!OpenProcessToken(Process.GetCurrentProcess().Handle, MaximumAllowed, out token)) return null;
                if (!DuplicateTokenEx(token, MaximumAllowed, IntPtr.Zero, 2, 1, out dup)) return null;   // SecurityImpersonation, TokenPrimary
                if (!SetTokenInformation(dup, TokenSessionId, ref session, sizeof(uint))) return null;
                CreateEnvironmentBlock(out env, dup, false);
                var si = new STARTUPINFO(); si.cb = Marshal.SizeOf(typeof(STARTUPINFO)); si.lpDesktop = "WinSta0\\Default";
                PROCESS_INFORMATION pi;
                const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400, CREATE_NO_WINDOW = 0x08000000;
                if (!CreateProcessAsUser(dup, null, commandLine, IntPtr.Zero, IntPtr.Zero, false,
                        CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env, null, ref si, out pi)) return null;
                try
                {
                    if (WaitForSingleObject(pi.hProcess, (uint)timeoutMs) != 0) return null;   // délai dépassé : laissé en cours
                    uint code; GetExitCodeProcess(pi.hProcess, out code);
                    return unchecked((int)code);                      // code de winget tel quel (ses échecs sont de grands uint)
                }
                finally { CloseHandle(pi.hProcess); CloseHandle(pi.hThread); }
            }
            catch (Exception) { return null; }
            finally { if (env != IntPtr.Zero) DestroyEnvironmentBlock(env); if (dup != IntPtr.Zero) CloseHandle(dup); if (token != IntPtr.Zero) CloseHandle(token); }
        }

        // Vérifie la signature Authenticode du fichier (chaîne de confiance valide jusqu'à une racine du système) ET que
        // l'éditeur attendu l'a bien signé (son nom dans le sujet du certificat). Fausse si l'un ou l'autre échoue.
        static readonly Guid WINTRUST_ACTION_GENERIC_VERIFY_V2 = new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
        [StructLayout(LayoutKind.Sequential)]
        struct WINTRUST_FILE_INFO { public uint cbStruct; public IntPtr pcwszFilePath; public IntPtr hFile; public IntPtr pgKnownSubject; }
        [StructLayout(LayoutKind.Sequential)]
        struct WINTRUST_DATA { public uint cbStruct; public IntPtr pPolicyCallbackData; public IntPtr pSIPClientData;
            public uint dwUIChoice; public uint fdwRevocationChecks; public uint dwUnionChoice; public IntPtr pFile;
            public uint dwStateAction; public IntPtr hWVTStateData; public IntPtr pwszURLReference; public uint dwProvFlags;
            public uint dwUIContext; public IntPtr pSignatureSettings; }
        [DllImport("wintrust.dll", CharSet = CharSet.Unicode)] static extern uint WinVerifyTrust(IntPtr hwnd, ref Guid action, IntPtr data);

        static bool PublisherTrusted(string path, string signer)
        {
            bool valid = false;
            IntPtr pPath = IntPtr.Zero, pFile = IntPtr.Zero, pData = IntPtr.Zero;
            try
            {
                var fi = new WINTRUST_FILE_INFO { cbStruct = (uint)Marshal.SizeOf(typeof(WINTRUST_FILE_INFO)) };
                pPath = Marshal.StringToCoTaskMemUni(path);
                fi.pcwszFilePath = pPath;
                pFile = Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(WINTRUST_FILE_INFO)));
                Marshal.StructureToPtr(fi, pFile, false);
                var wd = new WINTRUST_DATA { cbStruct = (uint)Marshal.SizeOf(typeof(WINTRUST_DATA)),
                    dwUIChoice = 2 /*WTD_UI_NONE*/, fdwRevocationChecks = 0 /*WTD_REVOKE_NONE*/,
                    dwUnionChoice = 1 /*WTD_CHOICE_FILE*/, pFile = pFile, dwStateAction = 1 /*WTD_STATEACTION_VERIFY*/,
                    dwProvFlags = 0x00000010 /*WTD_SAFER_FLAG*/ };
                pData = Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(WINTRUST_DATA)));
                Marshal.StructureToPtr(wd, pData, false);
                var action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
                valid = WinVerifyTrust((IntPtr)(-1), ref action, pData) == 0;    // INVALID_HANDLE_VALUE : pas d'interface
                wd = (WINTRUST_DATA)Marshal.PtrToStructure(pData, typeof(WINTRUST_DATA));
                wd.dwStateAction = 2 /*WTD_STATEACTION_CLOSE*/;
                Marshal.StructureToPtr(wd, pData, false);
                WinVerifyTrust((IntPtr)(-1), ref action, pData);
            }
            catch (Exception) { valid = false; }
            finally { if (pData != IntPtr.Zero) Marshal.FreeCoTaskMem(pData); if (pFile != IntPtr.Zero) Marshal.FreeCoTaskMem(pFile); if (pPath != IntPtr.Zero) Marshal.FreeCoTaskMem(pPath); }
            if (!valid) return false;
            try
            {
                string subject = X509Certificate.CreateFromSignedFile(path).Subject ?? "";
                return string.IsNullOrEmpty(signer) || subject.IndexOf(signer, StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch (Exception) { return false; }
        }

        // Version installée que winget connaît pour ce paquet (colonne « Version » de winget list), ou "".
        static string InstalledWingetVersion(string pid, string winget, string libraries)
        {
            try
            {
                string outp;
                RunCapture(winget, "list --id \"" + pid + "\" --exact --disable-interactivity --accept-source-agreements", 60, out outp, libraries);
                foreach (string raw in (outp ?? "").Split('\n'))
                {
                    string line = raw.Replace("\r", "");
                    int at = line.IndexOf(pid, StringComparison.OrdinalIgnoreCase);
                    if (at < 0) continue;
                    string ver = line.Substring(at + pid.Length).Trim().Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
                    if (Regex.IsMatch(ver, @"^\d")) return ver;
                }
            }
            catch (Exception) { }
            return "";
        }

        // Met un logiciel de côté avec sa raison : plus remonté par l'inventaire tant que sa version installée ne change
        // pas (même mécanisme que « introuvable/rien d'applicable », étendu aux installeurs retirés et aux faux positifs).
        static void SetAside(string pid, string reason, List<string> lines, Action<string, string> markPkg)
        {
            lines.Add(Lang.T("Non mis à jour : ", "Not updated: ", "No actualizado: ") + pid + " — " + reason
                + Lang.T(" (plus proposé tant que sa version ne change pas)", " (no longer offered until its version changes)",
                         " (ya no se propone mientras su versión no cambie)"));
            var skip = LoadWingetSkip();
            string seen;
            skip[pid] = lastExport.TryGetValue(pid, out seen) ? seen : "";
            SaveWingetSkip(skip);
            if (markPkg != null) markPkg(pid, "ok");
        }

        // Repli : télécharge l'installeur officiel indiqué par le serveur, vérifie sa signature Authenticode (éditeur
        // attendu), puis le pose en silence. Sert aux logiciels dont le fichier du manifeste winget a été retiré (HWiNFO).
        static readonly Dictionary<string, string> DirectSilent = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) {
            { "inno", "/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART" }, { "nullsoft", "/S" },
            { "msi", "/qn /norestart" }, { "wix", "/qn /norestart" }, { "burn", "/quiet /norestart" } };

        static bool DirectInstall(string pid, Dictionary<string, object> desc, List<string> lines, Action<string, string> markPkg)
        {
            string url = Str(desc, "url"), type = Str(desc, "type"), args = Str(desc, "args"), signer = Str(desc, "signer");
            if (!(url.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
            {
                lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + pid + Lang.T(" : source de repli invalide", " : invalid fallback source", " : origen de reserva no válido"));
                if (markPkg != null) markPkg(pid, "failed");
                return false;
            }
            string ext = Path.GetExtension(new Uri(url).AbsolutePath);
            if (ext == "" || ext.Length > 5) ext = ".exe";
            string file = Path.Combine(Path.GetTempPath(), "loco-dl-" + Guid.NewGuid().ToString("N") + ext);
            try
            {
                Download(url, file, (s, cert, chain, errors) => errors == SslPolicyErrors.None);   // éditeur tiers : racines du système
                if (!PublisherTrusted(file, signer))
                {
                    lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + pid + Lang.T(" : signature de l'éditeur non vérifiée (installeur ignoré)",
                        " : publisher signature not verified (installer skipped)", " : firma del editor no verificada (instalador ignorado)"));
                    if (markPkg != null) markPkg(pid, "failed");
                    return false;
                }
                if (args == "") { string d; args = DirectSilent.TryGetValue(type ?? "", out d) ? d : "/S"; }
                string output;
                int code = RunCapture(file, args, InstallerTimeout, out output, null);
                uint r = unchecked((uint)code);
                if (code == 0 || r == 0x8A15010D || r == 0x8A150109 || r == 0x8A15010B || code == 3010)   // 3010 : redémarrage MSI
                {
                    lines.Add(Lang.T("OK : ", "OK: ", "OK: ") + pid + Lang.T(" (installeur de l'éditeur)", " (publisher installer)", " (instalador del editor)"));
                    if (markPkg != null) markPkg(pid, "ok");
                    return true;
                }
                lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + pid + " : " + WingetMessage(output, code)
                    + Lang.T(" (installeur de l'éditeur ; réessayé à la prochaine mise à jour)", " (publisher installer; will be retried at the next update)",
                             " (instalador del editor; se reintentará en la próxima actualización)"));
                if (markPkg != null) markPkg(pid, "failed");
                return false;
            }
            catch (Exception e)
            {
                lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + pid + " : " + e.Message.Trim()
                    + Lang.T(" (installeur de l'éditeur)", " (publisher installer)", " (instalador del editor)"));
                if (markPkg != null) markPkg(pid, "failed");
                return false;
            }
            finally { try { File.Delete(file); } catch (Exception) { } }
        }

        // Pilote NVIDIA demandé depuis loco : installeur officiel (adresse download.nvidia.com donnée par le serveur), signé par
        // NVIDIA (Authenticode vérifié), posé sans fenêtre ni redémarrage. Réussi quand Windows rapporte la version voulue.
        static readonly Regex NvidiaUrl = new Regex(@"^https://(?:[a-z]{2}|international)\.download\.nvidia\.com/Windows/\d{3}\.\d{2}/[A-Za-z0-9._-]+\.exe$");
        static readonly Regex NvidiaVer = new Regex(@"^\d{3}\.\d{2}$");

        static bool InstallGpuDriver(Dictionary<string, object> cmd, List<string> lines, Action<string, int> step)
        {
            string url = Str(cmd, "url"), version = Str(cmd, "version"), signer = Str(cmd, "signer");
            if (signer == "") signer = "NVIDIA Corporation";
            if (!NvidiaUrl.IsMatch(url) || !NvidiaVer.IsMatch(version) || !url.Contains("/" + version + "/"))
            {
                lines.Add(Lang.T("Adresse ou version du pilote invalide : rien n'est installé.", "Invalid driver address or version: nothing installed.",
                                 "Dirección o versión del controlador no válida: no se instala nada."));
                return false;
            }
            string before = HardwareInfo.NvidiaInstalled();
            if (before == "")
            {
                lines.Add(Lang.T("Aucune carte NVIDIA sur ce poste : rien n'est installé.", "No NVIDIA card on this machine: nothing installed.",
                                 "Ninguna tarjeta NVIDIA en este equipo: no se instala nada."));
                return false;
            }
            string drive = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.Windows));
            try
            {
                if (new DriveInfo(drive).AvailableFreeSpace < (4L << 30))
                {
                    lines.Add(Lang.T("Moins de 4 Go libres sur ", "Less than 4 GB free on ", "Menos de 4 GB libres en ") + drive
                        + Lang.T(" : l'installeur (1 Go) et ses fichiers n'y tiendraient pas.", ": the installer (1 GB) and its files would not fit.",
                                 ": el instalador (1 GB) y sus archivos no cabrían."));
                    return false;
                }
            }
            catch (Exception) { }
            string dir = Path.Combine(Root, "gpu-driver");
            Directory.CreateDirectory(dir);
            string file = Path.Combine(dir, version + ".exe");
            try
            {
                step("download", 0);
                Download(url, file, (s, cert, chain, errors) => errors == SslPolicyErrors.None, percent => step("download", percent));   // racines du système
                step("signature", -1);
                if (!PublisherTrusted(file, signer))
                {
                    lines.Add(Lang.T("ÉCHEC : installeur non signé par ", "FAILED: installer not signed by ", "FALLO: instalador no firmado por ") + signer
                        + Lang.T(" (rien n'est installé).", " (nothing installed).", " (no se instala nada)."));
                    return false;
                }
                step("install", -1);
                int code = RunInstaller(file, "-s -noreboot -noeula -nofinish");
                string after = HardwareInfo.NvidiaInstalled();
                if (after == version)
                {
                    lines.Add(Lang.T("OK : pilote NVIDIA ", "OK: NVIDIA driver ", "OK: controlador NVIDIA ") + version
                        + Lang.T(" installé (avant : ", " installed (before: ", " instalado (antes: ") + before + ").");
                    return true;
                }
                if (code == 0)
                {
                    lines.Add(Lang.T("Installeur terminé sans erreur, mais Windows rapporte encore le pilote ", "Installer finished without error, but Windows still reports driver ",
                                     "El instalador terminó sin error, pero Windows aún indica el controlador ") + after
                        + Lang.T(" : redémarrez le poste pour charger le ", ": restart the machine to load ", ": reinicie el equipo para cargar el ") + version + ".");
                    return true;
                }
                lines.Add(Lang.T("ÉCHEC : l'installeur NVIDIA s'est arrêté avec le code 0x", "FAILED: the NVIDIA installer stopped with code 0x",
                                 "FALLO: el instalador de NVIDIA se detuvo con el código 0x") + unchecked((uint)code).ToString("X8")
                    + Lang.T(" ; pilote en place : ", "; driver in place: ", "; controlador actual: ") + after
                    + Lang.T(". Journaux de l'installeur : C:\\ProgramData\\NVIDIA Corporation\\Installer2.", ". Installer logs: C:\\ProgramData\\NVIDIA Corporation\\Installer2.",
                             ". Registros del instalador: C:\\ProgramData\\NVIDIA Corporation\\Installer2."));
                return false;
            }
            catch (Exception e)
            {
                lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + e.Message.Trim());
                return false;
            }
            finally
            {
                try { File.Delete(file); } catch (Exception) { }
                // L'installeur s'extrait dans C:\NVIDIA\DisplayDriver\<version> et l'y laisse (plus d'un gigaoctet).
                try
                {
                    string extracted = Path.Combine(drive, "NVIDIA", "DisplayDriver", version);
                    if (Directory.Exists(extracted)) Directory.Delete(extracted, true);
                }
                catch (Exception) { }
            }
        }

        static bool MsiBusy(int code)
        {
            uint result = unchecked((uint)code);
            return result == 1618 || result == 0x80070652 || result == 0x8A150102;
        }

        static int RunWingetUpgrade(string winget, string arguments, string libraries, List<string> lines, out string output)
        {
            int code = RunCapture(winget, arguments, InstallerTimeout, out output, libraries);
            for (int attempt = 0; attempt < 3 && MsiBusy(code); attempt++)
            {
                if (attempt == 0)
                    lines.Add(Lang.T("  Une installation MSI est en cours, nouvelle tentative après attente.",
                                     "  Another MSI installation is in progress, waiting before retrying.",
                                     "  Hay otra instalación MSI en curso, se espera antes de reintentar."));
                Thread.Sleep(15000 * (attempt + 1));
                code = RunCapture(winget, arguments, InstallerTimeout, out output, libraries);
            }
            return code;
        }

        // Logiciels hors catalogue choisis dans loco : mis à jour un à un par winget, en silence, au compte SYSTEM.
        // Codes de winget : 0x8A150014 (introuvable pour la mise à jour) et 0x8A15002B (rien d'applicable) : mis de côté,
        // sans échec ; 0x8A15010D (déjà installé) ; 0x8A150109 et 0x8A15010B : installé, un redémarrage termine l'installation.
        static bool UpgradeSoftware(object idsValue, Dictionary<string, object> fallback, List<string> lines, Progress progress = null, Action<string, string> markPkg = null)
        {
            var ids = new List<string>();
            if (idsValue is IEnumerable && !(idsValue is string))
                foreach (object one in (IEnumerable)idsValue)
                {
                    string pid = Convert.ToString(one);
                    if (WingetIdRe.IsMatch(pid) && !ids.Contains(pid)) ids.Add(pid);
                }
            if (ids.Count == 0) return true;
            string libraries;
            string winget = WingetPath(out libraries);
            if (winget == null)
            {
                lines.Add(Lang.T("Logiciels : winget absent du poste, rien n'est mis à jour.", "Software: winget is not on this machine, nothing updated.", "Programas: winget no está en el equipo, nada actualizado."));
                return false;
            }
            bool ok = true;
            int rank = 0;
            foreach (string pid in ids)
            {
                rank++;
                if (progress != null) progress("install", pid, rank, ids.Count, -1);
                if (markPkg != null) markPkg(pid, "running");

                // Repli fourni par le serveur : winget ne peut pas mettre ce logiciel à jour sur ce poste (fichier du
                // manifeste retiré par l'éditeur, ex. HWiNFO) → installeur officiel téléchargé et vérifié par signature.
                object descObj;
                if (fallback != null && fallback.TryGetValue(pid, out descObj) && descObj is Dictionary<string, object>)
                {
                    DirectInstall(pid, (Dictionary<string, object>)descObj, lines, markPkg);
                    continue;
                }

                string output;
                string arguments = "upgrade --id \"" + pid + "\" --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity";
                int code = RunWingetUpgrade(winget, arguments, libraries, lines, out output);
                uint result = unchecked((uint)code);
                if (result == 0x8A15002B)
                {
                    // Sur un Windows qui n'est pas en anglais, winget écarte les installeurs « en-US » quand il ne connaît pas la
                    // langue de la version installée (« Installer locale does not match … Preferred Locales: [fr-FR] ») et ne
                    // trouve rien d'applicable. Un second essai les accepte ; un logiciel publié dans la langue du poste a, lui,
                    // déjà été mis à jour au premier.
                    string again;
                    int retry = RunWingetUpgrade(winget, arguments + " --locale en-US", libraries, lines, out again);
                    if (unchecked((uint)retry) != 0x8A15002B) { code = retry; output = again; result = unchecked((uint)retry); }
                }
                if (result == 0x8A150014 || result == 0x8A15002B || result == 0x80190194)
                {
                    // Introuvable pour winget, rien d'applicable, ou installeur retiré par l'éditeur (404, 0x80190194,
                    // sans repli disponible) : mis de côté avec sa raison plutôt que de rester en échec sans fin.
                    SetAside(pid, WingetMessage(output, code), lines, markPkg);
                    continue;
                }
                if (!MsiBusy(code) && !(code == 0 || result == 0x8A15010D || result == 0x8A150109 || result == 0x8A15010B))
                {
                    // Le logiciel est installé « pour tout le monde » (ALLUSERS=1) mais winget lance l'installeur pour le profil
                    // par défaut : le MSI refuse (1603, « already installed for everyone, pass ALLUSERS=1 to msiexec »). On réessaie
                    // en passant ALLUSERS=1 à l'installeur (--custom), ce qui met bien à jour la copie « pour tout le monde ».
                    string again;
                    int retry = RunWingetUpgrade(winget, arguments + " --custom ALLUSERS=1", libraries, lines, out again);
                    uint r = unchecked((uint)retry);
                    if (r == 0 || r == 0x8A15010D || r == 0x8A150109 || r == 0x8A15010B || MsiBusy(retry))
                    { code = retry; output = again; result = r; }
                }
                if (!MsiBusy(code) && !(code == 0 || result == 0x8A15010D || result == 0x8A150109 || result == 0x8A15010B))
                {
                    // L'installeur a échoué, le plus souvent parce que le logiciel était ouvert (fichiers verrouillés). On ferme
                    // ses processus SANS confirmation et on réessaie une fois ; l'agent est en SYSTEM, aucune session ouverte requise.
                    int closed = CloseApp(InstallDir(pid, winget, libraries));
                    if (closed > 0)
                    {
                        lines.Add(Lang.T("  logiciel fermé (" + closed + " processus), nouvel essai", "  software closed (" + closed + " processes), retrying", "  programa cerrado (" + closed + " procesos), reintentando"));
                        code = RunWingetUpgrade(winget, arguments, libraries, lines, out output);
                        result = unchecked((uint)code);
                    }
                }
                if (!(code == 0 || result == 0x8A15010D || result == 0x8A150109 || result == 0x8A15010B)
                    && output != null && (output.IndexOf("exit code: 3221", StringComparison.OrdinalIgnoreCase) >= 0 || output.IndexOf("C00004", StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    // L'installeur s'est terminé brutalement en session 0 (ex. Corsair iCUE : « Installer failed with exit
                    // code: 3221226505 » = 0xC0000409, famille des exceptions fatales 0xC00004xx). Ces installeurs n'aboutissent
                    // que dans une session interactive : rejoué en SYSTEM dans la session console active. Sans personne de
                    // connecté, reporté à plus tard ; le résultat de la relance (succès ou échec de l'éditeur) est classé normalement.
                    string outFile = Path.Combine(Path.GetTempPath(), "loco-session-" + Guid.NewGuid().ToString("N") + ".txt");
                    string bat = Path.ChangeExtension(outFile, ".bat");
                    try
                    {
                        File.WriteAllText(bat, "@echo off\r\nset \"PATH=" + libraries + ";%PATH%\"\r\n\"" + winget + "\" " + arguments + " > \"" + outFile + "\" 2>&1\r\n", Encoding.Default);
                        int? sc = RunInActiveSession("cmd.exe /c \"\"" + bat + "\"\"", InstallerTimeout * 1000);
                        if (sc.HasValue)        // la relance a eu lieu : on prend le code de winget tel quel (succès ou échec de l'éditeur)
                        {
                            try { output = File.ReadAllText(outFile); } catch (Exception) { }
                            code = sc.Value; result = unchecked((uint)code);
                            lines.Add(Lang.T("  rejoué dans la session de l'utilisateur connecté", "  retried in the signed-in user's session", "  reintentado en la sesión del usuario conectado"));
                        }
                        else
                            lines.Add(Lang.T("  installeur à relancer quand un utilisateur sera connecté", "  installer to retry when a user is signed in", "  instalador a reintentar cuando haya un usuario conectado"));
                    }
                    catch (Exception) { }
                    finally { try { File.Delete(bat); } catch (Exception) { } try { File.Delete(outFile); } catch (Exception) { } }
                }
                if (code == 0 || result == 0x8A15010D || result == 0x8A150109 || result == 0x8A15010B)
                {
                    // Faux positif : winget signale une réussite mais la version installée n'a pas bougé → l'éditeur gère
                    // lui-même ses versions (Google Play Games) ; déjà à jour, inutile de le reproposer indéfiniment.
                    if (code == 0)
                    {
                        string before;
                        lastExport.TryGetValue(pid, out before);
                        if (!string.IsNullOrEmpty(before) && before == InstalledWingetVersion(pid, winget, libraries))
                        {
                            SetAside(pid, Lang.T("déjà à jour (l'éditeur gère lui-même ses versions)", "already up to date (the publisher manages its own versions)", "ya actualizado (el editor gestiona sus propias versiones)"), lines, markPkg);
                            continue;
                        }
                    }
                    if (result == 0x8A150109 || result == 0x8A15010B)
                        lines.Add(Lang.T("OK : ", "OK: ", "OK: ") + pid + Lang.T(" (redémarrage nécessaire)", " (reboot required)", " (reinicio necesario)"));
                    else
                        lines.Add(Lang.T("OK : ", "OK: ", "OK: ") + pid + (code == 0 ? "" : Lang.T(" (déjà à jour)", " (already up to date)", " (ya actualizado)")));
                }
                else
                {
                    // Même après fermeture, winget n'a pas réussi : signalé à part, non abandonné (réessayé à la prochaine mise à jour,
                    // par exemple planifiée hors session). Un logiciel hors catalogue ne fait pas échouer toute la mise à jour.
                    string reason = MsiBusy(code)
                        ? Lang.T("Windows Installer est encore occupé (MSI 1618)", "Windows Installer is still busy (MSI 1618)",
                                 "Windows Installer sigue ocupado (MSI 1618)")
                        : WingetMessage(output, code);
                    lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + pid + " : " + reason
                        + Lang.T(" (réessayé à la prochaine mise à jour)", " (will be retried at the next update)", " (se reintentará en la próxima actualización)"));
                    if (markPkg != null) markPkg(pid, "failed");
                    continue;
                }
                if (markPkg != null) markPkg(pid, "ok");
            }
            return ok;
        }

        // ------------------------------------------------------------ actions

        // Pile de maintenance (servicing stack) d'abord, comme le fait Windows Update : une mise à jour cumulative peut l'exiger.
        static readonly Regex ServicingStack = new Regex("servicing stack|pile de maintenance|pila de servicio", RegexOptions.IgnoreCase);

        // Mises à jour Windows une par une (téléchargement puis installation), toutes ou seulement celles choisies dans
        // loco (identifiants Windows Update) ; l'avancement part au serveur à chaque étape.
        static bool InstallWindowsUpdates(List<string> lines, HashSet<string> only = null, Progress progress = null, Action<string, string> markPkg = null)
        {
            dynamic session = NewSession();
            if (progress != null) progress("updates", "", 0, 0, -1, "prepare");
            dynamic found = SearchUpdates(session.CreateUpdateSearcher());
            var chosen = new List<object>();
            foreach (dynamic u in found.Updates)
            {
                if (only != null && !only.Contains(Convert.ToString(u.Identity.UpdateID))) continue;
                if (IsBlocked(u)) { lines.Add(Lang.T("Bloquée dans loco, non installée : ", "Blocked in loco, not installed: ", "Bloqueada en loco, no instalada: ") + Convert.ToString(u.Title)); HideUpdate(u); continue; }
                if (IsUpgrade(u)) continue;             // nouvelle version de Windows : seulement sur demande (InstallWindowsUpgrade)
                if ((bool)u.InstallationBehavior.CanRequestUserInput) { lines.Add(Lang.T("Ignorée (interactive) : ", "Skipped (interactive): ", "Omitida (interactiva): ") + u.Title); continue; }
                if (!(bool)u.EulaAccepted) u.AcceptEula();
                chosen.Add(u);
            }
            chosen = chosen.OrderBy(u => ServicingStack.IsMatch(Convert.ToString(((dynamic)u).Title)) ? 0 : 1).ToList();
            if (chosen.Count == 0) { lines.Add(Lang.T("Windows Update : aucune mise à jour.", "Windows Update: no updates.", "Windows Update: sin actualizaciones.")); return true; }
            lines.Add(Lang.T("Windows Update, mises à jour à installer : ", "Windows Update, updates to install: ", "Windows Update, actualizaciones por instalar: ") + chosen.Count + ".");
            bool ok = true, reboot = false;
            for (int i = 0; i < chosen.Count; i++)
            {
                dynamic u = chosen[i];
                string title = Convert.ToString(u.Title);
                if (markPkg != null) markPkg(title, "running");
                try
                {
                    dynamic coll = Activator.CreateInstance(Type.GetTypeFromProgID("Microsoft.Update.UpdateColl"));
                    coll.Add(u);
                    if (!(bool)u.IsDownloaded)
                    {
                        if (progress != null) progress("updates", title, i + 1, chosen.Count, 100 * i / chosen.Count, "download");
                        dynamic downloader = session.CreateUpdateDownloader();
                        downloader.Updates = coll;
                        downloader.Download();
                    }
                    if (progress != null) progress("updates", title, i + 1, chosen.Count, (100 * i + 40) / chosen.Count, "install");
                    dynamic installer = session.CreateUpdateInstaller();
                    installer.Updates = coll;
                    dynamic result = installer.Install();
                    dynamic r = result.GetUpdateResult(0);
                    int code = (int)r.ResultCode;
                    string state = code == 2 ? "OK" : code == 3 ? Lang.T("OK avec erreurs", "OK with errors", "OK con errores") : code == 4 ? Lang.T("ÉCHEC", "FAILED", "FALLO") : code == 5 ? Lang.T("ANNULÉE", "CANCELLED", "CANCELADA") : "code " + code;
                    if (code >= 4) ok = false;
                    if (markPkg != null) markPkg(title, code >= 4 ? "failed" : "ok");
                    int hr = (int)r.HResult;
                    lines.Add("  " + state + (hr != 0 ? string.Format(" (0x{0:X8})", hr) : "") + " : " + title);
                    if ((bool)result.RebootRequired) reboot = true;
                }
                catch (Exception e)
                {
                    ok = false;                          // une mise à jour en échec n'empêche pas les suivantes
                    if (markPkg != null) markPkg(title, "failed");
                    lines.Add("  " + Lang.T("ÉCHEC", "FAILED", "FALLO") + " (" + e.Message.Trim() + ") : " + title);
                }
            }
            if (reboot) lines.Add(Lang.T("Redémarrage nécessaire pour terminer.", "Reboot required to finish.", "Reinicio necesario para terminar."));
            if (progress != null && markPkg != null) progress("updates", "", chosen.Count, chosen.Count, 100, "");   // état final des mises à jour
            return ok;
        }

        // Nouvelle version de Windows choisie dans loco (identifiant Windows Update d'une mise à jour « Upgrades ») :
        // téléchargée puis installée ; Windows la termine au redémarrage qui suit. Vrai si elle est installée.
        static bool InstallWindowsUpgrade(string id, List<string> lines, Progress progress, out bool reboot)
        {
            reboot = false;
            dynamic session = NewSession();
            if (progress != null) progress("updates", "", 0, 0, -1, "prepare");
            dynamic target = null;
            foreach (dynamic u in SearchUpdates(session.CreateUpdateSearcher()).Updates)
                if (string.Equals(Convert.ToString(u.Identity.UpdateID), id, StringComparison.OrdinalIgnoreCase) && IsUpgrade(u)) { target = u; break; }
            if (target == null) { lines.Add(Lang.T("Windows Update ne propose plus cette nouvelle version : rien n'a été installé.", "Windows Update no longer offers this new version: nothing was installed.", "Windows Update ya no ofrece esta nueva versión: no se instaló nada.")); return false; }
            string title = Convert.ToString(target.Title);
            if (IsBlocked(target)) { lines.Add(Lang.T("Bloquée dans loco, non installée : ", "Blocked in loco, not installed: ", "Bloqueada en loco, no instalada: ") + title); return false; }
            if (!(bool)target.EulaAccepted) target.AcceptEula();
            dynamic coll = Activator.CreateInstance(Type.GetTypeFromProgID("Microsoft.Update.UpdateColl"));
            coll.Add(target);
            try
            {
                if (!(bool)target.IsDownloaded)
                {
                    if (progress != null) progress("updates", title, 1, 1, 5, "download");
                    dynamic downloader = session.CreateUpdateDownloader();
                    downloader.Updates = coll;
                    downloader.Download();
                }
                if (progress != null) progress("updates", title, 1, 1, 50, "install");
                dynamic installer = session.CreateUpdateInstaller();
                installer.Updates = coll;
                dynamic result = installer.Install();
                dynamic r = result.GetUpdateResult(0);
                int code = (int)r.ResultCode, hr = (int)r.HResult;
                reboot = (bool)result.RebootRequired;
                if (code == 2 || code == 3)
                {
                    lines.Add(Lang.T("Windows Update : nouvelle version installée : ", "Windows Update: new version installed: ", "Windows Update: nueva versión instalada: ") + title + ".");
                    return true;
                }
                lines.Add(Lang.T("ÉCHEC", "FAILED", "FALLO") + " (code " + code + (hr != 0 ? string.Format(", 0x{0:X8}", hr) : "") + ") : " + title);
            }
            catch (Exception e) { lines.Add(Lang.T("ÉCHEC", "FAILED", "FALLO") + " (" + e.Message.Trim() + ") : " + title); }
            return false;
        }

        // Script de la bibliothèque loco : contenu vérifié par son empreinte, exécuté en SYSTEM par PowerShell.
        static int RunScript(Dictionary<string, object> cmd, out string output)
        {
            var script = cmd.ContainsKey("script") ? cmd["script"] as Dictionary<string, object> : null;
            string content = script == null ? "" : Str(script, "content");
            string platform = script == null ? "" : Str(script, "platform");
            bool python = platform == "python";
            if (script == null || (platform != "windows" && !python)) { output = Lang.T("Script destiné à un autre système.", "Script intended for another system.", "Script destinado a otro sistema."); return 1; }
            using (var sha = SHA256.Create())
                if (BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(content))).Replace("-", "").ToLowerInvariant()
                    != Str(script, "sha256").ToLowerInvariant()) { output = Lang.T("Empreinte du script invalide : non exécuté.", "Invalid script fingerprint: not run.", "Huella del script no válida: no ejecutado."); return 1; }
            string python_exe = python ? FindPython() : null;
            if (python && python_exe == null) { output = Lang.T("Python introuvable sur ce poste.", "Python not found on this machine.", "Python no se encuentra en este equipo."); return 1; }
            string dir = Path.Combine(Path.GetTempPath(), "loco-script-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                if (python)
                {
                    string file = Path.Combine(dir, "script.py");
                    File.WriteAllText(file, content, new UTF8Encoding(false));
                    // -X utf8 : Python écrit sa sortie en UTF-8 quelle que soit la page de code du poste.
                    return RunCapture(python_exe, "-X utf8 \"" + file + "\"", InstallerTimeout, out output);
                }
                string ps1 = Path.Combine(dir, "script.ps1");
                File.WriteAllText(ps1, content, new UTF8Encoding(true));          // BOM : PowerShell 5.1 lit alors l'UTF-8
                string arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"[Console]::OutputEncoding = " +
                                   "[Text.Encoding]::UTF8; & '" + ps1.Replace("'", "''") + "'; exit $LASTEXITCODE\"";
                return RunCapture("powershell.exe", arguments, InstallerTimeout, out output);
            }
            finally { try { Directory.Delete(dir, true); } catch (Exception) { } }
        }

        // Interpréteur Python du poste : le lanceur « py », puis python / python3 dans le PATH. null si aucun.
        static string FindPython()
        {
            foreach (var name in new[] { "py.exe", "python.exe", "python3.exe" })
            {
                string o;
                if (RunCapture("where", name, 10, out o) == 0)
                {
                    string line = (o ?? "").Split('\n')[0].Trim();
                    if (line != "") return line;
                }
            }
            return null;
        }

        static int RunCapture(string file, string arguments, int timeoutSeconds, out string output, string pathPrefix = null)
        {
            var psi = new ProcessStartInfo(file, arguments)
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            if (pathPrefix != null)
            {
                psi.EnvironmentVariables["PATH"] = pathPrefix + ";" + (Environment.GetEnvironmentVariable("PATH") ?? "");
                psi.WorkingDirectory = Path.GetDirectoryName(file);
            }
            var text = new StringBuilder();
            DataReceivedEventHandler collect = (sender, e) => { if (e.Data != null) lock (text) if (text.Length < OutputLimit * 2) text.AppendLine(e.Data); };
            using (var p = new Process { StartInfo = psi })
            {
                p.OutputDataReceived += collect; p.ErrorDataReceived += collect;
                p.Start(); p.BeginOutputReadLine(); p.BeginErrorReadLine();
                if (!p.WaitForExit(timeoutSeconds * 1000))
                {
                    try { p.Kill(); } catch (Exception) { }
                    lock (text) output = text + Lang.T("\nArrêté : pas terminé après ", "\nStopped: not finished after ", "\nDetenido: no terminó tras ") + timeoutSeconds + " s.";
                    return -1;
                }
                p.WaitForExit();                              // fin de la lecture asynchrone des sorties
                lock (text) output = text.ToString().TrimEnd();
                if (output.Length == 0) output = Lang.T("(aucune sortie)", "(no output)", "(sin salida)");
                return p.ExitCode;
            }
        }

        static int RunInstaller(string file, string arguments)
        {
            var psi = new ProcessStartInfo(file, arguments ?? "") { UseShellExecute = false, CreateNoWindow = true };
            using (var p = Process.Start(psi))
            {
                if (!p.WaitForExit(InstallerTimeout * 1000))
                {
                    try { p.Kill(); } catch (Exception) { }
                    throw new Exception(Lang.T("pas terminé après ", "not finished after ", "no terminó tras ") + InstallerTimeout + Lang.T(" s (options silencieuses à vérifier)", " s (check the silent options)", " s (comprobar las opciones silenciosas)"));
                }
                return p.ExitCode;
            }
        }

        // Étape remontée au serveur (fiche du poste, page du déploiement) : étape, élément, rang, nombre, pourcentage et,
        // pour les mises à jour du système, la phase (préparation, téléchargement, installation).
        internal delegate void Progress(string step, string app, int index, int total, int percent, string phase = "");

        static bool InstallCatalogApps(string server, object appsValue, List<string> lines, Progress progress = null)
        {
            var apps = appsValue as ArrayList ?? new ArrayList();
            if (apps.Count == 0) { lines.Add(Lang.T("Logiciels du catalogue : rien à mettre à jour.", "Catalog software: nothing to update.", "Programas del catálogo: nada que actualizar.")); return true; }
            bool ok = true;
            string dir = Path.Combine(Path.GetTempPath(), "loco-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                int index = 0;
                foreach (Dictionary<string, object> a in apps)
                {
                    string name = Str(a, "name") + " " + Str(a, "version");
                    int rank = ++index;
                    try
                    {
                        string dest = Path.Combine(dir, Path.GetFileName(Str(a, "file")));
                        if (progress != null) progress("download", Str(a, "name"), rank, apps.Count, 0);
                        Download(server + Str(a, "url"), dest, null,
                                 progress == null ? (Action<int>)null : p => progress("download", Str(a, "name"), rank, apps.Count, p));
                        if (progress != null) progress("verify", Str(a, "name"), rank, apps.Count, -1);
                        if (Sha256File(dest) != Str(a, "sha256").ToLowerInvariant()) throw new Exception(Lang.T("somme SHA-256 invalide", "invalid SHA-256 checksum", "suma SHA-256 no válida"));
                        if (progress != null) progress("install", Str(a, "name"), rank, apps.Count, -1);
                        string args = Str(a, "args");
                        int code;
                        switch (Str(a, "kind"))
                        {
                            case "msi": code = RunInstaller("msiexec.exe", "/i \"" + dest + "\" " + args); break;
                            case "msu": code = RunInstaller("wusa.exe", "\"" + dest + "\" " + args); break;
                            case "ps1": code = RunInstaller("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + dest + "\" " + args); break;
                            case "exe": code = RunInstaller(dest, args); break;
                            default:
                                File.Copy(dest, Path.Combine(Environment.GetEnvironmentVariable("PUBLIC") ?? @"C:\Users\Public", "Downloads", Path.GetFileName(dest)), true);
                                code = 0;
                                break;
                        }
                        if (code == 0 || code == 1638 || code == 2359302) lines.Add(Lang.T("OK : ", "OK: ", "OK: ") + name);
                        else if (code == 3010 || code == 1641) lines.Add(Lang.T("OK : ", "OK: ", "OK: ") + name + Lang.T(" (redémarrage nécessaire)", " (reboot required)", " (reinicio necesario)"));
                        else throw new Exception(Lang.T("code de sortie ", "exit code ", "código de salida ") + code);
                    }
                    catch (Exception e) { ok = false; lines.Add(Lang.T("ÉCHEC : ", "FAILED: ", "FALLO: ") + name + " : " + e.Message); }
                }
            }
            finally { try { Directory.Delete(dir, true); } catch (Exception) { } }
            return ok;
        }

        // ------------------------------------------------------------ agent Wazuh (fonctionnalité facultative de loco)

        const string WazuhService = "WazuhSvc";

        // Entrée « Wazuh Agent » des programmes installés : (code produit pour msiexec, version).
        static KeyValuePair<string, string> WazuhProduct()
        {
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                try
                {
                    using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view).OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
                    {
                        if (root == null) continue;
                        foreach (string name in root.GetSubKeyNames())
                            using (var k = root.OpenSubKey(name))
                            {
                                var display = k == null ? null : k.GetValue("DisplayName") as string;
                                if (display != null && display.StartsWith("Wazuh Agent", StringComparison.OrdinalIgnoreCase))
                                    return new KeyValuePair<string, string>(name, Convert.ToString(k.GetValue("DisplayVersion") ?? ""));
                            }
                    }
                }
                catch (Exception) { }
            }
            return new KeyValuePair<string, string>("", "");
        }

        static bool WazuhRunning()
        {
            try { using (var service = new ServiceController(WazuhService)) return service.Status == ServiceControllerStatus.Running; }
            catch (Exception) { return false; }
        }

        internal static Dictionary<string, object> WazuhInfo()
        {
            try
            {
                string home = WazuhState.Home();
                return WazuhState.Read(home, home != null && WazuhRunning(), home == null ? "" : WazuhProduct().Value);
            }
            catch (Exception) { return new Dictionary<string, object> { { "installed", false } }; }
        }

        static void WazuhRestart()
        {
            using (var service = new ServiceController(WazuhService))
            {
                if (service.Status != ServiceControllerStatus.Stopped && service.Status != ServiceControllerStatus.StopPending)
                {
                    service.Stop();
                    service.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(60));
                }
                service.Start();
                service.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(60));
            }
        }

        // Mot de passe d'enrôlement : dans le fichier que lit l'agent Wazuh, lisible de SYSTEM et des administrateurs
        // seulement — posé vide avec ses droits, rempli ensuite. Jamais en argument d'une commande ni dans un journal.
        static void WazuhPassword(string home, string secret)
        {
            string target = Path.Combine(home, "authd.pass"), tmp = target + ".loco";
            File.WriteAllText(tmp, "");
            var security = new FileSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), FileSystemRights.FullControl, AccessControlType.Allow));
            File.SetAccessControl(tmp, security);
            File.WriteAllText(tmp, secret + "\n", new UTF8Encoding(false));
            if (File.Exists(target)) File.Delete(target);
            File.Move(tmp, target);
        }

        // Installe l'agent Wazuh remis par loco (MSI vérifié par son empreinte), réglé pour le manager, ou le retire.
        static bool WazuhApply(string server, Dictionary<string, object> cmd, out string output)
        {
            var lines = new List<string>();
            object value;
            var spec = cmd.TryGetValue("wazuh", out value) ? value as Dictionary<string, object> : null;
            string op = Str(spec, "op");
            try
            {
                if (op == "remove")
                {
                    var product = WazuhProduct();
                    if (product.Key == "") { output = Lang.T("L'agent Wazuh n'est pas installé sur ce poste.", "The Wazuh agent is not installed on this machine.", "El agente Wazuh no está instalado en este equipo."); return true; }
                    int removed = RunInstaller("msiexec.exe", "/x " + product.Key + " /qn /norestart");
                    if (removed != 0 && removed != 3010 && removed != 1641 && removed != 1605) throw new Exception(Lang.T("code de sortie ", "exit code ", "código de salida ") + removed);
                    output = Lang.T("Agent Wazuh retiré du poste.", "Wazuh agent removed from the machine.", "Agente Wazuh retirado del equipo.");
                    return true;
                }
                var package = spec != null && spec.TryGetValue("package", out value) ? value as Dictionary<string, object> : null;
                string manager = Str(spec, "manager"), group = Str(spec, "group"), file = Path.GetFileName(Str(package, "file")), url = Str(package, "url");
                if (op != "install" || !WazuhState.Manager.IsMatch(manager) || !WazuhState.Group.IsMatch(group) || !WazuhState.Package.IsMatch(file) || !url.StartsWith("/agent/wazuh/"))
                    throw new Exception(Lang.T("Commande Wazuh incomplète.", "Incomplete Wazuh command.", "Orden Wazuh incompleta."));
                bool already = WazuhState.Home() != null;
                string dir = Path.Combine(Path.GetTempPath(), "loco-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(dir);
                try
                {
                    string dest = Path.Combine(dir, file);
                    Download(server + url, dest);
                    if (Sha256File(dest) != Str(package, "sha256").ToLowerInvariant()) throw new Exception(Lang.T("somme SHA-256 invalide", "invalid SHA-256 checksum", "suma SHA-256 no válida"));
                    // Propriétés de déploiement de Wazuh : lues par l'installeur à la première installation (manager, groupes).
                    string args = "/i \"" + dest + "\" /qn /norestart WAZUH_MANAGER=\"" + manager + "\"" + (group == "" ? "" : " WAZUH_AGENT_GROUP=\"" + group + "\"");
                    int code = RunInstaller("msiexec.exe", args);
                    if (code != 0 && code != 3010 && code != 1641) throw new Exception(Lang.T("code de sortie ", "exit code ", "código de salida ") + code);
                }
                finally { try { Directory.Delete(dir, true); } catch (Exception) { } }
                string home = WazuhState.Home();
                if (home == null) throw new Exception(Lang.T("agent Wazuh introuvable après l'installation", "Wazuh agent not found after the installation", "agente Wazuh no encontrado tras la instalación"));
                if (already)
                {
                    string conf = Path.Combine(home, "ossec.conf"), text = File.ReadAllText(conf), changed = WazuhState.WithManager(text, manager);
                    if (changed != text)
                    {
                        File.WriteAllText(conf, changed, new UTF8Encoding(false));
                        lines.Add(Lang.T("Adresse du manager mise à jour : ", "Manager address updated: ", "Dirección del manager actualizada: ") + manager);
                    }
                }
                string secret = Str(spec, "password");
                if (secret != "")
                {
                    WazuhPassword(home, secret);
                    lines.Add(Lang.T("Mot de passe d'enrôlement posé pour l'agent Wazuh.", "Enrollment password set for the Wazuh agent.", "Contraseña de inscripción puesta para el agente Wazuh."));
                }
                else if (File.Exists(Path.Combine(home, "authd.pass")))
                {
                    // Aucun mot de passe réglé dans loco : l'agent Wazuh ne doit plus en envoyer (sinon un manager qui
                    // n'en attend pas refuse l'enrôlement : « Invalid request for new agent »).
                    File.Delete(Path.Combine(home, "authd.pass"));
                    lines.Add(Lang.T("Mot de passe d'enrôlement retiré du poste : l'agent Wazuh s'enrôle sans.", "Enrollment password removed from the machine: the Wazuh agent enrolls without one.",
                                     "Contraseña de inscripción retirada del equipo: el agente Wazuh se inscribe sin ella."));
                }
                WazuhRestart();
                var state = WazuhInfo();
                for (int i = 0; i < 15 && Str(state, "status") != "connected"; i++) { Thread.Sleep(2000); state = WazuhInfo(); }
                string version = Str(state, "version");
                if (Str(state, "status") == "connected")
                    lines.Add(Lang.T("Agent Wazuh " + version + " installé et connecté au manager " + manager + ".",
                                     "Wazuh agent " + version + " installed and connected to manager " + manager + ".",
                                     "Agente Wazuh " + version + " instalado y conectado al manager " + manager + "."));
                else
                    lines.Add(Lang.T("Agent Wazuh " + version + " installé et démarré ; pas encore connecté au manager " + manager + " (ports 1514 et 1515 à vérifier, mot de passe d'enrôlement, groupe).",
                                     "Wazuh agent " + version + " installed and started; not connected to manager " + manager + " yet (check ports 1514 and 1515, the enrollment password, the group).",
                                     "Agente Wazuh " + version + " instalado e iniciado; aún no conectado al manager " + manager + " (compruebe los puertos 1514 y 1515, la contraseña de inscripción, el grupo)."));
                if (Str(state, "status") != "connected")
                    foreach (string line in WazuhState.LastErrors(home, 2))
                        lines.Add(Lang.T("Journal de l'agent Wazuh : ", "Wazuh agent log: ", "Registro del agente Wazuh: ") + line);
                output = string.Join("\n", lines);
                return true;
            }
            catch (Exception e)
            {
                lines.Add(Lang.T("ÉCHEC : agent Wazuh : ", "FAILED: Wazuh agent: ", "FALLO: agente Wazuh: ") + e.Message);
                output = string.Join("\n", lines);
                return false;
            }
        }

        static void Report(Dictionary<string, object> conf, object id, string state, string output, int? code, object inventory, object wazuh = null)
        {
            if (output.Length > OutputLimit) output = output.Substring(output.Length - OutputLimit);
            var data = new Dictionary<string, object> { { "id", id }, { "state", state }, { "output", output }, { "exit_code", code } };
            if (inventory != null) data["inventory"] = inventory;
            if (wazuh != null) data["wazuh"] = wazuh;
            try { PublishIndicator(Post(Str(conf, "server"), "/agent/api/result", data, Str(conf, "token"))); }
            catch (Exception e) { Log(Lang.T("résultat non transmis : ", "result not sent: ", "resultado no transmitido: ") + e.Message); }
        }

        static void PublishIndicator(Dictionary<string, object> answer)
        {
            object value;
            var indicator = answer.TryGetValue("indicator", out value) ? value as Dictionary<string, object> : null;
            if (indicator == null) { DesktopIndicator.Unavailable(Lang.T("État du poste indisponible sur le serveur", "Machine status unavailable on the server", "Estado del equipo no disponible en el servidor")); return; }
            DesktopIndicator.Publish(Str(indicator, "color"), Str(indicator, "reason"));
        }

        internal static void Detached(string file, string args)
        {
            Process.Start(new ProcessStartInfo(file, args) { UseShellExecute = false, CreateNoWindow = true });
        }

        static string ProductCode()
        {
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
                using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view).OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
                    if (root != null)
                        foreach (string name in root.GetSubKeyNames())
                            using (var k = root.OpenSubKey(name))
                                if (k != null && Convert.ToString(k.GetValue("DisplayName")) == "loco-client" && name.StartsWith("{")) return name;
            return "";
        }

        // Retrait poussé depuis loco : réglages effacés, puis désinstallation du MSI (qui arrête ce service).
        static void Uninstall()
        {
            DesktopIndicator.StopSessions();
            try { ReleaseHiddenUpdates(); } catch (Exception) { }     // plus d'agent pour les démasquer : les rendre à Windows
            try { File.Delete(BlocksPath); } catch (Exception) { }
            try { File.Delete(DesktopStatus.FilePath); } catch (Exception) { }
            try { if (File.Exists(WuProxyMarker)) { string o; RunCapture("netsh", "winhttp reset proxy", 20, out o); File.Delete(WuProxyMarker); } } catch (Exception) { }
            try { if (File.Exists(WuBlockMarker)) { using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(WuPolicyKey, true)) if (k != null && k.GetValue("NoAutoUpdate") != null) k.DeleteValue("NoAutoUpdate", false); File.Delete(WuBlockMarker); } } catch (Exception) { }
            foreach (var f in new[] { ConfigPath, StatePath, CaPath }) try { File.Delete(f); } catch (Exception) { }
            string code = ProductCode();
            if (code != "") Detached("msiexec.exe", "/x " + code + " /qn /norestart");
            else Log(Lang.T("produit loco-client introuvable : désinstallation à faire à la main", "loco-client product not found: uninstall manually", "producto loco-client no encontrado: desinstalación manual necesaria"));
        }

        // Proxy WinHTTP du poste dirigé vers le cache Windows Update de loco (sans DNS), piloté par le portail.
        // On ne touche au proxy que si loco l'a posé (marqueur local) : pas de collision avec un proxy d'entreprise.
        static readonly string WuProxyMarker = Path.Combine(Root, "winhttp-proxy");

        static void ApplyWuProxy(Dictionary<string, object> answer)
        {
            object v;
            if (!answer.TryGetValue("wu_proxy", out v)) return;   // serveur plus ancien : ne rien changer
            string marker = null;
            try { if (File.Exists(WuProxyMarker)) marker = File.ReadAllText(WuProxyMarker).Trim(); } catch (Exception) { }
            if (v is Dictionary<string, object>)
            {
                var d = (Dictionary<string, object>)v;
                string target = Str(d, "host") + ":" + Convert.ToString(d["port"]);
                string bypass = Str(d, "bypass");
                string desired = target + "|" + bypass;
                string output;
                RunCapture("netsh", "winhttp show proxy", 20, out output);
                if (marker == desired && output.Contains(target)) return;   // déjà en place
                if (RunCapture("netsh", "winhttp set proxy proxy-server=\"" + target + "\" bypass-list=\"" + bypass + "\"", 20, out output) == 0)
                {
                    try { File.WriteAllText(WuProxyMarker, desired); } catch (Exception) { }
                    Log(Lang.T("proxy WinHTTP dirigé vers ", "WinHTTP proxy pointed at ", "proxy WinHTTP dirigido a ") + target + Lang.T(" (cache Windows Update)", " (Windows Update cache)", " (caché de Windows Update)"));
                }
                else Log(Lang.T("proxy WinHTTP : échec du réglage (", "WinHTTP proxy: failed to set (", "proxy WinHTTP: fallo al configurar (") + output.Replace("\n", " ").Trim() + ")");
            }
            else ClearWuProxy(Lang.T("cache Windows Update par proxy désactivé", "Windows Update proxy cache disabled", "caché de Windows Update por proxy desactivada"));
        }

        // Retire le proxy WinHTTP seulement s'il a été posé par loco (marqueur) : Windows Update repasse en direct.
        static void ClearWuProxy(string reason)
        {
            if (!File.Exists(WuProxyMarker)) return;
            string output;
            if (RunCapture("netsh", "winhttp reset proxy", 20, out output) == 0)
            {
                try { File.Delete(WuProxyMarker); } catch (Exception) { }
                Log(Lang.T("proxy WinHTTP retiré (", "WinHTTP proxy removed (", "proxy WinHTTP retirado (") + reason + ")");
            }
        }

        // Gel des mises à jour Windows piloté par le portail. loco pose une politique « ne pas installer tout seul »
        // (HKLM\...\WindowsUpdate\AU\NoAutoUpdate=1) et ne défait que ce qu'il a posé (marqueur local) : pas de collision
        // avec une GPO d'entreprise déjà en place quand loco ne demande rien.
        const string WuPolicyKey = @"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU";
        static readonly string WuBlockMarker = Path.Combine(Root, "windows-update-blocked");

        static void ApplyWindowsUpdatePolicy(Dictionary<string, object> answer)
        {
            object v;
            if (!answer.TryGetValue("windows_update", out v) || !(v is Dictionary<string, object>)) return;   // serveur plus ancien : ne rien changer
            bool blocked = Convert.ToBoolean(((Dictionary<string, object>)v)["blocked"]);
            bool posed = File.Exists(WuBlockMarker);
            try
            {
                if (blocked)
                {
                    using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).CreateSubKey(WuPolicyKey))
                        k.SetValue("NoAutoUpdate", 1, RegistryValueKind.DWord);
                    if (!posed)
                    {
                        try { File.WriteAllText(WuBlockMarker, "1"); } catch (Exception) { }
                        Log(Lang.T("mises à jour Windows gelées (pilotées par loco)", "Windows Update frozen (driven by loco)", "Windows Update congelado (gestionado por loco)"));
                    }
                }
                else if (posed)
                {
                    using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(WuPolicyKey, true))
                        if (k != null && k.GetValue("NoAutoUpdate") != null) k.DeleteValue("NoAutoUpdate", false);
                    try { File.Delete(WuBlockMarker); } catch (Exception) { }
                    Log(Lang.T("mises à jour Windows rendues à Windows", "Windows Update handed back to Windows", "Windows Update devuelto a Windows"));
                }
            }
            catch (Exception e)
            {
                Log(Lang.T("politique Windows Update : échec (", "Windows Update policy: failed (", "política de Windows Update: fallo (") + e.Message + ")");
            }
        }

        // Contacts manqués d'affilée : au-delà de UnreachableLimit (~6 min), le proxy vers un loco absent est retiré
        // pour ne pas bloquer Windows Update ; il est reposé au premier contact réussi.
        static int unreachable;
        const int UnreachableLimit = 3;

        // Nouvelle version de loco : l'agent installe le MSI servi par le serveur (empreinte vérifiée).
        static void SelfUpdate(Dictionary<string, object> conf, Dictionary<string, object> answer)
        {
            object msi;
            if (!answer.TryGetValue("agent_msi", out msi) || !(msi is Dictionary<string, object>)) return;
            var info = (Dictionary<string, object>)msi;
            string wanted = Str(info, "version"), sha = Str(info, "sha256").ToLowerInvariant();
            System.Version target;
            if (!System.Version.TryParse(wanted, out target) || target <= typeof(Agent).Assembly.GetName().Version || sha.Length != 64) return;
            string path = Path.Combine(Root, "loco-client-" + wanted + ".msi");
            try
            {
                Download(Str(conf, "server") + "/agent/files/loco-client.msi", path);
                if (Sha256File(path) != sha) { File.Delete(path); throw new Exception(Lang.T("empreinte du MSI incorrecte", "incorrect MSI fingerprint", "huella del MSI incorrecta")); }
                Log(Lang.T("mise à jour de l'agent : ", "agent update: ", "actualización del agente: ") + Version + " -> " + wanted);
                // Journal écrit ligne à ligne (« ! ») : lisible même après une coupure en pleine mise à jour.
                Detached("msiexec.exe", "/i \"" + path + "\" /qn /norestart /l*v! \"" + Path.Combine(Root, "update.log") + "\"");
            }
            catch (Exception e) { Log(Lang.T("mise à jour de l'agent impossible : ", "agent update failed: ", "no se pudo actualizar el agente: ") + e.Message); }
        }

        static void Restart(string reason)
        {
            Detached("shutdown.exe", "/r /t 60 /c \"loco : " + reason + "\"");
        }

        public static bool RunOnce(WaitHandle stopping)
        {
            if (!Monitor.TryEnter(runLock)) return true;
            try
            {
                ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
                var conf = ReadJson(ConfigPath);
                if (Str(conf, "token") == "")
                {
                    DesktopIndicator.Unavailable(Lang.T("Inscription du poste en attente", "Machine enrollment pending", "Inscripción del equipo pendiente"));
                    try { conf = Enroll(conf); }
                    catch (LocoHttpException e) { Log(e.Status == 403 ? Lang.T("clé d'inscription refusée", "enrollment key refused", "clave de inscripción rechazada") : Lang.T("inscription refusée : ", "enrollment refused: ", "inscripción rechazada: ") + e.Message); return false; }
                    catch (Exception e) { Log(Lang.T("inscription impossible : ", "enrollment failed: ", "inscripción imposible: ") + e.Message); return false; }
                    if (conf == null) { Log(Lang.T("poste non inscrit : adresse du serveur et clé absentes (installeur ou GPO)", "machine not enrolled: server address and key missing (installer or GPO)", "equipo no inscrito: dirección del servidor y clave ausentes (instalador o GPO)")); return false; }
                }
                LoadCa();
                var state = ReadJson(StatePath) ?? new Dictionary<string, object>();
                double lastScan = 0; int scanHours = 6; bool rescan = true;
                object v;
                if (state.TryGetValue("last_scan", out v)) lastScan = Convert.ToDouble(v);
                if (state.TryGetValue("scan_hours", out v)) scanHours = Math.Max(1, Convert.ToInt32(v));
                if (state.TryGetValue("rescan", out v)) rescan = Convert.ToBoolean(v);
                double now = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
                var data = Identity();
                data["metrics"] = Metrics();
                // Actions reçues que l'agent traite encore : aucune. Il exécute toutes les actions d'une réponse avant le
                // contact suivant (runLock). Le serveur sait ainsi qu'une action notée « transmise » n'a jamais démarré
                // (réponse perdue, service ou poste arrêté avant de la lire) et la transmet de nouveau.
                data["holding"] = new ArrayList();
                if (rescan || firstRun || now - lastScan > scanHours * 3600)
                {
                    data["inventory"] = Inventory();
                    lastScan = now;
                    rescan = false;
                }
                Dictionary<string, object> answer;
                try
                {
                    answer = Checkin(conf, data);
                    if (firstRun) ClearInstallerKey();
                    firstRun = false;
                    unreachable = 0;
                    PublishIndicator(answer);
                }
                catch (LocoHttpException e)
                {
                    DesktopIndicator.Unavailable(Lang.T("Contact avec loco refusé", "Contact with loco refused", "Contacto con loco rechazado"));
                    if (e.Status == 401)
                    {
                        // Un MSI d'enrôlement peut avoir été installé sur un service encore présent.
                        // Seule une clé locale fournie par l'installateur autorise ce nouvel enrôlement.
                        if (Setting("Key", true) != "")
                        {
                            try
                            {
                                if (Enroll(conf, true) != null)
                                {
                                    DesktopIndicator.Unavailable(Lang.T("Inscription du poste en cours", "Machine enrollment in progress", "Inscripción del equipo en curso"));
                                    return true;
                                }
                            }
                            catch (Exception enrollment)
                            {
                                Log(Lang.T("réinscription impossible : ", "re-enrollment failed: ", "no se pudo volver a inscribir: ") + enrollment.Message);
                            }
                        }
                        Log(Lang.T("jeton refusé par le serveur (poste retiré du parc ?) : réinstallez l'agent pour le réinscrire", "token refused by the server (machine removed from the fleet?): reinstall the agent to re-enroll it", "token rechazado por el servidor (¿equipo retirado del parque?): reinstale el agente para volver a inscribirlo"));
                        ClearWuProxy(Lang.T("poste refusé par loco", "machine refused by loco", "equipo rechazado por loco"));
                    }
                    else Log(Lang.T("serveur : ", "server: ", "servidor: ") + e.Message);
                    return false;
                }
                catch (Exception e)
                {
                    DesktopIndicator.Unavailable(Lang.T("Serveur loco injoignable", "loco server unreachable", "Servidor loco inaccesible"));
                    Log(Lang.T("serveur injoignable : ", "server unreachable: ", "servidor inaccesible: ") + e.Message);
                    if (++unreachable >= UnreachableLimit) ClearWuProxy(Lang.T("loco injoignable depuis ", "loco unreachable for ", "loco inaccesible desde hace ") + unreachable + Lang.T(" contacts", " check-ins", " contactos"));
                    if (data.ContainsKey("inventory")) rescan = true;
                    SaveState(lastScan, scanHours, rescan);
                    return false;
                }
                if (answer.TryGetValue("interval", out v)) Interval = Math.Max(30, Convert.ToInt32(v));
                if (answer.TryGetValue("scan_hours", out v)) scanHours = Math.Max(1, Convert.ToInt32(v));
                if (ApplyUpdateBlocks(answer)) rescan = true;
                try { ForwardLogs(conf, answer, stopping); }
                catch (Exception e) when (!(e is OutOfMemoryException))     // serveur syslog absent (503), journal illisible : au prochain contact
                {
                    if (!(e is LocoHttpException)) Log(Lang.T("journaux non remontés : ", "logs not forwarded: ", "registros no enviados: ") + e.Message);
                }
                foreach (Dictionary<string, object> cmd in (answer["commands"] as ArrayList) ?? new ArrayList())
                {
                    if (stopping != null && stopping.WaitOne(0)) break;
                    object id = cmd["id"];
                    string action = Str(cmd, "action");
                    Log("action " + id + " : " + action);
                    Report(conf, id, "running", "", null, null);
                    if (action == "scan")
                    {
                        var inv = Inventory();
                        lastScan = now;
                        string err = Str(inv, "error");
                        Report(conf, id, err == "" ? "success" : "failed", err == "" ? "Inventaire transmis." : err, 0, inv);
                    }
                    else if (action == "update" || action == "install-app" || action == "os-upgrade")
                    {
                        var lines = new List<string>();
                        bool ok = true;
                        // Avancement : au plus une remontée par seconde pour une même étape, immédiate à chaque changement.
                        string lastStep = ""; var lastSent = DateTime.MinValue;
                        // Suivi mise à jour par mise à jour (installée, en cours, en échec), pour l'affichage en direct.
                        var pkgOrder = new List<string>();
                        var pkgState = new Dictionary<string, string>();
                        Action<string, string> markPkg = (name, st) =>
                        {
                            if (string.IsNullOrEmpty(name)) return;
                            if (!pkgState.ContainsKey(name)) pkgOrder.Add(name);
                            else if (pkgState[name] == "failed" && st != "failed") return;
                            pkgState[name] = st;
                        };
                        Progress progress = (step, app, index, total, percent, phase) =>
                        {
                            string key = step + "|" + phase + "|" + app + "|" + index;
                            if (key == lastStep && (DateTime.UtcNow - lastSent).TotalSeconds < 1) return;
                            lastStep = key; lastSent = DateTime.UtcNow;
                            var detail = new Dictionary<string, object> { { "step", step }, { "app", app } };
                            if (index > 0 && total > 0) { detail["index"] = index; detail["total"] = total; }
                            if (percent >= 0) detail["percent"] = percent;
                            if (!string.IsNullOrEmpty(phase)) detail["phase"] = phase;
                            if (pkgOrder.Count > 0)
                            {
                                var items = new List<object>();
                                int nOk = 0, nFailed = 0, nRunning = 0;
                                foreach (var n in pkgOrder)
                                {
                                    string s = pkgState[n];
                                    items.Add(new Dictionary<string, object> { { "name", n }, { "state", s } });
                                    if (s == "ok") nOk++; else if (s == "failed") nFailed++; else if (s == "running") nRunning++;
                                }
                                detail["items"] = items;
                                detail["counts"] = new Dictionary<string, object> { { "ok", nOk }, { "failed", nFailed }, { "running", nRunning }, { "total", pkgOrder.Count } };
                            }
                            var report = new Dictionary<string, object> { { "id", id }, { "state", "running" }, { "output", "" }, { "exit_code", null }, { "progress", detail } };
                            try { Post(Str(conf, "server"), "/agent/api/result", report, Str(conf, "token")); }
                            catch (Exception) { }
                        };
                        if (action == "os-upgrade")
                        {
                            // Nouvelle version de Windows : elle seule, puis le redémarrage qui la termine.
                            var target = cmd.ContainsKey("target") ? cmd["target"] as Dictionary<string, object> : null;
                            bool restart = false;
                            try { ok = target != null && InstallWindowsUpgrade(Str(target, "id"), lines, progress, out restart); }
                            catch (Exception e) { ok = false; lines.Add(Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + e.Message); }
                            if (target == null) lines.Add(Lang.T("Nouvelle version non précisée.", "New version not specified.", "Nueva versión no indicada."));
                            var after = Inventory();
                            lastScan = now;
                            if (ok) lines.Add(Lang.T("Redémarrage dans une minute : Windows termine l'installation pendant ce redémarrage (jusqu'à une heure).", "Rebooting in one minute: Windows finishes the installation during this restart (up to an hour).", "Reinicio en un minuto: Windows termina la instalación durante este reinicio (hasta una hora)."));
                            Report(conf, id, ok ? "success" : "failed", string.Join("\n", lines), ok ? 0 : 1, after);
                            if (ok) { SaveState(lastScan, scanHours, true); Restart(Lang.T("redémarrage pour la nouvelle version de Windows", "reboot for the new Windows version", "reinicio para la nueva versión de Windows")); return true; }
                            continue;
                        }
                        bool selection = false;
                        if (action == "update")
                        {
                            // Sélection faite dans loco (« only ») : seulement ces mises à jour Windows, sans les logiciels du catalogue.
                            HashSet<string> only = null;
                            object onlyValue;
                            if (cmd.TryGetValue("only", out onlyValue) && onlyValue is Dictionary<string, object>)
                            {
                                only = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                                selection = true;
                                object ids;
                                if (((Dictionary<string, object>)onlyValue).TryGetValue("updates", out ids) && ids is System.Collections.IEnumerable)
                                    foreach (object one in (System.Collections.IEnumerable)ids) only.Add(Convert.ToString(one));
                            }
                            // Sélection sans mise à jour Windows (seulement des logiciels) : Windows Update n'est pas consulté.
                            if (only == null || only.Count > 0)
                            {
                                try { ok = InstallWindowsUpdates(lines, only, progress, markPkg); }
                                catch (Exception e) { ok = false; lines.Add(Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + e.Message); }
                            }
                        }
                        object apps;
                        cmd.TryGetValue("apps", out apps);
                        if (!selection && !InstallCatalogApps(Str(conf, "server"), apps, lines, progress)) ok = false;
                        object software, softwareFallback;
                        cmd.TryGetValue("software", out software);
                        cmd.TryGetValue("software_fallback", out softwareFallback);
                        if (action == "update") UpgradeSoftware(software, softwareFallback as Dictionary<string, object>, lines, progress, markPkg);   // au mieux : ne fait pas échouer la commande
                        var inv = Inventory();
                        lastScan = now;
                        bool reboot = Convert.ToBoolean(cmd.ContainsKey("reboot") ? cmd["reboot"] : false) && (bool)inv["reboot_required"] && ok;
                        if (reboot) lines.Add(Lang.T("Redémarrage dans une minute.", "Rebooting in one minute.", "Reinicio en un minuto."));
                        Report(conf, id, ok ? "success" : "failed", string.Join("\n", lines), ok ? 0 : 1, inv);
                        if (reboot) { SaveState(lastScan, scanHours, false); Restart(Lang.T("redémarrage après mise à jour", "reboot after update", "reinicio tras la actualización")); return true; }
                    }
                    else if (action == "gpu-driver")
                    {
                        // Pilote graphique NVIDIA : téléchargement (avec son avancement), signature, installation silencieuse.
                        var lines = new List<string>();
                        string app = "NVIDIA " + Str(cmd, "version"), lastKey = "";
                        var lastSent = DateTime.MinValue;
                        Action<string, int> step = (stage, percent) =>
                        {
                            string key = stage + "|" + percent;
                            if (key == lastKey || (stage == "download" && (DateTime.UtcNow - lastSent).TotalSeconds < 1)) return;
                            lastKey = key; lastSent = DateTime.UtcNow;
                            var detail = new Dictionary<string, object> { { "step", stage }, { "app", app } };
                            if (percent >= 0) detail["percent"] = percent;
                            var report = new Dictionary<string, object> { { "id", id }, { "state", "running" }, { "output", "" }, { "exit_code", null }, { "progress", detail } };
                            try { Post(Str(conf, "server"), "/agent/api/result", report, Str(conf, "token")); }
                            catch (Exception) { }
                        };
                        bool ok = InstallGpuDriver(cmd, lines, step);
                        var inv = Inventory();
                        lastScan = now;
                        Report(conf, id, ok ? "success" : "failed", string.Join("\n", lines), ok ? 0 : 1, inv);
                    }
                    else if (action == "run-script")
                    {
                        string output; int code = RunScript(cmd, out output);
                        Report(conf, id, code == 0 ? "success" : "failed", output, code, null);
                    }
                    else if (action == "wazuh")
                    {
                        string output; bool done = WazuhApply(Str(conf, "server"), cmd, out output);
                        Report(conf, id, done ? "success" : "failed", output, done ? 0 : 1, null, WazuhInfo());
                    }
                    else if (action == "wake-relay")
                    {
                        string output; bool sent = RelayWake(cmd, out output);
                        Report(conf, id, sent ? "success" : "failed", output, sent ? 0 : 1, null);
                    }
                    else if (action == "reboot")
                    {
                        Report(conf, id, "success", Lang.T("Redémarrage dans une minute.", "Rebooting in one minute.", "Reinicio en un minuto."), 0, null);
                        SaveState(lastScan, scanHours, true);
                        Restart(Lang.T("redémarrage demandé", "reboot requested", "reinicio solicitado"));
                        return true;
                    }
                    else if (action == "uninstall")
                    {
                        Report(conf, id, "success", Lang.T("Agent retiré du poste.", "Agent removed from the machine.", "Agente retirado del equipo."), 0, null);
                        Log(Lang.T("retrait de l'agent demandé par le serveur", "agent removal requested by the server", "retirada del agente solicitada por el servidor"));
                        Uninstall();
                        return true;
                    }
                    else if (action == "remove-package")
                    {
                        // Désinstallation d'un logiciel suivi par winget (son identifiant winget), demandée depuis la fiche du poste.
                        var names = new List<string>();
                        object pkgs;
                        if (cmd.TryGetValue("packages", out pkgs) && pkgs is System.Collections.IEnumerable)
                            foreach (object one in (System.Collections.IEnumerable)pkgs) { string s = Convert.ToString(one); if (!string.IsNullOrEmpty(s)) names.Add(s); }
                        var lines = new List<string>();
                        bool ok = names.Count > 0;
                        if (names.Count == 0) lines.Add(Lang.T("Aucun logiciel indiqué.", "No software given.", "Ningún software indicado."));
                        else
                        {
                            string libraries;
                            string winget = WingetPath(out libraries);
                            if (winget == null) { ok = false; lines.Add(Lang.T("winget absent du poste (paquet App Installer de Microsoft).", "winget is not on this machine (Microsoft App Installer package).", "winget no está en el equipo (paquete App Installer de Microsoft).")); }
                            else foreach (string pid in names)
                            {
                                string output;
                                int code = RunCapture(winget, "uninstall --id \"" + pid + "\" --exact --silent --accept-source-agreements --disable-interactivity", InstallerTimeout, out output, libraries);
                                lines.Add(pid + " : " + WingetMessage(output, code));
                                if (code != 0) ok = false;
                            }
                        }
                        var inv = Inventory();
                        lastScan = now;
                        Report(conf, id, ok ? "success" : "failed", string.Join("\n", lines), ok ? 0 : 1, inv);
                    }
                    else Report(conf, id, "failed", Lang.T("Action non prise en charge : ", "Action not supported: ", "Acción no admitida: ") + action, 1, null);
                }
                SaveState(lastScan, scanHours, rescan);
                ApplyTrust(answer);
                ApplyWuProxy(answer);
                ApplyWindowsUpdatePolicy(answer);
                ApplyWake(answer);
                SelfUpdate(conf, answer);
                return true;
            }
            finally { Monitor.Exit(runLock); }
        }

        // ------------------------------------------------------------ journaux du poste vers syslog (par loco)

        const int LogLines = 300;                       // événements par journal et par contact
        const int LogMessage = 4000;
        const long AuditFailure = 0x10000000000000L;    // mot-clé Windows : échec d'audit

        static long NewestRecord(string channel)
        {
            var query = new EventLogQuery(channel, PathType.LogName) { ReverseDirection = true };
            using (var reader = new EventLogReader(query))
            using (var record = reader.ReadEvent())
                return record != null && record.RecordId.HasValue ? record.RecordId.Value : 0;
        }

        // Filtre XPath de l'événement : gravité minimale, ou journal Sécurité (niveaux sans objet : échecs d'audit, et aussi
        // les réussites à partir du niveau Informations).
        static string LogFilter(string channel, int minimum)
        {
            if (channel == "Security") return minimum >= 6 ? "" : " and band(Keywords," + AuditFailure + ")";
            if (minimum <= 3) return " and (Level=1 or Level=2)";
            if (minimum == 4) return " and (Level=1 or Level=2 or Level=3)";
            return " and (Level=0 or Level=1 or Level=2 or Level=3 or Level=4)";
        }

        static Dictionary<string, object> LogEntry(EventRecord record, string channel)
        {
            int level = record.Level.HasValue ? record.Level.Value : 4;
            int severity = level == 1 ? 2 : level == 2 ? 3 : level == 3 ? 4 : level == 5 ? 7 : 6;
            if (channel == "Security") severity = record.Keywords.HasValue && (record.Keywords.Value & AuditFailure) != 0 ? 4 : 6;
            string text = null;
            try { text = record.FormatDescription(); }
            catch (Exception) { }                       // description du fournisseur absente ou illisible
            if (string.IsNullOrWhiteSpace(text)) text = Lang.T("Événement ", "Event ", "Evento ") + record.Id;
            if (text.Length > LogMessage) text = text.Substring(0, LogMessage);
            DateTime when = (record.TimeCreated ?? DateTime.Now).ToUniversalTime();
            return new Dictionary<string, object> {
                { "t", (when - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds }, { "p", severity },
                { "f", channel == "Security" ? 4 : channel == "System" ? 3 : 1 }, { "a", record.ProviderName ?? "-" },
                { "i", record.ProcessId.HasValue ? record.ProcessId.Value.ToString() : "-" }, { "n", record.Id.ToString() },
                { "c", channel }, { "m", text } };
        }

        // Remonte à loco les journaux d'événements, si la consigne du serveur le demande. Position avancée seulement quand
        // loco a relayé : si le serveur syslog est absent (503), les événements repartent au prochain contact.
        static void ForwardLogs(Dictionary<string, object> conf, Dictionary<string, object> answer, WaitHandle stopping)
        {
            object value;
            var settings = answer.TryGetValue("syslog", out value) ? value as Dictionary<string, object> : null;
            if (settings == null)
            {
                if (File.Exists(LogStatePath)) File.Delete(LogStatePath);   // envoi coupé : à la reprise, on repart de ce moment
                return;
            }
            int minimum = settings.TryGetValue("min", out value) ? Convert.ToInt32(value) : 4;
            bool security = settings.TryGetValue("security", out value) && Convert.ToBoolean(value);
            var positions = ReadJson(LogStatePath) ?? new Dictionary<string, object>();
            var channels = security ? new[] { "System", "Application", "Security" } : new[] { "System", "Application" };
            var watch = Stopwatch.StartNew();
            foreach (string channel in channels)
            {
                if (stopping.WaitOne(0) || watch.Elapsed.TotalSeconds > 30) return;
                long newest = NewestRecord(channel);
                if (!positions.ContainsKey(channel))    // à partir de maintenant, pas tout l'historique
                {
                    positions[channel] = newest;
                    SaveJson(LogStatePath, positions);
                    continue;
                }
                long position = Convert.ToInt64(positions[channel]);
                if (newest < position) position = 0;    // journal vidé : numéros repartis de zéro
                if (newest <= position) continue;
                string xpath = "*[System[EventRecordID>" + position + " and EventRecordID<=" + newest + LogFilter(channel, minimum) + "]]";
                var entries = new List<object>();
                long last = position;
                using (var reader = new EventLogReader(new EventLogQuery(channel, PathType.LogName, xpath)))
                {
                    EventRecord record;
                    while (entries.Count < LogLines && (record = reader.ReadEvent()) != null)
                        using (record)
                        {
                            entries.Add(LogEntry(record, channel));
                            if (record.RecordId.HasValue) last = record.RecordId.Value;
                        }
                }
                if (entries.Count > 0)
                {
                    var reply = Post(Str(conf, "server"), "/agent/api/logs", new Dictionary<string, object> { { "entries", entries } }, Str(conf, "token"), 30000);
                    if (reply.ContainsKey("off")) return;
                }
                positions[channel] = entries.Count >= LogLines ? last : newest;
                SaveJson(LogStatePath, positions);
            }
        }

        static void SaveState(double lastScan, int scanHours, bool rescan)
        {
            try
            {
                SaveJson(StatePath, new Dictionary<string, object> { { "last_scan", lastScan }, { "scan_hours", scanHours }, { "rescan", rescan } });
            }
            catch (Exception e) { Log(Lang.T("état non enregistré : ", "status not saved: ", "estado no guardado: ") + e.Message); }
        }

        public static void PrintStatus()
        {
            var conf = ReadJson(ConfigPath);
            string server = conf != null ? Str(conf, "server") : Lang.T("non inscrit (", "not enrolled (", "no inscrito (") + Setting("Server") + ")";
            var rows = new System.Collections.Generic.List<string[]> {
                new[] { Lang.T("Agent", "Agent", "Agente"), "loco-client " + Version },
                new[] { Lang.T("Serveur", "Server", "Servidor"), server },
                new[] { Lang.T("Identifiant", "ID", "Identificador"), conf != null ? Str(conf, "uid") : "-" } };
            if (conf != null && KnownServers(conf).Count > 1)
                rows.Add(new[] { Lang.T("De secours", "Fallback", "De reserva"), string.Join(" ", KnownServers(conf).Skip(1)) });
            int width = 0; foreach (var r in rows) if (r[0].Length > width) width = r[0].Length;
            foreach (var r in rows) Console.WriteLine(r[0].PadRight(width) + " : " + r[1]);
        }
    }
}
