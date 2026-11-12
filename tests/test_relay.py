"""Relais à connexions persistantes : même protocole que les allers-retours HTTPS, mais le serveur prévient."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time
import unittest
from unittest import mock

from test_fleet import FleetTest, Agent, LINUX, device
from loco import relay
from loco.db import transaction

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loco_client_relay", ROOT / "portal/agents/loco-client.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class RelayTest(FleetTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.quiet = mock.patch.object(relay, "PRESENCE", 3600)       # pas de rappel de présence au milieu d'un test lent
        cls.quiet.start()
        cls.relay = relay.Relay()
        assert cls.relay.start(0)
        cls.base = f"http://127.0.0.1:{cls.relay.port}"

    @classmethod
    def tearDownClass(cls):
        cls.relay.stop()
        cls.quiet.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.agent = Agent(LINUX, "192.168.43.70")
        self.agent.enroll()
        self.dev_id = device(self.agent.uid)["id"]
        self.sockets = []

    def tearDown(self):
        for item in self.sockets:
            item.close()
        super().tearDown()

    def agent_socket(self, token=None):
        item = agent.RemoteSocket(self.base, token or self.agent.token)
        self.sockets.append(item)
        return item

    def ask(self, item, sessions=()):
        item.send(json.dumps({"sessions": list(sessions)}).encode())
        while True:
            answer = json.loads(item.recv(5))
            if not answer.get("wake"):
                return answer

    def open(self, kind="terminal"):
        response = self.admin.post(f"/fleet/{self.dev_id}/session", {"kind": kind})
        self.assertEqual(response.status_code, 302)
        return response.headers["Location"].rsplit("/", 1)[1]

    def browser(self, sid, origin=None, cookie=True):
        """Navigateur réduit à l'essentiel : poignée de main avec les cookies de la session admin, trames masquées."""
        raw = socket.create_connection(("127.0.0.1", self.relay.port), timeout=5)
        self.sockets.append(raw)
        host = f"127.0.0.1:{self.relay.port}"
        jar = "; ".join(f"{c.key}={c.value}" for c in self._cookies()) if cookie else ""
        key = base64.b64encode(os.urandom(16)).decode()
        raw.sendall((f"GET /fleet/session/{sid}/ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin or 'http://' + host}\r\n"
                     + (f"Cookie: {jar}\r\n" if jar else "") + "\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(4096)
            if not chunk:
                break
            head += chunk
        return raw, head.split(b"\r\n", 1)[0].decode(), head.partition(b"\r\n\r\n")[2]

    def _cookies(self):
        client = self.admin.c
        if hasattr(client, "cookie_jar") and client.cookie_jar is not None:        # Werkzeug < 2.3
            return [type("C", (), {"key": c.name, "value": c.value}) for c in client.cookie_jar]
        return [client.get_cookie(name) for name in ("loco_session",) if client.get_cookie(name)]

    @staticmethod
    def say(raw, message):
        data, key = json.dumps(message).encode(), os.urandom(4)
        raw.sendall(relay.frame(1, data, key))

    @staticmethod
    def hear(raw, rest=b"", timeout=5):
        raw.settimeout(timeout)
        buffer = rest
        while True:
            if len(buffer) >= 2:
                size, offset = buffer[1] & 0x7F, 2
                if size == 126 and len(buffer) >= 4:
                    size, offset = struct.unpack("!H", buffer[2:4])[0], 4
                elif size == 127 and len(buffer) >= 10:
                    size, offset = struct.unpack("!Q", buffer[2:10])[0], 10
                if size < 126 or offset > 2:
                    if len(buffer) >= offset + size:
                        opcode, data, buffer = buffer[0] & 0x0F, buffer[offset:offset + size], buffer[offset + size:]
                        if opcode == 8:
                            raise ConnectionError("fermé par le serveur")
                        if opcode > 2:                      # ping, pong
                            continue
                        return json.loads(data), buffer
            chunk = raw.recv(65536)
            if not chunk:
                raise ConnectionError("fermé")
            buffer += chunk

    def csrf(self):
        with self.admin.c.session_transaction() as s:
            return s["csrf"]


class TestFraming(unittest.TestCase):
    def test_masking_is_its_own_inverse(self):
        key = b"\x01\x02\x03\x04"
        for data in (b"", b"a", b"abcd", b"abcde", os.urandom(70000)):
            self.assertEqual(relay.mask(relay.mask(data, key), key), data)
            self.assertEqual(agent.RemoteSocket.mask(data, key), relay.mask(data, key))

    def test_frame_lengths(self):
        self.assertEqual(relay.frame(1, b"abc"), b"\x81\x03abc")
        self.assertEqual(relay.frame(1, b"x" * 300)[:4], b"\x81\x7e\x01\x2c")
        self.assertEqual(relay.frame(1, b"x" * 70000)[:10], b"\x81\x7f" + struct.pack("!Q", 70000))
        self.assertEqual(relay.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")       # exemple de la RFC 6455


class TestServing(unittest.TestCase):
    def test_a_taken_port_leaves_the_portal_without_relay(self):
        taken = socket.socket()
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        other = relay.Relay()
        self.assertFalse(other.start(taken.getsockname()[1]))            # le portail sert quand même : tout passe par HTTPS
        other.stop()

    def test_nginx_sends_only_the_two_paths_to_the_relay_with_the_usual_access_rules(self):
        portal = (ROOT / "conf/nginx-portal.conf").read_text(encoding="utf-8")
        agent_block = portal[portal.index("location = /agent/relay {"):].split("}", 1)[0]
        self.assertIn("include snippets/loco-clients.conf;", agent_block)            # réseaux des postes, comme /agent/
        self.assertIn("include snippets/loco-relay.conf;", agent_block)
        browser_block = portal[portal.index("location ~ ^/fleet/session/[0-9a-f]+/ws$ {"):].split("}", 1)[0]
        self.assertIn("include snippets/loco-admin.conf;", browser_block)            # réseaux de l'interface
        self.assertIn("include snippets/loco-relay.conf;", browser_block)
        self.assertEqual(portal.count("loco-relay.conf"), 2)
        self.assertNotIn("loco-relay", (ROOT / "conf/nginx-portal-http.conf").read_text(encoding="utf-8"))   # jamais en clair
        snippet = (ROOT / "conf/nginx-relay-snippet.conf").read_text(encoding="utf-8")
        for line in ("proxy_pass http://127.0.0.1:@RELAY_PORT@;", "proxy_set_header Upgrade $http_upgrade;",
                     "proxy_set_header X-Forwarded-For $remote_addr;", 'proxy_set_header X-Forwarded-Host "";'):
            self.assertIn(line, snippet)
        install = (ROOT / "linux/install.sh").read_text(encoding="utf-8")
        self.assertIn('render "$PREFIX/conf/nginx-relay-snippet.conf" > /etc/nginx/snippets/loco-relay.conf', install)
        self.assertIn('LRT_RELAY_PORT="$RELAY_PORT"', install)
        windows = (ROOT / "portal/locowin.py").read_text(encoding="utf-8")
        self.assertIn('"snippets/loco-relay.conf": tpl("nginx-relay-snippet.conf")', windows)

    def test_stopping_the_service_does_not_wait_for_the_persistent_connections(self):
        # Vu sur une appliance : nginx attendait dix secondes que les agents rendent leur connexion, le service mettait
        # douze secondes à s'arrêter, et le supervisord relancé entre-temps échouait quatre fois sur les cinq permises.
        for name in ("linux/conf/supervisord-alpine.conf", "linux/docker/supervisord.conf"):
            block = (ROOT / name).read_text(encoding="utf-8").split("[program:nginx]", 1)[1].split("[program:", 1)[0]
            self.assertIn("\nstopsignal=TERM\n", block, name)
            self.assertNotIn("stopsignal=QUIT", block, name)
        alpine = (ROOT / "linux/lib/alpine.sh").read_text(encoding="utf-8")
        stop, wait, start = (alpine.index("rc-service loco stop"), alpine.index('kill -0 "$(cat /run/loco-supervisord.pid'),
                             alpine.index("rc-service loco start"))
        self.assertLess(stop, wait)                       # arrêt, attente de la fin réelle de l'ancien, puis démarrage
        self.assertLess(wait, start)
        self.assertNotIn("rc-service loco restart", alpine)

    def test_the_three_agents_keep_https_as_a_fallback(self):
        csharp = (ROOT / "windows/client/RemoteControl.cs").read_text(encoding="utf-8")
        self.assertIn("return Agent.RemoteExchange(payload);", csharp)                # canal absent ou rompu : HTTPS
        self.assertIn("RemoteChannel.cs", (ROOT / "windows/client/build.sh").read_text(encoding="utf-8"))
        kotlin = (ROOT / "android/client/app/src/main/kotlin/org/loco/agent/RemoteClient.kt").read_text(encoding="utf-8")
        self.assertIn('return Net.postJson("$server/agent/api/remote"', kotlin)
        relay_socket = (ROOT / "android/client/app/src/main/kotlin/org/loco/agent/RelaySocket.kt").read_text(encoding="utf-8")
        self.assertIn('endpointIdentificationAlgorithm = "HTTPS"', relay_socket)      # le nom du certificat est vérifié aussi


class TestProcessWaits(unittest.TestCase):
    def test_a_session_asks_for_an_exchange_when_its_process_speaks(self):
        process = agent.RemoteProcess.__new__(agent.RemoteProcess)
        process.state, process.process, process.pending, process.reader, process.consent_pipe = "active", object(), b"", 7, None
        self.assertEqual(process.waits(), [7])
        process.pending = b"x"                              # paquet pas encore acquitté : rien à lire de plus pour l'instant
        self.assertEqual(process.waits(), [])
        process.pending, process.consent_pipe = b"", 9      # réponse à la demande d'accord attendue
        self.assertEqual(process.waits(), [7, 9])
        process.state = "closed"
        self.assertEqual(process.waits(), [9])


class TestAgentChannel(RelayTest):
    def test_same_answer_as_over_https(self):
        answer = self.ask(self.agent_socket())
        self.assertEqual(answer["sessions"], [])
        self.assertIn("interval", answer)

    def test_a_wrong_token_is_refused_like_over_https(self):
        item = self.agent_socket(token=self.agent.uid + ".faux")
        item.send(b'{"sessions": []}')
        self.assertEqual(json.loads(item.recv(5)), {"status": 401})

    def test_only_the_two_relay_paths_exist(self):
        raw = socket.create_connection(("127.0.0.1", self.relay.port), timeout=5)
        self.sockets.append(raw)
        raw.sendall(b"GET /fleet HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nSec-WebSocket-Key: a\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.assertTrue(raw.recv(200).startswith(b"HTTP/1.1 404"))

    def test_pings_are_answered_and_fragments_joined(self):
        raw = socket.create_connection(("127.0.0.1", self.relay.port), timeout=5)
        self.sockets.append(raw)
        key = base64.b64encode(os.urandom(16)).decode()
        raw.sendall((f"GET /agent/relay HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer {self.agent.token}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += raw.recv(4096)
        self.assertIn(relay.accept_key(key).encode(), head)
        mask = os.urandom(4)
        raw.sendall(relay.frame(9, b"vivant", mask))                                         # ping
        self.assertEqual(raw.recv(8), b"\x8a\x06vivant")                                      # pong, même contenu
        first, rest = b'{"sess', b'ions": []}'
        raw.sendall(bytes([0x01, 0x80 | len(first)]) + mask + relay.mask(first, mask))        # texte, sans FIN
        raw.sendall(bytes([0x80, 0x80 | len(rest)]) + mask + relay.mask(rest, mask))          # suite, avec FIN
        answer, _ = RelayTest.hear(raw)
        self.assertEqual(answer["sessions"], [])

    def test_an_unmasked_or_oversized_frame_closes_the_connection(self):
        for frame in (b"\x81\x02{}", bytes([0x81, 0x80 | 127]) + struct.pack("!Q", relay.MAX_MESSAGE + 1) + b"abcd"):
            item = self.agent_socket()
            item.sock.sendall(frame)
            with self.assertRaises(OSError):                 # fermée par le serveur, pas seulement muette
                self.assertIsNotNone(item.recv(3))

    def test_a_requested_session_wakes_the_agent_at_once(self):
        item = self.agent_socket()
        self.ask(item)
        started = time.monotonic()
        sid = self.open()
        self.assertEqual(json.loads(item.recv(5)), {"wake": True})                 # prévenu, sans attendre son prochain relevé
        self.assertLess(time.monotonic() - started, 1.5)
        answer = self.ask(item)
        self.assertEqual([(s["id"], s["state"]) for s in answer["sessions"]], [(sid, "waiting")])

    def test_a_queued_action_wakes_the_agent(self):
        item = self.agent_socket()
        self.ask(item)
        self.admin.post(f"/fleet/{self.dev_id}/action", {"action": "scan"}, page=f"/fleet/{self.dev_id}")
        self.assertEqual(json.loads(item.recv(5)), {"wake": True})
        self.assertTrue(self.ask(item).get("checkin"))

    def test_another_machine_is_left_alone(self):
        other = Agent(dict(LINUX, hostname="voisin"), "192.168.43.71")
        other.enroll()
        quiet = self.agent_socket(token=other.token)
        self.ask(quiet)
        mine = self.agent_socket()
        self.ask(mine)
        self.open()
        self.assertEqual(json.loads(mine.recv(5)), {"wake": True})
        self.assertIsNone(quiet.recv(0.3))

    def test_the_agent_link_uses_the_channel_then_falls_back(self):
        link = agent.RemoteLink()
        self.addCleanup(link.close)
        answer = link.exchange(self.base, {"sessions": []}, self.agent.token)
        self.assertTrue(link.persistent)
        self.assertEqual(answer["sessions"], [])
        # Canal rompu : le même échange repart par HTTPS, sans erreur pour l'appelant.
        link.socket.sock.close()
        seen = {}

        def https(server, data, token):
            seen["data"] = data
            return {"sessions": [], "interval": 5}
        link.http.exchange = https
        self.assertEqual(link.exchange(self.base, {"sessions": []}, self.agent.token)["interval"], 5)
        self.assertFalse(link.persistent)
        self.assertEqual(seen["data"], {"sessions": []})

    def test_a_server_without_relay_keeps_https(self):
        link = agent.RemoteLink()
        self.addCleanup(link.close)
        link.http.exchange = lambda server, data, token: {"sessions": [], "interval": 5}
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        self.assertEqual(link.exchange(f"http://127.0.0.1:{port}", {"sessions": []}, "x.y")["interval"], 5)
        self.assertFalse(link.persistent)
        self.assertGreater(link.retry_at, time.monotonic() + 20)                   # nouvel essai plus tard, pas à chaque échange

    def test_wait_returns_when_the_server_wakes_or_a_process_speaks(self):
        link = agent.RemoteLink()
        self.addCleanup(link.close)
        link.exchange(self.base, {"sessions": []}, self.agent.token)
        started = time.monotonic()
        threading.Timer(0.3, self.open).start()
        link.wait(10, [], [False])
        self.assertLess(time.monotonic() - started, 3)
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        link.exchange(self.base, {"sessions": []}, self.agent.token)
        link.woken = False
        started = time.monotonic()
        threading.Timer(0.2, os.write, (writer, b"x")).start()
        link.wait(10, [reader], [False])
        self.assertLess(time.monotonic() - started, 3)


class TestBrowserChannel(RelayTest):
    def test_only_a_page_of_the_portal_opens_the_channel(self):
        sid = self.open()
        _raw, status, _rest = self.browser(sid, origin="https://ailleurs.example")
        self.assertIn("403", status)

    def test_without_the_session_cookie_nothing_is_served(self):
        sid = self.open()
        raw, status, rest = self.browser(sid, cookie=False)
        self.assertIn("101", status)
        self.say(raw, {"re": 1, "csrf": "x", "ack": 0, "cols": 100, "rows": 30})
        answer, _ = self.hear(raw, rest)
        self.assertEqual((answer["re"], answer["body"]), (1, None))
        self.assertIn(answer["status"], (302, 400, 401, 403))

    def test_keystrokes_and_output_cross_without_polling(self):
        sid = self.open()
        item = self.agent_socket()
        waiting = self.ask(item)["sessions"][0]
        self.assertEqual(waiting["state"], "waiting")
        raw, status, rest = self.browser(sid)
        self.assertIn("101", status)
        self.say(raw, {"re": 1, "csrf": self.csrf(), "ack": 0, "cols": 120, "rows": 40})
        answer, rest = self.hear(raw, rest)
        self.assertEqual((answer["re"], answer["status"], answer["body"]["state"]), (1, 200, "waiting"))
        # L'agent prend la session : le navigateur l'apprend sans rien demander.
        reply = self.ask(item, [{"id": sid, "ack": 0, "seq": 1, "data": base64.b64encode(b"$ ").decode(), "state": "active"}])
        self.assertEqual((reply["sessions"][0]["cols"], reply["sessions"][0]["rows"]), (120, 40))
        pushed, rest = self.hear(raw, rest)
        self.assertNotIn("re", pushed)
        self.assertEqual(pushed["body"]["state"], "active")
        self.assertEqual([base64.b64decode(p["data"]) for p in pushed["body"]["packets"]], [b"$ "])
        # Une frappe : l'agent est prévenu, puis la reçoit à son échange.
        self.say(raw, {"re": 2, "csrf": self.csrf(), "ack": 1, "cols": 120, "rows": 40, "seq": 1, "data": base64.b64encode(b"ls\n").decode()})
        while True:                                                                # (une réponse poussée a pu s'intercaler)
            answer, rest = self.hear(raw, rest)
            if answer.get("re") == 2:
                break
        self.assertEqual(answer["body"]["ack"], 1)
        while True:                                                                # un réveil au moins (un autre a pu précéder)
            message = json.loads(item.recv(5))
            if message.get("wake"):
                break
        got = self.ask(item, [{"id": sid, "ack": 0, "seq": 1, "data": "", "state": "active"}])["sessions"][0]
        self.assertEqual([base64.b64decode(p["data"]) for p in got["packets"]], [b"ls\n"])

    def test_closing_from_the_browser_reaches_the_agent(self):
        sid = self.open()
        item = self.agent_socket()
        self.ask(item, [])
        self.ask(item, [{"id": sid, "ack": 0, "seq": 0, "data": "", "state": "active"}])
        raw, _status, rest = self.browser(sid)
        self.say(raw, {"re": 1, "csrf": self.csrf(), "close": 1})
        answer, _ = self.hear(raw, rest)
        self.assertEqual(answer["body"]["state"], "closed")
        while True:
            message = json.loads(item.recv(5))
            if message.get("wake"):
                break
        states = [s["state"] for s in self.ask(item, [{"id": sid, "ack": 0, "seq": 0, "data": "", "state": "active"}])["sessions"]]
        self.assertEqual(states, ["closed"])

    def test_an_agent_still_polling_over_https_reaches_the_browser_at_once(self):
        # Agent pas encore à jour (ou canal coupé de son côté) : ses allers-retours écrivent en base, le relais le voit et
        # pousse quand même vers le navigateur.
        sid = self.open()
        raw, _status, rest = self.browser(sid)
        self.say(raw, {"re": 1, "csrf": self.csrf(), "ack": 0, "cols": 100, "rows": 30})
        answer, rest = self.hear(raw, rest)
        self.assertEqual(answer["body"]["state"], "waiting")
        started = time.monotonic()
        polled = self.agent.post("/agent/api/remote", {"sessions": [{"id": sid, "ack": 0, "seq": 1, "state": "active",
                                                                    "data": base64.b64encode(b"bonjour").decode()}]}, self.agent.token)
        self.assertEqual(polled.status_code, 200)
        pushed, rest = self.hear(raw, rest)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual([base64.b64decode(p["data"]) for p in pushed["body"]["packets"]], [b"bonjour"])

    def test_a_logged_out_account_loses_the_channel(self):
        sid = self.open()
        raw, _status, rest = self.browser(sid)
        token = self.csrf()
        self.say(raw, {"re": 1, "csrf": token, "ack": 0, "cols": 100, "rows": 30})
        answer, rest = self.hear(raw, rest)
        self.assertEqual(answer["status"], 200)
        self.admin.post("/logout", {})
        # Le cookie gardé par le canal ne vaut plus rien : l'échange est refusé, ou le canal est déjà fermé par le serveur
        # (le refus a pu être poussé juste avant, avec la fermeture de la session).
        try:
            self.say(raw, {"re": 2, "csrf": token, "ack": 0, "cols": 100, "rows": 30})
            while True:
                answer, rest = self.hear(raw, rest)
                if answer.get("re") == 2:
                    break
            self.assertEqual(answer["body"], None)
            self.assertNotEqual(answer["status"], 200)
        except (ConnectionError, OSError):
            pass
        with self.assertRaises((ConnectionError, OSError, AssertionError)):       # et plus rien n'est servi ensuite
            self.say(raw, {"re": 3, "csrf": token, "ack": 0, "cols": 100, "rows": 30})
            answer, rest = self.hear(raw, rest)
            self.assertEqual(answer["status"], 200)

    def test_a_connected_browser_stays_present_even_when_it_says_nothing(self):
        sid = self.open()
        raw, _status, rest = self.browser(sid)
        self.say(raw, {"re": 1, "csrf": self.csrf(), "ack": 0, "cols": 100, "rows": 30})
        _answer, rest = self.hear(raw, rest)
        with transaction() as conn:
            conn.execute("UPDATE remote_sessions SET browser_seen=? WHERE id=?", (time.time() - 50, sid))
        with mock.patch.object(relay, "PRESENCE", 0.2):
            pushed, rest = self.hear(raw, rest)                                    # le relais redit la présence du navigateur
        self.assertNotIn("re", pushed)
        with transaction() as conn:
            seen = conn.execute("SELECT browser_seen FROM remote_sessions WHERE id=?", (sid,)).fetchone()[0]
        self.assertGreater(seen, time.time() - 5)

    def test_a_session_of_someone_else_is_not_served(self):
        sid = self.open()
        with transaction() as conn:
            conn.execute("UPDATE remote_sessions SET owner='autre' WHERE id=?", (sid,))
        raw, _status, rest = self.browser(sid)
        self.say(raw, {"re": 1, "csrf": self.csrf(), "ack": 0, "cols": 100, "rows": 30})
        answer, _ = self.hear(raw, rest)
        self.assertEqual((answer["status"], answer["body"]), (404, None))


if __name__ == "__main__":
    unittest.main()
