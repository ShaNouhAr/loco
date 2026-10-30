"""Serveur MCP : terminal, fichiers et bureau des postes, par les sessions distantes de l'agent.

Le portail tient ici le rôle du navigateur sur le relais de remote.py (mêmes tables, mêmes limites, même agent) :
une session par accès MCP, poste et type, réutilisée d'un appel à l'autre et fermée par le relais après quelques
minutes sans appel (l'agent espace alors ses relevés). Chaque appel est borné dans le temps et le nombre d'appels distants simultanés est limité, pour garder
des fils de gunicorn à l'interface et aux agents.

- Terminal : chaque commande est encadrée de marqueurs (avec son code de sortie) ; sous Windows (ConPTY), la sortie
  revient en base64 entre deux marqueurs, seule façon fiable de la relire à travers le rendu d'écran de la console.
- Fichiers : trames du gestionnaire de fichiers de l'agent (longueurs, en-tête JSON, données).
- Terminal interactif : une seconde session dont l'écran est reconstitué par pyte (vim, top, invites…).
- Bureau : client RFB minimal (Tight sans perte ou brut) qui garde l'image en mémoire : après la première capture, seules
  les zones modifiées transitent ; rendu PNG, clics, frappes et touches."""
import base64
import binascii
import json
import math
import os
import re
import secrets
import struct
import threading
import time
import zlib

from flask import g
from werkzeug.exceptions import HTTPException

from . import fleet, remote
from .auth import audit, can
from .db import db
from .i18n import L_
from .mcp import DEVICE, ToolError, _active, _device, _via, tool

TERMINAL_COLS, TERMINAL_ROWS = 300, 200
TTY_COLS, TTY_ROWS = 120, 40
COMMAND_MAX = 64_000
OUTPUT_MAX = 60_000
FILE_READ_MAX = 1024 * 1024
FILE_CHUNK = 256 * 1024
SCREEN_WIDTH = 1440                     # au-delà, la capture est réduite d'un facteur entier (lisible par l'assistant)
POLL = 0.05
MIN_AGENT = {"terminal": (2, 4), "desktop": (2, 4), "files": (2, 23)}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[P^_][^\x1b]*\x1b\\|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1d\x7f]")
B64_RE = re.compile(r"[^A-Za-z0-9+/=]")

_SLOTS = threading.BoundedSemaphore(3)  # appels distants simultanés (le portail a 8 fils)
_GUARD = threading.Lock()
_LOCKS = {}                             # (accès, poste, type) -> verrou : un appel à la fois par session
_TTY_USED = set()                       # terminaux interactifs déjà ouverts (accès, poste) : détecter une session perdue
_STATE = {}                             # session -> état gardé (écran RFB, écran du terminal interactif, octets en attente) ;
                                        # perdu au redémarrage du portail : la session est alors rouverte


class ChannelError(ToolError):
    """Session interrompue ou flux incohérent : la session est fermée, l'appel suivant en rouvre une."""


def owner(role=""):
    return f"mcp:{g.mcp['token']['id']}" + (f":{role}" if role else "")


