"""Assistants IA (MCP) : terminal, fichiers et bureau par le relais des sessions distantes, face au vrai code de
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

from test_fleet import LINUX, WINDOWS, Agent
from test_mcp import Client, McpTest, create
from loco import mcp_remote
from loco.db import transaction
import fake_agent

HERE = os.path.dirname(os.path.abspath(__file__))
REMOTE = ["terminal_run", "list_files", "read_file", "write_file", "manage_files", "delete_file",
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
        mcp_remote._RFB.clear()
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

    def test_timeout_interrupts_and_the_terminal_stays_usable(self):
        host = self.device()
        client = self.client(["terminal_run"])
        late = client.data("terminal_run", device=host, command="echo avant; sleep 30", timeout=1)
        self.assertTrue(late["timed_out"])
        self.assertIn("avant", late["output"])
        self.assertEqual(client.data("terminal_run", device=host, command="echo après")["output"], "après")


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
        windows = self.device(WINDOWS, "192.168.43.50")
        client = self.client(["desktop_screenshot"])
        info = self.check_picture(client.call("desktop_screenshot", device=linux))
        self.assertEqual((info["screen_width"], info["screen_height"], info["scale"]), (64, 40, 1))
        self.check_picture(client.call("desktop_screenshot", device=windows))
        zoom = client.call("desktop_screenshot", device=linux, region="5,7,10,4")
        image = next(c for c in zoom["content"] if c["type"] == "image")
        w, h, rgb = decode_png(base64.b64decode(image["data"]))
        self.assertEqual((w, h, rgb[:3]), (10, 4, bytes((200, 21, 10))))

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

    def test_large_screens_are_scaled_and_clicks_mapped(self):
        self.assertEqual((mcp_remote.scale_of(1440), mcp_remote.scale_of(1920), mcp_remote.scale_of(3840)), (1, 2, 3))
        self.assertEqual(mcp_remote._point({"width": 1920, "height": 1080}, {"x": 100, "y": 50}), (201, 101))


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
