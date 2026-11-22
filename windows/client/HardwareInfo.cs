// loco-client : caractéristiques du poste pour sa fiche dans loco (section « Caractéristiques »), comme l'agent Linux —
// processeur, mémoire, disque système, version et démarrage de Windows, virtualisation ; et la configuration matérielle :
// machine et numéro de série, BIOS, carte mère, barrettes de mémoire, disques physiques. Lecture seule (WMI, registre).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Management;
using Microsoft.Win32;

namespace Loco
{
    static class HardwareInfo
    {
        // Valeurs d'usine sans intérêt que les fabricants laissent dans le SMBIOS : traitées comme vides.
        static readonly HashSet<string> Noise = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
            "to be filled by o.e.m.", "default string", "none", "not specified", "not available", "not applicable", "unknown",
            "system manufacturer", "system product name", "system serial number", "system version", "o.e.m.", "0", "" };
        const long GiB = 1L << 30, MiB = 1L << 20;

        public static string Text(object value, int max)
        {
            string s = (Convert.ToString(value) ?? "").Trim();
            if (Noise.Contains(s)) return "";
            return s.Length > max ? s.Substring(0, max) : s;
        }

        static long Long(object value)
        {
            try { return value == null ? 0 : Convert.ToInt64(value); }
            catch (Exception) { return 0; }
        }

        // Instances d'une classe WMI, réduites aux propriétés demandées. « SELECT * » : une propriété que cette version de
        // Windows ne connaît pas vaut null au lieu de faire échouer toute la requête.
        static List<Dictionary<string, object>> Rows(string scope, string cls, params string[] props)
        {
            var rows = new List<Dictionary<string, object>>();
            try
            {
                using (var searcher = new ManagementObjectSearcher(scope, "SELECT * FROM " + cls))
                using (var results = searcher.Get())
                    foreach (ManagementBaseObject o in results)
                    {
                        var row = new Dictionary<string, object>();
                        foreach (string p in props)
                        {
                            try { row[p] = o[p]; }
                            catch (ManagementException) { row[p] = null; }
                        }
                        rows.Add(row);
                        o.Dispose();
                        if (rows.Count >= 64) break;
                    }
            }
            catch (Exception) { }
            return rows;
        }

        static object Get(List<Dictionary<string, object>> rows, string prop)
        {
            return rows.Count > 0 ? rows[0][prop] : null;
        }

        // Taille d'une barrette, écrite comme dmidecode : « 16 GB », « 512 MB ».
        public static string Size(long bytes)
        {
            if (bytes <= 0) return "";
            return bytes % GiB == 0 ? (bytes / GiB) + " GB" : (bytes / MiB) + " MB";
        }

        // Type de mémoire : code SMBIOS (SMBIOSMemoryType, Windows 10 et plus), sinon code CIM (MemoryType) — les deux
        // tables ne se recouvrent pas.
        public static string MemoryType(long smbios, long cim)
        {
            switch (smbios)
            {
                case 3: return "DRAM";
                case 7: return "RAM";
                case 15: return "SDRAM";
                case 18: return "DDR";
                case 19: return "DDR2";
                case 20: return "DDR2 FB-DIMM";
                case 24: return "DDR3";
                case 26: return "DDR4";
                case 27: return "LPDDR";
                case 28: return "LPDDR2";
                case 29: return "LPDDR3";
                case 30: return "LPDDR4";
                case 34: return "DDR5";
                case 35: return "LPDDR5";
            }
            switch (cim)
            {
                case 2: return "DRAM";
                case 9: return "RAM";
                case 17: return "SDRAM";
                case 20: return "DDR";
                case 21: return "DDR2";
                case 22: return "DDR2 FB-DIMM";
                case 24: return "DDR3";
                case 26: return "DDR4";
            }
            return "";
        }

