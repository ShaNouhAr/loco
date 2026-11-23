"""Pare-feu Stormshield SNS simulé, pour les tests de loco : son API HTTPS telle que la décrit le client officiel
(stormshield.sns.sslclient) — connexion par /auth/admin.html (identifiants en base64), session serverd, commandes en
GET avec réponse XML, téléchargement et envoi de fichier avec taille et CRC, droit d'écriture (MODIFY ON), dépôt et
activation d'un firmware (le pare-feu « redémarre » : sa version change, sa durée d'allumage repart de zéro).

    server = FakeSns(password="secret").start()   # server.port, server.fingerprint ; server.stop()
    python3 netsim_sns.py PORT MOT_DE_PASSE        # serveur seul (banc d'essai sur devzone)
"""
import base64
import datetime
import hashlib
import http.server
import os
import re
import secrets
import ssl
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netsim_data as data  # noqa: E402


def _certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SN31XA19D0012345")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SNS-WebServer-default-authority")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365)).sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()


def _xml_section(values, title="Result"):
    keys = "".join(f'<key name="{k}" value="{v}"/>' for k, v in values.items())
    return f'<data format="section"><section title="{title}">{keys}</section></data>'


def _answer(body):
    return f'<?xml version="1.0"?><nws code="100" msg="OK">{body}</nws>'


