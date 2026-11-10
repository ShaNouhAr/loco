"""Transmission des actions aux agents : rien ne part après un redémarrage, les actions attendent le retour du poste,
et une action « transmise » que l'agent n'a jamais démarrée repart au lieu d'échouer des heures plus tard."""
import datetime
import json
import unittest

from test_fleet import Agent, FleetTest, LINUX, WINDOWS, device
from loco import fleet
from loco.db import transaction


class DeliveryTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.agent = Agent(LINUX, "192.168.43.60")
        self.agent.enroll()
        self.dev = device(self.agent.uid)["id"]

    def queue(self, action, **params):
        with transaction() as conn:
            return conn.execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by) VALUES (?,?,?,?,'admin')",
                                (self.dev, action, json.dumps(params), fleet._now())).lastrowid

    def commands(self):
        with transaction() as conn:
            return {r["id"]: r for r in conn.execute("SELECT * FROM device_commands WHERE device_id=? ORDER BY id", (self.dev,))}

    def states(self):
        return [c["state"] for c in self.commands().values()]

    def checkin(self, **extra):
        """Contact d'un agent actuel : il annonce son démarrage et les actions qu'il tient encore (aucune)."""
        data = dict(self.agent.ident, holding=[], boot=1794300000)
        data.update(extra)
        answer = self.agent.post("/agent/api/checkin", data, self.agent.token)
        self.assertEqual(answer.status_code, 200)
        return answer.get_json()

    def old_checkin(self):
        return self.agent.checkin().get_json()                         # agent d'avant : ni « holding » ni « boot »

    def age(self, column, seconds, table="device_commands"):
        """Vieillit un horodatage : la base garde l'heure à la seconde, les essais n'attendent pas."""
        with transaction() as conn:
            for row in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                stamp, _sep, rest = (row[1] or "").partition("|")
                if stamp:
                    moved = (datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (moved + _sep + rest, row[0]))


