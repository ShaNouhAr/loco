"""Mon compte : identité, sécurité avec règles en direct, apparence, droits regroupés et activité du compte seul."""
import unittest

import test_permissions
from test_fleet import FleetTest
from loco.db import transaction


class TestAccountPage(FleetTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)

    def test_page_shows_own_activity_rights_and_password_rules(self):
        with transaction() as conn:
            conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES ('2026-10-19 08:00:00', 'admin', "
                         "'192.168.43.20', 'parc.update', 'pc-compta-01'), ('2026-10-19 08:01:00', 'quelqu-un', "
                         "'192.168.43.21', 'windows.ajout', 'action d’un autre compte')")
        page = self.admin.c.get("/compte").get_data(as_text=True)
        for text in ('data-tab="securite"', 'data-tab="apparence"', 'data-tab="droits"', 'data-tab="activite"',
                     'data-rule="length"', 'data-rule="match"', 'data-set-theme="dim"', 'data-set-accent="green"',
                     "Mise à jour demandée", "pc-compta-01", '<details class="rights-group">'):
            self.assertIn(text, page)
        self.assertNotIn("action d’un autre compte", page)               # seulement l'activité de ce compte

    def test_password_change_still_works_and_reader_sees_no_management(self):
        reader = test_permissions.TestPermissions.browser(
            self, "compte-lecture", test_permissions.TestPermissions.profile(self, "compte-lecture", {"dashboard.view"}))
        page = reader.c.get("/compte").get_data(as_text=True)
        self.assertIn("compte-lecture", page)
        self.assertNotIn("Tout le journal de ce compte", page)            # pas de droit sur le journal
        r = reader.post("/compte", {"current": test_permissions.PASSWORD, "new": "Nouveau-Secret-2026x",
                                    "confirm": "Nouveau-Secret-2026x"}, page="/compte")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM audit WHERE username='perm-compte-lecture' AND action='compte.mot_de_passe'")
                            .fetchone())


if __name__ == "__main__":
    unittest.main()
