"""Terminal adb des appareils Android : le serveur loco joue le rôle d'un ordinateur de développement sur le réseau local.

loco lance son propre serveur adb : sa clé (STATE/adb) et son socket (DATA/adb) sont accessibles au seul compte loco,
et il n'écoute sur aucun port réseau. loco s'appaire à l'appareil par le débogage sans fil d'Android (code d'appairage saisi
dans le portail), ou s'y connecte par le port TCP classique (5555). Il ouvre ensuite `adb shell` dans un pseudo-terminal
relié au navigateur par le relais des sessions distantes : un vrai terminal, avec les droits de l'utilisateur « shell »
d'Android (pm, am, settings, dumpsys, input, logcat…), plus larges que ceux de l'application loco.

L'application loco ne transporte rien : elle indique seulement à loco si le débogage sans fil est actif et sur quel port.
"""
import json
import os
import random
import re
import select
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import threading
import time

from flask import Response, abort, flash, g, request

from . import app, config
from .i18n import _
from .auth import audit, lock_access, login_required
from .db import connect, db
from .util import back

try:
    import fcntl
    import pty
    import termios
except ImportError:                     # serveur Windows : pas de pseudo-terminal
    pty = None

ROOT = os.path.join(config.STATE, "adb")     # clé adb de loco (secret, sauvegardée avec l'état)
RUN = os.path.join(config.DATA, "adb")       # socket du serveur adb : hors des sauvegardes (tar ignore mal les sockets)
PAIR_VERSION = (30,)                          # adb pair : platform-tools 30 et plus
HOST_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]{2,39}$")
SERIAL_RE = re.compile(r"^[0-9A-Za-z.:\[\]_-]{3,120}$")
WAIT_OUTPUT = 0.04
SEEN_EVERY = 5


# --------------------------------------------------------------------------- serveur adb de loco

def binary():
    for path in (os.environ.get("LOCO_ADB", ""), shutil.which("adb") or "", "/usr/lib/android-sdk/platform-tools/adb"):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return ""


_VERSION = {}


def version():
    """(majeur, mineur, correctif) de adb, ou () s'il est absent."""
    path = binary()
    if not path:
        return ()
    if path not in _VERSION:
        try:
            out = subprocess.run([path, "version"], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"^Version (\d+)\.(\d+)\.(\d+)", out, re.M)
            _VERSION[path] = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
        except (OSError, subprocess.SubprocessError):
            _VERSION[path] = (0, 0, 0)
    return _VERSION[path]


def available():
    return bool(pty is not None and not config.WINDOWS and binary())


def can_pair():
    return available() and version() >= PAIR_VERSION


def _env():
    for path in (ROOT, RUN):
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    return {"HOME": ROOT, "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
            # Socket de fichier dans un dossier 0700 : seul le compte loco parle à ce serveur adb (jamais de port TCP).
            "ADB_SERVER_SOCKET": "localfilesystem:" + os.path.join(RUN, "server.sock")}


