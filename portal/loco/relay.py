"""Relais à connexions persistantes (WebSocket) des sessions distantes et du canal des agents.

Le protocole d'échange ne change pas : chaque message reçu est rejoué, dans ce processus, vers la route HTTP existante
(/agent/api/remote pour un agent, /fleet/session/<id>/io pour un navigateur) avec les en-têtes que nginx aurait transmis.
Droits, accord, limites, numérotation des paquets et journal restent donc ceux du portail, et l'état reste en base : une
session passe d'un transport à l'autre sans coupure, et un agent ou un navigateur plus ancien continue par allers-retours.

Ce que la connexion persistante apporte, c'est que le serveur prévient. Dès que la base change pour une connexion
(session demandée, saisie, sortie, action en attente), l'agent reçoit {"wake": true} et fait son échange aussitôt ; le
navigateur reçoit directement la réponse qu'il aurait obtenue en interrogeant. Plus aucun relevé à vide.

Le relais tourne dans un fil du processus qui sert le portail (voir portal/app.py) et n'écoute que sur 127.0.0.1 : nginx
lui transmet /agent/relay (réseaux des postes) et /fleet/session/<id>/ws (réseaux de l'interface). RFC 6455 écrite ici,
sans extension ni dépendance.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import sqlite3
import struct
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from werkzeug.test import EnvironBuilder, run_wsgi_app

from . import app, config

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AGENT_PATH = "/agent/relay"
BROWSER_PATH = re.compile(r"^/fleet/session/([0-9a-f]{32})/ws$")
BEARER = re.compile(r"^Bearer ([0-9a-f]{8,64})\.")
MAX_MESSAGE = 8 * 1024 * 1024       # un échange d'agent porte au plus quelques paquets de 256 Kio en base64
BUFFER = 256 * 1024
SILENCE = 75                        # secondes sans message : connexion fermée (chacun donne signe toutes les 20 s au plus)
FIRST = 10                          # premier message attendu après la poignée de main : pas de connexion muette qui s'installe
RETRY, RETRIES = 2, 30              # port encore tenu par le portail précédent (relance) : nouveaux essais pendant une minute
MAX_CONNECTIONS = 4096
TICK, FULL = 0.02, 1.0              # veille de la base : changement vu en 20 ms ; états liés à l'heure revus chaque seconde
PRESENCE = 20                       # navigateur connecté : sa présence est redite au portail à ce rythme, même onglet en arrière-plan
FORWARDED = ("host", "cookie", "authorization", "user-agent", "accept-language", "x-forwarded-for", "x-forwarded-proto")
BROWSER_FIELDS = ("csrf", "ack", "cols", "rows", "seq", "data", "close")
EMPTY = {"sessions": {}}


class Closed(Exception):
    """Connexion terminée, proprement ou non."""


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def mask(data, key):
    """Masque (ou démasque) une charge utile : XOR avec la clé de 4 octets répétée, fait d'un bloc sur de grands entiers."""
    if not data:
        return b""
    size = len(data)
    pad = (key * (size // 4 + 1))[:size]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(size, "big")


def frame(opcode, data, key=None):
    """Trame complète (FIN) ; masquée si une clé est donnée (sens client → serveur)."""
    size = len(data)
    head = bytes([0x80 | opcode])
    flag = 0x80 if key else 0
    if size < 126:
        head += bytes([flag | size])
    elif size < 65536:
        head += bytes([flag | 126]) + struct.pack("!H", size)
    else:
        head += bytes([flag | 127]) + struct.pack("!Q", size)
    return head + key + mask(data, key) if key else head + data


class Peer:
    """Une connexion WebSocket côté serveur : messages entiers, pings servis, écritures sérialisées."""
    def __init__(self, reader, writer):
        self.reader, self.writer, self.writing, self.closed = reader, writer, asyncio.Lock(), False

    async def recv(self):
        parts, size, started = [], 0, False
        while True:
            head = await self.reader.readexactly(2)
            fin, opcode, length = head[0] & 0x80, head[0] & 0x0F, head[1] & 0x7F
            if head[0] & 0x70 or not head[1] & 0x80:        # extension jamais négociée, ou trame de client non masquée
                raise Closed
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if length > MAX_MESSAGE or size + length > MAX_MESSAGE:
                raise Closed
            key = await self.reader.readexactly(4)
            data = mask(await self.reader.readexactly(length), key)
            if opcode >= 8:                                 # trames de contrôle : courtes, jamais fragmentées
                if not fin or length > 125:
                    raise Closed
                if opcode == 8:
                    await self.close()
                    raise Closed
                if opcode == 9:
                    await self._write(frame(10, data))
                continue
            if (opcode == 0) != started or opcode > 2:      # suite sans début, début sans fin, ou type inconnu
                raise Closed
            started = True
            parts.append(data)
            size += length
            if fin:
                return b"".join(parts)

    async def _write(self, data):
        if self.closed:
            raise Closed
        async with self.writing:
            self.writer.write(data)
            await self.writer.drain()

    async def send(self, text):
        await self._write(frame(1, text if isinstance(text, bytes) else text.encode("utf-8")))

    async def close(self, code=1000):
        if not self.closed:
            try:
                await self._write(frame(8, struct.pack("!H", code)))
            except (Closed, ConnectionError, OSError):
                pass
            self.closed = True


class Link:
    """Une connexion suivie : un agent (clé = identifiant du poste) ou le navigateur d'une session (clé = session)."""
    def __init__(self, kind, peer, headers):
        self.kind, self.peer, self.headers = kind, peer, headers
        self.key = None                 # poste ou session, connu au premier échange accepté
        self.seen = None                # état de la base déjà porté à cette connexion (voir Relay.signature)
        self.form = None                # navigateur : dernier acquittement et dernière taille, pour pousser une réponse
        self.busy = asyncio.Lock()      # un échange à la fois par connexion, dans l'ordre
        self.dirty = self.pushing = False


class Relay:
    def __init__(self):
        self.agents, self.browsers = {}, {}             # poste → connexions ; session → connexions
        self.queued, self.live = frozenset(), frozenset()
        self.calls = ThreadPoolExecutor(max_workers=4, thread_name_prefix="loco-relay")
        self.watch = ThreadPoolExecutor(max_workers=1, thread_name_prefix="loco-relay-veille")
        self.local, self.opened, self.opening = threading.local(), [], threading.Lock()
        self.full, self.count = 0.0, 0
        self.loop = self.server = self.port = self.thread = None
        self.stopping = False
        self.ready, self.failed = threading.Event(), None

    # ------------------------------------------------------------ lecture de la base (jamais d'écriture ici)

    def _db(self):
        """Connexion de lecture du fil courant, rouverte si la base a été remplacée sous le portail (restauration)."""
        stat = os.stat(config.DB_PATH)
        stamp = (stat.st_dev, stat.st_ino)
        if getattr(self.local, "stamp", None) != stamp:
            old = getattr(self.local, "conn", None)
            conn = sqlite3.connect(config.DB_PATH, timeout=5, check_same_thread=False)     # fermée par stop(), d'un autre fil
            with self.opening:
                if old is not None:
                    old.close()
                    self.opened.remove(old)
                self.opened.append(conn)
            self.local.conn, self.local.stamp, self.local.version = conn, stamp, None
        return self.local.conn

    def _sessions(self):
        try:
            return {"sessions": {row[0]: row[1:] for row in self._db().execute(
                "SELECT id,device_id,state,input_seq,output_seq,cols,rows,consent,error FROM remote_sessions")}}
        except (sqlite3.Error, OSError):
            self.local.stamp = None
            return EMPTY

    def _device(self, uid):
        try:
            row = self._db().execute("SELECT id FROM devices WHERE uid=?", (uid,)).fetchone()
        except (sqlite3.Error, OSError):
            self.local.stamp = None
            return None
        return row[0] if row else None

    def _tick(self):
        """Relevé complet si la base a changé depuis le précédent (autre connexion, donc aussi le portail lui-même),
        ou si une seconde a passé : action dont l'heure arrive, suivi en direct qui expire."""
        try:
            conn = self._db()
            version = conn.execute("PRAGMA data_version").fetchone()[0]
            now = time.time()
            if version == self.local.version and now - self.full < FULL:
                return None
            self.local.version, self.full = version, now
            snapshot = self._sessions()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            snapshot["queued"] = frozenset(row[0] for row in conn.execute(
                "SELECT DISTINCT device_id FROM device_commands WHERE state='queued' AND (run_after='' OR run_after<=?)", (stamp,)))
            snapshot["live"] = frozenset(row[0] for row in conn.execute("SELECT id FROM devices WHERE live_until>?", (now,)))
            return snapshot
        except (sqlite3.Error, OSError):
            self.local.stamp = None
            return None

    # ------------------------------------------------------------ appel du portail, dans ce processus

    def _exchange(self, path, headers, body, content_type):
        """Rejoue un message vers la route HTTP existante. L'état des sessions est relevé avant (ce que la réponse
        porte au moins) et après (ce que les autres connexions doivent apprendre)."""
        before = self._sessions()
        builder = EnvironBuilder(path=path, method="POST", base_url="http://%s/" % headers.get("host", "localhost"),
                                 headers=[(name, headers[name]) for name in FORWARDED if name in headers]
                                 + [("X-Requested-With", "fetch")],
                                 data=body, content_type=content_type, environ_base={"REMOTE_ADDR": "127.0.0.1"}, multithread=True)
        try:
            environ = builder.get_environ()
        finally:
            builder.close()
        chunks, status, _headers = run_wsgi_app(app.wsgi_app, environ, buffered=True)
        return before, int(status.split(" ", 1)[0]), b"".join(chunks), self._sessions()

    async def _call(self, *args):
        return await self.loop.run_in_executor(self.calls, self._exchange, *args)

    # ------------------------------------------------------------ qui prévenir

    def signature(self, link, snapshot):
        sessions = snapshot["sessions"]
        if link.kind == "browser":
            row = sessions.get(link.key)
            return row and (row[1], row[3], row[6], row[7])                     # état, sortie, accord, erreur
        dev = link.key
        return (tuple(sorted((sid, row[1], row[2], row[4], row[5], row[6]) for sid, row in sessions.items() if row[0] == dev)),
                dev in self.queued, dev in self.live)                           # état, saisie, taille, accord ; action ; direct

    def distribute(self, snapshot):
        """Prévient chaque connexion pour laquelle la base a changé depuis ce qui lui a été porté."""
        if "queued" in snapshot:
            self.queued, self.live = snapshot["queued"], snapshot["live"]
        for group in (self.agents, self.browsers):
            for links in group.values():
                for link in links:
                    current = self.signature(link, snapshot)
                    if link.seen is not None and current != link.seen:
                        link.seen = current
                        self.wake(link)

    def wake(self, link):
        link.dirty = True
        if not link.pushing:
            link.pushing = True
            self.loop.create_task(self._push(link))

    async def _push(self, link):
        try:
            while link.dirty and not link.peer.closed:
                link.dirty = False
                if link.kind == "agent":
                    await link.peer.send('{"wake":true}')
                elif link.form is not None:
                    async with link.busy:
                        status, body, after = await self._browser_call(link, link.form)
                        await link.peer.send(b'{"status":%d,"body":%s}' % (status, body if status == 200 else b"null"))
                    self.distribute(after)
                    if status in (401, 403, 404):
                        await link.peer.close()
        except (Closed, ConnectionError, OSError, asyncio.TimeoutError):
            link.peer.closed = True
        finally:
            link.pushing = False

    async def _ticker(self):
        said = time.monotonic()
        while True:
            await asyncio.sleep(TICK if self.agents or self.browsers else 0.5)
            snapshot = await self.loop.run_in_executor(self.watch, self._tick)
            if snapshot is not None:
                self.distribute(snapshot)
            if time.monotonic() - said > PRESENCE:
                # Le portail ferme une session dont le navigateur ne donne plus signe de vie depuis une minute. Un onglet
                # en arrière-plan ne parle presque plus (minuteries ralenties par le navigateur) alors que sa connexion
                # est bien là : le relais le redit pour lui. Onglet fermé, connexion fermée : plus rien n'est dit.
                said = time.monotonic()
                for links in self.browsers.values():
                    for link in links:
                        self.wake(link)

    # ------------------------------------------------------------ connexions

    async def _agent(self, link):
        uid = BEARER.match(link.headers.get("authorization", ""))
        if not uid:
            raise Closed
        while True:
            raw = await asyncio.wait_for(link.peer.recv(), SILENCE if link.seen is not None else FIRST)
            async with link.busy:
                before, status, body, after = await self._call("/agent/api/remote", link.headers, raw, "application/json")
                if status == 200 and link.key is None:
                    link.key = await self.loop.run_in_executor(self.calls, self._device, uid.group(1))
                    if link.key is None:
                        raise Closed
                    self.agents.setdefault(link.key, set()).add(link)
                if status == 200:
                    link.seen = self.signature(link, before)
                await link.peer.send(body if status == 200 else b'{"status":%d}' % status)
            self.distribute(after)
            if status in (401, 403):
                raise Closed

    async def _browser_call(self, link, form):
        before, status, body, after = await self._call("/fleet/session/%s/io" % link.key, link.headers,
                                                       urllib.parse.urlencode(form), "application/x-www-form-urlencoded")
        if status == 200:
            link.seen = self.signature(link, before)
        return status, body, after

    async def _browser(self, link):
        self.browsers.setdefault(link.key, set()).add(link)
        while True:
            raw = await asyncio.wait_for(link.peer.recv(), SILENCE if link.form is not None else FIRST)
            try:
                message = json.loads(raw)
                form = {name: str(message[name]) for name in BROWSER_FIELDS if name in message}
                number = int(message.get("re", 0))
            except (ValueError, TypeError, AttributeError):
                raise Closed from None
            async with link.busy:
                status, body, after = await self._browser_call(link, form)
                if status == 200:               # de quoi pousser la suite : même jeton, dernier acquittement, même taille
                    link.form = {name: form[name] for name in ("csrf", "ack", "cols", "rows") if name in form}
                await link.peer.send(b'{"re":%d,"status":%d,"body":%s}' % (number, status, body if status == 200 else b"null"))
            self.distribute(after)
            if status in (401, 403, 404):
                raise Closed

    async def _serve(self, reader, writer):
        link = None
        self.count += 1
        try:
            head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)).decode("latin-1").split("\r\n")
            method, target, _version = head[0].split(" ", 2)
            headers = {}
            for line in head[1:]:
                name, sep, value = line.partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
            path = target.split("?", 1)[0]
            session = BROWSER_PATH.match(path)
            key = headers.get("sec-websocket-key", "")
            refusal = None
            if method != "GET" or not (session or path == AGENT_PATH):
                refusal = "404 Not Found"
            elif "websocket" not in headers.get("upgrade", "").lower() or headers.get("sec-websocket-version") != "13" or not key:
                refusal = "400 Bad Request"
            elif session and urllib.parse.urlsplit(headers.get("origin", "")).netloc != headers.get("host"):
                refusal = "403 Forbidden"       # un navigateur n'ouvre ce canal que depuis une page du portail
            elif self.count > MAX_CONNECTIONS:
                refusal = "503 Service Unavailable"
            if refusal:
                writer.write(("HTTP/1.1 %s\r\nConnection: close\r\nContent-Length: 0\r\n\r\n" % refusal).encode("ascii"))
                await writer.drain()
                return
            writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)).encode("ascii"))
            await writer.drain()
            link = Link("browser" if session else "agent", Peer(reader, writer), headers)
            if session:
                link.key = session.group(1)
                await self._browser(link)
            else:
                await self._agent(link)
        except (Closed, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError,
                ConnectionError, OSError):
            pass
        finally:
            self.count -= 1
            if link is not None:
                link.peer.closed = True
                group = self.browsers if link.kind == "browser" else self.agents
                links = group.get(link.key)
                if links:
                    links.discard(link)
                    if not links:
                        del group[link.key]
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass

    async def _main(self, port):
        self.loop = asyncio.get_running_loop()
        for attempt in range(RETRIES):
            try:
                self.server = await asyncio.start_server(self._serve, "127.0.0.1", port, limit=BUFFER)
                break
            except OSError as e:
                self.failed = e
                self.ready.set()                # l'appelant n'attend pas : le portail sert, le relais suivra s'il le peut
                if self.stopping or attempt == RETRIES - 1:
                    return
                await asyncio.sleep(RETRY)
        self.port, self.failed = self.server.sockets[0].getsockname()[1], None
        self.ready.set()
        ticker = self.loop.create_task(self._ticker())
        try:
            await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            ticker.cancel()

    def run(self, port):
        asyncio.run(self._main(port))

    def start(self, port):
        """Lance le relais dans son fil ; vrai s'il écoute."""
        self.thread = threading.Thread(target=self.run, args=(port,), name="loco-relay", daemon=True)
        self.thread.start()
        self.ready.wait(10)
        return self.failed is None and self.port is not None

    def stop(self):
        self.stopping = True
        if self.loop is not None and self.server is not None:
            self.loop.call_soon_threadsafe(self.server.close)
        if self.thread is not None:
            self.thread.join(5)
        self.calls.shutdown(wait=True)
        self.watch.shutdown(wait=True)
        with self.opening:
            for conn in self.opened:
                conn.close()
            self.opened.clear()


_relay = None


def start(port=None):
    """Lance le relais dans un fil du processus qui sert le portail. Port déjà pris ou relais coupé (port 0) : le portail
    sert quand même, agents et navigateurs continuent par allers-retours HTTPS."""
    global _relay
    if _relay is not None:
        return _relay
    if port is None:
        port = config.RELAY_PORT
        if not port:
            return None
    _relay = relay = Relay()
    if not relay.start(port):
        app.logger.warning("relais des sessions : port %s indisponible (%s) ; nouveaux essais pendant une minute, d'ici là les "
                           "sessions passent par HTTPS", port, relay.failed)
    return relay
