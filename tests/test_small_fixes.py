"""Petits défauts : commandes sans réponse, filtres hors ligne et redémarrage, infobulle, pluriels, processeur, cache vide."""
import datetime
import re
from unittest import mock
import unittest

import test_i18n
import test_portal as fixture
from test_fleet import FleetTest, Agent, LINUX, status_of
from test_fleet_pages import add_devices, hosts
from loco import alerts, dashboard, fleet, schedules, util
from loco.db import transaction

G = 1024 ** 3


def stamp(hours=0, seconds=0):
    return (datetime.datetime.now() - datetime.timedelta(hours=hours, seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


class TestCommandsWithoutAnswer(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")
        self.dev = add_devices(1, prefix="poste-mort", last_seen=stamp(hours=48))[0]

    def command(self, state, hours):
        with transaction() as conn:
            return conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, sent_at, created_by) VALUES (?,?,?,?,?,?)",
                                (self.dev, "update", state, stamp(hours), stamp(hours) if state != "queued" else "", "admin")).lastrowid

    def state(self, cmd):
        with transaction() as conn:
            return conn.execute("SELECT state, output, finished_at FROM device_commands WHERE id=?", (cmd,)).fetchone()

    def test_background_task_closes_commands_of_a_machine_that_never_comes_back(self):
        sent, running = self.command("sent", 7), self.command("running", 7)
        alerts._tick()
        for cmd in (sent, running):
            row = self.state(cmd)
            self.assertEqual((row["state"], row["output"]), ("failed", "Pas de réponse du poste."))
            self.assertTrue(row["finished_at"])

    def test_recent_commands_and_waiting_ones_are_left_alone(self):
        recent, waiting = self.command("sent", 1), self.command("queued", 100)
        alerts._tick()
        self.assertEqual(self.state(recent)["state"], "sent")
        self.assertEqual(self.state(waiting)["state"], "queued")

    def test_it_then_shows_as_finished_and_failed(self):
        self.command("running", 7)
        alerts._tick()
        page = self.admin.c.get("/fleet/updates").get_data(as_text=True)
        self.assertIn("poste-mort-000", page)
        self.assertIn("Pas de réponse du poste.", page)

    def test_check_in_still_closes_only_its_own_commands(self):
        mine = self.command("sent", 7)
        other = add_devices(1, prefix="autre")[0]
        with transaction() as conn:
            theirs = conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, sent_at) VALUES (?,?,?,?,?)",
                                  (other, "update", "sent", stamp(7), stamp(7))).lastrowid
            fleet.expire_commands(conn, self.dev)
        self.assertEqual(self.state(mine)["state"], "failed")
        self.assertEqual(self.state(theirs)["state"], "sent")


class TestViewFilters(FleetTest):
    def setUp(self):
        super().setUp()
        add_devices(2, prefix="enligne")
        add_devices(3, prefix="eteint", last_seen=stamp(hours=2))
        add_devices(1, prefix="reboot", reboot_required=1)

    def get(self, query=""):
        return self.admin.c.get("/fleet" + query).get_data(as_text=True)

    def test_offline_filter(self):
        self.assertEqual(sorted(hosts(self.get("?voir=hors-ligne"))), ["eteint-000", "eteint-001", "eteint-002"])

    def test_restart_filter(self):
        self.assertEqual(hosts(self.get("?voir=redemarrage")), ["reboot-000"])

    def test_unknown_filter_is_ignored(self):
        self.assertEqual(len(hosts(self.get("?voir=nimporte"))), 6)

    def test_chips_show_counts_and_a_second_click_removes_the_filter(self):
        page = self.get()
        self.assertRegex(page, r'href="/fleet\?voir=hors-ligne">Hors ligne <b>3</b>')
        self.assertRegex(page, r'href="/fleet\?voir=redemarrage">Redémarrage nécessaire <b>1</b>')
        self.assertRegex(self.get("?voir=hors-ligne"), r'class="chip on" href="/fleet">Hors ligne')

    def test_chip_is_hidden_without_machine_unless_selected(self):
        with transaction() as conn:
            conn.execute("UPDATE devices SET reboot_required=0")
        self.assertNotIn(">Redémarrage nécessaire <b>", self.get())
        self.assertIn(">Redémarrage nécessaire <b>0</b>", self.get("?voir=redemarrage"))

    def test_other_filters_and_search_keep_it(self):
        page = self.get("?voir=hors-ligne")
        self.assertIn('name="voir" value="hors-ligne"', page)
        self.assertRegex(page, r'href="/fleet\?os=windows&amp;voir=hors-ligne"')
        self.assertEqual(hosts(self.get("?voir=hors-ligne&q=eteint-001")), ["eteint-001"])

    def test_overview_links_lead_to_the_same_count(self):
        dashboard._state_cache.clear()
        with mock.patch.object(alerts, "evaluate", return_value=[]):
            page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn('<a href="/fleet?voir=hors-ligne">Hors ligne : <b>3</b></a>', page)
        self.assertIn('<a href="/fleet?voir=redemarrage">Redémarrage nécessaire : <b>1</b></a>', page)