def run(args, stdin=None, timeout=30):
    """(code de retour, sortie) d'une commande adb du serveur de loco. Les secrets passent par l'entrée, jamais en argument."""
    try:
        done = subprocess.run([binary(), *args], input=stdin, capture_output=True, text=True, timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        return 124, _("adb ne répond pas")
    except OSError as e:
        return 127, str(e)
    return done.returncode, (done.stdout + done.stderr).strip()


def _clean(text):
    """Dernière ligne utile d'une sortie adb (sans l'invite du code, ni le démarrage du démon)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("*") and "pairing code" not in ln.lower()]
    return (lines[-1] if lines else text.strip())[:300]


def pair(host, port, code):
    rc, out = run(["pair", f"{host}:{port}"], stdin=code + "\n", timeout=40)
    if rc == 0 and "Successfully paired" in out:
        return ""
    return _clean(out) or _("appairage refusé")


def connect_serial(host, port):
    """Connexion au service adb de l'appareil : "" si elle est établie et autorisée, sinon le motif."""
    serial = f"{host}:{port}"
    rc, out = run(["connect", serial], timeout=25)
    if "connected to" not in out:
        return _clean(out) or _("connexion impossible")
    for _attempt in range(10):
        state = device_state(serial)
        if state == "device":
            return ""
        if state not in ("unauthorized", "offline", "authorizing", "connecting"):
            break
        time.sleep(1)
    if state == "unauthorized":
        return _("accès à autoriser sur l'appareil : acceptez « Autoriser le débogage » puis réessayez")
    return _("appareil injoignable par adb (%(state)s)", state=state or "?")


def device_state(serial):
    rc, out = run(["-s", serial, "get-state"], timeout=10)
    out = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if rc == 0:
        return out
    m = re.search(r"device (unauthorized|offline|still authorizing|still connecting)", out)
    return m.group(1).split()[-1] if m else ""


def disconnect(serial):
    run(["disconnect", serial], timeout=10)


# --------------------------------------------------------------------------- appareils

def link(dev, conn=None):
    return (conn or db()).execute("SELECT * FROM android_adb WHERE device_id=?", (dev["id"],)).fetchone()


def report(dev):
    """Ce que l'application loco a vu sur l'appareil : options pour les développeurs, débogage sans fil, port, adresse."""
    row = link(dev)
    try:
        return json.loads(row["report"]) if row and row["report"] else {}
    except ValueError:
        return {}


def record_report(conn, dev, value):
    """Rapport de l'application loco (contact régulier) : borné, et seulement s'il change."""
    if not isinstance(value, dict):
        return
    clean = {"developer": bool(value.get("developer")), "wireless": bool(value.get("wireless")),
             "port": value["port"] if isinstance(value.get("port"), int) and 0 < value["port"] < 65536 else 0,
             "ip": value["ip"] if isinstance(value.get("ip"), str) and HOST_RE.match(value["ip"]) else ""}
    text = json.dumps(clean, sort_keys=True)
    row = conn.execute("SELECT * FROM android_adb WHERE device_id=?", (dev["id"],)).fetchone()
    if row is None:
        conn.execute("INSERT INTO android_adb (device_id, report) VALUES (?, ?)", (dev["id"], text))
    elif row["report"] != text:
        conn.execute("UPDATE android_adb SET report=? WHERE device_id=?", (text, dev["id"]))


def target(dev, row=None):
    """(hôte, port) où joindre adb sur l'appareil : port classique choisi, sinon celui du débogage sans fil annoncé."""
    row = row or link(dev)
    seen = report(dev)
    host = (row["host"] if row and row["host"] else "") or seen.get("ip") or fleet_address(dev)
    if row and row["mode"] == "tcp" and row["port"]:
        return host, row["port"]
    return host, seen.get("port") or (row["port"] if row else 0)


def fleet_address(dev):
    from .fleet import main_address
    return main_address(dev)


def status(dev):
    """État lisible du terminal adb pour la fiche : (clé, texte)."""
    if not available():
        return "missing", _("adb n'est pas installé sur ce serveur")
    row = link(dev)
    if row is None or not (row["paired_at"] or row["mode"] == "tcp"):
        return "new", _("non configuré")
    host, port = target(dev, row)
    if port and device_state(f"{host}:{port}") == "device":
        return "ready", _("connecté")
    seen = report(dev)
    if row["mode"] != "tcp" and not seen.get("wireless"):
        return "off", _("débogage sans fil désactivé sur l'appareil")
    return "idle", row["error"] or _("prêt à se connecter")


def view(dev):
    """Fiche d'un appareil : état du terminal adb et de quoi le configurer."""
    state, text = status(dev)
    row, seen = link(dev), report(dev)
    host, port = target(dev, row) if row else (seen.get("ip") or fleet_address(dev), 0)
    presets = [("Pixel / AOSP", PATTERN_DEFAULT), ("Samsung", PATTERN_PROFILES["samsung"]),
               ("Xiaomi / Redmi / Poco", PATTERN_PROFILES["xiaomi"]), ("OnePlus / Oppo / Realme", PATTERN_PROFILES["oneplus"]),
               ("Vivo", PATTERN_PROFILES["vivo"]), ("Motorola", PATTERN_PROFILES["motorola"])]
    return {"state": state, "text": text, "available": available(), "pair": can_pair(), "report": seen,
            "prepared": prepared(row),
            "configured": bool(row and (row["paired_at"] or row["mode"] == "tcp")), "mode": row["mode"] if row else "",
            "paired_at": row["paired_at"] if row else "", "host": host, "port": port, "error": row["error"] if row else "",
            "calibrated": bool(row and row["unlock"]), "geometry": geometry(dev),
            "presets": [dict(label=label, **geo) for label, geo in presets]}


# --------------------------------------------------------------------------- autorisations données une fois à l'application

AGENT = "org.loco.agent"
INPUT_SERVICE = AGENT + "/" + AGENT + ".InputService"
AGENT_PREPARED = (2, 145)       # application loco qui sait s'en servir (et déclare le droit de rallumer le débogage)
# Ce que l'appairage adb donne une fois pour toutes à l'application loco de l'appareil, pour que l'écran distant ne
# demande plus aucun geste : (clé, commande du shell adb). L'ordre va du plus utile au plus accessoire.
PREPARE = (
    ("screen", ("appops", "set", AGENT, "PROJECT_MEDIA", "allow")),              # partage d'écran sans fenêtre d'accord
    ("launch", ("appops", "set", AGENT, "SYSTEM_ALERT_WINDOW", "allow")),        # … demandé depuis l'arrière-plan
    ("adb", ("pm", "grant", AGENT, "android.permission.WRITE_SECURE_SETTINGS")),  # rallumer le débogage sans fil coupé
    ("restricted", ("appops", "set", AGENT, "ACCESS_RESTRICTED_SETTINGS", "allow")),   # Android 13+ : accessibilité permise
    ("battery", ("dumpsys", "deviceidle", "whitelist", "+" + AGENT)),            # pas de mise en veille de l'application
)
_PREPARING = {}                 # appareil -> instant du dernier essai en arrière-plan (un à la fois, pas en rafale)
PREPARE_EVERY = 600


def _sh(serial, *command, timeout=20):
    return run(["-s", serial, "shell", *command], timeout=timeout)


def prepare(serial):
    """Pose sur l'appareil, par adb, les autorisations de l'application loco. Renvoie les clés posées : « control » en
    plus quand son service d'accessibilité (écran et contrôle à chaque session) est activé."""
    done = []
    for key, command in PREPARE:
        rc, out = _sh(serial, *command)
        if rc == 0 and not re.search(r"(?i)\b(error|exception|unknown|not requested|no such)\b", out):
            done.append(key)
    rc, out = _sh(serial, "settings", "get", "secure", "enabled_accessibility_services")
    if rc == 0:
        services = [v for v in out.strip().splitlines()[-1].strip().split(":") if v and v != "null"] if out.strip() else []
        if INPUT_SERVICE not in services and AGENT + "/.InputService" not in services:
            _sh(serial, "settings", "put", "secure", "enabled_accessibility_services", ":".join(services + [INPUT_SERVICE]))
            _sh(serial, "settings", "put", "secure", "accessibility_enabled", "1")
            rc, out = _sh(serial, "settings", "get", "secure", "enabled_accessibility_services")
            services = out.strip().split(":") if rc == 0 else []
        if INPUT_SERVICE in services or AGENT + "/.InputService" in services:
            done.append("control")
    return done


def prepared(row):
    """Autorisations déjà posées sur l'appareil : {at, agent, done} ou {}."""
    try:
        value = json.loads(row["prepared"]) if row and row["prepared"] else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _prepare_due(dev, row):
    """À (re)faire : jamais fait, ou fait pour une application d'avant ces autorisations, mise à jour depuis."""
    from .fleet import version_key
    if row is None or not (row["paired_at"] or row["mode"] == "tcp") or version_key(dev["agent_version"]) < AGENT_PREPARED:
        return False
    return version_key(prepared(row).get("agent", "")) < AGENT_PREPARED


_PREP_LOCK = threading.Lock()


def _prepare(dev, serial):
    """Après une connexion adb réussie : autorisations posées une fois, notées et journalisées. Sous verrou : la
    préparation et la mise à jour par adb peuvent se connecter en même temps, une seule pose les autorisations."""
    with _PREP_LOCK:
        _prepare_locked(dev, serial)


def _prepare_locked(dev, serial):
    row = link(dev)
    if not _prepare_due(dev, row):
        return
    done = prepare(serial)
    with connect() as conn:
        conn.execute("UPDATE android_adb SET prepared=? WHERE device_id=?",
                     (json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "agent": dev["agent_version"], "done": done}), dev["id"]))
        conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?, ?, '', 'parc.adb.autorisations', ?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), row["paired_by"] or "loco",
                      f"{dev['hostname']} : {', '.join(done) or 'aucune'}"))


