"""Terminal adb des appareils Android : appairage, connexion, relais d'un vrai shell par loco, outil MCP adb_shell.

Un faux adb (script) remplace l'outil de Google : il note ses arguments, lit le code d'appairage sur son entrée et
lance un vrai shell local à la place de « adb shell ». Le relais de loco (pseudo-terminal, paquets) est le vrai."""
import base64
import json
import os
import shutil
import stat
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

import test_portal as fixture
from test_android import ANDROID
from test_fleet import LINUX, Agent, FleetTest, device
from test_mcp import Client, create
from loco import adb, features, mcp_remote
from loco.db import transaction

FAKE = """#!/bin/sh
echo "$*" >> {log}
case "$1" in
  version) echo "Android Debug Bridge version 1.0.41"; echo "Version 35.0.2-test"; exit 0 ;;
  pair) read code; if [ "$code" = "123456" ]; then echo "Successfully paired to $2 [guid=adb-test]"; exit 0; fi
        echo "Failed: Wrong password or connection was dropped."; exit 1 ;;
  connect) echo "connected to $2"; exit 0 ;;
  disconnect) echo "disconnected $2"; exit 0 ;;
  -s) shift 2
      case "$1" in
        get-state) cat {state} 2>/dev/null || echo device; exit 0 ;;
        shell) shift
               if [ $# -eq 0 ]; then PS1='$ ' exec /bin/sh -i; fi
               case "$*" in
                 *"wm size"*) echo "Physical size: 1080x2400" ;;
                 *"dumpsys window"*) echo "    mDreamingLockscreen=$(cat {state}.lk 2>/dev/null || echo true)" ;;
                 *"getprop ro.product.manufacturer"*) echo Google ;;
                 *"getprop ro.product.brand"*) echo google ;;
                 *"input motionevent"*) echo false > {state}.lk ;;
                 *"input keyevent 66"*) echo false > {state}.lk ;;
                 *"pm grant"*) if [ -e {state}.oldapp ]; then
                       echo "Exception occurred while executing 'grant': java.lang.SecurityException: Package org.loco.agent has not requested permission android.permission.WRITE_SECURE_SETTINGS"; exit 255; fi ;;
                 *"appops set"*"ACCESS_RESTRICTED_SETTINGS"*) if [ -e {state}.android12 ]; then echo "Error: Unknown operation string: ACCESS_RESTRICTED_SETTINGS"; exit 255; fi ;;
                 *"settings get secure enabled_accessibility_services"*) cat {state}.a11y 2>/dev/null || echo null ;;
                 *"settings put secure enabled_accessibility_services"*) echo "$5" > {state}.a11y ;;
               esac
               exit 0 ;;
      esac ;;
esac
exit 1
"""


class AdbTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp(prefix="loco-adb-")
        self.log = os.path.join(self.folder, "args.log")
        self.state = os.path.join(self.folder, "state")
        fake = os.path.join(self.folder, "adb")
        with open(fake, "w") as f:
            f.write(FAKE.format(log=self.log, state=self.state))
        os.chmod(fake, stat.S_IRWXU)
        patch = mock.patch.dict(os.environ, {"LOCO_ADB": fake})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.folder, True)
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["android"]'))
            conn.execute("DELETE FROM remote_sessions")
            conn.execute("DELETE FROM android_adb")
        self.addCleanup(self.reset)

    def reset(self):
        for _ in range(100):                     # les relais se terminent quand leur session est fermée
            if not adb._WORKERS:
                break
            time.sleep(0.05)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))

    def phone(self, report=None, version="2.139.0"):
        a = Agent(dict(ANDROID, agent_version=version), "192.168.43.71", uid="d" * 32)
        a.enroll()
        data = dict(a.ident, adb=report if report is not None else {"developer": True, "wireless": True, "port": 41235, "ip": "192.168.43.71"})
        a.post("/agent/api/checkin", data, a.token)
        return a, device(a.uid)

    def args(self):
        with open(self.log) as f:
            return f.read()

    def pair(self, dev, code="123456"):
        return self.admin.post(f"/fleet/{dev['id']}/adb/pair", {"port": "37851", "code": code}, page=f"/fleet/{dev['id']}")

    def link(self, dev):
        with transaction() as conn:
            return conn.execute("SELECT * FROM android_adb WHERE device_id=?", (dev["id"],)).fetchone()

    def open_terminal(self, dev):
        r = self.admin.post(f"/fleet/{dev['id']}/session", {"kind": "adb"}, page=f"/fleet/{dev['id']}")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def io(self, sid, data="", seq=0, ack=0):
        form = {"ack": str(ack), "cols": "90", "rows": "24"}
        if data:
            form.update(seq=str(seq), data=base64.b64encode(data.encode()).decode())
        return self.admin.post(f"/fleet/session/{sid}/io", form, page="/account").get_json()

    def read_until(self, sid, text, ack=0, timeout=10):
        out, end = "", time.time() + timeout
        while time.time() < end:
            answer = self.io(sid, ack=ack)
            for p in answer["packets"]:
                out += base64.b64decode(p["data"]).decode("utf-8", "replace")
                ack = p["seq"]
            if text in out or answer["state"] == "closed":
                return out, ack, answer
            time.sleep(0.1)
        return out, ack, answer