class Channel:
    def __init__(self, dev, kind, role=""):
        self.dev, self.kind, self.role = dev, kind, role
        self.key = (g.mcp["token"]["id"], dev["id"], kind, role)
        self.sid, self.fresh, self.reopened, self.ack, self.state = None, False, False, 0, None
        self.persistent = kind == "desktop" or role == "tty"   # flux à état : rien ne doit s'en perdre entre deux appels
        self.buffer, self.outgoing = bytearray(), []

    # ----------------------------------------------------------------- cycle de vie
    def __enter__(self):
        if not _SLOTS.acquire(timeout=30):
            raise ToolError("loco is busy with other remote calls: retry in a moment.")
        with _GUARD:
            self.lock = _LOCKS.setdefault(self.key, threading.Lock())
        if not self.lock.acquire(timeout=90):
            _SLOTS.release()
            raise ToolError("Another call is still using this remote session: retry in a moment.")
        try:
            self._open()
        except BaseException:
            self.lock.release()
            _SLOTS.release()
            raise
        return self

    def __exit__(self, kind, error, trace):
        try:
            if isinstance(error, ChannelError) or (error is not None and not isinstance(error, ToolError)):
                self.close(str(error)[:200] or "Erreur de la session MCP")
            elif self.sid:
                self._finish()
        finally:
            self.lock.release()
            _SLOTS.release()

    def _open(self):
        dev = self.dev
        if fleet.version_key(dev["agent_version"]) < MIN_AGENT[self.kind]:
            raise ToolError(f"The agent of {dev['hostname']} must be updated to {'.'.join(map(str, MIN_AGENT[self.kind]))}.0 or later.")
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            remote._prune()
            alive = {r["id"] for r in conn.execute("SELECT id FROM remote_sessions WHERE owner LIKE 'mcp:%' AND state IN "
                                                   "('waiting','active')")}
            for sid in [sid for sid in _STATE if sid not in alive]:
                _STATE.pop(sid, None)                       # sessions fermées par inactivité ou pour une personne
            row = conn.execute("SELECT * FROM remote_sessions WHERE owner=? AND device_id=? AND kind=? AND state IN "
                               "('waiting','active') ORDER BY created DESC LIMIT 1", (owner(self.role), dev["id"], self.kind)).fetchone()
            if row is not None and self.persistent and row["id"] not in _STATE:
                remote._close(row["id"], "Session MCP rouverte.", g.user["username"])
                self.reopened, row = True, None
            if row is None:
                remote.make_room(dev, self.kind, owner())
                if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE state IN ('waiting','active')").fetchone()[0] >= remote.SERVER_SESSIONS:
                    raise ToolError("Eight remote sessions are already open on loco: retry later.")
                if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE device_id=? AND kind=? AND state IN "
                                "('waiting','active')", (dev["id"], self.kind)).fetchone()[0] >= remote.KIND_SESSIONS:
                    raise ToolError(f"Two {self.kind} sessions are already open on {dev['hostname']}: retry later.")
                if dev["os_family"] == "windows" and conn.execute(
                        "SELECT COUNT(*) FROM remote_sessions WHERE device_id=? AND state IN ('waiting','active')",
                        (dev["id"],)).fetchone()[0] >= remote.DEVICE_SESSIONS:
                    raise ToolError(f"Four remote sessions are already open on {dev['hostname']}, the most its agent serves: retry later.")
                self.sid, now = secrets.token_hex(16), time.time()
                cols, rows = ((TTY_COLS, TTY_ROWS) if self.role == "tty" else (TERMINAL_COLS, TERMINAL_ROWS)
                              if self.kind == "terminal" else (100, 30))
                conn.execute("INSERT INTO remote_sessions (id,device_id,user_id,owner,pw_version,kind,created,browser_seen,"
                             "expires,terminal_fallback,cols,rows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (self.sid, dev["id"], g.user["id"], owner(self.role), g.user["pw_version"], self.kind, now, now, now + 3600,
                              int(self.kind == "desktop" and dev["os_family"] != "windows" and can("remote.terminal")), cols, rows))
                audit("parc.session.ouverture", f"{dev['hostname']} : {self.kind}, {self.sid}{_via()}", commit=False)
                self.fresh = True
            else:
                self.sid = row["id"]
                conn.execute("UPDATE remote_sessions SET browser_seen=? WHERE id=?", (time.time(), self.sid))
            conn.commit()
            if self.persistent:
                self.state = _STATE.setdefault(self.sid, {})
                self.buffer = self.state.setdefault("rest", bytearray())
        except BaseException:
            conn.rollback()
            raise

    def close(self, reason):
        if not self.sid:
            return
        conn = db()
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        remote._close(self.sid, reason, g.user["username"])
        conn.commit()
        _STATE.pop(self.sid, None)

    def _finish(self):
        """Acquitte ce qui a été lu : rien ne sera relu par l'appel suivant."""
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM remote_packets WHERE session_id=? AND direction='out' AND seq<=?", (self.sid, self.ack))
        conn.execute("UPDATE remote_sessions SET browser_seen=? WHERE id=?", (time.time(), self.sid))
        conn.commit()
        if self.state is not None:
            self.state["rest"] = self.buffer

    def quiet(self, settle, timeout, output=False):
        """Attend que la sortie se calme (rien de neuf pendant settle secondes), au plus timeout secondes ; avec output,
        seulement après un premier affichage (invite d'un shell qui démarre)."""
        end, size, changed = time.monotonic() + timeout, len(self.buffer), time.monotonic()
        while time.monotonic() < end:
            self.poll()
            if len(self.buffer) != size:
                size, changed = len(self.buffer), time.monotonic()
            elif (size or not output) and time.monotonic() - changed >= settle:
                return True
            time.sleep(POLL)
        return False

    # ----------------------------------------------------------------- échanges
    def send(self, data):
        for start in range(0, len(data), remote.PACKET):
            self.outgoing.append(bytes(data[start:start + remote.PACKET]))

    def poll(self):
        conn = remote._relay_transaction()
        try:
            row = conn.execute("SELECT * FROM remote_sessions WHERE id=?", (self.sid,)).fetchone()
            if row is None or row["state"] not in remote.OPEN:
                conn.commit()
                raise ChannelError("The remote session closed" + (f": {row['error']}" if row is not None and row["error"] else "."))
            while self.outgoing:
                try:
                    remote._append(row, "in", row["input_seq"] + 1, base64.b64encode(self.outgoing[0]).decode())
                except HTTPException as e:
                    if e.code == 429:                        # l'agent n'a pas encore vidé la file : réessayer au tour suivant
                        break
                    raise ChannelError(f"Remote input refused ({e.code}).")
                self.outgoing.pop(0)
                row = conn.execute("SELECT * FROM remote_sessions WHERE id=?", (self.sid,)).fetchone()
            for packet in remote._read(row, "out", self.ack):
                self.buffer += base64.b64decode(packet["data"])
                self.ack = packet["seq"]
            conn.execute("UPDATE remote_sessions SET browser_seen=? WHERE id=?", (time.time(), self.sid))
            conn.commit()
            return row["state"]
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def wait(self, ready, timeout):
        end = time.monotonic() + timeout
        while True:
            self.poll()
            if ready():
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(POLL)

    def flush(self, timeout=10):
        if not self.wait(lambda: not self.outgoing, timeout):
            raise ChannelError("The agent did not take the input in time.")

    def take(self, size, timeout):
        if not self.wait(lambda: len(self.buffer) >= size, timeout):
            raise ChannelError("The agent did not answer in time.")
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


# --------------------------------------------------------------------------- terminal

def _bash_line(command, nonce):
    # Script en argument (base64) : guillemets, retours à la ligne et lecture clavier du script sans surprise.
    script = base64.b64encode(command.encode()).decode()
    return (f" printf '\\036LOCO:{nonce}:B\\036\\n'; \"$(command -v bash || echo sh)\" -c \"$(printf %s {script} | base64 -d)\";"
            f" printf '\\036LOCO:{nonce}:E:%d\\036\\n' \"$?\"")


def _powershell_lines(command, nonce):
    script = base64.b64encode(command.encode("utf-8")).decode()
    lines = [" $__l = ''"] + [f" $__l += '{script[i:i + 3000]}'" for i in range(0, len(script), 3000)]
    lines.append(
        " & { $n = '" + nonce + "'; $global:LASTEXITCODE = 0; "
        "$s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($__l)); "
        "try { $r = & ([scriptblock]::Create($s)) 2>&1; $ok = $?; $o = $r | Out-String -Width 250; "
        "$c = if (-not $ok) { 1 } elseif ($LASTEXITCODE) { $LASTEXITCODE } else { 0 } } catch { $o = $_ | Out-String; $c = 1 }; "
        f"if ($o.Length -gt {OUTPUT_MAX}) {{ $o = '[...]' + $o.Substring($o.Length - {OUTPUT_MAX}) }}; "
        "$b = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($o)); "
        "Write-Host ('@' + '@LOCO:' + $n + ':B'); "
        "for ($i = 0; $i -lt $b.Length; $i += 240) { Write-Host $b.Substring($i, [Math]::Min(240, $b.Length - $i)); "
        "if ($i % 24000 -eq 0) { Start-Sleep -Milliseconds 30 } }; "
        "Write-Host ('@' + '@LOCO:' + $n + ':E:' + $c + ':' + $b.Length) }")
    return lines


