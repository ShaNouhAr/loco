"""Gestion des appareils Android : fonctionnalité expérimentale (coupée par défaut), inscription, APK, pages adaptées."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_portal as fixture
from test_fleet import FleetTest, Agent, device
from loco import config, features
from loco.db import transaction

ANDROID = {"hostname": "tablette-salon", "agent_version": config.VERSION, "addresses": ["192.168.43.70"],
           "os": {"family": "android", "name": "Android 13 · Pixel 7"}}


class AndroidTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.reset_experimental)

    def reset_experimental(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))

    def enable_android(self):
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (features.EXPERIMENTAL_SETTING, json.dumps(["android"])))

    # --- Fonctionnalité expérimentale -------------------------------------
    def test_android_is_off_by_default(self):
        with fixture.loco.app.test_request_context():
            self.assertFalse(features.enabled("android"))
            self.assertIn("android", features.disabled())

    def test_admin_only_turns_it_on_in_the_experimental_section(self):
        page = self.admin.c.get("/administration/features").get_data(as_text=True)
        self.assertIn('name="android"', page)
        self.assertIn("Appareils Android", page)
        self.enable_android()
        with fixture.loco.app.test_request_context():
            self.assertTrue(features.enabled("android"))

    # --- Enrôlement (page et APK) -----------------------------------------
    def test_enrollment_has_no_android_tab_until_enabled(self):
        page = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertNotIn('data-tab="android"', page)
        self.enable_android()
        page = self.admin.c.get("/enrollment").get_data(as_text=True)
        self.assertIn('data-tab="android"', page)
        self.assertIn("loco-agent.apk", page)
        self.assertIn(Agent.key(), page)                    # la clé d'inscription est proposée

    def test_apk_is_refused_while_the_feature_is_off(self):
        self.assertEqual(fixture.loco.app.test_client().get("/agent/files/loco-agent.apk").status_code, 404)

    def test_apk_is_served_when_enabled(self):
        self.enable_android()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "loco-agent.apk"
            path.write_bytes(b"PK\x03\x04 faux apk")
            with mock.patch("loco.fleet.agent_apk", return_value=(str(path), "a" * 64)):
                r = fixture.loco.app.test_client().get("/agent/files/loco-agent.apk")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/vnd.android.package-archive")
        self.assertIn(b"faux apk", r.data)

    def test_ca_pem_is_public(self):
        r = fixture.loco.app.test_client().get("/agent/ca.pem")
        self.assertIn(r.status_code, (200, 404))            # 404 seulement si l'autorité n'est pas configurée en test
        if r.status_code == 200:
            self.assertIn("BEGIN CERTIFICATE", r.get_data(as_text=True))

    # --- Inscription d'un appareil ----------------------------------------
    def test_an_android_device_enrolls(self):
        a = Agent(ANDROID, "192.168.43.70")
        self.assertEqual(a.enroll().status_code, 200)
        dev = device(a.uid)
        self.assertEqual(dev["os_family"], "android")
        self.assertEqual(a.checkin().status_code, 200)

    # --- Pages adaptées ----------------------------------------------------
    def enrolled_device(self):
        a = Agent(ANDROID, "192.168.43.70")
        a.enroll()
        return a, device(a.uid)["id"]

    def test_device_page_offers_screen_files_terminal_but_no_updates(self):
        self.enable_android()
        _a, dev_id = self.enrolled_device()
        page = self.admin.c.get(f"/fleet/{dev_id}").get_data(as_text=True)
        self.assertIn("Terminal", page)
        self.assertIn("Écran", page)                        # libellé du bureau pour Android
        self.assertNotIn("Mettre à jour", page)             # pas d'action de mise à jour sur Android
        self.assertNotIn('data-tab="maj"', page)            # ni d'onglet « Mises à jour » sur la fiche

    def test_desktop_session_page_is_android_tailored(self):
        self.enable_android()
        _a, dev_id = self.enrolled_device()
        loc = self.admin.post(f"/fleet/{dev_id}/session", {"kind": "desktop"}).headers["Location"]
        page = self.admin.c.get(loc).get_data(as_text=True)
        self.assertIn("data-label=", page)                  # libellé « écran de l'appareil » posé pour Android
        self.assertIn("écran de l", page)
        self.assertIn("Écran de l", page)                   # description « Écran de l'appareil · toucher… »
        self.assertIn('data-keys="recents"', page)          # barre de navigation Android (bouton propre à Android)
        self.assertIn('data-keys="notifications"', page)
        self.assertIn("Clic droit : Retour", page)          # aide souris → navigation


if __name__ == "__main__":
    unittest.main()
