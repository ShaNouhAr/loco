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
            // Premier contact peu après le démarrage (réseau prêt), puis à l'intervalle donné par le serveur.
            int delay = 15000;
            while (!stopping.WaitOne(delay))
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
        static readonly string LogPath = Path.Combine(Root, "loco-client.log");
        const string SecurityCategory = "0fa1201d-4330-4fa8-8ae9-b877473b6441";   // Windows Update : Security Updates
        const int OutputLimit = 60000;
        const int InstallerTimeout = 1800;
        public static int Interval = 120;
        // Premier contact depuis le démarrage du service (démarrage du poste, mise à jour de l'agent) :
        // inventaire refait, pour ne pas garder l'état d'avant un redémarrage (mises à jour installées depuis).
        static bool firstRun = true;
        static X509Certificate2 ca;
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

        static void SaveJson(string path, object data)
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, Json.Serialize(data), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(tmp, path, null); else File.Move(tmp, path);
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
            if (ca == null) return errors == SslPolicyErrors.None;
            if ((errors & (SslPolicyErrors.RemoteCertificateNotAvailable | SslPolicyErrors.RemoteCertificateNameMismatch)) != 0) return false;
            using (var c = new X509Chain())
            {
                c.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                c.ChainPolicy.VerificationFlags = X509VerificationFlags.AllowUnknownCertificateAuthority;
                c.ChainPolicy.ExtraStore.Add(ca);
                if (!c.Build(new X509Certificate2(cert))) return false;
                return c.ChainElements[c.ChainElements.Count - 1].Certificate.Thumbprint == ca.Thumbprint;
            }
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
            ca = File.Exists(CaPath) ? new X509Certificate2(File.ReadAllBytes(CaPath)) : null;
        }

        internal static Dictionary<string, object> RemoteExchange(object data)
        {
            var conf = ReadJson(ConfigPath);
            if (Str(conf, "token") == "") throw new IOException(Lang.T("Inscription du poste en attente", "Machine enrollment pending", "Inscripción del equipo pendiente"));
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            if (ca == null) LoadCa();
            return Post(Str(conf, "server"), "/agent/api/remote", data, Str(conf, "token"), 10000);
        }

        // ------------------------------------------------------------ inscription

        static string Setting(string name)
        {
            foreach (var key in new[] { @"SOFTWARE\Policies\loco\Client", @"SOFTWARE\loco\Client" })
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
            var cert = new X509Certificate2(File.ReadAllBytes(tmp));
            string digest;
            using (var sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(cert.RawData)).Replace("-", "").ToLowerInvariant();
            if (digest != pin) { File.Delete(tmp); throw new Exception(Lang.T("empreinte de l'autorité différente de celle attendue : inscription refusée", "authority fingerprint differs from the expected one: enrollment refused", "huella de la autoridad distinta de la esperada: inscripción rechazada")); }
            if (File.Exists(CaPath)) File.Delete(CaPath);
            File.Move(tmp, CaPath);
        }

        static Dictionary<string, object> Enroll(Dictionary<string, object> conf)
        {
            string server = Setting("Server").TrimEnd('/'), key = Setting("Key"), pin = Setting("CaSha256");
            if (server == "" || key == "") return null;
            SecureRoot();
            if (pin != "") FetchCa(server, pin); else if (File.Exists(CaPath)) File.Delete(CaPath);
            LoadCa();
            string uid = Str(conf, "uid");
            if (uid == "") uid = Guid.NewGuid().ToString("N");
            var data = Identity();
            data["key"] = key;
            data["uid"] = uid;
            var answer = Post(server, "/agent/api/enroll", data, null);
            conf = new Dictionary<string, object> {
                { "server", server }, { "uid", uid }, { "token", Str(answer, "token") }, { "enrolled", DateTime.Now.ToString("s") } };
            SaveJson(ConfigPath, conf);
            SaveJson(StatePath, new Dictionary<string, object> { { "last_scan", 0 }, { "scan_hours", 6 }, { "rescan", true } });
            Log(Lang.T("poste inscrit auprès de ", "machine enrolled with ", "equipo inscrito en ") + server);
            // La clé d'inscription n'est plus utile : retirée du registre (lisible par les utilisateurs du poste).
            try
            {
                using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(@"SOFTWARE\loco\Client", true))
                    if (k != null && k.GetValue("Key") != null) k.DeleteValue("Key");
            }
            catch (Exception) { }
            return conf;
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
            var addresses = new List<string>();
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up || nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                foreach (var a in nic.GetIPProperties().UnicastAddresses)
                {
                    var ip = a.Address;
                    if (IPAddress.IsLoopback(ip) || ip.IsIPv6LinkLocal || ip.ToString().StartsWith("169.254.")) continue;
                    if (addresses.Count < 16) addresses.Add(ip.ToString().Split('%')[0]);
                }
            }
            return new Dictionary<string, object> {
                { "hostname", Environment.MachineName.ToLowerInvariant() }, { "agent_version", Version },
                { "addresses", addresses }, { "os", new Dictionary<string, object> { { "family", "windows" }, { "name", caption } } } };
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

        static Dictionary<string, object> Inventory()
        {
            var updates = new List<Dictionary<string, object>>();
            string error = "";
            try
            {
                dynamic result = NewSession().CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'");
                foreach (dynamic u in result.Updates)
                {
                    var kbs = new List<string>();
                    foreach (object kb in u.KBArticleIDs) kbs.Add("KB" + kb);
                    updates.Add(new Dictionary<string, object> { { "name", Convert.ToString(u.Title) },
                        { "version", string.Join(" ", kbs) }, { "security", IsSecurity(u) } });
                }
            }
            catch (Exception e) { error = Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + e.Message; }
            return new Dictionary<string, object> { { "updates", updates }, { "apps", InstalledApps() },
                { "reboot_required", RebootRequired() }, { "error", error } };
        }

        // ------------------------------------------------------------ actions

        static bool InstallWindowsUpdates(List<string> lines)
        {
            dynamic session = NewSession();
            dynamic found = session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'");
            dynamic coll = Activator.CreateInstance(Type.GetTypeFromProgID("Microsoft.Update.UpdateColl"));
            foreach (dynamic u in found.Updates)
            {
                if ((bool)u.InstallationBehavior.CanRequestUserInput) { lines.Add(Lang.T("Ignorée (interactive) : ", "Skipped (interactive): ", "Omitida (interactiva): ") + u.Title); continue; }
                if (!(bool)u.EulaAccepted) u.AcceptEula();
                coll.Add(u);
            }
            if ((int)coll.Count == 0) { lines.Add(Lang.T("Windows Update : aucune mise à jour.", "Windows Update: no updates.", "Windows Update: sin actualizaciones.")); return true; }
            lines.Add(Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + coll.Count + Lang.T(" mise(s) à jour à installer.", " update(s) to install.", " actualización(es) por instalar."));
            dynamic downloader = session.CreateUpdateDownloader();
            downloader.Updates = coll;
            downloader.Download();
            dynamic installer = session.CreateUpdateInstaller();
            installer.Updates = coll;
            dynamic result = installer.Install();
            bool ok = true;
            for (int i = 0; i < (int)coll.Count; i++)
            {
                dynamic r = result.GetUpdateResult(i);
                int code = (int)r.ResultCode;
                string state = code == 2 ? "OK" : code == 3 ? Lang.T("OK avec erreurs", "OK with errors", "OK con errores") : code == 4 ? Lang.T("ÉCHEC", "FAILED", "FALLO") : code == 5 ? Lang.T("ANNULÉE", "CANCELLED", "CANCELADA") : "code " + code;
                if (code >= 4) ok = false;
                int hr = (int)r.HResult;
                lines.Add("  " + state + (hr != 0 ? string.Format(" (0x{0:X8})", hr) : "") + " : " + coll.Item(i).Title);
            }
            if ((bool)result.RebootRequired) lines.Add(Lang.T("Redémarrage nécessaire pour terminer.", "Reboot required to finish.", "Reinicio necesario para terminar."));
            return ok;
        }

        // Script de la bibliothèque loco : contenu vérifié par son empreinte, exécuté en SYSTEM par PowerShell.
        static int RunScript(Dictionary<string, object> cmd, out string output)
        {
            var script = cmd.ContainsKey("script") ? cmd["script"] as Dictionary<string, object> : null;
            string content = script == null ? "" : Str(script, "content");
            if (script == null || Str(script, "platform") != "windows") { output = Lang.T("Script destiné à un autre système.", "Script intended for another system.", "Script destinado a otro sistema."); return 1; }
            using (var sha = SHA256.Create())
                if (BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(content))).Replace("-", "").ToLowerInvariant()
                    != Str(script, "sha256").ToLowerInvariant()) { output = Lang.T("Empreinte du script invalide : non exécuté.", "Invalid script fingerprint: not run.", "Huella del script no válida: no ejecutado."); return 1; }
            string dir = Path.Combine(Path.GetTempPath(), "loco-script-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                string file = Path.Combine(dir, "script.ps1");
                File.WriteAllText(file, content, new UTF8Encoding(true));        // BOM : PowerShell 5.1 lit alors l'UTF-8
                string arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"[Console]::OutputEncoding = " +
                                   "[Text.Encoding]::UTF8; & '" + file.Replace("'", "''") + "'; exit $LASTEXITCODE\"";
                return RunCapture("powershell.exe", arguments, InstallerTimeout, out output);
            }
            finally { try { Directory.Delete(dir, true); } catch (Exception) { } }
        }

        static int RunCapture(string file, string arguments, int timeoutSeconds, out string output)
        {
            var psi = new ProcessStartInfo(file, arguments)
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
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

        // Étape d'installation remontée au serveur (page du déploiement) : étape, logiciel, rang, nombre, pourcentage.
        internal delegate void Progress(string step, string app, int index, int total, int percent);

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

        static void Report(Dictionary<string, object> conf, object id, string state, string output, int? code, object inventory)
        {
            if (output.Length > OutputLimit) output = output.Substring(output.Length - OutputLimit);
            var data = new Dictionary<string, object> { { "id", id }, { "state", state }, { "output", output }, { "exit_code", code } };
            if (inventory != null) data["inventory"] = inventory;
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
                Detached("msiexec.exe", "/i \"" + path + "\" /qn /norestart /l*v \"" + Path.Combine(Root, "update.log") + "\"");
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
                if (rescan || firstRun || now - lastScan > scanHours * 3600)
                {
                    data["inventory"] = Inventory();
                    lastScan = now;
                    rescan = false;
                }
                Dictionary<string, object> answer;
                try { answer = Checkin(conf, data); firstRun = false; unreachable = 0; PublishIndicator(answer); }
                catch (LocoHttpException e)
                {
                    DesktopIndicator.Unavailable(Lang.T("Contact avec loco refusé", "Contact with loco refused", "Contacto con loco rechazado"));
                    if (e.Status == 401)
                    {
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
                    else if (action == "update" || action == "install-app")
                    {
                        var lines = new List<string>();
                        bool ok = true;
                        // Avancement : au plus une remontée par seconde pour une même étape, immédiate à chaque changement.
                        string lastStep = ""; var lastSent = DateTime.MinValue;
                        Progress progress = (step, app, index, total, percent) =>
                        {
                            string key = step + "|" + app + "|" + index;
                            if (key == lastStep && (DateTime.UtcNow - lastSent).TotalSeconds < 1) return;
                            lastStep = key; lastSent = DateTime.UtcNow;
                            var detail = new Dictionary<string, object> { { "step", step }, { "app", app }, { "index", index }, { "total", total } };
                            if (percent >= 0) detail["percent"] = percent;
                            var report = new Dictionary<string, object> { { "id", id }, { "state", "running" }, { "output", "" }, { "exit_code", null }, { "progress", detail } };
                            try { Post(Str(conf, "server"), "/agent/api/result", report, Str(conf, "token")); }
                            catch (Exception) { }
                        };
                        if (action == "update")
                        {
                            progress("windows_update", "", 1, 1, -1);
                            try { ok = InstallWindowsUpdates(lines); }
                            catch (Exception e) { ok = false; lines.Add(Lang.T("Windows Update : ", "Windows Update: ", "Windows Update: ") + e.Message); }
                        }
                        object apps;
                        cmd.TryGetValue("apps", out apps);
                        if (!InstallCatalogApps(Str(conf, "server"), apps, lines, progress)) ok = false;
                        var inv = Inventory();
                        lastScan = now;
                        bool reboot = Convert.ToBoolean(cmd.ContainsKey("reboot") ? cmd["reboot"] : false) && (bool)inv["reboot_required"] && ok;
                        if (reboot) lines.Add(Lang.T("Redémarrage dans une minute.", "Rebooting in one minute.", "Reinicio en un minuto."));
                        Report(conf, id, ok ? "success" : "failed", string.Join("\n", lines), ok ? 0 : 1, inv);
                        if (reboot) { SaveState(lastScan, scanHours, false); Restart(Lang.T("redémarrage après mise à jour", "reboot after update", "reinicio tras la actualización")); return true; }
                    }
                    else if (action == "run-script")
                    {
                        string output; int code = RunScript(cmd, out output);
                        Report(conf, id, code == 0 ? "success" : "failed", output, code, null);
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
                    else Report(conf, id, "failed", Lang.T("Action non prise en charge : ", "Action not supported: ", "Acción no admitida: ") + action, 1, null);
                }
                SaveState(lastScan, scanHours, rescan);
                ApplyWuProxy(answer);
                ApplyWindowsUpdatePolicy(answer);
                SelfUpdate(conf, answer);
                return true;
            }
            finally { Monitor.Exit(runLock); }
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