def _ready(chan, windows):
    """Session neuve : le shell démarre (profil, PowerShell) et peut perdre ce qui est tapé avant d'être prêt. Une sonde
    est renvoyée à intervalle court jusqu'à ce que l'une d'elles réponde ; ensuite seulement, la commande part."""
    markers, sent, end = [], 0.0, time.monotonic() + 30
    while time.monotonic() < end:
        if time.monotonic() - sent >= (0.7 if windows else 2.0):
            probe = secrets.token_hex(4)
            if windows:
                # PSReadLine aspire la saisie en attente et la perd quand on le retire : on le retire dans la sonde
                # même, la commande ne part qu'une fois la console simple en place (pas de redessin à chaque frappe).
                chan.send(f" Remove-Module PSReadLine -ErrorAction SilentlyContinue; Write-Host ('@' + '@LOCO:R{probe}')\r".encode())
                markers.append(f"@@LOCO:R{probe}")
            else:
                chan.send(f" printf '\\036LOCO:R{probe}\\036\\n'\r".encode())
                markers.append(f"\x1eLOCO:R{probe}\x1e")
            sent = time.monotonic()
        chan.poll()
        text = clean(bytes(chan.buffer).decode("utf-8", "replace")) if windows else bytes(chan.buffer).decode("utf-8", "replace")
        if any(marker in text for marker in markers):
            chan.buffer.clear()
            return
        time.sleep(POLL)
    raise ChannelError("The device shell did not become ready.")


def clean(text):
    return ANSI_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def bash_result(raw, nonce):
    """(sortie, code) ou None tant que le marqueur de fin n'est pas arrivé."""
    text = raw.decode("utf-8", "replace")
    begin = text.find(f"\x1eLOCO:{nonce}:B\x1e")
    end = re.search(f"\x1eLOCO:{nonce}:E:(-?\\d+)\x1e", text)
    if begin < 0 or not end:
        return None
    body = clean(text[begin + len(nonce) + 9:end.start()]).strip("\n")
    return body, int(end.group(1))


def powershell_result(raw, nonce):
    text = clean(raw.decode("utf-8", "replace"))
    begin = text.find(f"@@LOCO:{nonce}:B")
    end = re.search(f"@@LOCO:{nonce}:E:(-?\\d+):(\\d+)", text)
    if begin < 0 or not end or end.start() < begin:
        return None
    encoded = B64_RE.sub("", text[begin + len(nonce) + 10:end.start()])
    if len(encoded) != int(end.group(2)):
        raise ChannelError("The PowerShell output was garbled by the console: run the command again.")
    try:
        text = base64.b64decode(encoded).decode("utf-8", "replace").replace("\r\n", "\n")
        return text.strip("\n"), int(end.group(1))
    except (binascii.Error, ValueError):
        raise ChannelError("The PowerShell output was garbled by the console: run the command again.")


def _partial(raw, nonce, windows):
    text = clean(raw.decode("utf-8", "replace"))
    if windows:
        return ""                                          # sortie PowerShell rendue seulement à la fin
    marker = f"LOCO:{nonce}:B"
    return text[text.find(marker) + len(marker):].strip("\x1e\n")[-OUTPUT_MAX:] if marker in text else ""


@tool("terminal_run", "remote", "Run a terminal command", L_("Commandes dans le terminal"),
      L_("Lancer des commandes dans le terminal root (Linux) ou PowerShell SYSTEM (Windows) d'un poste et lire leur sortie, en direct."),
      ("fleet.view", "remote.terminal"),
      "Run a command in the device's live terminal — root bash on Linux, Windows PowerShell as SYSTEM on Windows — and "
      "return its output and exit code within seconds (no agent check-in wait). Each call is independent: no working "
      "directory or variable is kept between calls. The command must not wait for keyboard input (use terminal_interact "
      "for interactive programs); it is interrupted after the timeout. For tasks longer than a few minutes, start them in "
      "the background (nohup … & / Start-Process) and check back. Scripts up to 64 KB; output limited to the last 60 KB.",
      {"device": DEVICE,
       "command": {"type": "string", "minLength": 1, "maxLength": COMMAND_MAX, "description": "Command or script (bash on Linux, PowerShell on Windows)."},
       "timeout": {"type": "integer", "minimum": 1, "maximum": 300, "description": "Seconds to wait (default 30)."}},
      ("device", "command"), read_only=False, destructive=True, idempotent=False)
def _terminal_run(args):
    dev = _device(args["device"])
    _active(dev)
    windows = dev["os_family"] == "windows"
    command = args["command"].replace("\r\n", "\n")
    if len(command.encode()) > COMMAND_MAX:
        raise ToolError(f"Scripts are limited to {COMMAND_MAX // 1000} KB here: use run_command for longer ones.")
    nonce = secrets.token_hex(6)
    timeout = args.get("timeout", 30)
    with Channel(dev, "terminal") as chan:
        if chan.fresh:
            _ready(chan, windows)
        for line in (_powershell_lines(command, nonce) if windows else [_bash_line(command, nonce)]):
            chan.send(line.encode("utf-8") + b"\r")
        audit("parc.session.commande", f"{dev['hostname']} : {command[:300]}{_via()}")
        parse = powershell_result if windows else bash_result
        found = []

        def ready():
            result = parse(bytes(chan.buffer), nonce)
            if result is not None:
                found.append(result)
            return bool(found)
        start = time.monotonic()
        if chan.wait(ready, timeout):
            output, code = found[0]
            truncated = len(output) > OUTPUT_MAX
            return {"device_id": dev["id"], "hostname": dev["hostname"], "exit_code": code,
                    "output": output[-OUTPUT_MAX:], "truncated": truncated, "seconds": round(time.monotonic() - start, 1)}
        partial = _partial(bytes(chan.buffer), nonce, windows)
        chan.send(b"\x03")                                  # Ctrl+C : le terminal redevient disponible
        chan.wait(lambda: False, 1.0)
    return {"device_id": dev["id"], "hostname": dev["hostname"], "exit_code": None, "timed_out": True,
            "output": partial, "message": f"No result after {timeout} s: the command was interrupted (Ctrl+C). "
                                          "Use a longer timeout, or run_command for long tasks."}


# --------------------------------------------------------------------------- terminal interactif

TTY_KEYS = {"enter": "\r", "return": "\r", "tab": "\t", "escape": "\x1b", "esc": "\x1b", "backspace": "\x7f",
            "delete": "\x1b[3~", "insert": "\x1b[2~", "home": "\x1b[H", "end": "\x1b[F", "pageup": "\x1b[5~",
            "pagedown": "\x1b[6~", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "space": " ",
            "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS", "f5": "\x1b[15~", "f6": "\x1b[17~",
            "f7": "\x1b[18~", "f8": "\x1b[19~", "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~"}


