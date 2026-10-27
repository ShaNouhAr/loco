"""Supervision des postes : relevés des agents (processeur, mémoire, disques, réseau), historique, courbes,
aperçu dans Mon parc et alertes sur seuil."""
import importlib.util
import json
import os
import time
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, LINUX, WINDOWS, device
from loco import alerts, monitoring
from loco.db import transaction

GIB = 1024 ** 3


def reading(cpu=12.5, mem=0.5, disk=0.4, **extra):
    return dict({"cores": 4, "cpu": cpu, "mem_total": 8 * GIB, "mem_used": int(8 * GIB * mem), "swap_total": 2 * GIB,
                 "swap_used": 0, "load": [0.5, 0.4, 0.3], "uptime": 93784, "net_rx": 125000, "net_tx": 40000,
                 "disks": [{"mount": "/", "fs": "ext4", "total": 100 * GIB, "used": int(100 * GIB * disk)},
                           {"mount": "/srv", "fs": "xfs", "total": 500 * GIB, "used": 50 * GIB}]}, **extra)


class SanitizeTest(unittest.TestCase):
    def test_values_are_bounded_and_cleaned(self):
        m = monitoring.sanitize({"cpu": 250, "mem_total": 100, "mem_used": 900, "net_rx": -5, "load": [1, "x", 2],
                                 "disks": [{"mount": "/", "total": 10, "used": 99}, {"mount": "bad\x07", "total": 10, "used": 1},
                                           "pas un disque"] + [{"mount": f"/d{i}", "total": 10, "used": 1} for i in range(30)]})
        self.assertEqual(m["cpu"], 100.0)
        self.assertEqual(m["mem_used"], 100)                      # jamais plus que le total
        self.assertNotIn("net_rx", m)
        self.assertNotIn("load", m)
        self.assertEqual(m["disks"][0]["used"], 10)
        self.assertLessEqual(len(m["disks"]), 16)
        self.assertFalse(any("\x07" in d["mount"] for d in m["disks"]))
        self.assertEqual(monitoring.sanitize("n'importe quoi"), {})

    def test_summary_levels(self):
        s = monitoring.summary(monitoring.sanitize(reading(cpu=97, mem=0.9, disk=0.5)))
        self.assertEqual((s["levels"]["cpu"], s["levels"]["mem"], s["levels"]["disk"]), ("err", "warn", "ok"))
        self.assertEqual(s["disk_mount"], "/")


class ChartTest(unittest.TestCase):
    def test_gaps_break_the_line_and_labels_never_overlap(self):
        points = [{"at": 1_800_000_000 + i * 360, "cpu": (None if 100 <= i < 120 else 50.0), "mem": 51.0,
                   "net_rx": None, "net_tx": None} for i in range(monitoring.BUCKETS)]
        c = monitoring.line_chart(points, 86400, (("cpu", "Processeur"), ("mem", "Mémoire")), ymax=100)
        self.assertEqual(len(c["series"][0]["segments"]), 2)              # poste éteint : courbe interrompue
        self.assertEqual(len(c["series"][1]["segments"]), 1)
        ends = sorted(s["end"]["y"] for s in c["series"])
        self.assertGreaterEqual(ends[1] - ends[0], 13)                    # 50 % et 51 % : étiquettes décalées
        self.assertEqual(len(c["cells"]), monitoring.BUCKETS)
        self.assertIsNone(monitoring.line_chart([dict(p, cpu=None, mem=None) for p in points], 86400,
                                                (("cpu", "P"), ("mem", "M"))))


    def test_round_ticks_survive_the_clock_change(self):
        end = 1793228400                                                  # 27/10/2026 : semaine du passage à l'heure d'hiver
        for span, expected in ((86400, 6), (7 * 86400, 7)):
            points = [{"at": end - span + (i + 0.5) * span / monitoring.BUCKETS, "cpu": 1.0, "mem": 1.0,
                       "net_rx": None, "net_tx": None} for i in range(monitoring.BUCKETS)]
            labels = [t["label"] for t in monitoring.line_chart(points, span, (("cpu", "P"),), ymax=100)["ticks"]]
            self.assertEqual(len(labels), len(set(labels)))                 # jamais deux fois le même jour
            self.assertGreaterEqual(len(labels), expected - 1)
        self.assertEqual([monitoring.nice(v) for v in (3.7, 4071, 99, 101)], [5, 5000, 100, 200])


