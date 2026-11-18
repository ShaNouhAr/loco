"""Logiciels hors catalogue des postes Windows : relevés par winget sur le poste (identifiant, version), comparés par loco
à son index winget, montrés et cochables avec les mises à jour Windows, mis à jour par l'agent, blocables, et sans jamais
rejoindre le catalogue de loco."""
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, WINDOWS, device
from loco import fleet, winget
from loco.db import transaction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Index winget : (identifiant, nom, versions publiées).
PACKAGES = [("7zip.7zip", "7-Zip", ("24.09", "26.06")), ("Notepad++.Notepad++", "Notepad++", ("8.9", "8.9.9.1")),
            ("VideoLAN.VLC", "VLC media player", ("3.0.21", "3.0.24")), ("Example.Tool", "Example Tool", ("1.0", "2.0")),
            ("Vendor.UpToDate", "À jour", ("5.0",)), ("Microsoft.AppInstaller", "App Installer", ("1.24", "1.28")),
            ("Microsoft.EdgeWebView2Runtime", "Microsoft Edge WebView2 Runtime", ("140.0", "157.0")),
            ("Devolutions.RemoteDesktopManager", "Remote Desktop Manager", ("2026.3.15.0", "2026.3.18.0"))]
INSTALLED = [{"id": "7zip.7zip", "version": "25.01"}, {"id": "Notepad++.Notepad++", "version": "8.9"},
             {"id": "VideoLAN.VLC", "version": "3.0.21"}, {"id": "Example.Tool", "version": "1.0"},
             {"id": "Vendor.UpToDate", "version": "5.0"}, {"id": "Absent.FromIndex", "version": "1.0"},
             {"id": "Vendor.Unknown", "version": "Unknown"}, {"id": "bad id; rm", "version": "1.0"},
             {"id": "Microsoft.AppInstaller", "version": "1.24"}, {"id": "Microsoft.EdgeWebView2Runtime", "version": "140.0"},
             {"id": "Devolutions.RemoteDesktopManager", "version": "2026.3.15.0"}]


class SoftwareTest(FleetTest):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.index = os.path.join(tmp.name, "index.db")
        with sqlite3.connect(self.index) as conn:
            conn.executescript("CREATE TABLE ids (id TEXT); CREATE TABLE names (name TEXT); CREATE TABLE versions (version TEXT); "
                               "CREATE TABLE manifest (id INTEGER, name INTEGER, version INTEGER, moniker INTEGER, pathpart INTEGER, hash BLOB);")
            for i, (package, name, versions) in enumerate(PACKAGES, 1):
                conn.execute("INSERT INTO ids VALUES (?)", (package,))
                for version in versions:
                    n = conn.execute("INSERT INTO names VALUES (?)", (name,)).lastrowid
                    v = conn.execute("INSERT INTO versions VALUES (?)", (version,)).lastrowid
                    conn.execute("INSERT INTO manifest VALUES (?,?,?,0,0,NULL)", (i, n, v))
        for patch in (mock.patch.object(winget, "INDEX", self.index), mock.patch.object(winget, "refresh_later")):
            self.refresh = patch.start()
            self.addCleanup(patch.stop)
        with transaction() as conn:
            conn.execute("DELETE FROM update_blocks")
        self.addCleanup(self.forget_blocks)

    def forget_blocks(self):
        with transaction() as conn:
            conn.execute("DELETE FROM update_blocks")

    def machine(self, software=INSTALLED, note=None, updates=()):
        agent = Agent(WINDOWS, "192.168.43.50")
        agent.enroll()
        inv = {"updates": list(updates), "apps": [], "reboot_required": False, "error": ""}
        if software is not None:
            inv["software"] = software
        if note:
            inv["software_note"] = note
        agent.checkin(inv)
        return agent

    def commands(self, agent):
        return agent.checkin().get_json().get("commands") or []

    def page(self, agent):
        return self.admin.c.get(f"/fleet/{device(agent.uid)['id']}").get_data(as_text=True)


