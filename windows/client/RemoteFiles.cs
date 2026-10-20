// Gestionnaire de fichiers de l'agent Windows : lit des trames de requête et écrit des trames de réponse, sur deux
// tubes anonymes internes branchés comme lecteur/écrivain dans RemoteSession. Même protocole que l'agent Linux
// (loco-client.py « files ») : [u32 longueur en-tête][u32 longueur données] + en-tête JSON + données binaires.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;

namespace Loco
{
    internal sealed class FileRelay : IDisposable
    {
        const int HeaderMax = 64 * 1024, DataMax = 1024 * 1024, Chunk = 256 * 1024;
        readonly AnonymousPipeServerStream requestOut;   // RemoteSession y écrit les requêtes du navigateur
        readonly AnonymousPipeClientStream requestIn;    // le service y lit les requêtes
        readonly AnonymousPipeServerStream responseOut;  // le service y écrit les réponses
        readonly AnonymousPipeClientStream responseIn;   // RemoteSession y lit les réponses
        readonly Thread worker;
        readonly JavaScriptSerializer json = new JavaScriptSerializer();
        readonly Dictionary<string, FileStream> uploads = new Dictionary<string, FileStream>();
        readonly Dictionary<string, string> uploadTemp = new Dictionary<string, string>();
        bool disposed;