class TestSetup(AdbTest):
    def test_the_report_of_the_app_is_bounded_and_kept(self):
        _a, dev = self.phone({"developer": 1, "wireless": True, "port": 99999, "ip": "pas une adresse", "extra": "x" * 9000})
        with fixture.loco.app.test_request_context():
            self.assertEqual(adb.report(dev), {"developer": True, "wireless": True, "port": 0, "ip": ""})

    def test_pairing_reads_the_code_on_stdin_never_in_the_arguments(self):
        _a, dev = self.phone()
        self.pair(dev, "654321")
        self.assertIsNone(self.link(dev)["paired_at"] or None)            # code refusé : rien d'enregistré
        self.pair(dev)
        row = self.link(dev)
        self.assertEqual((row["mode"], row["paired_by"]), ("wireless", "admin"))
        self.assertIn("pair 192.168.43.71:37851", self.args())
        self.assertNotIn("123456", self.args())
        self.assertNotIn("654321", self.args())
        page = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        self.assertIn('value="adb"', page)                                 # bouton qui ouvre le terminal adb

    def test_a_classic_port_and_forgetting(self):
        _a, dev = self.phone({"developer": False, "wireless": False, "port": 0, "ip": ""})
        self.admin.post(f"/fleet/{dev['id']}/adb/connect", {"host": "192.168.43.71", "port": "5555"}, page=f"/fleet/{dev['id']}")
        row = self.link(dev)
        self.assertEqual((row["mode"], row["port"], row["error"]), ("tcp", 5555, ""))
        self.assertIn("connect 192.168.43.71:5555", self.args())
        self.admin.post(f"/fleet/{dev['id']}/adb/forget", page=f"/fleet/{dev['id']}")
        self.assertEqual(self.link(dev)["mode"], "wireless")
        self.assertEqual(self.link(dev)["port"], 0)

    def test_unauthorized_or_missing_adb_is_explained(self):
        _a, dev = self.phone({"developer": False, "wireless": False, "port": 0, "ip": ""})
        with open(self.state, "w") as f:
            f.write("unauthorized\n")
        with mock.patch("loco.adb.time.sleep"):
            self.admin.post(f"/fleet/{dev['id']}/adb/connect", {"port": "5555"}, page=f"/fleet/{dev['id']}")
        self.assertIn("Autoriser le débogage", self.link(dev)["error"])
        with mock.patch("loco.adb.binary", return_value=""):
            r = self.admin.post(f"/fleet/{dev['id']}/session", {"kind": "adb"}, page=f"/fleet/{dev['id']}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("est pas installé sur ce serveur loco", r.get_data(as_text=True))

    def test_only_android_devices_and_only_with_the_feature(self):
        linux = Agent(LINUX, "192.168.43.72")
        linux.enroll()
        r = self.admin.post(f"/fleet/{device(linux.uid)['id']}/session", {"kind": "adb"}, page="/fleet")
        self.assertEqual(r.status_code, 400)
        _a, dev = self.phone()
        self.pair(dev)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
        r = self.admin.post(f"/fleet/{dev['id']}/session", {"kind": "adb"}, page=f"/fleet/{dev['id']}")
        self.assertEqual(r.status_code, 403)


class TestPairingFromTheDevice(AdbTest):
    """Appairage demandé par l'application de l'appareil : code saisi sur l'appareil, adresse = celle de l'appareil."""

    def ask(self, agent, **body):
        return agent.post("/agent/api/adb-pair", body, agent.token)

    def setUp(self):
        super().setUp()
        adb._SELF_PAIRING.clear()

    def phone(self, report=None, version="2.139.0"):
        """Appareil qui déclare l'adresse d'où il contacte loco (même réseau que le serveur)."""
        a = Agent(dict(ANDROID, agent_version=version, addresses=["192.168.43.71"]), "192.168.43.71", uid="d" * 32)
        a.enroll()
        a.post("/agent/api/checkin", dict(a.ident, adb={"developer": True, "wireless": True, "port": 41235, "ip": "192.168.43.71"}), a.token)
        return a, device(a.uid)

    def test_the_device_pairs_itself_with_its_own_address_only(self):
        a, dev = self.phone()
        r = self.ask(a, code="123456", port=37851)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        row = self.link(dev)
        self.assertEqual((row["mode"], row["paired_by"]), ("wireless", "depuis l'appareil"))
        self.assertTrue(row["paired_at"])
        self.assertIn("pair 192.168.43.71:37851", self.args())               # l'adresse d'où vient la demande
        self.assertNotIn("123456", self.args())                              # le code passe par l'entrée, jamais en argument
        with transaction() as conn:
            event = conn.execute("SELECT * FROM audit WHERE action='parc.adb.appairage' ORDER BY id DESC").fetchone()
        self.assertIn("depuis l'appareil", event["detail"])

    def test_wrong_code_bad_input_and_repeated_attempts(self):
        a, dev = self.phone()
        r = self.ask(a, code="654321", port=37851)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Appairage refusé", r.get_json()["error"])
        self.assertFalse(self.link(dev)["paired_at"])
        self.assertEqual(self.ask(a, code="123456", port=37851).status_code, 429)     # trop rapproché
        adb._SELF_PAIRING.clear()
        for body in ({"code": "12345", "port": 37851}, {"code": "123456", "port": 0}, {"code": "123456", "port": "37851"},
                     {"code": "123456", "port": True}, {"code": "123456"}):
            self.assertEqual(self.ask(a, **body).status_code, 400, body)

    def test_a_device_seen_through_a_router_cannot_be_paired_from_there(self):
        a = Agent(dict(ANDROID, addresses=["10.0.2.15"]), "192.168.43.99", uid="e" * 32)
        a.enroll()
        r = self.ask(a, code="123456", port=37851)
        self.assertEqual(r.status_code, 409)                                # adresse non déclarée par l'appareil
        self.assertNotIn("pair", self.args() if os.path.exists(self.log) else "")

    def test_only_an_authenticated_android_device(self):
        a, _dev = self.phone()
        self.assertEqual(a.post("/agent/api/adb-pair", {"code": "123456", "port": 37851}, "faux.jeton").status_code, 401)
        linux = Agent(LINUX, "192.168.43.73")
        linux.enroll()
        self.assertEqual(self.ask(linux, code="123456", port=37851).status_code, 404)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
        self.assertEqual(self.ask(a, code="123456", port=37851).status_code, 503)    # fonctionnalité Android coupée


class TestUpdateThroughAdb(AdbTest):
    """Appareil appairé : la mise à jour de l'application passe par adb (sans Play Protect sur l'appareil) ; sinon,
    ou si adb échoue, l'application se met à jour seule comme avant."""

    def setUp(self):
        super().setUp()
        adb._UPDATING.clear()
        self.apk = os.path.join(self.folder, "loco-agent.apk")
        with open(self.apk, "wb") as f:
            f.write(b"apk")
        patch = mock.patch("loco.fleet.agent_apk", return_value=(self.apk, "a" * 64))
        patch.start()
        self.addCleanup(patch.stop)

    def contact(self, agent):
        return agent.post("/agent/api/checkin", dict(agent.ident, adb={"developer": True, "wireless": True, "port": 41235,
                                                                        "ip": "192.168.43.71"}), agent.token).get_json()

    def wait(self):
        for _ in range(100):
            if not any(t.name == "loco-adb-update" for t in threading.enumerate()):
                return
            time.sleep(0.05)

    def test_a_paired_device_is_updated_through_adb_and_the_app_is_not_asked(self):
        a, dev = self.phone(version="2.150.0")
        self.pair(dev)
        answer = self.contact(a)
        self.assertNotIn("agent_apk", answer)                            # l'application ne la tente pas elle-même
        self.wait()
        self.assertIn(f"install -r {self.apk}", self.args())
        with transaction() as conn:
            event = conn.execute("SELECT * FROM audit WHERE action LIKE 'parc.agent.adb%' ORDER BY id DESC").fetchone()
        self.assertEqual(event["action"], "parc.agent.adb.echec")      # le faux adb ne sait pas installer
        self.assertIn("agent_apk", self.contact(a))                     # après un échec, l'application reprend la main

    def test_an_unpaired_device_updates_itself_as_before(self):
        a, _dev = self.phone(version="2.150.0")
        self.assertIn("agent_apk", self.contact(a))
        self.wait()
        self.assertNotIn("install", self.args() if os.path.exists(self.log) else "")

    def test_never_during_a_remote_session(self):
        a, dev = self.phone(version="2.150.0")
        self.pair(dev)
        with transaction() as conn:
            conn.execute("INSERT INTO remote_sessions (id, device_id, user_id, owner, pw_version, kind, state, created, browser_seen, expires) "
                         "VALUES ('s1', ?, (SELECT id FROM users WHERE username='admin'), 'o', 1, 'desktop', 'active', 0, 0, 0)", (dev["id"],))
        self.contact(a)
        self.wait()
        self.assertNotIn("install", self.args())


class TestPermanentScreen(AdbTest):
    """Appairage adb = autorisation donnée une fois : loco pose sur l'application loco de quoi montrer et piloter
    l'écran sans geste, et lui demande de garder le débogage sans fil joignable."""

    def ensure(self, dev):
        with fixture.loco.app.test_request_context():
            return adb.ensure(device(dev["uid"]))

    def prepared(self, dev):
        return json.loads(self.link(dev)["prepared"] or "{}")

    def test_pairing_then_connecting_grants_everything_once(self):
        with open(self.state + ".a11y", "w") as f:
            f.write("com.exemple/.Lecteur\n")                             # un service déjà activé par la personne
        _a, dev = self.phone(version="2.145.0")
        self.pair(dev)
        self.assertEqual(self.prepared(dev), {})                           # rien tant que loco n'a pas joint l'appareil
        self.assertEqual(self.ensure(dev), "192.168.43.71:41235")
        done = self.prepared(dev)
        self.assertEqual(done["done"], ["screen", "launch", "adb", "restricted", "battery", "control"])
        self.assertEqual(done["agent"], "2.145.0")
        log = self.args()
        for command in ("appops set org.loco.agent PROJECT_MEDIA allow", "appops set org.loco.agent SYSTEM_ALERT_WINDOW allow",
                        "pm grant org.loco.agent android.permission.WRITE_SECURE_SETTINGS",
                        "appops set org.loco.agent ACCESS_RESTRICTED_SETTINGS allow", "dumpsys deviceidle whitelist +org.loco.agent",
                        "settings put secure enabled_accessibility_services com.exemple/.Lecteur:org.loco.agent/org.loco.agent.InputService",
                        "settings put secure accessibility_enabled 1"):
            self.assertIn("-s 192.168.43.71:41235 shell " + command, log)
        with transaction() as conn:
            detail = conn.execute("SELECT detail FROM audit WHERE action='parc.adb.autorisations'").fetchone()[0]
        self.assertIn("screen, launch, adb, restricted, battery, control", detail)
        self.assertIn("Écran distant autorisé une fois pour toutes", self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True))
        self.ensure(dev)                                                   # déjà fait : rien n'est reposé
        self.assertEqual(self.args().count("PROJECT_MEDIA"), 1)

    def test_what_android_or_the_app_refuses_is_not_claimed(self):
        open(self.state + ".android12", "w").close()                       # pas de réglages restreints avant Android 13
        open(self.state + ".oldapp", "w").close()
        _a, dev = self.phone(version="2.145.0")
        self.pair(dev)
        self.ensure(dev)
        self.assertEqual(self.prepared(dev)["done"], ["screen", "launch", "battery", "control"])

    def test_an_older_app_is_prepared_once_it_is_updated(self):
        a, dev = self.phone()                                              # 2.139.0 : ne saurait rien en faire
        self.pair(dev)
        self.ensure(dev)
        self.assertEqual(self.prepared(dev), {})
        self.assertNotIn("PROJECT_MEDIA", self.args())
        a.ident["agent_version"] = "2.145.0"                               # l'application s'est mise à jour
        a.post("/agent/api/checkin", dict(a.ident, adb={"developer": True, "wireless": True, "port": 41235, "ip": "192.168.43.71"}), a.token)
        self.ensure(dev)
        self.assertIn("screen", self.prepared(dev)["done"])

    def test_a_contact_of_a_paired_device_prepares_it_in_the_background(self):
        a, dev = self.phone(version="2.145.0")
        self.pair(dev)
        adb._PREPARING.clear()
        answer = a.post("/agent/api/checkin", dict(a.ident, adb={"developer": True, "wireless": True, "port": 41235, "ip": "192.168.43.71"}),
                        a.token).get_json()
        self.assertEqual(answer["adb"], {"keep": True})                    # garder le débogage sans fil joignable
        for _ in range(100):
            if self.prepared(dev):
                break
            time.sleep(0.05)
        self.assertIn("screen", self.prepared(dev).get("done", []))
        count = self.args().count("PROJECT_MEDIA")
        a.post("/agent/api/checkin", dict(a.ident, adb={"developer": True, "wireless": True, "port": 41235, "ip": "192.168.43.71"}), a.token)
        time.sleep(0.2)
        self.assertEqual(self.args().count("PROJECT_MEDIA"), count)        # une fois suffit

    def test_keeping_adb_reachable_is_only_asked_while_paired(self):
        a, dev = self.phone(version="2.145.0")
        contact = lambda: a.post("/agent/api/checkin", dict(a.ident, adb={"developer": True, "wireless": False, "port": 0, "ip": ""}),  # noqa: E731
                                 a.token).get_json()["adb"]
        self.assertEqual(contact(), {"keep": False})
        self.pair(dev)
        self.assertEqual(contact(), {"keep": True})
        self.ensure_prepared_then_forget(dev)
        self.assertEqual(contact(), {"keep": False})
        self.assertEqual(self.prepared(dev), {})

    def ensure_prepared_then_forget(self, dev):
        with transaction() as conn:
            conn.execute("UPDATE android_adb SET prepared=? WHERE device_id=?", ('{"at": "2026-11-10 10:00", "agent": "2.145.0", "done": ["screen"]}', dev["id"]))
        self.admin.post(f"/fleet/{dev['id']}/adb/forget", page=f"/fleet/{dev['id']}")


