"""Fonctionnalités désactivables par le seul compte « admin » : masquées, pages/API refusées, agents tenus à l'écart."""
import html
import json
import unittest

import test_permissions
import test_portal as fixture
from test_fleet import FleetTest, Agent, WINDOWS, device
from loco import features
from loco.db import transaction


class TestFeatures(FleetTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.reset)
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)

    def reset(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.SETTING,))

    def switch(self, *off):
        form = {k: "1" for k in features.FEATURES if k not in off}
        return self.admin.post("/administration/features", form, page="/administration/features")

    def test_only_the_installation_admin_account_manages_features(self):
        page = self.admin.c.get("/administration/features").get_data(as_text=True)
        self.assertIn("Fonctionnalités", page)
        self.assertIn('name="remote"', page)
        self.assertIn("/administration/features", self.admin.c.get("/").get_data(as_text=True))   # navigation
        other = test_permissions.TestPermissions.browser(self, "second-admin", "admin")        # autre administrateur
        self.assertEqual(other.c.get("/administration/features").status_code, 403)
        self.assertEqual(other.post("/administration/features", {}, page="/account").status_code, 403)
        self.assertNotIn("/administration/features", other.c.get("/").get_data(as_text=True))

    def test_disabled_feature_is_hidden_and_refused_everywhere(self):
        self.switch("wu")
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT value FROM settings WHERE key=?", (features.SETTING,)).fetchone()[0]), ["wu"])
            self.assertIn("Cache Windows Update désactivé",
                          conn.execute("SELECT detail FROM audit WHERE action='systeme.fonctionnalites' ORDER BY id DESC").fetchone()[0])
        r = self.admin.c.get("/windows-update")
        self.assertEqual(r.status_code, 403)
        # Werkzeug échappe l'apostrophe en &#39; ou &#x27; selon sa version.
        self.assertIn("Fonctionnalité désactivée par l'administrateur : Cache Windows Update", html.unescape(r.get_data(as_text=True)))
        home = self.admin.c.get("/").get_data(as_text=True)
        self.assertNotIn('href="/windows-update"', home)
        self.assertNotIn("Windows Update, 14 derniers jours", home)
        self.assertEqual(fixture.Browser().c.get("/windows-update").status_code, 302)          # visiteur : connexion
        self.switch()
        self.assertEqual(self.admin.c.get("/windows-update").status_code, 200)

    def test_fleet_off_keeps_agents_waiting_and_turns_remote_off(self):
        a = Agent(WINDOWS, "192.168.43.50"); a.enroll()
        self.switch("fleet")
        r = a.checkin()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers["Retry-After"], "600")
        page = self.admin.c.get("/administration/features").get_data(as_text=True)
        self.assertIn("Inactive tant que Mon parc et agents est désactivé", page)
        self.assertEqual(self.admin.c.get("/fleet").status_code, 403)
        self.switch("apt")
        self.assertEqual(fixture.Browser().c.get("/linux/setup.sh").status_code, 403)          # script public coupé
        self.switch()
        self.assertEqual(a.checkin().status_code, 200)

    def test_install_commands_wait_while_windows_software_is_off(self):
        a = Agent(WINDOWS, "192.168.43.50"); a.enroll()
        with transaction() as conn:
            conn.execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by) VALUES "
                         "(?, 'install-app', '{\"slug\": \"x\"}', '2026-10-19 10:00:00', 'admin')", (device(a.uid)["id"],))
        self.switch("windows_apps")
        self.assertEqual([c["action"] for c in a.checkin().get_json()["commands"]], [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM device_commands").fetchone()[0], "queued")


if __name__ == "__main__":
    unittest.main()