class AgentReadingTest(unittest.TestCase):
    """L'agent Linux mesure le processeur et le réseau en moyenne depuis le contact précédent."""

    def test_linux_agent_metrics(self):
        spec = importlib.util.spec_from_file_location("loco_client", os.path.join("portal", "agents", "loco-client.py"))
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        state = {}
        first = agent.metrics(state)
        self.assertNotIn("cpu", first)                                    # pas de relevé précédent
        self.assertIn("metrics_sample", state)
        state["metrics_sample"]["at"] -= 120
        state["metrics_sample"]["cpu_total"] -= 1000
        second = agent.metrics(state)
        self.assertIn("cpu", second)
        self.assertTrue(0 <= second["cpu"] <= 100)
        self.assertGreater(second["mem_total"], 0)
        self.assertEqual(monitoring.sanitize(second)["mem_total"], second["mem_total"])


class FleetMonitoringTest(unittest.TestCase):
    def setUp(self):
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)
        self.agent = Agent(dict(LINUX, hostname="mon-linux"), "192.168.43.70")
        self.agent.enroll()

    def checkin(self, agent, metrics):
        data = dict(agent.ident, metrics=metrics)
        self.assertEqual(agent.post("/agent/api/checkin", data, agent.token).status_code, 200)

    def test_checkin_stores_the_reading_and_history(self):
        self.checkin(self.agent, reading())
        dev = device(self.agent.uid)
        latest = json.loads(dev["metrics"])
        self.assertEqual(latest["cpu"], 12.5)
        with transaction() as conn:
            rows = conn.execute("SELECT cpu, mem, disk FROM device_metrics WHERE device_id=?", (dev["id"],)).fetchall()
            conn.execute("INSERT INTO device_metrics (device_id, at, cpu) VALUES (?, ?, 1)", (dev["id"], int(time.time()) - 8 * 86400))
        self.assertEqual(tuple(rows[-1]), (12.5, 50.0, 40.0))
        self.checkin(self.agent, reading())
        with transaction() as conn:                                       # plus vieux que 7 jours : purgé
            old = conn.execute("SELECT COUNT(*) FROM device_metrics WHERE device_id=? AND at < ?",
                               (dev["id"], int(time.time()) - 7 * 86400)).fetchone()[0]
        self.assertEqual(old, 0)

    def test_device_page_and_fleet_list(self):
        self.checkin(self.agent, reading(disk=0.93))
        dev = device(self.agent.uid)
        page = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        self.assertIn('data-panel="supervision"', page)
        self.assertIn('class="chart line-chart"', page)
        self.assertIn("/srv", page)                                       # tableau des disques
        self.assertIn("presque plein", page)
        self.assertIn("1 j 2 h", page)                                    # démarré depuis 93 784 s
        week = self.admin.c.get(f"/fleet/{dev['id']}?periode=7j").get_data(as_text=True)
        self.assertIn('seg-btn on" href="/fleet/%d?periode=7j' % dev["id"], week)
        listing = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn('class="res"', listing)
        self.assertIn("93 %", listing)

    def test_device_without_readings(self):
        other = Agent(dict(WINDOWS, hostname="mon-ancien"), "192.168.43.71")
        other.enroll()
        other.checkin()
        page = self.admin.c.get(f"/fleet/{device(other.uid)['id']}").get_data(as_text=True)
        self.assertIn("Aucun relevé pour l'instant", page)

    def test_alerts_on_thresholds(self):
        for _ in range(3):
            self.checkin(self.agent, reading(cpu=99, mem=0.98, disk=0.95))
        with fixture.loco.app.test_request_context(), transaction() as conn:
            cfg = alerts.defaults()
            fired = {f["key"]: f["body"] for f in alerts.evaluate(conn, cfg)}
        self.assertIn("mon-linux (/ 95 %)", fired["device_disk_high"])
        self.assertIn("mon-linux", fired["device_cpu_high"])
        self.assertIn("mon-linux", fired["device_memory_high"])

    def test_no_cpu_alert_on_a_single_spike(self):
        lone = Agent(dict(LINUX, hostname="mon-pic"), "192.168.43.72")
        lone.enroll()
        self.checkin(lone, reading(cpu=100))
        with fixture.loco.app.test_request_context(), transaction() as conn, \
             mock.patch.object(monitoring, "recent_averages", return_value={device(lone.uid)["id"]: {"cpu": 100, "mem": 10, "n": 1}}):
            fired = {f["key"]: f["body"] for f in alerts.evaluate(conn, alerts.defaults())}
        self.assertNotIn("mon-pic", fired.get("device_cpu_high", ""))


if __name__ == "__main__":
    unittest.main()