class TestTerminal(AdbTest):
    def test_a_real_shell_is_relayed_and_the_app_never_sees_the_session(self):
        a, dev = self.phone()
        self.pair(dev)
        sid = self.open_terminal(dev)
        out, ack, _answer = self.read_until(sid, "$ ")
        self.assertIn("$ ", out)
        self.io(sid, "echo bonjour-$((6*7))\r", seq=1, ack=ack)
        out, ack, answer = self.read_until(sid, "bonjour-42", ack)
        self.assertIn("bonjour-42", out, answer)
        # L'application loco de l'appareil ne reçoit pas cette session et ne la ferme pas.
        answer = a.post("/agent/api/remote", {"sessions": []}, a.token).get_json()
        self.assertEqual(answer["sessions"], [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM remote_sessions WHERE id=?", (sid,)).fetchone()[0], "active")
        self.io(sid, "exit\r", seq=2, ack=ack)
        _out, _ack, answer = self.read_until(sid, "\x00never", ack)
        self.assertEqual(answer["state"], "closed")
        self.assertIn("Session adb terminée", answer["error"])
        with transaction() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM audit WHERE action='parc.session.ouverture' AND detail LIKE ?",
                                              (f"%adb, {sid}%",)).fetchone())


class TestMcp(AdbTest):
    def setUp(self):
        super().setUp()
        mcp_remote._STATE.clear()

    def test_adb_shell_runs_commands_on_android_devices_only(self):
        _a, dev = self.phone()
        self.pair(dev)
        token, _ = create(self.admin, "Assistant adb", ["adb_shell"])
        client = Client(token)
        result = client.data("adb_shell", device=dev["hostname"], command="echo bonjour; exit 3")
        self.assertEqual((result["output"], result["exit_code"]), ("bonjour", 3))
        linux = Agent(LINUX, "192.168.43.73")
        linux.enroll()
        refused = client.call("adb_shell", device=LINUX["hostname"], command="id")
        self.assertTrue(refused["isError"])
        with transaction() as conn:
            detail = conn.execute("SELECT detail FROM audit WHERE action='parc.session.commande' ORDER BY id DESC").fetchone()[0]
            sids = [r[0] for r in conn.execute("SELECT id FROM remote_sessions WHERE kind='adb' AND state IN ('waiting','active')")]
            for sid in sids:
                conn.execute("UPDATE remote_sessions SET state='closed' WHERE id=?", (sid,))
        self.assertIn("adb shell : echo bonjour", detail)
        self.assertEqual(json.loads(json.dumps(result))["hostname"], dev["hostname"])

    def test_adb_unlock_injects_the_pattern_blind(self):
        _a, dev = self.phone()
        self.pair(dev)
        client = Client(create(self.admin, "Assistant déverrouillage", ["adb_unlock"])[0])
        result = client.data("adb_unlock", device=dev["hostname"], method="pattern", pattern=[1, 4, 5, 9])
        self.assertEqual(result["unlocked"], True)
        self.assertIn("input motionevent", self.args())
        linux = Agent(LINUX, "192.168.43.74")
        linux.enroll()
        self.assertTrue(client.call("adb_unlock", device=LINUX["hostname"], method="pin", code="1234")["isError"])


