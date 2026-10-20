"""Agent Linux : lecture des paquets installés, des applications Flatpak et des caractéristiques du poste.

On charge loco-client.py comme un module et on simule « run » (les commandes locales) pour vérifier l'analyse
de leurs sorties, sans rien installer ni appeler le réseau."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "portal" / "agents" / "loco-client.py"
spec = importlib.util.spec_from_file_location("loco_client", str(SOURCE))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def fake_run(outputs):
    """Renvoie une fonction « run » qui répond selon le premier mot de la commande."""
    def run(cmd, timeout=7200):
        return outputs.get(cmd[0], (1, ""))
    return run


class TestDebianPackages(unittest.TestCase):
    def test_installed_only_with_manual_flag_and_size_in_bytes(self):
        outputs = {
            "dpkg-query": (0, "bash\t5.2-2\tamd64\tii \t1800\n"
                              "libc6\t2.37-1\tamd64\tii \t12000\n"
                              "retire\t1.0\tamd64\trc \t50\n"),      # « rc » : désinstallé, à exclure
            "apt-mark": (0, "bash\n"),
        }
        with mock.patch.object(agent, "run", fake_run(outputs)):
            pkgs = agent.installed_packages("debian")
        self.assertEqual([p["name"] for p in pkgs], ["bash", "libc6"])
        bash, libc = pkgs
        self.assertTrue(bash["manual"])
        self.assertFalse(libc["manual"])
        self.assertEqual(bash["size"], 1800 * 1024)                 # dpkg compte en Kio
        self.assertEqual(bash["arch"], "amd64")

    def test_manual_flag_tolerates_architecture_qualifier(self):
        outputs = {
            "dpkg-query": (0, "libc6\t2.37-1\tamd64\tii \t12000\n"),
            "apt-mark": (0, "libc6:amd64\n"),                        # apt-mark peut qualifier par l'architecture
        }
        with mock.patch.object(agent, "run", fake_run(outputs)):
            pkgs = agent.installed_packages("debian")
        self.assertTrue(pkgs[0]["manual"])

    def test_empty_when_dpkg_fails(self):
        with mock.patch.object(agent, "run", fake_run({})):
            self.assertEqual(agent.installed_packages("debian"), [])


class TestRhelPackages(unittest.TestCase):
    def test_excludes_keys_and_shows_epoch_only_when_set(self):
        outputs = {
            "rpm": (0, "bash\t0\t5.1.8-9.el9\tx86_64\t1800000\n"
                       "gpg-pubkey\t0\tfd431d51\t(none)\t0\n"        # clé GPG, à exclure
                       "python3\t1\t3.9.18-1.el9\tx86_64\t30000\n"),
            "dnf": (0, "bash\n"),
        }
        with mock.patch.object(agent, "run", fake_run(outputs)), \
             mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/dnf"):
            pkgs = agent.installed_packages("rhel")
        self.assertEqual([p["name"] for p in pkgs], ["bash", "python3"])
        bash, py = pkgs
        self.assertEqual(bash["version"], "5.1.8-9.el9")            # époque 0 masquée
        self.assertEqual(py["version"], "1:3.9.18-1.el9")          # époque affichée
        self.assertEqual(bash["size"], 1800000)                    # rpm compte déjà en octets
        self.assertTrue(bash["manual"])
        self.assertFalse(py["manual"])


class TestFlatpaks(unittest.TestCase):
    def test_lists_system_applications(self):
        outputs = {"flatpak": (0, "org.mozilla.firefox\tFirefox\t123.0\tstable\tflathub\n"
                                  "org.gimp.GIMP\tGIMP\t2.10.36\tstable\tflathub\n")}
        with mock.patch.object(agent, "run", fake_run(outputs)), \
             mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/flatpak"):
            apps = agent.installed_flatpaks()
        self.assertEqual([a["id"] for a in apps], ["org.mozilla.firefox", "org.gimp.GIMP"])
        self.assertEqual(apps[0]["name"], "Firefox")
        self.assertEqual(apps[0]["origin"], "flathub")

    def test_empty_without_flatpak(self):
        with mock.patch.object(agent.shutil, "which", lambda name: None):
            self.assertEqual(agent.installed_flatpaks(), [])


class TestSystemInfo(unittest.TestCase):
    def test_reports_kernel_cpu_and_virtualization(self):
        with mock.patch.object(agent, "run", fake_run({"systemd-detect-virt": (0, "kvm\n")})), \
             mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/" + name):
            info = agent.system_info()
        self.assertTrue(info["kernel"])
        self.assertGreaterEqual(info["cores"], 1)
        self.assertEqual(info["virtualization"], "kvm")
        self.assertIn("memory", info)


class TestRhelScanIsNonInteractive(unittest.TestCase):
    def test_scan_accepts_repository_keys_without_a_terminal(self):
        calls = []

        def run(cmd, timeout=7200):
            calls.append(cmd)
            return 0, ""
        with mock.patch.object(agent, "family", lambda: "rhel"), mock.patch.object(agent, "run", run), \
             mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/dnf" if name == "dnf" else None), \
             mock.patch.object(agent, "installed_packages", lambda fam: []), \
             mock.patch.object(agent, "installed_flatpaks", lambda: []), \
             mock.patch.object(agent, "system_info", lambda: {}), \
             mock.patch.object(agent, "flatpak_updates", lambda: 0), \
             mock.patch.object(agent, "reboot_required", lambda: False):
            agent.scan()
        self.assertIn(["dnf", "-y", "-q", "--refresh", "check-update"], calls)
        self.assertIn(["dnf", "-y", "-q", "updateinfo", "list", "--security", "--available"], calls)


class TestScanIncludesInventory(unittest.TestCase):
    def test_scan_adds_packages_flatpaks_and_system(self):
        with mock.patch.object(agent, "family", lambda: "debian"), \
             mock.patch.object(agent, "run", fake_run({})), \
             mock.patch.object(agent, "installed_packages", lambda fam: [{"name": "bash"}]), \
             mock.patch.object(agent, "installed_flatpaks", lambda: [{"id": "org.x.Y"}]), \
             mock.patch.object(agent, "system_info", lambda: {"kernel": "6.1"}), \
             mock.patch.object(agent, "flatpak_updates", lambda: 0), \
             mock.patch.object(agent, "reboot_required", lambda: False):
            inv = agent.scan()
        self.assertEqual(inv["packages"], [{"name": "bash"}])
        self.assertEqual(inv["flatpaks"], [{"id": "org.x.Y"}])
        self.assertEqual(inv["system"], {"kernel": "6.1"})


if __name__ == "__main__":
    unittest.main()
