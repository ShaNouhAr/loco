"""Vue d'ensemble : état général selon les droits et le parc du compte, parc cliquable, stockage, actualisation en direct."""
import datetime
from pathlib import Path
import re
from unittest import mock

import test_permissions
import test_scope
from test_fleet import FleetTest, Agent, LINUX, WINDOWS, device
from loco import alerts, dashboard
from loco.db import transaction

G = 1024 ** 3
ROOT = Path(__file__).resolve().parents[1]


def alert(key, body, severity="warning"):
    return {"key": key, "severity": severity, "subject": key, "body": body}


def stamp(hours=0):
    return (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def command(dev_id, state, hours=1, action="update"):
    with transaction() as conn:
        conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, finished_at) VALUES (?,?,?,?,?)",
                     (dev_id, action, state, stamp(hours), stamp(hours) if state in ("failed", "success") else ""))


class OverviewTest(FleetTest):
    profile = test_permissions.TestPermissions.profile
    browser = test_permissions.TestPermissions.browser
    cleanup_accounts = test_permissions.TestPermissions.cleanup_accounts

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_accounts)
        dashboard._state_cache.clear()
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")

    def page(self, browser=None, firing=()):
        with mock.patch.object(alerts, "evaluate", return_value=list(firing)):
            r = (browser or self.admin).c.get("/")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def viewer(self, name="vue", grants=("dashboard.view",)):
        return self.browser(name, self.profile(name, set(grants)))


class TestAttention(OverviewTest):
    def test_nothing_to_do_says_what_was_checked(self):
        page = self.page()
        self.assertIn("Aucun point à traiter", page)
        self.assertIn("Contrôlés : services, disque, certificat, sauvegardes, postes.", page)
        self.assertNotIn('class="attention"', page)
        self.assertIn("Contrôlés : services, disque.", self.page(self.viewer()))

    def test_most_serious_first_each_with_the_page_where_to_act(self):
        page = self.page(firing=[alert("security_updates", "Trois mises à jour."), alert("cert_expiring", "Le certificat expire."),
                                 alert("service_down", "Services arrêtés : nginx.", "critical")])
        self.assertNotIn("Aucun point à traiter", page)
        rows = re.findall(r'<a class="attention-row" href="([^"]+)"><span class="st (\w+)">([^<]+)', page)
        self.assertEqual(rows, [("/system", "err", "Services arrêtés : nginx."), ("/fleet", "warn", "Trois mises à jour."),
                                ("/system#certificat", "warn", "Le certificat expire.")])

    def test_each_row_follows_the_rights_of_the_account(self):
        page = self.page(self.viewer(), firing=[alert("service_down", "Services arrêtés : nginx.", "critical"),
                                                alert("cert_expiring", "Le certificat expire."), alert("security_updates", "Trois mises à jour."),
                                                alert("login_failures", "Douze échecs."), alert("syslog_failed", "Syslog en panne.")])
        self.assertIn('<div class="attention-row"><span class="st err">Services arrêtés : nginx.', page)    # visible, sans lien
        for hidden in ("Le certificat expire.", "Trois mises à jour.", "Douze échecs.", "Syslog en panne."):
            self.assertNotIn(hidden, page)

    def test_a_machine_switched_off_is_not_a_problem(self):
        page = self.page(firing=[alert("devices_offline", "2 postes sans contact.")])
        self.assertNotIn("2 postes sans contact.", page)
        self.assertIn("Aucun point à traiter", page)

    def test_turning_a_notification_off_does_not_hide_the_problem(self):
        cfg = alerts.defaults()
        cfg["rules"]["service_down"]["on"] = False
        with mock.patch.object(alerts, "load", return_value=cfg), mock.patch("loco.util.service_active", return_value=False):
            with transaction() as conn:
                self.assertEqual([a for a in alerts.evaluate(conn) if a["key"] == "service_down"], [])
            page = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("Services arrêtés :", page)

    def test_a_failed_update_counts_until_it_succeeds(self):
        agent = Agent(LINUX, "192.168.43.80")
        agent.enroll()
        dev = device(agent.uid)["id"]
        command(dev, "failed", hours=30)
        self.assertNotIn("mise à jour en échec", self.page())           # trop ancien
        command(dev, "failed")
        self.assertIn('<a class="attention-row" href="/fleet/updates"><span class="st warn">1 poste avec une mise à jour en échec', self.page())
        command(dev, "success")
        self.assertNotIn("mise à jour en échec", self.page())


