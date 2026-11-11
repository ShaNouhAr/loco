"""Agent Linux : lecture des paquets installés, des applications Flatpak et des caractéristiques du poste.

On charge loco-client.py comme un module et on simule « run » (les commandes locales) pour vérifier l'analyse
de leurs sorties, sans rien installer ni appeler le réseau."""

import importlib.util
import os
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


# Sorties réelles d'apt-get update (Debian 13), code de retour 0 dans les deux cas.
APT_NOTHING = """Err:1 http://security.debian.org trixie-security InRelease
  Could not connect to 192.168.42.226:3142 (192.168.42.226). - connect (113: No route to host)
Err:2 http://deb.debian.org/debian trixie InRelease
  Unable to connect to 192.168.42.226:3142:
Reading package lists...
W: Failed to fetch http://security.debian.org/dists/trixie-security/InRelease  Could not connect to 192.168.42.226:3142
W: Some index files failed to download. They have been ignored, or old ones used instead.
"""
APT_PARTIAL = """Hit:1 http://deb.debian.org/debian trixie InRelease
Get:2 http://deb.debian.org/debian trixie-updates InRelease [47.3 kB]
Err:3 http://ppa.example.org/abandoned noble InRelease
  Temporary failure resolving 'ppa.example.org'
Reading package lists...
W: Failed to fetch http://ppa.example.org/abandoned/dists/noble/InRelease  Temporary failure resolving 'ppa.example.org'
W: Some index files failed to download. They have been ignored, or old ones used instead.
"""
APT_FINE = "Hit:1 http://deb.debian.org/debian trixie InRelease\nReading package lists...\n"
# Sortie réelle (Debian 13, code de retour 100) : un index périmé parmi des dépôts lus, et un dépôt injoignable.
APT_EXPIRED = """Ign:1 http://nexiste-pas.invalid/debian trixie InRelease
Get:2 http://deb.debian.org/debian trixie InRelease [140 kB]
Get:3 http://deb.debian.org/debian trixie-updates InRelease [47.3 kB]
Get:4 http://deb.debian.org/debian trixie/main amd64 Packages [9678 kB]
Err:1 http://nexiste-pas.invalid/debian trixie InRelease
  Could not resolve 'nexiste-pas.invalid'
Reading package lists...
E: Release file for http://deb.debian.org/debian/dists/trixie-updates/InRelease is expired (invalid since 9h 58min 20s). Updates for this repository will not be applied.
"""
APT_ONLY_EXPIRED = """Get:1 http://deb.debian.org/debian trixie-updates InRelease [47.3 kB]
Reading package lists...
E: Release file for http://deb.debian.org/debian/dists/trixie-updates/InRelease is expired (invalid since 9h 58min 20s). Updates for this repository will not be applied.
"""
APT_UNSIGNED = """Hit:1 http://deb.debian.org/debian trixie InRelease
Get:2 http://depot.example.org/apt stable InRelease [3 kB]
Reading package lists...
W: GPG error: http://depot.example.org/apt stable InRelease: The following signatures couldn't be verified because the public key is not available: NO_PUBKEY 0123456789ABCDEF
E: The repository 'http://depot.example.org/apt stable InRelease' is not signed.
"""
UPGRADE = "Inst curl [8.0] (8.1 Debian:13/stable [amd64])\n"


