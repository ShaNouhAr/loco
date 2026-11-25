"""Serveur MCP : terminal, fichiers et bureau par le relais des sessions distantes, face au vrai code de
l'agent (PTY bash, service de fichiers, serveur RFB) qui tourne dans un fil comme l'agent du poste."""

import base64
import json
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

import test_portal as fixture
from test_fleet import LINUX, WINDOWS, Agent
from test_mcp import Client, McpTest, create
from loco import mcp_remote, remote
from loco.db import db, transaction
import fake_agent

HERE = os.path.dirname(os.path.abspath(__file__))
REMOTE = ["terminal_run", "terminal_interact", "list_files", "read_file", "write_file", "manage_files", "delete_file",
          "desktop_screenshot", "desktop_click", "desktop_type", "desktop_key", "desktop_scroll"]


class AgentLoop(threading.Thread):
    """Boucle « remote » de loco-client : mêmes descripteurs, mêmes RemoteProcess, relevé toutes les 20 ms."""

    def __init__(self, agent, module):
        super().__init__(daemon=True)
        self.agent, self.module, self.sessions = agent, module, {}
        self.stopping = threading.Event()

    def run(self):
        try:
            while not self.stopping.is_set():
                payload = {"sessions": [s.payload() for s in self.sessions.values()]}
                answer = self.agent.post("/agent/api/remote", payload, self.agent.token).get_json() or {"sessions": []}
                descriptors = {d["id"]: d for d in answer["sessions"]}
                for sid in list(self.sessions):
                    if sid not in descriptors or descriptors[sid]["state"] == "closed":
                        self.sessions.pop(sid).close()
                for sid, descriptor in descriptors.items():
                    if descriptor["state"] not in ("waiting", "active"):
                        continue
                    if sid not in self.sessions:
                        self.sessions[sid] = self.module.RemoteProcess(descriptor)
                    try:
                        self.sessions[sid].update(descriptor)
                    except (OSError, ValueError):
                        self.sessions[sid].close()
                time.sleep(0.02)
        finally:
            for item in self.sessions.values():
                item.close()


