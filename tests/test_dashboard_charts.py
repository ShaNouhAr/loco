"""Vue d'ensemble : historique Windows Update par jour (journaux tournants) et graphiques SVG."""
import datetime
import gzip
import os
import tempfile
import unittest
from unittest import mock

from test_fleet import FleetTest, Agent, WINDOWS
from loco import config, wupdate

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def line(day, cache, size):
    stamp = f"{day.day:02d}/{MONTHS[day.month - 1]}/{day.year}:10:00:00 +0000"
    return f'192.168.43.50 [{stamp}] "GET /filestreamingservice/files/x HTTP/1.1" 200 {size} {cache} "dl.delivery.mp.microsoft.com"\n'


class TestWindowsUpdateHistory(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.log = os.path.join(self.work.name, "loco-wu.log")
        patcher = mock.patch.object(config, "WU_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)
        wupdate._DAILY.clear()
        self.today = datetime.date.today()

    def day(self, ago):
        return self.today - datetime.timedelta(days=ago)

    def test_days_span_current_rotated_and_compressed_logs(self):
        with gzip.open(self.log + ".2.gz", "wt") as f:
            f.write(line(self.day(3), "HIT", 300) + line(self.day(3), "MISS", 50))
        with open(self.log + ".1", "w") as f:
            f.write(line(self.day(1), "HIT", 100) + line(self.day(0), "HIT", 7))     # rotation en cours de journée
        with open(self.log, "w") as f:
            f.write(line(self.day(0), "HIT", 10) + line(self.day(0), "MISS", 20) + "ligne illisible\n")
        days = {d: (h, m) for d, h, m in wupdate.wu_daily(14)}
        self.assertEqual(len(days), 14)
        self.assertEqual(days[self.day(0).isoformat()], (17, 20))
        self.assertEqual(days[self.day(1).isoformat()], (100, 0))
        self.assertEqual(days[self.day(3).isoformat()], (300, 50))
        self.assertEqual(days[self.day(2).isoformat()], (0, 0))
        self.assertNotIn(self.day(14).isoformat(), days)

    def test_current_log_is_read_incrementally_and_survives_truncation(self):
        with open(self.log, "w") as f:
            f.write(line(self.day(0), "HIT", 10))
        self.assertEqual(wupdate.wu_daily(1)[0][1:], (10, 0))
        with open(self.log, "a") as f:
            f.write(line(self.day(0), "MISS", 5) + line(self.day(0), "HIT", 1)[:30])    # dernière ligne incomplète
        self.assertEqual(wupdate.wu_daily(1)[0][1:], (10, 5))
        with open(self.log, "a") as f:
            f.write(line(self.day(0), "HIT", 1)[30:])
        self.assertEqual(wupdate.wu_daily(1)[0][1:], (11, 5))
        with open(self.log, "w") as f:                                                      # copytruncate
            f.write(line(self.day(0), "HIT", 2))
        self.assertEqual(wupdate.wu_daily(1)[0][1:], (2, 0))


class TestDashboardCharts(FleetTest):
    def test_dashboard_draws_the_charts(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [], "apps": []})
        with mock.patch("loco.wupdate.wu_daily", return_value=[("2026-10-17", 3 * 2**30, 2**30), ("2026-10-18", 0, 0)]):
            page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Windows Update, 14 derniers jours", page)
        self.assertIn("75 % servis par le cache", page)
        self.assertIn('class="bar-hit"', page)
        self.assertIn("17/10 : 3,00 Go depuis le cache, 1,00 Go depuis Internet", page)
        self.assertIn('class="ring-green"', page)
        self.assertIn("Disque des données", page)
        self.assertIn('class="usage-list"', page)
        self.assertNotIn("<script", page.split("Windows Update, 14 derniers jours")[1].split("Services")[0])

    def test_fleet_chart_follows_permissions(self):
        import test_permissions
        browser = test_permissions.TestPermissions.browser
        profile = test_permissions.TestPermissions.profile
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        viewer = browser(self, "graphiques", profile(self, "graphiques", {"dashboard.view"}))
        page = viewer.c.get("/").get_data(as_text=True)
        self.assertIn("Windows Update, 14 derniers jours", page)
        self.assertNotIn('class="chart status-bar"', page)


class TestWindowsUpdatePage(FleetTest):
    def setUp(self):
        super().setUp()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.log = os.path.join(work.name, "loco-wu.log")
        patcher = mock.patch.object(config, "WU_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)
        wupdate._DAILY.clear()
        wupdate._CACHE.clear()
        service = mock.patch("loco.wupdate.service_active", return_value=True)   # nginx absent des VM de test
        self.nginx = service.start()
        self.addCleanup(service.stop)

    def test_page_names_known_devices_and_counts_the_last_14_days(self):
        page = self.admin.c.get("/windows-update").get_data(as_text=True)
        self.assertIn("Aucun poste n'est encore passé par loco", page)
        self.nginx.return_value = False
        self.assertIn("Le cache est arrêté (nginx)", self.admin.c.get("/windows-update").get_data(as_text=True))
        self.nginx.return_value = True
        Agent(WINDOWS, "192.168.43.50").enroll()                            # même adresse que le journal
        today = datetime.date.today()
        with open(self.log, "w") as f:
            f.write(line(today - datetime.timedelta(days=20), "MISS", 999) + line(today, "MISS", 2**20)
                    + line(today, "HIT", 3 * 2**20))
        data = self.admin.c.get("/api/wu").get_json()
        self.assertEqual(data["period"], {"hit": 3 * 2**20, "miss": 2**20, "ratio": 75})
        self.assertEqual(data["recent"][0]["name"], WINDOWS["hostname"])
        self.assertEqual([r["source"] for r in data["recent"]], ["cache", "Internet", "Internet"])
        self.assertEqual(data["clients"], 1)
        self.assertTrue(data["last"].startswith(today.isoformat()))
        page = self.admin.c.get("/windows-update").get_data(as_text=True)
        self.assertNotIn("Aucun poste n'est encore passé", page)
        self.assertIn(f'<span>{WINDOWS["hostname"]}</span><span class="sub mono">192.168.43.50</span>', page)
        self.assertIn('id="domains"', page)
        self.assertIn('class="bar-hit"', page)

    def test_viewers_without_fleet_rights_see_addresses_only(self):
        import test_permissions
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        viewer = test_permissions.TestPermissions.browser(
            self, "wu-lecture", test_permissions.TestPermissions.profile(self, "wu-lecture", {"wu.view"}))
        Agent(WINDOWS, "192.168.43.50").enroll()
        with open(self.log, "w") as f:
            f.write(line(datetime.date.today(), "HIT", 10))
        self.assertEqual(viewer.c.get("/api/wu").get_json()["recent"][0]["name"], "")
        page = viewer.c.get("/windows-update").get_data(as_text=True)
        self.assertNotIn('id="domains"', page)
        self.assertNotIn(WINDOWS["hostname"], page)


if __name__ == "__main__":
    unittest.main()