class TestAptRepositoriesDown(unittest.TestCase):
    """apt-get update rend 0 même si aucun dépôt ne répond : l'agent doit le voir sans bloquer pour un seul dépôt mort."""

    def apt(self, update_output, code=0):
        def run(cmd, timeout=7200):
            if cmd[:2] == ["apt-get", "update"]:
                return code, update_output
            if cmd[0] == "apt-get" and "-s" in cmd:
                return 0, UPGRADE
            return 1, ""
        return run

    def test_nothing_readable_is_a_failure_with_its_cause(self):
        with mock.patch.object(agent, "run", self.apt(APT_NOTHING)):
            code, out = agent.apt_update()
        self.assertEqual(code, 100)
        self.assertIn("192.168.42.226:3142", out.splitlines()[-1])
        self.assertIn("(113: No route to host)", out.splitlines()[-1])

    def test_one_repository_down_among_others_is_not_a_failure(self):
        with mock.patch.object(agent, "run", self.apt(APT_PARTIAL)):
            self.assertEqual(agent.apt_update(), (0, APT_PARTIAL))

    def test_healthy_or_empty_output_changes_nothing(self):
        for output in (APT_FINE, ""):
            with mock.patch.object(agent, "run", self.apt(output)):
                self.assertEqual(agent.apt_update(), (0, output))

    def test_failure_already_reported_by_apt_is_kept(self):
        with mock.patch.object(agent, "run", self.apt("E: Malformed entry\n", code=100)):
            self.assertEqual(agent.apt_update(), (100, "E: Malformed entry\n"))

    def test_an_expired_index_among_readable_repositories_is_not_a_failure(self):
        """Index périmé (copie trop ancienne servie par un cache) : apt rend 100, mais les autres dépôts se mettent à jour."""
        with mock.patch.object(agent, "run", self.apt(APT_EXPIRED, code=100)):
            self.assertEqual(agent.apt_update(), (0, APT_EXPIRED))
        self.assertEqual(agent.apt_unreachable(APT_EXPIRED),
                         {"http://nexiste-pas.invalid/debian trixie": "Could not resolve 'nexiste-pas.invalid'",
                          "http://deb.debian.org/debian trixie-updates": "Release file is expired (invalid since 9h 58min 20s)"})
        inv = self.inventory(APT_EXPIRED, code=100)
        self.assertEqual(inv["error"], "")
        self.assertRegex(inv["warning"], r"^2 (dépôts inutilisables|repositories unusable)")
        self.assertIn("http://deb.debian.org/debian trixie-updates (Release file is expired (invalid since 9h 58min 20s))", inv["warning"])
        self.assertIn("(Could not resolve 'nexiste-pas.invalid')", inv["warning"])
        self.assertEqual([u["name"] for u in inv["updates"]], ["curl"])

    def test_an_unsigned_repository_among_others_is_named_too(self):
        with mock.patch.object(agent, "run", self.apt(APT_UNSIGNED, code=100)):
            self.assertEqual(agent.apt_update()[0], 0)
        self.assertEqual(agent.apt_unreachable(APT_UNSIGNED), {"http://depot.example.org/apt stable": "repository is not signed"})

    def test_when_every_repository_is_refused_it_stays_a_failure_that_names_it(self):
        with mock.patch.object(agent, "run", self.apt(APT_ONLY_EXPIRED, code=100)):
            code, out = agent.apt_update()
        self.assertEqual(code, 100)
        inv = self.inventory(APT_ONLY_EXPIRED, code=100)
        self.assertIn("is expired", inv["error"])

    def test_other_errors_are_never_excused(self):
        for output in ("Hit:1 http://deb.debian.org/debian trixie InRelease\nE: Malformed entry 3 in list file /etc/apt/sources.list\n",
                       APT_EXPIRED + "E: Could not get lock /var/lib/apt/lists/lock. It is held by process 1 (apt)\n",
                       "Get:1 http://deb.debian.org/debian trixie InRelease\nE: Release file for http://deb.debian.org/debian/dists/trixie/InRelease "
                       "is not valid yet (invalid for another 3h). Updates for this repository will not be applied.\n"):
            with mock.patch.object(agent, "run", self.apt(output, code=100)), mock.patch.object(agent.time, "sleep", lambda s: None), \
                    mock.patch.object(agent.time, "monotonic", side_effect=[0, 100, 200, 300]):
                self.assertEqual(agent.apt_update()[0], 100, output[-60:])

    def test_update_goes_on_with_an_expired_index(self):
        streamed = []

        def stream(cmd, _on_line, timeout=7200):
            streamed.append(cmd)
            return 0, ""
        with mock.patch.object(agent, "run", self.apt(APT_EXPIRED, code=100)), mock.patch.object(agent, "run_stream", stream), \
                mock.patch.object(agent, "family", return_value="debian"), mock.patch.object(agent.shutil, "which", return_value=None):
            code, _log = agent.update()
        self.assertEqual((code, bool(streamed)), (0, True))

    def test_an_error_of_the_apt_cache_is_told_as_such(self):
        """Sortie réelle (Debian 13 derrière le cache APT de loco, dépôt injoignable depuis le serveur)."""
        out = ("Err:1 http://nexiste-pas.invalid/debian trixie InRelease\n  502  Bad Gateway [IP: 172.21.0.2 3142]\n"
               "Err:8 http://192.0.2.10/debian trixie InRelease\n  504  Gateway Time-out [IP: 172.21.0.2 3142]\n"
               "Err:9 http://miroir.example/debian trixie InRelease\n  503  Service Unavailable [IP: 203.0.113.7 80]\n")
        with mock.patch.object(agent, "apt_proxy_ports", return_value={"3142"}):
            failed = agent.apt_unreachable(out)
        self.assertRegex(failed["http://nexiste-pas.invalid/debian trixie"], r"172\.21\.0\.2:3142 .*\(502\)$")
        self.assertRegex(failed["http://192.0.2.10/debian trixie"], r"(cache APT|APT cache) 172\.21\.0\.2:3142 .*\(504\)$")
        self.assertEqual(failed["http://miroir.example/debian trixie"], "503  Service Unavailable [IP: 203.0.113.7 80]")   # le dépôt lui-même
        with mock.patch.object(agent, "apt_proxy_ports", return_value=set()):                   # poste sans proxy : rien n'est réécrit
            self.assertEqual(agent.apt_unreachable(out)["http://192.0.2.10/debian trixie"], "504  Gateway Time-out [IP: 172.21.0.2 3142]")

    def test_proxy_ports_are_read_from_the_apt_configuration(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "01loco-proxy"), "w") as f:
                f.write('Acquire::http::Proxy "http://192.168.10.18:3142";\nAcquire::https::Proxy "DIRECT";\n')
            with open(os.path.join(folder, "02autre"), "w") as f:
                f.write('// Acquire::http::Proxy "http://ancien:9999";\nacquire::HTTP::proxy "http://proxy.lan:8080/";\n')
            with mock.patch.object(agent.glob, "glob", return_value=[os.path.join(folder, n) for n in ("01loco-proxy", "02autre", "absent")]):
                self.assertEqual(agent.apt_proxy_ports(), {"3142", "8080"})

    def test_cause_does_not_end_with_a_colon(self):
        self.assertEqual(agent.apt_unreachable("Err:1 http://a.example/x y InRelease\n  Unable to connect to a:3142:\n"),
                         {"http://a.example/x y": "Unable to connect to a:3142"})

    def test_same_repository_is_only_counted_once(self):
        out = "Err:1 http://a.example/x y InRelease\n  down\nErr:2 http://a.example/x y Release\n  down\n"
        self.assertEqual(agent.apt_unreachable(out), {"http://a.example/x y": "down"})

    def inventory(self, output, code=0):
        with mock.patch.object(agent, "run", self.apt(output, code)), mock.patch.object(agent, "family", return_value="debian"), \
                mock.patch.object(agent, "flatpak_updates", return_value=[], create=True), mock.patch.object(agent.shutil, "which", return_value=None):
            return agent.scan()

    def test_scan_reports_the_outage_as_an_error(self):
        inv = self.inventory(APT_NOTHING)
        self.assertIn("apt-get update : ", inv["error"])
        self.assertIn("192.168.42.226:3142", inv["error"])
        self.assertEqual(inv["warning"], "")

    def test_scan_keeps_counting_updates_with_one_repository_down(self):
        inv = self.inventory(APT_PARTIAL)
        self.assertEqual(inv["error"], "")
        self.assertIn("http://ppa.example.org/abandoned noble", inv["warning"])
        self.assertEqual([u["name"] for u in inv["updates"]], ["curl"])

    def test_scan_stays_silent_when_all_is_fine(self):
        inv = self.inventory(APT_FINE)
        self.assertEqual((inv["error"], inv["warning"]), ("", ""))

    def test_update_stops_when_nothing_is_readable_but_not_for_one_repository(self):
        for output, upgraded in ((APT_NOTHING, False), (APT_PARTIAL, True)):
            streamed = []

            def stream(cmd, _on_line, timeout=7200):
                streamed.append(cmd)
                return 0, ""
            with mock.patch.object(agent, "run", self.apt(output)), mock.patch.object(agent, "run_stream", stream), \
                    mock.patch.object(agent, "family", return_value="debian"), mock.patch.object(agent.shutil, "which", return_value=None):
                code, log = agent.update()
            self.assertEqual(bool(streamed), upgraded, output[:20])
            self.assertEqual(code != 0, not upgraded)
            if not upgraded:
                self.assertIn("192.168.42.226:3142", log)


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


