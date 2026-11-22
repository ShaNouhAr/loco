"""Agent Windows : relevé des caractéristiques et du matériel (windows/client/HardwareInfo.cs). WMI n'existe pas sous Mono :
on y vérifie ce qui se calcule sans lui — tailles et types des barrettes, date du BIOS, hyperviseur, type de disque,
valeurs d'usine écartées. Le relevé WMI lui-même a été essayé sur de vrais Windows (machine virtuelle et poste physique)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r'''using System;
namespace Loco {
    static class Probe {
        static void Main() {
            Console.WriteLine(HardwareInfo.Size(17179869184L) + "|" + HardwareInfo.Size(536870912L) + "|" + HardwareInfo.Size(0) + "|");
            Console.WriteLine(HardwareInfo.MemoryType(26, 0) + "|" + HardwareInfo.MemoryType(34, 0) + "|" + HardwareInfo.MemoryType(0, 24)
                + "|" + HardwareInfo.MemoryType(2, 20) + "|" + HardwareInfo.MemoryType(7, 0) + "|" + HardwareInfo.MemoryType(0, 0) + "|");
            Console.WriteLine(HardwareInfo.BiosDate("20260416000000.000000+000") + "|" + HardwareInfo.BiosDate("") + "|" + HardwareInfo.BiosDate("garbage!") + "|");
            Console.WriteLine(HardwareInfo.Virtualization("QEMU", "Standard PC (Q35 + ICH9, 2009)") + "|" + HardwareInfo.Virtualization("ASUS", "")
                + "|" + HardwareInfo.Virtualization("Microsoft Corporation", "Virtual Machine") + "|" + HardwareInfo.Virtualization("Microsoft Corporation", "Surface Pro 9")
                + "|" + HardwareInfo.Virtualization("VMware, Inc.", "VMware20,1") + "|" + HardwareInfo.Virtualization("innotek GmbH", "VirtualBox"));
            Console.WriteLine(HardwareInfo.Ssd(4) + "|" + HardwareInfo.Ssd(3) + "|" + (HardwareInfo.Ssd(0) == null));
            Console.WriteLine(HardwareInfo.IsPhysicalGpu(@"PCI\VEN_10DE&DEV_2704&SUBSYS_51111462&REV_A1\4&256A0AA8&0&0008", "NVIDIA GeForce RTX 4080")
                + "|" + HardwareInfo.IsPhysicalGpu(@"ROOT\DISPLAY\0000", "SudoMaker Virtual Display Adapter")
                + "|" + HardwareInfo.IsPhysicalGpu(@"PCI\VEN_1234&DEV_1111", "Microsoft Basic Display Adapter") + "|" + HardwareInfo.IsPhysicalGpu(null, ""));
            Console.WriteLine(HardwareInfo.RegLong(17171480576L) + "|" + HardwareInfo.RegLong(-1) + "|" + HardwareInfo.RegLong(BitConverter.GetBytes(17171480576L))
                + "|" + HardwareInfo.RegLong(BitConverter.GetBytes(268435456)) + "|" + HardwareInfo.RegLong(null) + "|" + HardwareInfo.RegLong("x"));
            Console.WriteLine(HardwareInfo.NvidiaVersion("32.0.16.2124") + "|" + HardwareInfo.NvidiaVersion("31.0.15.5222") + "|"
                + HardwareInfo.NvidiaVersion("27.21.14.5671") + "|" + HardwareInfo.NvidiaVersion("10.0.0.18000") + "|" + HardwareInfo.NvidiaVersion("abc") + "|");
            Console.WriteLine(HardwareInfo.Text("To be filled by O.E.M.", 120) + "|" + HardwareInfo.Text("System Serial Number", 120) + "|"
                + HardwareInfo.Text("  ASUSTeK COMPUTER INC.  ", 120) + "|" + HardwareInfo.Text("0123456789", 4));
        }
    }
}'''


@unittest.skipUnless(shutil.which("mcs") and shutil.which("mono"), "Compilateur .NET/Mono (job MSI)")
class TestHardwareInfo(unittest.TestCase):
    def test_what_is_computed_without_wmi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "Probe.cs").write_text(HARNESS)
            subprocess.run(["mcs", "-warnaserror+", "-sdk:4.6.2", "-r:System.Core.dll", "-r:System.Management.dll", "-out:" + str(path / "Probe.exe"),
                            str(ROOT / "windows/client/HardwareInfo.cs"), str(path / "Probe.cs")], check=True, capture_output=True)
            out = subprocess.run(["mono", str(path / "Probe.exe")], check=True, capture_output=True, text=True,
                                 env=dict(os.environ, LANG="C.UTF-8")).stdout
        self.assertEqual(out.splitlines(), [
            "16 GB|512 MB||",
            "DDR4|DDR5|DDR3|DDR|RAM||",                    # code SMBIOS d'abord, sinon code CIM (tables différentes)
            "04/16/2026|||",
            "kvm|none|microsoft|none|vmware|oracle",       # une Surface (Microsoft, physique) n'est pas une machine virtuelle
            "True|False|True",                             # SSD, disque mécanique, inconnu (machine virtuelle)
            "True|False|False|False",                      # carte PCI ; adaptateur virtuel, pilote de base : non
            "17171480576|4294967295|17171480576|268435456|0|0",   # QWORD, DWORD (non signé), octets bruts 8 ou 4
            "621.24|552.22|456.71|180.00||",               # version NVIDIA : les cinq derniers chiffres de celle de Windows
            "||ASUSTeK COMPUTER INC.|0123",                # valeurs d'usine écartées, espaces retirés, longueur bornée
        ])


if __name__ == "__main__":
    unittest.main()
