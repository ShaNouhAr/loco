"""Parc de postes : inscription de l'agent, identité indépendante de l'IP, voyants, actions, catalogue winget."""

import base64
import datetime
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
from loco import config, fleet, winget
from loco.db import transaction

WINDOWS = {"hostname": "pc-compta", "agent_version": config.VERSION, "addresses": ["192.168.43.50"],
           "os": {"family": "windows", "name": "Windows 11 Professionnel (10.0.26100)"}}
LINUX = {"hostname": "srv-web", "agent_version": config.VERSION, "addresses": ["192.168.43.60"],
         "os": {"family": "debian", "name": "Debian GNU/Linux 13 (trixie)"}}


class Agent:
    """Agent simulé : appels JSON de loco-client, depuis une adresse IP choisie."""

    def __init__(self, ident, ip, uid=None):
        self.c = fixture.loco.app.test_client()
        self.ident, self.ip = dict(ident), ip
        self.uid = uid or ("%032x" % abs(hash((ident["hostname"], ip))))[:32]
        self.token = None

    def post(self, path, data, token=None):
        headers = {"X-Forwarded-For": self.ip}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.c.post(path, json=data, headers=headers)

    def enroll(self, key=None):
        r = self.post("/agent/api/enroll", dict(self.ident, uid=self.uid, key=key or self.key()))
        if r.status_code == 200:
            self.token = r.get_json()["token"]
        return r

    def checkin(self, inventory=None):
        data = dict(self.ident)
        if inventory is not None:
            data["inventory"] = inventory
        return self.post("/agent/api/checkin", data, self.token)

    def result(self, cmd_id, state, output="", inventory=None):
        data = {"id": cmd_id, "state": state, "output": output, "exit_code": 0 if state == "success" else 1}
        if inventory is not None:
            data["inventory"] = inventory
        return self.post("/agent/api/result", data, self.token)

    @staticmethod
    def key():
        with fixture.loco.app.test_request_context():
            key = fleet.enroll_key()
        return key


def device(uid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone()


def status_of(uid):
    with fixture.loco.app.test_request_context():
        from loco.db import db
        return fleet.status(db().execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone())


class FleetTest(unittest.TestCase):
    def setUp(self):
        with transaction() as conn:
            conn.execute("DELETE FROM devices")
            conn.execute("DELETE FROM win_apps")
            conn.execute("DELETE FROM settings WHERE key='fleet_red_threshold'")
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)


