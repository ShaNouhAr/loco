// loco-client : agent Wazuh du poste (fonctionnalité facultative de loco) — ce qui se lit et se calcule sans rien
// toucher au système : dossier d'installation, état lu dans ses fichiers, adresse du manager dans ossec.conf, contrôle
// de la commande reçue. De client.keys, seuls le numéro et le nom de l'agent sont lus, jamais sa clé.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;

namespace Loco
{
    static class WazuhState
    {
        public static readonly Regex Manager = new Regex(@"^[A-Za-z0-9.:\[\]-]{1,253}$");
        public static readonly Regex Group = new Regex(@"^[A-Za-z0-9_.,-]{0,300}$");
        public static readonly Regex Package = new Regex(@"^wazuh-agent-[0-9.]+-1\.msi$");
        static readonly Regex Address = new Regex(@"(<client>.*?<address>)[^<]*(</address>)", RegexOptions.Singleline);
        static readonly Regex Status = new Regex(@"^status='(\w+)'", RegexOptions.Multiline);
        static readonly Regex Current = new Regex(@"<client>.*?<address>\s*([^<\s]+)\s*</address>", RegexOptions.Singleline);

        // Dossier de l'agent Wazuh : sous « Program Files (x86) » sur un Windows 64 bits (l'agent est en 32 bits).
        public static string Home()
        {
            foreach (string variable in new[] { "ProgramFiles(x86)", "ProgramFiles" })
            {
                string root = Environment.GetEnvironmentVariable(variable);
                if (string.IsNullOrEmpty(root)) continue;
                string home = Path.Combine(root, "ossec-agent");
                if (Installed(home)) return home;
            }
            return null;
        }

        public static bool Installed(string home)
        {
            return !string.IsNullOrEmpty(home) && (File.Exists(Path.Combine(home, "wazuh-agent.exe")) || File.Exists(Path.Combine(home, "ossec-agent.exe")));
        }

        // État remonté à loco. « running » : le service Windows de l'agent tourne ; « version » : celle du registre.
        public static Dictionary<string, object> Read(string home, bool running, string version)
        {
            if (!Installed(home)) return new Dictionary<string, object> { { "installed", false } };
            string status = "", manager = "", id = "", name = "";
            try
            {
                var found = Status.Match(File.ReadAllText(Path.Combine(home, "wazuh-agent.state")));
                if (found.Success && running) status = found.Groups[1].Value;
            }
            catch (Exception) { }
            try
            {
                var found = Current.Match(File.ReadAllText(Path.Combine(home, "ossec.conf")));
                if (found.Success) manager = found.Groups[1].Value;
            }
            catch (Exception) { }
            try
            {
                using (var reader = new StreamReader(Path.Combine(home, "client.keys")))
                {
                    string[] fields = (reader.ReadLine() ?? "").Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                    if (fields.Length >= 2) { id = Cut(fields[0], 8); name = Cut(fields[1], 128); }
                }
            }
            catch (Exception) { }
            return new Dictionary<string, object> { { "installed", true }, { "version", Cut((version ?? "").TrimStart('v'), 20) }, { "active", running },
                { "status", status }, { "manager", Cut(manager, 255) }, { "id", id }, { "name", name } };
        }

        // ossec.conf avec l'adresse du manager remplacée (poste déjà équipé) ; le texte reçu si rien n'est à changer.
        public static string WithManager(string conf, string manager)
        {
            return Address.Replace(conf, m => m.Groups[1].Value + manager + m.Groups[2].Value, 1);
        }

        // Dernières erreurs du journal de l'agent Wazuh : elles disent pourquoi l'enrôlement n'aboutit pas.
        public static List<string> LastErrors(string home, int limit)
        {
            var found = new List<string>();
            try
            {
                string[] lines = File.ReadAllLines(Path.Combine(home, "ossec.log"));
                for (int i = Math.Max(0, lines.Length - 200); i < lines.Length; i++)
                {
                    if (lines[i].IndexOf(": ERROR: ", StringComparison.Ordinal) < 0) continue;
                    string text = Cut(Regex.Replace(lines[i], @"^\S+ \S+ ", "").Trim(), 300);
                    found.Remove(text);
                    found.Add(text);
                }
            }
            catch (Exception) { }
            return found.Count > limit ? found.GetRange(found.Count - limit, limit) : found;
        }

        static string Cut(string text, int length)
        {
            return text.Length > length ? text.Substring(0, length) : text;
        }
    }
}