class RemoteTest(McpTest):
    @classmethod
    def setUpClass(cls):
        cls.module = fake_agent.load_agent()
        cls.module.PROGRAM = os.path.join(HERE, "fake_agent.py")         # bureau et fichiers : programme de test

    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM remote_sessions")
        mcp_remote._STATE.clear()
        os.environ.pop("LOCO_TEST_WIDTH", None)
        os.environ.pop("LOCO_TEST_HEIGHT", None)
        self.folder = tempfile.mkdtemp(prefix="loco-mcp-")
        self.events = os.path.join(self.folder, "events.jsonl")
        os.environ["LOCO_TEST_EVENTS"] = self.events
        self.loops = []

    def tearDown(self):
        for loop in self.loops:
            loop.stopping.set()
            loop.join(10)
        shutil.rmtree(self.folder, ignore_errors=True)
        super().tearDown()

    def device(self, ident=LINUX, ip="192.168.43.60"):
        agent = Agent(ident, ip)
        agent.enroll()
        loop = AgentLoop(agent, self.module)
        loop.start()
        self.loops.append(loop)
        return ident["hostname"]

    def client(self, tools=REMOTE):
        token, _ = create(self.admin, "Distant", tools)
        return Client(token)

    def logged(self):
        with open(self.events) as f:
            return [json.loads(line) for line in f]

    def sessions(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM remote_sessions ORDER BY created").fetchall()


def decode_png(blob):
    """(largeur, hauteur, octets RGB) d'un PNG 8 bits RGB sans filtre, tel que le produit loco."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", blob[16:24])
    pos, idat = 8, b""
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        if blob[pos + 4:pos + 8] == b"IDAT":
            idat += blob[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    return width, height, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


class TestTerminal(RemoteTest):
    def test_commands_output_exit_code_reuse_and_journal(self):
        host = self.device()
        client = self.client(["terminal_run"])
        result = client.data("terminal_run", device=host, command="echo bonjour; uname -s")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["output"].split("\n"), ["bonjour", "Linux"])
        quoted = client.data("terminal_run", device=host, command="printf '%s|' \"a b\" 'c'\necho\nexit 3")
        self.assertEqual((quoted["output"], quoted["exit_code"]), ("a b|c|", 3))
        self.assertEqual(len(self.sessions()), 1)                     # la même session sert aux appels suivants
        self.assertEqual(self.sessions()[0]["owner"][:4], "mcp:")
        with transaction() as conn:
            commands = [r["detail"] for r in conn.execute("SELECT detail FROM audit WHERE action='parc.session.commande' "
                                                          "AND detail LIKE '%bonjour%' ORDER BY id DESC")]
        self.assertIn("echo bonjour; uname -s", commands[0])
        self.assertIn("MCP « Distant »", commands[0])

    def test_long_scripts(self):
        host = self.device()
        client = self.client(["terminal_run"])
        script = "\n".join(f"echo ligne-{i}" for i in range(4000))             # ~ 55 Ko, une seule frappe
        result = client.data("terminal_run", device=host, command=script, timeout=60, max_output=60000)
        lines = result["output"].split("\n")
        self.assertEqual((len(lines), lines[0], lines[-1], result["exit_code"]), (4000, "ligne-0", "ligne-3999", 0))

    def test_very_long_output_keeps_the_start_and_the_end(self):
        """Sortie au-delà des 60 Ko : on garde le DÉBUT et la FIN (l'assistant voit le contexte du haut et le résultat du
        bas), avec une marque du milieu omis — pas seulement la queue, comme avant."""
        host = self.device()
        client = self.client(["terminal_run"])
        script = "echo DEBUT_MARQUEUR; for i in $(seq 1 3000); do echo ligne-de-remplissage-numero-$i; done; echo FIN_MARQUEUR"
        result = client.data("terminal_run", device=host, command=script, timeout=60)
        self.assertTrue(result["truncated"])
        self.assertIn("DEBUT_MARQUEUR", result["output"])            # le début est gardé
        self.assertIn("FIN_MARQUEUR", result["output"])              # et la fin aussi
        self.assertIn(mcp_remote.OMIT_MARK, result["output"])        # avec le repère du milieu omis
        self.assertLessEqual(len(result["output"]), mcp_remote.OUTPUT_MAX + 200)

    def test_timeout_interrupts_and_the_terminal_stays_usable(self):
        host = self.device()
        client = self.client(["terminal_run"])
        late = client.data("terminal_run", device=host, command="echo avant; sleep 30", timeout=1)
        self.assertTrue(late["timed_out"])
        self.assertIn("avant", late["output"])
        self.assertEqual(client.data("terminal_run", device=host, command="echo après")["output"], "après")


class TestTerminalRobustness(RemoteTest):
    """Un délai dépassé arrête vraiment la commande et ce qu'elle a lancé ; un shell resté occupé est repéré et remplacé ;
    shell neuf à la demande ; tâches de fond détachées ; sortie plafonnée et jamais d'écho de la commande."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(mcp_remote.JOB_DIRS, {"linux": os.path.join(self.folder, "jobs")})
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        with open(f"/proc/{pid}/stat") as f:                       # zombie en attente de son parent : déjà mort
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"

    def pid(self, name, timeout=5):
        path, end = os.path.join(self.folder, name), time.time() + timeout
        while time.time() < end:
            if os.path.exists(path):
                with open(path) as f:
                    text = f.read().strip()
                if text:
                    return int(text)
            time.sleep(0.05)
        self.fail(f"no {name}")

    def gone(self, pid, timeout=8):
        end = time.time() + timeout
        while time.time() < end and self.alive(pid):
            time.sleep(0.1)
        return not self.alive(pid)

    def type_into_session(self, data):
        from loco import app
        with app.app_context():
            conn = db()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM remote_sessions WHERE owner LIKE 'mcp:%' AND kind='terminal' "
                               "AND state IN ('waiting','active')").fetchone()
            remote._append(row, "in", row["input_seq"] + 1, base64.b64encode(data).decode())
            conn.commit()

    def test_a_timeout_kills_the_command_and_its_children(self):
        host = self.device()
        client = self.client(["terminal_run"])
        f = self.folder
        late = client.data("terminal_run", device=host, timeout=2,
                           command=f"echo avant; (sleep 300; echo jamais) & echo $! > {f}/child; echo $$ > {f}/main; sleep 300")
        self.assertTrue(late["timed_out"])
        self.assertTrue(late["killed"])
        self.assertIn("avant", late["output"])
        self.assertTrue(self.gone(self.pid("main")), "la commande tourne encore")
        self.assertTrue(self.gone(self.pid("child")), "son enfant tourne encore")
        again = client.data("terminal_run", device=host, command="echo après")
        self.assertEqual((again["output"], again["exit_code"]), ("après", 0))
        states = [r["state"] for r in self.sessions()]
        self.assertEqual(states.count("closed"), 1)                   # la session du délai est fermée, une neuve a servi

    def test_a_busy_shell_is_detected_and_replaced(self):
        host = self.device()
        client = self.client(["terminal_run"])
        self.assertEqual(client.data("terminal_run", device=host, command="echo prêt")["output"], "prêt")
        # Un programme reste au premier plan du shell (comme cmd /c … d8.bat qui attend) : les commandes suivantes
        # partiraient dans sa saisie.
        self.type_into_session(f"sh -c 'echo $$ > {self.folder}/stuck; exec sleep 300'\r".encode())
        stuck = self.pid("stuck")
        result = client.data("terminal_run", device=host, command="echo vivant", timeout=40)
        self.assertEqual((result["output"], result["exit_code"]), ("vivant", 0))
        self.assertIn("still busy", result["note"])
        self.assertTrue(self.gone(stuck), "le programme bloquant tourne encore")

    def test_fresh_shell_on_demand(self):
        host = self.device()
        client = self.client(["terminal_run"])
        client.data("terminal_run", device=host, command="echo un")
        first = [r["id"] for r in self.sessions() if r["state"] in ("waiting", "active")]
        result = client.data("terminal_run", device=host, command="echo deux", fresh=True)
        self.assertEqual(result["output"], "deux")
        now = [r["id"] for r in self.sessions() if r["state"] in ("waiting", "active")]
        self.assertEqual(len(now), 1)
        self.assertNotEqual(first, now)

    def test_background_jobs_run_detached_report_and_stop(self):
        host = self.device()
        client = self.client(["terminal_run", "terminal_job"])
        started = client.data("terminal_run", device=host, background=True,
                              command="echo début; sleep 2; echo fin; exit 4")
        job = started["job"]
        self.assertRegex(job, "^[0-9a-f]{12}$")
        self.assertEqual(client.data("terminal_job", device=host, job=job)["state"], "running")
        # Le shell de la session est fermé (délai, shell neuf…) : la tâche continue.
        client.data("terminal_run", device=host, command="echo autre", fresh=True)
        end, status = time.time() + 15, {}
        while time.time() < end:
            status = client.data("terminal_job", device=host, job=job)
            if status["state"] != "running":
                break
            time.sleep(0.5)
        self.assertEqual((status["state"], status["exit_code"], status["output"]), ("done", 4, "début\nfin"))
        long_job = client.data("terminal_run", device=host, background=True,
                               command=f"echo $$ > {self.folder}/job; sleep 300")["job"]
        pid = self.pid("job")
        stopped = client.data("terminal_job", device=host, job=long_job, action="stop")
        self.assertEqual(stopped["state"], "stopped")
        self.assertTrue(self.gone(pid), "la tâche arrêtée tourne encore")
        self.assertEqual(client.data("terminal_job", device=host, job="0123456789ab")["state"], "missing")
        self.assertTrue(client.call("terminal_job", device=host, job="../../etc/x")["isError"])
        big = client.data("terminal_run", device=host, background=True,
                          command="echo HAUT; for i in $(seq 1 4000); do echo ligne-$i; done; echo BAS")["job"]
        end = time.time() + 15
        while time.time() < end and client.data("terminal_job", device=host, job=big)["state"] == "running":
            time.sleep(0.3)
        clipped = client.data("terminal_job", device=host, job=big, max_output=2000)
        self.assertTrue(clipped["truncated"])
        self.assertIn("HAUT", clipped["output"])
        self.assertIn("BAS", clipped["output"])
        self.assertLessEqual(len(clipped["output"]), 2200)

    def test_output_is_capped_and_the_command_is_never_echoed(self):
        host = self.device()
        client = self.client(["terminal_run"])
        payload = base64.b64encode(os.urandom(15000)).decode()          # 20 Ko de base64 dans la commande
        result = client.data("terminal_run", device=host, command=f"printf '%s' '{payload}' | wc -c")
        self.assertEqual((result["output"].strip(), result["exit_code"]), (str(len(payload)), 0))
        self.assertNotIn(payload[:40], json.dumps(result))
        script = "echo HAUT; for i in $(seq 1 3000); do echo ligne-de-remplissage-$i; done; echo BAS"
        default = client.data("terminal_run", device=host, command=script)
        self.assertTrue(default["truncated"])
        self.assertLessEqual(len(default["output"]), mcp_remote.OUTPUT_DEFAULT + 200)
        small = client.data("terminal_run", device=host, command=script, max_output=1000)
        self.assertLessEqual(len(small["output"]), 1200)
        self.assertIn("HAUT", small["output"])
        self.assertIn("BAS", small["output"])
        self.assertIn(mcp_remote.OMIT_MARK, small["output"])