class TestUnlock(AdbTest):
    """Réveil et déverrouillage à distance (schéma, PIN, mot de passe) injectés par adb ; calage de la grille."""
    W, H = 1080, 2400

    def ready(self):
        _agent, dev = self.phone()
        self.pair(dev)
        return dev, self.admin.token(f"/fleet/{dev['id']}")

    def unlock(self, dev, tok, **data):
        data["csrf"] = tok
        return self.admin.c.post(f"/fleet/{dev['id']}/adb/unlock", data=data)

    def test_pattern_route_injects_the_computed_gesture(self):
        dev, tok = self.ready()
        r = self.unlock(dev, tok, method="pattern", pattern="1,4,5,9")
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "error": "", "locked": False}))
        cx, cy, dx, dy = 0.5 * self.W, 0.683 * self.H, 0.278 * self.W, 0.142 * self.H
        log = self.args()
        self.assertIn("shell input keyevent 224", log)                                      # réveil
        self.assertIn("wm dismiss-keyguard", log)
        self.assertIn(f"input motionevent DOWN {int(cx - dx)} {int(cy - dy)}", log)          # point 1
        self.assertIn(f"input motionevent UP {int(cx + dx)} {int(cy + dy)}", log)            # point 9

    def test_pin_route_types_digits_then_enter_without_exposing_the_code(self):
        dev, tok = self.ready()
        self.assertTrue(self.unlock(dev, tok, method="pin", code="1234").get_json()["ok"])
        log = self.args()
        self.assertIn("shell input keyevent 8 9 10 11", log)                                # 1,2,3,4 = KEYCODE 8..11
        self.assertIn("shell input keyevent 66", log)                                       # Entrée
        self.assertNotIn("1234", log)

    def test_password_route_types_then_validates(self):
        dev, tok = self.ready()
        self.assertTrue(self.unlock(dev, tok, method="password", code="ab12").get_json()["ok"])
        self.assertIn("shell input text ab12", self.args())

    def test_an_invalid_pattern_is_refused_without_injecting(self):
        dev, tok = self.ready()
        self.assertTrue(self.unlock(dev, tok, method="pattern", pattern="5").get_json()["error"])
        self.assertNotIn("input motionevent", self.args())

    def test_calibration_moves_the_grid_and_reset_restores_the_default(self):
        dev, tok = self.ready()
        self.admin.c.post(f"/fleet/{dev['id']}/adb/calibrate",
                          data={"csrf": tok, "cx": "0.4", "cy": "0.6", "dx": "0.3", "dy": "0.15"})
        self.unlock(dev, tok, method="pattern", pattern="1,2,3")
        self.assertIn(f"input motionevent DOWN {int(0.4 * self.W - 0.3 * self.W)} {int(0.6 * self.H - 0.15 * self.H)}",
                      self.args())
        r = self.admin.c.post(f"/fleet/{dev['id']}/adb/calibrate", data={"csrf": tok, "reset": "1"})
        self.assertAlmostEqual(r.get_json()["geometry"]["cx"], 0.5)

    def test_wake_route(self):
        dev, tok = self.ready()
        r = self.admin.c.post(f"/fleet/{dev['id']}/adb/wake", data={"csrf": tok})
        self.assertEqual(r.status_code, 200)
        self.assertIn("shell input keyevent 224", self.args())


