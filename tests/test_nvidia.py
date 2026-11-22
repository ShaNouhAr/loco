"""Pilotes NVIDIA des postes Windows : lecture des réponses de NVIDIA (produits, dernier pilote), comparaison avec le
pilote installé, offre sur la fiche, demande contrôlée (version et adresse prises dans le relevé du serveur, jamais dans
le formulaire) et commande transmise à l'agent. Les services de NVIDIA sont simulés (cache pré-rempli)."""
import json
import os
import time
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import WINDOWS, Agent, FleetTest, device
from loco import config, fleet, nvidia
from loco.db import transaction

PRODUCTS = ('<?xml version="1.0"?><LookupValueSearch><LookupValues>'
            '<LookupValue ParentID="127"><Name>NVIDIA GeForce RTX 4080</Name><Value>999</Value></LookupValue>'
            '<LookupValue ParentID="120"><Name>GeForce RTX 3060</Name><Value>942</Value></LookupValue>'
            '</LookupValues></LookupValueSearch>')
DRIVER = json.dumps({"Success": "1", "IDS": [{"downloadInfo": {
    "Version": "622.15", "Name": "GeForce%20Game%20Ready%20Driver", "ReleaseDateTime": "Tue%20Dec%2001%2C%202026",
    "DownloadURL": "https%3A%2F%2Fus.download.nvidia.com%2FWindows%2F622.15%2F622.15-desktop-win10-win11-64bit-international-dch-whql.exe",
    "DownloadURLFileSize": "1.05%20GB"}}]})


class TestParsing(unittest.TestCase):
    def test_products_and_driver(self):
        products = nvidia.parse_products(PRODUCTS)
        self.assertEqual(products["geforce rtx 4080"], [127, 999])
        self.assertEqual(products["geforce rtx 3060"], [120, 942])
        found = nvidia.parse_driver(DRIVER)
        self.assertEqual((found["version"], found["size"], found["date"]), ("622.15", "1.05 GB", "2026-12-01"))
        self.assertTrue(found["url"].startswith("https://us.download.nvidia.com/Windows/622.15/"))

    def test_only_an_official_https_installer_of_that_version(self):
        for url in ("http://us.download.nvidia.com/Windows/622.15/x.exe", "https://evil.example/Windows/622.15/x.exe",
                    "https://us.download.nvidia.com.evil.example/Windows/622.15/x.exe", "https://us.download.nvidia.com/Windows/622.14/x.exe"):
            bad = json.loads(DRIVER)
            bad["IDS"][0]["downloadInfo"]["DownloadURL"] = url
            self.assertIsNone(nvidia.parse_driver(json.dumps(bad)), url)
        self.assertIsNone(nvidia.parse_driver("{}"))
        self.assertIsNone(nvidia.parse_driver("pas du json"))

    def test_versions(self):
        self.assertEqual(nvidia.windows_version("32.0.16.2124"), "621.24")
        self.assertEqual(nvidia.windows_version("31.0.15.5222"), "552.22")
        self.assertEqual(nvidia.windows_version("bochs-drm"), "")
        self.assertEqual(nvidia.normalize("NVIDIA GeForce(R) RTX  4080"), "geforce rtx 4080")
        self.assertGreater(nvidia.vkey("622.15"), nvidia.vkey("621.24"))


class TestOffer(FleetTest):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(nvidia, "CACHE", os.path.join(config.STATE, "nvidia-test.json"))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lambda: os.path.exists(nvidia.CACHE) and os.unlink(nvidia.CACHE))
        nvidia._save({"products": nvidia.parse_products(PRODUCTS), "products_at": time.time(),
                      "drivers": {"geforce rtx 4080|135": dict(nvidia.parse_driver(DRIVER), at=time.time())}})

    def machine(self, driver="32.0.16.2124", agent="2.192.0", gpu="NVIDIA GeForce RTX 4080"):
        ident = dict(WINDOWS, agent_version=agent)
        win = Agent(ident, "192.168.43.97")
        win.enroll()
        with transaction() as conn:
            conn.execute("UPDATE devices SET os_name='Windows 11 Professionnel (10.0.26300)' WHERE uid=?", (win.uid,))
        win.checkin({"updates": [], "apps": [], "system": {"kernel": "10.0.26300.9974", "cpu": "Intel(R) Core(TM) i7-14700KF"},
                     "hardware": {"gpus": [{"name": gpu, "vram": 17171480576, "driver": driver, "pci": "10de:2704"}]}})
        return win, device(win.uid)

    def test_an_older_driver_is_offered_and_installed_on_request(self):
        win, dev = self.machine()
        html = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        for text in ("Pilote graphique", "621.24", "622.15", "Mettre à jour le pilote", "622.15 disponible"):
            self.assertIn(text, html, text)
        # Version et adresse : celles du relevé du serveur, pas celles d'un formulaire trafiqué.
        self.admin.post(f"/fleet/{dev['id']}/action", {"action": "gpu-driver", "gpu": "NVIDIA GeForce RTX 4080",
                                                       "url": "https://evil.example/x.exe", "version": "999.99"}, page=f"/fleet/{dev['id']}")
        with transaction() as conn:
            cmd = conn.execute("SELECT * FROM device_commands WHERE device_id=? AND action='gpu-driver'", (dev["id"],)).fetchone()
        params = json.loads(cmd["params"])
        self.assertEqual((params["version"], params["gpu"]), ("622.15", "NVIDIA GeForce RTX 4080"))
        self.assertTrue(params["url"].startswith("https://us.download.nvidia.com/Windows/622.15/"))
        answer = win.checkin({"updates": [], "apps": []}).get_json()
        sent = next(c for c in answer["commands"] if c["action"] == "gpu-driver")
        self.assertEqual((sent["version"], sent["signer"], sent["url"]), ("622.15", "NVIDIA Corporation", params["url"]))
        with fixture.loco.app.test_request_context():
            self.assertIn("621.24 → 622.15", str(fleet.action_label(cmd)))

    def test_nothing_to_do_when_up_to_date_or_old_agent(self):
        _win, dev = self.machine(driver="32.0.16.2215")                      # 622.15 : déjà le dernier
        html = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        self.assertIn("Pilote graphique", html)
        self.assertNotIn("Mettre à jour le pilote", html)
        refused = self.admin.post(f"/fleet/{dev['id']}/action", {"action": "gpu-driver", "gpu": "NVIDIA GeForce RTX 4080"}, page=f"/fleet/{dev['id']}")
        self.assertEqual(refused.status_code, 302)
        with transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM device_commands WHERE device_id=? AND action='gpu-driver'", (dev["id"],)).fetchone())
        _win, old = self.machine(agent="2.191.0")                             # agent qui ne connaît pas l'action
        self.assertNotIn("Pilote graphique", self.admin.c.get(f"/fleet/{old['id']}").get_data(as_text=True))

    def test_a_card_unknown_to_nvidia_is_not_offered(self):
        _win, dev = self.machine(gpu="NVIDIA Quantum GPU 9000")
        html = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        self.assertNotIn("Mettre à jour le pilote", html)


if __name__ == "__main__":
    unittest.main()