class TestDetection(SoftwareTest):
    def test_only_newer_versions_of_the_index_are_kept(self):
        agent = self.machine()
        inv = fleet.inventory(device(agent.uid))
        # Remote Desktop Manager compris ; ni App Installer ni WebView2, que Windows et Edge tiennent à jour eux-mêmes.
        self.assertEqual([(s["id"], s["version"], s["latest"]) for s in inv["software"]],
                         [("7zip.7zip", "25.01", "26.06"), ("Example.Tool", "1.0", "2.0"),
                          ("Notepad++.Notepad++", "8.9", "8.9.9.1"),
                          ("Devolutions.RemoteDesktopManager", "2026.3.15.0", "2026.3.18.0"), ("VideoLAN.VLC", "3.0.21", "3.0.24")])
        self.assertEqual(inv["software"][0]["name"], "7-Zip")
        dev = device(agent.uid)
        self.assertEqual(dev["software_updates"], 5)
        self.assertEqual(dev["updates"], 0)                                    # le système, lui, est à jour
        with fixture.loco.app.test_request_context():
            color, reason = fleet.status(dev)
            self.assertTrue(fleet.needs_update(dev))
        self.assertEqual(color, "orange")
        self.assertIn("5 logiciels à mettre à jour", reason)

    def test_software_of_the_loco_catalog_keeps_its_own_way_and_nothing_joins_the_catalog(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, winget_id, status) VALUES ('example-tool', 'Example Tool', '2.0', 'Example.Tool', 'ready')")
        agent = self.machine()
        ids = [s["id"] for s in fleet.inventory(device(agent.uid))["software"]]
        self.assertNotIn("Example.Tool", ids)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM win_apps").fetchone()[0], 1)

    def test_an_older_agent_or_no_winget_says_why(self):
        agent = self.machine(software=[], note="winget absent du poste (paquet App Installer de Microsoft)")
        self.assertEqual(device(agent.uid)["software_updates"], 0)
        self.assertIn("Logiciels hors catalogue : winget absent du poste", self.page(agent))
        old = self.machine(software=None)                                     # agent d'avant : rien à comparer
        self.assertEqual(device(old.uid)["software_updates"], 0)

    def test_without_an_index_nothing_is_guessed_and_it_is_fetched_in_the_background(self):
        os.unlink(self.index)
        agent = self.machine()
        self.assertEqual(device(agent.uid)["software_updates"], 0)
        self.refresh.assert_called()
        old = time.time() - winget.MAX_AGE - 10
        open(self.index, "wb").close()
        os.utime(self.index, (old, old))
        self.refresh.reset_mock()
        winget.upgrades(INSTALLED)
        self.refresh.assert_called_once()                                     # index trop ancien : renouvelé à part


class TestUpdating(SoftwareTest):
    def test_shown_and_checkable_with_the_windows_updates(self):
        agent = self.machine(updates=[{"id": "a1b2c3d4-0000-1111-2222-333344445555", "name": "Mise à jour cumulative",
                                       "version": "KB5032190", "security": True}])
        html = self.page(agent)
        self.assertIn("Système et logiciels", html)
        self.assertIn('name="only_software" value="7zip.7zip"', html)
        self.assertIn("25.01 → 26.06", html)
        self.assertIn('data-update-row="7zip.7zip"', html)
        dev_id = device(agent.uid)["id"]
        r = self.admin.post(f"/fleet/{dev_id}/action", {"action": "update", "scope": "selection", "only_software": "7zip.7zip"},
                            page=f"/fleet/{dev_id}")
        self.assertEqual(r.status_code, 302)
        cmd, = self.commands(agent)
        self.assertEqual(cmd["software"], ["7zip.7zip"])
        self.assertEqual(cmd["only"]["updates"], [])                          # seulement le logiciel coché
        self.assertNotIn("apps", cmd)

    def test_a_full_update_takes_every_software_but_the_blocked_ones(self):
        agent = self.machine()
        dev_id = device(agent.uid)["id"]
        self.admin.post("/fleet/blocks", {"family": "windows", "pattern": "VLC*", "reason": "version figée"}, page="/fleet/blocks")
        dev = device(agent.uid)
        self.assertEqual((dev["software_updates"], dev["blocked_updates"]), (4, 1))
        html = self.page(agent)
        self.assertNotIn('value="VideoLAN.VLC"', html)
        self.assertIn("version figée", html)
        self.admin.post(f"/fleet/{dev_id}/action", {"action": "update"}, page=f"/fleet/{dev_id}")
        cmd, = self.commands(agent)
        self.assertEqual(sorted(cmd["software"]), ["7zip.7zip", "Devolutions.RemoteDesktopManager", "Example.Tool", "Notepad++.Notepad++"])
        # Un blocage par identifiant winget vaut aussi.
        self.admin.post("/fleet/blocks", {"family": "windows", "pattern": "7zip.*"}, page="/fleet/blocks")
        self.assertEqual(device(agent.uid)["software_updates"], 3)

    def test_a_selection_is_limited_to_what_the_machine_announces(self):
        agent = self.machine()
        dev_id = device(agent.uid)["id"]
        r = self.admin.post(f"/fleet/{dev_id}/action", {"action": "update", "scope": "selection",
                                                        "only_software": ["Inconnu.Logiciel", "bad id; rm"]}, page=f"/fleet/{dev_id}")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.commands(agent), [])

    def test_the_result_refreshes_the_list(self):
        agent = self.machine()
        dev_id = device(agent.uid)["id"]
        self.admin.post(f"/fleet/{dev_id}/action", {"action": "update", "scope": "selection", "only_software": "7zip.7zip"},
                        page=f"/fleet/{dev_id}")
        cmd, = self.commands(agent)
        after = [dict(s, version="26.06") if s["id"] == "7zip.7zip" else s for s in INSTALLED]
        agent.result(cmd["id"], "success", "OK : 7zip.7zip",
                     inventory={"updates": [], "apps": [], "reboot_required": False, "error": "", "software": after})
        self.assertNotIn("7zip.7zip", [s["id"] for s in fleet.inventory(device(agent.uid))["software"]])
        self.assertEqual(device(agent.uid)["software_updates"], 4)

    def test_a_fallback_installer_is_attached_when_winget_cannot(self):
        # Repli à winget : pour un logiciel dont l'installeur du manifeste a été retiré (HWiNFO), loco joint au poste
        # l'adresse de l'installeur officiel courant et l'éditeur attendu ; l'agent le vérifie par signature avant de poser.
        agent = self.machine()
        dev_id = device(agent.uid)["id"]
        desc = {"REALiX.HWiNFO": {"url": "https://www.sac.sk/download/utildiag/hwi_860x.exe", "type": "inno",
                                  "args": "/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART", "signer": "REALiX", "version": "8.60"}}
        with mock.patch("loco.winget_fallback.descriptors", return_value=desc):
            self.admin.post(f"/fleet/{dev_id}/action", {"action": "update"}, page=f"/fleet/{dev_id}")
            cmd, = self.commands(agent)       # le repli est joint quand l'agent récupère la commande (_payload)
        self.assertEqual(cmd["software_fallback"]["REALiX.HWiNFO"]["signer"], "REALiX")
        self.assertTrue(cmd["software_fallback"]["REALiX.HWiNFO"]["url"].endswith("hwi_860x.exe"))


