// Service : état expurgé et tray avec le jeton utilisateur ; contrôle RFB SYSTEM dans la session interactive.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace Loco
{
    internal static class DesktopIndicator
    {
        static readonly string Executable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "loco-tray.exe");

        public static void Publish(string color, string reason)
        {
            try
            {
                var security = new DirectorySecurity();
                security.SetAccessRuleProtection(true, false);
                var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
                security.SetOwner(system);
                foreach (var sid in new[] { WellKnownSidType.LocalSystemSid, WellKnownSidType.BuiltinAdministratorsSid, WellKnownSidType.BuiltinUsersSid })
                    security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null),
                        sid == WellKnownSidType.BuiltinUsersSid ? FileSystemRights.ReadAndExecute : FileSystemRights.FullControl,
                        InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                Directory.CreateDirectory(DesktopStatus.DirectoryPath, security);
                if ((File.GetAttributes(DesktopStatus.DirectoryPath) & FileAttributes.ReparsePoint) != 0) return;
                Directory.SetAccessControl(DesktopStatus.DirectoryPath, security);
                if (color != "green" && color != "red" && color != "orange") color = "grey";
                string json = new JavaScriptSerializer().Serialize(new {
                    color = color, reason = reason ?? Lang.T("Informations en attente", "Awaiting information", "Información en espera"), updated_at = DateTime.UtcNow.ToString("o") });
                string tmp = Path.Combine(DesktopStatus.DirectoryPath, Guid.NewGuid().ToString("N") + ".tmp");
                try
                {
                    using (var stream = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write))
                    using (var writer = new StreamWriter(stream, new UTF8Encoding(false))) writer.Write(json);
                    if (File.Exists(DesktopStatus.FilePath)) File.Delete(DesktopStatus.FilePath);
                    File.Move(tmp, DesktopStatus.FilePath);
                }
                finally { if (File.Exists(tmp)) File.Delete(tmp); }
            }
            catch (Exception e) { Agent.Log(Lang.T("indicateur : ", "indicator: ", "indicador: ") + e.Message); }
        }

        public static void Unavailable(string reason)
        {
            Publish("grey", reason);
        }

        public static void StartSessions()
        {
            if (!File.Exists(Executable) || !WindowsIdentity.GetCurrent().IsSystem) return;
            IntPtr sessions;
            int count;
            if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out sessions, out count)) return;
            try
            {
                int size = Marshal.SizeOf(typeof(Session));
                for (int i = 0; i < count; i++)
                {
                    var session = (Session)Marshal.PtrToStructure(IntPtr.Add(sessions, i * size), typeof(Session));
                    if (session.Id == 0 || session.State != 0 || Running(session.Id)) continue;
                    Start(session.Id);
                }
            }
            finally { WTSFreeMemory(sessions); }
        }

        static bool Running(int session)
        {
            foreach (var process in Process.GetProcessesByName("loco-tray"))
                using (process)
                    try { if (process.SessionId == session && String.Equals(process.MainModule.FileName, Executable, StringComparison.OrdinalIgnoreCase)) return true; }
                    catch (Exception) { }
            return false;
        }

        static void Start(int session)
        {
            try { using (var process = Launch(session, Executable, "")) { } }
            catch (Exception e) { Agent.Log(Lang.T("indicateur de session : ", "session indicator: ", "indicador de sesión: ") + e.Message); }
        }

        internal static Process StartDesktop(string pipeName)
        {
            string file = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "loco-desktop.exe");
            if (!WindowsIdentity.GetCurrent().IsSystem || !File.Exists(file)) throw new IOException(Lang.T("Compagnon de bureau indisponible.", "Desktop companion unavailable.", "Compañero de escritorio no disponible."));
            int selected = InteractiveSession();
            if (selected < 0) throw new IOException(Lang.T("Aucune session interactive disponible sur ce poste Windows.", "No interactive session available on this Windows machine.", "Ninguna sesión interactiva disponible en este equipo Windows."));
            return Launch(selected, file, " --pipe " + pipeName, true);
        }

        // Session interactive du poste : celle de la console si elle est active, sinon la première active ; -1 s'il n'y en a pas.
        static int InteractiveSession()
        {
            IntPtr sessions; int count;
            if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out sessions, out count)) throw new Win32Exception();
            try
            {
                int console = (int)WTSGetActiveConsoleSessionId();
                int selected = -1;
                for (int i = 0; i < count; i++)
                {
                    var session = (Session)Marshal.PtrToStructure(IntPtr.Add(sessions, i * Marshal.SizeOf(typeof(Session))), typeof(Session));
                    if (session.Id > 0 && session.State == 0) { if (selected < 0 || session.Id == console) selected = session.Id; }
                }
                if (selected < 0 && console > 0) selected = console;
                return selected;
            }
            finally { WTSFreeMemory(sessions); }
        }

        // Accord de la personne devant le poste, avant le bureau distant : given, nobody (aucun compte ouvert à qui demander),
        // refused ou timeout. Fenêtre système affichée par le service dans la session, sans aucun processus de plus.
        internal static string AskConsent(string by, bool assistant, int seconds)
        {
            int session = InteractiveSession();
            if (session < 0 || SessionUser(session) == "") return "nobody";
            string who = Regex.Replace(by ?? "", @"[^\w .@+-]", "");
            if (who.Length > 64) who = who.Substring(0, 64);
            if (who == "") who = Lang.T("un administrateur", "an administrator", "un administrador");
            string ask = assistant
                ? Lang.T("Un assistant IA, au nom de " + who + ", demande à voir et à contrôler cet écran avec loco.", "An AI assistant, on behalf of " + who + ", asks to view and control this screen with loco.", "Un asistente de IA, en nombre de " + who + ", solicita ver y controlar esta pantalla con loco.")
                : Lang.T(who + " demande à voir et à contrôler cet écran avec loco.", who + " asks to view and control this screen with loco.", who + " solicita ver y controlar esta pantalla con loco.");
            string text = ask + "\n\n" + Lang.T("Accepter ? Sans réponse dans " + seconds + " secondes, la demande est refusée.", "Accept? Without an answer within " + seconds + " seconds, the request is declined.", "¿Aceptar? Sin respuesta en " + seconds + " segundos, la solicitud se rechaza.");
            string title = Lang.T("Prise de main à distance", "Remote control request", "Solicitud de control remoto");
            const int YesNoQuestion = 0x4 | 0x20 | 0x100 | 0x10000 | 0x40000;   // Oui/Non, « Non » par défaut, au premier plan
            int response;
            if (!WTSSendMessage(IntPtr.Zero, session, title, title.Length * 2, text, text.Length * 2, YesNoQuestion, seconds, out response, true))
                throw new Win32Exception();
            return response == 6 ? "given" : response == 32000 ? "timeout" : "refused";
        }

        static string SessionUser(int session)
        {
            IntPtr buffer; int size;
            if (!WTSQuerySessionInformation(IntPtr.Zero, session, 5, out buffer, out size)) throw new Win32Exception();
            try { return Marshal.PtrToStringUni(buffer) ?? ""; }
            finally { WTSFreeMemory(buffer); }
        }

        static Process Launch(int session, string executable, string arguments, bool desktopControl = false)
        {
            IntPtr token = IntPtr.Zero, environment = IntPtr.Zero;
            try
            {
                if (desktopControl)
                {
                    // Seul le compagnon RFB hérite de SYSTEM, dans la session interactive choisie.
                    // Le tray reste lancé avec le jeton de l'utilisateur, sans privilèges.
                    using (var identity = WindowsIdentity.GetCurrent())
                        if (!DuplicateTokenEx(identity.Token, 0xF01FF, IntPtr.Zero, 2, 1, out token)) throw new Win32Exception();
                    if (!SetTokenInformation(token, 12, ref session, sizeof(int))) throw new Win32Exception();
                }
                else if (!WTSQueryUserToken(session, out token)) throw new Win32Exception();
                if (!CreateEnvironmentBlock(out environment, token, false)) throw new Win32Exception();
                var startup = new Startup { Size = Marshal.SizeOf(typeof(Startup)), Desktop = @"winsta0\default" };
                ProcessInfo process;
                if (!CreateProcessAsUser(token, executable, new StringBuilder("\"" + executable + "\"" + arguments),
                    IntPtr.Zero, IntPtr.Zero, false, 0x400, environment, Path.GetDirectoryName(executable), ref startup, out process))
                {
                    throw new Win32Exception();
                }
                CloseHandle(process.Process); CloseHandle(process.Thread);
                return Process.GetProcessById(process.ProcessId);
            }
            finally { if (environment != IntPtr.Zero) DestroyEnvironmentBlock(environment); if (token != IntPtr.Zero) CloseHandle(token); }
        }

        public static void StopSessions()
        {
            foreach (var process in Process.GetProcessesByName("loco-tray"))
                using (process)
                    try
                    {
                        if (String.Equals(process.MainModule.FileName, Executable, StringComparison.OrdinalIgnoreCase)) process.Kill();
                    }
                    catch (Exception) { }
        }

        [StructLayout(LayoutKind.Sequential)] struct Session { public int Id; public IntPtr Name; public int State; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct Startup
        {
            public int Size; public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public short ShowWindow, ReservedSize; public IntPtr ReservedPtr, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public int ProcessId, ThreadId; }
        [DllImport("wtsapi32.dll", SetLastError = true)] static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
        [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr memory);
        [DllImport("wtsapi32.dll", EntryPoint = "WTSQuerySessionInformationW", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool WTSQuerySessionInformation(IntPtr server, int session, int infoClass, out IntPtr buffer, out int size);
        [DllImport("wtsapi32.dll", EntryPoint = "WTSSendMessageW", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool WTSSendMessage(IntPtr server, int session, string title, int titleBytes, string message, int messageBytes, int style, int timeout, out int response, bool wait);
        [DllImport("kernel32.dll")] static extern uint WTSGetActiveConsoleSessionId();
        [DllImport("wtsapi32.dll", SetLastError = true)] static extern bool WTSQueryUserToken(int session, out IntPtr token);
        [DllImport("userenv.dll", SetLastError = true)] static extern bool CreateEnvironmentBlock(out IntPtr environment, IntPtr token, bool inherit);
        [DllImport("userenv.dll")] static extern bool DestroyEnvironmentBlock(IntPtr environment);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessAsUser(IntPtr token, string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, int flags, IntPtr environment, string directory, ref Startup startup, out ProcessInfo process);
        [DllImport("advapi32.dll", SetLastError = true)] static extern bool DuplicateTokenEx(IntPtr token, uint access, IntPtr attributes, int level, int type, out IntPtr duplicate);
        [DllImport("advapi32.dll", SetLastError = true)] static extern bool SetTokenInformation(IntPtr token, int informationClass, ref int information, int length);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }
}
