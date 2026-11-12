// Connexion persistante au relais du serveur (WebSocket, RFC 6455, sur une liaison TLS vérifiée comme les échanges
// HTTPS) : le serveur y prévient l'agent de ce qui arrive (session demandée, saisie, action en attente), qui ne fait
// plus aucun relevé à vide. Le protocole des échanges est celui de /agent/api/remote : si le canal manque ou tombe,
// l'agent reprend ses allers-retours HTTPS sans perdre une session.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace Loco
{
    internal sealed class RemoteChannel : IDisposable
    {
        const string Magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
        const int Limit = 8 * 1024 * 1024;
        readonly TcpClient tcp;
        readonly Stream stream;
        readonly object sending = new object(), gate = new object();
        readonly Queue<Dictionary<string, object>> answers = new Queue<Dictionary<string, object>>();
        volatile bool broken;
        internal readonly string Key;                    // serveur et jeton de cette connexion : changés, elle est refaite
        internal bool Broken { get { return broken; } }

        internal RemoteChannel(string server, string token, RemoteCertificateValidationCallback check, string version)
        {
            Key = server + "\n" + token;
            var uri = new Uri(server);
            tcp = new TcpClient { NoDelay = true };
            try
            {
                var pending = tcp.BeginConnect(uri.Host, uri.Port, null, null);
                if (!pending.AsyncWaitHandle.WaitOne(10000)) throw new IOException("relais : délai de connexion dépassé");
                tcp.EndConnect(pending);
                tcp.ReceiveTimeout = 15000; tcp.SendTimeout = 15000;
                Stream s = tcp.GetStream();
                if (uri.Scheme == "https")
                {
                    var tls = new SslStream(s, false, check);
                    tls.AuthenticateAsClient(uri.Host, null, SslProtocols.Tls12, false);
                    s = tls;
                }
                stream = s;
                var nonce = new byte[16];
                using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(nonce);
                string key = Convert.ToBase64String(nonce);
                byte[] request = Encoding.ASCII.GetBytes("GET " + uri.AbsolutePath.TrimEnd('/') + "/agent/relay HTTP/1.1\r\nHost: " + uri.Authority
                    + "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer "
                    + token + "\r\nUser-Agent: loco-client/" + version + "\r\n\r\n");
                stream.Write(request, 0, request.Length); stream.Flush();
                var head = new StringBuilder();
                while (head.Length < 4 || head.ToString(head.Length - 4, 4) != "\r\n\r\n")
                {
                    int b = stream.ReadByte();
                    if (b < 0 || head.Length > 16384) throw new IOException("relais : réponse incomplète");
                    head.Append((char)b);
                }
                string[] lines = head.ToString().Split(new[] { "\r\n" }, StringSplitOptions.RemoveEmptyEntries);
                string accept = "", expected;
                foreach (string line in lines)
                    if (line.StartsWith("Sec-WebSocket-Accept:", StringComparison.OrdinalIgnoreCase)) accept = line.Substring(line.IndexOf(':') + 1).Trim();
                using (var sha = SHA1.Create()) expected = Convert.ToBase64String(sha.ComputeHash(Encoding.ASCII.GetBytes(key + Magic)));
                if (lines.Length == 0 || !lines[0].StartsWith("HTTP/1.1 101", StringComparison.Ordinal) || accept != expected)
                    throw new IOException("relais indisponible");
                tcp.ReceiveTimeout = 0;      // le fil de lecture attend sans limite : le serveur ne parle que s'il a quelque chose à dire
            }
            catch (Exception) { tcp.Close(); throw; }
            new Thread(Read) { IsBackground = true, Name = "loco-relay" }.Start();
        }

        byte[] Exact(int count)
        {
            var buffer = new byte[count]; int got = 0;
            while (got < count)
            {
                int read = stream.Read(buffer, got, count - got);
                if (read <= 0) throw new EndOfStreamException();
                got += read;
            }
            return buffer;
        }

        void Frame(int opcode, byte[] data)
        {
            var key = new byte[4];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(key);
            var frame = new MemoryStream(data.Length + 14);
            frame.WriteByte((byte)(0x80 | opcode));
            if (data.Length < 126) frame.WriteByte((byte)(0x80 | data.Length));
            else if (data.Length < 65536) { frame.WriteByte(0x80 | 126); frame.WriteByte((byte)(data.Length >> 8)); frame.WriteByte((byte)data.Length); }
            else { frame.WriteByte(0x80 | 127); for (int i = 7; i >= 0; i--) frame.WriteByte((byte)((long)data.Length >> (8 * i))); }
            frame.Write(key, 0, 4);
            var masked = new byte[data.Length];
            for (int i = 0; i < data.Length; i++) masked[i] = (byte)(data[i] ^ key[i & 3]);
            frame.Write(masked, 0, masked.Length);
            byte[] all = frame.ToArray();
            lock (sending) { stream.Write(all, 0, all.Length); stream.Flush(); }
        }

        // Fil de lecture : réponses rangées pour l'échange en cours ; un « wake » réveille la boucle des sessions.
        void Read()
        {
            try
            {
                var parts = new MemoryStream();
                while (true)
                {
                    byte[] head = Exact(2);
                    bool fin = (head[0] & 0x80) != 0, masked = (head[1] & 0x80) != 0;
                    int opcode = head[0] & 0x0F; long size = head[1] & 0x7F;
                    if (size == 126) { byte[] e = Exact(2); size = (e[0] << 8) | e[1]; }
                    else if (size == 127) { size = 0; foreach (byte b in Exact(8)) size = (size << 8) | b; }
                    if (size < 0 || size > Limit || parts.Length + size > Limit) throw new IOException("relais : message trop volumineux");
                    byte[] mask = masked ? Exact(4) : null;
                    byte[] data = Exact((int)size);
                    if (mask != null) for (int i = 0; i < data.Length; i++) data[i] ^= mask[i & 3];
                    if (opcode == 8) throw new IOException("relais : connexion fermée par le serveur");
                    if (opcode == 9) { Frame(10, data); continue; }
                    if (opcode > 2) continue;
                    parts.Write(data, 0, data.Length);
                    if (!fin) continue;
                    var message = Agent.FromJson(Encoding.UTF8.GetString(parts.ToArray()));
                    parts.SetLength(0);
                    if (message.ContainsKey("wake")) { RemoteControl.Activity.Set(); continue; }
                    lock (gate) { answers.Enqueue(message); Monitor.PulseAll(gate); }
                }
            }
            catch (Exception) { /* connexion rompue : signalée ci-dessous */ }
            finally
            {
                broken = true;
                lock (gate) Monitor.PulseAll(gate);
                RemoteControl.Activity.Set();
            }
        }

        // Un échange : même réponse que POST /agent/api/remote ; un refus du portail lève la même erreur que par HTTPS.
        internal Dictionary<string, object> Exchange(object data, int timeout)
        {
            if (broken) throw new IOException("relais : connexion rompue");
            Frame(1, new UTF8Encoding(false).GetBytes(Agent.ToJson(data)));
            lock (gate)
            {
                DateTime until = DateTime.UtcNow.AddMilliseconds(timeout);
                while (answers.Count == 0)
                {
                    if (broken) throw new IOException("relais : connexion rompue");
                    int left = (int)(until - DateTime.UtcNow).TotalMilliseconds;
                    if (left <= 0) { broken = true; throw new IOException("relais : réponse attendue en vain"); }
                    Monitor.Wait(gate, left);
                }
                var answer = answers.Dequeue();
                object status;
                if (answer.TryGetValue("status", out status) && status != null) throw new LocoHttpException(Convert.ToInt32(status));
                return answer;
            }
        }

        public void Dispose()
        {
            broken = true;
            try { tcp.Close(); } catch (Exception) { /* déjà fermée */ }
        }
    }
}
