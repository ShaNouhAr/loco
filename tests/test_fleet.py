"""Parc de postes : inscription de l'agent, identité indépendante de l'IP, voyants, actions, catalogue winget."""

import base64
import hashlib
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
        self.admin.post("/parc/cle", page="/parc")
        self.assertNotEqual(Agent.key(), old_key)
        self.assertEqual(a.checkin().status_code, 200)
        self.assertEqual(Agent(LINUX, "192.168.43.61").enroll(key=old_key).status_code, 403)


class TestStatus(FleetTest):
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
        self.admin.post("/parc/seuil", {"threshold": "5"}, page="/parc")
        for count, color in ((4, "orange"), (5, "red")):
            a.checkin({"updates": [{"name": f"pkg{i}"} for i in range(count)]})
            self.assertEqual(status_of(a.uid)[0], color)
        self.admin.post("/parc/seuil", {"threshold": "0"}, page="/parc")
        with fixture.loco.app.test_request_context():
            self.assertEqual(fleet.red_threshold(), 5)
        self.assertIn("5 mises à jour ou plus", self.admin.c.get("/parc").get_data(as_text=True))

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
        return self.admin.post(f"/parc/{dev['id']}/action", dict(form, action=action), page=f"/parc/{dev['id']}")

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
        page = self.admin.c.get(f"/parc/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn("OK : Notepad++ 8.9.8", page)

    def test_update_all_only_targets_devices_with_pending_updates(self):
        late = Agent(LINUX, "192.168.43.60")
        late.enroll()
        late.checkin({"updates": [{"name": "curl", "version": "8.14"}]})
        fine = Agent(WINDOWS, "192.168.43.50")
        fine.enroll()
        fine.checkin({"updates": [], "apps": []})
        self.admin.post("/parc/tout-mettre-a-jour", {}, page="/parc")
        self.assertEqual([c["action"] for c in late.checkin().get_json()["commands"]], ["update"])
        self.assertEqual(fine.checkin().get_json()["commands"], [])

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
        self.assertNotIn("srv-web", self.admin.c.get("/parc").get_data(as_text=True))

    def test_viewers_see_the_fleet_but_cannot_act(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        with transaction() as conn:
            from loco import auth
            if not conn.execute("SELECT 1 FROM users WHERE username='lecteur-parc'").fetchone():
                auth.create_user(conn, "lecteur-parc", "Lecteur-Parc-2026", "viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("lecteur-parc", "Lecteur-Parc-2026")
        page = viewer.c.get("/parc").get_data(as_text=True)
        self.assertIn("srv-web", page)
        self.assertNotIn("key=", page)                       # pas de clé d'inscription en lecture seule
        dev = device(a.uid)
        r = viewer.post(f"/parc/{dev['id']}/action", {"action": "reboot"}, page="/parc")
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

    def test_enroll_dialog_offers_the_msi_with_its_install_command(self):
        sha = "e" * 64
        with mock.patch.object(fleet, "agent_msi", return_value=("/nulle/part.msi", sha)), \
                mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
            page = self.admin.c.get("/parc").get_data(as_text=True)
        self.assertIn("Télécharger le MSI", page)
        self.assertIn('href="/agent/files/loco-client.msi" download', page)
        self.assertIn("https://repo.test:8443/agent/files/loco-client.msi", page)   # depuis le poste, en HTTPS
        self.assertIn("http://repo.test:8088/agent/files/loco-client.msi", page)    # ou en HTTP
        self.assertIn(f"SHA-256 {sha[:12]}", page)
        command = (f'msiexec /i &#34;%USERPROFILE%\\Downloads\\loco-client.msi&#34; SERVER=https://repo.test:8443 '
                   f'KEY={Agent.key()} CA_SHA256={CA_SHA256} /qb')
        self.assertIn(command, page)

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
            self.assertTrue(r.headers["Location"].endswith("/windows-apps/logiciel/outil"))
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)):
            page = self.admin.c.get("/windows-apps/store/Example.Tool").get_data(as_text=True)
        self.assertIn("Le catalogue local a la version 2.0.0 ; le centre de logiciels propose 2.5.0", page)
        self.assertIn("Voir dans le catalogue local", page)
        self.assertNotIn("Ajouter à loco</button>", page)

    def test_offline_store_can_read_the_last_valid_index(self):
        with mock.patch.object(winget, "INDEX", self.index), \
                mock.patch.object(winget, "refresh_index", side_effect=OSError("hors ligne")):
            self.assertEqual(winget.browse(query="Example")[1], 1)


if __name__ == "__main__":
    unittest.main()