        // Date du BIOS (« 20240115000000.000000+000 ») écrite comme le SMBIOS sous Linux : « 01/15/2024 ».
        public static string BiosDate(object value)
        {
            string s = Convert.ToString(value) ?? "";
            if (s.Length < 8 || !s.Substring(0, 8).All(char.IsDigit)) return "";
            return s.Substring(4, 2) + "/" + s.Substring(6, 2) + "/" + s.Substring(0, 4);
        }

        // Hyperviseur d'une machine virtuelle d'après son fabricant et son modèle, nommé comme systemd-detect-virt sous
        // Linux. « none » sur une machine physique (même si Hyper-V ou la sécurité basée sur la virtualisation y tournent).
        public static string Virtualization(string vendor, string model)
        {
            string v = (vendor ?? "").ToLowerInvariant(), m = (model ?? "").ToLowerInvariant();
            if (v.Contains("qemu") || m.Contains("kvm")) return "kvm";
            if (v.Contains("vmware") || m.Contains("vmware")) return "vmware";
            if (v.Contains("innotek") || m.Contains("virtualbox")) return "oracle";
            if (v.Contains("xen") || m.Contains("hvm domu")) return "xen";
            if (v.Contains("parallels")) return "parallels";
            if (v.Contains("microsoft") && m.Contains("virtual machine")) return "microsoft";
            return "none";
        }

        // Disque physique : SSD (vrai), disque mécanique (faux), ou inconnu (null) d'après MSFT_PhysicalDisk.MediaType.
        public static object Ssd(long mediaType)
        {
            if (mediaType == 4 || mediaType == 5) return true;
            if (mediaType == 3) return false;
            return null;
        }

        public static Dictionary<string, object> SystemInfo()
        {
            var info = new Dictionary<string, object>();
            var os = Rows(@"root\cimv2", "Win32_OperatingSystem", "Version", "TotalVisibleMemorySize", "FreePhysicalMemory", "LastBootUpTime");
            string version = Text(Get(os, "Version"), 40);
            object ubr = null;
            try
            {
                using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                using (var key = hklm.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
                    if (key != null) ubr = key.GetValue("UBR");
            }
            catch (Exception) { }
            if (version != "") info["kernel"] = ubr != null ? version + "." + Convert.ToString(ubr) : version;
            string arch = Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432") ?? Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "";
            info["arch"] = arch == "AMD64" ? "x86_64" : arch == "ARM64" ? "aarch64" : arch == "x86" ? "i686" : arch.ToLowerInvariant();
            var cpus = Rows(@"root\cimv2", "Win32_Processor", "Name", "NumberOfLogicalProcessors");
            if (cpus.Count > 0) info["cpu"] = Text(cpus[0]["Name"], 120);
            long logical = cpus.Sum(c => Long(c["NumberOfLogicalProcessors"]));
            info["cores"] = logical > 0 ? logical : Environment.ProcessorCount;       // comme sous Linux : processeurs logiques
            info["memory"] = Long(Get(os, "TotalVisibleMemorySize")) * 1024;
            info["memory_available"] = Long(Get(os, "FreePhysicalMemory")) * 1024;
            try
            {
                var drive = new DriveInfo(Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.Windows)));
                info["disk"] = drive.TotalSize;
                info["disk_free"] = drive.AvailableFreeSpace;
            }
            catch (Exception) { }
            try
            {
                string boot = Convert.ToString(Get(os, "LastBootUpTime"));
                if (!string.IsNullOrEmpty(boot))
                    info["boot"] = (long)(ManagementDateTimeConverter.ToDateTime(boot).ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            }
            catch (Exception) { }
            var cs = Rows(@"root\cimv2", "Win32_ComputerSystem", "Manufacturer", "Model");
            info["virtualization"] = Virtualization(Convert.ToString(Get(cs, "Manufacturer")), Convert.ToString(Get(cs, "Model")));
            return info;
        }

