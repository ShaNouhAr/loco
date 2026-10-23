"""Alertes : chiffrement des secrets, évaluation des règles, anti-répétition et canaux d'envoi."""
import datetime
import unittest
from unittest import mock

import test_portal
from test_portal import Browser, ADMIN_PW
import loco
from loco import alerts
from loco.db import transaction


def only(cfg, rule, **threshold):
    for key in alerts.RULES:
        cfg["rules"][key]["on"] = key == rule
    cfg["rules"][rule].update(threshold)
    return cfg


class AlertsTest(unittest.TestCase):
    def setUp(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (alerts.SETTING,))
            conn.execute("DELETE FROM alert_state")
            conn.execute("DELETE FROM alert_outbox")
            conn.execute("DELETE FROM audit")
            conn.execute("DELETE FROM devices")

    def ctx(self):
        return loco.app.test_request_context()


class TestConfig(AlertsTest):
    def test_secrets_are_sealed_and_masked(self):
        with self.ctx():
            cfg = alerts.defaults()
            cfg["email"]["password"] = "s3cr3t"
            cfg["webhook"]["secret"] = "hmackey"
            alerts.save(cfg)
            stored = loco.db.db().execute("SELECT value FROM settings WHERE key=?", (alerts.SETTING,)).fetchone()[0]
            self.assertNotIn("s3cr3t", stored)                 # jamais en clair en base
            got = alerts.load()
            self.assertEqual(got["email"]["password"], "s3cr3t")
            self.assertEqual(alerts._public(got)["email"]["password"], alerts.MASK)
            self.assertEqual(alerts._public(got)["webhook"]["secret"], alerts.MASK)

    def test_severity_routing(self):
        cfg = alerts.defaults()
        cfg["email"].update(enabled=True, min_severity="critical")
        cfg["webhook"].update(enabled=True, min_severity="info")
        self.assertEqual(alerts._targets(cfg, "warning"), ["webhook"])
        self.assertEqual(sorted(alerts._targets(cfg, "critical")), ["email", "webhook"])
        self.assertEqual(alerts._targets(cfg, "info"), ["webhook"])


class TestEvaluate(AlertsTest):
    def fired(self, cfg):
        with self.ctx(), transaction() as conn:
            return [a["key"] for a in alerts.evaluate(conn, cfg)]

    def test_login_failures_rule(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            for i in range(3):
                conn.execute("INSERT INTO audit(at,username,ip,action,detail) VALUES (?,?,?,?,?)",
                             (now, "x", "1.2.3.4", "connexion.echec", ""))
        cfg = only(alerts.defaults(), "login_failures", count=3)
        self.assertIn("login_failures", self.fired(cfg))
        self.assertNotIn("login_failures", self.fired(only(alerts.defaults(), "login_failures", count=4)))

    def test_fleet_rules_count_devices(self):
        old = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            conn.execute("INSERT INTO devices(uid,token_hash,hostname,os_family,enrolled_at,last_seen,security_updates) "
                         "VALUES (?,?,?,?,?,?,?)", ("u1", "h", "pc1", "windows", old, old, 4))
        self.assertIn("security_updates", self.fired(only(alerts.defaults(), "security_updates", count=1)))
        self.assertIn("devices_offline", self.fired(only(alerts.defaults(), "devices_offline", count=1)))

    def test_disabled_rule_never_fires(self):
        cfg = alerts.defaults()
        for key in alerts.RULES:
            cfg["rules"][key]["on"] = False
        self.assertEqual(self.fired(cfg), [])


class TestDispatch(AlertsTest):
    def test_enqueue_send_and_cooldown(self):
        cfg = only(alerts.defaults(), "login_failures", count=1)
        cfg["email"].update(enabled=True, host="smtp.test", recipients="a@b", min_severity="info")
        with transaction() as conn:
            alerts.save(cfg, conn)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            conn.execute("INSERT INTO audit(at,username,ip,action,detail) VALUES (?,?,?,?,?)",
                         (now, "x", "1.2.3.4", "connexion.echec", ""))
        with mock.patch("loco.alerts.send_email") as sent:
            with self.ctx(), transaction() as conn:
                alerts.dispatch(conn)
            self.assertEqual(sent.call_count, 1)
            with transaction() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0], 0)  # vidée
            with self.ctx(), transaction() as conn:                                                   # 2e passage : anti-répétition
                alerts.dispatch(conn)
            self.assertEqual(sent.call_count, 1)

    def test_failed_send_is_retried_not_lost(self):
        cfg = only(alerts.defaults(), "login_failures", count=1)
        cfg["webhook"].update(enabled=True, url="https://hook.test", min_severity="info")
        with transaction() as conn:
            alerts.save(cfg, conn)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as conn:
            conn.execute("INSERT INTO audit(at,username,ip,action,detail) VALUES (?,?,?,?,?)",
                         (now, "x", "1.2.3.4", "connexion.echec", ""))
        with mock.patch("loco.alerts.send_webhook", side_effect=OSError("boom")):
            with self.ctx(), transaction() as conn:
                alerts.dispatch(conn)
            with transaction() as conn:
                row = conn.execute("SELECT attempts, last_error FROM alert_outbox").fetchone()
            self.assertIsNotNone(row)                          # gardée pour réémission
            self.assertEqual(row["attempts"], 1)
            self.assertIn("boom", row["last_error"])


class TestRoutes(AlertsTest):
    def setUp(self):
        super().setUp()
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def test_page_loads(self):
        self.assertEqual(self.admin.c.get("/alerts").status_code, 200)

    def test_save_and_secret_preserved(self):
        self.admin.post("/alerts", {"email_enabled": "1", "email_host": "smtp.x", "email_recipients": "a@b",
                                    "email_port": "587", "email_security": "starttls", "email_min_severity": "warning",
                                    "email_password": "topsecret", "webhook_min_severity": "info",
                                    "rule_login_failures": "1", "rule_login_failures_count": "5"}, page="/alerts")
        with self.ctx():
            cfg = alerts.load()
        self.assertTrue(cfg["email"]["enabled"])
        self.assertEqual(cfg["email"]["password"], "topsecret")
        self.assertEqual(cfg["rules"]["login_failures"]["count"], 5)
        # Ré-enregistrer en laissant le masque : le secret est conservé.
        self.admin.post("/alerts", {"email_enabled": "1", "email_host": "smtp.x", "email_recipients": "a@b",
                                    "email_port": "587", "email_security": "starttls", "email_min_severity": "warning",
                                    "email_password": alerts.MASK, "webhook_min_severity": "info"}, page="/alerts")
        with self.ctx():
            self.assertEqual(alerts.load()["email"]["password"], "topsecret")

    def test_test_button_reports_result(self):
        with mock.patch("loco.alerts.send_webhook") as sent:
            r = self.admin.post("/alerts/test", {"channel": "webhook", "webhook_url": "https://hook.test",
                                                 "webhook_min_severity": "info"}, page="/alerts")
            self.assertEqual(r.json["ok"], True)
            sent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
