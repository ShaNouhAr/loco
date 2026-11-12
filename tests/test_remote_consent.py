"""Accord de la personne devant le poste avant le bureau distant : désactivé par défaut, jamais contourné une fois exigé."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, LINUX, WINDOWS, device
from loco import auth, remote
from loco.db import transaction

ROOT = Path(__file__).resolve().parent.parent


class TestPortal(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM remote_sessions")
            conn.execute("DELETE FROM audit WHERE action LIKE 'parc.session%'")
        self.agent = Agent(WINDOWS, "192.168.43.61")
        self.agent.enroll()
        self.dev_id = device(self.agent.uid)["id"]

    def require(self, enabled=True):
        self.assertEqual(self.admin.post(f"/fleet/{self.dev_id}/remote-consent", {"enabled": "1" if enabled else "0"}).status_code, 302)

    def open(self, kind="desktop", **form):
        response = self.admin.post(f"/fleet/{self.dev_id}/session", dict({"kind": kind}, **form))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:200])
        return response.headers["Location"].rsplit("/", 1)[1]

    def exchange(self, *sessions):
        return self.agent.post("/agent/api/remote", {"sessions": list(sessions)}, self.agent.token).get_json()["sessions"]

    def audit(self, action):
        with transaction() as conn:
            return [r["detail"] for r in conn.execute("SELECT detail FROM audit WHERE action=? ORDER BY id", (action,))]

    def test_off_by_default(self):
        self.assertEqual(device(self.agent.uid)["remote_consent"], 0)
        self.open()
        self.assertNotIn("consent", self.exchange()[0])

    def test_required_on_the_device_reaches_the_agent_with_who_asks(self):
        self.require()
        self.open()
        self.assertEqual(self.exchange()[0]["consent"], {"by": "admin", "assistant": False})
        self.assertIn(", accord demandé", self.audit("parc.session.ouverture")[-1])

    def test_can_be_asked_for_one_session_only(self):
        sid = self.open(consent="1")
        self.assertEqual(self.exchange()[0]["consent"]["by"], "admin")
        self.assertEqual(device(self.agent.uid)["remote_consent"], 0)
        self.admin.c.get(f"/fleet/session/{sid}")
        page = self.admin.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True)
        self.assertIn("avec l'accord de l'utilisateur", page)

    def test_reopening_keeps_asking(self):
        sid = self.open(consent="1")
        page = self.admin.c.get(f"/fleet/session/{sid}").get_data(as_text=True)
        self.assertEqual(page.count('name="consent" value="1"'), 2)
        other = self.open()
        self.assertNotIn('name="consent"', self.admin.c.get(f"/fleet/session/{other}").get_data(as_text=True))

    def test_terminal_and_files_are_not_concerned(self):
        self.require()
        self.open("terminal")
        self.assertNotIn("consent", self.exchange()[0])

    def test_old_agent_is_refused_rather_than_opened_without_asking(self):
        old = Agent(dict(LINUX, agent_version="2.136.0"), "192.168.43.62")
        old.enroll()
        dev_id = device(old.uid)["id"]
        self.assertEqual(self.admin.post(f"/fleet/{dev_id}/session", {"kind": "desktop", "consent": "1"}).status_code, 409)
        self.admin.post(f"/fleet/{dev_id}/remote-consent", {"enabled": "1"})
        self.assertEqual(self.admin.post(f"/fleet/{dev_id}/session", {"kind": "desktop"}).status_code, 409)
        self.assertEqual(old.post("/agent/api/remote", {"sessions": []}, old.token).get_json()["sessions"], [])
        self.assertEqual(self.admin.post(f"/fleet/{dev_id}/session", {"kind": "terminal"}).status_code, 302)

    def test_acceptance_is_journaled_and_opens_the_stream(self):
        self.require()
        sid = self.open()
        self.exchange()
        self.assertEqual(self.admin.post(f"/fleet/session/{sid}/io", {}).get_json()["consent"], "pending")
        self.exchange({"id": sid, "state": "active", "seq": 1, "ack": 0, "data": "UkZC", "consent": "given"})
        answer = self.admin.post(f"/fleet/session/{sid}/io", {}).get_json()
        self.assertEqual((answer["consent"], len(answer["packets"])), ("given", 1))
        self.assertIn("accepté sur le poste", self.audit("parc.session.accord")[0])
        self.assertNotIn("consent", self.exchange({"id": sid, "state": "active", "seq": 1, "ack": 0, "data": "UkZC", "consent": "given"})[0])
        self.assertEqual(len(self.audit("parc.session.accord")), 1)

    def test_nobody_signed_in_is_journaled_as_such(self):
        self.require()
        sid = self.open()
        self.exchange({"id": sid, "state": "active", "seq": 0, "ack": 0, "data": "", "consent": "nobody"})
        self.assertIn("personne de connecté", self.audit("parc.session.accord")[0])

    def test_no_image_goes_to_the_browser_without_the_answer(self):
        self.require()
        sid = self.open()
        self.exchange({"id": sid, "state": "active", "seq": 1, "ack": 0, "data": "UkZC"})
        answer = self.admin.post(f"/fleet/session/{sid}/io", {}).get_json()
        self.assertEqual((answer["state"], answer["packets"]), ("closed", []))
        self.assertIn("sans l'accord", answer["error"])
        for lie in ("refused", "yes", True):
            sid = self.open()
            self.exchange({"id": sid, "state": "active", "seq": 1, "ack": 0, "data": "UkZC", "consent": lie})
            self.assertEqual(self.admin.post(f"/fleet/session/{sid}/io", {}).get_json()["packets"], [])

    def test_refusal_closes_with_its_reason(self):
        self.require()
        sid = self.open()
        self.exchange({"id": sid, "state": "closed", "seq": 0, "ack": 0, "data": "", "error": "La personne a refusé."})
        answer = self.admin.post(f"/fleet/session/{sid}/io", {}).get_json()
        self.assertEqual((answer["state"], answer["error"]), ("closed", "La personne a refusé."))
        self.assertIn("La personne a refusé.", self.audit("parc.session.fin")[-1])

    def test_only_the_dedicated_right_changes_the_setting(self):
        with transaction() as conn:
            auth.create_user(conn, "technicien", "Technicien-Pass-1234", "operator", must_change=False)
        operator = fixture.Browser()
        operator.login("technicien", "Technicien-Pass-1234")
        self.require()
        self.assertEqual(operator.post(f"/fleet/{self.dev_id}/remote-consent", {"enabled": "0"}).status_code, 403)
        self.assertEqual(operator.post("/fleet/remote-consent-selected?enabled=0", {"ids": [self.dev_id]}).status_code, 403)
        self.assertEqual(device(self.agent.uid)["remote_consent"], 1)
        self.assertNotIn("Ne plus exiger l'accord", operator.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True))
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='technicien'")

    def test_selection_and_journal(self):
        other = Agent(LINUX, "192.168.43.63")
        other.enroll()
        ids = [self.dev_id, device(other.uid)["id"]]
        self.assertEqual(self.admin.post("/fleet/remote-consent-selected?enabled=1", {"ids": ids}).status_code, 302)
        self.assertEqual((device(self.agent.uid)["remote_consent"], device(other.uid)["remote_consent"]), (1, 1))
        self.admin.post("/fleet/remote-consent-selected?enabled=0", {"ids": [self.dev_id]})
        self.assertEqual((device(self.agent.uid)["remote_consent"], device(other.uid)["remote_consent"]), (0, 1))
        self.assertEqual(self.admin.post("/fleet/remote-consent-selected?enabled=1", {"ids": ["x"]}).status_code, 400)
        self.assertEqual(self.audit("parc.accord-reglage")[-1], "1 poste(s) : plus exigé")

    def test_device_page_says_what_applies(self):
        page = self.admin.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True)
        self.assertIn("s'ouvre sans demander d'accord", page)
        self.assertIn("Exiger l'accord de l'utilisateur", page)
        self.require()
        page = self.admin.c.get(f"/fleet/{self.dev_id}").get_data(as_text=True)
        self.assertIn("accord exigé pour le bureau", page)
        self.assertIn("Ne plus exiger l'accord", page)
        self.assertNotIn("Bureau distant, avec l'accord", page)
        self.assertTrue(remote.consent_ready(device(self.agent.uid)))


class TestLinuxAgent(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_consent", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.agent.LANG = "fr"

    def consent(self):
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        return read_end, {"fd": write_end, "by": "alice", "assistant": False}

    def test_nothing_is_asked_when_nothing_is_required(self):
        with mock.patch.object(self.agent, "desktop_users", side_effect=AssertionError):
            self.agent.require_consent(None)

    def test_acceptance_and_nobody_are_reported_before_anything_else(self):
        for users, dialog, expected in (([1000], "given", b"given"), ([], None, b"nobody")):
            read_end, consent = self.consent()
            with mock.patch.object(self.agent, "desktop_users", return_value=users), mock.patch.object(self.agent, "ask_consent", return_value=dialog):
                self.agent.require_consent(consent)
            self.assertEqual(os.read(read_end, 16), expected)

    def test_everything_else_refuses_the_desktop(self):
        for answer, text in (("refused", "a refusé"), ("timeout", "Pas de réponse"), ("no-tool", "zenity"), ("failed", "n'a pas pu s'afficher")):
            read_end, consent = self.consent()
            with mock.patch.object(self.agent, "desktop_users", return_value=[1000]), mock.patch.object(self.agent, "ask_consent", return_value=answer):
                with self.assertRaisesRegex(RuntimeError, text):
                    self.agent.require_consent(consent)
            os.close(consent["fd"])
            self.assertEqual(os.read(read_end, 16), b"")

    def test_a_broken_loginctl_refuses_rather_than_skips(self):
        _, consent = self.consent()
        self.addCleanup(os.close, consent["fd"])
        with mock.patch.object(self.agent, "desktop_users", side_effect=subprocess.SubprocessError):
            with self.assertRaisesRegex(RuntimeError, "n'a pas pu s'afficher"):
                self.agent.require_consent(consent)

    def test_desktop_refuses_before_capturing_anything(self):
        with mock.patch.object(self.agent, "require_consent", side_effect=RuntimeError("non")), mock.patch.object(self.agent, "x11_environment", side_effect=AssertionError):
            with self.assertRaisesRegex(RuntimeError, "non"):
                self.agent.linux_desktop(consent={"fd": 9, "by": "a", "assistant": False})

    def test_only_open_graphical_sessions_count_and_the_active_one_first(self):
        sessions = {"10": "Class=greeter\nType=wayland\nUser=115\nState=online\n", "11": "Class=user\nType=tty\nUser=1001\nState=active\n",
                    "12": "Class=user\nType=x11\nUser=1002\nState=online\n", "13": "Class=user\nType=wayland\nUser=1003\nState=active\n"}

        def run(command, **kwargs):
            if command[1] == "list-sessions":
                return mock.Mock(stdout="".join(f"{k} 1 u seat0\n" for k in sessions))
            return mock.Mock(stdout=sessions[command[2]])
        with mock.patch.object(self.agent.subprocess, "run", run):
            self.assertEqual(self.agent.desktop_users(), [1003, 1002])

    def dialog(self, code=0, env=None, tools=("zenity",), assistant=False, by="alice"):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if isinstance(code, Exception):
                raise code
            return mock.Mock(returncode=code)
        with mock.patch.object(self.agent, "_session_environment", return_value={"DISPLAY": ":0"} if env is None else env), \
                mock.patch.object(self.agent.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None), \
                mock.patch.object(self.agent.subprocess, "run", run), \
                mock.patch.object(self.agent.pwd, "getpwuid", return_value=mock.Mock(pw_dir="/home/a", pw_name="a", pw_gid=1000)):
            return self.agent.ask_consent(1000, by, assistant), calls

    def test_zenity_answers(self):
        self.assertEqual(self.dialog(0)[0], "given")
        self.assertEqual(self.dialog(1)[0], "refused")
        self.assertEqual(self.dialog(5)[0], "timeout")
        self.assertEqual(self.dialog(255)[0], "failed")
        self.assertEqual(self.dialog(subprocess.TimeoutExpired("z", 1))[0], "timeout")

    def test_question_is_plain_text_in_the_users_session(self):
        answer, calls = self.dialog(by="<b>alice</b>; rm -rf /")
        command, kwargs = calls[0]
        self.assertEqual(answer, "given")
        self.assertIn("--no-markup", command)
        self.assertIn("--timeout=60", command)
        text = next(a for a in command if a.startswith("--text="))
        self.assertIn("balice", text)
        self.assertNotIn("<", text)
        self.assertNotIn(";", text)
        self.assertEqual((kwargs["env"]["DISPLAY"], kwargs["env"]["USER"], kwargs["stdout"]), (":0", "a", subprocess.DEVNULL))
        self.assertIsNotNone(kwargs["preexec_fn"])

    def test_question_uses_the_language_of_the_session(self):
        self.agent.LANG = "en"
        _, calls = self.dialog(env={"DISPLAY": ":0", "LANG": "fr_FR.UTF-8"})
        self.assertIn("demande à voir", next(a for a in calls[0][0] if a.startswith("--text=")))
        self.assertIn("--ok-label=Accepter", calls[0][0])
        self.agent.LANG = "fr"
        _, calls = self.dialog(env={"DISPLAY": ":0", "LANG": "es_ES.UTF-8"})
        self.assertIn("--ok-label=Aceptar", calls[0][0])
        self.agent.LANG = "es"
        _, calls = self.dialog(env={"DISPLAY": ":0", "LANG": "C"})      # pas de langue connue : celle de l'agent
        self.assertIn("--ok-label=Aceptar", calls[0][0])

    def test_an_assistant_says_so(self):
        _, calls = self.dialog(assistant=True)
        self.assertIn("assistant IA", next(a for a in calls[0][0] if a.startswith("--text=")))

    def test_kde_uses_its_own_dialog_with_a_time_limit(self):
        _, calls = self.dialog(env={"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "KDE"}, tools=("zenity", "kdialog"))
        self.assertEqual(calls[0][0][0], "/usr/bin/kdialog")
        self.assertEqual(calls[0][1]["timeout"], 60)

    def test_without_a_tool_or_a_session_nothing_is_guessed(self):
        self.assertEqual(self.dialog(tools=())[0], "no-tool")
        self.assertEqual(self.dialog(env={})[0], "failed")

    def test_arguments(self):
        self.assertIsNone(self.agent.consent_arguments(["--terminal-fallback"]))
        self.assertEqual(self.agent.consent_arguments(["--consent-fd=7", "--consent-by=alice", "--consent-assistant"]),
                         {"fd": 7, "by": "alice", "assistant": True})

    def test_session_service_reports_the_answer_no_later_than_the_first_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp, "agent.py")
            program.write_text("import os, sys, time\n"
                               "fd = int(next(a for a in sys.argv if a.startswith('--consent-fd=')).split('=')[1])\n"
                               "assert '--consent-by=alice' in sys.argv\n"
                               "os.write(fd, b'given'); os.close(fd)\n"
                               "sys.stdout.buffer.write(b'RFB 003.008\\n'); sys.stdout.flush(); time.sleep(20)\n")
            self.agent.PROGRAM = str(program)
            process = self.agent.RemoteProcess({"id": "s", "kind": "desktop", "cols": 80, "rows": 25, "consent": {"by": "alice", "assistant": False}})
            self.addCleanup(process.close)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                payload = process.payload()
                if payload["data"]:
                    break
                time.sleep(0.02)
            self.assertTrue(payload["data"])
            self.assertEqual(payload["consent"], "given")

    def test_session_service_does_not_ask_otherwise(self):
        seen = {}
        real = subprocess.Popen

        def popen(command, **kwargs):
            seen["command"], seen["keep"] = command, kwargs.get("pass_fds")
            return real([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
        with mock.patch.object(self.agent.subprocess, "Popen", popen):
            process = self.agent.RemoteProcess({"id": "s", "kind": "desktop", "cols": 80, "rows": 25})
        self.addCleanup(process.close)
        self.assertFalse([a for a in seen["command"] if a.startswith("--consent")])
        self.assertEqual(seen["keep"], ())
        self.assertEqual(process.payload()["consent"], "")


class TestWindowsAgentSource(unittest.TestCase):
    def test_desktop_only_starts_after_the_answer(self):
        source = (ROOT / "windows/client/RemoteControl.cs").read_text(encoding="utf-8")
        ask = source.index("void AskThenOpen")
        self.assertLess(source.index("DesktopIndicator.AskConsent", ask), source.index("OpenDesktop(stopping);", ask))
        self.assertLess(source.index('answer == "timeout"', ask), source.index("OpenDesktop(stopping);", ask))


    def test_rest_follows_the_server(self):
        source = (ROOT / "windows/client/RemoteControl.cs").read_text(encoding="utf-8")
        self.assertIn(": RestDelay(answer);", source)
        self.assertIn("Math.Min(5.0, Math.Max(0.25, seconds))", source)


if __name__ == "__main__":
    unittest.main()
