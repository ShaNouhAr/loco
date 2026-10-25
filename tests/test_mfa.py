"""Double authentification (TOTP) : enrôlement, connexion en deux temps, codes de secours, retrait."""
import time
import unittest

import test_portal
from test_portal import Browser, ADMIN_PW
import loco
from loco import totp
from loco.db import transaction as connect


def code_now(secret):
    return totp._code(secret, int(time.time() // totp.PERIOD))


class MfaTest(unittest.TestCase):
    def setUp(self):
        self._clear_admin_mfa()

    def tearDown(self):
        self._clear_admin_mfa()                                 # ne pas laisser la 2FA active pour les autres tests

    @staticmethod
    def _clear_admin_mfa():
        with connect() as conn:
            conn.execute("UPDATE users SET totp_secret='', mfa_enabled=0, recovery_codes='' WHERE username='admin'")
            conn.execute("DELETE FROM login_failures")

    def enroll(self):
        """Active la 2FA pour admin ; renvoie (browser connecté, secret TOTP, codes de secours en clair)."""
        b = Browser()
        b.login("admin", ADMIN_PW)
        b.c.get("/account/mfa/setup")
        with b.c.session_transaction() as s:
            secret = s["mfa_setup_secret"]
        r = b.post("/account/mfa/setup", {"code": code_now(secret)}, page="/account/mfa/setup")
        self.assertEqual(r.status_code, 302)
        with b.c.session_transaction() as s:
            codes = s.get("mfa_recovery_shown")
        return b, secret, codes

    def test_enrollment_stores_encrypted_secret_and_codes(self):
        _b, secret, codes = self.enroll()
        with connect() as conn:
            row = conn.execute("SELECT mfa_enabled, totp_secret, recovery_codes FROM users WHERE username='admin'").fetchone()
        self.assertEqual(row["mfa_enabled"], 1)
        self.assertNotIn(secret, row["totp_secret"])            # chiffré en base
        self.assertEqual(totp.unseal(row["totp_secret"]), secret)
        self.assertEqual(totp.recovery_left(row["recovery_codes"]), 10)
        self.assertEqual(len(codes), 10)

    def test_login_requires_the_code(self):
        _b, secret, _codes = self.enroll()
        fresh = Browser()
        r = fresh.login("admin", ADMIN_PW)                      # mot de passe OK -> 2e étape
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/mfa", r.headers.get("Location", ""))
        self.assertEqual(fresh.c.get("/").status_code, 302)      # pas encore connecté
        self.assertEqual(fresh.post("/login/mfa", {"code": "000000"}, page="/login/mfa").status_code, 401)
        self.assertEqual(fresh.c.get("/").status_code, 302)      # toujours pas connecté
        self.assertEqual(fresh.post("/login/mfa", {"code": code_now(secret)}, page="/login/mfa").status_code, 302)
        self.assertEqual(fresh.c.get("/").status_code, 200)      # connecté

    def test_recovery_code_logs_in_once(self):
        _b, _secret, codes = self.enroll()
        fresh = Browser()
        fresh.login("admin", ADMIN_PW)
        self.assertEqual(fresh.post("/login/mfa", {"code": codes[0]}, page="/login/mfa").status_code, 302)
        self.assertEqual(fresh.c.get("/").status_code, 200)
        with connect() as conn:
            self.assertEqual(totp.recovery_left(conn.execute(
                "SELECT recovery_codes FROM users WHERE username='admin'").fetchone()[0]), 9)
        # le même code ne marche plus
        again = Browser()
        again.login("admin", ADMIN_PW)
        self.assertEqual(again.post("/login/mfa", {"code": codes[0]}, page="/login/mfa").status_code, 401)

    def test_disable_requires_a_valid_code(self):
        b, secret, _codes = self.enroll()
        self.assertEqual(b.post("/account/mfa/disable", {"code": "000000"}, page="/account").status_code, 302)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT mfa_enabled FROM users WHERE username='admin'").fetchone()[0], 1)
        b.post("/account/mfa/disable", {"code": code_now(secret)}, page="/account")
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT mfa_enabled FROM users WHERE username='admin'").fetchone()[0], 0)

    def test_admin_reset_removes_mfa(self):
        self.enroll()
        with connect() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        admin = Browser()
        admin.login("admin", ADMIN_PW)                          # admin a la 2FA -> passe par la 2e étape
        with connect() as conn:
            secret = totp.unseal(conn.execute("SELECT totp_secret FROM users WHERE username='admin'").fetchone()[0])
        admin.post("/login/mfa", {"code": code_now(secret)}, page="/login/mfa")
        r = admin.post(f"/accounts/{uid}/mfa-reset", {}, page="/accounts")
        self.assertEqual(r.status_code, 302)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT mfa_enabled FROM users WHERE username='admin'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