def prepare_soon(dev, row):
    """Contact de l'application : si l'appareil est appairé, joignable et pas encore préparé, le faire en arrière-plan
    (sans faire attendre le contact), au plus une fois toutes les dix minutes par appareil."""
    seen = report(dev)
    if not _prepare_due(dev, row) or not (row["mode"] == "tcp" or seen.get("wireless") and seen.get("port")):
        return
    now = time.time()
    with _GUARD:
        if now - _PREPARING.get(dev["id"], 0) < PREPARE_EVERY:
            return
        _PREPARING[dev["id"]] = now
    dev_id = dev["id"]

    def work():
        with app.app_context():
            try:
                ensure(db().execute("SELECT * FROM devices WHERE id=?", (dev_id,)).fetchone())
            except (ValueError, TypeError, sqlite3.Error):
                pass

    threading.Thread(target=work, name="loco-adb-prepare", daemon=True).start()


# --------------------------------------------------------------------------- mise à jour de l'application par adb

UPDATE_RETRY = 1800                 # après un échec, l'application reprend la main (sa propre mise à jour) pendant 30 min
_UPDATING = {}                      # appareil -> (version visée, instant du dernier essai, échec)


def can_update(dev, row):
    """L'application de cet appareil peut-elle être mise à jour par adb ? Appareil appairé et joignable (débogage sans fil
    annoncé, ou port classique), et pas d'échec récent pour cette version : sinon l'application se met à jour seule."""
    if not available() or row is None or not (row["paired_at"] or row["mode"] == "tcp"):
        return False
    seen = report(dev)
    if not (row["mode"] == "tcp" or seen.get("wireless") and seen.get("port")):
        return False
    last = _UPDATING.get(dev["id"])
    return not (last and last[0] == config.VERSION and last[2] and time.time() - last[1] < UPDATE_RETRY)


def update_soon(dev, row, path):
    """Installe par adb la version de l'application que sert ce serveur, en arrière-plan, au plus une fois par
    tranche de dix minutes et jamais pendant une session distante (l'installation redémarre l'application). Par adb,
    Android installe comme depuis un ordinateur : sans la fenêtre de confirmation ni l'analyse proposée par Play
    Protect pour une application installée depuis un fichier sur l'appareil."""
    now = time.time()
    with _GUARD:
        last = _UPDATING.get(dev["id"])
        if last and last[0] == config.VERSION and now - last[1] < PREPARE_EVERY:
            return
        _UPDATING[dev["id"]] = (config.VERSION, now, False)
    if db().execute("SELECT 1 FROM remote_sessions WHERE device_id=? AND state IN ('waiting','active')", (dev["id"],)).fetchone():
        with _GUARD:
            _UPDATING.pop(dev["id"], None)      # session en cours : on réessaiera au contact suivant
        return
    dev_id, version = dev["id"], config.VERSION

    def work():
        with app.app_context():
            ok, detail = False, ""
            try:
                current = db().execute("SELECT * FROM devices WHERE id=?", (dev_id,)).fetchone()
                serial = ensure(current)
                rc, out = run(["-s", serial, "install", "-r", path], timeout=300)
                ok = rc == 0 and "Success" in out
                detail = "" if ok else _clean(out)
            except (ValueError, TypeError, sqlite3.Error) as e:
                detail, current = str(e), None
            with _GUARD:
                _UPDATING[dev_id] = (version, time.time(), not ok)
            if current is not None:
                with connect() as conn:
                    conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?, 'loco', '', ?, ?)",
                                 (time.strftime("%Y-%m-%d %H:%M:%S"), "parc.agent.adb" if ok else "parc.agent.adb.echec",
                                  f"{current['hostname']} : {current['agent_version']} → {version}" + ("" if ok else f" ({detail[:200]})")))

    threading.Thread(target=work, name="loco-adb-update", daemon=True).start()


def directive(dev, row=None):
    """Consigne à l'application à chaque contact : garder le débogage sans fil joignable tant que l'appareil est appairé."""
    row = row or link(dev)
    return {"keep": bool(row and row["paired_at"] and row["mode"] != "tcp")}


def ensure(dev):
    """Connexion adb à l'appareil avant d'ouvrir un terminal : son numéro de série, sinon ValueError (motif lisible)."""
    if not available():
        raise ValueError(_("adb n'est pas installé sur ce serveur loco."))
    row = link(dev)
    if row is None or not (row["paired_at"] or row["mode"] == "tcp"):
        raise ValueError(_("Configurez d'abord le débogage adb de cet appareil (menu Plus › Débogage adb…)."))
    host, port = target(dev, row)
    if not host or not port:
        raise ValueError(_("Port adb inconnu : activez le débogage sans fil sur l'appareil, l'application loco l'indiquera "
                           "à son prochain contact."))
    serial = f"{host}:{port}"
    if device_state(serial) != "device":
        error = connect_serial(host, port)
        with connect() as conn:
            conn.execute("UPDATE android_adb SET error=?, connected_at=? WHERE device_id=?",
                         (error, "" if error else time.strftime("%Y-%m-%d %H:%M"), dev["id"]))
        if error:
            raise ValueError(_("Connexion adb impossible : %(e)s.", e=error))
    _prepare(dev, serial)
    return serial


# --------------------------------------------------------------------------- terminal : adb shell dans un pseudo-terminal

_WORKERS = {}
_GUARD = threading.Lock()


def start(sid, serial):
    start_command(sid, [binary(), "-s", serial, "shell"], _env(), "adb")


def start_command(sid, argv, env, name, after=None):
    """Relais d'une session de terminal tenue par loco lui-même : la commande tourne ici, dans un pseudo-terminal.
    name : « adb », ou le nom à dire quand la session se termine (terminal SSH d'un Home Assistant).
    after : appelé une fois la session terminée (rangement de ce qui ne servait qu'à elle)."""
    with _GUARD:
        if sid in _WORKERS:
            return
        worker = threading.Thread(target=_shell, args=(sid, argv, env, name, after), name="loco-pty-" + name, daemon=True)
        _WORKERS[sid] = worker
    worker.start()