class FakeSns:
    def __init__(self, user="admin", password="secret", port=0, bind="127.0.0.1"):
        self.user, self.password, self.want_port, self.bind = user, password, port, bind
        self.sessions, self.pending, self.modify = {}, {}, set()
        self.version, self.boot, self.staged, self.commands, self.uploaded = "4.3.29", time.time() - 3 * 86400, "", [], b""
        self.backup = b"NA-ARCHIVE\x00" + os.urandom(4096)
        self.dir = tempfile.mkdtemp(prefix="netsim-sns-")
        self.cert, self.key, self.fingerprint = _certificate(self.dir)

    # -- réponses

    def command(self, sid, cmd):
        self.commands.append(cmd)
        word = cmd.upper()
        ok = '<serverd ret="100" code="00a00100" msg="Ok"></serverd>'
        if word == "SYSTEM PROPERTY":
            values = dict(data.SNS_PROPERTY, Version=self.version)
            return f'<serverd ret="101" code="00a01000" msg="Begin">{_xml_section(values)}</serverd>' + ok
        if word == "MONITOR SYSTEM":
            values = {"Uptime": str(int(time.time() - self.boot)), "CPU": "12", "Mem": "41", "Temperature": "47"}
            return f'<serverd ret="101" code="00a01000" msg="Begin">{_xml_section(values)}</serverd>' + ok
        if word == "SYSTEM UPDATE STATUS":
            values = {"Version": self.staged} if self.staged else {}
            return f'<serverd ret="101" code="00a01000" msg="Begin">{_xml_section(values)}</serverd>' + ok
        if word == "MODIFY ON":
            self.modify.add(sid)
            return ok
        if word.startswith("CONFIG BACKUP"):
            crc = "%X" % (zlib.crc32(self.backup) ^ 0xFFFFFFFF)
            self.pending[sid] = self.backup
            return (f'<serverd ret="101" code="00a01c00" msg="Begin download">'
                    f'<data format="section"><section title="Result"><key name="format" value="base64,crc={crc},size={len(self.backup)}"/></section></data></serverd>')
        if word == "SYSTEM UPDATE UPLOAD":
            if sid not in self.modify:
                return '<serverd ret="205" code="00a00800" msg="Not enough privilege"></serverd>'
            self.pending[sid] = "upload"
            return '<serverd ret="102" code="00a00300" msg="Waiting for upload"></serverd>'
        if word == "SYSTEM UPDATE ACTIVATE":
            if not self.staged:
                return '<serverd ret="200" code="00a00500" msg="No update uploaded"></serverd>'
            self.version, self.staged, self.boot = self.staged, "", time.time()
            return '<serverd ret="103" code="00a00100" msg="Ok, you will be disconnected"></serverd>'
        if word == "SYSTEM REBOOT":
            self.boot = time.time()
            return '<serverd ret="103" code="00a00100" msg="Ok, you will be disconnected"></serverd>'
        return '<serverd ret="200" code="00a00500" msg="Unknown command"></serverd>'

    # -- serveur

    def start(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send(self, body, status=200, kind="text/xml", cookie=None):
                payload = body if isinstance(body, bytes) else body.encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(payload)

            def form(self):
                size = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(size)

            def session(self):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                sid = (query.get("sessionid") or [""])[0]
                cookie = self.headers.get("Cookie", "")
                return sid if sid in fake.sessions and fake.sessions[sid] in cookie else None, query

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/auth/admin.html":
                    form = urllib.parse.parse_qs(self.form().decode())
                    user = base64.b64decode((form.get("uid") or [""])[0]).decode()
                    password = base64.b64decode((form.get("pswd") or [""])[0]).decode()
                    if user == fake.user and password == fake.password:
                        self.cookie = secrets.token_hex(8)
                        fake.last_cookie = self.cookie
                        return self.send('<?xml version="1.0"?><nws code="100" msg="AUTH_SUCCESS"/>', cookie=f"NETASQ_sslclient={self.cookie}; Path=/; Secure")
                    return self.send('<?xml version="1.0"?><nws code="100" msg="AUTH_FAILED"/>')
                if path == "/api/auth/login":
                    self.form()
                    if fake.last_cookie not in self.headers.get("Cookie", ""):
                        return self.send('<?xml version="1.0"?><nws code="203" msg="Authentication error"/>')
                    sid = secrets.token_hex(8)
                    fake.sessions[sid] = fake.last_cookie
                    return self.send(_answer(f"<sessionid>{sid}</sessionid><protocol>1</protocol><sessionlevel>base,modify,maintenance</sessionlevel>")
                                     .replace('<nws code="100" msg="OK">', '<nws code="100" msg="OK">'))
                if path == "/api/upload":
                    sid, _query = self.session()
                    body = self.form()
                    if sid is None or fake.pending.get(sid) != "upload":
                        return self.send(_answer('<serverd ret="200" code="00a00500" msg="No upload pending"></serverd>'))
                    m = re.search(rb"\r\n\r\n(.*)\r\n--loco", body, re.S)
                    fake.uploaded = m.group(1) if m else b""
                    name = re.search(rb'filename="([^"]+)"', body)
                    version = re.search(rb"(\d+\.\d+\.\d+)", name.group(1) if name else b"")
                    fake.staged = version.group(1).decode() if version else "?"
                    fake.pending.pop(sid, None)
                    return self.send(_answer('<serverd ret="100" code="00a00100" msg="Ok"></serverd>'))
                return self.send("", 404)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                sid, query = self.session()
                if path == "/api/auth/logout":
                    fake.sessions.pop(sid, None)
                    return self.send(_answer(""))
                if sid is None:
                    return self.send('<?xml version="1.0"?><nws code="203" msg="Invalid session"/>')
                if path == "/api/command":
                    return self.send(_answer(fake.command(sid, (query.get("cmd") or [""])[0])))
                if path == "/api/download/tmp.file":
                    content = fake.pending.pop(sid, b"")
                    return self.send(content if isinstance(content, bytes) else b"", kind="application/octet-stream")
                return self.send("", 404)

        self.server = http.server.ThreadingHTTPServer((self.bind, self.want_port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.last_cookie = "-"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    server = FakeSns(password=sys.argv[2] if len(sys.argv) > 2 else "secret", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8443, bind="0.0.0.0")
    server.start()
    print(f"port {server.port} empreinte {server.fingerprint}", flush=True)
    threading.Event().wait()
