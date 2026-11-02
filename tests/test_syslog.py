"""Envoi syslog : format RFC 5424, vrais échanges UDP, TCP et TLS, Journal de loco, journaux des postes, agent Linux."""
import datetime
import json
import os
import re
import socket
import ssl
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import fake_agent
import test_portal as fixture
from test_fleet import FleetTest, Agent, LINUX, WINDOWS, device
from loco import auth, logforward, permissions
from loco.db import transaction

AGENT = fake_agent.load_agent()
PW = "Mot-de-passe-de-test-42"
RFC5424 = re.compile(r'^<(\d{1,3})>1 (\S+) (\S+) (\S+) (\S+) (\S+) (\[.*?(?<!\\)\]) (.*)$')


def config(**values):
    cfg = dict(logforward.DEFAULTS, enabled=True, host="127.0.0.1", **values)
    with transaction() as conn:
        logforward._store(conn, logforward.SETTING, cfg)
    return cfg


class Receiver:
    """Vrai serveur syslog minimal : garde ce qu'il reçoit."""

    def __init__(self, kind="tcp", context=None):
        self.kind, self.context, self.data, self.conns, self.datagrams = kind, context, b"", [], []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.1)
        if kind != "udp":
            self.sock.listen(5)
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopping:
            try:
                if self.kind == "udp":
                    self.datagrams.append(self.sock.recv(65535))
                    continue
                conn, _address = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        try:
            if self.context:
                conn = self.context.wrap_socket(conn, server_side=True)
            self.conns.append(conn)
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                self.data += chunk
        except OSError:
            pass

    def drop_connections(self):
        for conn in self.conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.conns = []

    def stop(self):
        self.stopping = True
        self.thread.join()
        self.sock.close()
        self.drop_connections()

    def wait(self, test):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not test():
            time.sleep(0.01)
        return test()


def certificate(common_name="127.0.0.1", ip="127.0.0.1"):
    """Autorité de test et certificat serveur : (PEM de l'autorité, contexte serveur)."""
    import ipaddress
    now = datetime.datetime(2026, 1, 1)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=36500))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()) + pem)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(f.name)
    os.unlink(f.name)
    return pem.decode(), context