class WingetFallbackTest(unittest.TestCase):
    """Repli à winget : l'installeur officiel de l'éditeur, résolu depuis une source ouverte (sans navigateur ni service
    tiers), confié au poste pour les logiciels que winget ne sait pas (ou plus) mettre à jour."""

    def test_resolves_hwinfo_from_the_official_feed_and_mirror(self):
        from loco import winget_fallback
        feed = ("<rss><channel>"
                "<item><title>HWiNFO v8.60 released</title></item>"
                "<item><title>HWiNFO v8.61-6125 Beta released</title></item>"
                "<item><title>HWiNFO for DOS v6.2.7 released</title></item>"
                "</channel></rss>")
        with mock.patch("loco.winget_fallback._get", return_value=feed), \
             mock.patch("loco.winget_fallback._head_ok", return_value=True):
            winget_fallback._CACHE.clear()
            d = winget_fallback.descriptor("REALiX.HWiNFO")
        self.assertEqual(d["version"], "8.60")                        # la bêta 8.61 est ignorée
        self.assertEqual(d["url"], "https://www.sac.sk/download/utildiag/hwi_860x.exe")
        self.assertEqual((d["signer"], d["type"]), ("REALiX", "inno"))

    def test_no_descriptor_when_the_official_mirror_lacks_the_file(self):
        from loco import winget_fallback
        with mock.patch("loco.winget_fallback._get", return_value="<item><title>HWiNFO v8.60 released</title></item>"), \
             mock.patch("loco.winget_fallback._head_ok", return_value=False):
            winget_fallback._CACHE.clear()
            self.assertIsNone(winget_fallback.descriptor("REALiX.HWiNFO"))

    def test_software_without_a_known_fallback_has_none(self):
        from loco import winget_fallback
        self.assertIsNone(winget_fallback.descriptor("7zip.7zip"))