class TestScreen(AdbTest):
    """Contrôle de l'écran déverrouillé : flux MJPEG et gestes (tap/swipe/touches/texte) injectés par adb."""

    def ready(self):
        _agent, dev = self.phone()
        self.pair(dev)
        return dev, self.admin.token(f"/fleet/{dev['id']}")

    def inp(self, dev, tok, **data):
        data["csrf"] = tok
        return self.admin.c.post(f"/fleet/{dev['id']}/adb/input", data=data)

    def test_tap_and_swipe_are_scaled_from_fractions_to_pixels(self):
        dev, tok = self.ready()
        self.assertTrue(self.inp(dev, tok, action="tap", x="0.5", y="0.5").get_json()["ok"])
        self.assertIn("shell input tap 540 1200", self.args())
        self.inp(dev, tok, action="swipe", x="0.1", y="0.9", x2="0.1", y2="0.4", ms="200")
        self.assertIn("shell input swipe 108 2160 108 960 200", self.args())

    def test_navigation_keys_and_text(self):
        dev, tok = self.ready()
        self.inp(dev, tok, action="key", key="back")
        self.inp(dev, tok, action="key", key="home")
        self.inp(dev, tok, action="text", text="salut")
        log = self.args()
        self.assertIn("shell input keyevent 4", log)        # Retour
        self.assertIn("shell input keyevent 3", log)        # Accueil
        self.assertIn("shell input text salut", log)

    def test_unknown_key_or_action_is_refused(self):
        dev, tok = self.ready()
        self.assertEqual(self.inp(dev, tok, action="key", key="autodestruction").status_code, 400)
        self.assertEqual(self.inp(dev, tok, action="zap").status_code, 400)

    def test_screen_needs_ffmpeg(self):
        dev, tok = self.ready()
        with mock.patch("loco.adb.shutil.which", return_value=None):
            self.assertEqual(self.admin.c.get(f"/fleet/{dev['id']}/adb/screen").status_code, 503)

    def test_screen_streams_jpeg_frames_as_multipart(self):
        dev, tok = self.ready()
        with mock.patch("loco.adb.shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("loco.adb.screen_frames", return_value=iter([b"\xff\xd8frame\xff\xd9"])):
            r = self.admin.c.get(f"/fleet/{dev['id']}/adb/screen")
            self.assertEqual(r.status_code, 200)
            self.assertIn("multipart/x-mixed-replace", r.content_type)
            self.assertIn(b"Content-Type: image/jpeg", r.get_data())

    def test_scrcpy_stream_needs_the_bundled_server(self):
        dev, tok = self.ready()
        with mock.patch("loco.adb.scrcpy_available", return_value=False):
            self.assertEqual(self.admin.c.get(f"/fleet/{dev['id']}/adb/stream").status_code, 503)

    def test_scrcpy_stream_frames_each_access_unit(self):
        dev, tok = self.ready()
        aus = [(True, b"\x00\x00\x00\x01gSPS"), (False, b"\x00\x00\x00\x01aXYZ")]
        with mock.patch("loco.adb.scrcpy_available", return_value=True), \
             mock.patch("loco.adb.scrcpy_stream", return_value=iter(aus)):
            r = self.admin.c.get(f"/fleet/{dev['id']}/adb/stream")
            self.assertEqual((r.status_code, r.content_type), (200, "application/octet-stream"))
            body = r.get_data()
        self.assertEqual(body, struct.pack(">BI", 1, 8) + aus[0][1] + struct.pack(">BI", 0, 8) + aus[1][1])

    def test_control_is_signalled_to_the_agent_and_shortens_the_interval(self):
        adb._CONTROL.clear()
        agent, dev = self.phone()
        idle = agent.post("/agent/api/checkin", dict(agent.ident, adb={}), agent.token).get_json()
        self.assertFalse(idle["control"])                                   # aucun contrôle en cours
        adb.mark_control(dev["id"])                                         # un geste/flux adb vient d'avoir lieu
        active = agent.post("/agent/api/checkin", dict(agent.ident, adb={}), agent.token).get_json()
        self.assertTrue(active["control"])                                  # garde-fou : l'agent l'affichera
        self.assertLessEqual(active["interval"], 3)                         # et contacte loco souvent


if __name__ == "__main__":
    unittest.main()
