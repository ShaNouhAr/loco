"""Postes réservés : un compte se réserve un appareil ; les autres comptes doivent saisir son mot de passe pour le
terminal, le bureau à distance et les fichiers (et sur Android l'écran adb), le reste leur restant ouvert. Mot de passe
haché et borné en essais, accès ouvert une demi-heure et refermé par un changement, sessions des autres fermées,
outils MCP des autres comptes refusés, réservation levée avec le compte qui la tient."""
import datetime
import json
import time
import unittest

import test_portal as fixture
from test_fleet import LINUX, Agent, device
from test_mcp import Client, create
from test_mcp_remote import RemoteTest
from loco import auth, features, fleet, permissions, reservations
from loco.db import db, transaction

OWNER_PW = "Poste-de-Lucie-2026"
OTHER_PW = "Collegue-Secret-2026"


def account(name, role="admin"):
    with transaction() as conn:
        conn.execute("DELETE FROM users WHERE username=?", (name,))
        auth.create_user(conn, name, OTHER_PW, role, must_change=False)
    fixture.reset_failures()
    browser = fixture.Browser()
    assert browser.login(name, OTHER_PW).status_code == 302
    return browser


class ReservationTest(RemoteTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM device_reservations")
            conn.execute("DELETE FROM device_unlock_failures")
            conn.execute("DELETE FROM users WHERE username LIKE 'rv-%'")
        self.addCleanup(self.cleanup)
        self.host = self.device()
        with transaction() as conn:
            self.dev_id = conn.execute("SELECT id FROM devices WHERE hostname=? AND state='active' ORDER BY id DESC",
                                       (self.host,)).fetchone()[0]
        self.page = f"/fleet/{self.dev_id}"
        self.other = account("rv-collegue")

    def cleanup(self):
        with transaction() as conn:
            conn.execute("DELETE FROM device_reservations")
            conn.execute("DELETE FROM users WHERE username LIKE 'rv-%'")

    def reserve(self, browser=None, password=OWNER_PW, confirm=None):
        return (browser or self.admin).post(f"{self.page}/reservation", {"password": password, "confirm": confirm or password},
                                            page=self.page)

    def open(self, browser, kind="terminal", **extra):
        return browser.post(f"{self.page}/session", dict(kind=kind, **extra), page=self.page)

    def unlock(self, browser, password, **extra):
        return browser.c.post(f"{self.page}/reservation/unlock", data=dict(csrf=browser.token(self.page), password=password, **extra),
                              headers={"X-Requested-With": "fetch"})

    def row(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM device_reservations WHERE device_id=?", (self.dev_id,)).fetchone()


class TestReserving(ReservationTest):
    def test_the_right_and_a_good_password_are_needed(self):
        operator = account("rv-operateur", role="operator")                 # pas le droit de réserver
        self.assertEqual(self.reserve(operator).status_code, 403)
        self.reserve(password="court")
        self.reserve(password=OWNER_PW, confirm=OWNER_PW + "x")
        self.assertIsNone(self.row())
        self.assertEqual(self.reserve().status_code, 302)
        row = self.row()
        self.assertNotIn(OWNER_PW, row["password_hash"])                    # jamais gardé en clair
        page = self.admin.c.get(self.page).get_data(as_text=True)
        self.assertIn("Réservé pour vous", page)
        self.assertIn("Libérer ce poste", page)
        # Un autre ne peut ni la reprendre, ni la libérer.
        self.reserve(self.other, password=OTHER_PW)
        self.assertEqual(self.row()["user_id"], row["user_id"])
        self.other.post(f"{self.page}/reservation/release", {}, page=self.page)
        self.assertIsNotNone(self.row())
        listing = self.other.c.get("/fleet").get_data(as_text=True)
        self.assertIn("reserve-mark", listing)
        self.assertIn("Réservé par admin", listing)

    def test_the_owner_opens_everything_without_a_password(self):
        self.reserve()
        self.assertEqual(self.open(self.admin).status_code, 302)
        self.assertIn("sid", self.open(self.admin, "files").get_json())

    def test_release(self):
        self.reserve()
        self.admin.post(f"{self.page}/reservation/release", {}, page=self.page)
        self.assertIsNone(self.row())
        self.assertEqual(self.open(self.other).status_code, 302)

    def test_lifted_with_the_owner_account(self):
        owner = account("rv-proprietaire")
        self.reserve(owner, password=OWNER_PW)
        self.assertIsNotNone(self.row())
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='rv-proprietaire'")
        self.assertIsNone(self.row())


class TestOtherAccounts(ReservationTest):
    def test_terminal_desktop_and_files_ask_for_the_password(self):
        self.reserve()
        page = self.other.c.get(self.page).get_data(as_text=True)
        self.assertIn("Réservé par admin", page)
        self.assertIn('id="device-unlock"', page)
        self.assertIn("reserve.js", page)
        gate = self.open(self.other)
        self.assertEqual(gate.status_code, 403)
        self.assertIn('name="password"', gate.get_data(as_text=True))
        self.assertEqual(self.open(self.other, "desktop").status_code, 403)
        files = self.open(self.other, "files")
        self.assertEqual((files.status_code, files.get_json()["code"]), (403, "reserved"))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE device_id=?", (self.dev_id,)).fetchone()[0], 0)

    def test_the_rest_stays_open(self):
        self.reserve()
        r = self.other.post(f"{self.page}/action", {"action": "scan"}, page=self.page)
        self.assertIn(r.status_code, (200, 302))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM device_commands WHERE device_id=? AND action='scan'",
                                          (self.dev_id,)).fetchone()[0], 1)

    def test_the_password_opens_for_half_an_hour_then_closes(self):
        self.reserve()
        self.assertEqual(self.unlock(self.other, "mauvais").status_code, 403)
        opened = self.unlock(self.other, OWNER_PW)
        self.assertTrue(opened.get_json()["ok"])
        self.assertEqual(self.open(self.other).status_code, 302)
        self.assertIn("sid", self.open(self.other, "files").get_json())
        # Une demi-heure plus tard, le mot de passe est redemandé.
        with self.other.c.session_transaction() as s:
            s[reservations.SESSION_KEY] = {str(self.dev_id): [time.time() - 1, s[reservations.SESSION_KEY][str(self.dev_id)][1]]}
        self.assertEqual(self.open(self.other).status_code, 403)

    def test_the_gate_replays_the_opening(self):
        self.reserve()
        r = self.other.c.post(f"{self.page}/reservation/unlock", data={"csrf": self.other.token(self.page), "password": OWNER_PW,
                                                                        "kind": "terminal"})
        self.assertEqual(r.status_code, 307)                                   # même formulaire, vers l'ouverture
        self.assertTrue(r.headers["Location"].endswith(f"{self.page}/session"))
        again = self.other.c.post(r.headers["Location"], data={"csrf": self.other.token(self.page), "kind": "terminal",
                                                                "password": OWNER_PW})
        self.assertEqual(again.status_code, 302)
        self.assertIn("/fleet/session/", again.headers["Location"])
        wrong = self.other.c.post(f"{self.page}/reservation/unlock", data={"csrf": self.other.token(self.page), "password": "faux",
                                                                            "kind": "terminal"})
        self.assertEqual(wrong.status_code, 403)
        self.assertIn("Mot de passe incorrect", wrong.get_data(as_text=True))

    def test_tries_are_bounded(self):
        self.reserve()
        for _i in range(reservations.TRIES):
            self.assertEqual(self.unlock(self.other, "faux").status_code, 403)
        blocked = self.unlock(self.other, OWNER_PW)
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("Trop d'essais", blocked.get_json()["error"])
        with transaction() as conn:
            refused = conn.execute("SELECT COUNT(*) FROM audit WHERE action='parc.reservation.refus'").fetchone()[0]
        self.assertGreaterEqual(refused, reservations.TRIES + 1)

    def test_a_new_password_closes_the_others(self):
        self.reserve()
        self.unlock(self.other, OWNER_PW)
        self.assertEqual(self.open(self.other).status_code, 302)
        self.reserve(password=OWNER_PW + "-bis")                               # changé par le propriétaire
        with transaction() as conn:
            states = [r[0] for r in conn.execute("SELECT state FROM remote_sessions WHERE device_id=?", (self.dev_id,))]
        self.assertEqual(states, ["closed"])
        self.assertEqual(self.open(self.other).status_code, 403)               # l'accès ouvert par l'ancien est refermé

    def test_lock_again(self):
        self.reserve()
        self.unlock(self.other, OWNER_PW)
        self.other.post(f"{self.page}/reservation/lock", {}, page=self.page)
        self.assertEqual(self.open(self.other).status_code, 403)


