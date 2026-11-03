"""Déploiement par vagues : répartition, départ de chaque vague, pause sur échec, postes éteints, décisions humaines, droits."""
import datetime
import json
import re
from unittest import mock

import test_permissions
import test_scope
from test_fleet import FleetTest
from test_fleet_pages import add_devices
from loco import alerts, dashboard, deployments, fleet
from loco.db import transaction


def stamp(hours=0):
    return (datetime.datetime.now() - datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def tick():
    with transaction() as conn:
        deployments.advance_rollouts(conn)


OPTIONS = {"mode": "waves", "wave_size": "2", "wave_delay": "0", "wave_stop": "1"}


class RolloutTest(FleetTest):
    profile = test_permissions.TestPermissions.profile
    browser = test_permissions.TestPermissions.browser
    cleanup_accounts = test_permissions.TestPermissions.cleanup_accounts

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_accounts)
        with transaction() as conn:
            for table in ("deployment_targets", "deployments", "device_commands", "device_groups"):
                conn.execute(f"DELETE FROM {table}")
        dashboard._state_cache.clear()

    def start(self, **options):
        r = self.admin.post("/fleet/update-all", dict(OPTIONS, **options), page="/fleet")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:200])
        self.assertIn("/fleet/deployments/", r.headers["Location"])
        return r.headers["Location"].rsplit("/", 1)[-1]

    def targets(self, deployment_id):
        with transaction() as conn:
            return conn.execute("SELECT t.hostname,t.wave,t.pending,t.last_state,t.reason,t.command_id,c.state FROM deployment_targets t "
                                "LEFT JOIN device_commands c ON c.id=t.command_id WHERE t.deployment_id=? ORDER BY t.wave,t.hostname", (deployment_id,)).fetchall()

    def wave(self, deployment_id, number):
        return [t for t in self.targets(deployment_id) if t["wave"] == number]

    def finish(self, deployment_id, number, state="success"):
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state=?,finished_at=? WHERE id IN (SELECT command_id FROM deployment_targets WHERE deployment_id=? AND wave=?)",
                         (state, stamp(), deployment_id, number))

    def rollout(self, deployment_id):
        with transaction() as conn:
            return json.loads(conn.execute("SELECT waves FROM deployments WHERE id=?", (deployment_id,)).fetchone()[0])

    def page(self, deployment_id, browser=None):
        return (browser or self.admin).c.get(f"/fleet/deployments/{deployment_id}").get_data(as_text=True)

    def commands(self):
        with transaction() as conn:
            return conn.execute("SELECT COUNT(*) FROM device_commands").fetchone()[0]


class TestStart(RolloutTest):
    def test_only_the_first_wave_receives_a_command(self):
        add_devices(5, updates=3)
        deployment = self.start()
        self.assertEqual(self.rollout(deployment)["total"], 3)
        self.assertEqual(self.commands(), 2)
        first, later = self.wave(deployment, 1), self.wave(deployment, 2) + self.wave(deployment, 3)
        self.assertTrue(all(t["state"] == "queued" for t in first))
        self.assertTrue(all(t["last_state"] == "planned" and t["pending"] and not t["command_id"] for t in later))
        page = self.page(deployment)
        self.assertIn("Vague 1 sur 3", page)
        self.assertIn("Prévu dans une vague suivante", page)

    def test_machines_online_go_first_they_answer_faster(self):
        add_devices(2, prefix="a-eteint", updates=3, last_seen=stamp(5))
        add_devices(2, prefix="z-allume", updates=3)
        deployment = self.start()
        self.assertEqual([t["hostname"] for t in self.wave(deployment, 1)], ["z-allume-000", "z-allume-001"])

    def test_pilot_group_forms_the_first_wave(self):
        ids = add_devices(6, prefix="p", updates=3)
        with transaction() as conn:
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Pilotes', '')").lastrowid
            conn.executemany("INSERT INTO device_group_members VALUES (?,?)", [(group, ids[5]), (group, ids[4]), (group, ids[3])])
        deployment = self.start(wave_pilot=str(group))
        self.assertEqual([t["hostname"] for t in self.wave(deployment, 1)], ["p-003", "p-004", "p-005"])
        self.assertEqual(len(self.wave(deployment, 2)), 2)
        self.assertEqual(self.rollout(deployment)["total"], 3)

    def test_machines_without_update_are_left_out(self):
        add_devices(2, prefix="u", updates=3)
        add_devices(3, prefix="ok")
        deployment = self.start()
        self.assertEqual(len(self.targets(deployment)), 2)

    def test_bad_values_are_refused_and_nothing_starts(self):
        add_devices(3, updates=3)
        for values in ({"wave_size": "0"}, {"wave_size": "abc"}, {"wave_delay": "99999"}, {"wave_stop": "0"}, {"wave_pilot": "999999"}):
            r = self.admin.post("/fleet/update-all", dict(OPTIONS, **values), page="/fleet")
            self.assertEqual(r.status_code, 302)
            self.assertNotIn("deployments/", r.headers["Location"])
        self.assertEqual(self.commands(), 0)

    def test_all_at_once_is_unchanged(self):
        add_devices(3, updates=3)
        r = self.admin.post("/fleet/update-all", {"mode": "all"}, page="/fleet")
        self.assertIn("/fleet/updates", r.headers["Location"])
        self.assertEqual(self.commands(), 3)


