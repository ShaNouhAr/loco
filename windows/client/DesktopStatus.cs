// État public en lecture seule : aucun jeton, aucune commande, aucun accès au service.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Web.Script.Serialization;

namespace Loco
{
    public sealed class DesktopStatus
    {
        public static readonly string DirectoryPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "loco-client-status");
        public static readonly string FilePath = Path.Combine(DirectoryPath, "status.json");
        public string Color = "grey";
        public string Reason = Lang.T("En attente des informations de loco", "Awaiting information from loco", "A la espera de la información de loco");
        public string UpdatedAt = "";

        public static DesktopStatus Load()
        {
            var result = new DesktopStatus();
            try
            {
                if (new FileInfo(FilePath).Length > 16384) return result;
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(FilePath));
                DateTime updated;
                string stamp = Convert.ToString(data["updated_at"]);
                if (!DateTime.TryParse(stamp, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out updated)) return result;
                result.UpdatedAt = updated.ToUniversalTime().ToString("o");
                if (DateTime.UtcNow - updated.ToUniversalTime() > TimeSpan.FromMinutes(10) || updated.ToUniversalTime() > DateTime.UtcNow.AddMinutes(2))
                {
                    result.Reason = Lang.T("Informations anciennes : contact avec loco à vérifier", "Stale information: check-in with loco to verify", "Información antigua: comprobar el contacto con loco");
                    return result;
                }
                string color = Convert.ToString(data["color"]);
                if (color != "green" && color != "orange" && color != "red" && color != "grey") return result;
                string reason = Convert.ToString(data["reason"]);
                if (String.IsNullOrWhiteSpace(reason)) return result;
                result.Color = color;
                result.Reason = reason.Length > 2000 ? reason.Substring(0, 2000) : reason;
            }
            catch (Exception) { }
            return result;
        }
    }
}