class TestTooltip(FleetTest):
    def test_state_is_not_said_twice(self):
        add_devices(1, prefix="ok")
        add_devices(1, prefix="incomplet", inventory='{"updates": [], "error": "apt-get update : panne"}')
        page = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertNotIn('title="à jour : à jour"', page)
        self.assertIn('title="à jour"', page)
        self.assertNotIn("inventaire incomplet : inventaire incomplet", page)
        self.assertIn('title="inventaire incomplet : apt-get update : panne"', page)

    def test_a_different_detail_is_still_given(self):
        add_devices(1, prefix="depot", inventory='{"updates": [], "warning": "Dépôt injoignable, ses mises à jour ne sont pas comptées : x"}')
        self.assertIn('title="à jour : Dépôt injoignable, ses mises à jour ne sont pas comptées : x"', self.admin.c.get("/fleet").get_data(as_text=True))

    def test_machine_out_of_contact_is_not_said_twice(self):
        add_devices(1, prefix="vieux", last_seen=stamp(hours=24 * 60))
        page = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertNotIn("sans contact récent : poste sans contact récent", page)


class TestPlurals(FleetTest):
    def test_no_placeholder_plural_remains_in_any_message(self):
        found = [m for m in test_i18n.all_messages() if re.search(r"[a-zéèà]\((s|x|aux|e|es)\)", m)]
        self.assertEqual(found, [])

    def test_singular_and_plural_read_correctly(self):
        agent = Agent(LINUX, "192.168.43.97")
        agent.enroll()
        agent.checkin({"updates": [{"name": "curl", "version": "1", "security": True}]})
        self.assertIn("1 mise à jour de sécurité en attente", status_of(agent.uid)[1])
        agent.checkin({"updates": [{"name": n, "version": "1", "security": True} for n in ("curl", "wget")]})
        self.assertIn("2 mises à jour de sécurité en attente", status_of(agent.uid)[1])

    def test_english_and_spanish_follow(self):
        from loco import i18n
        for lang, text in (("en", "Update 2 machines?"), ("es", "¿Actualizar 2 equipos?")):
            with fixture.loco.app.test_request_context("/"), mock.patch.object(i18n, "locale", return_value=lang):
                self.assertEqual(i18n.n_("Mettre à jour %(num)s poste ?", "Mettre à jour %(num)s postes ?", 2), text)

    def test_results_saved_by_older_versions_still_read_well(self):
        with fixture.loco.app.test_request_context("/", headers={"Accept-Language": "fr"}):
            self.assertEqual(schedules.result_text("3 poste(s)"), "3 postes")
            self.assertEqual(schedules.result_text("1 poste"), "1 poste")
            self.assertEqual(schedules.result_text("4 postes"), "4 postes")

    def test_counted_helper(self):
        self.assertEqual([util.counted(n, "poste") for n in (0, 1, 2)], ["0 poste", "1 poste", "2 postes"])


class TestProcessor(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(util, "_cpu_last", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_reading_samples_then_averages_without_waiting(self):
        times = iter([(1000, 900), (1100, 950), (2100, 1450)])
        clock = iter([100.0, 100.2, 110.2, 110.2])
        with mock.patch.object(util, "_cpu_times", lambda: next(times)), mock.patch.object(util.time, "sleep") as sleep, \
                mock.patch.object(util.time, "monotonic", lambda: next(clock)), mock.patch.object(util.config, "WINDOWS", False):
            first = util.cpu_usage()
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual(first["pct"], 50)
            second = util.cpu_usage()
            self.assertEqual(sleep.call_count, 1)        # moyenne sur l'intervalle, sans nouvelle attente
        self.assertEqual(second["pct"], 50)

    def test_old_reading_is_not_averaged_over_hours(self):
        util._cpu_last = (0.0, 1000, 900)
        with mock.patch.object(util, "_cpu_times", side_effect=[(5000, 4000), (5100, 4100)]), mock.patch.object(util.time, "sleep") as sleep, \
                mock.patch.object(util.time, "monotonic", return_value=9999.0), mock.patch.object(util.config, "WINDOWS", False):
            self.assertEqual(util.cpu_usage()["pct"], 0)
        sleep.assert_called_once()

    def test_two_tabs_at_once_do_not_divide_by_nothing(self):
        util._cpu_last = (100.0, 1000, 900)
        with mock.patch.object(util, "_cpu_times", side_effect=[(1000, 900), (1000, 900)]), mock.patch.object(util.time, "sleep"), \
                mock.patch.object(util.time, "monotonic", return_value=100.1), mock.patch.object(util.config, "WINDOWS", False):
            self.assertEqual(util.cpu_usage()["pct"], 0)


class TestNewInstallStorage(FleetTest):
    def page(self, stats, cpu=None):
        disk = {"total": 10 * G, "used": G, "free": 9 * G, "pct": 10}
        dashboard._state_cache.clear()
        with mock.patch.object(dashboard, "read_stats", return_value=stats), mock.patch.object(dashboard, "disk_usage", return_value=disk), \
                mock.patch.object(dashboard, "cpu_usage", return_value=cpu or {"pct": 3, "load1": 0.31, "cores": 2}), \
                mock.patch.object(alerts, "evaluate", return_value=[]):
            return self.admin.c.get("/").get_data(as_text=True)

    def test_an_empty_cache_is_not_listed(self):
        usage = self.page({"acng": 2, "wucache": 0, "flatpakcache": 5 * 1024 ** 2}).split('class="usage-list"', 1)[1].split("</ul>", 1)[0]
        self.assertNotIn("Cache APT", usage)
        self.assertIn("Cache Flathub", usage)
        self.assertIn("Système et autres", usage)

    def test_load_uses_the_same_decimal_as_the_live_refresh(self):
        self.assertIn('<span data-bind="cpu.load1">0,31</span>', self.page({}))
