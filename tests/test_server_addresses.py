"""Changement d'adresse du serveur : annonce aux agents, suivi des postes prévenus, bascule de l'agent Linux."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest import mock

import test_permissions
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from loco import addresses, cli, config, db

ROOT = Path(__file__).resolve().parents[1]
NEW = "https://192.168.42.230:8443"


class TestAnnouncedAddresses(FleetTest):
    profile = test_permissions.TestPermissions.profile
    browser = test_permissions.TestPermissions.browser
    cleanup_accounts = test_permissions.TestPermissions.cleanup_accounts

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_accounts)
        with db.transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (addresses.SETTING,))

    def announce(self, url, action="announce"):
        return self.admin.post("/parc/adresses", {"url": url, "action": action}, page="/parc")

    def test_announced_address_reaches_agents_and_coverage_is_counted(self):
        win, lin = Agent(WINDOWS, "192.168.43.50"), Agent(LINUX, "192.168.43.60")
        win.enroll()
        lin.enroll()
        self.assertEqual(win.checkin().get_json()["servers"], [config.HTTPS_URL])
        self.announce(NEW + "/")                                   # barre finale normalisée
        win.ident["servers"] = [config.HTTPS_URL, NEW]
        self.assertEqual(win.checkin().get_json()["servers"], [config.HTTPS_URL, NEW])
        self.assertEqual(device(win.uid)["servers"], f"{config.HTTPS_URL} {NEW}")
        lin.checkin()                                               # agent ancien : ne déclare rien
        with db.transaction() as conn:
            self.assertEqual(addresses.coverage(conn), [(config.HTTPS_URL, 1, 2), (NEW, 1, 2)])
        page = self.admin.c.get("/parc").get_data(as_text=True)
        self.assertIn(NEW, page)
        self.assertIn("1/2 poste(s) prévenu(s)", page)
        self.announce(NEW, "forget")
        self.assertEqual(win.checkin().get_json()["servers"], [config.HTTPS_URL])
        with db.transaction() as conn:
            actions = [r[0] for r in conn.execute("SELECT action FROM audit WHERE action LIKE 'parc.adresse%' ORDER BY id")]
        self.assertEqual(actions, ["parc.adresse-annoncee", "parc.adresse-retiree"])

    def test_invalid_or_hostile_addresses_are_refused(self):
        for url in ("http://192.168.42.230:8443", "https://loco.lan/chemin", "https://a b", "javascript:alert(1)",
                    "https://loco.lan:99999", "https://user@loco.lan"):
            with self.subTest(url=url):
                self.announce(url)
                with db.transaction() as conn:
                    self.assertEqual(addresses.announced(conn), [])
        self.announce("https://[fd00::10]:8443")
        self.announce("https://loco.example.org")
        with db.transaction() as conn:
            self.assertEqual(addresses.announced(conn), ["https://[fd00::10]:8443", "https://loco.example.org"])
            # La liste déclarée par un agent est bornée et filtrée.
            self.assertEqual(addresses.reported([NEW, NEW, "http://x", 3, "https://loco.lan"]), f"{NEW} https://loco.lan")

    def test_current_address_is_never_duplicated_and_the_list_is_bounded(self):
        self.announce(config.HTTPS_URL)
        for i in range(addresses.MAX_ANNOUNCED + 1):
            self.announce(f"https://10.0.0.{i + 1}:8443")
        with db.transaction() as conn:
            self.assertEqual(len(addresses.announced(conn)), addresses.MAX_ANNOUNCED)
            self.assertNotIn(config.HTTPS_URL, addresses.announced(conn))

    def test_only_fleet_settings_can_announce(self):
        viewer = self.browser("adresse-lecture", self.profile("adresse-lecture", {"fleet.view"}))
        r = viewer.post("/parc/adresses", {"url": NEW}, page="/compte")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("Annoncer aux agents", viewer.c.get("/parc").get_data(as_text=True))

    def test_console_command(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            cli.fleet_command(["announce", NEW])
        self.assertIn(f"{NEW}", out.getvalue())
        self.assertIn("annoncée", out.getvalue())
        with mock.patch("sys.stdout", io.StringIO()):
            cli.fleet_command(["forget", NEW])
        with db.transaction() as conn:
            self.assertEqual(addresses.announced(conn), [])
        with self.assertRaises(SystemExit):
            cli.fleet_command(["announce", "http://non-chiffre"])


@unittest.skipUnless(os.name == "posix", "Agent Linux")
class TestLinuxAgentFailover(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_failover", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.agent.CONFIG = os.path.join(self.work.name, "client.json")
        self.agent.log = lambda _text: None
        self.conf = {"server": "https://192.168.42.65:8443", "uid": "u", "token": "t",
                     "servers": ["https://192.168.42.65:8443", "https://192.168.42.208:8443"]}

    def test_unreachable_address_falls_back_to_the_announced_one(self):
        tried = []

        def call(server, path, data, token=None, timeout=60):
            tried.append(server)
            if server.endswith(".65:8443"):
                raise urllib.error.URLError(OSError(113, "No route to host"))
            return {"commands": [], "servers": ["https://192.168.42.208:8443"]}
        with mock.patch.object(self.agent, "call", call):
            answer = self.agent.checkin(self.conf, {})
        self.assertEqual(tried, ["https://192.168.42.65:8443", "https://192.168.42.208:8443"])
        self.assertEqual(answer["servers"], ["https://192.168.42.208:8443"])
        saved = json.loads(Path(self.agent.CONFIG).read_text())
        self.assertEqual((saved["server"], saved["servers"]), ("https://192.168.42.208:8443", ["https://192.168.42.208:8443"]))

    def test_certificate_from_another_authority_is_not_accepted(self):
        import ssl

        def call(server, path, data, token=None, timeout=60):
            raise ssl.SSLCertVerificationError("autorité inconnue")
        with mock.patch.object(self.agent, "call", call), self.assertRaises(ssl.SSLError):
            self.agent.checkin(self.conf, {})
        self.assertFalse(Path(self.agent.CONFIG).exists())          # rien n'est adopté

    def test_http_answers_do_not_trigger_a_switch(self):
        tried = []

        def call(server, path, data, token=None, timeout=60):
            tried.append(server)
            raise urllib.error.HTTPError(server, 401, "Unauthorized", {}, None)
        with mock.patch.object(self.agent, "call", call), self.assertRaises(urllib.error.HTTPError):
            self.agent.checkin(self.conf, {})
        self.assertEqual(tried, ["https://192.168.42.65:8443"])

    def test_announced_list_is_learned_and_reported(self):
        sent = {}

        def call(server, path, data, token=None, timeout=60):
            sent.update(data)
            return {"commands": [], "servers": ["https://192.168.42.65:8443", "https://loco.lan:8443/", "http://piege"]}
        conf = {"server": "https://192.168.42.65:8443", "uid": "u", "token": "t"}
        with mock.patch.object(self.agent, "call", call):
            self.agent.checkin(conf, {})
        self.assertEqual(sent["servers"], ["https://192.168.42.65:8443"])
        self.assertEqual(json.loads(Path(self.agent.CONFIG).read_text())["servers"],
                         ["https://192.168.42.65:8443", "https://loco.lan:8443"])


if __name__ == "__main__":
    unittest.main()