class TestServiceLoop(unittest.TestCase):
    """Boucle de contact : premier contact en deux temps (partiel puis complet), puis contacts rapprochés tant que le
    serveur demande un intervalle court, et arrêt au retour au repos — sans refaire l'inventaire."""

    def _run(self, answers, state, relaunched=None):
        calls, seq = [], iter(answers)
        relaunched = [] if relaunched is None else relaunched
        def fake_checkin(conf, data):
            calls.append(data)
            return next(seq)
        loads = iter([{"token": "t"}, dict(state)])
        with mock.patch.object(agent, "load", lambda path: next(loads)), \
             mock.patch.object(agent, "save", lambda *a: None), \
             mock.patch.object(agent, "ensure_remote_service", lambda: None), \
             mock.patch.object(agent, "reapply_wake", lambda s: None), \
             mock.patch.object(agent, "identity", lambda: {"hostname": "h"}), \
             mock.patch.object(agent, "metrics", lambda s: {}), \
             mock.patch.object(agent, "family", lambda: "debian"), \
             mock.patch.object(agent, "scan_local", lambda fam=None: {"packages": [{"name": "vim"}]}), \
             mock.patch.object(agent, "scan", lambda state=None, local=None: dict({"updates": [], "error": ""}, **(local or {}))), \
             mock.patch.object(agent, "checkin", fake_checkin), \
             mock.patch.object(agent, "_apply_answer", lambda a, s: None), \
             mock.patch.object(agent, "forward_logs", lambda *a: None), \
             mock.patch.object(agent, "_run_commands", lambda *a: False), \
             mock.patch.multiple(agent, self_update=lambda conf, wanted: bool(wanted) and wanted != agent.VERSION,
                                 relaunch=lambda: relaunched.append(len(calls)) or 0,
                                 forget_package_key=lambda: False), \
             mock.patch.object(agent, "ensure_desktop_backend", lambda s: None), \
             mock.patch.object(agent, "_apt_lock_busy", lambda o: False), \
             mock.patch.object(agent, "booted_since", lambda last: False), \
             mock.patch.object(agent.time, "sleep", lambda s: None), \
             mock.patch.object(agent.fcntl, "flock", lambda *a: None), \
             mock.patch("builtins.open", mock.mock_open()):
            rc = agent.service()
        return rc, calls

    def test_first_contact_sends_partial_then_full(self):
        rc, calls = self._run([{"interval": 120, "commands": []}, {"interval": 120, "commands": []}], state={})
        self.assertEqual((rc, len(calls)), (0, 2))                   # partiel + complet
        self.assertTrue(calls[0]["inventory"]["partial"])            # 1er contact : partiel (fiche tout de suite)
        self.assertNotIn("partial", calls[1]["inventory"])          # 2e : inventaire complet (comptage)

    def test_active_window_repeats_fast_until_rest_without_rescanning(self):
        rc, calls = self._run([{"interval": 120, "commands": []},    # partiel (réponse ignorée)
                               {"interval": 10, "commands": []},     # complet → entre en fenêtre active
                               {"interval": 10, "commands": []},     # tour 1
                               {"interval": 120, "commands": []}],   # tour 2 → repos : on s'arrête
                              state={})
        self.assertEqual(len(calls), 4)
        self.assertNotIn("inventory", calls[2])                     # la boucle active ne refait pas l'inventaire
        self.assertNotIn("inventory", calls[3])

    def test_known_device_at_rest_contacts_once_without_partial_or_loop(self):
        rc, calls = self._run([{"interval": 120, "commands": []}],
                              state={"last_scan": agent.time.time(), "inventory_version": agent.VERSION})
        self.assertEqual((rc, len(calls)), (0, 1))                   # ni partiel (déjà connu) ni boucle
        self.assertNotIn("inventory", calls[0])                     # inventaire récent : pas refait