def tty_input(text):
    """Texte à taper, avec les touches entre accolades : « {esc}:wq{enter} », « {ctrl+c} », « {{ » pour une accolade."""
    out, pos = [], 0
    for match in re.finditer(r"\{\{|\}\}|\{([^{}]{1,20})\}", text):
        out.append(text[pos:match.start()].replace("\n", "\r"))
        pos = match.end()
        if match.group(0) in ("{{", "}}"):
            out.append(match.group(0)[0])
            continue
        name = match.group(1).strip().lower()
        mods, _sep, key = name.rpartition("+")
        if name in TTY_KEYS:
            out.append(TTY_KEYS[name])
        elif mods in ("ctrl", "control") and len(key) == 1 and (key.isalpha() or key in "[\\]^_@"):
            out.append(chr(ord(key.upper()) & 0x1f))
        elif mods == "alt" and (len(key) == 1 or key in TTY_KEYS):
            out.append("\x1b" + TTY_KEYS.get(key, key))
        else:
            raise ToolError(f"Unknown key {{{name}}}: use names such as enter, tab, escape, up, f2, ctrl+c, alt+x.")
    out.append(text[pos:].replace("\n", "\r"))
    return "".join(out)


def _pyte():
    """pyte, l'émulateur de terminal déjà embarqué pour la console texte des agents (portal/agents/console)."""
    console = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "console")
    if console not in __import__("sys").path:
        __import__("sys").path.append(console)
    import pyte
    return pyte


def _tty_screen():
    pyte = _pyte()
    from console_rfb import tolerant                         # même écran que la console texte des agents
    screen = tolerant(pyte.Screen)(TTY_COLS, TTY_ROWS)
    return screen, pyte.ByteStream(screen)


def _screen_text(state):
    screen = state["screen"]
    lines = [line.rstrip() for line in screen.display]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines), [screen.cursor.x, screen.cursor.y]


def close_role(token_id, dev_id, kind, role, reason):
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    for row in conn.execute("SELECT id FROM remote_sessions WHERE owner=? AND device_id=? AND kind=? AND state IN "
                            "('waiting','active')", (f"mcp:{token_id}:{role}", dev_id, kind)).fetchall():
        remote._close(row["id"], reason, g.user["username"])
        _STATE.pop(row["id"], None)
    conn.commit()


@tool("terminal_interact", "remote", "Use the interactive terminal", L_("Terminal interactif"),
      L_("Programmes interactifs (vim, top, invites oui/non, PowerShell) : le LLM tape, appuie sur des touches et lit l'écran du terminal."),
      ("fleet.view", "remote.terminal"),
      "Drive the device's interactive terminal like a person (root bash on Linux, PowerShell as SYSTEM on Windows): "
      "type input, with keys in braces such as {enter}, {tab}, {escape}, {up}, {f2}, {ctrl+c}, {ctrl+d}, {alt+x} "
      "({{ for a literal brace; \\n also presses Enter), then read the terminal screen (120x40). Use it for interactive "
      "programs: editors (vim, nano), top, prompts and installers. The session keeps its state between calls (working "
      "directory, running program); call without input to read the screen again, reset=true for a fresh shell.",
      {"device": DEVICE, "input": {"type": "string", "maxLength": 8000, "description": "Text and {keys} to type."},
       "wait_ms": {"type": "integer", "minimum": 0, "maximum": 60000,
                   "description": "Maximum wait for the screen to settle after the input (default 2000)."},
       "reset": {"type": "boolean", "description": "Close the current interactive shell and open a new one."}},
      ("device",), read_only=False, destructive=True, idempotent=False)
def _terminal_interact(args):
    dev = _device(args["device"])
    _active(dev)
    keys = tty_input(args["input"]) if args.get("input") else ""
    if args.get("reset"):
        close_role(g.mcp["token"]["id"], dev["id"], "terminal", "tty", "Terminal interactif réinitialisé par le client MCP.")
    with Channel(dev, "terminal", "tty") as chan:
        state = chan.state
        if "screen" not in state:
            state["screen"], state["stream"] = _tty_screen()
        if chan.fresh:
            chan.quiet(0.8, 20, output=True)                 # invite affichée : la saisie ne se perd plus
        # La session d'avant s'est fermée entre deux appels (inactivité, place cédée, portail redémarré) : la saisie
        # prévue pour elle (« :wq » dans vim…) ne part pas dans un shell neuf sans que l'assistant l'ait vu.
        used = (g.mcp["token"]["token_hash"], dev["id"])     # empreinte : un identifiant d'accès supprimé peut resservir
        lost = chan.fresh and not args.get("reset") and (chan.reopened or used in _TTY_USED)
        _TTY_USED.add(used)
        if keys and not lost:
            chan.send(keys.encode("utf-8"))
            audit("parc.session.commande", f"{dev['hostname']} (interactif) : {args['input'][:300]}{_via()}")
        chan.quiet(0.5, args.get("wait_ms", 2000) / 1000)
        state["stream"].feed(bytes(chan.buffer))
        chan.buffer.clear()
        screen, cursor = _screen_text(state)
    result = {"device_id": dev["id"], "hostname": dev["hostname"], "screen": screen, "cursor": cursor,
              "size": [TTY_COLS, TTY_ROWS]}
    if lost:
        result["note"] = ("The previous interactive session had closed (idle for minutes, loco restarted, or its place given to a "
                          "person): this is a NEW shell and your input was NOT typed. Send it again if it still applies.")
    return result


# --------------------------------------------------------------------------- fichiers

def _frame(header, data=b""):
    blob = json.dumps(header, ensure_ascii=False).encode()
    return struct.pack(">II", len(blob), len(data)) + blob + bytes(data)


class Files:
    def __init__(self, chan):
        self.chan, self.next = chan, 0

    def call(self, op, data=b"", timeout=30, **fields):
        self.next += 1
        rid = f"mcp-{self.next}"
        self.chan.send(_frame({"op": op, "id": rid, **fields}, data))
        hlen, dlen = struct.unpack(">II", self.chan.take(8, timeout))
        if hlen > 64 * 1024 or dlen > 1024 * 1024:
            raise ChannelError("Invalid reply from the agent file manager.")
        header = json.loads(self.chan.take(hlen, timeout).decode("utf-8", "replace"))
        payload = self.chan.take(dlen, timeout) if dlen else b""
        if header.get("id") != rid:
            raise ChannelError("Unexpected reply from the agent file manager.")
        if not header.get("ok"):
            raise ToolError(f"{op} {fields.get('path', '')}: {header.get('error') or 'refused by the agent'}")
        return header, payload


def _path(args, key="path"):
    value = args[key]
    if "\x00" in value:
        raise ToolError("Invalid path.")
    return value


@tool("list_files", "remote", "List files", L_("Parcourir les fichiers"),
      L_("Lister le contenu d'un dossier du poste (accès root ou SYSTEM)."), ("fleet.view", "remote.files"),
      "List a folder of the device (as root / SYSTEM): name, type, size, modification time and mode of each entry. "
      "Without a path, returns the home folder and the roots (drives on Windows).",
      {"device": DEVICE, "path": {"type": "string", "maxLength": 4096, "description": "Absolute folder path."}}, ("device",))