class SyslogTest(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key LIKE 'syslog_%' OR key='lease_syslog'")
            conn.execute("DELETE FROM device_groups")
            conn.execute("DELETE FROM users WHERE username='sans-droit'")
        logforward._status.clear()
        logforward._rates.clear()
        self.reset_sender()
        self.addCleanup(self.reset_sender)

    @staticmethod
    def reset_sender():
        logforward.SENDER.close()
        logforward.SENDER.pause_until, logforward.SENDER.key = 0, None

    def receiver(self, kind="tcp", context=None):
        r = Receiver(kind, context)
        self.addCleanup(r.stop)
        return r


class TestFormat(unittest.TestCase):
    WHEN = datetime.datetime(2026, 11, 2, 13, 4, 5, 120000, datetime.timezone.utc)

    def build(self, **kw):
        values = dict(facility=16, severity=4, when=self.WHEN, host="srv-01", app_name="sshd", procid="42", msgid="ID7",
                      params={"type": "poste"}, text="Bonjour")
        values.update(kw)
        return logforward.message(**values).decode()

    def test_message_follows_rfc5424(self):
        m = RFC5424.match(self.build())
        self.assertEqual(m.groups(), (str(16 * 8 + 4), "2026-11-02T13:04:05.120Z", "srv-01", "sshd", "42", "ID7",
                                      '[loco@32473 type="poste"]', "Bonjour"))

    def test_a_device_cannot_forge_a_line_or_a_field(self):
        line = self.build(host="poste\nfaux", app_name="a b", text='ok\n<13>1 - faux - - - - injecté\x1b[31m rouge\x00',
                          params={"type": 'x"] [faux@1 a="b', "vide": ""})
        self.assertNotIn("\n", line)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\x00", line)
        m = RFC5424.match(line)
        self.assertEqual((m.group(3), m.group(4)), ("poste_faux", "a_b"))
        self.assertEqual(m.group(7), r'[loco@32473 type="x\"\] [faux@1 a=\"b"]')
        self.assertNotIn("vide", line)

    def test_empty_fields_become_the_nil_value_and_long_text_stays_valid_utf8(self):
        m = RFC5424.match(self.build(procid="", msgid=None))
        self.assertEqual((m.group(5), m.group(6)), ("-", "-"))
        raw = logforward.message(16, 6, self.WHEN, "h", "a", "-", "-", {}, "é" * 5000, limit=2048)
        self.assertLessEqual(len(raw), 2048)
        raw.decode("utf-8")


class TestSender(SyslogTest):
    MSG = [b"<134>1 - h a - - - premier", b"<134>1 - h a - - - second"]

    def cfg(self, port, **kw):
        return dict(logforward.DEFAULTS, enabled=True, host="127.0.0.1", port=port, **kw)

    def test_udp_sends_one_datagram_per_message(self):
        r, sender = self.receiver("udp"), logforward.Sender()
        sender.send(self.cfg(r.port, transport="udp"), self.MSG)
        sender.close()
        self.assertTrue(r.wait(lambda: len(r.datagrams) == 2))
        self.assertEqual(r.datagrams, self.MSG)

    def test_tcp_framing_by_line_and_by_length(self):
        for framing, expected in (("lf", b"".join(m + b"\n" for m in self.MSG)),
                                  ("octet", b"".join(b"%d %s" % (len(m), m) for m in self.MSG))):
            r, sender = self.receiver(), logforward.Sender()
            sender.send(self.cfg(r.port, transport="tcp", framing=framing), self.MSG)
            self.assertTrue(r.wait(lambda: r.data == expected), (framing, r.data))
            sender.close()

    def test_tls_verifies_the_server_against_the_given_authority(self):
        pem, context = certificate()
        r, sender = self.receiver(context=context), logforward.Sender()
        sender.send(self.cfg(r.port, transport="tls", ca=pem), self.MSG)
        self.assertTrue(r.wait(lambda: b"second" in r.data))
        sender.close()
        with self.assertRaises(logforward.SyslogError) as refused:     # autorité inconnue : rien ne part
            logforward.Sender().send(self.cfg(r.port, transport="tls"), self.MSG, probe=True)
        self.assertIn("Certificat du serveur refusé", str(refused.exception))

    def test_tls_refuses_a_certificate_for_another_address(self):
        pem, context = certificate(ip="10.9.8.7")
        r = self.receiver(context=context)
        with self.assertRaises(logforward.SyslogError):
            logforward.Sender().send(self.cfg(r.port, transport="tls", ca=pem), self.MSG, probe=True)
        self.assertEqual(r.data, b"")

    def test_a_connection_closed_by_the_server_is_reopened(self):
        r, sender = self.receiver(), logforward.Sender()
        cfg = self.cfg(r.port, transport="tcp")
        sender.send(cfg, self.MSG[:1])
        self.assertTrue(r.wait(lambda: b"premier" in r.data and r.conns))
        r.drop_connections()
        time.sleep(0.05)
        sender.send(cfg, self.MSG[1:])
        self.assertTrue(r.wait(lambda: b"second" in r.data))
        sender.close()

    def test_a_missing_server_is_not_hammered_and_keeps_the_real_reason(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        sender = logforward.Sender()
        with self.assertRaises(logforward.SyslogError) as first:
            sender.send(self.cfg(port, transport="tcp"), self.MSG)
        self.assertIn("refusée", str(first.exception))
        with mock.patch.object(sender, "_connect", side_effect=AssertionError("pas pendant la pause")):
            with self.assertRaises(logforward.SyslogError) as second:
                sender.send(self.cfg(port, transport="tcp"), self.MSG)
        self.assertEqual(str(first.exception), str(second.exception))


class TestJournal(SyslogTest):
    def ship(self, cfg):
        with transaction() as conn:
            return logforward.ship_journal(conn, cfg)

    def add(self, action="parc.update", detail="srv-web", user="admin"):
        with transaction() as conn:
            conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                         (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user, "192.168.1.9", action, detail))

    def test_starts_from_now_sends_new_events_then_waits_when_the_server_is_away(self):
        r = self.receiver()
        cfg = config(transport="tcp", port=r.port)
        self.add(detail="avant-activation")
        self.assertEqual(self.ship(cfg), 0)                 # premier passage : pas l'historique
        self.add(detail="apres-activation")
        self.add(action="connexion.echec", detail="mauvais mot de passe", user="alice")
        self.assertEqual(self.ship(cfg), 2)
        self.assertTrue(r.wait(lambda: r.data.count(b"\n") == 2))
        lines = r.data.decode().splitlines()
        self.assertNotIn("avant-activation", r.data.decode())
        first, second = RFC5424.match(lines[0]), RFC5424.match(lines[1])
        self.assertEqual(first.group(1), str(16 * 8 + 6))    # local0, information
        self.assertEqual(first.group(6), "parc.update")
        self.assertIn('user="admin"', first.group(7))
        self.assertIn('category="parc"', first.group(7))
        self.assertIn("Mise à jour demandée : apres-activation", first.group(8))
        self.assertEqual(second.group(1), str(16 * 8 + 4))   # un échec passe en avertissement
        # Serveur absent : le curseur ne bouge pas, l'événement repart au retour.
        self.add(detail="pendant-la-panne")
        with mock.patch.object(logforward.SENDER, "send", side_effect=logforward.SyslogError("panne")):
            self.assertEqual(self.ship(cfg), 0)
        with transaction() as conn:
            self.assertEqual(logforward.status(conn)["last_error"], "panne")
        self.assertEqual(self.ship(cfg), 1)
        self.assertTrue(r.wait(lambda: b"pendant-la-panne" in r.data))
        self.assertEqual(self.ship(cfg), 0)

    def test_failure_alert_waits_five_minutes(self):
        config()
        with transaction() as conn:
            logforward._note(conn, "panne")
            self.assertEqual(logforward.failure(conn), "")
            logforward._status["failing_since"] = (datetime.datetime.now() - datetime.timedelta(minutes=6)).strftime("%Y-%m-%d %H:%M:%S")
            self.assertEqual(logforward.failure(conn), "panne")
            logforward._note(conn)
            self.assertEqual(logforward.failure(conn), "")


class TestPage(SyslogTest):
    FORM = {"enabled": "1", "host": "syslog.exemple.lan", "port": "", "transport": "tls", "framing": "lf", "ca": "",
            "facility": "17", "loco": "1", "min_severity": "4"}

    def account(self, grants):
        with transaction() as conn:
            conn.execute("DELETE FROM access_roles WHERE builtin=0")
        r = self.admin.post("/accounts/profiles/add", {"label": "Essai", "description": "", "fleet_scope": "all",
                            "permissions": sorted(permissions.expand(grants))}, page="/accounts/profiles/new")
        role = r.headers["Location"].rsplit("/", 1)[-1]
        with transaction() as conn:
            auth.create_user(conn, "sans-droit", PW, role, must_change=False)
        fixture.reset_failures()
        browser = fixture.Browser()
        browser.login("sans-droit", PW)
        return browser

    def test_only_with_the_right_permission(self):
        self.assertEqual(self.admin.c.get("/admin/syslog").status_code, 200)
        self.assertIn("Envoi syslog", self.admin.c.get("/fleet").get_data(as_text=True))
        reader = self.account({"audit.view", "fleet.view"})
        self.assertEqual(reader.c.get("/admin/syslog").status_code, 403)
        journal_page = reader.c.get("/log")
        self.assertEqual(journal_page.status_code, 200)
        self.assertNotIn("/admin/syslog", journal_page.get_data(as_text=True))     # ni bouton ni entrée de menu
        self.assertIn("/admin/syslog", self.admin.c.get("/log").get_data(as_text=True))

    def test_choose_what_is_sent_and_validate(self):
        r = self.admin.post("/admin/syslog", self.FORM, page="/admin/syslog")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            cfg = logforward.load(conn)
            logged = conn.execute("SELECT detail FROM audit WHERE action='syslog.configuration' ORDER BY id DESC").fetchone()
        self.assertEqual((cfg["loco"], cfg["devices"], cfg["facility"], logforward.port_of(cfg)), (True, False, 17, 6514))
        self.assertEqual(logged[0], "tls://syslog.exemple.lan:6514 ; loco")
        # Les postes seuls, puis les deux.
        self.admin.post("/admin/syslog", {**{k: v for k, v in self.FORM.items() if k != "loco"}, "devices": "1"}, page="/admin/syslog")
        with transaction() as conn:
            cfg = logforward.load(conn)
        self.assertEqual((cfg["loco"], cfg["devices"]), (False, True))
        self.admin.post("/admin/syslog", dict(self.FORM, devices="1"), page="/admin/syslog")
        with transaction() as conn:
            cfg = logforward.load(conn)
        self.assertEqual((cfg["loco"], cfg["devices"]), (True, True))
        # Refus : rien à envoyer, pas d'adresse, adresse invalide, autorité illisible.
        for bad in ({k: v for k, v in self.FORM.items() if k != "loco"}, dict(self.FORM, host=""),
                    dict(self.FORM, host="a b;rm"), dict(self.FORM, ca="pas un certificat")):
            self.assertEqual(self.admin.post("/admin/syslog", bad, page="/admin/syslog").status_code, 400, bad)

    def test_enabling_starts_from_now(self):
        self.admin.post("/admin/syslog", self.FORM, page="/admin/syslog")
        with transaction() as conn:
            cursor = logforward._cursor(conn)
            newest = logforward._newest_audit(conn)
        self.assertLess(newest - cursor, 2)                  # seul l'événement de ce réglage peut suivre

    def test_test_button_reports_success_and_failure_without_saving(self):
        r = self.receiver()
        form = dict(self.FORM, host="127.0.0.1", port=str(r.port), transport="tcp")
        result = self.admin.post("/admin/syslog/test", form, page="/admin/syslog").get_json()
        self.assertTrue(result["ok"], result)
        self.assertTrue(r.wait(lambda: b"syslog.test" in r.data))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        result = self.admin.post("/admin/syslog/test", dict(form, port=str(closed)), page="/admin/syslog").get_json()
        self.assertFalse(result["ok"])
        self.assertIn("refusée", result["message"])
        with transaction() as conn:
            self.assertEqual(logforward.load(conn)["host"], "")

    def test_state_lists_devices_and_flags_old_agents(self):
        recent = Agent(dict(LINUX, hostname="poste-recent", agent_version="2.124.0"), "192.168.43.81")
        old = Agent(dict(WINDOWS, hostname="poste-ancien", agent_version="2.120.0"), "192.168.43.82")
        for a in (recent, old):
            a.enroll()
            a.checkin()
        config(devices=True)
        with transaction() as conn:
            conn.execute("UPDATE devices SET agent_version='2.124.0' WHERE uid=?", (recent.uid,))
            conn.execute("UPDATE devices SET agent_version='2.120.0' WHERE uid=?", (old.uid,))
        page = self.admin.c.get("/admin/syslog").get_data(as_text=True)
        self.assertIn("poste-ancien", page)
        self.assertIn("Agent à mettre à jour", page)
        self.assertIn("En attente du premier envoi", page)


class TestDevices(SyslogTest):
    ENTRY = {"t": 1793600000.5, "p": 3, "f": 4, "a": "sshd", "i": "812", "m": "Failed password for root"}

    def agent(self, ident=LINUX, ip="192.168.43.90"):
        a = Agent(ident, ip)
        a.enroll()
        a.checkin()
        return a

    def post(self, agent, entries):
        return agent.post("/agent/api/logs", {"entries": entries}, agent.token)

    def test_requires_the_device_token_and_nothing_is_asked_while_off(self):
        a = self.agent()
        self.assertEqual(a.post("/agent/api/logs", {"entries": []}).status_code, 401)
        self.assertNotIn("syslog", a.checkin().get_json())
        self.assertTrue(self.post(a, [self.ENTRY]).get_json()["off"])
        config(loco=True, devices=False)                     # Journal de loco seul : les postes ne remontent rien
        self.assertNotIn("syslog", a.checkin().get_json())

    def test_relays_under_the_name_loco_knows_and_counts(self):
        r = self.receiver()
        config(transport="tcp", port=r.port, devices=True, min_severity=5, security=False)
        a = self.agent()
        self.assertEqual(a.checkin().get_json()["syslog"], {"min": 5, "security": False})
        forged = dict(self.ENTRY, m="ligne\n<13>1 forgée", host="autre-poste", hostname="autre-poste")
        self.assertEqual(self.post(a, [self.ENTRY, forged, {"m": ""}, "pas un objet"]).get_json(), {"ok": True, "accepted": 2})
        self.assertTrue(r.wait(lambda: r.data.count(b"\n") == 2))
        lines = r.data.decode().splitlines()
        self.assertEqual(len(lines), 2)
        m = RFC5424.match(lines[0])
        self.assertEqual(m.group(1), str(4 * 8 + 3))
        self.assertEqual((m.group(3), m.group(4), m.group(5)), (device(a.uid)["hostname"], "sshd", "812"))
        self.assertIn('ip="192.168.43.90"', m.group(7))
        self.assertTrue(m.group(2).startswith("2026-"))
        self.assertNotIn("autre-poste", r.data.decode())
        row = device(a.uid)
        self.assertEqual(row["log_lines"], 2)
        self.assertTrue(row["log_sent_at"])

    def test_refused_when_the_relay_fails_so_the_agent_keeps_its_position(self):
        config(transport="tcp", port=9, devices=True)
        a = self.agent()
        with mock.patch.object(logforward.SENDER, "send", side_effect=logforward.SyslogError("panne")):
            r = self.post(a, [self.ENTRY])
        self.assertEqual(r.status_code, 503)
        self.assertEqual(device(a.uid)["log_lines"], 0)
        page = self.admin.c.get("/admin/syslog").get_data(as_text=True)
        self.assertIn("ne reçoit plus rien", page)
        self.assertIn("panne", page)

    def test_limits_size_and_rate(self):
        config(transport="udp", port=9, devices=True)
        a = self.agent()
        self.assertEqual(self.post(a, [self.ENTRY] * (logforward.AGENT_LINES + 1)).status_code, 400)
        with mock.patch.object(logforward.SENDER, "send"):
            codes = [self.post(a, [self.ENTRY]).status_code for _ in range(logforward.AGENT_BATCHES_PER_MINUTE + 1)]
        self.assertEqual((codes[0], codes[-1]), (200, 429))

    def test_groups_limit_which_devices_send(self):
        inside, outside = self.agent(dict(LINUX, hostname="dedans"), "192.168.43.91"), self.agent(dict(LINUX, hostname="dehors"), "192.168.43.92")
        with transaction() as conn:
            group = conn.execute("INSERT INTO device_groups (name, updated_at) VALUES ('Serveurs', '')").lastrowid
            conn.execute("INSERT INTO device_group_members VALUES (?,?)", (group, device(inside.uid)["id"]))
        config(transport="udp", port=9, devices=True, groups=[group])
        self.assertIn("syslog", inside.checkin().get_json())
        self.assertNotIn("syslog", outside.checkin().get_json())
        with mock.patch.object(logforward.SENDER, "send"):
            self.assertEqual(self.post(inside, [self.ENTRY]).get_json()["accepted"], 1)
            self.assertTrue(self.post(outside, [self.ENTRY]).get_json()["off"])
        page = self.admin.c.get("/admin/syslog").get_data(as_text=True)
        self.assertIn("dedans", page)
        self.assertNotIn(">dehors<", page)


class TestLinuxAgent(unittest.TestCase):
    """Le vrai code de l'agent, avec un faux journalctl qui suit la position demandée."""
    SETTINGS = {"min": 4, "security": True}

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = os.path.join(self.dir.name, "journal.jsonl")
        script = os.path.join(self.dir.name, "journalctl")
        with open(script, "w") as f:
            f.write(f'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
open({self.log!r} + ".args", "a").write(" ".join(args) + "\\n")
lines = [json.loads(l) for l in open({self.log!r})]
if "-n" in args:
    print(json.dumps(lines[-1])) if lines else None
    sys.exit(0)
cursor = next(a.split("=", 1)[1] for a in args if a.startswith("--after-cursor="))
if cursor == "purge":
    print("Failed to seek to cursor: Invalid argument", file=sys.stderr)
    sys.exit(1)
try:
    for line in lines[int(cursor) + 1:]:
        print(json.dumps(line), flush=True)
except BrokenPipeError:
    pass
''')
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        path = self.dir.name + os.pathsep + os.environ["PATH"]
        for patch in (mock.patch.dict(AGENT.ENV, {"PATH": path}), mock.patch.dict(os.environ, {"PATH": path}),
                      mock.patch.object(AGENT, "save"), mock.patch.object(AGENT, "LOG_LINES", 3)):
            patch.start()
            self.addCleanup(patch.stop)
        self.write(5)
        self.conf = {"server": "https://loco.test", "token": "t"}

    def write(self, count, start=0):
        with open(self.log, "a") as f:
            for i in range(start, start + count):
                f.write(json.dumps({"__CURSOR": str(i), "__REALTIME_TIMESTAMP": "1793600000000000", "PRIORITY": "4",
                                    "SYSLOG_FACILITY": "4", "SYSLOG_IDENTIFIER": "sshd", "_PID": "9", "MESSAGE": f"ligne {i}"}) + "\n")

    def test_filter_and_entry(self):
        self.assertEqual(AGENT.journal_matches({"min": 3}), ["PRIORITY=0", "PRIORITY=1", "PRIORITY=2", "PRIORITY=3"])
        self.assertEqual(AGENT.journal_matches({"min": 0, "security": True}), ["PRIORITY=0", "+", "SYSLOG_FACILITY=4", "+", "SYSLOG_FACILITY=10"])
        entry = AGENT.journal_entry({"__REALTIME_TIMESTAMP": "1793600000500000", "PRIORITY": "3", "_COMM": "cron", "MESSAGE": [104, 105, 255]})
        self.assertEqual((entry["t"], entry["p"], entry["f"], entry["a"], entry["i"]), (1793600000.5, 3, 1, "cron", "-"))
        self.assertTrue(entry["m"].startswith("hi"))
        self.assertIsNone(AGENT.journal_entry({"MESSAGE": "   "}))
        self.assertEqual(len(AGENT.journal_entry({"MESSAGE": "x" * 9000})["m"]), AGENT.LOG_MESSAGE)

    def test_starts_from_now_then_sends_only_what_follows(self):
        sent, state = [], {}
        with mock.patch.object(AGENT, "call", side_effect=lambda *a, **k: sent.append(a[2]) or {"ok": True}):
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
            self.assertEqual(sent, [])                       # rien de l'historique
            self.assertEqual(state["log_cursor"], "4")
            self.write(2, start=5)
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
        self.assertEqual([e["m"] for e in sent[0]["entries"]], ["ligne 5", "ligne 6"])
        self.assertEqual(state["log_cursor"], "6")
        with open(self.log + ".args") as f:
            args = f.read()
        self.assertIn("--after-cursor=4 PRIORITY=0", args)
        self.assertIn("+ SYSLOG_FACILITY=4 + SYSLOG_FACILITY=10", args)

    def test_reads_batches_in_order_without_skipping(self):
        sent, state = [], {"log_cursor": "1"}
        self.write(4, start=5)                               # 7 lignes à envoyer, 3 par envoi
        with mock.patch.object(AGENT, "call", side_effect=lambda *a, **k: sent.append(a[2]) or {"ok": True}):
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
        self.assertEqual([[e["m"][6:] for e in b["entries"]] for b in sent], [["2", "3", "4"], ["5", "6", "7"], ["8"]])
        self.assertEqual(state["log_cursor"], "8")

    def test_a_refused_send_keeps_the_position(self):
        state = {"log_cursor": "1"}
        with mock.patch.object(AGENT, "call", side_effect=OSError("503")):
            with self.assertRaises(OSError):
                AGENT.forward_logs(self.conf, self.SETTINGS, state)
        self.assertEqual(state["log_cursor"], "1")

    def test_stopping_forgets_the_position_and_a_purged_journal_restarts_from_now(self):
        state = {"log_cursor": "1", "log_error": True}
        AGENT.forward_logs(self.conf, None, state)
        self.assertEqual(state, {})
        state = {"log_cursor": "purge"}
        with mock.patch.object(AGENT, "call") as call:
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
        call.assert_not_called()
        self.assertEqual(state["log_cursor"], "4")

    def test_no_journald_is_reported_once(self):
        state = {}
        with mock.patch.object(AGENT.shutil, "which", return_value=None), mock.patch.object(AGENT, "call", return_value={"ok": True}) as call:
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
            AGENT.forward_logs(self.conf, self.SETTINGS, state)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args[0][2], {"entries": [], "error": "journald absent"})


if __name__ == "__main__":
    unittest.main()