class TestWindowsJobScripts(unittest.TestCase):
    def test_a_running_job_is_read_in_shared_mode(self):
        """Sous Windows, le lanceur d'une tâche garde out.txt ouvert en écriture : ReadAllText (accès exclusif) levait
        « fichier utilisé par un autre processus » au lieu d'un état. Toutes les lectures du suivi sont partagées."""
        for script in (mcp_remote._ps_job_status("0123456789ab", 24000), mcp_remote._ps_job_status("0123456789ab", 24000, stop=True)):
            self.assertNotIn("ReadAllText", script)
            self.assertNotIn("Get-Content", script)
            self.assertIn("[IO.FileShare]'ReadWrite, Delete'", script)
            self.assertIn("catch { $null }", script)                    # un fichier illisible ne coupe jamais le suivi


class TestInteractive(RemoteTest):
    def test_programs_keys_state_and_reset(self):
        host = self.device()
        client = self.client(["terminal_interact"])
        self.assertIn("bon{jour}", client.data("terminal_interact", device=host, input="echo bon{{jour}}\n")["screen"])
        client.data("terminal_interact", device=host, input="python3 -q\n")
        result = client.data("terminal_interact", device=host, input="6*7{enter}")
        self.assertIn("42", result["screen"])
        self.assertEqual(result["size"], [120, 40])
        # Quitter d'abord : l'interpréteur de Python 3.13 lit d'avance la saisie en attente et l'emporte en sortant.
        client.data("terminal_interact", device=host, input="{ctrl+d}")
        client.data("terminal_interact", device=host, input="cd /tmp\n")
        self.assertIn("/tmp", client.data("terminal_interact", device=host, input="pwd\n")["screen"].split("\n")[-2])
        self.assertIn("/tmp", client.data("terminal_interact", device=host)["screen"])      # relire sans rien taper
        fresh = client.data("terminal_interact", device=host, reset=True, input="pwd\n")["screen"]
        self.assertNotIn("6*7", fresh)                                   # nouveau shell, nouvel écran
        self.assertEqual(sum(1 for r in self.sessions() if r["owner"].endswith(":tty") and r["state"] != "closed"), 1)

    def test_private_sequences_do_not_break_the_screen(self):
        host = self.device()
        client = self.client(["terminal_interact"])
        # vim interroge modifyOtherKeys par ESC [ ? 4 m : pyte tombait sur un TypeError et l'écran restait mort.
        screen = client.data("terminal_interact", device=host,
                             input="printf '\\033[?4m\\033[?6n\\033[?1r\\033[>4;1m%s\\n' prive-ok\n")["screen"]
        self.assertIn("\nprive-ok", screen)
        self.assertIn("encore", client.data("terminal_interact", device=host, input="echo encore\n")["screen"])

    def test_key_notation(self):
        self.assertEqual(mcp_remote.tty_input("{esc}:wq{enter}"), "\x1b:wq\r")
        self.assertEqual(mcp_remote.tty_input("y\n{ctrl+c}{alt+b}{up}{{}}"), "y\r\x03\x1bb\x1b[A{}")
        with self.assertRaises(mcp_remote.ToolError):
            mcp_remote.tty_input("{hyper}")


