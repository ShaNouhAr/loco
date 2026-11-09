"""Alertes : chiffrement des secrets, évaluation des règles, anti-répétition, canaux d'envoi, et envoi qui ne retient
jamais la base."""
import datetime
import sqlite3
import time
import unittest
from unittest import mock

from test_portal import Browser, ADMIN_PW
import loco
from loco import alerts, config
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
    def failing_login(self, channel="webhook"):
        """Une alerte « échecs de connexion » due, et son canal d'envoi activé."""
        cfg = only(alerts.defaults(), "login_failures", count=1)
        if channel == "email":
            cfg["email"].update(enabled=True, host="smtp.test", recipients="a@b", min_severity="info")
        else:
            cfg["webhook"].update(enabled=True, url="https://hook.test", min_severity="info")
        with transaction() as conn:
            alerts.save(cfg, conn)
            conn.execute("INSERT INTO audit(at,username,ip,action,detail) VALUES (?,?,?,?,?)",
                         (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "x", "1.2.3.4", "connexion.echec", ""))

    def outbox(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM alert_outbox ORDER BY id").fetchall()

    def test_enqueue_send_and_cooldown(self):
        self.failing_login("email")
        with mock.patch("loco.alerts.send_email") as sent:
            with self.ctx():
                alerts.dispatch()
            self.assertEqual(sent.call_count, 1)
            self.assertEqual(self.outbox(), [])                # vidée
            with self.ctx():                                   # 2e passage : anti-répétition
                alerts.dispatch()
            self.assertEqual(sent.call_count, 1)

    def test_failed_send_is_retried_not_lost(self):
        self.failing_login()
        with mock.patch("loco.alerts.send_webhook", side_effect=OSError("boom")):
            with self.ctx():
                alerts.dispatch()
            row = self.outbox()[0]                             # gardée pour réémission
            self.assertEqual(row["attempts"], 1)
            self.assertIn("boom", row["last_error"])
            self.assertGreater(float(row["next_try"]), time.time() + 30)   # reprogrammée, pas au passage suivant

    def test_sending_never_holds_the_database(self):
        """Un serveur de messagerie ou un webhook qui tarde ne doit retenir aucune écriture du portail."""
        self.failing_login()
        seen = {}

        def slow(cfg, subject, body, severity):
            other = sqlite3.connect(config.DB_PATH, timeout=0.2)          # une écriture d'ailleurs : contact d'un agent…
            try:
                other.execute("BEGIN IMMEDIATE")                          # « database is locked » si l'envoi tenait la base
                seen["reserved"] = float(other.execute("SELECT next_try FROM alert_outbox").fetchone()[0]) > time.time()
                other.rollback()
            finally:
                other.close()

        with mock.patch("loco.alerts.send_webhook", side_effect=slow) as sent:
            with self.ctx():
                alerts.dispatch()
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(seen, {"reserved": True})
        self.assertEqual(self.outbox(), [])

    def test_an_entry_being_sent_is_not_sent_twice(self):
        self.failing_login()
        subjects = []

        def reentrant(cfg, subject, body, severity):
            subjects.append(subject)
            if len(subjects) == 1:
                alerts.drain()                                 # un autre passage pendant l'envoi : entrée réservée

        with mock.patch("loco.alerts.send_webhook", side_effect=reentrant):
            with self.ctx():
                alerts.dispatch()
        self.assertEqual(len(subjects), 1)
        self.assertEqual(self.outbox(), [])

    def test_a_reservation_left_by_a_sudden_stop_expires(self):
        self.failing_login()
        with self.ctx(), transaction() as conn:
            self.assertTrue(alerts.enqueue(conn))
        entry = self.outbox()[0]["id"]
        self.assertIsNotNone(alerts._claim(entry))             # envoi commencé… puis arrêt brutal : jamais soldée
        with mock.patch("loco.alerts.send_webhook") as sent:
            alerts.drain()
            self.assertEqual(sent.call_count, 0)               # encore réservée
            with transaction() as conn:
                conn.execute("UPDATE alert_outbox SET next_try=?", (str(time.time() - 1),))
            alerts.drain()
            self.assertEqual(sent.call_count, 1)               # réservation expirée : renvoyée
        self.assertEqual(self.outbox(), [])

    def test_an_entry_is_given_up_after_too_many_attempts(self):
        self.failing_login()
        with self.ctx(), transaction() as conn:
            alerts.enqueue(conn)
        with transaction() as conn:
            conn.execute("UPDATE alert_outbox SET attempts=?", (alerts.MAX_ATTEMPTS - 1,))
        with mock.patch("loco.alerts.send_webhook", side_effect=OSError("boom")):
            alerts.drain()
        self.assertEqual(self.outbox(), [])

    def test_nothing_is_sent_without_a_channel(self):
        with mock.patch("loco.alerts.send_webhook") as sent, mock.patch("loco.alerts.send_email") as mailed:
            with self.ctx():
                alerts.dispatch()
        self.assertEqual((sent.call_count, mailed.call_count), (0, 0))


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
