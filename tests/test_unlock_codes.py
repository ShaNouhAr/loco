"""Code de déverrouillage d'un appareil Android retenu pour un utilisateur seul (bouton « Déverrouiller » de la session
d'écran) : scellé, lié à l'utilisateur et à l'appareil, rendu seulement depuis une session d'écran de cet utilisateur,
jamais affiché ni journalisé."""
import json
import unittest

import test_portal as fixture
from test_android import ANDROID
from test_fleet import Agent, FleetTest, LINUX, device
from loco import features
from loco.db import transaction

PATTERN = "14789"
SECRET = "Deverrou-Essai-2026"


class TestUnlockCodes(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["android"]'))
            conn.execute("DELETE FROM unlock_codes")
        self.addCleanup(self.reset)
        self.agent = Agent(dict(ANDROID, hostname="tablette-code"), "192.168.43.72", uid="c" * 32)
        self.agent.enroll()
        self.dev = device(self.agent.uid)

    def reset(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
            conn.execute("DELETE FROM unlock_codes")

    def screen(self, browser=None, dev=None):
        browser = browser or self.admin
        r = browser.post(f"/fleet/{(dev or self.dev)['id']}/session", {"kind": "desktop"}, page=f"/fleet/{(dev or self.dev)['id']}")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def code(self, sid, browser=None, **form):
        browser = browser or self.admin
        form["csrf"] = browser.token("/fleet")
        return browser.c.post(f"/fleet/session/{sid}/unlock-code", data=form, headers={"X-Requested-With": "fetch"})

    def member(self, name):
        from loco import auth, permissions
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("uc-" + name, "Uc " + name, permissions.role_key("Uc " + name),
                          json.dumps(sorted(permissions.expand({"remote.desktop"})))))
            auth.create_user(conn, "uc-" + name, SECRET + "-loco", "uc-" + name, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login("uc-" + name, SECRET + "-loco").status_code, 302)
        return browser

    def stored(self):
        with transaction() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM unlock_codes")]

    def test_retained_sealed_then_given_back_to_its_user_only(self):
        sid = self.screen()
        r = self.code(sid, action="save", method="pattern", code=PATTERN)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["method"], "pattern")
        self.assertIn("Schéma retenu pour vous seul", r.get_json()["label"])
        self.assertNotIn(PATTERN, r.get_data(as_text=True))
        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(PATTERN, rows[0]["secret"])                       # scellé, pas en clair
        # La page de la session dit qu'un schéma est retenu, sans jamais le montrer.
        page = self.admin.c.get(f"/fleet/session/{self.screen()}").get_data(as_text=True)
        self.assertIn("data-saved", page)
        self.assertIn("Schéma retenu pour vous seul", page)
        self.assertNotIn(PATTERN, page)
        r = self.code(sid, action="use")
        self.assertEqual(r.get_json(), {"method": "pattern", "code": PATTERN})
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        with transaction() as conn:
            journal = " ".join(f"{a} {d}" for a, d in conn.execute("SELECT action, detail FROM audit WHERE action LIKE 'parc.deverrouillage.%'"))
        self.assertIn("parc.deverrouillage.retenu", journal)
        self.assertIn("parc.deverrouillage.utilise", journal)
        self.assertNotIn(PATTERN, journal)

    def test_another_user_never_gets_it(self):
        mine = self.screen()
        self.code(mine, action="save", method="pin", code="123456")
        other = self.member("autre")
        sid = self.screen(other)
        page = other.c.get(f"/fleet/session/{sid}").get_data(as_text=True)
        self.assertNotIn("data-saved", page)
        self.assertEqual(self.code(sid, other, action="use").status_code, 404)
        self.assertEqual(self.code(mine, other, action="use").status_code, 404)            # session d'un autre : introuvable
        # Une ligne recopiée pour un autre utilisateur ne s'ouvre pas : le code est lié à l'utilisateur et à l'appareil.
        with transaction() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='uc-autre'").fetchone()[0]
            conn.execute("INSERT INTO unlock_codes (user_id, device_id, method, secret, saved_at) SELECT ?, device_id, method, secret, saved_at "
                         "FROM unlock_codes WHERE user_id=(SELECT id FROM users WHERE username='admin')", (uid,))
        self.assertEqual(self.code(sid, other, action="use").status_code, 404)

    def test_forget_invalid_codes_and_other_sessions(self):
        sid = self.screen()
        for method, code in (("pattern", "1"), ("pattern", "1123"), ("pattern", "1023"), ("pin", "12"), ("pin", "12a4"),
                             ("password", ""), ("password", "x" * 65), ("otp", "1234")):
            self.assertEqual(self.code(sid, action="save", method=method, code=code).status_code, 400, (method, code))
        self.assertEqual(self.stored(), [])
        self.code(sid, action="save", method="password", code="mot de passe é")
        self.assertEqual(self.code(sid, action="use").get_json()["code"], "mot de passe é")
        self.assertEqual(self.code(sid, action="forget").get_json(), {"ok": True})
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.code(sid, action="use").status_code, 404)
        # Ni terminal, ni poste autre qu'Android.
        terminal = self.admin.post(f"/fleet/{self.dev['id']}/session", {"kind": "terminal"}, page=f"/fleet/{self.dev['id']}")
        tsid = terminal.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        self.assertEqual(self.code(tsid, action="use").status_code, 404)
        linux = Agent(dict(LINUX, hostname="poste-code"), "192.168.43.73", uid="e" * 32)
        linux.enroll()
        self.assertEqual(self.code(self.screen(dev=device(linux.uid)), action="save", method="pin", code="1234").status_code, 404)

    def test_it_goes_with_the_device(self):
        self.code(self.screen(), action="save", method="pin", code="2468")
        with transaction() as conn:
            conn.execute("DELETE FROM devices WHERE id=?", (self.dev["id"],))
        self.assertEqual(self.stored(), [])


if __name__ == "__main__":
    unittest.main()
