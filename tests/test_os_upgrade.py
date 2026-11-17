"""Agent Linux : passage à la version majeure suivante du système (Debian, Ubuntu, Fedora).

loco-client.py est chargé comme un module ; le réseau, apt et systemd sont simulés. On vérifie la détection de la
nouvelle version, la réécriture des dépôts, et le suivi de la mise à jour par l'agent (relance, coupure, compte rendu)."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "portal" / "agents" / "loco-client.py"
spec = importlib.util.spec_from_file_location("loco_client_upgrade", str(SOURCE))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

DEBIAN_LIST = """deb http://deb.debian.org/debian bookworm main contrib non-free-firmware
# deb-src http://deb.debian.org/debian bookworm main
deb http://security.debian.org/debian-security bookworm-security main
deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable
deb https://dl.google.com/linux/chrome/deb/ stable main
"""
DEBIAN_SOURCES = """Types: deb
URIs: http://deb.debian.org/debian
Suites: bookworm bookworm-updates bookworm-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

# dépôt d'un éditeur
Types: deb
URIs: https://repo.example.org/apt
Suites: bookworm
Components: main
"""
LOCO_SOURCES = """Types: deb
URIs: https://loco:8443/apt/repositories/debian/bookworm/
Suites: ./
Signed-By: /etc/apt/keyrings/loco.gpg
"""
TRIXIE_RELEASE = "Origin: Debian\nLabel: Debian\nSuite: stable\nVersion: 13.2\nCodename: trixie\nDate: Sat, 08 Nov 2026\nMD5Sum:\n abc 123 main/binary-amd64/Packages\n"


class TestSources(unittest.TestCase):
    def test_moves_debian_and_known_repositories_and_disables_the_rest_without_deleting(self):
        missing = {("https://download.docker.com/linux/debian", "trixie"), ("https://repo.example.org/apt", "trixie"),
                   ("http://deb.debian.org/debian", "trixie-backports")}
        texts = {"/etc/apt/sources.list": DEBIAN_LIST, "/etc/apt/sources.list.d/debian.sources": DEBIAN_SOURCES,
                 "/etc/apt/sources.list.d/loco.sources": LOCO_SOURCES}
        with mock.patch.object(agent, "LANG", "fr"):
            changed, moved, disabled = agent.retarget_sources(texts, "bookworm", "trixie", lambda u, s: (u, s) not in missing)
        lines = changed["/etc/apt/sources.list"].split("\n")
        self.assertEqual(lines[0], "deb http://deb.debian.org/debian trixie main contrib non-free-firmware")
        self.assertEqual(lines[1], "# deb-src http://deb.debian.org/debian bookworm main")          # commentaire laissé
        self.assertEqual(lines[2], "deb http://security.debian.org/debian-security trixie-security main")
        self.assertTrue(lines[3].startswith("# loco (bookworm → trixie)"))                          # éditeur sans trixie
        self.assertEqual(lines[4], "# deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable")
        self.assertIn("deb https://dl.google.com/linux/chrome/deb/ stable main", lines)              # suite sans rapport
        deb822 = changed["/etc/apt/sources.list.d/debian.sources"]
        self.assertIn("Suites: trixie trixie-updates\n", deb822)                                      # backports absent : retiré
        self.assertIn("Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg", deb822)
        self.assertTrue(deb822.rstrip("\n").endswith("Suites: bookworm\nComponents: main\n# loco (bookworm → trixie) : rien pour trixie à cette adresse, dépôt désactivé\nEnabled: no"))
        self.assertIn("URIs: https://loco:8443/apt/repositories/debian/trixie/\nSuites: ./", changed["/etc/apt/sources.list.d/loco.sources"])
        self.assertIn({"uri": "http://deb.debian.org/debian", "suite": "trixie", "components": ["main", "contrib", "non-free-firmware"]}, moved)
        self.assertEqual(sorted(disabled), ["http://deb.debian.org/debian trixie-backports", "https://download.docker.com/linux/debian trixie",
                                            "https://repo.example.org/apt trixie"])

    def test_an_existing_enabled_field_is_switched_off_and_unrelated_files_are_left_alone(self):
        text = "Types: deb\nURIs: https://repo.example.org/apt\nSuites: bookworm\nComponents: main\nEnabled: yes\n"
        changed, _moved, _disabled = agent.retarget_sources({"/x.sources": text, "/y.list": "deb https://a.example/ stable main\n"},
                                                            "bookworm", "trixie", lambda u, s: False)
        self.assertNotIn("/y.list", changed)
        self.assertTrue(changed["/x.sources"].endswith("disabled\nEnabled: no\n") or changed["/x.sources"].rstrip().endswith("Enabled: no"))
        self.assertNotIn("Enabled: yes", changed["/x.sources"])

    def test_suite_names(self):
        self.assertEqual(agent.suite_for("bookworm-security", "bookworm", "trixie"), "trixie-security")
        self.assertEqual(agent.suite_for("buster/updates", "buster", "bullseye"), "bullseye-security")   # ancien nom
        self.assertEqual(agent.suite_for("bookworm-backports-sloppy", "bookworm", "trixie"), "trixie-backports-sloppy")
        self.assertIsNone(agent.suite_for("stable", "bookworm", "trixie"))
        self.assertIsNone(agent.suite_for("bookworm-1.2", "bookworm", "trixie"))
        self.assertIsNone(agent.flat_uri_for("https://bookworm.example/apt/", "bookworm", "trixie"))      # le nom d'hôte ne change pas


class TestOffers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        list_path = os.path.join(self.dir, "sources.list")
        with open(list_path, "w") as f:
            f.write(DEBIAN_LIST)
        self.patches = [mock.patch.object(agent, "SOURCES_LIST", list_path), mock.patch.object(agent, "SOURCES_DIR", self.dir + "/none"),
                        mock.patch.object(agent, "UPGRADE_JOB", os.path.join(self.dir, "job.json"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_debian_offers_the_next_release_once_published_as_stable(self):
        asked = []

        def fetch(url, **kw):
            asked.append(url)
            return TRIXIE_RELEASE
        with mock.patch.object(agent, "fetch_small", fetch):
            offer = agent.debian_offer({"VERSION_ID": "12", "VERSION_CODENAME": "bookworm"})
        self.assertEqual(offer, {"kind": "debian", "version": "13", "codename": "trixie", "name": "Debian 13 (trixie)"})
        self.assertEqual(asked, ["http://deb.debian.org/debian/dists/trixie/Release"])           # l'archive du poste
        with mock.patch.object(agent, "fetch_small", lambda url, **kw: TRIXIE_RELEASE.replace("Suite: stable", "Suite: testing")):
            self.assertIsNone(agent.debian_offer({"VERSION_ID": "12", "VERSION_CODENAME": "bookworm"}))   # pas encore publiée
        self.assertIsNone(agent.debian_offer({"VERSION_CODENAME": "forky"}))                    # testing, sans numéro

    def test_ubuntu_follows_do_release_upgrade(self):
        out = "Checking for a new Ubuntu release\nNew release '26.04.1 LTS' available.\nRun 'do-release-upgrade' to upgrade to it.\n"
        with mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/" + name), mock.patch.object(agent, "run", lambda cmd, **kw: (0, out)):
            self.assertEqual(agent.ubuntu_offer({"VERSION_ID": "24.04"}),
                             {"kind": "ubuntu", "version": "26.04", "name": "Ubuntu 26.04.1 LTS"})
        with mock.patch.object(agent.shutil, "which", lambda name: "/usr/bin/" + name), \
                mock.patch.object(agent, "run", lambda cmd, **kw: (1, "Checking for a new Ubuntu release\nNo new release found.\n")):
            self.assertIsNone(agent.ubuntu_offer({"VERSION_ID": "26.04"}))

    def test_fedora_asks_fedora_for_the_state_of_the_next_release(self):
        with mock.patch.object(agent, "fetch_small", lambda url, **kw: json.dumps({"name": "F44", "state": "current"})), \
                mock.patch.object(agent.os.path, "exists", lambda p: False):
            self.assertEqual(agent.fedora_offer({"VERSION_ID": "43"}), {"kind": "fedora", "version": "44", "name": "Fedora Linux 44"})
        with mock.patch.object(agent, "fetch_small", lambda url, **kw: json.dumps({"state": "pending"})), \
                mock.patch.object(agent.os.path, "exists", lambda p: False):
            self.assertIsNone(agent.fedora_offer({"VERSION_ID": "44"}))

    def test_checked_once_a_day_and_again_after_the_upgrade(self):
        calls = []
        rel = {"ID": "debian", "VERSION_ID": "12", "VERSION_CODENAME": "bookworm"}
        offer = {"kind": "debian", "version": "13", "codename": "trixie", "name": "Debian 13 (trixie)"}
        state = {}
        with mock.patch.object(agent, "os_release", lambda: rel), mock.patch.object(agent, "debian_offer", lambda r: calls.append(1) or offer):
            self.assertEqual(agent.release_offer(state), offer)
            self.assertEqual(agent.release_offer(state), offer)
            self.assertEqual(len(calls), 1)
            rel["VERSION_ID"] = "13"
            agent.release_offer(state)
            self.assertEqual(len(calls), 2)                                            # nouvelle version : nouvelle recherche
        with mock.patch.object(agent, "os_release", lambda: dict(rel, ID="rocky")):
            self.assertIsNone(agent.release_offer(state))                              # Rocky, Alma, RHEL : pas pris en charge

    def test_a_network_failure_keeps_the_previous_answer(self):
        rel = {"ID": "debian", "VERSION_ID": "12", "VERSION_CODENAME": "bookworm"}
        state = {"release": {"base": "debian 12", "checked": time.time() - 2 * 86400, "offer": {"kind": "debian"}}}

        def broken(r):
            raise OSError("réseau")
        with mock.patch.object(agent, "os_release", lambda: rel), mock.patch.object(agent, "debian_offer", broken):
            self.assertEqual(agent.release_offer(state), {"kind": "debian"})

    def test_disk_space_read_from_apt(self):
        out = ("Need to get 1,234 MB/1,300 MB of archives.\nAfter this operation, 2,345 kB of additional disk space will be used.\n"
               "E: Trivial Only specified but this is not a trivial operation.\n")
        self.assertEqual(agent.upgrade_space(out), (1234000000, 2345000))
        self.assertEqual(agent.upgrade_space("After this operation, 12.5 MB disk space will be freed.\n"), (0, -12500000))


class TestFollowUp(unittest.TestCase):
    """Suivi par l'agent : la mise à jour tourne hors de lui ; il la déclare tenue, la relance, la solde si elle meurt."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.reports = []
        for name, value in (("UPGRADE_DIR", self.dir), ("UPGRADE_JOB", os.path.join(self.dir, "job.json")),
                            ("UPGRADE_LOG", os.path.join(self.dir, "upgrade.log"))):
            p = mock.patch.object(agent, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(agent, "report", lambda conf, cmd_id, state, output="", code=None, **kw: self.reports.append((cmd_id, state, output)) or True)
        p.start()
        self.addCleanup(p.stop)

    def job(self, **values):
        job = dict({"id": 41, "kind": "debian", "target": {"version": "13", "codename": "trixie", "name": "Debian 13 (trixie)"}}, **values)
        agent.save_job(job)
        return job

    def test_invalid_or_concurrent_requests_are_refused(self):
        with mock.patch.object(agent, "os_release", lambda: {"ID": "debian", "VERSION_ID": "12"}):
            agent.os_upgrade_start({}, {"id": 1, "target": {"kind": "ubuntu", "version": "26.04"}})       # autre système
            agent.os_upgrade_start({}, {"id": 2, "target": {"kind": "debian", "version": "13"}})          # sans nom de code
            self.job(stage="running", boot=agent.boot_id(), pid=os.getpid())
            agent.os_upgrade_start({}, {"id": 3, "target": {"kind": "debian", "version": "13", "codename": "trixie"}})
        self.assertEqual([(r[0], r[1]) for r in self.reports], [(1, "failed"), (2, "failed"), (3, "failed")])

    def test_starts_outside_the_agent_and_is_held_while_running(self):
        launched = []
        with mock.patch.object(agent, "os_release", lambda: {"ID": "debian", "VERSION_ID": "12", "PRETTY_NAME": "Debian GNU/Linux 12 (bookworm)"}), \
                mock.patch.object(agent, "launch_worker", lambda job: launched.append(job["id"]) or agent.save_job(dict(job, stage="running", boot=agent.boot_id(), launched=time.time())) or True):
            agent.os_upgrade_start({}, {"id": 7, "target": {"kind": "debian", "version": "13", "codename": "trixie", "name": "Debian 13 (trixie)"}})
        self.assertEqual(launched, [7])
        self.assertEqual(agent.os_upgrade_poll({}), 7)                                  # tenue tant qu'elle tourne
        self.assertEqual(self.reports, [])

    def test_a_worker_that_died_is_reported_failed_then_forgotten(self):
        self.job(stage="running", boot="un-autre-démarrage", pid=1)
        self.assertIsNone(agent.os_upgrade_poll({}))
        self.assertEqual(self.reports[0][:2], (41, "failed"))
        self.assertIn("dpkg --configure -a", self.reports[0][2])
        self.assertIsNone(agent.upgrade_job())
        self.assertTrue(os.path.exists(agent.UPGRADE_JOB + ".done"))

    def test_a_result_not_yet_delivered_is_sent_at_the_next_contact(self):
        self.job(stage="done", result={"state": "success", "output": "Passé à Debian GNU/Linux 13 (trixie).", "code": 0})
        self.assertIsNone(agent.os_upgrade_poll({}))
        self.assertEqual(self.reports, [(41, "success", "Passé à Debian GNU/Linux 13 (trixie).")])

    def test_resumes_after_the_restart_it_asked_for(self):
        self.job(stage="reboot-first", reboot_at=time.time() - 600, kind="ubuntu")
        relaunched = []
        with mock.patch.object(agent, "boot_time", lambda: int(time.time()) - 60), \
                mock.patch.object(agent, "launch_worker", lambda job: relaunched.append(job.get("rebooted")) or agent.save_job(dict(job, stage="running", boot=agent.boot_id(), launched=time.time())) or True):
            self.assertEqual(agent.os_upgrade_poll({}), 41)
        self.assertEqual(relaunched, [True])
        self.job(stage="reboot-first", reboot_at=time.time() - 60, kind="ubuntu")       # pas encore redémarré
        with mock.patch.object(agent, "boot_time", lambda: int(time.time()) - 86400):
            self.assertEqual(agent.os_upgrade_poll({}), 41)

    def test_fedora_is_checked_after_its_offline_installation(self):
        self.job(stage="offline", reboot_at=time.time() - 900, kind="fedora", target={"version": "44", "name": "Fedora Linux 44"})
        with mock.patch.object(agent, "boot_time", lambda: int(time.time()) - 60), \
                mock.patch.object(agent, "os_release", lambda: {"VERSION_ID": "44", "PRETTY_NAME": "Fedora Linux 44 (Server Edition)"}):
            self.assertIsNone(agent.os_upgrade_poll({}))
        self.assertEqual(self.reports[0][:2], (41, "success"))
        self.assertIn("Fedora Linux 44 (Server Edition)", self.reports[0][2])

    def test_other_actions_wait_while_it_runs(self):
        self.job(stage="running", boot=agent.boot_id(), pid=os.getpid())
        ran = []
        with mock.patch.object(agent, "scan", lambda *a, **k: ran.append("scan")), mock.patch.object(agent, "update", lambda *a, **k: ran.append("update")):
            agent._run_commands({}, {"commands": [{"id": 5, "action": "update"}, {"id": 6, "action": "reboot"}]}, {})
        self.assertEqual(ran, [])
        self.assertEqual([(r[0], r[1]) for r in self.reports if r[1] != "running"], [(5, "failed"), (6, "failed")])


class TestDebianUpgrade(unittest.TestCase):
    """Déroulé Debian avec apt simulé : refus avant tout changement, dépôts remis comme avant si la suite échoue."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        os.makedirs(os.path.join(self.dir, "sources.list.d"))
        self.list = os.path.join(self.dir, "sources.list")
        with open(self.list, "w") as f:
            f.write(DEBIAN_LIST)
        for name, value in (("UPGRADE_DIR", os.path.join(self.dir, "up")), ("UPGRADE_LOG", os.path.join(self.dir, "up.log")),
                            ("SOURCES_LIST", self.list), ("SOURCES_DIR", os.path.join(self.dir, "sources.list.d"))):
            p = mock.patch.object(agent, name, value)
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(agent.UPGRADE_DIR)
        for p in (mock.patch.object(agent, "os_release", lambda: {"ID": "debian", "VERSION_ID": "12", "VERSION_CODENAME": "bookworm",
                                                                  "PRETTY_NAME": "Debian GNU/Linux 12 (bookworm)"}),
                  mock.patch.object(agent, "report", lambda *a, **k: True), mock.patch.object(agent, "run_stream", lambda *a, **k: (0, ""))):
            p.start()
            self.addCleanup(p.stop)
        self.work = agent.OsUpgrade({}, {"id": 9, "kind": "debian", "target": {"version": "13", "codename": "trixie"}})
        self.addCleanup(self.work.out.close)
        self.work.exists = lambda uri, suite: "docker" not in uri

    def fake_run(self, held=""):
        def run(cmd, timeout=7200, env=None):
            if cmd[:2] == ["apt-mark", "showhold"]:
                return 0, held
            return 0, ""
        return run

    def test_the_log_keeps_the_final_state_of_lines_rewritten_in_place(self):
        self.work.write("(Reading database ... 5%\r(Reading database ... 100%\r(Reading database ... 1306 files installed.)\r")
        self.work.out.flush()
        with open(agent.UPGRADE_LOG) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith(" (Reading database ... 1306 files installed.)"))

    def test_held_packages_stop_it_before_anything_changes(self):
        with mock.patch.object(agent, "run", self.fake_run(held="linux-image-amd64\n")), mock.patch.object(agent, "apt_update", lambda: (0, "")):
            outcome, text = self.work.debian()
        self.assertEqual(outcome, "failed")
        self.assertIn("linux-image-amd64", text)
        with open(self.list) as f:
            self.assertEqual(f.read(), DEBIAN_LIST)

    def test_unreadable_new_repositories_put_the_old_ones_back(self):
        updates = iter([(0, ""), (100, "E: Failed to fetch http://deb.debian.org/debian/dists/trixie/InRelease  503\n"), (0, "")])
        with mock.patch.object(agent, "run", self.fake_run()), mock.patch.object(agent, "apt_update", lambda: next(updates)):
            outcome, text = self.work.debian()
        self.assertEqual(outcome, "failed")
        with open(self.list) as f:
            self.assertEqual(f.read(), DEBIAN_LIST)                                     # remis comme avant
        self.assertTrue(os.path.exists(os.path.join(agent.UPGRADE_DIR, "sources-bookworm") + self.list))   # copie gardée

    def test_success_when_the_new_version_is_reached(self):
        versions = iter(["12", "13"])                                                  # au début, puis à la vérification
        rel = lambda: {"ID": "debian", "VERSION_ID": next(versions, "13"), "VERSION_CODENAME": "bookworm", "PRETTY_NAME": "Debian GNU/Linux 13 (trixie)"}
        with mock.patch.object(agent, "run", self.fake_run()), mock.patch.object(agent, "apt_update", lambda: (0, "")), \
                mock.patch.object(agent, "os_release", rel):
            outcome, text = self.work.debian()
        self.assertEqual(outcome, "success", text)
        with open(self.list) as f:
            content = f.read()
        self.assertIn("deb http://deb.debian.org/debian trixie main", content)
        self.assertIn("# deb [arch=amd64", content)                                     # dépôt sans trixie désactivé


if __name__ == "__main__":
    unittest.main()
