"""Shell root du serveur : désactivé par défaut, actif seulement après un redémarrage, réservé au compte admin."""
import base64
import os
import tempfile
import time
import unittest
from unittest import mock

from test_portal import Browser, ADMIN_PW
from loco import auth
from loco.db import transaction
import locoadmin


class ShellStateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        run = os.path.join(self.dir, "run")
        for name, value in (("SHELL_FLAG", os.path.join(self.dir, "server-shell")), ("SHELL_RUN", run),
                            ("SHELL_BOOT", os.path.join(run, "boot"))):
            patch = mock.patch.object(locoadmin, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(locoadmin.shutil, "chown")
        patch.start()
        self.addCleanup(patch.stop)

    def reboot(self):
        os.remove(locoadmin.SHELL_BOOT)           # /run est vidé au démarrage

    def test_off_by_default(self):
        self.assertEqual(locoadmin.shell_status(), "off")

    def test_enable_needs_a_reboot_and_disable_is_immediate(self):
        locoadmin.set_shell({"enabled": "1"}, log=lambda *_: None)
        self.assertEqual(locoadmin.shell_status(), "pending")
        locoadmin._shell_runtime()                 # relance de l'agent sans redémarrage : toujours en attente
        self.assertEqual(locoadmin.shell_status(), "pending")
        self.reboot()
        self.assertEqual(locoadmin.shell_status(), "active")
        locoadmin._shell_runtime()                 # nouvel agent après le redémarrage
        self.assertEqual(locoadmin.shell_status(), "active")
        locoadmin.set_shell({"enabled": ""}, log=lambda *_: None)
        self.assertEqual(locoadmin.shell_status(), "off")


class ShellBrokerTest(unittest.TestCase):
    def test_real_pty_round_trip_and_disable(self):
        sessions, sid = {}, "d" * 32
        self.assertEqual(locoadmin.shell_handle({"op": "open", "sid": sid}, sessions, active=True)["state"], "active")
        command = base64.b64encode(b"echo LOCO-$((40+2))\n").decode()
        locoadmin.shell_handle({"op": "io", "sid": sid, "seq": 1, "data": command}, sessions, active=True)
        out, ack = b"", 0
        for _ in range(100):
            reply = locoadmin.shell_handle({"op": "io", "sid": sid, "ack": ack}, sessions, active=True)
            for packet in reply["packets"]:
                out += base64.b64decode(packet["data"])
                ack = packet["seq"]
            if b"LOCO-42" in out:
                break
            time.sleep(0.05)
        self.assertIn(b"LOCO-42", out)
        reply = locoadmin.shell_handle({"op": "io", "sid": sid, "ack": ack}, sessions, active=False)
        self.assertEqual(reply["state"], "closed")             # désactivé : session coupée
        self.assertEqual(sessions, {})

    def test_disable_is_immediate_through_the_socket(self):
        import threading
        state = ["active"]
        path = os.path.join(tempfile.mkdtemp(), "shell.sock")
        with mock.patch.object(locoadmin, "shell_status", lambda: state[0]):
            threading.Thread(target=locoadmin.shell_broker, args=(path,), daemon=True).start()
            for _ in range(50):
                if os.path.exists(path):
                    break
                time.sleep(0.02)
            self.assertEqual(locoadmin.shell_call({"op": "open", "sid": "1" * 32}, path=path)["state"], "active")
            state[0] = "off"                                     # désactivation…
            self.assertEqual(locoadmin.shell_call({"op": "open", "sid": "2" * 32}, path=path)["state"], "closed")
            self.assertEqual(locoadmin.shell_call({"op": "io", "sid": "1" * 32}, path=path)["state"], "closed")

    def test_refuses_when_not_active_and_bad_ids(self):
        self.assertEqual(locoadmin.shell_handle({"op": "open", "sid": "e" * 32}, {}, active=False)["state"], "closed")
        self.assertEqual(locoadmin.shell_handle({"op": "open", "sid": "../x"}, {}, active=True)["state"], "closed")


class ShellPortalTest(unittest.TestCase):
    def setUp(self):
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)
        for name in ("running_job", "agent_running", "request", "shell_status", "shell_call"):
            patch = mock.patch.object(locoadmin, name)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)
        self.running_job.return_value = None
        self.agent_running.return_value = True
        self.shell_status.return_value = "off"

    def post(self, path, data=None, browser=None):
        return (browser or self.admin).post(path, data or {}, page="/system")

    def test_menu_entry_and_page_follow_the_state(self):
        html = self.admin.c.get("/system").get_data(as_text=True)
        self.assertIn('href="/system/shell"', html)                 # menu Administration (et donc Ctrl+K)
        self.assertNotIn("shell-quick", html)                         # icône de la barre : seulement si actif
        page = self.admin.c.get("/system/shell").get_data(as_text=True)
        self.assertIn("Activer le shell root", page)
        self.shell_status.return_value = "pending"
        self.assertIn("Annuler l'activation", self.admin.c.get("/system/shell").get_data(as_text=True))
        self.shell_status.return_value = "active"
        page = self.admin.c.get("/system/shell").get_data(as_text=True)
        self.assertIn("Ouvrir le shell root", page)
        self.assertIn("shell-quick", page)

    def test_enable_requires_the_password(self):
        self.post("/system/shell/enable", {"password": "faux"})
        self.request.assert_not_called()
        self.post("/system/shell/enable", {"password": ADMIN_PW})
        self.request.assert_called_once_with("set-shell", {"enabled": "1"}, user="admin")

    def test_open_and_relay_only_when_active(self):
        self.assertEqual(self.post("/system/shell/open").status_code, 302)
        self.shell_call.assert_not_called()                      # pas actif : rien d'ouvert
        self.shell_status.return_value = "active"
        self.shell_call.return_value = {"state": "active"}
        r = self.post("/system/shell/open")
        sid = r.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        self.assertEqual(self.admin.c.get(f"/system/shell/{sid}").status_code, 200)
        self.shell_call.return_value = {"state": "active", "error": "", "packets": [{"seq": 1, "data": "aGk="}], "ack": 1}
        data = self.post(f"/system/shell/{sid}/io", {"seq": "1", "data": "bHMK", "ack": "0"}).get_json()
        self.assertEqual(data["packets"][0]["data"], "aGk=")
        self.assertEqual(self.shell_call.call_args[0][0]["op"], "io")
        other = Browser()
        other.login("admin", ADMIN_PW)                           # autre connexion : la session ne la suit pas
        self.assertEqual(other.c.get(f"/system/shell/{sid}").status_code, 404)

    @staticmethod
    def _drop_ops():
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='shell-ops'")

    def test_other_admins_are_refused(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='shell-ops'")
            auth.create_user(conn, "shell-ops", "Shell-Ops-Test-2026", "admin", must_change=False)
        self.addCleanup(self._drop_ops)
        ops = Browser()
        ops.login("shell-ops", "Shell-Ops-Test-2026")
        self.assertEqual(self.post("/system/shell/enable", {"password": "Shell-Ops-Test-2026"}, ops).status_code, 403)
        self.assertNotIn('href="/system/shell"', ops.c.get("/system").get_data(as_text=True))
        self.assertEqual(ops.c.get("/system/shell").status_code, 403)


if __name__ == "__main__":
    unittest.main()
