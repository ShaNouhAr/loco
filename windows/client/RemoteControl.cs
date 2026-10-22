// Relais sortant HTTPS : terminal ConPTY et flux RFB du compagnon de bureau, sans port d'écoute réseau.
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;

namespace Loco
{
    internal sealed class RemoteControl
    {
        readonly WaitHandle stopping;
        readonly Thread worker;
        // Signalé dès qu'une session a des octets à envoyer : l'échange suivant part sans attendre la fin du délai.
        internal static readonly AutoResetEvent Activity = new AutoResetEvent(false);
        public RemoteControl(WaitHandle stopping)
        {
            this.stopping = stopping;
            worker = new Thread(Loop) { IsBackground = true, Name = "loco-remote" }; worker.Start();
            SecureAttentionRelay.Start(stopping);
        }
        public void Join() { worker.Join(15000); }
        internal static string Str(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) && v != null ? Convert.ToString(v) : ""; }
        internal static int Number(Dictionary<string, object> d, string key, int min, int max)
        {
            int n; if (!int.TryParse(Str(d, key), out n) || n < min || n > max) throw new InvalidDataException(Lang.T("Valeur de session invalide : ", "Invalid session value: ", "Valor de sesión no válido: ") + key);
            return n;
        }
        internal static IEnumerable<Dictionary<string, object>> Items(Dictionary<string, object> d, string key)
        {
            object v; var list = d.TryGetValue(key, out v) ? v as IEnumerable : null;
            if (list == null) yield break;
            foreach (object item in list) { var row = item as Dictionary<string, object>; if (row != null) yield return row; }
        }
        void Loop()
        {
            var sessions = new Dictionary<string, RemoteSession>();
            var lastOk = Stopwatch.StartNew(); int delay = 2000;
            try
            {
                var waits = new[] { stopping, (WaitHandle)Activity };
                int signaled;
                while ((signaled = WaitHandle.WaitAny(waits, delay)) != 0)
                {
                    // Réveil sur sortie : le compagnon écrit une image d'un bloc, la laisser finir d'arriver pour l'envoyer en un seul échange.
                    if (signaled == 1) Thread.Sleep(3);
                    delay = 5000;
                    try
                    {
                        var answer = Agent.RemoteExchange(new { sessions = sessions.Values.Select(s => s.Payload()).ToArray() });
                        lastOk.Restart();
                        var descriptors = Items(answer, "sessions").ToDictionary(d => Str(d, "id"));
                        foreach (string sid in sessions.Keys.ToArray())
                            if (!descriptors.ContainsKey(sid) || Str(descriptors[sid], "state") == "closed")
                            { sessions[sid].Dispose(); sessions.Remove(sid); }
                        foreach (var pair in descriptors)
                        {
                            string state = Str(pair.Value, "state");
                            if (state != "waiting" && state != "active") continue;
                            if (sessions.Count >= 4 && !sessions.ContainsKey(pair.Key)) continue;
                            RemoteSession item;
                            if (!sessions.TryGetValue(pair.Key, out item)) { item = new RemoteSession(pair.Value, stopping); sessions[pair.Key] = item; }
                            item.Update(pair.Value);
                        }
                        // Session ouverte : relancer vite (saisie reçue en ~6 ms en moyenne) ; aucune session : contact espacé.
                        delay = sessions.Count > 0 ? (sessions.Values.Any(s => s.HasOutput) || descriptors.Values.Any(d => Items(d, "packets").Any()) ? 0 : 12) : 5000;
                    }
                    catch (Exception e)
                    {
                        var rejected = e as LocoHttpException;
                        if (rejected != null && rejected.Status == 429)
                        { lastOk.Restart(); delay = 50; } // Récepteur lent : garder le paquet et reprendre après acquittement.
                        else
                        {
                            Agent.Log("sessions distantes : " + e.Message);
                            if (lastOk.Elapsed.TotalSeconds > 20 || (rejected != null && (rejected.Status == 401 || rejected.Status == 403)))
                            { foreach (var item in sessions.Values) item.Dispose(); sessions.Clear(); }
                        }
                    }
                }
            }
            finally { foreach (var item in sessions.Values) item.Dispose(); }
        }
    }

    // Ctrl+Alt+Suppr d'une session de bureau : Windows n'accepte SendSAS que d'un service. Le compagnon (SYSTEM dans la
    // session) signale la demande par un événement réservé à SYSTEM ; le service l'exécute si un bureau distant est ouvert.
    internal static class SecureAttentionRelay
    {
        internal const string EventName = @"Global\loco-sas";
        static int desktops;
        static DateTime last = DateTime.MinValue;
        internal static void Opened() { Interlocked.Increment(ref desktops); }
        internal static void Closed() { Interlocked.Decrement(ref desktops); }
        internal static void Start(WaitHandle stopping)
        {
            new Thread(() =>
            {
                try
                {
                    var security = new EventWaitHandleSecurity();
                    security.SetAccessRuleProtection(true, false);
                    security.AddAccessRule(new EventWaitHandleAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                        EventWaitHandleRights.FullControl, AccessControlType.Allow));
                    bool created;
                    using (var request = new EventWaitHandle(false, EventResetMode.AutoReset, EventName, out created, security))
                        while (WaitHandle.WaitAny(new[] { stopping, request }) == 1)
                        {
                            if (Volatile.Read(ref desktops) <= 0 || DateTime.UtcNow - last < TimeSpan.FromSeconds(2)) continue;
                            last = DateTime.UtcNow;
                            Send();
                        }
                }
                catch (Exception) { }   // sans relais, Ctrl+Alt+Suppr reste sans effet ; le reste de la session fonctionne
            }) { IsBackground = true, Name = "loco-sas" }.Start();
        }
        static void Send()
        {
            // La stratégie SoftwareSASGeneration doit autoriser les services (bit 1) : le temps de l'appel seulement.
            const string path = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System";
            using (var key = Microsoft.Win32.Registry.LocalMachine.CreateSubKey(path))
            {
                object previous = key.GetValue("SoftwareSASGeneration");
                int current = previous is int ? (int)previous : 0;
                try
                {
                    if ((current & 1) == 0) key.SetValue("SoftwareSASGeneration", current | 1, Microsoft.Win32.RegistryValueKind.DWord);
                    SendSAS(false);
                    Thread.Sleep(800);
                }
                catch (Exception) { }
                finally
                {
                    try
                    {
                        if (previous == null) key.DeleteValue("SoftwareSASGeneration", false);
                        else if ((current & 1) == 0) key.SetValue("SoftwareSASGeneration", previous, Microsoft.Win32.RegistryValueKind.DWord);
                    }
                    catch (Exception) { }
                }
            }
        }
        [DllImport("sas.dll")] static extern void SendSAS(bool asUser);
    }

    internal sealed class RemoteSession : IDisposable
    {
        readonly object gate = new object();
        readonly Queue<byte[]> output = new Queue<byte[]>();
        readonly Queue<InputPacket> input = new Queue<InputPacket>();
        readonly AutoResetEvent wake = new AutoResetEvent(false);
        readonly string id, kind;
        Stream reader, writer;
        NativeTerminal terminal;
        Process desktop;
        ProcessJob desktopJob;
        FileRelay files;
        Thread readThread, writeThread;
        IntPtr writeNativeThread;
        bool disposed, eof;
        int queuedBytes, inputBytes, cols, rows;
        long inputAck, inputQueued, outputSeq;
        byte[] pending;
        string error = "";
        sealed class InputPacket { public long Seq; public byte[] Data; }
        public RemoteSession(Dictionary<string, object> descriptor, WaitHandle stopping)
        {
            id = RemoteControl.Str(descriptor, "id"); kind = RemoteControl.Str(descriptor, "kind");
            try
            {
                cols = RemoteControl.Number(descriptor, "cols", 20, 500); rows = RemoteControl.Number(descriptor, "rows", 5, 200);
                if (kind == "terminal") { terminal = new NativeTerminal(cols, rows); reader = terminal.Output; writer = terminal.Input; }
                else if (kind == "desktop") { OpenDesktop(stopping); }
                else if (kind == "files") { files = new FileRelay(); reader = files.Reader; writer = files.Writer; }
                else throw new InvalidDataException(Lang.T("Type de session inconnu", "Unknown session type", "Tipo de sesión desconocido"));
                readThread = new Thread(Read) { IsBackground = true, Name = "loco-remote-output" }; readThread.Start();
                writeThread = new Thread(Write) { IsBackground = true, Name = "loco-remote-input" }; writeThread.Start();
            }
            catch (Exception e) { error = e.Message; Dispose(); }
        }
        void OpenDesktop(WaitHandle stopping)
        {
            string pipeName = "loco-desktop-" + Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            var acl = new PipeSecurity(); acl.SetAccessRuleProtection(true, false);
            acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
            acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), PipeAccessRights.FullControl, AccessControlType.Allow));
            var pipe = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536, acl);
            reader = writer = pipe;
            desktop = DesktopIndicator.StartDesktop(pipeName);
            SecureAttentionRelay.Opened();
            desktopJob = new ProcessJob(); desktopJob.Add(desktop.Handle);
            IAsyncResult ready = pipe.BeginWaitForConnection(null, null);
            try
            {
                if (WaitHandle.WaitAny(new[] { ready.AsyncWaitHandle, stopping }, 15000) != 0) throw new IOException(Lang.T("Le bureau du poste n'est pas disponible.", "The machine's desktop is not available.", "El escritorio del equipo no está disponible."));
                pipe.EndWaitForConnection(ready);
                uint pid;
                if (!GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out pid) || pid != desktop.Id)
                    throw new IOException(Lang.T("Compagnon de bureau non reconnu.", "Desktop companion not recognised.", "Compañero de escritorio no reconocido."));
            }
            finally { ready.AsyncWaitHandle.Close(); }
        }
        void Read()
        {
            try
            {
                var buffer = new byte[65536]; int count;
                while ((count = reader.Read(buffer, 0, buffer.Length)) > 0)
                {
                    lock (gate)
                    {
                        while (!disposed && queuedBytes > 1024 * 1024) Monitor.Wait(gate, 100);
                        if (disposed) continue; // Continuer à vider ConPTY pendant sa fermeture.
                        byte[] part = new byte[count]; Buffer.BlockCopy(buffer, 0, part, 0, count); output.Enqueue(part); queuedBytes += count;
                    }
                    RemoteControl.Activity.Set();
                }
            }
            catch (Exception e) { lock (gate) if (!disposed) error = kind == "desktop" ? Lang.T("Session graphique inaccessible ou fermée.", "Graphical session inaccessible or closed.", "Sesión gráfica inaccesible o cerrada.") : e.Message; }
            finally { lock (gate) eof = true; }
        }
        void Write()
        {
            IntPtr native = OpenThread(1, false, GetCurrentThreadId());
            lock (gate) { writeNativeThread = native; }
            try
            {
                while (true)
                {
                    InputPacket packet = null;
                    lock (gate) { if (disposed) break; if (input.Count > 0) packet = input.Peek(); }
                    if (packet == null) { wake.WaitOne(100); continue; }
                    writer.Write(packet.Data, 0, packet.Data.Length); writer.Flush();
                    lock (gate) { inputAck = packet.Seq; inputBytes -= packet.Data.Length; input.Dequeue(); }
                }
            }
            catch (Exception e) { lock (gate) { if (!disposed) error = e.Message; eof = true; } }
            finally { lock (gate) { writeNativeThread = IntPtr.Zero; if (native != IntPtr.Zero) CloseHandle(native); } }
        }
        public void Update(Dictionary<string, object> descriptor)
        {
            lock (gate)
            {
                long ack; if (!long.TryParse(RemoteControl.Str(descriptor, "ack"), out ack) || ack < 0 || ack > outputSeq) throw new InvalidDataException(Lang.T("Acquittement invalide", "Invalid acknowledgement", "Acuse de recibo no válido"));
                if (ack == outputSeq) pending = null;
                if (disposed || eof) return;
                foreach (var p in RemoteControl.Items(descriptor, "packets"))
                {
                    long seq; if (!long.TryParse(RemoteControl.Str(p, "seq"), out seq)) throw new InvalidDataException(Lang.T("Séquence invalide", "Invalid sequence", "Secuencia no válida"));
                    if (seq <= inputQueued) continue;
                    if (seq != inputQueued + 1) throw new InvalidDataException(Lang.T("Séquence manquante", "Missing sequence", "Secuencia faltante"));
                    byte[] data = Convert.FromBase64String(RemoteControl.Str(p, "data"));
                    if (data.Length > 256 * 1024 || inputBytes + data.Length > 2 * 1024 * 1024) throw new InvalidDataException("Saisie trop volumineuse");
                    input.Enqueue(new InputPacket { Seq = seq, Data = data }); inputBytes += data.Length; inputQueued = seq;
                }
                wake.Set();
            }
            int nextCols = RemoteControl.Number(descriptor, "cols", 20, 500), nextRows = RemoteControl.Number(descriptor, "rows", 5, 200);
            if (terminal != null && (nextCols != cols || nextRows != rows)) { terminal.Resize(nextCols, nextRows); cols = nextCols; rows = nextRows; }
        }
        public bool HasOutput { get { lock (gate) return pending != null || output.Count > 0; } }
        public object Payload()
        {
            lock (gate)
            {
                if (pending == null && output.Count > 0)
                {
                    using (var batch = new MemoryStream())
                    {
                        while (output.Count > 0 && batch.Length + output.Peek().Length <= 256 * 1024)
                        { var part = output.Dequeue(); batch.Write(part, 0, part.Length); queuedBytes -= part.Length; }
                        pending = batch.ToArray();
                    }
                    outputSeq++; Monitor.PulseAll(gate);
                }
                return new { id = id, ack = inputAck, seq = outputSeq, data = pending == null ? "" : Convert.ToBase64String(pending),
                    state = disposed || (eof && pending == null && output.Count == 0) ? "closed" : "active", error = error.Length > 300 ? error.Substring(0, 300) : error };
            }
        }
        public void Dispose()
        {
            lock (gate)
            {
                if (disposed) return;
                disposed = true; Monitor.PulseAll(gate); wake.Set();
                if (writeNativeThread != IntPtr.Zero) CancelSynchronousIo(writeNativeThread);
            }
            // EOF laisse au compagnon le temps de relâcher les touches et boutons injectés, et de rétablir
            // la résolution d'origine (un changement de mode peut prendre plus d'une seconde sur un vrai GPU).
            if (desktop != null && reader != null) { reader.Dispose(); try { desktop.WaitForExit(5000); } catch (Exception) { } }
            if (desktopJob != null) { desktopJob.Dispose(); desktopJob = null; }
            if (desktop != null) { try { if (!desktop.HasExited) desktop.Kill(); } catch (Exception) { } desktop.Dispose(); desktop = null; SecureAttentionRelay.Closed(); }
            if (terminal != null) { terminal.Dispose(); terminal = null; }
            else if (files != null) { files.Dispose(); files = null; }
            else { if (reader != null) reader.Dispose(); if (writer != null && writer != reader) writer.Dispose(); }
            if (readThread != null) readThread.Join(1000);
            if (writeThread != null) writeThread.Join(1000);
            wake.Dispose();
        }
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetNamedPipeClientProcessId(IntPtr pipe, out uint pid);
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenThread(uint access, bool inherit, uint tid);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool CancelSynchronousIo(IntPtr thread);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }
}