class TestRestart(ReservationTest):
    """Le redémarrage aussi demande le mot de passe : bouton de la fiche, sélection, planification, outils MCP."""

    def commands(self, action):
        with transaction() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT params FROM device_commands WHERE device_id=? AND action=? "
                                                            "AND state='queued' ORDER BY id", (self.dev_id, action))]

    def test_the_restart_button_asks_for_the_password(self):
        self.reserve()
        self.assertIn("data-protected", self.other.c.get(self.page).get_data(as_text=True))
        self.other.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)
        self.other.post(f"{self.page}/action", {"action": "update", "reboot": "1"}, page=self.page)
        self.assertEqual((self.commands("reboot"), self.commands("update")), ([], []))
        self.other.post(f"{self.page}/action", {"action": "update"}, page=self.page)        # sans redémarrage : permis
        self.assertEqual(self.commands("update"), [{"reboot": False}])
        self.unlock(self.other, OWNER_PW)
        self.other.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)
        self.assertEqual(len(self.commands("reboot")), 1)

    def test_reserving_cancels_the_restarts_others_asked_for(self):
        self.other.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)              # avant la réservation
        self.admin.post(f"{self.page}/action", {"action": "update", "reboot": "1"}, page=self.page)   # le futur propriétaire
        self.assertEqual(len(self.commands("reboot")), 1)
        self.reserve()
        self.assertEqual((self.commands("reboot"), self.commands("update")), ([], [{"reboot": True}]))
        with transaction() as conn:
            self.assertIn("redémarrage annulé", conn.execute("SELECT output FROM device_commands WHERE device_id=? AND action='reboot'",
                                                             (self.dev_id,)).fetchone()[0])
        with transaction() as conn:                                  # (une mise à jour avec redémarrage en attente bloque un redémarrage)
            conn.execute("DELETE FROM device_commands WHERE device_id=?", (self.dev_id,))
        self.unlock(self.other, OWNER_PW)
        self.other.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)          # mot de passe saisi : permis
        self.reserve(password=OWNER_PW + "-2")                       # changer le mot de passe ne l'annule pas
        self.assertEqual(len(self.commands("reboot")), 1)

    def test_the_right_to_restart_is_enough_to_enter_the_password(self):
        with transaction() as conn:
            conn.execute("DELETE FROM access_roles WHERE id='rv-reboot'")
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("rv-reboot", "Rv reboot", permissions.role_key("Rv reboot"),
                          json.dumps(sorted(permissions.expand({"fleet.view", "fleet.reboot"})))))

        def forget():
            with transaction() as conn:
                conn.execute("DELETE FROM users WHERE username='rv-operateur'")
                conn.execute("DELETE FROM access_roles WHERE id='rv-reboot'")
        self.addCleanup(forget)
        operator = account("rv-operateur", "rv-reboot")              # redémarrer, sans aucun accès à distance
        self.reserve()
        self.assertIn('id="device-unlock"', operator.c.get(self.page).get_data(as_text=True))
        self.assertEqual(self.unlock(operator, OWNER_PW).status_code, 200)
        operator.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)
        self.assertEqual(len(self.commands("reboot")), 1)
        self.assertEqual(self.open(operator).status_code, 403)                          # pas de terminal pour autant

    def test_the_owner_restarts(self):
        self.reserve()
        self.admin.post(f"{self.page}/action", {"action": "reboot"}, page=self.page)
        self.assertEqual(len(self.commands("reboot")), 1)

    def test_selections_leave_it_alone(self):
        self.reserve()
        csrf = self.other.token("/fleet")
        r = self.other.c.post("/fleet/reboot-selected", data={"csrf": csrf, "ids": [str(self.dev_id)]})
        self.assertEqual((r.get_json()["count"], self.commands("reboot")), (0, []))
        self.other.c.post("/fleet/update-selected", data={"csrf": csrf, "ids": [str(self.dev_id)], "reboot": "1"})
        self.assertEqual(self.commands("update"), [{"reboot": False}])                     # mis à jour, sans redémarrer

    def test_waves_update_it_without_restarting(self):
        with transaction() as conn:
            conn.execute("UPDATE devices SET updates=3 WHERE id=?", (self.dev_id,))
            conn.execute("DELETE FROM device_groups WHERE name='rv-vagues'")
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('rv-vagues', '')").lastrowid
            conn.execute("INSERT INTO device_group_members (group_id, device_id) VALUES (?,?)", (group, self.dev_id))

        def forget():
            with transaction() as conn:
                conn.execute("DELETE FROM device_groups WHERE id=?", (group,))
        self.addCleanup(forget)
        self.reserve()
        self.unlock(self.other, OWNER_PW)                  # même saisi : les vagues partent plus tard, sans redémarrer
        r = self.other.post("/fleet/update-all", {"group": str(group), "mode": "waves", "wave_size": "5", "wave_delay": "0",
                                                  "wave_stop": "1", "reboot": "1"}, page="/fleet")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            params = [json.loads(p[0]) for p in conn.execute("SELECT params FROM device_commands WHERE device_id=? AND action='update'",
                                                             (self.dev_id,))]
        self.assertEqual([p.get("reboot") for p in params], [False])
        self.assertIn("mis à jour sans redémarrer", self.other.c.get(r.headers["Location"]).get_data(as_text=True))

    def test_schedules_only_restart_it_for_its_owner(self):
        mine = "FROM schedules WHERE created_by='rv-collegue'"          # la base des tests en garde d'autres modules
        with transaction() as conn:
            conn.execute("DELETE " + mine)
            conn.execute("DELETE FROM device_groups WHERE name='rv-groupe'")
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('rv-groupe', '')").lastrowid
            conn.execute("INSERT INTO device_group_members (group_id, device_id) VALUES (?,?)", (group, self.dev_id))

        def forget():
            with transaction() as conn:
                conn.execute("DELETE " + mine)
                conn.execute("DELETE FROM device_groups WHERE id=?", (group,))
        self.addCleanup(forget)
        self.reserve()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        named = {"action": "reboot", "target": "selection", "device": str(self.dev_id), "recurrence": "once",
                 "at_time": "02:00", "start_date": tomorrow}
        self.other.post("/fleet/schedules/save", named, page="/fleet/schedules")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) " + mine).fetchone()[0], 0)      # poste nommé : refusée
        in_group = dict(named, device_group=str(group))
        in_group.pop("device")
        self.other.post("/fleet/schedules/save", in_group, page="/fleet/schedules")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) " + mine).fetchone()[0], 1)      # un groupe : admise
            conn.execute("UPDATE schedules SET next_run=? WHERE created_by='rv-collegue'",
                         ((datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),))
        self.unlock(self.other, OWNER_PW)               # même ouvert dans son navigateur : la planification n'en profite pas
        self.other.c.get("/fleet/schedules")             # page des planifications : planifications dues déclenchées
        self.assertEqual(self.commands("reboot"), [])                                     # mis de côté
        with transaction() as conn:
            result = conn.execute("SELECT last_result " + mine).fetchone()[0]
        self.assertEqual(result, "1 réservé sans redémarrage")                            # et non « déjà en attente »
        with fixture.loco.app.test_request_context(headers={"Accept-Language": "en"}):
            from loco import schedules
            self.assertEqual(schedules.result_text("3 postes · 2 réservés sans redémarrage"), "3 devices · 2 reserved, not restarted")
            self.assertEqual(schedules.result_text("1 poste(s)"), "1 device")                # résultats d'avant

    def test_queue_never_restarts_it_for_someone_else(self):
        self.reserve()
        with fixture.loco.app.app_context():                     # hors requête : planification, vague
            dev = db().execute("SELECT * FROM devices WHERE id=?", (self.dev_id,)).fetchone()
            self.assertFalse(fleet.queue(dev, "reboot", {}, user="planification"))
            self.assertTrue(fleet.queue(dev, "update", {"reboot": True}, user="rv-collegue"))
            self.assertTrue(fleet.queue(dev, "reboot", {}, user="admin"))                    # son propriétaire
            db().commit()
        self.assertEqual(self.commands("update"), [{"reboot": False}])
        self.assertEqual(len(self.commands("reboot")), 1)

    def test_assistants_of_others_do_not_restart_it(self):
        token, _r = create(self.other, "Redemarrage", ["reboot_device", "update_device", "update_devices"])
        theirs = Client(token)
        self.reserve()
        refused = theirs.call("reboot_device", device=self.host)
        self.assertTrue(refused["isError"])
        self.assertIn("reserved by admin", refused["content"][0]["text"])
        self.assertIn("reboot=false", theirs.call("update_device", device=self.host, reboot=True)["content"][0]["text"])
        many = theirs.data("update_devices", devices=[self.host], reboot=True, only_if_needed=False)
        self.assertEqual(many["updated_without_reboot"], [self.host])
        self.assertEqual(self.commands("reboot"), [])


