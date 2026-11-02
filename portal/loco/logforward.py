"""Envoi des journaux vers un serveur syslog (RFC 5424) : le Journal de loco, ceux des postes, ou les deux.

Journal de loco : un fil de fond suit la table d'audit et n'avance son curseur qu'une fois l'envoi fait. Si le serveur
syslog est absent, les événements attendent (dans la limite de la conservation du Journal).

Postes : les agents remontent leurs journaux à loco par leur canal habituel, et loco les relaie. Les postes n'ont donc
pas besoin de joindre le serveur syslog. Si le relais échoue, l'agent reçoit 503 et garde sa position : rien n'est stocké
sur le serveur, et l'envoi reprend là où il s'était arrêté, dans la limite de ce que le poste conserve.

Après une panne, un événement peut arriver deux fois. Syslog n'a pas d'accusé de réception : en TCP et TLS, une coupure
brutale peut perdre ce qui était déjà parti ; en UDP, rien ne garantit l'arrivée.
"""
import datetime
import json
import re
import select
import socket
import ssl
import threading
import time

from flask import abort, flash, render_template, request

from . import app, config, fleet, groups, ha, journal
from .auth import audit, login_required, require
from .db import db, lease, transaction
from .i18n import _
from .util import back

SETTING, CURSOR, STATUS = "syslog_config", "syslog_cursor", "syslog_status"
SD_ID = "loco@32473"                    # numéro d'entreprise réservé aux exemples (RFC 5612) : loco n'en a pas d'attribué
TIMEOUT = 5                             # secondes : un serveur syslog lent ne doit pas retenir le portail
PAUSE_AFTER_FAILURE = 30
BATCH = 200                             # événements du Journal par envoi
AGENT_LINES = 500                       # lignes acceptées par envoi d'un agent
AGENT_MESSAGE = 4000
AGENT_BATCHES_PER_MINUTE = 30
AGENT_VERSION = (2, 124)                # agents qui savent remonter leurs journaux
MAX_DATAGRAM, MAX_MESSAGE = 2048, 8192  # UDP (RFC 5426), TCP et TLS
FAILING_AFTER = 300                     # alerte « envoi en échec » passé ce délai
TRANSPORTS = ("tls", "tcp", "udp")
FRAMINGS = ("lf", "octet")              # fin de ligne (le plus compatible) ou longueur en tête (RFC 5425 et 6587)
SEVERITIES = (3, 4, 5, 6)
FACILITIES = tuple(range(16, 24))       # local0 à local7
HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
HEADER_RE = re.compile(r"[^\x21-\x7e]")
DEFAULTS = {"enabled": False, "host": "", "port": 0, "transport": "tls", "framing": "lf", "ca": "", "facility": 16,
            "loco": True, "devices": False, "min_severity": 4, "security": True, "groups": []}
_status_lock, _status = threading.Lock(), {}
_rates = {}


class SyslogError(Exception):
    """Envoi impossible ; le message est prêt à être affiché."""


# --------------------------------------------------------------------------- configuration