class TestScope(FleetTest):
    def setUp(self):
        super().setUp()
        dashboard._state_cache.clear()
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username = 'tech'")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
            conn.execute("DELETE FROM device_commands")
        self.addCleanup(self.cleanup)
        self.ids = {}
        for name, ident, ip in (("mine", WINDOWS, "192.168.43.81"), ("other", LINUX, "192.168.43.82")):
            agent = Agent(ident, ip)
            agent.enroll()
            self.ids[name] = device(agent.uid)["id"]
        with transaction() as conn:
            conn.execute("UPDATE devices SET security_updates=1, updates=1 WHERE id=?", (self.ids["mine"],))
            conn.execute("UPDATE devices SET security_updates=5, updates=5, reboot_required=1 WHERE id=?", (self.ids["other"],))

    def cleanup(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username = 'tech'")
            conn.execute("DELETE FROM access_roles WHERE builtin=0")

    def test_a_limited_account_only_sees_its_own_machines(self):
        role, _r = test_scope.ScopeTest.profile(self, "Technicien", {"dashboard.view", "fleet.view"}, devices=["mine"])
        tech = test_scope.ScopeTest.account(self, "tech", role)
        command(self.ids["other"], "failed")
        whole = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("6 mises à jour de sécurité", whole)
        self.assertIn("1 poste avec une mise à jour en échec", whole)
        page = tech.c.get("/").get_data(as_text=True)
        self.assertIn("1 mise à jour de sécurité en attente", page)
        self.assertNotIn("6 mises à jour de sécurité", page)
        self.assertNotIn("mise à jour en échec", page)
        self.assertNotIn("Redémarrage nécessaire", page)
        self.assertIn('<p class="status-total"><b>1</b>', page)

    def test_the_filter_applies_to_every_machine_rule(self):
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE id=?", (stamp(5), self.ids["other"]))
            keys = {a["key"] for a in alerts.evaluate(conn, alerts.every_rule(conn), devices=f" AND id IN ({self.ids['mine']})")}
        self.assertIn("security_updates", keys)
        self.assertNotIn("devices_offline", keys)


class TestFleetPanel(OverviewTest):
    def test_states_lead_to_the_filtered_list_and_empty_ones_stay_quiet(self):
        agent = Agent(WINDOWS, "192.168.43.83")
        agent.enroll()
        agent.checkin({"updates": [], "apps": []})
        page = self.page()
        self.assertRegex(page, r'<a class="fleet-state" href="/fleet\?statut=green"><i class="key ring-green"></i><span>À jour</span><b class="num">1</b>')
        self.assertEqual(page.count('class="fleet-state zero"'), 3)
        self.assertNotIn("statut=red", page)

    def test_side_facts_only_appear_when_there_is_something_to_say(self):
        agent = Agent(LINUX, "192.168.43.84")
        agent.enroll()
        self.assertNotIn('class="fleet-facts"', self.page())
        dev = device(agent.uid)["id"]
        command(dev, "queued")
        with transaction() as conn:
            conn.execute("UPDATE devices SET reboot_required=1 WHERE id=?", (dev,))
        page = self.page()
        self.assertIn('Mises à jour en cours : <b data-bind="updating">1</b>', page)
        self.assertIn("Redémarrage nécessaire : <b>1</b>", page)
        self.assertNotIn("Hors ligne :", page)
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE id=?", (stamp(5), dev))
        page = self.page()
        self.assertIn("Hors ligne : <b>1</b>", page)
        self.assertNotIn("Mises à jour en cours", page)     # un poste éteint n'avance pas

    def test_no_machine_invites_to_install_the_agent(self):
        self.assertIn("Aucun poste : installez l'agent", self.page())


class TestPageContent(OverviewTest):
    STATS = {"acng": 2 * G, "wucache": 8 * G, "rpmcache": 0, "flatpakcache": 1 * G, "apt": G // 2, "rpm": 0, "flatpak": 0, "win": G // 2}
    DEBS = [{"package": f"p{i}", "version": "1", "arch": "all", "size": 1, "mtime": 1793600000, "repository": "debian/trixie"} for i in range(2)]

    def storage(self, browser=None):
        disk = {"total": 100 * G, "used": 20 * G, "free": 80 * G, "pct": 20}
        with mock.patch.object(dashboard, "read_stats", return_value=self.STATS), mock.patch.object(dashboard, "disk_usage", return_value=disk), \
                mock.patch.object(dashboard, "list_debs", return_value=self.DEBS):
            page = self.page(browser)
        return page, page.split('class="usage-list"', 1)[1].split("</ul>", 1)[0]

    def test_former_tiles_and_services_table_are_gone(self):
        page = self.page()
        for gone in ('class="metric"', "Réseau en direct", "Servi en local", "<th>Rôle", "Disque des données"):
            self.assertNotIn(gone, page)
        positions = [page.index(f"<h2>{title}</h2>") for title in ("Parc", "Serveur", "Stockage", "Derniers ajouts")]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(page.index("Aucun point à traiter"), positions[0])

    def test_disk_figure_and_bar_say_the_same_thing(self):
        page, _list = self.storage()
        self.assertIn('<span data-bind="disk.pct" data-fmt="pct">20 %</span>', page)
        self.assertIn('<i data-bind="disk.pct" data-fmt="meter" data-width="20">', page)
        self.assertNotIn('data-bind="disk.pct" data-fmt="bytes"', page)

    def test_storage_is_sorted_by_size_and_leads_to_each_cache(self):
        page, usage = self.storage()
        labels = re.findall(r"<li><span>(?:<a[^>]*>)?([^<]+)", usage)
        self.assertEqual(labels, ["Cache Windows Update", "Système et autres", "Cache APT", "Cache Flathub", "Dépôts et logiciels locaux"])
        self.assertIn('<a href="/windows-update">Cache Windows Update</a>', usage)
        self.assertIn('<a href="/linux">Cache APT</a>', usage)
        self.assertIn('<a href="/flatpak">Cache Flathub</a>', usage)
        self.assertIn('<span class="sub">2 paquets</span>', usage)

    def test_storage_links_follow_rights_and_features(self):
        _page, usage = self.storage(self.viewer())
        self.assertNotIn("<a ", usage)
        self.assertIn("Cache Windows Update", usage)
        with mock.patch("loco.features.disabled", return_value={"wu"}):
            _page, usage = self.storage()
        self.assertNotIn('href="/windows-update"', usage)

    def test_windows_update_share_is_only_given_once(self):
        with mock.patch("loco.wupdate.wu_daily", return_value=[("2026-10-17", 3 * G, G)]):
            page = self.page()
        self.assertEqual(page.count("servis par le cache"), 1)
        self.assertIn("75 % servis par le cache", page)
        self.assertNotIn("du trafic récent", page)


class TestLiveRefresh(OverviewTest):
    def signature(self, page):
        return re.search(r'data-poll-kind="status"[^>]*data-signature="([0-9a-f]+)"', page).group(1)

    def poll(self, browser=None):
        return (browser or self.admin).c.get("/api/status", headers={"X-Requested-With": "fetch"}).get_json()

    def test_poll_matches_the_page_until_something_changes(self):
        firing = [alert("service_down", "Services arrêtés : nginx.", "critical")]
        with mock.patch.object(alerts, "evaluate", return_value=[]):
            mark = self.signature(self.admin.c.get("/").get_data(as_text=True))
            self.assertEqual(self.poll()["signature"], mark)
        with mock.patch.object(alerts, "evaluate", return_value=firing), mock.patch.object(dashboard, "STATE_TTL", 0):
            self.assertNotEqual(self.poll()["signature"], mark)

    def test_the_costly_part_is_not_redone_at_every_poll_but_always_for_a_page(self):
        with mock.patch.object(alerts, "evaluate", return_value=[]) as evaluate:
            self.admin.c.get("/")
            self.poll()
            self.poll()
            self.assertEqual(evaluate.call_count, 1)
            self.admin.c.get("/")
            self.assertEqual(evaluate.call_count, 2)

    def test_updates_in_progress_change_in_place_without_reloading(self):
        agents = [Agent(LINUX, f"192.168.43.{85 + i}") for i in range(2)]
        for agent in agents:
            agent.enroll()
        command(device(agents[0].uid)["id"], "queued")
        with mock.patch.object(alerts, "evaluate", return_value=[]), mock.patch.object(dashboard, "STATE_TTL", 0):
            first = self.poll()
            self.assertEqual(first["updating"], 1)
            command(device(agents[1].uid)["id"], "queued")
            second = self.poll()
        self.assertEqual(second["updating"], 2)
        self.assertEqual(second["signature"], first["signature"])

    def test_the_count_is_not_sent_without_the_right_to_see_the_fleet(self):
        self.assertNotIn("updating", self.poll(self.viewer()))

    def test_each_account_has_its_own_state(self):
        viewer = self.viewer()
        with mock.patch.object(alerts, "evaluate", return_value=[alert("cert_expiring", "Le certificat expire.")]):
            self.admin.c.get("/")
            self.assertNotEqual(self.signature(viewer.c.get("/").get_data(as_text=True)), self.poll()["signature"])

    def test_page_polls_with_the_live_reload(self):
        self.assertIn("status: function (d) { bind(d); reload(d); }", (ROOT / "portal/static/app.js").read_text())