        public FileRelay()
        {
            requestOut = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.None);
            requestIn = new AnonymousPipeClientStream(PipeDirection.In, requestOut.GetClientHandleAsString());
            responseOut = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.None);
            responseIn = new AnonymousPipeClientStream(PipeDirection.In, responseOut.GetClientHandleAsString());
            worker = new Thread(Serve) { IsBackground = true, Name = "loco-files" };
            worker.Start();
        }

        public Stream Reader { get { return responseIn; } }   // lu par RemoteSession, envoyé au navigateur
        public Stream Writer { get { return requestOut; } }   // écrit par RemoteSession depuis le navigateur

        void Serve()
        {
            try
            {
                while (true)
                {
                    byte[] prefix = ReadExactly(requestIn, 8);
                    if (prefix == null) break;                       // fin de flux : la session se ferme
                    int hlen = BE(prefix, 0), dlen = BE(prefix, 4);
                    if (hlen < 0 || dlen < 0 || hlen > HeaderMax || dlen > DataMax) throw new InvalidDataException("trame trop grande");
                    byte[] headerBytes = ReadExactly(requestIn, hlen), data = ReadExactly(requestIn, dlen);
                    if (headerBytes == null || data == null) throw new InvalidDataException("flux interrompu");
                    var header = json.Deserialize<Dictionary<string, object>>(System.Text.Encoding.UTF8.GetString(headerBytes))
                                 ?? new Dictionary<string, object>();
                    object id; header.TryGetValue("id", out id);
                    var reply = new Dictionary<string, object>();
                    byte[] payload = new byte[0];
                    try { payload = Handle(header, data, reply); reply["ok"] = true; }
                    catch (Exception e) { reply.Clear(); reply["ok"] = false; reply["error"] = Trim(e.Message); }
                    reply["id"] = id;
                    WriteFrame(reply, payload);
                }
            }
            catch (Exception) { /* pipe fermé ou protocole invalide : on arrête le service */ }
            finally
            {
                lock (uploads) { foreach (var p in uploads) try { p.Value.Dispose(); } catch (Exception) { } CleanupTemp(); uploads.Clear(); }
                try { responseOut.Dispose(); } catch (Exception) { }
            }
        }

        byte[] Handle(Dictionary<string, object> h, byte[] data, Dictionary<string, object> reply)
        {
            switch (Str(h, "op"))
            {
                case "hello": return Hello(reply);
                case "list": return List(Str(h, "path"), reply);
                case "stat": return Stat(Str(h, "path"), reply);
                case "mkdir": Directory.CreateDirectory(Str(h, "path")); return Empty;
                case "rename": return Rename(Str(h, "path"), Str(h, "name"), reply);
                case "move": return Transfer(Str(h, "path"), Str(h, "to"), false, reply);
                case "copy": return Transfer(Str(h, "path"), Str(h, "to"), true, reply);
                case "delete": Delete(Str(h, "path")); return Empty;
                case "chmod": throw new InvalidDataException("Droits POSIX non applicables sous Windows.");
                case "read": return Read(Str(h, "path"), Long(h, "offset"), (int)Long(h, "length"), reply);
                case "write": return Write(Str(h, "path"), Long(h, "offset"), data, Bool(h, "final"), reply);
                case "write_cancel": CancelUpload(Str(h, "path")); return Empty;
                default: throw new InvalidDataException("opération inconnue");
            }
        }

        static readonly byte[] Empty = new byte[0];

        byte[] Hello(Dictionary<string, object> reply)
        {
            var roots = new List<object>();
            foreach (var d in DriveInfo.GetDrives())
            {
                bool ready; try { ready = d.IsReady; } catch (Exception) { ready = false; }
                if (ready) roots.Add(new Dictionary<string, object> { { "name", d.Name + (string.IsNullOrEmpty(d.VolumeLabel) ? "" : " (" + d.VolumeLabel + ")") }, { "path", d.RootDirectory.FullName } });
            }
            string home = Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\";   // on démarre à la racine du disque système (C:\)
            reply["os"] = "windows"; reply["sep"] = "\\"; reply["home"] = home;
            reply["user"] = Environment.UserName; reply["host"] = Environment.MachineName; reply["roots"] = roots;
            return Empty;
        }

        byte[] List(string path, Dictionary<string, object> reply)
        {
            var entries = new List<object>();
            int count = 0; bool truncated = false;
            foreach (var full in Directory.EnumerateFileSystemEntries(path))
            {
                if (count++ >= 5000) { truncated = true; break; }
                entries.Add(Entry(Path.GetFileName(full), full));
            }
            entries.Sort((a, b) => Compare((Dictionary<string, object>)a, (Dictionary<string, object>)b));
            var parent = Directory.GetParent(path.TrimEnd('\\', '/'));
            reply["path"] = path; reply["parent"] = parent == null ? null : parent.FullName;
            reply["entries"] = entries; reply["truncated"] = truncated;
            return Empty;
        }

        static int Compare(Dictionary<string, object> a, Dictionary<string, object> b)
        {
            bool da = (string)a["type"] == "dir", db = (string)b["type"] == "dir";
            if (da != db) return da ? -1 : 1;
            return string.Compare((string)a["name"], (string)b["name"], StringComparison.OrdinalIgnoreCase);
        }

        Dictionary<string, object> Entry(string name, string full)
        {
            try
            {
                var attr = File.GetAttributes(full);
                bool dir = (attr & FileAttributes.Directory) != 0;
                bool link = (attr & FileAttributes.ReparsePoint) != 0;
                long size = 0, mtime = 0;
                if (dir) { var info = new DirectoryInfo(full); mtime = Unix(info.LastWriteTimeUtc); }
                else { var info = new FileInfo(full); size = info.Length; mtime = Unix(info.LastWriteTimeUtc); }
                return new Dictionary<string, object> { { "name", name }, { "type", dir ? "dir" : "file" }, { "size", size }, { "mtime", mtime }, { "mode", 0 }, { "link", link } };
            }
            catch (Exception)
            {
                return new Dictionary<string, object> { { "name", name }, { "type", "other" }, { "size", 0 }, { "mtime", 0 }, { "mode", 0 }, { "link", false } };
            }
        }

        byte[] Stat(string path, Dictionary<string, object> reply)
        {
            bool exists = File.Exists(path) || Directory.Exists(path);
            reply["path"] = path; reply["exists"] = exists;
            if (exists) foreach (var kv in Entry(Path.GetFileName(path.TrimEnd('\\', '/')), path)) reply[kv.Key] = kv.Value;
            return Empty;
        }

        byte[] Rename(string path, string name, Dictionary<string, object> reply)
        {
            string dest = Path.Combine(Path.GetDirectoryName(path) ?? "", Path.GetFileName(name));
            if (File.Exists(dest) || Directory.Exists(dest)) throw new IOException("Un élément porte déjà ce nom.");
            if (Directory.Exists(path)) Directory.Move(path, dest); else File.Move(path, dest);
            reply["path"] = dest; return Empty;
        }

        byte[] Transfer(string path, string to, bool copy, Dictionary<string, object> reply)
        {
            string dest = Path.Combine(to, Path.GetFileName(path.TrimEnd('\\', '/')));
            if (File.Exists(dest) || Directory.Exists(dest)) throw new IOException("Un élément porte déjà ce nom à destination.");
            bool dir = Directory.Exists(path);
            if (copy) { if (dir) CopyTree(path, dest); else File.Copy(path, dest); }
            else { if (dir) Directory.Move(path, dest); else File.Move(path, dest); }
            reply["path"] = dest; return Empty;
        }

        static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (var d in Directory.GetDirectories(from)) CopyTree(d, Path.Combine(to, Path.GetFileName(d)));
            foreach (var f in Directory.GetFiles(from)) File.Copy(f, Path.Combine(to, Path.GetFileName(f)));
        }

        void Delete(string path)
        {
            if (Directory.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0) Directory.Delete(path, true);
            else if (Directory.Exists(path)) Directory.Delete(path);
            else File.Delete(path);
        }

        byte[] Read(string path, long offset, int length, Dictionary<string, object> reply)
        {
            if (length <= 0 || length > Chunk) length = Chunk;
            using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                long size = f.Length;
                if (offset < 0) offset = 0;
                f.Seek(offset, SeekOrigin.Begin);
                byte[] buffer = new byte[length];
                int got = 0, n;
                while (got < length && (n = f.Read(buffer, got, length - got)) > 0) got += n;
                if (got != buffer.Length) Array.Resize(ref buffer, got);
                reply["offset"] = offset; reply["size"] = size; reply["eof"] = offset + got >= size;
                return buffer;
            }
        }

        byte[] Write(string path, long offset, byte[] data, bool final, Dictionary<string, object> reply)
        {
            lock (uploads)
            {
                FileStream handle;
                if (offset == 0)
                {
                    if (uploads.TryGetValue(path, out handle)) handle.Dispose();
                    string tmp = path + ".loco-part";
                    handle = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None);
                    uploads[path] = handle; uploadTemp[path] = tmp;
                }
                else if (!uploads.TryGetValue(path, out handle)) throw new IOException("Téléversement sans début.");
                handle.Seek(offset, SeekOrigin.Begin);
                handle.Write(data, 0, data.Length);
                if (final)
                {
                    handle.Flush(); handle.Dispose();
                    string tmp = uploadTemp[path];
                    if (File.Exists(path)) File.Replace(tmp, path, null); else File.Move(tmp, path);
                    uploads.Remove(path); uploadTemp.Remove(path);
                }
                reply["offset"] = offset; reply["written"] = data.Length; reply["final"] = final;
                return Empty;
            }
        }

        void CancelUpload(string path)
        {
            lock (uploads)
            {
                FileStream handle;
                if (uploads.TryGetValue(path, out handle)) { handle.Dispose(); uploads.Remove(path); }
                string tmp; if (uploadTemp.TryGetValue(path, out tmp)) { try { File.Delete(tmp); } catch (Exception) { } uploadTemp.Remove(path); }
            }
        }

        void CleanupTemp()
        {
            foreach (var tmp in uploadTemp.Values) try { File.Delete(tmp); } catch (Exception) { }
            uploadTemp.Clear();
        }

        void WriteFrame(Dictionary<string, object> header, byte[] data)
        {
            byte[] hb = System.Text.Encoding.UTF8.GetBytes(json.Serialize(header));
            byte[] prefix = new byte[8];
            PutBE(prefix, 0, hb.Length); PutBE(prefix, 4, data.Length);
            responseOut.Write(prefix, 0, 8);
            responseOut.Write(hb, 0, hb.Length);
            if (data.Length > 0) responseOut.Write(data, 0, data.Length);
            responseOut.Flush();
        }

        static byte[] ReadExactly(Stream stream, int count)
        {
            byte[] buffer = new byte[count];
            int got = 0, n;
            while (got < count && (n = stream.Read(buffer, got, count - got)) > 0) got += n;
            if (got == 0 && count > 0) return null;              // fin de flux propre
            if (got < count) return null;
            return buffer;
        }

        static int BE(byte[] b, int at) { return (b[at] << 24) | (b[at + 1] << 16) | (b[at + 2] << 8) | b[at + 3]; }
        static void PutBE(byte[] b, int at, int v) { b[at] = (byte)(v >> 24); b[at + 1] = (byte)(v >> 16); b[at + 2] = (byte)(v >> 8); b[at + 3] = (byte)v; }
        static long Unix(DateTime utc) { return (long)(utc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds; }
        static string Trim(string s) { return s != null && s.Length > 300 ? s.Substring(0, 300) : (s ?? ""); }

        static string Str(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) && v != null ? v.ToString() : ""; }
        static long Long(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) && v != null ? Convert.ToInt64(v) : 0; }
        static bool Bool(Dictionary<string, object> d, string key) { object v; return d.TryGetValue(key, out v) && v != null && Convert.ToBoolean(v); }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            try { requestOut.Dispose(); } catch (Exception) { }     // le service lit EOF et s'arrête
            try { requestIn.Dispose(); } catch (Exception) { }
            try { responseIn.Dispose(); } catch (Exception) { }
            if (worker != null) worker.Join(2000);
            try { responseOut.Dispose(); } catch (Exception) { }
        }
    }
}