class TestNothingAfterAReboot(DeliveryTest):
    def test_a_reboot_ends_the_batch_and_the_rest_waits_for_the_device_to_come_back(self):
        """Cas remonté d'une instance : redémarrage puis mise à jour mis en file avant le contact. Les deux partaient
        dans la même réponse, l'agent redémarrait et la mise à jour restait « transmise » six heures, puis échouait."""
        reboot, update = self.queue("reboot"), self.queue("update")
        self.assertEqual([c["action"] for c in self.checkin()["commands"]], ["reboot"])
        self.assertEqual(self.states(), ["sent", "queued"])
        self.agent.result(reboot, "running")
        self.assertEqual(self.checkin(holding=[reboot])["commands"], [])   # redémarrage en cours chez l'agent : rien de plus
        self.assertEqual(self.states(), ["running", "queued"])
        self.agent.result(reboot, "success", "Redémarrage dans une minute.")
        self.assertTrue(device(self.agent.uid)["reboot_pending"].endswith("|1794300000"))
        for _contact in range(3):                                       # la minute avant l'arrêt du poste : la mise à jour attend
            answer = self.checkin()
            self.assertEqual(answer["commands"], [])
            self.assertEqual(answer["interval"], fleet.ACTIVE_INTERVAL)
        self.assertEqual(self.states(), ["success", "queued"])
        answer = self.checkin(boot=1794300150)                          # le poste a redémarré : elle part
        self.assertEqual([c["id"] for c in answer["commands"]], [update])
        self.assertEqual(device(self.agent.uid)["reboot_pending"], "")
        self.assertEqual(device(self.agent.uid)["boot"], 1794300150)

    def test_an_update_that_may_reboot_ends_the_batch_too(self):
        update, script = self.queue("update", reboot=True), self.queue("scan")
        self.assertEqual([c["id"] for c in self.checkin()["commands"]], [update])
        self.agent.result(update, "success", "ok\nRedémarrage dans une minute.", inventory={"updates": [], "reboot_required": True})
        self.assertEqual(self.checkin()["commands"], [])
        self.assertEqual([c["id"] for c in self.checkin(boot=1794300200)["commands"]], [script])

    def test_an_update_that_does_not_reboot_lets_the_rest_follow_at_once(self):
        for label, params, inventory in (("sans redémarrage demandé", {}, {"updates": [], "reboot_required": True}),
                                         ("redémarrage demandé mais inutile", {"reboot": True}, {"updates": [], "reboot_required": False})):
            with self.subTest(label):
                update, scan = self.queue("update", **params), self.queue("scan")
                sent = [c["id"] for c in self.checkin()["commands"]]
                self.agent.result(update, "success", "ok", inventory=inventory)
                if scan in sent:
                    self.agent.result(scan, "success")
                else:
                    self.assertEqual([c["id"] for c in self.checkin()["commands"]], [scan])
                    self.agent.result(scan, "success")
                self.assertEqual(device(self.agent.uid)["reboot_pending"], "")

    def test_a_failed_update_never_holds_the_device(self):
        update = self.queue("update", reboot=True)
        self.checkin()
        self.agent.result(update, "failed", "E: dpkg", inventory={"updates": [], "reboot_required": True})
        self.assertEqual(device(self.agent.uid)["reboot_pending"], "")

    def test_without_the_boot_time_the_wait_ends_by_itself(self):
        """Agents d'avant (et Windows) : pas d'instant de démarrage, les actions repartent après REBOOT_HOLD."""
        reboot, update = self.queue("reboot"), self.queue("update")
        self.assertEqual([c["id"] for c in self.old_checkin()["commands"]], [reboot])
        self.agent.result(reboot, "success")
        self.assertEqual(self.old_checkin()["commands"], [])
        self.age("reboot_pending", fleet.REBOOT_HOLD - 20, table="devices")
        self.assertEqual(self.old_checkin()["commands"], [])
        self.age("reboot_pending", 30, table="devices")
        self.assertEqual([c["id"] for c in self.old_checkin()["commands"]], [update])
        self.assertEqual(device(self.agent.uid)["reboot_pending"], "")

    def test_a_cancelled_reboot_does_not_block_the_device_for_ever(self):
        reboot, update = self.queue("reboot"), self.queue("update")
        self.checkin()
        self.agent.result(reboot, "success")
        self.age("reboot_pending", fleet.REBOOT_HOLD + 5, table="devices")     # « shutdown -c » sur le poste : il n'a jamais redémarré
        self.assertEqual([c["id"] for c in self.checkin()["commands"]], [update])

    def test_the_remote_channel_does_not_call_the_agent_back_while_it_waits(self):
        reboot = self.queue("reboot")
        self.queue("update")
        self.checkin()
        poll = lambda: self.agent.post("/agent/api/remote", {"sessions": []}, self.agent.token).get_json()
        self.assertNotIn("checkin", poll())                             # rien d'autre à prendre tant que le redémarrage tourne
        self.agent.result(reboot, "success")
        self.assertNotIn("checkin", poll())                             # redémarrage annoncé : inutile de rappeler l'agent
        self.age("reboot_pending", fleet.REBOOT_HOLD + 5, table="devices")
        self.assertTrue(poll().get("checkin"))

    def test_removal_still_ends_the_batch(self):
        self.queue("uninstall")
        self.queue("scan")
        self.assertEqual([c["action"] for c in self.checkin()["commands"]], ["uninstall"])