def _list_files(args):
    dev = _device(args["device"])
    _active(dev)
    with Channel(dev, "files") as chan:
        files = Files(chan)
        hello = files.call("hello")[0]
        if not args.get("path"):
            return {"device_id": dev["id"], "os": hello.get("os"), "separator": hello.get("sep"), "home": hello.get("home"),
                    "roots": hello.get("roots")}
        listing = files.call("list", path=_path(args))[0]
    entries = listing.get("entries") or []
    return {"device_id": dev["id"], "path": listing.get("path"), "parent": listing.get("parent"),
            "entries": [{"name": e.get("name"), "type": e.get("type"), "size": e.get("size"),
                         "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("mtime") or 0)),
                         "mode": oct(e.get("mode") or 0)} for e in entries[:1000]],
            "truncated": bool(listing.get("truncated")) or len(entries) > 1000}


@tool("read_file", "remote", "Read a file", L_("Lire un fichier"),
      L_("Lire le contenu d'un fichier du poste (texte, ou base64 pour un fichier binaire)."), ("fleet.view", "remote.files"),
      "Read a file of the device (as root / SYSTEM), up to 1 MB per call from an offset. Text is decoded as UTF-8; "
      "use encoding=base64 for binary files.",
      {"device": DEVICE, "path": {"type": "string", "minLength": 1, "maxLength": 4096},
       "offset": {"type": "integer", "minimum": 0, "description": "Start offset in bytes (default 0)."},
       "max_bytes": {"type": "integer", "minimum": 1, "maximum": FILE_READ_MAX, "description": "Bytes to read (default 65536)."},
       "encoding": {"type": "string", "enum": ["text", "base64"], "description": "text (default) or base64."}},
      ("device", "path"))
def _read_file(args):
    dev = _device(args["device"])
    _active(dev)
    offset, wanted = args.get("offset", 0), args.get("max_bytes", 65536)
    data, size = bytearray(), 0
    with Channel(dev, "files") as chan:
        files = Files(chan)
        while len(data) < wanted:
            header, chunk = files.call("read", path=_path(args), offset=offset + len(data),
                                       length=min(FILE_CHUNK, wanted - len(data)))
            data += chunk
            size = header.get("size", 0)
            if header.get("eof") or not chunk:
                break
    out = {"device_id": dev["id"], "path": args["path"], "size": size, "offset": offset, "bytes": len(data),
           "complete": offset + len(data) >= size}
    if args.get("encoding") == "base64":
        out["content_base64"] = base64.b64encode(bytes(data)).decode()
    elif b"\x00" in data[:8192]:
        out["binary"] = True
        out["message"] = "Binary file: read it again with encoding=base64."
    else:
        out["content"] = bytes(data).decode("utf-8", "replace")
    return out


@tool("write_file", "remote", "Write a file", L_("Écrire un fichier"),
      L_("Créer ou remplacer un fichier du poste (remplacement atomique)."), ("fleet.view", "remote.files"),
      "Create or replace a file on the device (as root / SYSTEM), atomically, with the given content (up to 1 MB). "
      "The parent folder must exist.",
      {"device": DEVICE, "path": {"type": "string", "minLength": 1, "maxLength": 4096},
       "content": {"type": "string", "maxLength": 1400000, "description": "New content of the file."},
       "encoding": {"type": "string", "enum": ["text", "base64"], "description": "text (UTF-8, default) or base64."}},
      ("device", "path"), read_only=False, destructive=True, idempotent=True)
def _write_file(args):
    dev = _device(args["device"])
    _active(dev)
    content = args.get("content", "")
    try:
        data = base64.b64decode(content, validate=True) if args.get("encoding") == "base64" else content.encode("utf-8")
    except (binascii.Error, ValueError):
        raise ToolError("content is not valid base64.")
    if len(data) > FILE_READ_MAX:
        raise ToolError("At most 1 MB per write_file call.")
    with Channel(dev, "files") as chan:
        files = Files(chan)
        offset = 0
        while True:
            chunk = data[offset:offset + FILE_CHUNK]
            final = offset + len(chunk) >= len(data)
            files.call("write", chunk, path=_path(args), offset=offset, final=final)
            offset += len(chunk)
            if final:
                break
    audit("parc.fichiers.ecriture", f"{dev['hostname']} : {args['path'][:300]} ({len(data)} octets){_via()}")
    return {"device_id": dev["id"], "path": args["path"], "written": len(data)}


@tool("manage_files", "remote", "Create, copy, move or rename files", L_("Organiser les fichiers"),
      L_("Créer un dossier, copier, déplacer, renommer ou changer les droits d'un élément du poste."),
      ("fleet.view", "remote.files"),
      "Organize files on the device (as root / SYSTEM): mkdir (create the folder path), copy or move path into the "
      "folder to, rename path to the new name, chmod path to an octal mode (Linux).",
      {"device": DEVICE, "operation": {"type": "string", "enum": ["mkdir", "copy", "move", "rename", "chmod"]},
       "path": {"type": "string", "minLength": 1, "maxLength": 4096},
       "to": {"type": "string", "maxLength": 4096, "description": "Destination folder (copy, move)."},
       "name": {"type": "string", "maxLength": 255, "description": "New name (rename)."},
       "mode": {"type": "string", "maxLength": 4, "description": "Octal mode such as 644 (chmod)."}},
      ("device", "operation", "path"), read_only=False, destructive=False, idempotent=False)
def _manage_files(args):
    dev = _device(args["device"])
    _active(dev)
    op, extra = args["operation"], {}
    if op in ("copy", "move"):
        if not args.get("to"):
            raise ToolError(f"{op} needs the destination folder in to.")
        extra["to"] = args["to"]
    elif op == "rename":
        if not args.get("name"):
            raise ToolError("rename needs the new name in name.")
        extra["name"] = args["name"]
    elif op == "chmod":
        if not re.fullmatch(r"[0-7]{3,4}", args.get("mode", "")):
            raise ToolError("chmod needs an octal mode such as 644.")
        extra["mode"] = int(args["mode"], 8)
    with Channel(dev, "files") as chan:
        header = Files(chan).call(op, path=_path(args), **extra)[0]
    audit("parc.fichiers.modification", f"{dev['hostname']} : {op} {args['path'][:250]}{_via()}")
    return {"device_id": dev["id"], "operation": op, "path": header.get("path") or args["path"], "done": True}