def _winsize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _shell(sid, argv, env, name, after=None):
    """Relais d'une session : sortie du pseudo-terminal vers le navigateur, saisie du navigateur vers la commande."""
    from .remote import PACKET, BUFFER, OPEN
    conn = connect()
    conn.execute("PRAGMA synchronous = NORMAL")
    master = proc = None
    error = ""
    try:
        row = conn.execute("SELECT * FROM remote_sessions WHERE id=?", (sid,)).fetchone()
        if row is None or row["state"] not in OPEN:
            return
        master, slave = pty.openpty()
        size = (row["cols"], row["rows"])
        _winsize(master, *size)
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True, close_fds=True)
        os.close(slave)
        with conn:
            conn.execute("UPDATE remote_sessions SET state='active', agent_seen=? WHERE id=? AND state IN ('waiting','active')",
                         (time.time(), sid))
        consumed, pending, seen = row["input_seq"] - _unread(conn, sid), bytearray(), time.time()
        ended, quiet = False, 0.0
        while True:
            if not ended:
                if len(pending) < PACKET:
                    ready = select.select([master], [], [], WAIT_OUTPUT)[0]
                else:                               # navigateur en retard : ne plus lire, adb shell attendra
                    ready = []
                    time.sleep(WAIT_OUTPUT)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:                 # EIO : adb shell est terminé et le pseudo-terminal fermé
                        chunk = b""
                    if chunk:
                        pending += chunk
                        quiet = 0.0
                    else:
                        ended = True
                elif proc.poll() is not None:
                    quiet += WAIT_OUTPUT
                    ended = quiet > 2
            else:
                time.sleep(WAIT_OUTPUT)
            row = conn.execute("SELECT state, cols, rows FROM remote_sessions WHERE id=?", (sid,)).fetchone()
            if row is None or row["state"] not in OPEN:
                return
            if (row["cols"], row["rows"]) != size:
                size = (row["cols"], row["rows"])
                _winsize(master, *size)
                try:                                # adb n'a pas ce terminal pour terminal de contrôle : le prévenir
                    os.killpg(proc.pid, signal.SIGWINCH)
                except OSError:
                    pass
            inputs = conn.execute("SELECT seq, data FROM remote_packets WHERE session_id=? AND direction='in' AND seq>? "
                                  "ORDER BY seq", (sid, consumed)).fetchall()
            for packet in inputs:
                if not ended:
                    try:
                        os.write(master, packet["data"])
                    except OSError:
                        ended = True
                consumed = packet["seq"]
            backlog = 0
            if pending:
                backlog = conn.execute("SELECT COALESCE(SUM(length(data)),0) FROM remote_packets WHERE session_id=? "
                                       "AND direction='out'", (sid,)).fetchone()[0]
            flush = bool(pending) and backlog + len(pending) <= BUFFER
            if flush or inputs or time.time() - seen > SEEN_EVERY:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current = conn.execute("SELECT state, output_seq FROM remote_sessions WHERE id=?", (sid,)).fetchone()
                    if current is None or current["state"] not in OPEN:
                        return
                    seq = current["output_seq"]
                    if flush:
                        for start_at in range(0, len(pending), PACKET):
                            seq += 1
                            conn.execute("INSERT INTO remote_packets VALUES (?,?,?,?)",
                                         (sid, "out", seq, bytes(pending[start_at:start_at + PACKET])))
                        pending.clear()
                    if inputs:
                        conn.execute("DELETE FROM remote_packets WHERE session_id=? AND direction='in' AND seq<=?", (sid, consumed))
                    conn.execute("UPDATE remote_sessions SET output_seq=?, agent_seen=? WHERE id=?", (seq, time.time(), sid))
                seen = time.time()
            if ended and not pending:
                code = proc.poll()
                if name == "adb":
                    error = _("Session adb terminée.") if code in (0, None) else _("Session adb terminée (code %(code)s).", code=code)
                else:
                    error = _("Session terminée.") if code in (0, None) else _("Session terminée (code %(code)s).", code=code)
                break
    except Exception as e:                  # jamais de session « active » sans relais derrière
        error = (_("Terminal adb interrompu : %(e)s", e=str(e)[:200]) if name == "adb"
                 else _("Terminal interrompu : %(e)s", e=str(e)[:200]))
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGHUP)    # comme un terminal raccroché (un shell interactif ignore SIGTERM)
                except OSError:
                    pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
        if master is not None:
            os.close(master)
        if error:
            try:
                with conn:
                    conn.execute("UPDATE remote_sessions SET state='closed', error=? WHERE id=? AND state!='closed'", (error, sid))
            except Exception:
                pass
        conn.close()
        if after is not None:
            try:
                after()
            except Exception:
                pass
        with _GUARD:
            _WORKERS.pop(sid, None)


def _unread(conn, sid):
    """Saisies déjà reçues mais pas encore lues (session tout juste ouverte : aucune en principe)."""
    return conn.execute("SELECT COUNT(*) FROM remote_packets WHERE session_id=? AND direction='in'", (sid,)).fetchone()[0]


# --------------------------------------------------------------------------- portail : configuration par appareil

def _device(dev_id):
    from .fleet import _get
    dev = _get(dev_id)
    if dev["os_family"] != "android":
        abort(404)
    return dev


def _host(dev, value):
    host = (value or "").strip() or report(dev).get("ip") or fleet_address(dev)
    if not HOST_RE.match(host):
        raise ValueError(_("Adresse de l'appareil invalide."))
    return host