def load(conn=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    if row:
        try:
            stored = json.loads(row[0])
        except (TypeError, ValueError):
            stored = {}
        cfg.update({k: v for k, v in stored.items() if k in cfg})
    return cfg


def _store(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


def port_of(cfg):
    return cfg["port"] or (6514 if cfg["transport"] == "tls" else 514)


def ready(cfg):
    return bool(cfg["enabled"] and cfg["host"])


# --------------------------------------------------------------------------- format RFC 5424

def _header(value, limit):
    return HEADER_RE.sub("_", str(value or ""))[:limit] or "-"


def _plain(value):
    """Une seule ligne, sans séquences de terminal ni caractères de contrôle : un poste ne peut pas forger de ligne."""
    return CONTROL_RE.sub(" ", ANSI_RE.sub("", str(value or ""))).strip()


def _sd_value(value):
    return _plain(value).replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _stamp(when):
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def message(facility, severity, when, host, app_name, procid, msgid, params, text, limit=MAX_MESSAGE):
    """Message RFC 5424 : <PRI>1 DATE HÔTE APPLI PID MSGID [DONNÉES] TEXTE."""
    sd = "".join(f' {k}="{_sd_value(v)}"' for k, v in params.items() if v not in ("", None))
    line = (f"<{facility * 8 + severity}>1 {_stamp(when)} {_header(host, 255)} {_header(app_name, 48)} "
            f"{_header(procid, 128)} {_header(msgid, 32)} [{SD_ID}{sd}] {_plain(text)}")
    return line.encode("utf-8", "replace")[:limit].decode("utf-8", "ignore").encode("utf-8")


def _audit_message(cfg, row, limit):
    described = journal.describe(row["action"])
    try:
        when = datetime.datetime.strptime(row["at"], "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        when = datetime.datetime.now().astimezone()
    text = str(described["label"]) + (f" : {row['detail']}" if row["detail"] else "")
    return message(cfg["facility"], 4 if described["failure"] else 6, when, config.HOST, "loco", "-", row["action"],
                   {"type": "journal", "id": row["id"], "user": row["username"], "ip": row["ip"],
                    "action": row["action"], "category": journal.category(row["action"])}, text, limit)


def _number(value, low, high, default):
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else default


def _entry_message(dev, entry, limit):
    """Ligne remontée par un agent. L'hôte vient de la base : un poste ne peut pas se faire passer pour un autre."""
    try:
        when = datetime.datetime.fromtimestamp(float(entry.get("t")), datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        when = datetime.datetime.now(datetime.timezone.utc)
    return message(_number(entry.get("f"), 0, 23, 1), _number(entry.get("p"), 0, 7, 6), when, dev["hostname"] or dev["uid"][:12],
                   entry.get("a") or "-", entry.get("i") or "-", entry.get("n") or "-",
                   {"type": "poste", "ip": dev["ip"], "os": dev["os_family"], "channel": entry.get("c")},
                   str(entry.get("m") or "")[:AGENT_MESSAGE], limit)


def _limit(cfg):
    return MAX_DATAGRAM if cfg["transport"] == "udp" else MAX_MESSAGE


# --------------------------------------------------------------------------- envoi

def _explain(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return _("Certificat du serveur refusé : %(reason)s", reason=error.verify_message)
    if isinstance(error, ssl.SSLError):
        return _("Échec de la négociation TLS : %(reason)s", reason=error.reason or str(error))
    if isinstance(error, socket.gaierror):
        return _("Nom de serveur introuvable.")
    if isinstance(error, (socket.timeout, TimeoutError)):
        return _("Pas de réponse du serveur (délai dépassé).")
    if isinstance(error, ConnectionRefusedError):
        return _("Connexion refusée par le serveur.")
    return str(error)[:200] or error.__class__.__name__


class Sender:
    """Une connexion gardée ouverte vers le serveur syslog. Après un échec, on n'insiste pas pendant 30 s : les fils du
    portail ne restent pas bloqués sur un serveur absent."""

    def __init__(self):
        self.lock, self.sock, self.key, self.pause_until, self.reason = threading.Lock(), None, None, 0, ""

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _connect(self, cfg):
        host, port = cfg["host"], port_of(cfg)
        if cfg["transport"] == "udp":
            family, kind, proto, _name, address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
            sock = socket.socket(family, kind, proto)
            sock.settimeout(TIMEOUT)
            sock.connect(address)               # « connecté » : un port fermé remonte en erreur au lieu de se perdre
            return sock
        sock = socket.create_connection((host, port), timeout=TIMEOUT)
        if cfg["transport"] == "tls":
            try:
                context = ssl.create_default_context(cadata=cfg["ca"] or None)
                # Chaîne et adresse restent vérifiées. Le mode strict de Python 3.13 refuserait en plus les autorités
                # internes sans « Authority Key Identifier », courantes sur les serveurs syslog d'entreprise.
                context.verify_flags &= ~ssl.VERIFY_X509_STRICT
                sock = context.wrap_socket(sock, server_hostname=host)
            except (OSError, ValueError):
                sock.close()
                raise
        sock.settimeout(TIMEOUT)
        return sock

    def _alive(self):
        """Le serveur a-t-il fermé la connexion gardée ouverte ? Il n'envoie jamais rien d'autre."""
        try:
            if select.select([self.sock], [], [], 0)[0]:
                self.sock.settimeout(0)
                alive = bool(self.sock.recv(1))
                self.sock.settimeout(TIMEOUT)
                return alive
        except ssl.SSLWantReadError:            # message TLS interne (ticket de session), pas une fermeture
            self.sock.settimeout(TIMEOUT)
        except OSError:
            return False
        return True

    def _write(self, cfg, messages):
        if cfg["transport"] == "udp":
            for raw in messages:
                self.sock.send(raw)
        elif cfg["framing"] == "octet":
            self.sock.sendall(b"".join(b"%d %s" % (len(raw), raw) for raw in messages))
        else:
            self.sock.sendall(b"".join(raw + b"\n" for raw in messages))

    def send(self, cfg, messages, probe=False):
        """Envoie tout ou lève SyslogError. probe : attend un instant qu'un refus remonte (bouton Tester)."""
        if not probe and time.monotonic() < self.pause_until:
            raise SyslogError(self.reason)      # la vraie cause, pas « en pause »
        if not self.lock.acquire(timeout=TIMEOUT):
            raise SyslogError(_("Serveur syslog trop lent : nouvel essai dans quelques secondes."))
        try:
            key = (cfg["host"], port_of(cfg), cfg["transport"], cfg["ca"])
            if key != self.key:
                self.close()
                self.key = key
            for attempt in (1, 2):              # une connexion gardée a pu être fermée entre deux envois
                try:
                    if self.sock is None or not self._alive():
                        self.close()
                        self.sock = self._connect(cfg)
                    self._write(cfg, messages)
                    if probe:
                        self._probe()
                    self.pause_until = 0
                    return
                except (OSError, ValueError) as e:
                    self.close()
                    if attempt == 2 or isinstance(e, (ssl.SSLError, socket.gaierror, ValueError)):
                        self.reason = _explain(e)
                        self.pause_until = time.monotonic() + PAUSE_AFTER_FAILURE
                        raise SyslogError(self.reason) from e
        finally:
            self.lock.release()

    def _probe(self):
        self.sock.settimeout(0.4)
        try:
            if not self.sock.recv(1) and self.key[2] != "udp":
                raise ConnectionResetError(_("Le serveur a fermé la connexion après l'envoi."))
        except (socket.timeout, TimeoutError, ssl.SSLWantReadError):
            pass                                # silence : c'est le comportement normal d'un serveur syslog


SENDER = Sender()


# --------------------------------------------------------------------------- état

def _note(conn, error=""):
    """Dernier envoi réussi ou en échec : écrit en base au changement, sinon au plus une fois par minute."""
    now = fleet._now()
    with _status_lock:
        if not _status:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (STATUS,)).fetchone()
            _status.update(json.loads(row[0]) if row else {})
        changed = bool(_status.get("failing_since")) != bool(error)
        if error:
            _status.update(last_error=error, last_failed=now, failing_since=_status.get("failing_since") or now)
        else:
            _status.update(last_ok=now, failing_since="")
        if changed or time.time() - _status.get("written", 0) > 60:
            _status["written"] = time.time()
            _store(conn, STATUS, _status)


def status(conn=None):
    conn = conn or db()
    with _status_lock:
        if _status:
            return dict(_status)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (STATUS,)).fetchone()
    return json.loads(row[0]) if row else {}


def failure(conn):
    """Raison de l'échec si l'envoi échoue depuis plus de 5 minutes (règle d'alerte), sinon ""."""
    if not ready(load(conn)):
        return ""
    current = status(conn)
    since = current.get("failing_since")
    if not since:
        return ""
    elapsed = datetime.datetime.now() - datetime.datetime.strptime(since, "%Y-%m-%d %H:%M:%S")
    return current.get("last_error") or "?" if elapsed.total_seconds() > FAILING_AFTER else ""


def _cursor(conn):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (CURSOR,)).fetchone()
    try:
        return int(json.loads(row[0])) if row else None
    except (TypeError, ValueError):
        return None


def _newest_audit(conn):
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]


# --------------------------------------------------------------------------- Journal de loco

def ship_journal(conn, cfg, sender=None):
    """Envoie les événements suivants du Journal ; renvoie leur nombre. Curseur avancé seulement si l'envoi a réussi."""
    cursor = _cursor(conn)
    if cursor is None:                          # premier passage : à partir de maintenant, pas tout l'historique
        _store(conn, CURSOR, _newest_audit(conn))
        return 0
    rows = conn.execute("SELECT * FROM audit WHERE id>? ORDER BY id LIMIT ?", (cursor, BATCH)).fetchall()
    if not rows:
        return 0
    limit = _limit(cfg)
    try:
        (sender or SENDER).send(cfg, [_audit_message(cfg, row, limit) for row in rows])
    except SyslogError as e:
        _note(conn, str(e))
        return 0
    _store(conn, CURSOR, rows[-1]["id"])
    _note(conn)
    return len(rows)


_started = False


def start():
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(10)
        held_until = 0
        while True:
            pause = 5
            try:
                with transaction() as conn:
                    cfg = load(conn)
                    if ready(cfg) and cfg["loco"] and not ha.skip_background():
                        now = time.monotonic()
                        if now >= held_until and lease("syslog", ttl=120):    # un seul processus envoie
                            held_until = now + 60
                        if now < held_until and ship_journal(conn, cfg) == BATCH:
                            pause = 0
            except Exception:                   # un fil de fond ne doit jamais faire tomber le serveur
                pause = 30
            time.sleep(pause)

    threading.Thread(target=loop, name="loco-syslog", daemon=True).start()


# --------------------------------------------------------------------------- postes

def concerned(conn, cfg, dev):
    """Ce poste doit-il remonter ses journaux ?"""
    if not (ready(cfg) and cfg["devices"]) or dev["state"] != "active":
        return False
    if not cfg["groups"]:
        return True
    marks = ",".join("?" * len(cfg["groups"]))
    return conn.execute(f"SELECT 1 FROM device_group_members WHERE device_id=? AND group_id IN ({marks}) LIMIT 1",
                        (dev["id"], *cfg["groups"])).fetchone() is not None


def directive(conn, dev):
    """Consigne transmise à l'agent à chaque contact ; None si ce poste n'a rien à remonter."""
    cfg = load(conn)
    return {"min": cfg["min_severity"], "security": cfg["security"]} if concerned(conn, cfg, dev) else None


def _too_fast(dev_id):
    now, recent = time.monotonic(), _rates.get(dev_id, [])
    recent = [t for t in recent if now - t < 60]
    allowed = len(recent) < AGENT_BATCHES_PER_MINUTE
    _rates[dev_id] = recent + [now] if allowed else recent
    return not allowed


@app.route("/agent/api/logs", methods=["POST"])
def agent_logs():
    """Journaux d'un poste, relayés aussitôt au serveur syslog : 503 si le relais échoue, l'agent garde sa position."""
    dev = fleet._device()
    data = fleet._json()
    conn = db()
    cfg = load(conn)
    if not concerned(conn, cfg, dev):
        return {"ok": False, "off": True}
    entries = data.get("entries")
    if not isinstance(entries, list) or len(entries) > AGENT_LINES:
        abort(400)
    if isinstance(data.get("error"), str):
        conn.execute("UPDATE devices SET log_error=? WHERE id=?", (data["error"][:200], dev["id"]))
        conn.commit()
    limit = _limit(cfg)
    messages = [_entry_message(dev, e, limit) for e in entries if isinstance(e, dict) and e.get("m")]
    if not messages:
        return {"ok": True, "accepted": 0}
    if _too_fast(dev["id"]):
        return {"ok": False}, 429, {"Retry-After": "30"}
    try:
        SENDER.send(cfg, messages)
    except SyslogError as e:
        _note(conn, str(e))
        conn.commit()
        return {"ok": False}, 503, {"Retry-After": "60"}
    conn.execute("UPDATE devices SET log_sent_at=?, log_lines=log_lines+?, log_error='' WHERE id=?",
                 (fleet._now(), len(messages), dev["id"]))
    _note(conn)
    conn.commit()
    return {"ok": True, "accepted": len(messages)}


def _devices_view(conn, cfg):
    """Postes concernés et où ils en sont, ceux à regarder en premier."""
    rows = [d for d in conn.execute("SELECT * FROM devices WHERE state='active' ORDER BY hostname COLLATE NOCASE").fetchall()
            if not cfg["groups"] or concerned(conn, dict(cfg, enabled=True, host=cfg["host"] or "-", devices=True), d)]
    hour_ago = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    for d in rows:
        too_old = fleet.version_key(d["agent_version"]) < AGENT_VERSION
        tone, state = ("err", d["log_error"]) if d["log_error"] else \
            ("warn", _("Agent à mettre à jour")) if too_old else \
            ("ok", _("Envoie ses journaux")) if d["log_sent_at"] >= hour_ago else \
            ("", _("Rien d'envoyé depuis une heure") if d["log_sent_at"] else _("En attente du premier envoi"))
        lines.append({"id": d["id"], "hostname": d["hostname"] or d["uid"][:12], "tone": tone, "state": state,
                      "sent": fleet.ago_text(d["log_sent_at"]) if d["log_sent_at"] else "", "lines": d["log_lines"],
                      "order": ({"err": 0, "warn": 1, "": 2, "ok": 3}[tone])})
    lines.sort(key=lambda r: r["order"])
    return {"total": len(lines), "active": sum(1 for r in lines if r["tone"] == "ok"), "rows": lines[:50]}


# --------------------------------------------------------------------------- page

def _page(cfg, code=200):
    conn = db()
    cursor = _cursor(conn)
    backlog = conn.execute("SELECT COUNT(*) FROM audit WHERE id>?", (cursor,)).fetchone()[0] if cursor is not None else 0
    current = status(conn)
    return render_template("syslog.html", cfg=cfg, port=port_of(cfg), device_groups=groups.listing("device"), backlog=backlog,
                           state=current, failing=bool(current.get("failing_since")),
                           last_ok=fleet.ago_text(current["last_ok"]) if current.get("last_ok") else "",
                           last_failed=fleet.ago_text(current["last_failed"]) if current.get("last_failed") else "",
                           devices=_devices_view(conn, cfg) if cfg["devices"] else None,
                           min_version=".".join(map(str, AGENT_VERSION)), facilities=FACILITIES), code


def _form_config():
    """Configuration du formulaire et erreur éventuelle."""
    form = request.form
    cfg = load()
    cfg["enabled"] = form.get("enabled") == "1"
    cfg["host"] = form.get("host", "").strip().strip("[]")
    cfg["port"] = int(form["port"]) if form.get("port", "").isdigit() and 0 < int(form["port"]) < 65536 else 0
    if form.get("transport") in TRANSPORTS:
        cfg["transport"] = form["transport"]
    if form.get("framing") in FRAMINGS:
        cfg["framing"] = form["framing"]
    if "ca" in form:                            # champ absent quand le transport n'est pas TLS : on garde l'autorité
        cfg["ca"] = form["ca"].strip()[:65536]
    if form.get("facility", "").isdigit() and int(form["facility"]) in FACILITIES:
        cfg["facility"] = int(form["facility"])
    cfg["loco"] = form.get("loco") == "1"
    cfg["devices"] = form.get("devices") == "1"
    cfg["security"] = form.get("security") == "1"
    if form.get("min_severity", "").isdigit() and int(form["min_severity"]) in SEVERITIES:
        cfg["min_severity"] = int(form["min_severity"])
    known = {g["id"] for g in groups.listing("device")}
    cfg["groups"] = sorted({int(v) for v in form.getlist("groups") if v.isdigit() and int(v) in known})
    error = ""
    if cfg["host"] and not HOST_RE.match(cfg["host"]):
        error = _("Adresse du serveur syslog invalide.")
    elif cfg["enabled"] and not cfg["host"]:
        error = _("Indiquez l'adresse du serveur syslog.")
    elif cfg["enabled"] and not (cfg["loco"] or cfg["devices"]):
        error = _("Choisissez ce qu'il faut envoyer : le journal de loco, les journaux des postes, ou les deux.")
    elif cfg["transport"] == "tls" and cfg["ca"]:
        try:
            ssl.create_default_context(cadata=cfg["ca"])
        except (ssl.SSLError, ValueError):
            error = _("Autorité de certification illisible : collez un certificat au format PEM.")
    return cfg, error


@app.route("/admin/syslog")
@login_required
def syslog_settings():
    require("syslog.configure")
    return _page(load())


@app.route("/admin/syslog", methods=["POST"])
@login_required
def syslog_save():
    require("syslog.configure")
    cfg, error = _form_config()
    if error:
        flash(error, "error")
        return _page(cfg, 400)
    conn = db()
    previous = load(conn)
    _store(conn, SETTING, cfg)
    if cfg["enabled"] and cfg["loco"] and not (previous["enabled"] and previous["loco"]):
        _store(conn, CURSOR, _newest_audit(conn))       # à partir de maintenant, pas tout l'historique
    sources = [label for on, label in ((cfg["loco"], "loco"), (cfg["devices"], "postes")) if on]
    audit("syslog.configuration", f"{cfg['transport']}://{cfg['host']}:{port_of(cfg)} ; {' + '.join(sources) or '-'}"
          if cfg["enabled"] else "désactivé")
    flash(_("Réglages de l'envoi syslog enregistrés."), "ok")
    return back("syslog_settings")


@app.route("/admin/syslog/test", methods=["POST"])
@login_required
def syslog_test():
    """Teste les valeurs à l'écran, sans les enregistrer, sur une connexion à part."""
    require("syslog.configure")
    cfg, error = _form_config()
    if not error and not cfg["host"]:
        error = _("Indiquez l'adresse du serveur syslog.")
    if error:
        return {"ok": False, "message": error}
    sender = Sender()
    raw = message(cfg["facility"], 6, datetime.datetime.now().astimezone(), config.HOST, "loco", "-", "syslog.test",
                  {"type": "test"}, _("Message de test envoyé depuis loco (%(host)s).", host=config.HOST), _limit(cfg))
    try:
        sender.send(cfg, [raw], probe=True)
    except SyslogError as e:
        return {"ok": False, "message": _("Échec : %(error)s", error=str(e))}
    finally:
        sender.close()
    note = _("Message envoyé en UDP : aucun accusé de réception, vérifiez qu'il est bien arrivé.") if cfg["transport"] == "udp" \
        else _("Message de test envoyé.")
    return {"ok": True, "message": note}