@tool("delete_file", "remote", "Delete a file or folder", L_("Supprimer des fichiers"),
      L_("Supprimer un fichier, ou un dossier avec tout son contenu, sur le poste."), ("fleet.view", "remote.files"),
      "Delete a file, or a folder with all its content, on the device (as root / SYSTEM). Irreversible.",
      {"device": DEVICE, "path": {"type": "string", "minLength": 1, "maxLength": 4096}}, ("device", "path"),
      read_only=False, destructive=True, idempotent=True)
def _delete_file(args):
    dev = _device(args["device"])
    _active(dev)
    if args["path"].rstrip("/\\") in ("", "C:", "c:") or re.fullmatch(r"[A-Za-z]:[\\/]?", args["path"]):
        raise ToolError("Refusing to delete a filesystem root.")
    with Channel(dev, "files") as chan:
        Files(chan).call("delete", path=_path(args))
    audit("parc.fichiers.suppression", f"{dev['hostname']} : {args['path'][:300]}{_via()}")
    return {"device_id": dev["id"], "path": args["path"], "deleted": True}


# --------------------------------------------------------------------------- bureau (RFB)

KEYS = {"enter": 0xff0d, "return": 0xff0d, "tab": 0xff09, "escape": 0xff1b, "esc": 0xff1b, "backspace": 0xff08,
        "delete": 0xffff, "del": 0xffff, "insert": 0xff63, "home": 0xff50, "end": 0xff57, "pageup": 0xff55,
        "pagedown": 0xff56, "left": 0xff51, "up": 0xff52, "right": 0xff53, "down": 0xff54, "space": 0x20,
        "ctrl": 0xffe3, "control": 0xffe3, "shift": 0xffe1, "alt": 0xffe9, "altgr": 0xfe03, "super": 0xffeb,
        "win": 0xffeb, "windows": 0xffeb, "meta": 0xffeb, "cmd": 0xffeb, "menu": 0xff67, "printscreen": 0xff61,
        **{f"f{n}": 0xffbd + n for n in range(1, 13)}}
BUTTONS = {"left": 1, "middle": 2, "right": 4}


def keysym(char):
    if char == "\n":
        return 0xff0d
    if char == "\t":
        return 0xff09
    code = ord(char)
    return code if 0x20 <= code <= 0x7e or 0xa0 <= code <= 0xff else 0x01000000 | code


ANSWER = 0x4C4F4301                                        # pseudo-encodage loco : répondre même sans changement


def encodings(dev):
    """Tight sans perte partout, sauf les agents Windows d'avant 2.115 qui y mêlent du JPEG (illisible ici) : brut."""
    if dev["os_family"] == "windows" and fleet.version_key(dev["agent_version"]) < (2, 115):
        return (0,)
    return (7, 0, ANSWER)


def _rfb(chan):
    """Écran négocié de la session (poignée de main RFB 3.8 au premier appel), gardé avec l'image en mémoire."""
    state = chan.state
    if "width" in state:
        return state
    if chan.take(12, 40) != b"RFB 003.008\n":
        raise ChannelError("Unexpected desktop protocol from the agent.")
    chan.send(b"RFB 003.008\n")
    count = chan.take(1, 20)[0]
    if count == 0:
        length = struct.unpack(">I", chan.take(4, 20))[0]
        raise ChannelError("Desktop unavailable: " + chan.take(min(length, 2000), 20).decode("utf-8", "replace"))
    if 1 not in chan.take(count, 20):
        raise ChannelError("Unexpected desktop security type from the agent.")
    chan.send(b"\x01")
    if chan.take(4, 20) != b"\0\0\0\0":
        raise ChannelError("Desktop session refused by the agent.")
    chan.send(b"\x01")                                      # ClientInit : session partagée
    width, height = struct.unpack(">HH", chan.take(4, 20))
    pixel = chan.take(16, 20)
    name = chan.take(struct.unpack(">I", chan.take(4, 20))[0], 20)
    if pixel[0] != 32 or pixel[3] != 1:
        raise ChannelError("Unsupported desktop pixel format.")
    wanted = encodings(chan.dev)
    chan.send(struct.pack(">BxH", 2, len(wanted)) + struct.pack(">" + "i" * len(wanted), *wanted))
    state.update(width=width, height=height, name=name.decode("utf-8", "replace"), frame=None, pending=False, updates=0, seen=-1,
                 silent=chan.dev["os_family"] == "windows" and ANSWER not in wanted)
    return state


class _Short(Exception):
    """Message RFB pas encore arrivé en entier."""


def _message(data, pos):
    """Message du serveur RFB qui commence à pos : (fin, rectangles décodables ou None pour un message sans image)."""
    def take(n):
        nonlocal pos
        if pos + n > len(data):
            raise _Short
        pos += n
        return data[pos - n:pos]
    kind = take(1)[0]
    if kind == 2:                                           # sonnerie
        return pos, None
    if kind == 3:                                           # presse-papiers du poste
        take(struct.unpack(">xxxI", take(7))[0])
        return pos, None
    if kind == 1:
        take(struct.unpack(">xHH", take(5))[1] * 6)
        return pos, None
    if kind != 0:
        raise ChannelError("Unexpected desktop message from the agent.")
    rects = []
    for _ in range(struct.unpack(">xH", take(3))[0]):
        x, y, w, h, encoding = struct.unpack(">HHHHi", take(12))
        if encoding == 0:
            rects.append((x, y, w, h, "raw", take(w * h * 4)))
        elif encoding == 7:
            control = take(1)[0]
            if control >> 4 == 8:                               # aplat
                r, gr, b = take(3)
                rects.append((x, y, w, h, "fill", bytes((b, gr, r, 0))))
            elif control >> 4 <= 7 and not control & 0x40:      # zlib (flux réinitialisé par rectangle) ou brut court
                size = w * h * 3
                if size < 12:
                    rects.append((x, y, w, h, "rgb", take(size)))
                else:
                    length, shift = 0, 0
                    for _ in range(3):
                        byte = take(1)[0]
                        length |= (byte & 0x7f) << shift
                        shift += 7
                        if not byte & 0x80:
                            break
                    rects.append((x, y, w, h, "zlib", take(length)))
            else:
                raise ChannelError("Unsupported desktop image encoding.")
        elif w == 0 and h == 0:                             # pseudo-rectangle sans données
            continue
        else:
            raise ChannelError(f"Unsupported desktop encoding {encoding}.")
    return pos, rects


