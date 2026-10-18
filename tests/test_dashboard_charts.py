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
        with mock.patch("loco.dashboard.wu_daily", return_value=[("2026-10-17", 3 * 2**30, 2**30), ("2026-10-18", 0, 0)]):
            page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Windows Update, 14 derniers jours", page)
        self.assertIn("75 % servis par le cache", page)
        self.assertIn('class="bar-hit"', page)
        self.assertIn("17/10 : 3,00 Go depuis le cache, 1,00 Go depuis Internet", page)
        self.assertIn('class="ring-green"', page)
        self.assertIn("Disque des données", page)
        self.assertIn('class="seg-free"', page)
        self.assertNotIn("<script", page.split("Windows Update, 14 derniers jours")[1].split("Services")[0])

    def test_fleet_chart_follows_permissions(self):
        import test_permissions
        browser = test_permissions.TestPermissions.browser
        profile = test_permissions.TestPermissions.profile
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        viewer = browser(self, "graphiques", profile(self, "graphiques", {"dashboard.view"}))
        page = viewer.c.get("/").get_data(as_text=True)
        self.assertIn("Windows Update, 14 derniers jours", page)
        self.assertNotIn('class="chart ring"', page)


if __name__ == "__main__":
    unittest.main()