class TestAdvance(RolloutTest):
    def test_next_wave_starts_after_a_success(self):
        add_devices(4, updates=3)
        deployment = self.start()
        tick()
        self.assertEqual(self.commands(), 2)        # la première vague n'a pas fini
        self.finish(deployment, 1)
        tick()
        self.assertEqual(self.commands(), 4)
        self.assertEqual(self.rollout(deployment)["current"], 2)
        self.finish(deployment, 2)
        tick()
        self.assertTrue(self.rollout(deployment)["finished"])
        self.assertIn("Toutes les vagues sont parties", self.page(deployment))

    def test_the_delay_is_respected_and_can_be_skipped_by_a_person(self):
        add_devices(4, updates=3)
        deployment = self.start(wave_delay="30")
        self.finish(deployment, 1)
        tick()
        tick()
        self.assertEqual(self.commands(), 2)
        self.assertIn("Vague suivante dans 30 minutes.", self.page(deployment))
        self.admin.post(f"/fleet/deployments/{deployment}/next-wave", {}, page=f"/fleet/deployments/{deployment}")
        self.assertEqual(self.commands(), 4)

    def test_a_failure_pauses_and_nothing_more_leaves(self):
        add_devices(6, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        tick()
        self.assertEqual(self.commands(), 2)
        self.assertEqual(self.rollout(deployment)["paused"], {"wave": 1, "failed": 2})
        page = self.page(deployment)
        self.assertIn("En pause après un échec", page)
        self.assertIn("Vague 1 : 2 échecs.", page)

    def test_failure_is_visible_on_the_overview_and_notified(self):
        add_devices(4, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        with mock.patch.object(alerts, "evaluate", return_value=[]):
            overview = self.admin.c.get("/").get_data(as_text=True)
        self.assertIn("en pause après un échec", overview)
        self.assertIn(f"/fleet/deployments/{deployment}", overview)
        with transaction() as conn:
            fired = [a for a in alerts.evaluate(conn, alerts.every_rule(conn)) if a["key"] == "rollout_paused"]
        self.assertEqual(len(fired), 1)
        self.assertIn("à la vague 1 : 2 échecs", fired[0]["body"])

    def test_a_person_can_decide_to_continue_and_only_the_new_wave_counts(self):
        add_devices(6, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        self.admin.post(f"/fleet/deployments/{deployment}/next-wave", {}, page=f"/fleet/deployments/{deployment}")
        rollout = self.rollout(deployment)
        self.assertEqual((rollout["current"], "paused" in rollout), (2, False))
        self.assertEqual(self.commands(), 4)
        self.finish(deployment, 2)
        tick()
        self.assertEqual(self.rollout(deployment)["current"], 3)     # les échecs de la vague 1 ne bloquent plus

    def test_threshold_allows_a_few_failures(self):
        add_devices(4, updates=3)
        deployment = self.start(wave_stop="2")
        first = self.wave(deployment, 1)
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state='failed' WHERE id=?", (first[0]["command_id"],))
            conn.execute("UPDATE device_commands SET state='success' WHERE id=?", (first[1]["command_id"],))
        tick()
        self.assertEqual(self.rollout(deployment)["current"], 2)

    def test_failure_in_the_last_wave_leaves_nothing_to_decide(self):
        add_devices(2, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        rollout = self.rollout(deployment)
        self.assertTrue(rollout["finished"])
        self.assertNotIn("paused", rollout)
        with transaction() as conn:
            self.assertEqual([a for a in alerts.evaluate(conn, alerts.every_rule(conn)) if a["key"] == "rollout_paused"], [])
        self.assertIn("Déploiement terminé avec erreurs", self.page(deployment))


class TestOfflineMachines(RolloutTest):
    def test_a_machine_switched_off_does_not_block_once_another_succeeded(self):
        add_devices(1, prefix="a-allume", updates=3)
        add_devices(1, prefix="b-eteint", updates=3, last_seen=stamp(5))
        add_devices(2, prefix="c-allume", updates=3)
        deployment = self.start()
        wave = self.wave(deployment, 1)
        self.assertEqual([t["hostname"] for t in wave], ["a-allume-000", "c-allume-000"])
        add = self.wave(deployment, 2)
        self.assertIn("b-eteint-000", [t["hostname"] for t in add])
        self.finish(deployment, 1)
        tick()
        self.assertEqual(self.rollout(deployment)["current"], 2)
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state='success' WHERE id IN (SELECT command_id FROM deployment_targets WHERE deployment_id=? AND hostname='c-allume-001')", (deployment,))
        tick()
        self.assertTrue(self.rollout(deployment)["finished"])        # le poste éteint recevra sa commande à son retour
        self.assertEqual([t["state"] for t in self.targets(deployment) if t["hostname"] == "b-eteint-000"], ["queued"])

    def test_a_wave_that_nobody_answered_proves_nothing(self):
        add_devices(4, updates=3, last_seen=stamp(5))
        deployment = self.start()
        tick()
        tick()
        rollout = self.rollout(deployment)
        self.assertTrue(rollout["silent"])
        self.assertEqual(rollout["current"], 1)
        page = self.page(deployment)
        self.assertIn("Aucun poste de cette vague n'a répondu", page)
        self.assertIn("Lancer la vague suivante", page)

    def test_a_machine_that_received_its_command_then_went_silent_blocks_until_it_expires(self):
        add_devices(2, updates=3)
        add_devices(2, prefix="z", updates=3)
        deployment = self.start()
        first = self.wave(deployment, 1)
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET state='running',sent_at=? WHERE id=?", (stamp(7), first[0]["command_id"]))
            conn.execute("UPDATE device_commands SET state='success' WHERE id=?", (first[1]["command_id"],))
        tick()
        self.assertEqual(self.rollout(deployment)["current"], 1)
        with transaction() as conn:
            fleet.expire_commands(conn)
        tick()
        self.assertEqual(self.rollout(deployment)["paused"]["failed"], 1)


class TestLaterChanges(RolloutTest):
    def test_stopping_cancels_the_waves_to_come(self):
        add_devices(6, updates=3)
        deployment = self.start()
        self.admin.post(f"/fleet/deployments/{deployment}/cancel", {}, page=f"/fleet/deployments/{deployment}")
        tick()
        self.assertEqual(self.commands(), 2)
        self.assertTrue(all(t["state"] == "cancelled" for t in self.wave(deployment, 1)))
        self.assertTrue(all(t["last_state"] == "cancelled" and not t["pending"] for t in self.wave(deployment, 2) + self.wave(deployment, 3)))
        self.assertIn("Déploiement annulé", self.page(deployment))

    def test_a_machine_removed_or_already_updated_is_skipped_when_its_wave_starts(self):
        ids = add_devices(4, updates=3)
        deployment = self.start()
        with transaction() as conn:
            conn.execute("UPDATE devices SET state='removed' WHERE id=?", (ids[2],))
            conn.execute("UPDATE devices SET updates=0,security_updates=0 WHERE id=?", (ids[3],))
        self.finish(deployment, 1)
        tick()
        later = self.wave(deployment, 2)
        self.assertEqual(self.commands(), 2)
        self.assertEqual(sorted(t["reason"] for t in later), ["Poste retiré du parc avant le départ de sa vague", "Rien à mettre à jour au départ de sa vague"])
        self.assertTrue(all(t["last_state"] == "skipped" for t in later))
        tick()
        self.assertTrue(self.rollout(deployment)["finished"] or self.rollout(deployment).get("silent"))

    def test_command_of_a_later_wave_keeps_the_author_and_the_options(self):
        add_devices(3, updates=3)
        deployment = self.start(reboot="1")
        self.finish(deployment, 1)
        tick()
        with transaction() as conn:
            row = conn.execute("SELECT c.created_by,c.params FROM deployment_targets t JOIN device_commands c ON c.id=t.command_id WHERE t.deployment_id=? AND t.wave=2", (deployment,)).fetchone()
        params = json.loads(row["params"])
        self.assertEqual((row["created_by"], params["reboot"], params["deployment_id"]), ("admin", True, deployment))


class TestSoftware(RolloutTest):
    def linux_draft(self, count):
        ids = add_devices(count, prefix="lin", family="debian")
        catalog = {"deb:acme": {"name": "acme", "kind": "deb", "family": "debian", "version": "1", "arch": "all", "repositories": {"debian/trixie"}}}
        with mock.patch.object(deployments, "_package_catalog", return_value=catalog):
            r = self.admin.post("/fleet/deployments/prepare-linux", {"package": "deb:acme", "device": [str(i) for i in ids]}, page="/account")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return r.headers["Location"].rsplit("/", 1)[-1], catalog

    def test_a_package_deployment_can_go_in_waves(self):
        draft, catalog = self.linux_draft(5)
        self.assertIn('name="wave_size"', self.page(draft))
        with mock.patch.object(deployments, "_package_catalog", return_value=catalog):
            self.admin.post(f"/fleet/deployments/{draft}/confirm", OPTIONS, page=f"/fleet/deployments/{draft}")
        self.assertEqual(self.commands(), 2)
        self.finish(draft, 1)
        tick()
        self.assertEqual(self.commands(), 4)
        with transaction() as conn:
            action = conn.execute("SELECT c.action FROM deployment_targets t JOIN device_commands c ON c.id=t.command_id WHERE t.deployment_id=? AND t.wave=2", (draft,)).fetchone()[0]
        self.assertEqual(action, "install-package")

    def test_without_choice_a_deployment_still_goes_all_at_once(self):
        draft, catalog = self.linux_draft(3)
        with mock.patch.object(deployments, "_package_catalog", return_value=catalog):
            self.admin.post(f"/fleet/deployments/{draft}/confirm", {}, page=f"/fleet/deployments/{draft}")
        self.assertEqual(self.commands(), 3)
        self.assertEqual(self.rollout_empty(draft), "")

    def rollout_empty(self, deployment_id):
        with transaction() as conn:
            return conn.execute("SELECT waves FROM deployments WHERE id=?", (deployment_id,)).fetchone()[0]

    def test_installers_of_later_waves_are_kept_by_the_cleanup(self):
        snapshot = {"sha256": "c" * 64}
        with transaction() as conn:
            conn.execute("INSERT INTO deployments(id,title,created_by,pw_version,owner,created_at,expires,plan,state) VALUES ('d1','t','admin',1,'o','',0,'{}','queued')")
            conn.execute("INSERT INTO deployment_targets(deployment_id,device_uid,hostname,os_family,wave,pending) VALUES ('d1','u','h','windows',2,?)",
                         (json.dumps({"action": "install-app", "params": {"app_snapshots": [snapshot]}}),))
            conn.execute("DELETE FROM settings WHERE key='deployment_gc'")
        root = deployments.Path(deployments.config.WIN_DIR, "_deployments", "c" * 64)
        root.mkdir(parents=True, exist_ok=True)
        import os
        os.utime(root, (1, 1))
        with transaction() as conn:
            deployments.cleanup_artifacts(conn)
        self.assertTrue(root.is_dir())


class TestRights(RolloutTest):
    def test_viewers_can_read_but_not_decide(self):
        add_devices(4, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        viewer = self.browser("lecture", self.profile("lecture", {"fleet.view", "deployments.view"}))
        page = self.page(deployment, viewer)
        self.assertIn("En pause après un échec", page)
        self.assertNotIn("Lancer la vague suivante", page)
        r = viewer.post(f"/fleet/deployments/{deployment}/next-wave", {}, page="/account")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.commands(), 2)

    def test_installing_software_does_not_allow_steering_a_fleet_update(self):
        add_devices(4, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        installer = self.browser("poseur", self.profile("poseur", {"fleet.view", "fleet.install", "deployments.view"}))
        self.assertEqual(installer.post(f"/fleet/deployments/{deployment}/next-wave", {}, page="/account").status_code, 403)
        self.assertEqual(installer.post(f"/fleet/deployments/{deployment}/cancel", {}, page="/account").status_code, 403)

    def test_starting_needs_the_bulk_update_right(self):
        add_devices(3, updates=3)
        limited = self.browser("simple", self.profile("simple", {"fleet.view", "fleet.update"}))
        self.assertEqual(limited.post("/fleet/update-all", OPTIONS, page="/account").status_code, 403)
        self.assertEqual(self.commands(), 0)

    def test_a_limited_account_does_not_see_a_rollout_outside_its_machines(self):
        ids = add_devices(2, prefix="a", updates=3)
        add_devices(2, prefix="b", updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        self.ids = {"mine": ids[0]}
        self.addCleanup(lambda: test_scope.ScopeTest.cleanup(self))
        role, _r = test_scope.ScopeTest.profile(self, "Technicien", {"dashboard.view", "fleet.view", "deployments.view"}, devices=["mine"])
        tech = test_scope.ScopeTest.account(self, "tech", role)
        self.assertEqual(tech.c.get(f"/fleet/deployments/{deployment}").status_code, 404)
        with mock.patch.object(alerts, "evaluate", return_value=[]):
            self.assertNotIn("en pause après un échec", tech.c.get("/").get_data(as_text=True))


class TestPages(RolloutTest):
    def test_list_shows_the_kind_and_the_pause(self):
        add_devices(4, updates=3)
        deployment = self.start()
        self.finish(deployment, 1, "failed")
        tick()
        page = self.admin.c.get("/fleet/deployments").get_data(as_text=True)
        self.assertIn("En pause après un échec", page)
        self.assertIn('<span class="os-tag">Mises à jour</span>', page)

    def test_update_all_dialog_offers_waves_only_for_several_machines(self):
        add_devices(1, updates=3)
        self.assertNotIn('name="wave_size"', self.admin.c.get("/fleet").get_data(as_text=True))
        add_devices(30, prefix="z", updates=3)
        self.assertRegex(self.admin.c.get("/fleet").get_data(as_text=True), r'name="wave_size" min="1" value="3"')

    def test_group_name_is_in_the_title(self):
        ids = add_devices(3, updates=3)
        with transaction() as conn:
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Compta', '')").lastrowid
            conn.executemany("INSERT INTO device_group_members VALUES (?,?)", [(group, ids[0]), (group, ids[1])])
        deployment = self.start(group=str(group))
        self.assertEqual(len(self.targets(deployment)), 2)
        self.assertTrue(re.search(r"<h1>Mises à jour · groupe Compta</h1>", self.page(deployment)))
