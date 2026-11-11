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


class TestTrustedNetworks(FleetTest):
    """Réseaux de confiance choisis sur l'appareil : loco n'en reçoit que le mode, pour dire pourquoi il le voit par
    intermittence. Le nom des Wi-Fi reste sur l'appareil."""

    def setUp(self):
        super().setUp()
        self.addCleanup(AndroidTest.reset_experimental, self)
        AndroidTest.enable_android(self)
        self.agent = Agent(ANDROID, "192.168.43.70")
        self.agent.enroll()
        self.dev = device(self.agent.uid)["id"]

    def announce(self, networks):
        data = dict(self.agent.ident)
        if networks is not None:
            data["networks"] = networks
        self.assertEqual(self.agent.post("/agent/api/checkin", data, self.agent.token).status_code, 200)
        return device(self.agent.uid)["net_policy"]

    def card(self):
        import html
        return html.unescape(self.admin.c.get(f"/fleet/{self.dev}").get_data(as_text=True))

    def test_the_card_tells_where_the_device_stays_in_touch(self):
        self.assertEqual(self.announce({"mode": "all", "count": 0}), "")
        page = self.card()
        self.assertIn("Réseaux de confiance", page)
        self.assertIn("actif partout", page)
        self.assertEqual(self.announce({"mode": "wifi", "count": 0}), "wifi")
        self.assertEqual(self.card().count("actif hors données mobiles seulement"), 2)       # en-tête et détails
        self.assertEqual(self.announce({"mode": "trusted", "count": 2}), "trusted:2")
        self.assertIn("actif sur 2 Wi-Fi approuvés seulement", self.card())
        self.assertEqual(self.announce({"mode": "trusted", "count": 1}), "trusted:1")
        self.assertIn("actif sur 1 Wi-Fi approuvé seulement", self.card())
        self.assertEqual(self.announce({"mode": "trusted", "count": 0}), "trusted:0")
        self.assertIn("en pause : aucun Wi-Fi approuvé", self.card())
        self.assertEqual(self.announce({"mode": "all"}), "")                               # revenu à « partout »
        self.assertNotIn("seulement", self.card())

    def test_an_older_app_or_a_malformed_announcement_changes_nothing(self):
        self.assertEqual(self.announce({"mode": "wifi"}), "wifi")
        self.assertEqual(self.announce(None), "wifi")                                      # application d'avant : rien d'annoncé
        self.assertEqual(self.announce("wifi"), "wifi")
        self.assertEqual(self.announce({"mode": "n'importe quoi", "count": "x"}), "")
        self.assertEqual(self.announce({"mode": "trusted", "count": -3}), "trusted:0")
        self.assertEqual(self.announce({"mode": "trusted", "count": True}), "trusted:0")
        self.assertEqual(self.announce({"mode": "trusted", "count": 10 ** 9}), "trusted:0")

    def test_only_android_devices_announce_it(self):
        from test_fleet import LINUX
        linux = Agent(LINUX, "192.168.43.60")
        linux.enroll()
        linux.post("/agent/api/checkin", dict(linux.ident, networks={"mode": "wifi"}), linux.token)
        self.assertEqual(device(linux.uid)["net_policy"], "")
        self.assertNotIn("Réseaux de confiance", self.admin.c.get(f"/fleet/{device(linux.uid)['id']}").get_data(as_text=True))


class TestDeviceKind(FleetTest):
    """Icône du type de poste : ordinateur ; pour Android, téléphone, tablette ou téléviseur annoncé par l'application."""

    def setUp(self):
        super().setUp()
        self.addCleanup(AndroidTest.reset_experimental, self)
        AndroidTest.enable_android(self)
        self.agent = Agent(ANDROID, "192.168.43.70")
        self.agent.enroll()
        self.dev = device(self.agent.uid)["id"]

    def announce(self, form):
        data = dict(self.agent.ident)
        if form is not None:
            data["form"] = form
        self.assertEqual(self.agent.post("/agent/api/checkin", data, self.agent.token).status_code, 200)
        return device(self.agent.uid)["form"]

    def test_an_android_device_is_a_phone_until_its_app_says_otherwise(self):
        from loco import fleet
        self.assertEqual(device(self.agent.uid)["form"], "")                      # application d'avant : rien d'annoncé
        self.assertEqual(fleet.device_kind(device(self.agent.uid)), "phone")
        card = self.admin.c.get(f"/fleet/{self.dev}").get_data(as_text=True)
        self.assertIn('aria-label="Téléphone"', card)
        self.assertIn("supposé", card)
        self.assertEqual(self.announce("tablet"), "tablet")
        card = self.admin.c.get(f"/fleet/{self.dev}").get_data(as_text=True)
        self.assertIn('aria-label="Tablette"', card)
        self.assertIn("<dd>Tablette</dd>", card)
        self.assertNotIn("supposé", card)
        self.assertEqual(self.announce("tv"), "tv")
        self.assertIn('aria-label="Téléviseur"', self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.announce(None), "tv")                              # contact sans l'information : gardée
        for bad in ("watch", "", 3, ["tablet"], {"form": "tv"}):
            self.assertEqual(self.announce(bad), "tv", bad)                      # valeur inconnue : ignorée

    def test_the_type_is_taken_at_enrollment_too(self):
        tablet = Agent(dict(ANDROID, hostname="tablette-accueil", form="tablet"), "192.168.43.71")
        tablet.enroll()
        self.assertEqual(device(tablet.uid)["form"], "tablet")

    def test_computers_get_the_computer_icon_whatever_they_announce(self):
        from loco import fleet
        from test_fleet import LINUX, WINDOWS
        linux, windows = Agent(LINUX, "192.168.43.60"), Agent(WINDOWS, "192.168.43.50")
        for agent in (linux, windows):
            agent.enroll()
            agent.post("/agent/api/checkin", dict(agent.ident, form="tv"), agent.token)
            self.assertEqual(device(agent.uid)["form"], "")
            self.assertEqual(fleet.device_kind(device(agent.uid)), "pc")
        page = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertEqual(page.count('aria-label="Ordinateur"'), 2)
        self.assertEqual(page.count('class="device-icon"'), 3)                   # un par poste de la liste
        self.assertEqual(fleet.device_kind({"os_family": "android"}), "phone")   # ligne partielle, sans la colonne
        self.assertEqual(fleet.device_kind({}), "pc")


if __name__ == "__main__":
    unittest.main()