class TestWindowsAgentSource(unittest.TestCase):
    """L'agent Windows : winget pris dans App Installer avec ses bibliothèques VCLibs (compte SYSTEM), relevé par
    « winget export » (JSON, non localisé, identifiants entiers), mise à jour une à une, identifiants vérifiés."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "windows", "client", "LocoClient.cs"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_winget_runs_under_system_with_its_libraries(self):
        self.assertIn('"Microsoft.DesktopAppInstaller_*_" + arch + "__8wekyb3d8bbwe"', self.src)
        self.assertIn('"Microsoft.VCLibs.140.00.UWPDesktop_*_" + arch + "__8wekyb3d8bbwe"', self.src)
        self.assertIn('psi.EnvironmentVariables["PATH"] = pathPrefix', self.src)

    def test_inventory_and_upgrade_commands(self):
        self.assertIn('"export -o \\"" + file + "\\" --include-versions --source winget', self.src)
        self.assertIn('"upgrade --id \\"" + pid + "\\" --exact --source winget --silent --accept-package-agreements', self.src)
        self.assertIn('new Regex(@"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")', self.src)
        self.assertIn("result == 0x8A15002B", self.src)
        self.assertIn('if (action == "update") UpgradeSoftware(software, softwareFallback as Dictionary<string, object>, lines, progress, markPkg);', self.src)   # un échec logiciel ne fait pas échouer la commande
        self.assertIn("if (!selection && !InstallCatalogApps(", self.src)

    def test_what_winget_cannot_update_is_set_aside_until_its_version_changes(self):
        self.assertIn("result == 0x8A150014 || result == 0x8A15002B", self.src)
        self.assertIn('RunCapture(winget, arguments + " --locale en-US"', self.src)     # installeur anglais sur un Windows français
        self.assertIn('Path.Combine(Root, "winget-skip.json")', self.src)
        self.assertIn("if (!lastExport.TryGetValue(pid, out now) || now != skip[pid]) skip.Remove(pid);", self.src)

    def test_a_software_failure_does_not_fail_the_update_and_closes_the_app(self):
        # Un logiciel winget hors catalogue qui échoue (installeur MSI en échec 0x8A150049, souvent parce qu'il est ouvert)
        # ne fait pas échouer toute la mise à jour et n'est PAS abandonné. L'agent ferme ses processus sans confirmation,
        # réessaie une fois, et sinon le signale (réessayé plus tard). Rien n'est mis de côté pour un échec d'installeur.
        self.assertIn('if (action == "update") UpgradeSoftware(software, softwareFallback as Dictionary<string, object>, lines, progress, markPkg);', self.src)
        self.assertIn("static string InstallDir(string wingetId, string winget, string libraries)", self.src)
        self.assertIn("static int CloseApp(string dir)", self.src)
        self.assertIn("QueryFullProcessImageName", self.src)
        self.assertIn("int closed = CloseApp(InstallDir(pid, winget, libraries));", self.src)
        # Installé « pour tout le monde » (ALLUSERS) : winget refuse (1603) ; réessai en passant ALLUSERS=1 à l'installeur.
        self.assertIn('arguments + " --custom ALLUSERS=1"', self.src)
        self.assertNotIn("winget-fail.json", self.src)
        # Garde-fous : on ne ferme que sous un dossier d'application précis (jamais une racine partagée), ni soi-même.
        self.assertIn("static bool SafeAppDir(string d)", self.src)
        self.assertIn("if (proc.Id == self) continue;", self.src)

    def test_winget_fallback_mechanisms(self):
        # (a) Descripteur de repli fourni par le serveur : installeur officiel téléchargé et vérifié par signature
        #     Authenticode (l'éditeur attendu) avant d'être posé — utilisé quand le fichier du manifeste a été retiré.
        self.assertIn("cmd.TryGetValue(\"software_fallback\", out softwareFallback);", self.src)
        self.assertIn("static bool DirectInstall(string pid, Dictionary<string, object> desc", self.src)
        self.assertIn("uint WinVerifyTrust(IntPtr hwnd, ref Guid action, IntPtr data)", self.src)
        self.assertIn("PublisherTrusted(file, signer)", self.src)
        # (b) Installeur qui se termine brutalement en session 0 (0xC0000409, « exit code: 3221226505 ») : rejoué en
        #     SYSTEM dans la session console active, où il aboutit (iCUE). Sans personne de connecté, reporté.
        self.assertIn("static int? RunInActiveSession(", self.src)
        self.assertIn("uint WTSGetActiveConsoleSessionId()", self.src)
        self.assertIn("bool CreateProcessAsUser(", self.src)
        self.assertIn("output.IndexOf(\"exit code: 3221\", StringComparison.OrdinalIgnoreCase) >= 0", self.src)
        # Le code de winget est repris tel quel (ses échecs sont de grands uint, négatifs en int) : pas de test « >= 0 ».
        self.assertIn("if (sc.HasValue)", self.src)
        self.assertNotIn("sc.Value >= 0", self.src)
        # (c) Faux positif : winget signale « OK » mais la version installée ne bouge pas (l'éditeur gère ses versions,
        #     Google Play Games) → mis de côté comme déjà à jour, plus reproposé sans fin.
        self.assertIn("before == InstalledWingetVersion(pid, winget, libraries)", self.src)
        # Installeur retiré par l'éditeur (404) sans repli : mis de côté avec sa raison, pas d'échec perpétuel.
        self.assertIn("result == 0x8A150014 || result == 0x8A15002B || result == 0x80190194", self.src)


if __name__ == "__main__":
    unittest.main()
