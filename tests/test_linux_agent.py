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


class TestTrustBundle(unittest.TestCase):
    """Faisceau d'autorités reçu au contact : remplace ca.crt s'il est intact, jamais sans autorité épinglée."""

    def setUp(self):
        import hashlib
        import os
        import subprocess
        import tempfile
        self.hashlib, self.os = hashlib, os
        self.dir = tempfile.mkdtemp()
        self.ca = os.path.join(self.dir, "ca.crt")
        pems = []
        for name in ("loco CA", "Corp CA"):
            out = os.path.join(self.dir, name.replace(" ", "") + ".crt")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                            "-days", "30", "-subj", f"/CN={name}", "-keyout", os.path.join(self.dir, "k.pem"), "-out", out],
                           check=True, capture_output=True)
            with open(out) as f:
                pems.append(f.read())
        self.loco, self.bundle = pems[0], pems[0] + pems[1]
        with open(self.ca, "w") as f:
            f.write(self.loco)
        patch = mock.patch.object(agent, "CA", self.ca)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        log = mock.patch.object(agent, "log")
        log.start()
        self.addCleanup(log.stop)

    def answer(self, pem, sha=None):
        return {"trust": {"pem": pem, "sha256": sha or self.hashlib.sha256(pem.encode()).hexdigest()}}

    def content(self):
        with open(self.ca) as f:
            return f.read()

    def test_bundle_replaces_the_pinned_authority_and_is_reported(self):
        agent.apply_trust(self.answer(self.bundle))
        self.assertEqual(self.content(), self.bundle)
        self.assertEqual(agent.identity()["trust"], self.hashlib.sha256(self.bundle.encode()).hexdigest())

    def test_damaged_or_foreign_bundles_are_ignored(self):
        agent.apply_trust(self.answer(self.bundle, sha="0" * 64))                  # empreinte fausse
        agent.apply_trust(self.answer("pas un certificat"))                        # pas de PEM
        agent.apply_trust({"trust": "rien"})
        self.assertEqual(self.content(), self.loco)

    def test_agent_using_the_system_store_is_left_alone(self):
        self.os.unlink(self.ca)
        agent.apply_trust(self.answer(self.bundle))
        self.assertFalse(self.os.path.exists(self.ca))
        self.assertEqual(agent.trust_sha(), "")


class TestAptUpdateLockRetry(unittest.TestCase):
    """apt-get update patiente quand un autre apt tient le verrou (installation en cours, mises à jour
    automatiques), et ne réessaie pas une erreur sans rapport."""
    LOCK = (100, "Reading package lists...\nE: Could not get lock /var/lib/dpkg/lock-frontend. It is held by "
                 "process 1 (apt)\nE: Unable to lock directory /var/lib/apt/lists/\n")
    DONE = (0, "Hit:1 https://loco ./ InRelease\n")

    def test_lock_busy_detects_apt_contention_only(self):
        self.assertTrue(agent._apt_lock_busy(self.LOCK[1]))
        self.assertFalse(agent._apt_lock_busy("E: Failed to fetch https://loco/InRelease  Could not resolve host"))

    def test_apt_update_retries_until_the_lock_is_released(self):
        calls, seq = [], [self.LOCK, self.LOCK, self.DONE]
        def run(cmd, timeout=7200):
            calls.append(cmd); return seq[min(len(calls) - 1, len(seq) - 1)]
        with mock.patch.object(agent, "run", run), mock.patch.object(agent.time, "sleep", lambda *_: None):
            code, _ = agent.apt_update()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)                             # a patienté puis réussi
        self.assertIn("DPkg::Lock::Timeout=60", calls[0])          # option de verrou transmise à apt

    def test_apt_update_does_not_retry_a_network_error(self):
        calls = []
        def run(cmd, timeout=7200):
            calls.append(cmd); return (100, "E: Failed to fetch https://loco/InRelease  Could not resolve host\n")
        with mock.patch.object(agent, "run", run), mock.patch.object(agent.time, "sleep", lambda *_: None):
            code, _ = agent.apt_update()
        self.assertEqual((code, len(calls)), (100, 1))             # pas de reprise inutile