class TestAssistants(ReservationTest):
    def test_remote_tools_of_other_accounts_are_refused(self):
        token, _r = create(self.other, "Collegue", ["terminal_run", "list_files"])
        theirs = Client(token)
        self.assertEqual(theirs.data("terminal_run", device=self.host, command="echo avant")["output"], "avant")
        self.reserve()
        refused = theirs.call("terminal_run", device=self.host, command="echo ok")
        self.assertTrue(refused["isError"])
        self.assertIn("reserved by admin", refused["content"][0]["text"])
        self.assertTrue(theirs.call("list_files", device=self.host, path="/")["isError"])
        mine = self.client(["terminal_run"])                                    # accès MCP du propriétaire
        self.assertEqual(mine.data("terminal_run", device=self.host, command="echo ok")["output"], "ok")


class TestAndroidScreen(ReservationTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["android"]'))
        self.addCleanup(self.feature_off)

    def feature_off(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))

    def test_the_adb_screen_is_protected(self):
        tablet = Agent(dict(LINUX, hostname="tablette-lucie", os={"family": "android", "name": "Android 15"}), "192.168.43.70")
        tablet.enroll()
        dev_id = device(tablet.uid)["id"]
        self.admin.post(f"/fleet/{dev_id}/reservation", {"password": OWNER_PW, "confirm": OWNER_PW}, page=f"/fleet/{dev_id}")
        with self.other.c.get(f"/fleet/{dev_id}/adb/screen") as screen:
            self.assertEqual(screen.status_code, 403)
            self.assertIn("réservé par admin", screen.get_data(as_text=True))       # refus de la réservation, pas d'adb
        moved = self.other.c.post(f"/fleet/{dev_id}/adb/input", data={"csrf": self.other.token(f"/fleet/{dev_id}"), "action": "tap"},
                                  headers={"X-Requested-With": "fetch"})
        self.assertEqual((moved.status_code, moved.get_json()["code"]), (403, "reserved"))


if __name__ == "__main__":
    unittest.main()