        public static Dictionary<string, object> Hardware()
        {
            var cs = Rows(@"root\cimv2", "Win32_ComputerSystem", "Manufacturer", "Model");
            var bios = Rows(@"root\cimv2", "Win32_BIOS", "Manufacturer", "SMBIOSBIOSVersion", "ReleaseDate", "SerialNumber");
            var board = Rows(@"root\cimv2", "Win32_BaseBoard", "Manufacturer", "Product");
            var cpus = Rows(@"root\cimv2", "Win32_Processor", "Name", "NumberOfCores", "NumberOfLogicalProcessors");
            var hw = new Dictionary<string, object> {
                { "vendor", Text(Get(cs, "Manufacturer"), 120) }, { "model", Text(Get(cs, "Model"), 120) },
                { "serial", Text(Get(bios, "SerialNumber"), 120) },
                { "bios", new Dictionary<string, object> { { "vendor", Text(Get(bios, "Manufacturer"), 120) },
                    { "version", Text(Get(bios, "SMBIOSBIOSVersion"), 120) }, { "date", BiosDate(Get(bios, "ReleaseDate")) } } },
                { "board", new Dictionary<string, object> { { "vendor", Text(Get(board, "Manufacturer"), 120) }, { "name", Text(Get(board, "Product"), 120) } } },
                { "cpu", new Dictionary<string, object> { { "model", cpus.Count > 0 ? Text(cpus[0]["Name"], 120) : "" },
                    { "sockets", cpus.Count }, { "cores", cpus.Sum(c => Long(c["NumberOfCores"])) },
                    { "threads", cpus.Sum(c => Long(c["NumberOfLogicalProcessors"])) } } } };
            var modules = new List<Dictionary<string, object>>();
            foreach (var m in Rows(@"root\cimv2", "Win32_PhysicalMemory", "Capacity", "SMBIOSMemoryType", "MemoryType", "ConfiguredClockSpeed",
                                   "Speed", "Manufacturer", "PartNumber", "DeviceLocator"))
            {
                long capacity = Long(m["Capacity"]);
                if (capacity <= 0) continue;
                long speed = Long(m["ConfiguredClockSpeed"]);
                if (speed <= 0) speed = Long(m["Speed"]);
                modules.Add(new Dictionary<string, object> {
                    { "size", Size(capacity) }, { "type", MemoryType(Long(m["SMBIOSMemoryType"]), Long(m["MemoryType"])) },
                    { "speed", speed > 0 ? speed + " MT/s" : "" }, { "vendor", Text(m["Manufacturer"], 40) },
                    { "part", Text(m["PartNumber"], 40) }, { "slot", Text(m["DeviceLocator"], 30) } });
            }
            hw["memory_modules"] = modules;
            // Disques physiques : MSFT_PhysicalDisk dit SSD ou disque mécanique ; à défaut, Win32_DiskDrive (type inconnu).
            var disks = new List<Dictionary<string, object>>();
            foreach (var d in Rows(@"root\Microsoft\Windows\Storage", "MSFT_PhysicalDisk", "DeviceId", "FriendlyName", "Size", "MediaType")
                                  .OrderBy(x => Long(x["DeviceId"])))
                disks.Add(new Dictionary<string, object> { { "name", "PhysicalDrive" + Long(d["DeviceId"]) },
                    { "model", Text(d["FriendlyName"], 60) }, { "size", Long(d["Size"]) }, { "ssd", Ssd(Long(d["MediaType"])) } });
            if (disks.Count == 0)
                foreach (var d in Rows(@"root\cimv2", "Win32_DiskDrive", "Index", "Model", "Size").OrderBy(x => Long(x["Index"])))
                    disks.Add(new Dictionary<string, object> { { "name", "PhysicalDrive" + Long(d["Index"]) },
                        { "model", Text(d["Model"], 60) }, { "size", Long(d["Size"]) }, { "ssd", null } });
            hw["disks"] = disks;
            return hw;
        }
    }
}