def _apply(state, rects):
    width, height = state["width"], state["height"]
    if state["frame"] is None:
        state["frame"] = bytearray(width * height * 4)
    frame = state["frame"]
    for x, y, w, h, kind, data in rects:
        if x + w > width or y + h > height:
            raise ChannelError("Desktop image out of bounds.")
        line, pixels = (data * w, None) if kind == "fill" else (None, data)
        if kind in ("rgb", "zlib"):
            rgb = zlib.decompress(data) if kind == "zlib" else data
            if len(rgb) != w * h * 3:
                raise ChannelError("Invalid desktop image data.")
            pixels = bytearray(w * h * 4)
            pixels[0::4], pixels[1::4], pixels[2::4] = rgb[2::3], rgb[1::3], rgb[0::3]
        for row in range(h):
            start = ((y + row) * width + x) * 4
            frame[start:start + w * 4] = line if pixels is None else pixels[row * w * 4:(row + 1) * w * 4]


def _drain(chan, state):
    """Applique à l'image gardée en mémoire les messages complets déjà reçus."""
    if len(chan.buffer) == state["seen"]:
        return
    data, pos = bytes(chan.buffer), 0
    while pos < len(data):
        try:
            end, rects = _message(data, pos)
        except _Short:
            break
        pos = end
        if rects is not None:
            _apply(state, rects)
            state["updates"] += 1
            state["pending"] = False
            state["last_rects"] = len(rects)
    del chan.buffer[:pos]
    state["seen"] = len(chan.buffer)


def refresh(chan, state):
    """Image à jour de l'écran. Après la première capture, la demande est incrémentale : l'agent n'envoie que les zones
    modifiées. L'agent Linux répond toujours, l'agent Windows aussi depuis 2.115 (ANSWER) ; avant, il ne répondait
    qu'à un changement, donc son silence voulait dire « rien n'a bougé depuis l'image gardée »."""
    _drain(chan, state)
    first = state["frame"] is None
    target = state["updates"]
    if first or not state["pending"]:
        chan.send(struct.pack(">BBHHHH", 3, 0 if first else 1, 0, 0, state["width"], state["height"]))
        state["pending"] = True

    def arrived():
        _drain(chan, state)
        return state["updates"] > target
    if chan.wait(arrived, 60 if first else 0.8 if state["silent"] else 15):
        return state["frame"]
    if first or not state["silent"]:
        raise ChannelError("The device desktop sent no image.")
    return state["frame"]


def png(width, height, rgb):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def scale_of(width):
    return max(1, math.ceil(width / SCREEN_WIDTH))


def render(frame, width, height, step=1, box=None):
    """PNG de l'écran (réduit d'un facteur entier) ou d'une zone (pleine résolution) : (png, largeur, hauteur)."""
    left, top, right, bottom = box or (0, 0, width, height)
    out_w = len(range(left, right, step))
    rows = range(top, bottom, step)
    rgb = bytearray(out_w * len(rows) * 3)
    for index, y in enumerate(rows):
        line = frame[(y * width + left) * 4:(y * width + right) * 4]
        start = index * out_w * 3
        rgb[start:start + out_w * 3:3] = line[2::4 * step]
        rgb[start + 1:start + out_w * 3:3] = line[1::4 * step]
        rgb[start + 2:start + out_w * 3:3] = line[0::4 * step]
    return png(out_w, len(rows), bytes(rgb)), out_w, len(rows)


class Picture(dict):
    """Résultat d'outil accompagné d'une image (bloc « image » du résultat MCP)."""
    def __init__(self, data, image, mime="image/png"):
        super().__init__(data)
        self.image, self.mime = image, mime


def _screenshot(chan, state, dev, region=None):
    frame = refresh(chan, state)
    width, height = state["width"], state["height"]
    step = scale_of(width)
    info = {"device_id": dev["id"], "hostname": dev["hostname"], "screen_width": width, "screen_height": height,
            "scale": step}
    if region:
        try:
            x, y, w, h = (int(v) for v in region.split(","))
        except ValueError:
            raise ToolError("region must be x,y,width,height in screenshot coordinates.")
        box = (max(0, x * step), max(0, y * step), min(width, (x + w) * step), min(height, (y + h) * step))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ToolError("region is outside the screen.")
        image, out_w, out_h = render(frame, width, height, 1, box)
        info.update(image_width=out_w, image_height=out_h, region=region,
                    note="Zoomed region at full resolution. Coordinates for desktop actions stay those of the full screenshot.")
    else:
        image, out_w, out_h = render(frame, width, height, step)
        info.update(image_width=out_w, image_height=out_h,
                    note=f"Coordinates for desktop actions are pixels of this image (1 image pixel = {step} screen pixel(s)).")
    return Picture(info, image)


def _point(state, args):
    step = scale_of(state["width"])
    x, y = args["x"] * step + step // 2, args["y"] * step + step // 2
    if not (0 <= x < state["width"] and 0 <= y < state["height"]):
        raise ToolError(f"Point outside the screen (the screenshot is {math.ceil(state['width'] / step)}x"
                        f"{math.ceil(state['height'] / step)}).")
    return x, y


def _after(chan, state, dev, args, result):
    """Capture après l'action (par défaut) : l'assistant voit l'effet sans second appel."""
    if args.get("screenshot", True):
        chan.wait(lambda: False, args.get("wait_ms", 700) / 1000)
        picture = _screenshot(chan, state, dev)
        picture.update(result)
        return picture
    return result


DESKTOP = ("fleet.view", "remote.desktop")
SHOT = {"type": "boolean", "description": "Return a screenshot after the action (default true)."}
WAIT = {"type": "integer", "minimum": 0, "maximum": 10000, "description": "Milliseconds to wait before that screenshot (default 700)."}


@tool("desktop_screenshot", "remote", "Take a desktop screenshot", L_("Voir l'écran du poste"),
      L_("Capture de l'écran (bureau Windows ou Linux, écran de connexion, ou console texte d'un poste sans affichage)."),
      DESKTOP,
      "Take a screenshot of the device's screen (Windows or Linux desktop, login or lock screen, or the text console of "
      "a Linux device without a display). Large screens are scaled down by an integer factor; region zooms on part of "
      "the screenshot at full resolution to read small text.",
      {"device": DEVICE, "region": {"type": "string", "maxLength": 40,
                                    "description": "Optional zoom: x,y,width,height in screenshot coordinates."}},
      ("device",))
def _desktop_screenshot(args):
    dev = _device(args["device"])
    _active(dev)
    with Channel(dev, "desktop") as chan:
        return _screenshot(chan, _rfb(chan), dev, args.get("region"))