def _port(value):
    value = (value or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(_("Port attendu : un nombre entre 1 et 65535, affiché sur l'appareil."))
    return int(value)


def _save(conn, dev, **values):
    if link(dev, conn) is None:
        conn.execute("INSERT INTO android_adb (device_id) VALUES (?)", (dev["id"],))
    conn.execute("UPDATE android_adb SET " + ", ".join(f"{k}=?" for k in values) + " WHERE device_id=?", (*values.values(), dev["id"]))


@app.route("/fleet/<int:dev_id>/adb/pair", methods=["POST"])
@login_required
def fleet_adb_pair(dev_id):
    """Appairage par le débogage sans fil (Android 11+) : code et port affichés par l'appareil, saisis ici."""
    dev = _device(dev_id)
    conn = lock_access("remote.adb")
    conn.rollback()
    try:
        if not can_pair():
            raise ValueError(_("La version d'adb de ce serveur est trop ancienne pour l'appairage sans fil (30 ou plus requise)."))
        host, port = _host(dev, request.form.get("host")), _port(request.form.get("port"))
        code = request.form.get("code", "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError(_("Code d'appairage attendu : les six chiffres affichés sur l'appareil."))
        error = pair(host, port, code)
        if error:
            raise ValueError(_("Appairage refusé : %(e)s. Le code change à chaque ouverture de la fenêtre d'appairage.", e=error))
    except ValueError as e:
        flash(str(e), "error")
        return back("fleet_device", dev_id=dev_id, _anchor="details")
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    _save(conn, dev, host="" if host == report(dev).get("ip") else host, mode="wireless", port=0,
          paired_at=time.strftime("%Y-%m-%d %H:%M"), paired_by=g.user["username"], error="")
    audit("parc.adb.appairage", f"{dev['hostname']} ({host})", commit=False)
    conn.commit()
    flash(_("%(name)s est appairé : ouvrez le terminal adb. Le débogage sans fil doit rester activé sur l'appareil.",
            name=dev["hostname"]), "ok")
    return back("fleet_device", dev_id=dev_id, _anchor="details")


_SELF_PAIRING = {}                # dernier essai d'appairage demandé par chaque appareil (anti-répétition)
SELF_PAIR_EVERY = 8


@app.route("/agent/api/adb-pair", methods=["POST"])
def agent_adb_pair():
    """Appairage demandé depuis l'application loco de l'appareil (application 2.154.0 et plus) : la personne qui tient
    l'appareil a ouvert « Associer avec un code » dans le débogage sans fil et saisi le code dans la notification de
    loco ; l'application en joint le port. Le serveur appaire alors avec SA propre adresse seulement — celle d'où vient
    la demande, déclarée par l'appareil — jamais une adresse choisie par l'appelant. La suite est celle d'un appairage
    fait depuis la fiche : au contact suivant, loco pose les autorisations (partage d'écran sans fenêtre, …)."""
    from .fleet import _device as authenticated, _client_ip
    dev = authenticated()
    if dev["os_family"] != "android" or dev["state"] != "active":
        abort(404)
    data = request.get_json(silent=True) or {}
    code = str(data.get("code") or "").strip()
    port = data.get("port")
    host = _client_ip()
    if not re.fullmatch(r"\d{6}", code):
        return {"error": _("Code attendu : les six chiffres affichés par l'appareil.")}, 400
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return {"error": _("Port d'association introuvable : laissez la fenêtre « Associer avec un code » ouverte.")}, 400
    if not HOST_RE.match(host) or host not in (dev["addresses"] or "").split():
        return {"error": _("loco ne joint pas cet appareil directement : il doit être sur le même réseau que le serveur.")}, 409
    if not can_pair():
        return {"error": _("La version d'adb de ce serveur est trop ancienne pour l'appairage sans fil (30 ou plus requise).")}, 503
    now = time.time()
    with _GUARD:
        if now - _SELF_PAIRING.get(dev["id"], 0) < SELF_PAIR_EVERY:
            return {"error": _("Un appairage est déjà en cours, patientez quelques secondes.")}, 429
        _SELF_PAIRING[dev["id"]] = now
    error = pair(host, port, code)
    if error:
        return {"error": _("Appairage refusé : %(e)s. Le code change à chaque ouverture de la fenêtre d'association.", e=error)}, 422
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    _save(conn, dev, host="", mode="wireless", port=0, paired_at=time.strftime("%Y-%m-%d %H:%M"),
          paired_by="depuis l'appareil", error="")
    audit("parc.adb.appairage", f"{dev['hostname']} ({host}, depuis l'appareil)", username="", commit=False)
    conn.commit()
    return {"ok": True}


@app.route("/fleet/<int:dev_id>/adb/connect", methods=["POST"])
@login_required
def fleet_adb_connect(dev_id):
    """Port adb classique (5555), déjà ouvert sur l'appareil (adb tcpip, Android TV, VM) : l'appareil demande l'accord."""
    dev = _device(dev_id)
    conn = lock_access("remote.adb")
    conn.rollback()
    try:
        if not available():
            raise ValueError(_("adb n'est pas installé sur ce serveur loco."))
        host, port = _host(dev, request.form.get("host")), _port(request.form.get("port") or "5555")
        error = connect_serial(host, port)
    except ValueError as e:
        flash(str(e), "error")
        return back("fleet_device", dev_id=dev_id, _anchor="details")
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    _save(conn, dev, host=host, port=port, mode="tcp", error=error, connected_at="" if error else time.strftime("%Y-%m-%d %H:%M"))
    audit("parc.adb.connexion", f"{dev['hostname']} ({host}:{port}){' : ' + error if error else ''}", commit=False)
    conn.commit()
    if error:
        flash(_("Connexion adb impossible : %(e)s.", e=error), "error")
    else:
        flash(_("%(name)s est joignable par adb : ouvrez le terminal adb.", name=dev["hostname"]), "ok")
    return back("fleet_device", dev_id=dev_id, _anchor="details")


@app.route("/fleet/<int:dev_id>/adb/forget", methods=["POST"])
@login_required
def fleet_adb_forget(dev_id):
    dev = _device(dev_id)
    conn = lock_access("remote.adb")
    row = link(dev, conn)
    if row is not None:
        host, port = target(dev, row)
        if host and port:
            disconnect(f"{host}:{port}")
        conn.execute("UPDATE android_adb SET host='', port=0, mode='wireless', paired_at='', paired_by='', error='', "
                     "connected_at='', prepared='' WHERE device_id=?", (dev["id"],))
        conn.execute("UPDATE remote_sessions SET state='closed', error=? WHERE device_id=? AND kind='adb' AND state IN ('waiting','active')",
                     ("Débogage adb oublié pour cet appareil.", dev["id"]))
    audit("parc.adb.oubli", dev["hostname"], commit=False)
    conn.commit()
    flash(_("Débogage adb oublié pour %(name)s. Retirez aussi loco de la liste des appareils associés, sur l'appareil.",
            name=dev["hostname"]), "ok")
    return back("fleet_device", dev_id=dev_id, _anchor="details")


# --------------------------------------------------------------------------- routes : réveil, verrou, déverrouillage, calage
# Réponses JSON (pilotées par du JS dédié dans le portail, pas le gestionnaire data-ajax). Droit remote.adb (ENDPOINTS).


@app.route("/fleet/<int:dev_id>/adb/wake", methods=["POST"])
@login_required
def fleet_adb_wake(dev_id):
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return {"error": str(e)}, 409
    error = wake(serial)
    return {"ok": not error, "error": error, "locked": locked(serial)}


@app.route("/fleet/<int:dev_id>/adb/lockstate", methods=["POST"])
@login_required
def fleet_adb_lockstate(dev_id):
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return {"error": str(e)}, 409
    w, h = screen_size(serial)
    return {"locked": locked(serial), "w": w, "h": h}


@app.route("/fleet/<int:dev_id>/adb/unlock", methods=["POST"])
@login_required
def fleet_adb_unlock(dev_id):
    """Réveille et déverrouille : method = pattern (séquence « 1,4,5,9 »), pin ou password. Le secret n'est pas journalisé."""
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return {"error": str(e)}, 409
    method = request.form.get("method", "")
    if method == "pattern":
        seq = [int(n) for n in request.form.get("pattern", "").split(",") if n.strip().isdigit()]
        error = unlock(serial, dev, "pattern", seq)
    elif method in ("pin", "password"):
        error = unlock(serial, dev, method, request.form.get("code", ""))
    else:
        return {"error": _("méthode de déverrouillage inconnue")}, 400
    audit("parc.adb.unlock", f"{dev['hostname']} ({method})")           # jamais le secret
    return {"ok": not error, "error": error, "locked": locked(serial)}


@app.route("/fleet/<int:dev_id>/adb/calibrate", methods=["POST"])
@login_required
def fleet_adb_calibrate(dev_id):
    """Enregistre (ou réinitialise) le calage de la grille de schéma de l'appareil."""
    dev = _device(dev_id)
    if link(dev) is None:
        return {"error": _("Configurez d'abord le débogage adb de cet appareil.")}, 409
    if request.form.get("reset"):
        with connect() as conn:
            conn.execute("UPDATE android_adb SET unlock='' WHERE device_id=?", (dev["id"],))
        return {"ok": True, "geometry": dict(PATTERN_DEFAULT)}
    try:
        g = set_geometry(dev, float(request.form["cx"]), float(request.form["cy"]),
                         float(request.form["dx"]), float(request.form["dy"]))
    except (KeyError, ValueError):
        return {"error": _("valeurs de calage invalides")}, 400
    return {"ok": True, "geometry": g}


# --------------------------------------------------------------------------- écran : réveil et déverrouillage

# Grille de schéma en fractions de l'écran : centre (cx, cy), pas entre points (dx sur la largeur, dy sur la hauteur).
# Les 9 points sont numérotés 1..9 (gauche->droite, haut->bas). L'écran de verrouillage étant une surface sécurisée,
# on ne peut pas le voir pour se caler : le défaut est validé sur AOSP/Pixel/émulateur, les autres profils sont des
# POINTS DE DÉPART par constructeur, et le « calage » par appareil (android_adb.unlock) les corrige au besoin.
PATTERN_DEFAULT = {"cx": 0.5, "cy": 0.683, "dx": 0.278, "dy": 0.142}
PATTERN_PROFILES = {
    "google": PATTERN_DEFAULT, "android": PATTERN_DEFAULT, "nothing": PATTERN_DEFAULT, "pixel": PATTERN_DEFAULT,
    "samsung": {"cx": 0.5, "cy": 0.640, "dx": 0.300, "dy": 0.150},
    "xiaomi": {"cx": 0.5, "cy": 0.660, "dx": 0.285, "dy": 0.143},
    "redmi": {"cx": 0.5, "cy": 0.660, "dx": 0.285, "dy": 0.143},
    "poco": {"cx": 0.5, "cy": 0.660, "dx": 0.285, "dy": 0.143},
    "oneplus": {"cx": 0.5, "cy": 0.655, "dx": 0.292, "dy": 0.146},
    "oppo": {"cx": 0.5, "cy": 0.655, "dx": 0.292, "dy": 0.146},
    "realme": {"cx": 0.5, "cy": 0.655, "dx": 0.292, "dy": 0.146},
    "vivo": {"cx": 0.5, "cy": 0.655, "dx": 0.290, "dy": 0.145},
    "motorola": {"cx": 0.5, "cy": 0.675, "dx": 0.280, "dy": 0.142},
}


def screen_size(serial):
    """(largeur, hauteur) de l'écran de l'appareil en pixels, ou (0, 0)."""
    rc, out = run(["-s", serial, "shell", "wm", "size"], timeout=10)
    m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def locked(serial):
    """L'appareil est-il sur son écran de verrouillage (saisie de sécurité affichée) ?"""
    rc, out = run(["-s", serial, "shell", "dumpsys", "window"], timeout=12)
    if "mDreamingLockscreen=true" in out:
        return True
    if "mDreamingLockscreen=false" in out:
        return False
    return "mShowingLockscreen=true" in out or "isKeyguardShowing=true" in out


def wake(serial):
    """Réveille l'écran sans l'éteindre (KEYCODE_WAKEUP) ; renvoie "" ou un motif d'erreur."""
    rc, out = run(["-s", serial, "shell", "input", "keyevent", "224"], timeout=10)
    return "" if rc == 0 else _clean(out)


def manufacturer(serial):
    """Marque de l'appareil (ro.product.manufacturer, à défaut brand), en minuscules, pour choisir un profil de grille."""
    for prop in ("ro.product.manufacturer", "ro.product.brand"):
        rc, out = run(["-s", serial, "shell", "getprop", prop], timeout=8)
        brand = out.strip().splitlines()[-1].strip().lower() if out.strip() else ""
        if brand:
            return brand
    return ""


def profile_for(brand):
    """Profil de grille de départ pour une marque (défaut AOSP si inconnue)."""
    for key, geo in PATTERN_PROFILES.items():
        if brand and key in brand:
            return dict(geo)
    return dict(PATTERN_DEFAULT)


def geometry(dev, serial=None):
    """Centre et pas de la grille pour cet appareil (fractions) : calage enregistré, sinon profil de la marque."""
    row = link(dev)
    if row and row["unlock"]:
        try:
            saved = json.loads(row["unlock"])
            return {k: float(saved.get(k, PATTERN_DEFAULT[k])) for k in PATTERN_DEFAULT}
        except (ValueError, TypeError):
            pass
    return profile_for(manufacturer(serial) if serial else "")


def set_geometry(dev, cx, cy, dx, dy):
    """Enregistre le calage de la grille de cet appareil (fractions bornées)."""
    g = {"cx": min(max(cx, 0.1), 0.9), "cy": min(max(cy, 0.1), 0.95),
         "dx": min(max(dx, 0.1), 0.4), "dy": min(max(dy, 0.05), 0.3)}
    with connect() as conn:
        conn.execute("UPDATE android_adb SET unlock=? WHERE device_id=?", (json.dumps(g), dev["id"]))
    return g


def pattern_grid(serial, dev):
    """Les 9 points (x, y) du schéma, numérotés 1..9, pour l'écran réel de l'appareil (profil ou calage)."""
    w, h = screen_size(serial)
    if not w or not h:
        return []
    g = geometry(dev, serial)
    cx, cy, dx, dy = g["cx"] * w, g["cy"] * h, g["dx"] * w, g["dy"] * h
    return [(int(cx + (col - 1) * dx), int(cy + (row - 1) * dy)) for row in range(3) for col in range(3)]


def _path(points, steps=6, dwell=3):
    """Chemin tactile continu : points des dots reliés par des points intermédiaires, avec une pause sur chaque dot
    (le LockPatternView ne l'enregistre que si le doigt s'y attarde) — un tracé fiable."""
    path = []
    for i, (x, y) in enumerate(points):
        if i:
            xa, ya = points[i - 1]
            for s in range(1, steps + 1):
                path.append((int(xa + (x - xa) * s / steps), int(ya + (y - ya) * s / steps)))
        else:
            path.append((x, y))
        path += [(x, y)] * dwell
    return path


def inject_motion(serial, points, timeout=20):
    """Injecte un geste tactile continu (DOWN, MOVE..., UP) en un seul appel = un seul tracé. "" ou motif d'erreur."""
    if len(points) < 2:
        return _("geste trop court")
    cmds = ["input motionevent DOWN %d %d" % points[0]]
    cmds += ["input motionevent MOVE %d %d" % p for p in points[1:]]
    cmds.append("input motionevent UP %d %d" % points[-1])
    rc, out = run(["-s", serial, "shell", "; ".join(cmds)], timeout=timeout)
    return "" if rc == 0 else _clean(out)


def _open_bouncer(serial):
    """Réveille l'écran et ouvre la saisie de sécurité (schéma/PIN/mot de passe)."""
    wake(serial)
    time.sleep(0.5)
    run(["-s", serial, "shell", "wm", "dismiss-keyguard"], timeout=10)
    time.sleep(1.3)


def unlock(serial, dev, method, value):
    """Déverrouille l'appareil. method : pattern (séquence 1..9), pin (chiffres), password (texte). "" ou motif.
    Le secret (schéma, code, mot de passe) n'est jamais journalisé."""
    if method == "pattern":
        return unlock_pattern(serial, dev, value if isinstance(value, list) else [])
    if method == "pin":
        return unlock_pin(serial, value)
    if method == "password":
        return unlock_password(serial, value)
    return _("méthode de déverrouillage inconnue")


def unlock_pattern(serial, dev, sequence):
    """Réveille, ouvre la saisie, puis injecte le schéma dessiné (dots 1..9). "" si déverrouillé, sinon motif."""
    seq = [n for n in sequence if isinstance(n, int) and 1 <= n <= 9]
    if len(seq) < 2 or len(seq) != len(set(seq)):
        return _("schéma invalide")
    grid = pattern_grid(serial, dev)
    if not grid:
        return _("taille d'écran inconnue")
    points = _path([grid[n - 1] for n in seq])
    # L'injection d'un geste tactile est parfois mal captée : on réessaie (3 fois au plus, bien sous le seuil de
    # blocage d'Android), en rouvrant la saisie à chaque fois. Une géométrie fausse échouera les 3 fois.
    error = ""
    for _attempt in range(3):
        _open_bouncer(serial)
        error = inject_motion(serial, points)
        if error:
            return error
        time.sleep(0.9)
        if not locked(serial):
            return ""
    return _("toujours verrouillé (schéma, ou calage de la grille à revoir)")


def unlock_pin(serial, pin):
    """Réveille, ouvre la saisie, tape le code touche par touche (keyevent, jamais en un seul argument) puis Entrée."""
    digits = [c for c in str(pin) if c.isdigit()]
    if not 3 <= len(digits) <= 16:
        return _("code invalide")
    _open_bouncer(serial)
    run(["-s", serial, "shell", "input", "keyevent", *[str(7 + int(d)) for d in digits]], timeout=15)
    run(["-s", serial, "shell", "input", "keyevent", "66"], timeout=10)   # ENTER = KEYCODE_ENTER
    time.sleep(0.9)
    return "" if not locked(serial) else _("toujours verrouillé (code incorrect ?)")


def unlock_password(serial, password):
    """Réveille, ouvre la saisie, focalise le champ (centré) puis écrit le mot de passe et valide. input text exige
    le texte en argument ; il n'est jamais journalisé, et c'est l'appareil lui-même qu'on ouvre avec son mot de passe."""
    text = str(password)
    if not 3 <= len(text) <= 64 or "\n" in text or "\r" in text:
        return _("mot de passe invalide")
    w, h = screen_size(serial)
    _open_bouncer(serial)
    if w and h:
        run(["-s", serial, "shell", "input", "tap", str(w // 2), str(int(h * 0.54))], timeout=10)
        time.sleep(0.4)
    run(["-s", serial, "shell", "input", "text", text], timeout=12)
    run(["-s", serial, "shell", "input", "keyevent", "66"], timeout=10)
    time.sleep(0.9)
    return "" if not locked(serial) else _("toujours verrouillé (mot de passe incorrect ?)")


# --------------------------------------------------------------------------- écran déverrouillé : flux et gestes
# Une fois l'appareil déverrouillé, loco diffuse son écran (adb screenrecord H.264 → ffmpeg → MJPEG) et injecte les
# gestes par adb (tap, swipe, touches, texte). L'écran de verrouillage reste invisible (surface sécurisée) : ce flux
# ne sert qu'après déverrouillage. Pas de dialogue MediaProjection.


def key(serial, code):
    run(["-s", serial, "shell", "input", "keyevent", str(int(code))], timeout=8)


def tap(serial, x, y):
    run(["-s", serial, "shell", "input", "tap", str(int(x)), str(int(y))], timeout=8)


def swipe(serial, x1, y1, x2, y2, ms=120):
    run(["-s", serial, "shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)),
         str(int(ms))], timeout=8)


def type_text(serial, text):
    run(["-s", serial, "shell", "input", "text", str(text)], timeout=10)


def screen_frames(serial, bitrate=4000000):
    """Images JPEG de l'écran (non sécurisé) : adb screenrecord (H.264) → ffmpeg → MJPEG. screenrecord est relancé à
    sa limite de durée ; les process sont tués quand le navigateur ferme le flux (GeneratorExit)."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return
    try:
        while True:
            rec = subprocess.Popen([binary(), "-s", serial, "exec-out", "screenrecord", "--output-format=h264",
                                    "--bit-rate", str(bitrate), "--time-limit", "170", "-"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_env())
            enc = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-i", "-", "-f", "mjpeg", "-q:v", "6", "-"],
                                   stdin=rec.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            rec.stdout.close()
            buf = b""
            try:
                while True:
                    chunk = enc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        start = buf.find(b"\xff\xd8")
                        end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                        if start < 0 or end < 0:
                            break
                        yield buf[start:end + 2]
                        buf = buf[end + 2:]
            finally:
                for p in (enc, rec):
                    try:
                        p.kill()
                        p.wait(timeout=2)
                    except Exception:
                        pass
    except GeneratorExit:
        pass


_KEYS = {"back": 4, "home": 3, "recents": 187, "power": 26, "wake": 224, "enter": 66, "del": 67,
         "volup": 24, "voldown": 25}

# Appareils sous contrôle adb en ce moment (identifiant → dernier geste/flux) : l'agent l'apprend au check-in et
# affiche « contrôle à distance en cours » sur l'appareil (garde-fou visible, l'écran étant piloté sans dialogue).
_CONTROL = {}
CONTROL_WINDOW = 8          # secondes sans activité au-delà desquelles le contrôle est considéré terminé


def mark_control(dev_id):
    _CONTROL[dev_id] = time.time()


def under_control(dev_id):
    return time.time() - _CONTROL.get(dev_id, 0) < CONTROL_WINDOW


@app.route("/fleet/<int:dev_id>/adb/screen")
@login_required
def fleet_adb_screen(dev_id):
    """Flux MJPEG de l'écran déverrouillé (multipart). Un seul <img> dans le navigateur le lit en direct."""
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return Response(str(e), status=409, mimetype="text/plain")
    if not shutil.which("ffmpeg"):
        return Response(_("ffmpeg n'est pas installé sur le serveur loco : le flux de l'écran a besoin de ffmpeg."),
                        status=503, mimetype="text/plain")
    audit("parc.adb.ecran", dev["hostname"])

    def stream():
        for jpg in screen_frames(serial):
            mark_control(dev["id"])
            yield b"--loco\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n"

    return Response(stream(), mimetype="multipart/x-mixed-replace; boundary=loco",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/fleet/<int:dev_id>/adb/input", methods=["POST"])
@login_required
def fleet_adb_input(dev_id):
    """Geste injecté sur l'écran déverrouillé : tap/swipe (coordonnées en fractions de l'écran), touche, ou texte."""
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return {"error": str(e)}, 409
    mark_control(dev["id"])
    action = request.form.get("action")
    try:
        if action in ("tap", "swipe"):
            w, h = screen_size(serial)
            if not w or not h:
                return {"error": _("taille d'écran inconnue")}, 409
            fx = lambda v, d: max(0, min(d - 1, int(float(v) * d)))   # noqa: E731
            if action == "tap":
                tap(serial, fx(request.form["x"], w), fx(request.form["y"], h))
            else:
                swipe(serial, fx(request.form["x"], w), fx(request.form["y"], h),
                      fx(request.form["x2"], w), fx(request.form["y2"], h),
                      max(20, min(2000, int(request.form.get("ms", 120)))))
        elif action == "key":
            if request.form.get("key") not in _KEYS:
                return {"error": _("touche inconnue")}, 400
            key(serial, _KEYS[request.form["key"]])
        elif action == "text":
            type_text(serial, request.form.get("text", "")[:500])
        else:
            return {"error": _("action inconnue")}, 400
    except (KeyError, ValueError):
        return {"error": _("geste invalide")}, 400
    return {"ok": True}


# --------------------------------------------------------------------------- écran : flux scrcpy (basse latence)
# Variante basse latence : loco lance scrcpy-server (vidéo seule) sur l'appareil, lit le flux H.264 brut et le relaie
# au navigateur, qui le décode avec WebCodecs (décodage matériel, rien à transcoder sur le serveur). Même principe que
# wraith. Le contrôle passe par adb input (routes ci-dessus), pas par le canal de contrôle de scrcpy. Repli : le flux
# MJPEG screen_frames (navigateurs sans WebCodecs). scrcpy-server (Apache 2.0) est embarqué, voir scrcpy/README.md.
SCRCPY_SERVER = os.path.join(os.path.dirname(__file__), "scrcpy", "scrcpy-server.jar")
SCRCPY_VERSION = "4.0"
SCRCPY_REMOTE = "/data/local/tmp/scrcpy-server.jar"


def scrcpy_available():
    return os.path.isfile(SCRCPY_SERVER)


def scrcpy_stream(serial):
    """Unités d'accès H.264 (keyframe?, données Annex-B) de l'écran via scrcpy-server : démarre le serveur en vidéo
    seule, lit la socket transférée (octet factice + nom + codec, puis paquets [PTS/flags 8 | taille 4]), saute les
    paquets « session » (bit 0x80). Nettoie serveur et transfert à la fermeture du flux (GeneratorExit)."""
    if not scrcpy_available():
        return
    run(["-s", serial, "push", SCRCPY_SERVER, SCRCPY_REMOTE], timeout=30)
    scid = "%08x" % random.randrange(1, 2 ** 31)
    port = 27100 + random.randrange(0, 800)
    server = subprocess.Popen(
        [binary(), "-s", serial, "shell", "CLASSPATH=" + SCRCPY_REMOTE, "app_process", "/",
         "com.genymobile.scrcpy.Server", SCRCPY_VERSION, "scid=" + scid, "log_level=error",
         "audio=false", "control=false", "tunnel_forward=true", "max_size=1920", "max_fps=60",
         "video_bit_rate=8000000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_env())
    sock = None
    try:
        time.sleep(1.6)
        run(["-s", serial, "forward", "tcp:%d" % port, "localabstract:scrcpy_%s" % scid], timeout=10)
        time.sleep(0.4)
        sock = socket.create_connection(("127.0.0.1", port), timeout=8)
        sock.settimeout(10)

        def rd(n):
            buf = b""
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise EOFError
                buf += chunk
            return buf

        rd(1); rd(64); rd(4)                               # octet factice (tunnel) + nom de l'appareil + codec « h264 »
        while True:
            head = rd(12)
            if head[0] & 0x80:                             # paquet « session » (resize…) : 12 octets, sans données
                continue
            keyframe = bool(head[0] & 0x40)
            length = struct.unpack(">I", head[8:12])[0]
            if not 0 < length <= 30_000_000:
                break
            yield keyframe, rd(length)
    except (OSError, EOFError, GeneratorExit):
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        run(["-s", serial, "forward", "--remove", "tcp:%d" % port], timeout=8)
        try:
            server.terminate()
            server.wait(timeout=3)
        except Exception:
            pass
        run(["-s", serial, "shell", "pkill", "-f", "com.genymobile.scrcpy"], timeout=8)


@app.route("/fleet/<int:dev_id>/adb/stream")
@login_required
def fleet_adb_stream(dev_id):
    """Flux H.264 (scrcpy) de l'écran déverrouillé, décodé par WebCodecs. Chaque unité d'accès est préfixée de
    [1 octet keyframe][4 octets longueur]. Repli pour les navigateurs sans WebCodecs : le flux MJPEG /adb/screen."""
    dev = _device(dev_id)
    try:
        serial = ensure(dev)
    except ValueError as e:
        return Response(str(e), status=409, mimetype="text/plain")
    if not scrcpy_available():
        return Response(_("scrcpy-server est absent du serveur loco."), status=503, mimetype="text/plain")
    audit("parc.adb.ecran", dev["hostname"])

    def stream():
        for keyframe, au in scrcpy_stream(serial):
            mark_control(dev["id"])
            yield struct.pack(">BI", 1 if keyframe else 0, len(au)) + au

    return Response(stream(), mimetype="application/octet-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