class TestPowerShellFraming(unittest.TestCase):
    def test_output_survives_the_console_rendering(self):
        nonce = "abc123def456"
        text = "Résultat\r\nligne 2"
        encoded = base64.b64encode(text.encode()).decode()
        lines = mcp_remote._powershell_lines("Get-Date", nonce)
        self.assertTrue(lines[0].startswith(" $__l = ''"))
        self.assertIn(base64.b64encode(b"Get-Date").decode(), "".join(lines))
        self.assertNotIn(f"@@LOCO:{nonce}", "".join(lines))         # l'écho de la commande ne contient pas les marqueurs
        # Rendu ConPTY : écho de la saisie, couleurs, positionnement du curseur et ligne coupée au milieu.
        rendered = ("\x1b[?25l\x1b[2J\x1b[H PS C:\\> " + "".join(lines) + "\r\n\x1b[32m@@LOCO:" + nonce + ":B\x1b[0m\r\n"
                    + encoded[:10] + "\x1b[12;1H" + encoded[10:] + "\r\n@@LOCO:" + nonce + ":E:0:" + str(len(encoded)) + "\r\n")
        self.assertEqual(mcp_remote.powershell_result(rendered.encode(), nonce), ("Résultat\nligne 2", 0))
        self.assertIsNone(mcp_remote.powershell_result(rendered[:200].encode(), nonce))
        broken = rendered.replace(encoded[10:], encoded[11:])
        with self.assertRaises(mcp_remote.ChannelError):
            mcp_remote.powershell_result(broken.encode(), nonce)


