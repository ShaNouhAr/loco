"""Faux Proxmox VE pour les essais : l'API HTTPS (mêmes chemins, mêmes formes de JSON, relevées sur un Proxmox VE 9.2
réel), un certificat autosigné créé à la volée, et l'état tenu en mémoire.

    server = FakeProxmox(); server.start() ; server.port, server.fingerprint ; server.stop()

Le jeton attendu est TOKEN_ID / SECRET. Chaque requête est notée (méthode, chemin, paramètres, jeton reçu ou non) dans
server.requests ; server.handshakes compte les connexions TLS (y compris celles qui n'envoient rien).
"""
import datetime
import hashlib
import http.server
import json
import os
import socketserver
import ssl
import tempfile
import threading
import time
import urllib.parse

TOKEN_ID = "loco@pve!loco"
SECRET = "0b6f2c1e-4d7a-4c55-9a3e-6f1d2b8c9e01"
UPID = "UPID:{node}:{pid:08X}:0001A2B3:{start:08X}:vzdump:{vmid}:loco@pve!loco:"


def certificate(name="pve.lan"):
    """(certificat PEM, clé PEM, empreinte SHA-256 en hexadécimal majuscule) d'un certificat autosigné."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Proxmox Virtual Environment")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=700)).sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            hashlib.sha256(der).hexdigest().upper())


def default_state():
    return {
        "guests": [
            {"vmid": 100, "name": "web", "type": "qemu", "node": "pve", "status": "running", "template": 0},
            {"vmid": 101, "name": "modele-debian", "type": "qemu", "node": "pve", "status": "stopped", "template": 1},
            {"vmid": 106, "name": "dns", "type": "lxc", "node": "pve", "status": "running", "template": 0},
        ],
        "storages": [
            {"storage": "local", "node": "pve", "plugintype": "dir", "content": "vztmpl,import,iso,backup", "shared": 0,
             "status": "available", "disk": 39799746560, "maxdisk": 100861726720},
            {"storage": "local-lvm", "node": "pve", "plugintype": "lvmthin", "content": "images,rootdir", "shared": 0,
             "status": "available", "disk": 724820619899, "maxdisk": 1884119105536},
        ],
        # Sauvegarde déjà là, faite par Proxmox lui-même : jamais touchée par la conservation de loco.
        "content": {"local": [{"volid": "local:backup/vzdump-qemu-100-2026_11_01-02_00_00.vma.zst", "vmid": 100,
                               "ctime": 1761958800, "size": 1508808800, "format": "vma.zst", "subtype": "qemu",
                               "content": "backup", "notes": "sauvegarde manuelle", "protected": 0}]},
        "privileges": ["VM.Audit", "VM.Backup", "Datastore.Audit", "Datastore.AllocateSpace", "Sys.Audit"],
        "tasks": {},
        "steps": 2,              # relevés d'état « running » avant la fin d'une sauvegarde
        "fail": set(),           # vmid dont la sauvegarde échoue
        "declared": [],          # stockages déclarés par POST /storage
    }


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _reply(self, status, data=None, reason=None, errors=None):
        body = json.dumps({"data": data} if errors is None else {"data": None, "errors": errors}).encode()
        self.send_response(status, reason)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        fake = self.server.fake
        parts = urllib.parse.urlsplit(self.path)
        params = dict(urllib.parse.parse_qsl(parts.query))
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            params.update(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
        auth = self.headers.get("Authorization", "")
        fake.requests.append({"method": method, "path": parts.path, "params": params,
                              "token": auth == f"PVEAPIToken={TOKEN_ID}={SECRET}", "auth": bool(auth)})
        if auth != f"PVEAPIToken={TOKEN_ID}={SECRET}":
            return self._reply(401, reason="invalid token value!")
        if not parts.path.startswith("/api2/json/"):
            return self._reply(404, reason="Not Found")
        # Segments décodés un à un : un identifiant de volume encodé (« local%3Abackup%2F… ») reste un seul segment.
        segments = [urllib.parse.unquote(x) for x in parts.path[len("/api2/json"):].split("/")]
        with fake.lock:
            return self._route(fake, method, segments, params)

    def _route(self, fake, method, p, params):
        st = fake.state
        if p == ["", "version"]:
            return self._reply(200, {"version": "9.2.21", "release": "9.2", "repoid": "4f6e0ac86f9e8c7f"})
        if p == ["", "access", "permissions"]:
            return self._reply(200, {"/": {k: 1 for k in st["privileges"]}})
        if p == ["", "cluster", "resources"]:
            if params.get("type") == "vm":
                return self._reply(200, [dict(g, id=f"{g['type']}/{g['vmid']}", maxmem=2147483648, cpu=0.01) for g in st["guests"]])
            return self._reply(200, [dict(s, id=f"storage/{s['node']}/{s['storage']}", type="storage") for s in st["storages"]])
        if p == ["", "storage"] and method == "POST":
            if "Datastore.Allocate" not in st["privileges"]:
                return self._reply(403, reason="Permission check failed (/storage, Datastore.Allocate)")
            st["declared"].append(params)
            st["storages"].append({"storage": params["storage"], "node": "pve", "plugintype": params["type"], "content": "backup",
                                   "shared": 1, "status": "available", "disk": 0, "maxdisk": 4 * 1024 ** 4})
            return self._reply(200, None)
        if len(p) >= 4 and p[1] == "nodes":
            node = p[2]
            if p[3:] == ["vzdump"] and method == "POST":
                vmid = int(params["vmid"])
                if "VM.Backup" not in st["privileges"]:
                    return self._reply(403, reason=f"Permission check failed (/vms/{vmid}, VM.Backup)")
                if params.get("storage") not in {s["storage"] for s in st["storages"]}:
                    return self._reply(400, reason="Parameter verification failed.", errors={"storage": "storage does not exist"})
                upid = UPID.format(node=node, pid=len(st["tasks"]) + 4242, start=int(time.time()), vmid=vmid)
                st["tasks"][upid] = {"vmid": vmid, "params": params, "left": st["steps"], "status": "running", "exitstatus": None}
                return self._reply(200, upid)
            if len(p) >= 5 and p[3] == "tasks":
                upid, task = p[4], st["tasks"].get(p[4])
                if task is None:
                    return self._reply(404, reason="no such task")
                if method == "DELETE" and len(p) == 5:
                    task.update(status="stopped", exitstatus="unexpected status", left=0, stopped=True)
                    return self._reply(200, None)
                if p[5:] == ["status"]:
                    if task["status"] == "running":
                        task["left"] -= 1
                        if task["left"] <= 0:
                            self._finish(fake, node, task)
                    return self._reply(200, {"status": task["status"], "exitstatus": task["exitstatus"], "upid": upid,
                                             "node": node, "type": "vzdump", "id": str(task["vmid"])})
                if p[5:] == ["log"]:
                    lines = [f"INFO: starting new backup job: vzdump {task['vmid']} --storage {task['params']['storage']}",
                             f"INFO: Starting Backup of VM {task['vmid']} (qemu)"]
                    lines.append("INFO: Backup job finished successfully" if task["exitstatus"] == "OK" else "ERROR: Backup of VM failed")
                    return self._reply(200, [{"n": i + 1, "t": t} for i, t in enumerate(lines)])
            if len(p) >= 7 and p[3] == "storage" and p[5] == "content":
                storage = p[4]
                items = st["content"].setdefault(storage, [])
                if method == "DELETE" and len(p) == 7:
                    volid = p[6]
                    before = len(items)
                    items[:] = [i for i in items if i["volid"] != volid]
                    return self._reply(200 if len(items) < before else 404, None)
            if len(p) == 6 and p[3] == "storage" and p[5] == "content" and method == "GET":
                items = st["content"].get(p[4], [])
                found = [i for i in items if not params.get("vmid") or str(i["vmid"]) == params["vmid"]]
                return self._reply(200, found)
        return self._reply(501, reason=f"Method '{method} {'/'.join(p)}' not implemented")

    @staticmethod
    def _finish(fake, node, task):
        st, vmid = fake.state, task["vmid"]
        if vmid in st["fail"]:
            task.update(status="stopped", exitstatus="job errors")
            return
        guest = next(g for g in st["guests"] if g["vmid"] == vmid)
        fake.clock += 60
        stamp = datetime.datetime.fromtimestamp(fake.clock).strftime("%Y_%m_%d-%H_%M_%S")
        kind = "qemu" if guest["type"] == "qemu" else "lxc"
        ext = "vma.zst" if kind == "qemu" else "tar.zst"
        notes = task["params"].get("notes-template", "").replace("{{guestname}}", guest["name"])
        st["content"].setdefault(task["params"]["storage"], []).append(
            {"volid": f"{task['params']['storage']}:backup/vzdump-{kind}-{vmid}-{stamp}.{ext}", "vmid": vmid, "ctime": fake.clock,
             "size": 734003200, "format": ext, "subtype": kind, "content": "backup", "notes": notes, "protected": 0})
        task.update(status="stopped", exitstatus="OK")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        sock, addr = super().get_request()
        self.fake.handshakes += 1
        return self.context.wrap_socket(sock, server_side=True), addr


class FakeProxmox:
    def __init__(self, port=0):
        self.state = default_state()
        self.requests, self.handshakes, self.lock = [], 0, threading.RLock()
        self.clock = 1763000000
        self._port = port
        self.server = None

    def start(self, name="pve.lan"):
        pem, key, self.fingerprint = certificate(name)
        folder = tempfile.mkdtemp(prefix="fake-pve-")
        cert_path, key_path = os.path.join(folder, "c.pem"), os.path.join(folder, "k.pem")
        with open(cert_path, "wb") as f:
            f.write(pem)
        with open(key_path, "wb") as f:
            f.write(key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        os.unlink(cert_path)
        os.unlink(key_path)
        os.rmdir(folder)
        self.server = _Server(("127.0.0.1", self._port), _Handler)
        self.server.context, self.server.fake = context, self
        self.port = self._port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def restart(self, name="pve-renouvele.lan"):
        """Même adresse, nouveau certificat (renouvelé, ou un autre serveur à la même adresse)."""
        self.stop()
        return self.start(name)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def backups(self, storage="local", vmid=None):
        return [i for i in self.state["content"].get(storage, []) if vmid is None or i["vmid"] == vmid]