class TestEnrollment(FleetTest):
    def test_wrong_key_is_refused(self):
        a = Agent(WINDOWS, "192.168.43.50")
        self.assertEqual(a.enroll(key="mauvaise-cle").status_code, 403)
        self.assertIsNone(device(a.uid))

    def test_device_keeps_its_identity_when_its_address_changes(self):
        a = Agent(WINDOWS, "192.168.43.50")
        self.assertEqual(a.enroll().status_code, 200)
        self.assertEqual(device(a.uid)["ip"], "192.168.43.50")
        a.ip = "10.20.0.7"                      # nouveau bail DHCP, autre réseau
        r = a.checkin()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["interval"], fleet.CHECKIN)
        self.assertEqual(device(a.uid)["ip"], "10.20.0.7")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)

    def test_tokens_are_checked_and_stored_hashed(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        self.assertNotIn(a.token.split(".", 1)[1], device(a.uid)["token_hash"])
        good = a.token
        a.token = good[:-2] + "xx"
        self.assertEqual(a.checkin().status_code, 401)
        a.token = "0" * 32 + "." + good.split(".", 1)[1]
        self.assertEqual(a.checkin().status_code, 401)
        a.token = None
        self.assertEqual(a.checkin().status_code, 401)

    def test_reinstalling_the_agent_keeps_the_device_and_rotates_the_token(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        old = a.token
        a.enroll()
        self.assertNotEqual(old, a.token)
        b = Agent(WINDOWS, "192.168.43.50", uid=a.uid)
        b.token = old
        self.assertEqual(b.checkin().status_code, 401)
        self.assertEqual(a.checkin().status_code, 200)

    def test_rotating_the_key_keeps_enrolled_devices(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        old_key = Agent.key()
        self.admin.post("/fleet/key", page="/fleet")
        self.assertNotEqual(Agent.key(), old_key)
        self.assertEqual(a.checkin().status_code, 200)
        self.assertEqual(Agent(LINUX, "192.168.43.61").enroll(key=old_key).status_code, 403)


class TestStatus(FleetTest):
    def test_presence_uses_recent_contacts_independently_of_inventory(self):
        now = datetime.datetime.now()
        for age, online in ((0, True), (120, True), (360, True), (361, False), (86400, False), (-86400, False)):
            with self.subTest(age=age), mock.patch.object(fleet, "_ago", return_value=datetime.timedelta(seconds=age)):
                # Un agent qui attend son premier inventaire ou dont le retrait est demandé peut être en ligne.
                for state in ("active", "removing"):
                    self.assertEqual(fleet.presence({"state": state, "last_seen": str(now)})["online"], online)
        for missing in ("", "invalide"):
            self.assertFalse(fleet.presence({"state": "active", "last_seen": missing})["online"])

    def test_fleet_separates_presence_from_update_volume_and_refreshes_when_offline(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [{"name": f"KB{i}"} for i in range(10)]})
        path = f"/fleet/{device(a.uid)['id']}"
        headers = {"X-Requested-With": "fetch"}
        with mock.patch.object(fleet, "_ago", return_value=datetime.timedelta(seconds=120)):
            page = self.admin.c.get("/fleet").get_data(as_text=True)
            cells = [c for c in re.findall(r'<td(?:\s[^>]*)?>(.*?)</td>', page, re.S) if 'data-select' not in c]
            self.assertIn('aria-label="En ligne"', cells[0])
            self.assertIn('class="st ok device-availability"', cells[0])
            self.assertIn('class="st err"', cells[2])
            self.assertIn('aria-label="Mises à jour : à traiter"', cells[2])
            detail = self.admin.c.get(path).get_data(as_text=True)
            self.assertIn('device-availability"', detail)
            self.assertIn('>En ligne</span>', detail)
            self.assertIn('class="device-update-state"', detail)
            self.assertIn('>À traiter</span>', detail)
            signatures = [self.admin.c.get(p, headers=headers).json["signature"] for p in ("/fleet", path)]
        # Sans nouvel événement ni nouvel inventaire, le franchissement du délai doit rafraîchir les deux pages.
        with mock.patch.object(fleet, "_ago", return_value=datetime.timedelta(seconds=361)):
            page = self.admin.c.get("/fleet").get_data(as_text=True)
            cells = [c for c in re.findall(r'<td(?:\s[^>]*)?>(.*?)</td>', page, re.S) if 'data-select' not in c]
            self.assertIn('aria-label="Hors ligne"', cells[0])
            self.assertIn('class="st err device-availability"', cells[0])
            self.assertIn('aria-label="Mises à jour : à traiter"', cells[2])
            for p, previous in zip(("/fleet", path), signatures):
                self.assertNotEqual(self.admin.c.get(p, headers=headers).json["signature"], previous)
        a.checkin({"updates": []})
        with mock.patch.object(fleet, "_ago", return_value=datetime.timedelta(seconds=361)):
            cells = [c for c in re.findall(r'<td(?:\s[^>]*)?>(.*?)</td>', self.admin.c.get("/fleet").get_data(as_text=True), re.S) if 'data-select' not in c]
            self.assertIn('aria-label="Hors ligne"', cells[0])
            self.assertIn('aria-label="Mises à jour : à jour"', cells[2])
            self.assertIn('class="st ok"', cells[2])

    def test_indicator_matches_the_fleet_and_marks_unknown_inventory(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        for inv, color in (({"updates": []}, "green"),
                           ({"updates": [{"name": "Correctif", "security": True}]}, "orange"),
                           ({"updates": [{"name": f"KB{i}"} for i in range(10)]}, "red"),
                           ({"updates": [], "error": "Windows Update indisponible"}, "grey")):
            r = a.checkin(inv).get_json()
            self.assertEqual(r["indicator"]["color"], color)
            self.assertEqual((r["indicator"]["color"], r["indicator"]["reason"]), status_of(a.uid))
        self.assertIn("inventaire incomplet", r["indicator"]["reason"])

    def test_lights_follow_updates_security_failures_and_contact(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        self.assertEqual(status_of(a.uid)[0], "grey")                   # premier inventaire attendu
        a.checkin({"updates": [], "reboot_required": False})
        self.assertEqual(status_of(a.uid), ("green", "à jour"))
        a.checkin({"updates": [{"name": "curl", "version": "8.14", "security": False}]})
        self.assertEqual(status_of(a.uid)[0], "orange")
        a.checkin({"updates": [{"name": "openssl", "version": "3.5", "security": True}]})
        color, reason = status_of(a.uid)
        self.assertEqual(color, "orange")
        self.assertIn("sécurité", reason)
        a.checkin({"updates": [], "reboot_required": True})
        self.assertEqual(status_of(a.uid), ("green", "redémarrage nécessaire"))
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen='2020-01-01 00:00:00' WHERE uid=?", (a.uid,))
        self.assertEqual(status_of(a.uid)[0], "grey")
        metrics = self.admin.c.get("/metrics").get_data(as_text=True)
        self.assertIn('loco_devices{status="grey"} 1', metrics)
        self.assertIn('loco_devices{status="green"} 0', metrics)

    def test_outdated_catalog_software_turns_windows_devices_orange(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('7-zip', '7-Zip', '26.04', 'msi', '/qn', '7z.msi', ?, 'ready', '')", ("a" * 64,))
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [], "apps": [{"name": "7-Zip 24.09 (x64 edition)", "version": "24.09.00.0"}]})
        color, reason = status_of(a.uid)
        self.assertEqual((color, reason), ("orange", "1 logiciel(s) à mettre à jour"))
        a.checkin({"updates": [], "apps": [{"name": "7-Zip 26.04 (x64 edition)", "version": "26.04.00.0"}]})
        self.assertEqual(status_of(a.uid)[0], "green")
        # La sécurité, les logiciels et le redémarrage restent détaillés ; seul le volume décide de la couleur.
        a.checkin({"updates": [{"name": "KB1", "security": True}, {"name": "KB2", "security": False}],
                   "apps": [{"name": "7-Zip 24.09 (x64 edition)", "version": "24.09.00.0"}], "reboot_required": True})
        self.assertEqual(status_of(a.uid), ("orange", "1 mise(s) à jour de sécurité en attente, 1 autre(s) mise(s) à jour "
                                                   "du système, 1 logiciel(s) à mettre à jour, redémarrage nécessaire"))

    def test_volume_boundaries_and_configurable_red_threshold(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        for count, color in ((0, "green"), (1, "orange"), (9, "orange"), (10, "red"), (11, "red")):
            answer = a.checkin({"updates": [{"name": f"pkg{i}"} for i in range(count)]}).get_json()
            self.assertEqual(answer["indicator"]["color"], color, count)
        self.admin.post("/fleet/threshold", {"threshold": "5"}, page="/fleet")
        for count, color in ((4, "orange"), (5, "red")):
            a.checkin({"updates": [{"name": f"pkg{i}"} for i in range(count)]})
            self.assertEqual(status_of(a.uid)[0], color)
        self.admin.post("/fleet/threshold", {"threshold": "0"}, page="/fleet")
        with fixture.loco.app.test_request_context():
            self.assertEqual(fleet.red_threshold(), 5)
        self.assertIn("5 mises à jour ou plus", self.admin.c.get("/fleet").get_data(as_text=True))

    def test_software_and_system_updates_are_added_together(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('7-zip', '7-Zip', '26.04', 'msi', '/qn', '7z.msi', ?, 'ready', '')", ("a" * 64,))
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        for system, color in ((8, "orange"), (9, "red")):
            a.checkin({"updates": [{"name": f"KB{i}", "security": True} for i in range(system)],
                       "apps": [{"name": "7-Zip 24.09 (x64 edition)", "version": "24.09.00.0"}]})
            self.assertEqual(status_of(a.uid)[0], color)

    def test_version_comparison_and_name_matching(self):
        self.assertLess(fleet.version_key("24.09"), fleet.version_key("24.10"))
        self.assertEqual(fleet.version_key("1.2"), fleet.version_key("1.2.0.0"))
        self.assertTrue(fleet.matches("7-Zip", "7-Zip 26.04 (x64 edition)"))
        self.assertTrue(fleet.matches("Notepad++", "Notepad++ (64-bit x64)"))
        self.assertTrue(fleet.matches("Mozilla Firefox", "Mozilla Firefox (x64 fr)"))
        self.assertTrue(fleet.matches("Git", "Git"))
        self.assertFalse(fleet.matches("Git", "Git Extensions"))
        self.assertFalse(fleet.matches("VLC", "VLC media player"))
        self.assertTrue(fleet.matches("VLC*", "VLC media player"))


class TestActions(FleetTest):
    def add_app(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('notepad', 'Notepad++', '8.9.8', 'exe', '/S', 'npp setup.exe', ?, 'ready', '')",
                         ("b" * 64,))

    def queue(self, uid, action, **form):
        dev = device(uid)
        return self.admin.post(f"/fleet/{dev['id']}/action", dict(form, action=action), page=f"/fleet/{dev['id']}")

    def test_command_reports_keep_a_busy_device_online(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.checkin({"updates": []})
        self.queue(a.uid, "scan")
        command = a.checkin().json["commands"][0]
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen='2020-01-01 00:00:00' WHERE uid=?", (a.uid,))
        self.assertEqual(a.result(command["id"], "running").status_code, 200)
        self.assertTrue(fleet.presence(device(a.uid))["online"])

    def test_update_reaches_the_device_with_catalog_files_and_reports_back(self):
        self.add_app()
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [{"name": "KB5070000", "security": True}],
                   "apps": [{"name": "Notepad++ (64-bit x64)", "version": "8.6"}]})
        self.queue(a.uid, "update", reboot="1")
        self.queue(a.uid, "update", reboot="1")             # pas de doublon
        cmds = a.checkin().get_json()["commands"]
        self.assertEqual(len(cmds), 1)
        cmd = cmds[0]
        self.assertEqual((cmd["action"], cmd["reboot"]), ("update", True))
        self.assertEqual(cmd["apps"][0]["url"], "/win/files/notepad/npp%20setup.exe")
        self.assertEqual(cmd["apps"][0]["sha256"], "b" * 64)
        self.assertEqual(a.checkin().get_json()["commands"], [])            # transmise une seule fois
        a.result(cmd["id"], "running")
        a.result(cmd["id"], "success", "OK : Notepad++ 8.9.8",
                 {"updates": [], "apps": [{"name": "Notepad++ (64-bit x64)", "version": "8.9.8"}]})
        self.assertEqual(status_of(a.uid)[0], "green")
        page = self.admin.c.get(f"/fleet/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn("OK : Notepad++ 8.9.8", page)

    def test_update_all_only_targets_devices_with_pending_updates(self):
        late = Agent(LINUX, "192.168.43.60")
        late.enroll()
        late.checkin({"updates": [{"name": "curl", "version": "8.14"}]})
        fine = Agent(WINDOWS, "192.168.43.50")
        fine.enroll()
        fine.checkin({"updates": [], "apps": []})
        self.admin.post("/fleet/update-all", {}, page="/fleet")
        self.assertEqual([c["action"] for c in late.checkin().get_json()["commands"]], ["update"])
        self.assertEqual(fine.checkin().get_json()["commands"], [])

    def test_update_all_dialog_holds_the_restart_option(self):
        late = Agent(dict(LINUX, hostname="maj-dialogue"), "192.168.43.61")
        late.enroll()
        late.checkin({"updates": [{"name": "curl", "version": "8.14"}]})
        page = self.admin.c.get("/fleet").get_data(as_text=True)
        start = page.index('<div class="actions">')
        head = page[start:page.index("</div>", start)]
        self.assertIn('data-open="#update-all"', head)                          # en-tête : un seul bouton
        self.assertNotIn('name="reboot"', head)                                  # plus de case flottante
        dialog = page[page.index('<dialog id="update-all"'):]
        self.assertIn('name="reboot" value="1" role="switch"', dialog)          # l'option est dans la fenêtre
        self.admin.post("/fleet/update-all", {"reboot": "1"}, page="/fleet")
        cmd = late.checkin().get_json()["commands"][0]
        self.assertEqual((cmd["action"], cmd.get("reboot")), ("update", True))

    def test_install_app_requires_a_ready_catalog_entry(self):
        self.add_app()
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        self.assertEqual(self.queue(a.uid, "install-app", slug="absent").status_code, 400)
        self.queue(a.uid, "install-app", slug="notepad")
        cmd = a.checkin().get_json()["commands"][0]
        self.assertEqual((cmd["action"], cmd["apps"][0]["slug"]), ("install-app", "notepad"))
        linux = Agent(LINUX, "192.168.43.60")
        linux.enroll()
        self.assertEqual(self.queue(linux.uid, "install-app", slug="notepad").status_code, 400)

    def test_uninstall_detaches_the_device_only_when_pushed(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.ip = "192.168.43.99"
        self.assertEqual(a.checkin().status_code, 200)       # changer d'adresse ne détache pas
        self.queue(a.uid, "uninstall")
        self.assertEqual(device(a.uid)["state"], "removing")
        self.assertEqual(self.queue(a.uid, "update").status_code, 400)
        cmd = a.checkin().get_json()["commands"][0]
        self.assertEqual(cmd["action"], "uninstall")
        a.result(cmd["id"], "success", "Agent retiré du poste.")
        self.assertEqual(device(a.uid)["state"], "removed")
        self.assertEqual(a.checkin().status_code, 401)
        self.assertNotIn("srv-web", self.admin.c.get("/fleet").get_data(as_text=True))

    def test_viewers_see_the_fleet_but_cannot_act(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        with transaction() as conn:
            from loco import auth
            if not conn.execute("SELECT 1 FROM users WHERE username='lecteur-parc'").fetchone():
                auth.create_user(conn, "lecteur-parc", "Lecteur-Parc-2026", "viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("lecteur-parc", "Lecteur-Parc-2026")
        page = viewer.c.get("/fleet").get_data(as_text=True)
        self.assertIn("srv-web", page)
        self.assertNotIn("key=", page)                       # pas de clé d'inscription en lecture seule
        dev = device(a.uid)
        r = viewer.post(f"/fleet/{dev['id']}/action", {"action": "reboot"}, page="/fleet")
        self.assertEqual(r.status_code, 403)


class TestInstallers(FleetTest):
    def test_linux_installer_needs_the_key_and_embeds_agent_and_authority(self):
        self.assertEqual(self.admin.c.get("/agent/linux.sh?key=faux").status_code, 403)
        key = Agent.key()
        sh = self.admin.c.get(f"/agent/linux.sh?key={key}").get_data(as_text=True)
        self.assertIn(f"--key '{key}'", sh)
        agent = re.search(r"<<'LOCO_CLIENT_EOF'\n(.*)\nLOCO_CLIENT_EOF", sh, re.S).group(1)
        compile(agent, "loco-client.py", "exec")
        self.assertIn(f'VERSION = "{config.VERSION}"', agent)
        self.assertEqual(self.admin.c.get("/agent/files/loco-client.py").status_code, 200)
        self.assertEqual(self.admin.c.get("/agent/files/../loco/config.py").status_code, 404)

    def test_windows_installer_checks_the_msi_and_pins_the_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "loco-client.msi")
            with open(msi, "wb") as f:
                f.write(b"MSI de test")
            sha = hashlib.sha256(b"MSI de test").hexdigest()
            with mock.patch.object(fleet, "agent_msi", return_value=(msi, sha)), \
                    mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
                self.assertEqual(self.admin.c.get("/agent/windows.ps1?key=faux").status_code, 403)
                ps1 = self.admin.c.get(f"/agent/windows.ps1?key={Agent.key()}").get_data(as_text=True)
                self.assertIn("\r\n", ps1)
                self.assertTrue(ps1.isascii())
                self.assertIn(sha, ps1)
                self.assertIn(f'CA_SHA256="{CA_SHA256}"', ps1)
                self.assertIn("/agent/files/loco-client.msi", ps1)
                r = self.admin.c.get("/agent/files/loco-client.msi")
                self.assertEqual(r.get_data(), b"MSI de test")
                r.close()
                # L'agent installé reçoit l'empreinte du MSI de cette version pour se mettre à jour.
                a = Agent(WINDOWS, "192.168.43.50")
                a.enroll()
                self.assertEqual(a.checkin().get_json()["agent_msi"], {"version": config.VERSION, "sha256": sha})
        with mock.patch.object(fleet, "agent_msi", return_value=(None, "pas d'accès à GitHub")):
            ps1 = self.admin.c.get(f"/agent/windows.ps1?key={Agent.key()}").get_data(as_text=True)
            self.assertIn("pas d''accès à GitHub", ps1)
            # Lien direct du MSI : indisponibilité expliquée (503 + raison), pas un 404 muet.
            r = self.admin.c.get("/agent/files/loco-client.msi")
            self.assertEqual(r.status_code, 503)
            self.assertIn("pas d'accès à GitHub", r.get_data(as_text=True))
            self.assertEqual(r.headers["Retry-After"], "30")

    def test_update_offer_announces_the_fingerprint_of_the_msi_actually_served(self):
        """Régression 2.39 → 2.71 : le contact annonçait l'empreinte du MSI brut, alors que le téléchargement sert le
        MSI aux emplacements d'inscription vidés ; les agents Windows refusaient donc toute mise à jour."""
        raw = b"MSI" + b"".join(placeholder for placeholder, _ in fleet._MSI_FIELDS) + b"fin"
        with tempfile.TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "loco-client.msi")
            with open(msi, "wb") as f:
                f.write(raw)
            fleet._CANON.clear()
            self.addCleanup(fleet._CANON.clear)
            with mock.patch.object(fleet, "agent_msi", return_value=(msi, hashlib.sha256(raw).hexdigest())):
                a = Agent(dict(WINDOWS, hostname="pc-maj", agent_version="2.38.0"), "192.168.43.55")
                a.enroll()
                offer = a.checkin().get_json()["agent_msi"]
                r = self.admin.c.get("/agent/files/loco-client.msi")
                served = r.get_data()
                r.close()
        self.assertNotEqual(served, raw)                                          # emplacements vidés au service
        self.assertEqual(offer["sha256"], hashlib.sha256(served).hexdigest())   # l'agent vérifie ce qu'il reçoit

    def test_enrollment_page_offers_the_msi_with_its_install_command(self):
        sha = "e" * 64
        with mock.patch.object(fleet, "agent_msi", return_value=("/nulle/part.msi", sha)), \
                mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
            page = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn("Télécharger le MSI", page)
        self.assertIn('href="/agent/windows/enroll.msi" download', page)             # MSI prêt : double-clic = inscription
        self.assertIn("https://repo.test:8443/agent/files/loco-client.msi", page)   # MSI générique depuis le poste, en HTTPS
        self.assertIn("http://repo.test:8088/agent/files/loco-client.msi", page)    # ou en HTTP
        command = (f'msiexec /i &#34;%USERPROFILE%\\Downloads\\loco-client.msi&#34; SERVER=https://repo.test:8443 '
                   f'KEY={Agent.key()} CA_SHA256={CA_SHA256} /qb')
        self.assertIn(command, page)

    def test_enroll_msi_bakes_server_and_key_while_generic_msi_is_blank(self):
        placeholders = (b"@LOCO_PROVISION_SERVER@" + b"." * 137, b"@LOCO_PROVISION_KEY@" + b"." * 76,
                        b"@LOCO_PROVISION_CA@" + b"." * 109)
        blob = b"MSI\x00" + placeholders[0] + b"\x01" + placeholders[1] + b"\x02" + placeholders[2] + b"end"
        with tempfile.TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "loco-client.msi")
            with open(msi, "wb") as f:
                f.write(blob)
            with mock.patch.object(fleet, "agent_msi", return_value=(msi, "sha")), \
                    mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
                fleet._CANON.clear()
                generic = self.admin.c.get("/agent/files/loco-client.msi").get_data()
                enroll = self.admin.c.get("/agent/windows/enroll.msi").get_data()
        self.assertEqual(len(generic), len(blob))            # tampon à longueur constante : MSI toujours valide
        self.assertEqual(len(enroll), len(blob))
        self.assertNotIn(b"@LOCO_PROVISION", generic)
        self.assertNotIn(b"@LOCO_PROVISION", enroll)
        self.assertNotIn(Agent.key().encode(), generic)      # générique (ps1/GPO) : aucune inscription gravée
        self.assertIn(b"https://repo.test:8443", enroll)     # prêt à l'emploi : adresse gravée
        self.assertIn(Agent.key().encode(), enroll)          # et la clé d'inscription

    def test_msi_is_fetched_from_the_release_and_checked(self):
        data = b"MSI publie"
        name = f"loco-client-{config.VERSION}.msi"
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "agents", name)
            def release(url, limit):
                if url.endswith("SHA256SUMS"):
                    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()
                return served[0]
            with mock.patch.object(fleet, "AGENT_DIR", tmp), mock.patch.object(fleet, "_msi_cache", return_value=cache), \
                    mock.patch.object(fleet, "_get_url", side_effect=release):
                served = [b"MSI altere"]
                self.assertEqual(fleet.agent_msi(fetch=True), (None, "empreinte SHA-256 du MSI incorrecte"))
                self.assertFalse(os.path.exists(cache))
                served = [data]
                self.assertEqual(fleet.agent_msi(fetch=True), (cache, hashlib.sha256(data).hexdigest()))
                with open(cache, "rb") as f:
                    self.assertEqual(f.read(), data)
                self.assertEqual(os.listdir(os.path.dirname(cache)), [name])     # aucun fichier temporaire restant

    def test_outdated_windows_agents_make_the_server_fetch_its_msi(self):
        """Après la mise à jour du serveur, le MSI de sa version est récupéré pour les agents Windows en retard."""
        old = dict(WINDOWS, agent_version="2.9.0")
        a = Agent(old, "192.168.43.50")
        a.enroll()
        fleet._PREFETCH["last"] = 0.0
        with mock.patch.object(fleet, "agent_msi", return_value=(None, "absent")), \
                mock.patch.object(fleet.threading, "Thread") as thread:
            self.assertNotIn("agent_msi", a.checkin().get_json())
            a.checkin()                                                     # moins de dix minutes : pas de relance
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["kwargs"], {"fetch": True})
            fleet._PREFETCH["last"] = 0.0
            current = Agent(WINDOWS, "192.168.43.51")                       # agent à jour : rien à récupérer
            current.enroll()
            current.checkin()
            thread.assert_called_once()


# Autorité de test (certificat auto-signé quelconque) et son empreinte SHA-256 (DER).
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBejCCASCgAwIBAgIUDEWTWZCXVQfYC6gXQbFNA40OJSkwCgYIKoZIzj0EAwIw
EjEQMA4GA1UEAwwHbG9jby1jYTAeFw0yNjEwMTYwMDAwMDBaFw0zNjEwMTMwMDAw
MDBaMBIxEDAOBgNVBAMMB2xvY28tY2EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAQpUqNvLkC9vR3vMV1nH3eK/Kq1Fwq4hb1dS9SbLqRn2v2tE6l3ZpE4VQbCm1Eo
d4tRQYhB0e3m1v0x2s2Xo7mDo1MwUTAdBgNVHQ4EFgQU2t3jCw4i4sUhw9WvYb8z
-----END CERTIFICATE-----"""
CA_SHA256 = hashlib.sha256(base64.b64decode("".join(CA_PEM.splitlines()[1:-1]))).hexdigest()


MANIFEST = """PackageIdentifier: Example.Tool
PackageVersion: 2.5.0
PackageName: Example Tool (fr)
Publisher: "Example \\xA9 Corp"
ShortDescription: Un outil
  sur deux lignes
Tags:
- outil
- exemple
Installers:
- Architecture: x86
  InstallerType: nullsoft
  InstallerUrl: https://example.com/tool-x86.exe
  InstallerSha256: 1111111111111111111111111111111111111111111111111111111111111111
- Architecture: x64
  InstallerType: inno
  Scope: machine
  InstallerUrl: https://example.com/tool%20setup-x64.exe
  InstallerSha256: 2222222222222222222222222222222222222222222222222222222222222222
  InstallerSwitches:
    Custom: /MERGETASKS=!desktopicon
- Architecture: arm64
  InstallerType: wix
  InstallerUrl: https://example.com/tool-arm64.msi
  InstallerSha256: 3333333333333333333333333333333333333333333333333333333333333333
ManifestType: merged
"""


class TestWinget(FleetTest):
    def test_verified_logos_are_cached_and_corrupt_images_are_rejected(self):
        image = b"\x89PNG\r\n\x1a\n" + b"test image"
        sha = hashlib.sha256(image).hexdigest()
        doc = {"Icons": [{"IconFileType": "png", "IconSha256": sha,
                          "IconUrl": f"https://cdn.winget.microsoft.com/icons/{sha}.png"}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(winget, "INDEX_DIR", tmp), \
                mock.patch.object(winget, "manifest", return_value=doc) as manifest, \
                mock.patch.object(winget, "_fetch", return_value=image) as fetch:
            r = self.admin.c.get("/windows-apps/icons/Example.Tool")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, "image/png")
            self.assertEqual(r.get_data(), image)
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            r.close()
            r = self.admin.c.get("/windows-apps/icons/Example.Tool")
            self.assertEqual(r.get_data(), image)
            r.close()
            self.assertEqual((manifest.call_count, fetch.call_count), (1, 1))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(winget, "INDEX_DIR", tmp), \
                mock.patch.object(winget, "manifest", return_value=doc), \
                mock.patch.object(winget, "_fetch", return_value=b"<html>not an icon</html>"):
            self.assertEqual(self.admin.c.get("/windows-apps/icons/Example.Tool").status_code, 204)

    def test_missing_logo_is_cached_and_a_letter_is_kept_in_the_page(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(winget, "INDEX_DIR", tmp), \
                mock.patch.object(winget, "manifest", return_value={}) as manifest:
            for _ in range(2):
                self.assertEqual(self.admin.c.get("/windows-apps/icons/Example.Tool").status_code, 204)
            self.assertEqual(manifest.call_count, 1)
        with mock.patch.object(winget, "browse", return_value=([{"id": "Example.Tool", "name": "Example", "version": "1"}], 1, 1, 1)):
            page = self.admin.c.get("/windows-apps/store").get_data(as_text=True)
        self.assertIn('class="app-logo-initial">E</span>', page)
        self.assertIn('/windows-apps/icons/Example.Tool', page)

    def test_manifest_subset_is_parsed(self):
        doc = winget.parse_yaml(MANIFEST)
        self.assertEqual(doc["Publisher"], "Example © Corp")
        self.assertEqual(doc["ShortDescription"], "Un outil sur deux lignes")
        self.assertEqual(doc["Tags"], ["outil", "exemple"])
        self.assertEqual(doc["Installers"][1]["InstallerSwitches"], {"Custom": "/MERGETASKS=!desktopicon"})

    def test_installer_choice_prefers_x64_machine_and_silent_options(self):
        inst = winget.choose_installer(winget.parse_yaml(MANIFEST))
        self.assertEqual(inst["url"], "https://example.com/tool%20setup-x64.exe")
        self.assertEqual(inst["kind"], "exe")
        self.assertEqual(inst["args"], "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /ALLUSERS /MERGETASKS=!desktopicon")
        no_silent = MANIFEST.replace("InstallerType: inno", "InstallerType: exe").replace("InstallerType: nullsoft", "InstallerType: zip")
        with self.assertRaises(ValueError):
            winget.choose_installer(winget.parse_yaml(no_silent))

    def test_import_creates_a_detectable_entry_and_checks_the_installer_hash(self):
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)), \
                mock.patch("loco.winapps.start_fetch") as fetch:
            r = self.admin.post("/windows-apps/winget/add", {"id": "Example.Tool"}, page="/windows-apps")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM win_apps WHERE winget_id='Example.Tool'").fetchone()
        self.assertEqual((row["name"], row["version"], row["detect"], row["kind"]),
                         ("Example Tool (fr)", "2.5.0", "Example Tool", "exe"))
        kwargs = fetch.call_args.kwargs
        self.assertEqual(kwargs["expected_sha256"], "2" * 64)
        self.assertEqual(kwargs["filename"], "tool_setup-x64.exe")

    def test_reimport_does_not_duplicate_or_download_again(self):
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)), \
                mock.patch("loco.winapps.start_fetch") as fetch:
            for _ in range(2):
                self.admin.post("/windows-apps/winget/add", {"id": "Example.Tool"}, page="/windows-apps")
            self.assertEqual(fetch.call_count, 1)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM win_apps WHERE winget_id='Example.Tool'").fetchone()[0], 1)


class TestSoftwareStore(FleetTest):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.index = os.path.join(tmp.name, "index.db")
        with sqlite3.connect(self.index) as conn:
            conn.executescript("CREATE TABLE ids (id TEXT); CREATE TABLE names (name TEXT); "
                               "CREATE TABLE versions (version TEXT); CREATE TABLE monikers (moniker TEXT); "
                               "CREATE TABLE manifest (id INTEGER, name INTEGER, version INTEGER, moniker INTEGER, "
                               "pathpart INTEGER, hash BLOB);")
            for i, package in enumerate(["7zip.7zip", "Example.Tool"] + [f"Vendor.App{n:02}" for n in range(25)], 1):
                conn.execute("INSERT INTO ids VALUES (?)", (package,))
                for version in ("2.9", "2.10"):
                    n = conn.execute("INSERT INTO names VALUES (?)", (package + " " + version,)).lastrowid
                    v = conn.execute("INSERT INTO versions VALUES (?)", (version,)).lastrowid
                    m = conn.execute("INSERT INTO monikers VALUES (?)", (package,)).lastrowid
                    conn.execute("INSERT INTO manifest VALUES (?,?,?,?,0,NULL)", (i, n, v, m))
        patch = mock.patch.object(winget, "refresh_index", return_value=self.index)
        patch.start()
        self.addCleanup(patch.stop)

    def test_browsing_pages_by_application_and_chooses_the_latest_version(self):
        results, total, page, pages = winget.browse(size=24)
        self.assertEqual((len(results), total, page, pages), (24, 27, 1, 2))
        self.assertTrue(all(r["version"] == "2.10" for r in results))
        second, total, page, pages = winget.browse(page=999)
        self.assertEqual((len(second), page, pages), (3, 2, 2))
        self.assertFalse({r["id"] for r in results} & {r["id"] for r in second})
        self.assertEqual(winget.browse(category="outils")[1], 1)
        self.assertEqual(winget.browse(query="%_")[1], 0)

    def test_store_and_details_use_real_index_data_and_require_an_admin(self):
        page = self.admin.c.get("/windows-apps/store?q=Example").get_data(as_text=True)
        self.assertIn("Example.Tool", page)
        self.assertIn("2.10", page)
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)):
            page = self.admin.c.get("/windows-apps/store/Example.Tool").get_data(as_text=True)
        self.assertIn("Un outil sur deux lignes", page)
        self.assertIn("Ajouter à loco", page)
        with transaction() as conn:
            from loco import auth
            if not conn.execute("SELECT 1 FROM users WHERE username='lecteur-store'").fetchone():
                auth.create_user(conn, "lecteur-store", "Lecteur-Store-2026", "viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("lecteur-store", "Lecteur-Store-2026")
        self.assertEqual(viewer.c.get("/windows-apps/store").status_code, 403)
        self.assertEqual(viewer.c.get("/windows-apps/store/Example.Tool").status_code, 403)

    def test_store_list_adds_in_place_and_flags_known_or_outdated_software(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect, winget_id) "
                         "VALUES ('outil', 'Example Tool', '2.0.0', 'exe', '/S', 'tool.exe', ?, 'ready', '', 'Example.Tool')",
                         ("d" * 64,))
        page = self.admin.c.get("/windows-apps/store?q=Vendor.App0").get_data(as_text=True)
        self.assertIn('name="retour" value="store"', page)
        self.assertIn('<input type="hidden" name="q" value="Vendor.App0">', page)
        page = self.admin.c.get("/windows-apps/store?q=Example").get_data(as_text=True)
        self.assertIn("dans loco", page)
        self.assertNotIn('value="Example.Tool"', page)                      # déjà présent : pas de bouton Ajouter
        with transaction() as conn:
            known = conn.execute("SELECT * FROM win_apps WHERE slug='outil'").fetchone()
        with mock.patch.object(winget, "import_package", return_value=known):
            r = self.admin.post("/windows-apps/winget/add", {"id": "Example.Tool", "retour": "store", "q": "Example"},
                                page="/windows-apps/store")
            self.assertTrue(r.headers["Location"].endswith("/windows-apps/store?q=Example"))
            r = self.admin.post("/windows-apps/winget/add", {"id": "Example.Tool"}, page="/windows-apps/store")
            self.assertTrue(r.headers["Location"].endswith("/windows-apps/software/outil"))
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)):
            page = self.admin.c.get("/windows-apps/store/Example.Tool").get_data(as_text=True)
        self.assertIn("Le catalogue local a la version 2.0.0 ; le centre de logiciels propose 2.5.0", page)
        self.assertIn("Voir dans le catalogue local", page)
        self.assertNotIn("Ajouter à loco</button>", page)

    def test_offline_store_can_read_the_last_valid_index(self):
        with mock.patch.object(winget, "INDEX", self.index), \
                mock.patch.object(winget, "refresh_index", side_effect=OSError("hors ligne")):
            self.assertEqual(winget.browse(query="Example")[1], 1)


class TestLinuxInventory(FleetTest):
    """Postes Linux : paquets, applications Flatpak et caractéristiques, stockés à part et affichés sur la fiche."""

    FULL = {
        "updates": [{"name": "curl", "version": "8.14.1", "security": True}],
        "flatpak": 0,
        "packages": [
            {"name": "bash", "version": "5.2-2", "arch": "amd64", "manual": True, "size": 1843200},
            {"name": "curl", "version": "8.13", "arch": "amd64", "manual": True, "size": 500000},
            {"name": "libc6", "version": "2.37-1", "arch": "amd64", "manual": False, "size": 12288000},
        ],
        "flatpaks": [{"id": "org.mozilla.firefox", "name": "Firefox", "version": "123.0",
                      "branch": "stable", "origin": "flathub"}],
        "system": {"kernel": "6.1.0-18-amd64", "arch": "x86_64", "cpu": "Intel Xeon", "cores": 4,
                   "memory": 8 * 1024 ** 3, "memory_available": 4 * 1024 ** 3, "disk": 50 * 1024 ** 3,
                   "disk_free": 30 * 1024 ** 3, "boot": 1700000000, "virtualization": "kvm"},
    }

    def soft(self, uid):
        with transaction() as conn:
            row = conn.execute("SELECT data FROM device_software WHERE device_id=(SELECT id FROM devices WHERE uid=?)",
                               (uid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def test_packages_are_stored_apart_and_shown_on_the_device_page(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.checkin(self.FULL)
        data = self.soft(a.uid)
        self.assertEqual(len(data["packages"]), 3)
        self.assertEqual(data["system"]["virtualization"], "kvm")
        self.assertNotIn("packages", json.loads(device(a.uid)["inventory"]))     # inventaire du parc : sans les paquets
        page = self.admin.c.get(f"/fleet/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn('data-tab="paquets"', page)
        self.assertIn('data-tab="flatpak"', page)
        self.assertIn("libc6", page)
        self.assertIn("Firefox", page)
        self.assertIn("Intel Xeon", page)
        self.assertIn("dépendance", page)                                        # libc6 : pas installé volontairement
        self.assertIn("→ 8.14.1", page)                                          # curl : version candidate en vis-à-vis
        self.assertIn('data-update="1"', page)

    def test_windows_devices_have_no_package_tab(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [], "apps": [{"name": "7-Zip", "version": "24.09"}]})
        self.assertIsNone(self.soft(a.uid))
        page = self.admin.c.get(f"/fleet/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertNotIn('data-tab="paquets"', page)

    def test_windows_driver_updates_are_kept_and_tagged(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [
            {"name": "2024-05 Cumulative Update", "version": "KB5037771", "security": True},
            {"name": "NVIDIA Corporation - Display - 31.0.15.5222", "version": "", "driver": True},
        ]})
        inv = json.loads(device(a.uid)["inventory"])
        driver = next(u for u in inv["updates"] if "NVIDIA" in u["name"])
        self.assertTrue(driver["driver"])
        self.assertFalse(driver["security"])
        page = self.admin.c.get(f"/fleet/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn("NVIDIA Corporation", page)
        self.assertIn(">pilote</span>", page)

    def test_oversized_inventory_is_capped(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.checkin({"updates": [],
                   "packages": [{"name": f"p{i}", "version": "1", "arch": "amd64", "manual": False, "size": 10}
                                for i in range(10050)],
                   "flatpaks": [{"id": f"org.x.A{i}", "name": "A", "version": "1"} for i in range(600)]})
        data = self.soft(a.uid)
        self.assertEqual(len(data["packages"]), 10000)
        self.assertEqual(len(data["flatpaks"]), 500)

    def test_a_later_scan_replaces_the_previous_inventory(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.checkin(self.FULL)
        a.checkin({"updates": [],
                   "packages": [{"name": "bash", "version": "5.2-3", "arch": "amd64", "manual": True, "size": 10}],
                   "system": {"kernel": "6.2"}})
        data = self.soft(a.uid)
        self.assertEqual(len(data["packages"]), 1)                               # remplacement, pas d'accumulation
        self.assertEqual(data["system"]["kernel"], "6.2")


class TestTrustBundle(FleetTest):
    """Faisceau d'autorités livré au check-in, seulement à l'agent qui ne l'a pas encore ; empreinte gardée par poste."""

    def setUp(self):
        super().setUp()
        os.makedirs(config.TLS_DIR, exist_ok=True)
        self.path = os.path.join(config.TLS_DIR, "trust.pem")
        with open(self.path, "w") as f:
            f.write("-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n")
        self.addCleanup(os.unlink, self.path)

    def test_bundle_is_sent_until_the_agent_reports_it(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        with open(self.path) as f:
            bundle = f.read()
        sha = hashlib.sha256(bundle.encode()).hexdigest()
        first = a.checkin().get_json()["trust"]
        self.assertEqual((first["sha256"], first["pem"]), (sha, bundle))
        later = a.post("/agent/api/checkin", dict(a.ident, trust=sha), a.token).get_json()["trust"]
        self.assertEqual(later, {"sha256": sha})                                  # déjà reçu : pas de nouvel envoi
        self.assertEqual(device(a.uid)["trust_sha256"], sha)


class TestWuProxy(FleetTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(config, "WU_PROXY", True)               # serveur Linux avec Squid installé
        patcher.start()
        self.addCleanup(patcher.stop)
        self.win = Agent(WINDOWS, "192.168.43.50"); self.win.enroll()
        self.lin = Agent(LINUX, "192.168.43.60"); self.lin.enroll()

    def test_proxy_directive_on_by_default_off_when_feature_disabled(self):
        # Par défaut la fonctionnalité « Proxy Windows Update » est active → directive poussée aux postes Windows.
        directive = self.win.checkin().get_json()["wu_proxy"]
        self.assertEqual(directive["port"], int(config.WU_PROXY_PORT))
        self.assertTrue(directive["host"])
        self.assertIn("<local>", directive["bypass"])
        self.assertNotIn("wu_proxy", self.lin.checkin().get_json())            # Linux : pas de WinHTTP
        # Fonctionnalité coupée par le compte admin → directive fausse (l'agent retire le proxy).
        from loco import features
        with transaction() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (features.SETTING, json.dumps(["wu_proxy"])))
        self.assertEqual(self.win.checkin().get_json()["wu_proxy"], False)

    def test_no_proxy_is_pushed_when_squid_is_not_installed(self):
        # Installation --no-wu ou serveur loco sous Windows : jamais de proxy inexistant envoyé aux postes.
        with mock.patch.object(config, "WU_PROXY", False):
            self.assertEqual(self.win.checkin().get_json()["wu_proxy"], False)
            wu = self.admin.c.get("/windows-update").get_data(as_text=True)
            self.assertIn("Non disponible sur ce serveur", wu)
            self.assertNotIn("netsh winhttp set proxy", wu)
            self.assertNotIn("netsh winhttp set proxy", self.admin.c.get("/enrollment").get_data(as_text=True))

    def test_squid_never_relays_to_the_server_itself(self):
        from pathlib import Path
        conf = (Path(__file__).resolve().parents[1] / "conf" / "squid-wu.conf").read_text()
        self.assertIn("127.0.0.0/8", conf)
        self.assertIn("169.254.0.0/16", conf)
        self.assertLess(conf.index("http_access deny loco_forbidden_dst"), conf.index("http_access allow loco_clients"))

    def test_pages_show_the_manual_proxy_command(self):
        target = f"{config.HOST}:{config.WU_PROXY_PORT}"
        wu = self.admin.c.get("/windows-update").get_data(as_text=True)
        self.assertIn("netsh winhttp set proxy", wu)
        self.assertIn(target, wu)
        self.assertIn("Diriger les postes vers le cache", wu)
        enroll = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn("netsh winhttp set proxy", enroll)
        self.assertIn(target, enroll)


class TestScheduling(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM schedules")
        self.a = Agent(LINUX, "192.168.43.60")
        self.a.enroll()
        self.dev = device(self.a.uid)["id"]

    def plan(self, **form):
        values = {"action": "update", "target": "selection", "device": str(self.dev), "recurrence": "once",
                  "start_date": (datetime.date.today() + datetime.timedelta(days=1)).isoformat(), "at_time": "02:00"}
        values.update(form)
        return self.admin.post("/fleet/schedules/save", values, page="/fleet/schedules")

    def due(self):
        past = (datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            conn.execute("UPDATE schedules SET next_run=? WHERE next_run!=''", (past,))

    def schedule(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM schedules ORDER BY id DESC").fetchone()

    def test_occurrences_and_wording(self):
        from loco import schedules as sc
        wednesday = datetime.datetime(2026, 10, 21, 3, 0)                   # mercredi 21 octobre 2026, 03:00
        base = {"at_time": "02:00", "start_date": "2026-10-21", "weekdays": "", "monthday": 0}
        nxt = lambda **k: sc.next_occurrence(dict(base, **k), wednesday)
        self.assertEqual(nxt(recurrence="daily"), datetime.datetime(2026, 10, 22, 2, 0))
        self.assertEqual(nxt(recurrence="weekly", weekdays="1,3"), datetime.datetime(2026, 10, 22, 2, 0))   # jeudi
        self.assertEqual(nxt(recurrence="weekly", weekdays="1"), datetime.datetime(2026, 10, 27, 2, 0))     # mardi suivant
        self.assertEqual(nxt(recurrence="monthly", monthday=31), datetime.datetime(2026, 10, 31, 2, 0))
        self.assertEqual(sc.next_occurrence(dict(base, recurrence="monthly", monthday=31), datetime.datetime(2026, 11, 1)),
                         datetime.datetime(2026, 11, 30, 2, 0))                # dernier jour d'un mois de 30 jours
        self.assertIsNone(nxt(recurrence="once"))                              # 02:00 déjà passé ce jour-là
        self.assertEqual(sc.describe(dict(base, recurrence="weekly", weekdays="1,3")), "Chaque mardi et jeudi à 02:00")
        self.assertEqual(sc.describe(dict(base, recurrence="weekly", weekdays="0,1,2,3,4")), "Du lundi au vendredi à 02:00")
        self.assertEqual(sc.describe(dict(base, recurrence="monthly", monthday=1)), "Le 1er de chaque mois à 02:00")
        self.assertEqual(sc.describe(dict(base, recurrence="monthly", monthday=31)), "Le dernier jour de chaque mois à 02:00")

    def test_one_shot_update_runs_when_due_then_finishes(self):
        self.assertEqual(self.plan().status_code, 302)
        s = self.schedule()
        self.assertEqual((s["action"], s["recurrence"]), ("update", "once"))
        self.assertTrue(s["next_run"])
        self.assertEqual(self.a.checkin().get_json()["commands"], [])            # pas avant l'heure
        self.due()
        self.assertEqual([c["action"] for c in self.a.checkin().get_json()["commands"]], ["update"])
        s = self.schedule()
        self.assertEqual((s["next_run"], s["last_result"]), ("", "1 poste(s)"))  # terminée
        self.assertIn("Terminée", self.admin.c.get("/fleet/schedules").get_data(as_text=True))

    def test_recurring_schedule_advances_without_piling_up_missed_runs(self):
        self.plan(recurrence="weekly", weekdays=["1", "3"], start_date=datetime.date.today().isoformat())
        self.due()
        self.assertEqual([c["action"] for c in self.a.checkin().get_json()["commands"]], ["update"])
        self.assertEqual(self.a.checkin().get_json()["commands"], [])            # une seule fois par occurrence
        s = self.schedule()
        self.assertGreater(s["next_run"], datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        page = self.admin.c.get(f"/fleet/{self.dev}").get_data(as_text=True)    # fiche : planifications du poste
        self.assertIn("chaque mardi et jeudi à 02:00", page)
        self.assertIn('id="plan-update"', page)

    def test_invalid_plans_create_nothing(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        self.plan(start_date=yesterday)                                        # instant passé
        self.plan(recurrence="weekly")                                         # aucun jour choisi
        self.plan(at_time="25:00")
        self.plan(target="selection", device="")                               # aucune cible
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0], 0)

    def test_pause_resume_and_delete(self):
        self.plan(recurrence="daily", start_date=datetime.date.today().isoformat())
        sid = self.schedule()["id"]
        self.admin.post(f"/fleet/schedules/{sid}/pause", {}, page="/fleet/schedules")
        self.due()
        self.assertEqual(self.a.checkin().get_json()["commands"], [])            # en pause : rien n'est envoyé
        self.admin.post(f"/fleet/schedules/{sid}/pause", {}, page="/fleet/schedules")
        s = self.schedule()
        self.assertEqual(s["enabled"], 1)
        self.assertGreater(s["next_run"], datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))   # pas de rattrapage
        self.admin.post(f"/fleet/schedules/{sid}/delete", {}, page="/fleet/schedules")
        self.assertIsNone(self.schedule())

    def test_rights_follow_the_planned_action(self):
        import test_permissions
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        viewer = test_permissions.TestPermissions.browser(
            self, "plan-lecture", test_permissions.TestPermissions.profile(self, "plan-lecture", {"fleet.view"}))
        self.assertEqual(viewer.c.get("/fleet/schedules").status_code, 200)
        self.assertEqual(viewer.post("/fleet/schedules/save", {"action": "update", "target": "all",
                                     "recurrence": "daily", "at_time": "02:00",
                                     "start_date": datetime.date.today().isoformat()}, page="/account").status_code, 403)
        single = test_permissions.TestPermissions.browser(
            self, "plan-poste", test_permissions.TestPermissions.profile(self, "plan-poste", {"fleet.update"}))
        r = single.post("/fleet/schedules/save", {"action": "update", "target": "all", "recurrence": "daily",
                        "at_time": "02:00", "start_date": datetime.date.today().isoformat()}, page="/account")
        self.assertEqual(r.status_code, 403)                                   # tout le parc : droit « tout le parc »

    def test_whoever_edits_a_schedule_answers_for_it(self):
        import test_permissions
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        self.plan(recurrence="daily", start_date=datetime.date.today().isoformat())
        sid = self.schedule()["id"]
        editor = test_permissions.TestPermissions.browser(
            self, "plan-editeur", test_permissions.TestPermissions.profile(self, "plan-editeur", {"fleet.update"}))
        r = editor.post(f"/fleet/schedules/{sid}/save", {"action": "update", "target": "selection",
                        "device": str(self.dev), "recurrence": "daily", "at_time": "03:00",
                        "start_date": datetime.date.today().isoformat()}, page="/account")
        self.assertEqual(r.status_code, 302)
        s = self.schedule()
        self.assertEqual((s["created_by"], s["at_time"]), ("perm-plan-editeur", "03:00"))   # droits revérifiés pour lui

    def test_queued_command_can_be_cancelled_before_it_is_sent(self):
        r = self.admin.post(f"/fleet/{self.dev}/action", {"action": "update"}, page=f"/fleet/{self.dev}")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            cid = conn.execute("SELECT id FROM device_commands WHERE device_id=?", (self.dev,)).fetchone()[0]
        self.assertEqual(self.admin.post(f"/fleet/{self.dev}/cancel/{cid}", {}, page=f"/fleet/{self.dev}").status_code, 302)
        self.assertEqual(self.a.checkin().get_json()["commands"], [])
        self.assertEqual(self.admin.post(f"/fleet/{self.dev}/cancel/{cid}", {}, page=f"/fleet/{self.dev}").status_code, 404)


class TestSearch(FleetTest):
    def test_devices_appear_in_the_command_palette(self):
        Agent(WINDOWS, "192.168.43.50").enroll()
        Agent(LINUX, "192.168.43.60").enroll()
        data = self.admin.c.get("/fleet/search.json").get_json()
        by_name = {d["title"]: d for d in data["devices"]}
        self.assertIn("pc-compta", by_name)
        self.assertIn("srv-web", by_name)
        self.assertIn("192.168.43.60", by_name["srv-web"]["sub"])
        self.assertRegex(by_name["srv-web"]["href"], r"^/fleet/\d+$")


class TestUpdateSelected(FleetTest):
    def test_queues_update_only_for_selected_active_devices(self):
        Agent(WINDOWS, "192.168.43.50").enroll()
        Agent(LINUX, "192.168.43.60").enroll()
        with transaction() as conn:
            ids = {r["hostname"]: r["id"] for r in conn.execute("SELECT id, hostname FROM devices")}
        r = self.admin.post("/fleet/update-selected", {"ids": [str(ids["pc-compta"])]}, page="/fleet")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["ok"], True)
        with transaction() as conn:
            queued = {row[0] for row in conn.execute("SELECT device_id FROM device_commands WHERE action='update' AND state='queued'")}
        self.assertIn(ids["pc-compta"], queued)
        self.assertNotIn(ids["srv-web"], queued)

    def test_empty_selection_is_rejected(self):
        self.assertEqual(self.admin.post("/fleet/update-selected", {}, page="/fleet").status_code, 400)


if __name__ == "__main__":
    unittest.main()