class TestLostCommands(DeliveryTest):
    def test_a_command_the_agent_never_started_is_sent_again(self):
        scan = self.queue("scan")
        self.assertEqual([c["id"] for c in self.checkin()["commands"]], [scan])        # réponse perdue, agent arrêté…
        answer = self.checkin()
        self.assertEqual([c["id"] for c in answer["commands"]], [scan])                # … elle repart au contact suivant
        self.assertEqual((self.commands()[scan]["state"], self.commands()[scan]["attempts"]), ("sent", 2))
        self.agent.result(scan, "running")
        self.agent.result(scan, "success", "Inventaire transmis.")
        self.assertEqual(self.checkin()["commands"], [])
        self.assertEqual(self.states(), ["success"])

    def test_it_is_not_sent_for_ever(self):
        scan = self.queue("scan")
        for attempt in range(fleet.MAX_ATTEMPTS):
            self.assertEqual([c["id"] for c in self.checkin()["commands"]], [scan], attempt)
        self.assertEqual(self.checkin()["commands"], [])
        command = self.commands()[scan]
        self.assertEqual((command["state"], command["attempts"]), ("failed", fleet.MAX_ATTEMPTS))
        self.assertIn("sans jamais la démarrer", command["output"])

    def test_a_running_command_the_agent_no_longer_holds_is_settled_at_once(self):
        update = self.queue("update")
        self.checkin()
        self.agent.result(update, "running")
        self.checkin()                                                  # l'agent revient sans elle : poste éteint pendant l'action
        command = self.commands()[update]
        self.assertEqual(command["state"], "failed")
        self.assertIn("Action interrompue", command["output"])
        self.assertTrue(command["finished_at"])
        self.agent.result(update, "success", "42 paquets mis à jour.")  # un résultat tardif reste pris en compte
        self.assertEqual(self.commands()[update]["state"], "success")

    def test_what_the_agent_says_it_still_holds_is_left_alone(self):
        first, second = self.queue("scan"), self.queue("scan", again=1)
        self.checkin()
        self.agent.result(first, "running")
        self.assertEqual(self.checkin(holding=[first, second, "x", True, None])["commands"], [])
        self.assertEqual(self.states(), ["running", "sent"])
        self.assertEqual([c["attempts"] for c in self.commands().values()], [1, 1])

    def test_agents_that_say_nothing_keep_the_long_timeout(self):
        """Agents d'avant 2.146.2 et application Android (qui exécute en parallèle de ses contacts) : rien ne change."""
        scan = self.queue("scan")
        self.assertEqual([c["id"] for c in self.old_checkin()["commands"]], [scan])
        self.assertEqual(self.old_checkin()["commands"], [])
        self.assertEqual(self.states(), ["sent"])
        self.age("sent_at", fleet.SENT_TIMEOUT_HOURS * 3600 + 60)
        self.old_checkin()
        self.assertEqual(self.commands()[scan]["output"], "Pas de réponse du poste.")

    def test_a_malformed_announcement_is_ignored(self):
        scan = self.queue("scan")
        self.checkin()
        for bad in ("tout", {"a": 1}, 7, None):
            self.checkin(holding=bad, boot=bad if bad != 7 else True)
        self.checkin(boot=-4)
        self.checkin(boot=1 << 50)
        self.assertEqual(self.commands()[scan]["state"], "sent")
        self.assertEqual(device(self.agent.uid)["boot"], 1794300000)

    def test_a_wake_request_is_not_an_agent_command(self):
        with transaction() as conn:
            wake = conn.execute("INSERT INTO device_commands (device_id, action, state, created_at, sent_at, created_by) VALUES (?,'wake','sent',?,?,'admin')",
                                (self.dev, fleet._now(), fleet._now())).lastrowid
        self.checkin()
        self.assertNotEqual(self.commands().get(wake, {"state": "sent"})["state"], "queued")

    def test_windows_agents_follow_the_same_rules(self):
        pc = Agent(WINDOWS, "192.168.43.50")
        pc.enroll()
        with transaction() as conn:
            scan = conn.execute("INSERT INTO device_commands (device_id, action, created_at, created_by) VALUES (?,'scan',?,'admin')",
                                (device(pc.uid)["id"], fleet._now())).lastrowid
        data = dict(pc.ident, holding=[])
        self.assertEqual([c["id"] for c in pc.post("/agent/api/checkin", data, pc.token).get_json()["commands"]], [scan])
        self.assertEqual([c["id"] for c in pc.post("/agent/api/checkin", data, pc.token).get_json()["commands"]], [scan])


if __name__ == "__main__":
    unittest.main()
