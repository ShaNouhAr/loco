"""Transmission des actions aux agents : rien ne part après un redémarrage, les actions attendent le retour du poste,
et une action « transmise » que l'agent n'a jamais démarrée repart au lieu d'échouer des heures plus tard."""
import datetime
import json
import unittest

from test_fleet import Agent, FleetTest, LINUX, WINDOWS, device
from loco import config, fleet
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


class TestAgentUpdate(DeliveryTest):
    """« Mettre à jour l'agent » depuis la fiche d'un poste : l'agent se met à jour seul en lisant la version du serveur ;
    loco le fait venir tout de suite et suit le résultat, sans rien lui transmettre comme commande."""
    OLD = "2.144.0"

    def setUp(self):
        super().setUp()
        self.agent.ident["agent_version"] = self.OLD
        self.checkin()
        self.page = f"/fleet/{self.dev}"

    def ask(self, browser=None):
        return (browser or self.admin).post(f"{self.page}/action", {"action": "agent-update"}, page=self.page)

    def html(self, browser=None):
        import html
        return html.unescape((browser or self.admin).c.get(self.page).get_data(as_text=True))

    def test_the_card_tells_the_agent_is_behind_and_offers_the_update(self):
        page = self.html()
        self.assertIn("agent à mettre à jour", page)
        self.assertIn(f"Mettre à jour l'agent ({self.OLD} → {config.VERSION})", page)
        self.assertIn(f"· ce serveur est en {config.VERSION}", page)
        self.agent.ident["agent_version"] = config.VERSION
        self.checkin()
        page = self.html()
        self.assertNotIn("agent à mettre à jour", page)
        self.assertNotIn("Mettre à jour l'agent", page)
        self.assertIn("· à jour", page)

    def test_the_request_is_followed_until_the_agent_announces_the_server_version(self):
        self.assertEqual(self.ask().status_code, 302)
        command = list(self.commands().values())[-1]
        self.assertEqual((command["action"], command["state"], json.loads(command["params"])), ("agent-update", "queued", {"from": self.OLD}))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT detail FROM audit WHERE action='parc.agent-update' ORDER BY id DESC").fetchone()[0],
                             f"srv-web : {self.OLD} → {config.VERSION}")
        self.assertIn("Mise à jour de l'agent", self.html())                 # visible dans les actions en attente
        poll = self.agent.post("/agent/api/remote", {"sessions": []}, self.agent.token).get_json()
        self.assertTrue(poll.get("checkin"))                               # l'agent est appelé tout de suite par le canal distant
        answer = self.checkin()
        self.assertEqual(answer["commands"], [])                           # jamais transmise comme commande à l'agent
        self.assertEqual(answer["agent_version"], config.VERSION)          # c'est cette version qui le fait se mettre à jour
        self.assertEqual(answer["interval"], fleet.ACTIVE_INTERVAL)        # et il revient vite l'annoncer
        self.assertEqual(self.states()[-1], "running")
        self.assertEqual(self.checkin()["commands"], [])                   # contacts suivants, encore l'ancienne version
        self.assertEqual(self.states()[-1], "running")                     # ni rejouée ni soldée par « holding »
        self.agent.ident["agent_version"] = config.VERSION
        self.checkin()
        command = list(self.commands().values())[-1]
        self.assertEqual((command["state"], command["output"]), ("success", f"Agent mis à jour : {self.OLD} → {config.VERSION}."))
        self.assertTrue(command["finished_at"])

    def test_asking_twice_or_for_an_agent_already_current_changes_nothing(self):
        self.ask()
        self.ask()
        self.assertEqual([c["action"] for c in self.commands().values()], ["agent-update"])
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")
        self.agent.ident["agent_version"] = config.VERSION
        self.checkin()
        self.ask()
        self.assertEqual(self.commands(), {})
        self.assertIn("déjà à jour", self.html())

    def test_an_agent_updated_before_its_next_contact_settles_the_request(self):
        self.ask()
        self.agent.ident["agent_version"] = config.VERSION                  # il s'était mis à jour seul entre-temps
        self.checkin()
        command = list(self.commands().values())[-1]
        self.assertEqual((command["state"], command["output"]), ("success", f"Agent mis à jour : {self.OLD} → {config.VERSION}."))

    def test_an_agent_that_does_not_follow_fails_with_where_to_look(self):
        for family, ident, hint in (("debian", LINUX, "journalctl -u loco-client"), ("windows", WINDOWS, "update.log")):
            with self.subTest(family):
                agent = Agent(ident, "192.168.43.77", uid=("%032x" % hash(family))[-32:].replace("-", "0"))
                agent.ident["agent_version"] = self.OLD
                agent.enroll()
                dev = device(agent.uid)["id"]
                self.admin.post(f"/fleet/{dev}/action", {"action": "agent-update"}, page=f"/fleet/{dev}")
                agent.post("/agent/api/checkin", dict(agent.ident, holding=[]), agent.token)
                self.age("sent_at", fleet.AGENT_UPDATE_MINUTES * 60 - 30)
                agent.post("/agent/api/checkin", dict(agent.ident, holding=[]), agent.token)
                with transaction() as conn:
                    command = conn.execute("SELECT state, output FROM device_commands WHERE device_id=? ORDER BY id DESC", (dev,)).fetchone()
                self.assertEqual(command["state"], "running")
                self.age("sent_at", 60)
                agent.post("/agent/api/checkin", dict(agent.ident, holding=[]), agent.token)
                with transaction() as conn:
                    command = conn.execute("SELECT state, output FROM device_commands WHERE device_id=? ORDER BY id DESC", (dev,)).fetchone()
                self.assertEqual(command["state"], "failed")
                self.assertIn(f"resté en {self.OLD}", command["output"])
                self.assertIn(hint, command["output"])

    def test_a_device_that_never_comes_back_is_settled_by_the_background_pass(self):
        self.ask()
        self.checkin()
        self.age("sent_at", fleet.AGENT_UPDATE_MINUTES * 60 + 60)
        with transaction() as conn:
            fleet.expire_commands(conn)                                     # passe de fond, sans contact du poste
        self.assertEqual(self.states()[-1], "failed")

    def test_it_never_blocks_nor_replaces_the_other_actions(self):
        self.ask()
        scan = self.queue("scan")
        self.assertEqual([c["id"] for c in self.checkin()["commands"]], [scan])
        self.agent.result(scan, "success")
        self.assertEqual(self.states(), ["running", "success"])

    def test_the_right_to_update_devices_is_required(self):
        from test_permissions import TestPermissions
        self.addCleanup(TestPermissions.cleanup_accounts, self)
        viewer = TestPermissions.browser(self, "lecture-agent", "viewer")
        self.assertNotIn("Mettre à jour l'agent", self.html(viewer))
        self.assertIn("agent à mettre à jour", self.html(viewer))            # l'état reste visible
        self.assertEqual(viewer.post(f"{self.page}/action", {"action": "agent-update"}, page="/account").status_code, 403)
        self.assertEqual(self.commands(), {})