class TestFiles(RemoteTest):
    def test_write_read_list_organize_delete(self):
        host = self.device()
        client = self.client(["list_files", "read_file", "write_file", "manage_files", "delete_file"])
        home = client.data("list_files", device=host)
        self.assertEqual(home["os"], "linux")
        path = os.path.join(self.folder, "notes.txt")
        self.assertEqual(client.data("write_file", device=host, path=path, content="première ligne\n")["written"], 16)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "première ligne\n")
        read = client.data("read_file", device=host, path=path)
        self.assertEqual((read["content"], read["complete"]), ("première ligne\n", True))
        names = [e["name"] for e in client.data("list_files", device=host, path=self.folder)["entries"]]
        self.assertIn("notes.txt", names)
        client.data("manage_files", device=host, operation="mkdir", path=os.path.join(self.folder, "archive"))
        client.data("manage_files", device=host, operation="move", path=path, to=os.path.join(self.folder, "archive"))
        moved = os.path.join(self.folder, "archive", "notes.txt")
        self.assertTrue(os.path.exists(moved))
        binary = base64.b64encode(bytes(range(256)) * 4).decode()
        client.data("write_file", device=host, path=moved, content=binary, encoding="base64")
        self.assertTrue(client.data("read_file", device=host, path=moved)["binary"])
        self.assertEqual(client.data("read_file", device=host, path=moved, encoding="base64", offset=256, max_bytes=256)["content_base64"],
                         base64.b64encode(bytes(range(256))).decode())
        missing = client.call("read_file", device=host, path=os.path.join(self.folder, "absent"))
        self.assertTrue(missing["isError"])
        self.assertIn("absent", missing["content"][0]["text"])
        client.data("delete_file", device=host, path=os.path.join(self.folder, "archive"))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "archive")))
        self.assertTrue(client.call("delete_file", device=host, path="C:\\")["isError"])
        with transaction() as conn:
            actions = {r["action"] for r in conn.execute("SELECT action FROM audit WHERE action LIKE 'parc.fichiers.%'")}
        self.assertEqual(actions, {"parc.fichiers.ecriture", "parc.fichiers.modification", "parc.fichiers.suppression"})


class TestFileEncodings(RemoteTest):
    """write_file écrit les octets voulus : UTF-8 SANS BOM par défaut (Windows compris), BOM, ASCII strict, UTF-16 et fins
    de ligne au choix ; push_file / pull_file copient d'un poste à l'autre sans passer par l'assistant."""

    def read(self, name):
        with open(os.path.join(self.folder, name), "rb") as f:
            return f.read()

    def test_charsets_and_line_endings(self):
        host = self.device()
        client = self.client(["write_file"])
        java = "class Main { String s = \"é\"; }\n"
        written = client.data("write_file", device=host, path=os.path.join(self.folder, "Main.java"), content=java)
        self.assertEqual(self.read("Main.java"), java.encode("utf-8"))                       # aucun BOM
        self.assertEqual(written["sha256"], __import__("hashlib").sha256(java.encode()).hexdigest())
        client.data("write_file", device=host, path=os.path.join(self.folder, "bom.txt"), content="é", charset="utf-8-bom")
        self.assertEqual(self.read("bom.txt"), b"\xef\xbb\xbf\xc3\xa9")
        client.data("write_file", device=host, path=os.path.join(self.folder, "run.bat"), content="@echo off\necho ok\n",
                    charset="ascii", newline="crlf")
        self.assertEqual(self.read("run.bat"), b"@echo off\r\necho ok\r\n")
        refused = client.call("write_file", device=host, path=os.path.join(self.folder, "a.txt"), content="café", charset="ascii")
        self.assertTrue(refused["isError"])
        self.assertIn("not ascii", refused["content"][0]["text"])
        self.assertFalse(os.path.exists(os.path.join(self.folder, "a.txt")))
        client.data("write_file", device=host, path=os.path.join(self.folder, "u.reg"), content="é", charset="utf-16le-bom")
        self.assertEqual(self.read("u.reg"), b"\xff\xfe\xe9\x00")
        client.data("write_file", device=host, path=os.path.join(self.folder, "lf.sh"), content="a\r\nb\r\n", newline="lf")
        self.assertEqual(self.read("lf.sh"), b"a\nb\n")

    def test_copies_between_a_device_and_an_android_device(self):
        """push_file from_device : le fichier va du poste source à l'appareil Android sans passer par l'assistant ;
        pull_file to_device : l'inverse. adb est simulé ; le gestionnaire de fichiers de l'agent est le vrai."""
        import hashlib
        host = self.device()
        phone = Agent(dict(LINUX, hostname="tablette-adb", os={"family": "android", "name": "Android 16"}), "192.168.43.75")
        phone.enroll()
        client = self.client(["push_file", "pull_file", "read_file", "write_file"])
        source = os.path.join(self.folder, "classes.dex")
        blob = os.urandom(700000)                                         # plusieurs morceaux du gestionnaire de fichiers
        with open(source, "wb") as f:
            f.write(blob)
        device_side = os.path.join(self.folder, "on-device.dex")

        def fake_push(dev, serial, local, path, mode=None):
            with open(local, "rb") as src, open(device_side, "wb") as dst:
                data = src.read()
                dst.write(data)
            return len(data), hashlib.sha256(data).hexdigest()
        with mock.patch.object(mcp_remote, "_adb_serial", return_value="serie"), \
                mock.patch.object(mcp_remote, "_adb_push_file", side_effect=fake_push), \
                mock.patch.object(mcp_remote, "_adb_get", return_value=blob[:1000]):
            pushed = client.data("push_file", device="tablette-adb", path="/data/local/tmp/classes.dex",
                                 from_device=host, from_path=source)
            self.assertEqual((pushed["written"], pushed["sha256"]), (len(blob), hashlib.sha256(blob).hexdigest()))
            self.assertEqual(self.read("on-device.dex"), blob)
            copied = client.data("pull_file", device="tablette-adb", path="/sdcard/log.txt", to_device=host,
                                 to_path=os.path.join(self.folder, "log-copie.txt"))
            self.assertEqual(copied["copied_to"]["path"], os.path.join(self.folder, "log-copie.txt"))
            self.assertEqual(self.read("log-copie.txt"), blob[:1000])
        no_files = self.client(["push_file"])
        with mock.patch.object(mcp_remote, "_adb_serial", return_value="serie"):
            refused = no_files.call("push_file", device="tablette-adb", path="/data/local/tmp/x", from_device=host, from_path=source)
        self.assertTrue(refused["isError"])
        self.assertIn("file tools", refused["content"][0]["text"])