@tool("desktop_click", "remote", "Click on the desktop", L_("Cliquer sur l'écran"),
      L_("Clic (gauche, droit, milieu, double) à un point de la dernière capture."), DESKTOP,
      "Click at a point of the screen, in the coordinates of the last desktop_screenshot image. Returns a new "
      "screenshot after the click unless screenshot=false.",
      {"device": DEVICE, "x": {"type": "integer", "minimum": 0, "maximum": 65535}, "y": {"type": "integer", "minimum": 0, "maximum": 65535},
       "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default left."},
       "double": {"type": "boolean", "description": "Double click (default false)."}, "screenshot": SHOT, "wait_ms": WAIT},
      ("device", "x", "y"), read_only=False, destructive=True, idempotent=False)
def _desktop_click(args):
    dev = _device(args["device"])
    _active(dev)
    with Channel(dev, "desktop") as chan:
        state = _rfb(chan)
        x, y = _point(state, args)
        mask = BUTTONS[args.get("button", "left")]
        chan.send(struct.pack(">BBHH", 5, 0, x, y))
        for _ in range(2 if args.get("double") else 1):
            chan.send(struct.pack(">BBHH", 5, mask, x, y) + struct.pack(">BBHH", 5, 0, x, y))
        chan.flush()
        audit("parc.bureau.action", f"{dev['hostname']} : clic {args.get('button', 'left')} ({x}, {y}){_via()}")
        return _after(chan, state, dev, args, {"clicked": [x, y]})


@tool("desktop_type", "remote", "Type text on the desktop", L_("Taper du texte"),
      L_("Saisir du texte au clavier dans la fenêtre active du poste."), DESKTOP,
      "Type text with the keyboard into the focused window of the device (\\n presses Enter). Use desktop_key for "
      "shortcuts. Returns a screenshot afterwards unless screenshot=false.",
      {"device": DEVICE, "text": {"type": "string", "minLength": 1, "maxLength": 2000}, "screenshot": SHOT, "wait_ms": WAIT},
      ("device", "text"), read_only=False, destructive=True, idempotent=False)
def _desktop_type(args):
    dev = _device(args["device"])
    _active(dev)
    with Channel(dev, "desktop") as chan:
        state = _rfb(chan)
        events = [struct.pack(">BBxxI", 4, 1, keysym(c)) + struct.pack(">BBxxI", 4, 0, keysym(c)) for c in args["text"]]
        if dev["os_family"] == "windows":
            chan.send(b"".join(events))                     # Windows tape en Unicode, dans l'ordre : d'un bloc
            chan.flush()
        else:
            for event in events:                            # Linux : rythme humain, sinon IBus peut inverser des lettres
                chan.send(event)
                chan.flush()
                time.sleep(0.03)
        audit("parc.bureau.action", f"{dev['hostname']} : saisie de {len(args['text'])} caractère(s){_via()}")
        return _after(chan, state, dev, args, {"typed": len(args["text"])})


@tool("desktop_key", "remote", "Press keys on the desktop", L_("Appuyer sur des touches"),
      L_("Touche ou raccourci clavier (Entrée, Échap, Ctrl+C, Alt+F4, Ctrl+Alt+Suppr…)."), DESKTOP,
      "Press a key or a shortcut on the device, such as enter, escape, tab, ctrl+c, alt+f4, super, ctrl+alt+delete, "
      "f5, up. Keys are joined with +. Returns a screenshot afterwards unless screenshot=false.",
      {"device": DEVICE, "keys": {"type": "string", "minLength": 1, "maxLength": 60, "description": "Key or shortcut, e.g. ctrl+alt+t."},
       "screenshot": SHOT, "wait_ms": WAIT}, ("device", "keys"), read_only=False, destructive=True, idempotent=False)
def _desktop_key(args):
    dev = _device(args["device"])
    _active(dev)
    syms = []
    for part in args["keys"].lower().split("+"):
        part = part.strip()
        if part in KEYS:
            syms.append(KEYS[part])
        elif len(part) == 1:
            syms.append(keysym(part))
        else:
            raise ToolError(f"Unknown key {part!r}: use names such as enter, escape, ctrl, alt, shift, super, f1…f12, up, or one character.")
    with Channel(dev, "desktop") as chan:
        state = _rfb(chan)
        chan.send(b"".join(struct.pack(">BBxxI", 4, 1, s) for s in syms) +
                  b"".join(struct.pack(">BBxxI", 4, 0, s) for s in reversed(syms)))
        chan.flush()
        audit("parc.bureau.action", f"{dev['hostname']} : touches {args['keys']}{_via()}")
        return _after(chan, state, dev, args, {"pressed": args["keys"]})


@tool("desktop_scroll", "remote", "Scroll on the desktop", L_("Faire défiler"),
      L_("Molette de la souris à un point de l'écran."), DESKTOP,
      "Scroll with the mouse wheel at a point of the screen (coordinates of the last screenshot). Returns a "
      "screenshot afterwards unless screenshot=false.",
      {"device": DEVICE, "x": {"type": "integer", "minimum": 0, "maximum": 65535}, "y": {"type": "integer", "minimum": 0, "maximum": 65535},
       "direction": {"type": "string", "enum": ["up", "down"]},
       "amount": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Wheel notches (default 3)."},
       "screenshot": SHOT, "wait_ms": WAIT}, ("device", "x", "y", "direction"), read_only=False, destructive=False, idempotent=False)
def _desktop_scroll(args):
    dev = _device(args["device"])
    _active(dev)
    with Channel(dev, "desktop") as chan:
        state = _rfb(chan)
        x, y = _point(state, args)
        bit = 8 if args["direction"] == "up" else 16
        chan.send(struct.pack(">BBHH", 5, 0, x, y))
        for _ in range(args.get("amount", 3)):
            chan.send(struct.pack(">BBHH", 5, bit, x, y) + struct.pack(">BBHH", 5, 0, x, y))
        chan.flush()
        return _after(chan, state, dev, args, {"scrolled": args["direction"]})


# --------------------------------------------------------------------------- révocation

def close_sessions(token_id, reason):
    """Ferme les sessions ouvertes par un accès (révoqué ou modifié) ; l'agent arrête terminal, bureau et fichiers."""
    conn = db()
    for row in conn.execute("SELECT id FROM remote_sessions WHERE (owner=? OR owner LIKE ?) AND state IN ('waiting','active')",
                            (f"mcp:{token_id}", f"mcp:{token_id}:%")).fetchall():
        remote._close(row["id"], reason, g.user["username"])
        _STATE.pop(row["id"], None)
