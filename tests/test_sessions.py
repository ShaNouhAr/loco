"""Sessions du portail connues du serveur : la déconnexion révoque, durée maximale, inactivité, anciens cookies."""
import hashlib
import unittest

import test_portal as fixture
from test_portal import ADMIN_PW, Browser
from loco import config
from loco.db import transaction as connect


class SessionTest(unittest.TestCase):
    def setUp(self):
        fixture.reset_failures()
        self.browser = Browser()
        self.browser.login("admin", ADMIN_PW)
        with self.browser.c.session_transaction() as s:
            self.cookie = dict(s)
        self.key = hashlib.sha256(self.cookie["sid"].encode()).hexdigest()

    def copy(self):
        """Un autre navigateur qui présente le même cookie (copié sur le poste, ou sorti d'une sauvegarde)."""
        other = Browser()
        with other.c.session_transaction() as s:
            s.update(self.cookie)
        return other

    def row(self):
        with connect() as conn:
            return conn.execute("SELECT * FROM web_sessions WHERE id=?", (self.key,)).fetchone()

    def test_the_server_keeps_only_a_digest_and_logout_revokes_a_copied_cookie(self):
        self.assertIsNotNone(self.row())
        with connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM web_sessions WHERE id=?", (self.cookie["sid"],)).fetchone())
        thief = self.copy()
        self.assertEqual(thief.c.get("/account").status_code, 200)
        self.assertEqual(self.browser.post("/logout", page="/account").status_code, 302)
        self.assertIsNone(self.row())
        self.assertEqual(thief.c.get("/account").status_code, 302)          # le cookie copié ne vaut plus rien
        self.assertIn("/login", thief.c.get("/account").headers["Location"])

    def test_a_session_ends_after_its_maximum_lifetime_even_when_used(self):
        with connect() as conn:
            conn.execute("UPDATE web_sessions SET created=created-? WHERE id=?", (config.SESSION_MAX_HOURS * 3600 + 60, self.key))
        self.assertEqual(self.browser.c.get("/account").status_code, 302)
        self.assertIsNone(self.row())

    def test_an_idle_session_ends(self):
        with connect() as conn:
            conn.execute("UPDATE web_sessions SET seen=seen-? WHERE id=?", (config.SESSION_IDLE_HOURS * 3600 + 60, self.key))
        self.assertEqual(self.browser.c.get("/account").status_code, 302)

    def test_last_visit_is_written_at_most_once_a_minute(self):
        before = self.row()["seen"]
        self.browser.c.get("/account")
        self.assertEqual(self.row()["seen"], before)                         # même minute : aucune écriture
        with connect() as conn:
            conn.execute("UPDATE web_sessions SET seen=seen-120 WHERE id=?", (self.key,))
        self.assertEqual(self.browser.c.get("/account").status_code, 200)
        self.assertGreater(self.row()["seen"], before - 60)

    def test_a_cookie_from_before_this_version_must_sign_in_again(self):
        old = Browser()
        with old.c.session_transaction() as s:
            s.update({k: v for k, v in self.cookie.items() if k != "sid"})
        self.assertEqual(old.c.get("/account").status_code, 302)
        login = old.c.get("/login").get_data(as_text=True)
        self.assertIn('name="csrf"', login)                                  # formulaire de connexion utilisable aussitôt

    def test_changing_the_password_closes_the_other_sessions_of_the_account(self):
        from loco import auth
        name, password = "sessions-essai", "Bleu-Canard-Verglas-42"
        with connect() as conn:
            conn.execute("DELETE FROM users WHERE username=?", (name,))
            auth.create_user(conn, name, password, "admin", must_change=False)

        def cleanup():
            with connect() as conn:
                conn.execute("DELETE FROM users WHERE username=?", (name,))
        self.addCleanup(cleanup)

        def sessions():
            with connect() as conn:
                return [r[0] for r in conn.execute("SELECT s.id FROM web_sessions s JOIN users u ON u.id=s.user_id WHERE u.username=?", (name,))]
        first, other = Browser(), Browser()
        first.login(name, password)
        other.login(name, password)
        self.assertEqual(len(sessions()), 2)
        with first.c.session_transaction() as s:
            kept = hashlib.sha256(s["sid"].encode()).hexdigest()
        new = password + "-bis"
        self.assertEqual(first.post("/account", {"current": password, "new": new, "confirm": new}, page="/account").status_code, 302)
        self.assertEqual(sessions(), [kept])                                 # seule la session qui a changé le mot de passe reste
        self.assertEqual(first.c.get("/account").status_code, 200)
        self.assertEqual(other.c.get("/account").status_code, 302)
        cleanup()
        self.assertEqual(sessions(), [])                                     # compte supprimé : ses sessions partent avec lui


if __name__ == "__main__":
    unittest.main()