class TestDesktop(RemoteTest):
    def check_picture(self, result, width=64):
        image = next(c for c in result["content"] if c["type"] == "image")
        w, h, rgb = decode_png(base64.b64decode(image["data"]))
        self.assertEqual((w, h), (width, 40))
        for x, y in ((0, 0), (5, 7), (width - 1, 39)):
            self.assertEqual(rgb[(y * w + x) * 3:(y * w + x) * 3 + 3], bytes((200, (3 * y) % 256, (2 * x) % 256)))
        return result["structuredContent"]

    def test_screenshot_linux_tight_and_windows_raw(self):
        linux = self.device()
        windows = self.device(dict(WINDOWS, agent_version="2.114.0"), "192.168.43.50")        # agent d'avant : brut
        client = self.client(["desktop_screenshot"])
        info = self.check_picture(client.call("desktop_screenshot", device=linux))
        self.assertEqual((info["screen_width"], info["screen_height"], info["scale"]), (64, 40, 1))
        self.check_picture(client.call("desktop_screenshot", device=windows))
        zoom = client.call("desktop_screenshot", device=linux, region="5,7,10,4")
        image = next(c for c in zoom["content"] if c["type"] == "image")
        w, h, rgb = decode_png(base64.b64decode(image["data"]))
        self.assertEqual((w, h, rgb[:3]), (10, 4, bytes((200, 21, 10))))

    def require_consent(self, host):
        with transaction() as conn:
            conn.execute("UPDATE devices SET remote_consent=1 WHERE hostname=?", (host,))

    def test_desktop_waits_for_the_approval_the_device_requires(self):
        os.environ["LOCO_TEST_CONSENT"] = "given"
        self.addCleanup(os.environ.pop, "LOCO_TEST_CONSENT", None)
        host = self.device()
        self.require_consent(host)
        client = self.client(["desktop_screenshot", "run_command"])
        self.check_picture(client.call("desktop_screenshot", device=host))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT consent, owner FROM remote_sessions WHERE kind='desktop'").fetchone()["consent"], "given")
            actions = [r["detail"] for r in conn.execute("SELECT detail FROM audit WHERE action='parc.session.accord' ORDER BY id DESC LIMIT 1")]
        self.assertIn("accepté sur le poste", actions[0])

    def test_desktop_refused_by_the_person_gives_the_reason(self):
        os.environ["LOCO_TEST_CONSENT"] = "refused"
        self.addCleanup(os.environ.pop, "LOCO_TEST_CONSENT", None)
        host = self.device()
        self.require_consent(host)
        result = self.client(["desktop_screenshot"]).call("desktop_screenshot", device=host)
        self.assertTrue(result["isError"])
        self.assertRegex(result["content"][0]["text"], "refusé|declined|rechazó")

    def test_desktop_on_a_device_with_an_old_agent_is_refused_when_approval_is_required(self):
        host = self.device(dict(LINUX, agent_version="2.136.0"))
        self.require_consent(host)
        result = self.client(["desktop_screenshot"]).call("desktop_screenshot", device=host)
        self.assertTrue(result["isError"])
        self.assertIn("must be updated", result["content"][0]["text"])

    def test_click_type_keys_and_scroll(self):
        host = self.device()
        client = self.client(["desktop_screenshot", "desktop_click", "desktop_type", "desktop_key", "desktop_scroll"])
        clicked = client.call("desktop_click", device=host, x=10, y=5, button="right")
        self.check_picture(clicked)                                   # capture renvoyée après le clic
        client.data("desktop_type", device=host, text="Ab\n", screenshot=False)
        client.data("desktop_key", device=host, keys="ctrl+alt+t", screenshot=False)
        client.data("desktop_scroll", device=host, x=3, y=3, direction="down", amount=2, screenshot=False)
        self.assertTrue(client.call("desktop_key", device=host, keys="hyper+x")["isError"])
        self.assertTrue(client.call("desktop_click", device=host, x=500, y=5)["isError"])
        deadline = time.time() + 5
        while time.time() < deadline and ["button", 5, False] not in self.logged():
            time.sleep(0.05)
        events = self.logged()
        self.assertIn(["motion", 10, 5], events)
        self.assertEqual([e for e in events if e[0] == "button" and e[1] == 3], [["button", 3, True], ["button", 3, False]])
        keys = [e[1:] for e in events if e[0] == "key"]
        self.assertEqual(keys[:6], [[0x41, True], [0x41, False], [0x62, True], [0x62, False], [0xff0d, True], [0xff0d, False]])
        self.assertEqual(keys[6:], [[0xffe3, True], [0xffe9, True], [0x74, True], [0x74, False], [0xffe9, False], [0xffe3, False]])
        self.assertEqual(sum(1 for e in events if e == ["button", 5, True]), 2)

    def test_only_changed_tiles_travel_after_the_first_capture(self):
        os.environ["LOCO_TEST_WIDTH"], os.environ["LOCO_TEST_HEIGHT"] = "300", "200"   # 3 x 2 tuiles de 128 px
        host = self.device()
        client = self.client(["desktop_screenshot"])
        shots = []
        for _ in range(2):
            image = next(c for c in client.call("desktop_screenshot", device=host)["content"] if c["type"] == "image")
            shots.append(decode_png(base64.b64decode(image["data"])))
            state = mcp_remote._STATE[self.sessions()[0]["id"]]
            shots[-1] += (state["last_rects"],)
        (w, h, first, full), (_w, _h, second, partial) = shots
        self.assertEqual((w, h, full, partial), (300, 200, 6, 1))       # tout l'écran, puis la seule tuile modifiée
        pixel = lambda rgb, x, y: rgb[(y * w + x) * 3:(y * w + x) * 3 + 3]             # noqa: E731
        self.assertNotEqual(pixel(first, 44, 24), pixel(second, 44, 24))
        self.assertEqual(pixel(second, 250, 150), bytes((200, 450 % 256, 500 % 256)))

    def test_encodings_follow_the_agent(self):
        self.assertEqual(mcp_remote.encodings({"os_family": "windows", "agent_version": "2.114.0"}), (0,))
        answer = mcp_remote.ANSWER                                       # répondre même sans changement
        self.assertEqual(mcp_remote.encodings({"os_family": "windows", "agent_version": "2.115.0"}), (7, 0, answer))
        self.assertEqual(mcp_remote.encodings({"os_family": "debian", "agent_version": "2.100.0"}), (7, 0, answer))
        self.assertEqual(mcp_remote._message(b"\0\0\0\0", 0), (4, []))      # « rien n'a changé » : une réponse vide

    def test_large_screens_are_scaled_and_clicks_mapped(self):
        self.assertEqual((mcp_remote.scale_of(1440), mcp_remote.scale_of(1920), mcp_remote.scale_of(3840)), (1, 2, 3))
        self.assertEqual(mcp_remote._point({"width": 1920, "height": 1080}, {"x": 100, "y": 50}), (201, 101))