class TestSelfUpdate(unittest.TestCase):
    """Mise à jour automatique : le nouveau programme prend la place de l'ancien tout de suite. Constat d'une instance :
    le processus en cours, resté sur l'ancien code, se retéléchargeait à chaque contact de la fenêtre active (9 fois)."""
    known = {"last_scan": agent.time.time(), "inventory_version": agent.VERSION}

    def test_the_new_program_takes_over_instead_of_looping(self):
        relaunched = []
        answers = [{"interval": 10, "commands": [], "agent_version": "99.0.0"}] + [{"interval": 10, "commands": []}] * 5
        rc, calls = TestServiceLoop._run(self, answers, dict(self.known), relaunched)
        self.assertEqual((rc, len(calls), relaunched), (0, 1, [1]))          # un seul contact, puis le nouveau code

    def test_it_also_takes_over_from_the_active_window(self):
        relaunched = []
        answers = [{"interval": 10, "commands": []}, {"interval": 10, "commands": [], "agent_version": "99.0.0"},
                   {"interval": 10, "commands": []}]
        rc, calls = TestServiceLoop._run(self, answers, dict(self.known), relaunched)
        self.assertEqual((len(calls), relaunched), (2, [2]))

    def test_nothing_is_relaunched_without_a_new_version(self):
        relaunched = []
        TestServiceLoop._run(self, [{"interval": 120, "commands": [], "agent_version": agent.VERSION}], dict(self.known), relaunched)
        self.assertEqual(relaunched, [])

    def test_self_update_tells_whether_the_program_was_replaced(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            program = os.path.join(folder, "loco-client.py")
            new = 'VERSION = "99.0.0"\nprint("nouveau")\n'
            with mock.patch.object(agent, "PROGRAM", program), mock.patch.object(agent, "log", lambda *a: None):
                with mock.patch.object(agent, "download", return_value=new.encode()):
                    self.assertFalse(agent.self_update({"server": "https://loco"}, agent.VERSION))     # déjà à jour
                    self.assertFalse(agent.self_update({"server": "https://loco"}, ""))
                    self.assertFalse(os.path.exists(program))
                    self.assertFalse(agent.self_update({"server": "https://loco"}, "98.0.0"))          # pas la version annoncée
                    self.assertTrue(agent.self_update({"server": "https://loco"}, "99.0.0"))
                with open(program) as f:
                    self.assertEqual(f.read(), new)
                with mock.patch.object(agent, "download", side_effect=OSError("réseau")):
                    self.assertFalse(agent.self_update({"server": "https://loco"}, "100.0.0"))
                with mock.patch.object(agent, "download", return_value=b"def (:"):
                    self.assertFalse(agent.self_update({"server": "https://loco"}, "100.0.0"))

    def test_relaunch_runs_the_installed_program_with_the_same_arguments(self):
        with mock.patch.object(agent.os, "execv") as execv, mock.patch.object(agent.sys, "argv", ["/x/loco-client.py", "service"]):
            agent.relaunch()
        execv.assert_called_once_with(agent.PROGRAM, [agent.PROGRAM, "service"])
        with mock.patch.object(agent.os, "execv", side_effect=OSError("absent")), mock.patch.object(agent, "log", lambda *a: None):
            self.assertEqual(agent.relaunch(), 0)                          # la minuterie lancera le nouveau code


class TestWhatEachContactAnnounces(unittest.TestCase):
    def test_boot_time_and_held_commands(self):
        self.assertGreater(agent.boot_time(), 1_000_000_000)                # lu du noyau
        self.assertEqual(agent.identity()["boot"], agent.boot_time())
        sent = []

        def call(server, path, data, token=None, timeout=60):
            sent.append(data)
            return {"commands": []}
        with mock.patch.object(agent, "call", call), mock.patch.object(agent, "known_servers", return_value=["https://loco"]):
            agent.checkin({"server": "https://loco", "token": "t"}, {"hostname": "h"})
        self.assertEqual(sent[0]["holding"], [])                            # rien en cours entre deux contacts

    def test_boot_time_survives_an_unreadable_kernel_file(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertEqual(agent.boot_time(), 0)


class TestDesktopDeps(unittest.TestCase):
    """Un poste KDE/GNOME en Wayland se voit installer tout seul, une fois, les composants du bureau distant."""

    def _run(self, dpkg_out):
        calls = []
        def run(cmd, timeout=7200):
            calls.append(cmd)
            return (0, dpkg_out) if cmd[0] == "dpkg-query" else (0, "")
        return calls, run

    def _ensure(self, state, kde, gnome, dpkg_out):
        calls, run = self._run(dpkg_out)
        with mock.patch.object(agent, "family", lambda: "debian"), \
             mock.patch.object(agent.shutil, "which", lambda n: "/usr/bin/apt-get"), \
             mock.patch.object(agent, "wayland_environment", lambda: {"uid": 1000}), \
             mock.patch.object(agent, "_kde_session", lambda uid: kde), \
             mock.patch.object(agent, "_gnome_session", lambda uid: gnome), \
             mock.patch.object(agent, "run", run), mock.patch.object(agent, "log", lambda *a: None):
            agent.ensure_desktop_backend(state)
        return [c for c in calls if c and c[0] == "apt-get"], calls

    def test_installs_only_the_missing_kde_components(self):
        state = {}
        install, _ = self._ensure(state, kde=True, gnome=False, dpkg_out="python3-gi install ok installed\n")
        self.assertEqual(len(install), 1)
        for p in ("gir1.2-gstreamer-1.0", "gstreamer1.0-pipewire", "xdg-desktop-portal-kde"):
            self.assertIn(p, install[0])
        self.assertNotIn("python3-gi", install[0])                       # déjà présent
        self.assertTrue(state.get("desktop_deps_at"))

    def test_gnome_does_not_pull_the_kde_portal(self):
        install, _ = self._ensure({}, kde=False, gnome=True, dpkg_out="")
        self.assertNotIn("xdg-desktop-portal-kde", install[0])
        self.assertIn("python3-gi", install[0])

    def test_nothing_when_all_present(self):
        out = "".join("%s install ok installed\n" % p for p in
                      ("python3-gi", "gir1.2-gstreamer-1.0", "gstreamer1.0-pipewire", "xdg-desktop-portal-kde"))
        install, _ = self._ensure({}, kde=True, gnome=False, dpkg_out=out)
        self.assertEqual(install, [])

    def test_skips_non_desktop_and_wlroots(self):
        install, calls = self._ensure({}, kde=False, gnome=False, dpkg_out="")
        self.assertEqual((install, calls), ([], []))                     # pas même le dpkg-query

    def test_does_not_hammer_apt_after_a_recent_attempt(self):
        install, _ = self._ensure({"desktop_deps_at": agent.time.time()}, kde=True, gnome=False, dpkg_out="")
        self.assertEqual(install, [])


class TestEnrollment(unittest.TestCase):
    """Réinscription : l'agent prouve au serveur qu'il détient le jeton du poste, sans jamais le présenter, et garde
    l'identité que le serveur retient."""

    KEY = "cle-d-inscription"

    def enroll(self, conf, server="https://loco.test:8443", answer=None):
        calls, saved = [], {}

        def call(target, path, data, token=None, timeout=60):
            calls.append({"server": target, "path": path, "data": dict(data), "token": token})
            return answer or {"token": data["uid"] + ".nouveau", "uid": data["uid"], "interval": 120}

        with mock.patch.object(agent, "load", lambda path, default=None: dict(conf) if path == agent.CONFIG else {}), \
             mock.patch.object(agent, "save", lambda path, data, mode=0o600: saved.__setitem__(path, data)), \
             mock.patch.object(agent.os, "makedirs", lambda *a, **k: None), \
             mock.patch.object(agent, "identity", lambda: {"hostname": "srv", "os": {"family": "debian", "name": "Debian"}}), \
             mock.patch.object(agent, "call", call), mock.patch.object(agent, "log", lambda *a: None):
            code = agent.enroll(server, self.KEY)
        return code, calls[0], saved.get(agent.CONFIG)

    def expected(self, uid, secret):
        import hashlib
        import hmac
        return hmac.new(hashlib.sha256(secret.encode()).hexdigest().encode(),
                        f"loco-enroll-v1\n{uid}\n{self.KEY}".encode(), hashlib.sha256).hexdigest()

    def test_first_enrollment_has_nothing_to_prove(self):
        code, sent, conf = self.enroll({})
        self.assertEqual(code, 0)
        self.assertNotIn("proof", sent["data"])
        self.assertEqual((sent["path"], sent["token"]), ("/agent/api/enroll", None))
        self.assertEqual((conf["uid"], conf["token"]), (sent["data"]["uid"], sent["data"]["uid"] + ".nouveau"))

    def test_reenrollment_proves_it_holds_the_token_without_showing_it(self):
        uid = "ab" * 16
        old = {"server": "https://loco.test:8443", "uid": uid, "token": uid + ".secret-du-poste"}
        code, sent, conf = self.enroll(old)
        self.assertEqual(sent["data"]["uid"], uid)
        self.assertEqual(sent["data"]["proof"], self.expected(uid, "secret-du-poste"))
        self.assertIsNone(sent["token"])                                 # jamais d'en-tête Authorization à l'inscription
        self.assertNotIn("secret-du-poste", repr(sent))
        self.assertEqual((conf["uid"], conf["token"]), (uid, uid + ".nouveau"))

    def test_the_proof_also_goes_to_a_new_address_of_the_server(self):
        """Adresse du serveur changée pendant que le poste était éteint : la commande d'installation le réinscrit sous
        la même identité. La preuve ne révèle rien à un autre serveur."""
        uid = "ab" * 16
        code, sent, conf = self.enroll({"server": "https://ancienne.test:8443", "uid": uid, "token": uid + ".secret-du-poste"},
                                       server="https://nouvelle.test:8443")
        self.assertEqual(sent["data"]["proof"], self.expected(uid, "secret-du-poste"))
        self.assertEqual(conf["server"], "https://nouvelle.test:8443")

    def test_the_identity_chosen_by_the_server_is_kept(self):
        uid, fresh = "ab" * 16, "cd" * 16
        code, sent, conf = self.enroll({"server": "https://loco.test:8443", "uid": uid},
                                       answer={"token": fresh + ".nouveau", "uid": fresh, "interval": 120})
        self.assertNotIn("proof", sent["data"])                          # plus de jeton : rien à prouver
        self.assertEqual((conf["uid"], conf["token"]), (fresh, fresh + ".nouveau"))

    def test_an_older_server_without_uid_in_its_answer_keeps_the_local_one(self):
        uid = "ab" * 16
        code, sent, conf = self.enroll({"server": "https://loco.test:8443", "uid": uid},
                                       answer={"token": uid + ".nouveau", "interval": 120})
        self.assertEqual(conf["uid"], uid)


if __name__ == "__main__":
    unittest.main()


class TestEnrollmentKeyDoesNotStay(unittest.TestCase):
    """La clé d'inscription ne reste ni sur le disque du poste, ni dans la liste de ses processus."""
    def test_package_script_gives_way_to_a_note_once_the_machine_is_enrolled(self):
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "install.sh")
            with open(path, "w") as f:
                f.write("#!/bin/sh\nLOCO_ENROLL_KEY='secret-key' loco-client.py enroll --server 'https://loco.test'\n")
            os.chmod(path, 0o644)                                # paquet d'avant : lisible par tous
            self.assertTrue(agent.forget_package_key(path))
            content = Path(path).read_text()
            self.assertNotIn("secret-key", content)
            self.assertTrue(content.startswith("#!/bin/sh\n"))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.listdir(work), ["install.sh"])   # aucun fichier temporaire laissé
            self.assertFalse(agent.forget_package_key(path))     # déjà fait : rien à réécrire
            self.assertFalse(agent.forget_package_key(os.path.join(work, "absent.sh")))   # poste installé sans paquet

    def test_key_is_read_from_the_environment(self):
        calls = []
        with mock.patch.object(agent.os, "geteuid", return_value=0), \
                mock.patch.object(agent, "enroll", side_effect=lambda server, key: calls.append((server, key)) or 0), \
                mock.patch.dict(agent.os.environ, {"LOCO_ENROLL_KEY": "cle-env"}):
            self.assertEqual(agent.main(["enroll", "--server", "https://loco.test"]), 0)
            self.assertEqual(agent.main(["enroll", "--server", "https://loco.test", "--key", "cle-arg"]), 0)   # forme historique
        self.assertEqual(calls, [("https://loco.test", "cle-env"), ("https://loco.test", "cle-arg")])
        with mock.patch.object(agent.os, "geteuid", return_value=0), mock.patch.dict(agent.os.environ, clear=False) as env, \
                mock.patch.object(agent.sys, "stderr"):
            env.pop("LOCO_ENROLL_KEY", None)
            self.assertEqual(agent.main(["enroll", "--server", "https://loco.test"]), 2)   # sans clé : mode d'emploi