class TestNothingTwice(DeliveryTest):
    """Une mise à jour ou un redémarrage déjà lancés sont montrés comme tels et ne se relancent pas."""

    UPDATES = {"updates": [{"name": "curl", "version": "8.14"}], "reboot_required": True}

    def setUp(self):
        super().setUp()
        self.agent.checkin(self.UPDATES)

    def list(self):
        return self.admin.c.get("/fleet").get_data(as_text=True)

    def row_actions(self):
        page = self.list()
        row = page[page.index('<tbody data-refresh="rows">'):page.index("</tbody>")]
        return row[row.index('class="right nowrap card-action"'):]

    def card(self):
        return self.admin.c.get(f"/fleet/{self.dev}").get_data(as_text=True)

    def act(self, action, **extra):
        return self.admin.post(f"/fleet/{self.dev}/action", dict({"action": action}, **extra), page=f"/fleet/{self.dev}")

    def test_an_update_is_shown_waiting_then_running_and_cannot_be_asked_again(self):
        self.assertIn(">Mettre à jour</button>", self.row_actions())
        self.assertIn("Tout mettre à jour (1)", self.list())
        self.act("update")
        actions, page = self.row_actions(), self.list()
        self.assertIn("mise à jour en attente", actions)                    # le poste ne l'a pas encore prise
        self.assertNotIn(">Mettre à jour</button>", actions)
        self.assertIn("Mises à jour en cours (1)", page)                    # et « Tout mettre à jour » ne le recompte pas
        self.assertNotIn("Tout mettre à jour", page[page.index('data-refresh="actions"'):page.index('data-refresh="states"')])
        self.assertNotIn('<dialog id="update-all"', page)
        self.assertIn('class="btn primary loading" disabled><span class="spin"></span>Mise à jour en attente', self.card())
        update = next(iter(self.commands()))
        self.checkin()                                                      # le poste la prend
        self.agent.result(update, "running")
        self.assertIn('<span class="busy-note"', self.row_actions())
        self.assertIn("mise à jour en cours", self.row_actions())
        self.assertIn('loading" disabled><span class="spin"></span>Mise à jour en cours', self.card())
        self.assertNotIn("data-update-select", self.card())                 # ni par la sélection de paquets
        # Rien ne s'ajoute : ni par le bouton du poste (même avec redémarrage), ni par « Tout mettre à jour », ni par la sélection.
        self.act("update")
        self.act("update", reboot="1")
        self.admin.post("/fleet/update-all", {"reboot": "1"}, page="/fleet")
        self.admin.post("/fleet/update-selected", {"ids": str(self.dev)}, page="/fleet")
        self.assertEqual([c["action"] for c in self.commands().values()], ["update"])
        self.agent.result(update, "success", inventory={"updates": [], "reboot_required": True})
        self.assertNotIn("busy-note", self.row_actions())                   # terminée : le poste retrouve ses boutons
        self.assertNotIn("Mises à jour en cours", self.list())

    def test_a_restart_stays_in_progress_until_the_machine_is_back(self):
        self.assertIn('aria-label="Redémarrer srv-web"', self.row_actions())
        self.act("reboot")
        reboot = next(iter(self.commands()))
        self.assertIn("redémarrage en attente", self.row_actions())
        self.checkin()
        self.agent.result(reboot, "success", "Redémarrage dans une minute.")
        # L'action est terminée, mais le poste n'a pas encore redémarré : toujours « en cours », plus de bouton.
        actions, page = self.row_actions(), self.list()
        self.assertIn('<span class="busy-note"><span class="spin"></span>redémarrage en cours</span>', actions)
        self.assertNotIn('aria-label="Redémarrer', actions)
        self.assertIn("reboot-note pending", page)
        card = self.card()
        self.assertIn('class="menu-item loading" disabled><span class="spin"></span>Redémarrage en cours', card)
        self.assertIn("le poste redémarre", card)
        self.act("reboot")                                                  # un second redémarrage est refusé
        self.assertEqual([c["action"] for c in self.commands().values()], ["reboot"])
        self.assertIn("déjà en cours", self.admin.c.get("/fleet").get_data(as_text=True))
        # Le poste revient (autre instant de démarrage) et n'a plus besoin de redémarrer : plus rien à montrer.
        self.checkin(boot=1794300150, inventory={"updates": [{"name": "curl", "version": "8.14"}], "reboot_required": False})
        self.assertNotIn("redémarrage", self.row_actions())
        self.assertNotIn("le poste redémarre", self.card())

    def test_the_list_signature_follows_what_is_in_progress(self):
        poll = lambda: self.admin.c.get("/fleet", headers={"X-Requested-With": "fetch"}).get_json()["signature"]   # noqa: E731
        before = poll()
        self.act("update")
        waiting = poll()
        self.assertNotEqual(before, waiting)
        self.checkin()
        self.agent.result(next(iter(self.commands())), "running")
        self.assertNotEqual(waiting, poll())                                # « en attente » → « en cours » : la liste se rafraîchit


if __name__ == "__main__":
    unittest.main()