class TestSessionLifetime(RemoteTest):
    def test_assistant_sessions_outlive_browser_ones_and_devices_are_warmed(self):
        Agent(LINUX, "192.168.43.60").enroll()                           # sans boucle d'agent : sessions inertes
        now = time.time()
        with transaction() as conn:
            dev = conn.execute("SELECT id FROM devices").fetchone()[0]
            user = conn.execute("SELECT id, pw_version FROM users WHERE username='admin'").fetchone()
            for sid, owner, kind, seen in (("a" * 32, "mcp:1", "terminal", now - 100), ("b" * 32, "mcp:1", "desktop", now - 150),
                                           ("c" * 32, "f" * 64, "terminal", now - 61)):
                conn.execute("INSERT INTO remote_sessions (id,device_id,user_id,owner,pw_version,kind,state,created,browser_seen,"
                             "agent_seen,expires) VALUES (?,?,?,?,?,?,'active',?,?,?,?)",
                             (sid, dev, user[0], owner, user[1], kind, now, seen, now, now + 3600))
        with fixture.loco.app.test_request_context():
            db().execute("BEGIN IMMEDIATE")
            remote._prune()
            db().commit()
        states = {r["id"][0]: r["state"] for r in self.sessions()}         # fermée depuis plus de 2 min : effacée
        self.assertEqual((states["a"], states.get("b", "closed"), states["c"]), ("active", "closed", "closed"))
        token, _ = create(self.admin, "Lecture", ["get_device"])
        Client(token).data("get_device", device="srv-web")
        with transaction() as conn:
            self.assertGreater(conn.execute("SELECT live_until FROM devices").fetchone()[0], time.time() + 50)

    def test_a_person_takes_the_place_of_idle_assistant_sessions(self):
        host = self.device()
        client = self.client(["terminal_run", "terminal_interact"])
        client.data("terminal_run", device=host, command="true")
        self.assertIn("\navant", client.data("terminal_interact", device=host, input="echo avant\n")["screen"])
        tty = next(r["id"] for r in self.sessions() if r["owner"].endswith(":tty"))
        with transaction() as conn:                                      # plus aucun appel en cours, l'interactif d'abord
            conn.execute("UPDATE remote_sessions SET browser_seen=browser_seen-(CASE WHEN id=? THEN 20 ELSE 10 END)", (tty,))
            dev = conn.execute("SELECT id FROM devices WHERE hostname=?", (host,)).fetchone()[0]
        self.assertEqual(self.admin.post(f"/fleet/{dev}/session", {"kind": "terminal"}, page="/fleet").status_code, 302)
        states = {r["id"]: r["state"] for r in self.sessions()}
        self.assertEqual((states[tty], sum(1 for s in states.values() if s != "closed")), ("closed", 2))
        # La saisie prévue pour l'ancienne session (vim ouvert…) ne part pas dans le shell neuf.
        lost = client.data("terminal_interact", device=host, input="echo perdu\n")
        self.assertIn("NOT typed", lost["note"])
        self.assertNotIn("perdu", lost["screen"])
        self.assertNotIn(tty, mcp_remote._STATE)                         # image et écran de la session fermée libérés
        again = client.data("terminal_interact", device=host, input="echo retrouve\n")
        self.assertNotIn("note", again)
        self.assertIn("\nretrouve", again["screen"])

    def test_windows_agents_serve_four_sessions(self):
        Agent(WINDOWS, "192.168.43.50").enroll()                         # sans boucle d'agent : sessions inertes
        now = time.time()
        with transaction() as conn:
            dev = conn.execute("SELECT id FROM devices WHERE hostname='pc-compta'").fetchone()[0]
            user = conn.execute("SELECT id, pw_version FROM users WHERE username='admin'").fetchone()
            for n, kind in enumerate(("desktop", "desktop", "files", "files")):
                conn.execute("INSERT INTO remote_sessions (id,device_id,user_id,owner,pw_version,kind,state,created,browser_seen,"
                             "agent_seen,expires) VALUES (?,?,?,?,?,?,'active',?,?,?,?)",
                             (str(n) * 32, dev, user[0], "f" * 64, user[1], kind, now, now - 30, now, now + 3600))
        result = self.client(["terminal_run"]).call("terminal_run", device="pc-compta", command="hostname")
        self.assertTrue(result["isError"])
        self.assertIn("Four remote sessions", result["content"][0]["text"])


class TestRemoteAccessControl(RemoteTest):
    def test_rights_session_ownership_and_revocation(self):
        host = self.device()
        token, _ = create(self.admin, "Lecture", ["list_devices"])
        self.assertNotIn("terminal_run", Client(token).names())
        marie = self.operator()                                        # profil sans accès distant
        page = marie.c.get("/accounts/mcp").get_data(as_text=True)
        self.assertIn('value="terminal_run" role="switch" disabled', page)
        client = self.client(["terminal_run"])
        self.assertEqual(client.data("terminal_run", device=host, command="echo ok")["output"], "ok")
        sid = self.sessions()[0]["id"]
        # Une session de l'assistant n'est pas joignable depuis un navigateur (propriétaire différent).
        r = self.admin.post(f"/fleet/session/{sid}/io", {"ack": 0}, page="/fleet")
        self.assertEqual(r.status_code, 404)
        with transaction() as conn:
            token_id = conn.execute("SELECT id FROM mcp_tokens WHERE name='Distant'").fetchone()[0]
        self.admin.post(f"/accounts/mcp/{token_id}/revoke", page="/accounts/mcp")
        self.assertEqual(self.sessions()[0]["state"], "closed")


if __name__ == "__main__":
    unittest.main()
