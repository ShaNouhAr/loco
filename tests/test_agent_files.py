"""Opérations de fichiers de l'agent Linux (loco-client.py) : surtout l'espace disque (op_usage), qui additionne la
taille RÉCURSIVE de chaque élément d'un dossier, du plus gros au plus petit, sans suivre les liens ni dépasser un budget."""
import importlib.util
import os
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loco_client_files", ROOT / "portal/agents/loco-client.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class TestUsage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="loco-usage-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        # big/ = 2000 + 1000 (sous-dossier) = 3000 o ; fichier.txt = 500 o ; small/ = 10 o
        os.makedirs(os.path.join(self.dir, "big", "sub"))
        os.makedirs(os.path.join(self.dir, "small"))
        self._write("big/a.bin", 2000)
        self._write("big/sub/b.bin", 1000)
        self._write("small/c.bin", 10)
        self._write("fichier.txt", 500)

    def _write(self, rel, size):
        with open(os.path.join(self.dir, rel), "wb") as f:
            f.write(b"x" * size)

    def test_recursive_sizes_sorted_largest_first(self):
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")
        self.assertEqual([(e["name"], e["type"], e["size"]) for e in head["entries"]],
                         [("big", "dir", 3000), ("fichier.txt", "file", 500), ("small", "dir", 10)])
        self.assertEqual(head["total"], 3510)
        self.assertFalse(head["partial"])
        self.assertEqual(head["parent"], os.path.dirname(self.dir))

    def test_a_symlinked_folder_is_not_followed(self):
        os.symlink(self.dir, os.path.join(self.dir, "loop"))   # lien : taille 0, et surtout pas de boucle infinie
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")
        link = next(e for e in head["entries"] if e["name"] == "loop")
        self.assertEqual((link["type"], link["size"]), ("dir", 0))
        self.assertEqual(head["total"], 3510)                  # le lien n'ajoute rien (pas de double comptage)

    def test_the_budget_bounds_the_scan(self):
        head, _ = agent.FileSession().op_usage({"path": self.dir}, b"")   # budget large : rien de partiel
        self.assertFalse(head["partial"])
        size, partial = agent.FileSession()._tree_size(self.dir, 0)       # échéance dépassée d'emblée
        self.assertTrue(partial)

    def test_unknown_op_is_refused(self):
        with self.assertRaises(ValueError):
            agent.FileSession().handle({"op": "nope", "path": self.dir}, b"")


class TestHardware(unittest.TestCase):
    """Inventaire matériel : structure stable (clés toujours là), quel que soit ce que le poste expose, sans planter."""

    def test_hardware_info_has_a_stable_shape(self):
        hw = agent.hardware_info()
        self.assertIsInstance(hw, dict)
        for key in ("vendor", "model", "serial", "bios", "board", "cpu", "memory_modules", "disks"):
            self.assertIn(key, hw)
        for key in ("bios", "board", "cpu"):
            self.assertIsInstance(hw[key], dict)
        for key in ("memory_modules", "disks"):
            self.assertIsInstance(hw[key], list)
        # Les disques relevés portent bien leurs champs (les cartes réseau sont déjà vues ailleurs, pas ici).
        for d in hw["disks"]:
            self.assertGreaterEqual(set(d), {"name", "model", "size", "ssd"})

    def test_physical_cores_come_from_lscpu(self):
        """« Cœurs » de la fiche : cœurs physiques (cœurs par socket × sockets), distincts des processeurs logiques."""
        lscpu = "CPU(s):                  28\nThread(s) per core:      2\nCore(s) per socket:      20\nSocket(s):               1\nModel name:              Intel(R) Core(TM) i7-14700KF\n"
        with mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/" + name if name == "lscpu" else None), \
                mock.patch.object(agent, "run", lambda cmd, timeout=30: (0, lscpu)):
            cpu = agent.hardware_info()["cpu"]
        self.assertEqual((cpu["threads"], cpu["cores"], cpu["sockets"], cpu["model"]), (28, 20, 1, "Intel(R) Core(TM) i7-14700KF"))

    def test_graphics_cards_are_the_pci_display_devices(self):
        with tempfile.TemporaryDirectory() as root:
            for slot, cls, vendor, device, driver in (("0000:01:00.0", "0x030000", "0x10de", "0x2704", "nvidia"),
                                                      ("0000:00:01.0", "0x030000", "0x1234", "0x1111", "bochs-drm"),
                                                      ("0000:00:1f.0", "0x060100", "0x8086", "0x7a06", "lpc_ich")):
                path = Path(root, slot)
                path.mkdir()
                for name, value in (("class", cls), ("vendor", vendor), ("device", device)):
                    (path / name).write_text(value + "\n")
                Path(root, "drivers", driver).mkdir(parents=True)
                (path / "driver").symlink_to(Path(root, "drivers", driver))
            outputs = {"nvidia-smi": "00000000:01:00.0, NVIDIA GeForce RTX 4080, 16376, 561.24\n",
                       "lspci": '0000:01:00.0 "VGA compatible controller" "NVIDIA Corporation" "AD103 [GeForce RTX 4080]" -ra1 -p00 "Micro-Star" "Device 5111"\n'
                                '0000:00:01.0 "VGA compatible controller" "Vendor 1234" "Device 1111" -r02 -p00 "Red Hat, Inc." "Device 1100"\n'}
            with mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/" + name), \
                    mock.patch.object(agent, "run", lambda cmd, timeout=30: (0, outputs[cmd[0]])):
                gpus = agent.gpu_inventory(root)
        self.assertEqual(gpus, [{"name": "QEMU Standard VGA", "vram": 0, "driver": "bochs-drm", "pci": "1234:1111"},
                                {"name": "NVIDIA GeForce RTX 4080", "vram": 16376 * 1048576, "driver": "nvidia 561.24", "pci": "10de:2704"}])
        self.assertEqual(agent._gpu_label("Advanced Micro Devices, Inc. [AMD/ATI]", "Navi 31 [Radeon RX 7900 XTX]"), "AMD/ATI Radeon RX 7900 XTX")

    def test_dmi_ignores_factory_placeholders(self):
        self.assertEqual(agent._dmi.__code__.co_argcount, 1)   # (name) -> valeur nettoyée
        self.assertIn("default string", agent._DMI_NOISE)       # les valeurs d'usine sont écartées


if __name__ == "__main__":
    unittest.main()
