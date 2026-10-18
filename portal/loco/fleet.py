"""Parc de postes : agent loco-client (Windows, Linux), état des mises à jour, actions à distance.

L'agent s'inscrit avec la clé d'inscription du serveur, puis s'authentifie avec un jeton qui lui est propre :
le poste est reconnu par son identifiant, jamais par son adresse IP (un changement d'adresse ne le déconnecte
pas). Il contacte le serveur toutes les CHECKIN secondes, envoie son inventaire et reçoit les actions en attente.
Les actions forment une liste fermée ; les sessions interactives des administrateurs sont gérées par remote.py.
Seule « uninstall » retire l'agent du poste.
"""

import base64
import datetime
import fnmatch
import hashlib
import hmac
import ipaddress
import io
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import zipfile

from flask import Response, abort, flash, g, redirect, render_template, request, send_file, url_for

from . import app, config, permissions
from .auth import audit, login_required, can, require
from .db import db
from .util import back

CHECKIN = 120                   # secondes entre deux contacts de l'agent
SCAN_HOURS = 6                  # inventaire complet des mises à jour (et après chaque action)
STALE_DAYS = 1                  # sans contact depuis : orange
SENT_TIMEOUT_HOURS = 6          # action transmise sans résultat : échec
MAX_BODY = 4 * 1024 * 1024
UID_RE = re.compile(r"^[0-9a-f]{32}$")
FAMILIES = {"windows": "Windows", "debian": "Debian / Ubuntu", "rhel": "Red Hat"}
ACTIONS = {"scan": "Inventaire", "update": "Mise à jour", "install-app": "Installation", "reboot": "Redémarrage",
           "uninstall": "Retrait de l'agent"}
AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ago(text):
    try:
        return datetime.datetime.now() - datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- réglages

def setting(key, default=""):
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db().execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    db().commit()


def red_threshold():
    try:
        value = int(setting("fleet_red_threshold", "10"))
        return value if 2 <= value <= 10000 else 10
    except (TypeError, ValueError):
        return 10


def enroll_key():
    key = setting("enroll_key")
    if not key:
        key = secrets.token_urlsafe(18)
        set_setting("enroll_key", key)
    return key


# --------------------------------------------------------------------------- versions et logiciels

def version_key(v):
    """Version comparable : « 24.09 » < « 24.10 », « 1.2 » == « 1.2.0 »."""
    parts = [int(x) for x in re.findall(r"\d+", v or "")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def matches(pattern, display_name):
    """Nom affiché dans Windows (Programmes et fonctionnalités) : motif avec *, nom exact, ou nom suivi d'une
    version ou d'une précision entre parenthèses (« 7-Zip 26.04 (x64) », « Notepad++ (64-bit x64) ») ;
    « Git » ne correspond donc pas à « Git Extensions »."""
    pattern, name = (pattern or "").strip().lower(), (display_name or "").strip().lower()
    if not pattern or not name:
        return False
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(name, pattern)
    if name == pattern:
        return True
    return name.startswith(pattern) and bool(re.match(r"^\s*(\(|-\s|v?\d|x64\b|x86\b|64-bit|32-bit)", name[len(pattern):]))


def catalog():
    return db().execute("SELECT * FROM win_apps WHERE status='ready' ORDER BY name COLLATE NOCASE").fetchall()


def catalog_state(inventory, apps=None):
    """Logiciels du catalogue sur un poste Windows : [(fiche, version installée, à mettre à jour)]."""
    installed = inventory.get("apps") or []
    out = []
    for a in apps if apps is not None else catalog():
        pattern = a["detect"] or a["name"]
        versions = [s.get("version", "") for s in installed if isinstance(s, dict) and matches(pattern, s.get("name"))]
        if not versions:
            out.append((a, "", False))
            continue
        current = max(versions, key=version_key)
        outdated = bool(a["version"]) and version_key(current) < version_key(a["version"])
        out.append((a, current or "?", outdated))
    return out


def fleet_usage(apps):
    """Par logiciel du catalogue : {slug: (postes où il est installé, postes à mettre à jour)}."""
    usage = {a["slug"]: [0, 0] for a in apps}
    for dev in db().execute("SELECT inventory FROM devices WHERE state='active' AND os_family='windows'"):
        try:
            inv = json.loads(dev["inventory"] or "{}")
        except ValueError:
            continue
        for a, installed, outdated in catalog_state(inv, apps):
            if installed:
                usage[a["slug"]][0] += 1
            if outdated:
                usage[a["slug"]][1] += 1
    return usage


def app_payload(a):
    return {"slug": a["slug"], "name": a["name"], "version": a["version"], "kind": a["kind"], "args": a["args"],
            "file": a["filename"], "sha256": a["sha256"],
            "url": f"/win/files/{a['slug']}/{urllib.parse.quote(a['filename'])}"}


# --------------------------------------------------------------------------- état des postes

def inventory(dev):
    try:
        data = json.loads(dev["inventory"] or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def app_updates(dev, apps=None):
    if dev["os_family"] != "windows":
        return []
    return [a for a, _, outdated in catalog_state(inventory(dev), apps) if outdated]


def last_result(dev_id):
    return db().execute("SELECT * FROM device_commands WHERE device_id=? AND state IN ('success', 'failed') "
                        "AND action != 'scan' ORDER BY id DESC LIMIT 1", (dev_id,)).fetchone()


def status(dev, apps=None):
    """Voyant du volume de mises à jour : aucune / quelques-unes / au moins le seuil rouge.
    Une information de santé (sécurité, échec, redémarrage) ne change pas ce comptage."""
    if dev["state"] == "removing":
        return "grey", "retrait de l'agent demandé"
    seen = _ago(dev["last_seen"])
    pending_apps = len(app_updates(dev, apps))
    reasons = []
    if dev["security_updates"]:
        reasons.append(f"{dev['security_updates']} mise(s) à jour de sécurité en attente")
    other = dev["updates"] - dev["security_updates"]
    if other > 0:
        reasons.append(f"{other} {'autre(s) ' if dev['security_updates'] else ''}mise(s) à jour du système")
    if pending_apps:
        reasons.append(f"{pending_apps} logiciel(s) à mettre à jour")
    if dev["reboot_required"]:
        reasons.append("redémarrage nécessaire")
    last = last_result(dev["id"])
    if last and last["state"] == "failed":
        reasons.append(f"échec : {ACTIONS.get(last['action'], last['action']).lower()}")
    error = inventory(dev).get("error")
    if error:
        reasons.append(f"inventaire incomplet : {error}")
    if seen is None or seen > datetime.timedelta(days=STALE_DAYS):
        reasons.append("poste sans contact récent")
    if error or not dev["last_scan"] or seen is None or seen > datetime.timedelta(days=STALE_DAYS):
        return "grey", ", ".join(reasons) or "inventaire en cours"
    total = dev["updates"] + pending_apps
    color = "green" if total == 0 else "red" if total >= red_threshold() else "orange"
    return color, ", ".join(reasons) or "à jour"


def action_label(cmd):
    """« Installation : 7-Zip » plutôt que « Installation »."""
    label = ACTIONS.get(cmd["action"], cmd["action"])
    try:
        params = json.loads(cmd["params"] or "{}")
    except ValueError:
        params = {}
    if params.get("slug"):
        row = db().execute("SELECT name FROM win_apps WHERE slug=?", (params["slug"],)).fetchone()
        label += " : " + (row["name"] if row else params["slug"])
    if params.get("reboot"):
        label += " (avec redémarrage)"
    return label


def needs_update(dev, apps=None):
    return dev["state"] == "active" and bool(dev["updates"] or dev["security_updates"] or app_updates(dev, apps))


def pending(dev_id):
    return db().execute("SELECT * FROM device_commands WHERE device_id=? AND state IN ('queued', 'sent', 'running') "
                        "ORDER BY id", (dev_id,)).fetchall()


def queue(dev, action, params=None, user=None):
    """Ajoute une action (sans doublon d'une action identique déjà en attente) ; renvoie True si ajoutée."""
    params = json.dumps(params or {}, sort_keys=True)
    for c in pending(dev["id"]):
        if c["action"] == action and c["params"] == params:
            return False
    db().execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by) VALUES (?,?,?,?,?)",
                 (dev["id"], action, params, _now(), user if user is not None else g.user["username"]))
    if action == "uninstall":
        db().execute("UPDATE devices SET state='removing' WHERE id=?", (dev["id"],))
    return True


# --------------------------------------------------------------------------- API de l'agent (postes)

def _json():
    if (request.content_length or 0) > MAX_BODY:
        abort(413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _text(value, limit=200):
    return str(value or "").replace("\x00", "")[:limit]


def _device():
    """Poste authentifié par « Authorization: Bearer UID.JETON » (jamais par son adresse)."""
    auth = request.headers.get("Authorization", "")
    uid, _, token = auth.removeprefix("Bearer ").partition(".")
    if not UID_RE.match(uid) or not token:
        abort(401)
    dev = db().execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone()
    if dev is None or dev["state"] == "removed" or not hmac.compare_digest(dev["token_hash"], _hash(token)):
        abort(401)
    return dev


def _client_ip():
    ip = request.remote_addr or ""
    try:
        addr = ipaddress.ip_address(ip)
        return str(getattr(addr, "ipv4_mapped", None) or addr)
    except ValueError:
        return ""


def _identity(data):
    os_info = data.get("os") if isinstance(data.get("os"), dict) else {}
    family = os_info.get("family") if os_info.get("family") in FAMILIES else ""
    addresses = [a for a in (data.get("addresses") or []) if isinstance(a, str)][:16]
    return {"hostname": _text(data.get("hostname"), 100), "os_family": family,
            "os_name": _text(os_info.get("name"), 120), "agent_version": _text(data.get("agent_version"), 20),
            "addresses": " ".join(_text(a, 45) for a in addresses)}


@app.route("/agent/api/enroll", methods=["POST"])
def agent_enroll():
    data = _json()
    if not hmac.compare_digest(str(data.get("key", "")), enroll_key()):
        abort(403)
    uid = str(data.get("uid", ""))
    if not UID_RE.match(uid):
        abort(400)
    ident = _identity(data)
    if not ident["os_family"]:
        abort(400)
    token = secrets.token_urlsafe(32)
    conn = db()
    row = conn.execute("SELECT id FROM devices WHERE uid=?", (uid,)).fetchone()
    if row:
        # Réinscription (agent réinstallé) : nouveau jeton, historique conservé.
        conn.execute("UPDATE devices SET token_hash=?, state='active', last_seen=?, ip=?, hostname=?, os_family=?, "
                     "os_name=?, agent_version=?, addresses=? WHERE id=?",
                     (_hash(token), _now(), _client_ip(), ident["hostname"], ident["os_family"], ident["os_name"],
                      ident["agent_version"], ident["addresses"], row["id"]))
        conn.execute("UPDATE device_commands SET state='cancelled', finished_at=? WHERE device_id=? AND "
                     "action='uninstall' AND state IN ('queued', 'sent', 'running')", (_now(), row["id"]))
    else:
        conn.execute("INSERT INTO devices (uid, token_hash, enrolled_at, last_seen, ip, hostname, os_family, os_name, "
                     "agent_version, addresses) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (uid, _hash(token), _now(), _now(), _client_ip(), ident["hostname"], ident["os_family"],
                      ident["os_name"], ident["agent_version"], ident["addresses"]))
    conn.commit()
    audit("parc.inscription", f"{ident['hostname']} ({FAMILIES[ident['os_family']]}, {_client_ip()})", username="")
    return {"token": f"{uid}.{token}", "interval": CHECKIN}


def _store_inventory(dev, inv):
    updates = [u for u in (inv.get("updates") or []) if isinstance(u, dict)][:500]
    apps = [a for a in (inv.get("apps") or []) if isinstance(a, dict)][:2000]
    clean = {
        "updates": [{"name": _text(u.get("name")), "version": _text(u.get("version"), 80),
                     "security": bool(u.get("security"))} for u in updates],
        "apps": [{"name": _text(a.get("name")), "version": _text(a.get("version"), 80),
                  "publisher": _text(a.get("publisher"), 120)} for a in apps],
        "flatpak": int(inv.get("flatpak") or 0) if str(inv.get("flatpak") or 0).isdigit() else 0,
        "error": _text(inv.get("error"), 500),
    }
    total = len(clean["updates"]) + clean["flatpak"]
    db().execute("UPDATE devices SET inventory=?, last_scan=?, updates=?, security_updates=?, reboot_required=? "
                 "WHERE id=?", (json.dumps(clean), _now(), total, sum(u["security"] for u in clean["updates"]),
                                int(bool(inv.get("reboot_required"))), dev["id"]))


def _expire(dev_id):
    limit = (datetime.datetime.now() - datetime.timedelta(hours=SENT_TIMEOUT_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    db().execute("UPDATE device_commands SET state='failed', finished_at=?, output='Pas de réponse du poste.' "
                 "WHERE device_id=? AND state IN ('sent', 'running') AND sent_at < ?", (_now(), dev_id, limit))


def _payload(dev, cmd):
    """Action transmise à l'agent, avec ce qu'il lui faut (fichiers du catalogue vérifiés par SHA-256)."""
    params = json.loads(cmd["params"] or "{}")
    out = {"id": cmd["id"], "action": cmd["action"], "reboot": bool(params.get("reboot"))}
    if cmd["action"] == "update" and dev["os_family"] == "windows":
        out["apps"] = [app_payload(a) for a in app_updates(dev)]
    if cmd["action"] == "install-app":
        a = db().execute("SELECT * FROM win_apps WHERE slug=? AND status='ready'", (params.get("slug", ""),)).fetchone()
        if a is None:
            return None
        out["apps"] = [app_payload(a)]
    return out


@app.route("/agent/api/checkin", methods=["POST"])
def agent_checkin():
    dev = _device()
    data = _json()
    ident = _identity(data)
    conn = db()
    # Le poste a pu changer d'adresse : elle est simplement mise à jour.
    conn.execute("UPDATE devices SET last_seen=?, ip=?, hostname=COALESCE(NULLIF(?, ''), hostname), "
                 "os_name=COALESCE(NULLIF(?, ''), os_name), agent_version=?, addresses=? WHERE id=?",
                 (_now(), _client_ip(), ident["hostname"], ident["os_name"], ident["agent_version"],
                  ident["addresses"], dev["id"]))
    if isinstance(data.get("inventory"), dict):
        _store_inventory(dev, data["inventory"])
    _expire(dev["id"])
    dev = conn.execute("SELECT * FROM devices WHERE id=?", (dev["id"],)).fetchone()
    commands = []
    for c in conn.execute("SELECT * FROM device_commands WHERE device_id=? AND state='queued' ORDER BY id",
                          (dev["id"],)).fetchall():
        actor = conn.execute("SELECT role,disabled,must_change FROM users WHERE username=?", (c["created_by"],)).fetchone()
        grants = permissions.role_permissions(conn, actor["role"]) if actor and not actor["disabled"] and not actor["must_change"] else frozenset()
        required = permissions.ACTION_PERMISSIONS.get(c["action"])
        if required not in grants or (json.loads(c["params"] or "{}").get("reboot") and "fleet.reboot" not in grants):
            conn.execute("UPDATE device_commands SET state='cancelled',finished_at=?,output=? WHERE id=?",
                         (_now(), "Droits insuffisants avant transmission à l'agent.", c["id"]))
            if c["action"] == "uninstall":
                conn.execute("UPDATE devices SET state='active' WHERE id=? AND state='removing'", (dev["id"],))
            continue
        payload = _payload(dev, c)
        if payload is None:
            conn.execute("UPDATE device_commands SET state='failed', finished_at=?, output=? WHERE id=?",
                         (_now(), "Logiciel absent du catalogue.", c["id"]))
            continue
        conn.execute("UPDATE device_commands SET state='sent', sent_at=? WHERE id=?", (_now(), c["id"]))
        commands.append(payload)
        if c["action"] == "uninstall":
            break                   # rien après le retrait de l'agent
    conn.commit()
    answer = {"interval": CHECKIN, "scan_hours": SCAN_HOURS, "agent_version": config.VERSION, "commands": commands}
    color, reason = status(dev)
    answer["indicator"] = {"color": color, "reason": reason}
    if dev["os_family"] == "windows":
        path, sha = agent_msi()
        if path:
            answer["agent_msi"] = {"version": config.VERSION, "sha256": sha}
    return answer


@app.route("/agent/api/result", methods=["POST"])
def agent_result():
    dev = _device()
    data = _json()
    try:
        cmd_id = int(data.get("id"))
    except (TypeError, ValueError):
        abort(400)
    state = data.get("state")
    if state not in ("running", "success", "failed"):
        abort(400)
    conn = db()
    cmd = conn.execute("SELECT * FROM device_commands WHERE id=? AND device_id=?", (cmd_id, dev["id"])).fetchone()
    if cmd is None:
        abort(404)
    output = _text(data.get("output"), 65536)
    code = data.get("exit_code") if isinstance(data.get("exit_code"), int) else None
    if state == "running":
        conn.execute("UPDATE device_commands SET state='running' WHERE id=? AND state='sent'", (cmd_id,))
    else:
        conn.execute("UPDATE device_commands SET state=?, finished_at=?, output=?, exit_code=? WHERE id=?",
                     (state, _now(), output, code, cmd_id))
    if isinstance(data.get("inventory"), dict):
        _store_inventory(dev, data["inventory"])
    if cmd["action"] == "uninstall" and state == "success":
        conn.execute("UPDATE devices SET state='removed', token_hash='' WHERE id=?", (dev["id"],))
    conn.commit()
    dev = conn.execute("SELECT * FROM devices WHERE id=?", (dev["id"],)).fetchone()
    color, reason = status(dev)
    return {"ok": True, "indicator": {"color": color, "reason": reason}}


def _ca_pem():
    try:
        with open(os.path.join(config.TLS_DIR, "ca.crt")) as f:
            return f.read().strip()
    except OSError:
        return ""   # certificat fourni (--tls-cert) : magasin du système


def _agent_source(name):
    with open(os.path.join(AGENT_DIR, name), encoding="utf-8") as f:
        source = f.read().replace("@VERSION@", config.VERSION)
    if "@CONSOLE_LIB@" in source:
        # Un seul fichier autonome à installer/mettre à jour, avec les sources et licences embarquées.
        from pathlib import Path
        folder = Path(AGENT_DIR, "console")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    entry = zipfile.ZipInfo(path.relative_to(folder).as_posix())
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(entry, path.read_bytes())
        source = source.replace("@CONSOLE_LIB@", base64.b64encode(output.getvalue()).decode())
    return source


# Agent Windows : MSI joint à la release GitHub de cette version de loco (construit par la CI, windows/client/),
# ou déposé à côté du portail (serveur hors ligne, essais). Téléchargé à la première demande, empreinte vérifiée.
RELEASES = "https://github.com/ShaNouhAr/loco/releases/download"
_MSI_LOCK = threading.Lock()


def _msi_cache():
    return os.path.join(config.DATA, "agents", f"loco-client-{config.VERSION}.msi")


def agent_msi(fetch=False):
    """(chemin, sha256) du MSI de l'agent Windows de cette version, ou (None, raison)."""
    for path in (os.path.join(AGENT_DIR, "loco-client.msi"), _msi_cache()):
        if os.path.isfile(path):
            return path, _file_sha256(path)
    if not fetch:
        return None, "absent"
    with _MSI_LOCK:
        if os.path.isfile(_msi_cache()):
            return _msi_cache(), _file_sha256(_msi_cache())
        name = f"loco-client-{config.VERSION}.msi"
        try:
            sums = _get_url(f"{RELEASES}/v{config.VERSION}/SHA256SUMS", 65536).decode()
            expected = next((line.split()[0].lower() for line in sums.splitlines()
                             if len(line.split()) == 2 and line.split()[1].lstrip("*") == name), "")
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                return None, f"{name} absent de la version publiée {config.VERSION}"
            data = _get_url(f"{RELEASES}/v{config.VERSION}/{name}", 64 * 1024 * 1024)
        except OSError as e:
            return None, f"téléchargement depuis GitHub impossible : {e}"
        if hashlib.sha256(data).hexdigest() != expected:
            return None, "empreinte SHA-256 du MSI incorrecte"
        os.makedirs(os.path.dirname(_msi_cache()), exist_ok=True)
        tmp = _msi_cache() + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, _msi_cache())
        _SHA.pop(_msi_cache(), None)
        return _msi_cache(), expected


_SHA = {}


def _file_sha256(path):
    st = os.stat(path)
    key = (st.st_size, st.st_mtime_ns)
    if _SHA.get(path, (None,))[0] != key:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA[path] = (key, h.hexdigest())
    return _SHA[path][1]


def _get_url(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise OSError("fichier trop volumineux")
    return data


def _ca_fingerprint():
    pem = _ca_pem()
    if not pem:
        return ""
    body = "".join(line for line in pem.splitlines() if line and not line.startswith("-----"))
    return hashlib.sha256(base64.b64decode(body)).hexdigest()


def _check_key():
    if not hmac.compare_digest(request.args.get("key", ""), enroll_key()):
        abort(403)
    return request.args["key"]


@app.route("/agent/linux.sh")
def agent_linux_installer():
    key = _check_key()
    script = render_template("agent-linux.sh", server=config.HTTPS_URL, key=key, ca=_ca_pem(), version=config.VERSION,
                             agent=_agent_source("loco-client.py"))
    return Response(script, mimetype="text/x-shellscript")


@app.route("/agent/windows.ps1")
def agent_windows_installer():
    """Script d'amorçage : télécharge le MSI depuis loco (empreinte vérifiée) et l'installe avec les réglages."""
    key = _check_key()
    path, sha = agent_msi(fetch=True)
    if path is None:
        reason = sha.replace("'", "''")
        script = f"Write-Warning 'Agent Windows indisponible sur le serveur loco : {reason}.'\n"
    else:
        from .clients import client_base
        script = render_template("agent-windows.ps1", server=config.HTTPS_URL, key=key, ca_sha256=_ca_fingerprint(),
                                 version=config.VERSION, msi_url=client_base() + "/agent/files/loco-client.msi",
                                 msi_sha256=sha)
    return Response(script.replace("\r\n", "\n").replace("\n", "\r\n"), mimetype="text/plain")


@app.route("/agent/files/<name>")
def agent_file(name):
    """Agent des postes (installation et mise à jour automatique de l'agent) ; sans secret."""
    if name == "loco-client.py":
        return Response(_agent_source(name), mimetype="text/plain")
    if name == "loco-client.msi":
        path, _ = agent_msi(fetch=True)
        if path is None:
            abort(404)
        return send_file(path, mimetype="application/x-msi", as_attachment=True, download_name="loco-client.msi")
    abort(404)


# --------------------------------------------------------------------------- portail

def _rows(apps=None):
    apps = catalog() if apps is None else apps
    out = []
    for d in db().execute("SELECT * FROM devices WHERE state != 'removed' ORDER BY hostname COLLATE NOCASE"):
        color, reason = status(d, apps)
        out.append({"d": d, "color": color, "reason": reason, "apps": len(app_updates(d, apps)),
                    "pending": pending(d["id"]), "needs_update": needs_update(d, apps)})
    return out


def _commands(key):
    base, http = config.HTTPS_URL, config.HTTP_URL
    return {
        "windows": f"irm '{base}/agent/windows.ps1?key={key}' | iex",
        "windows_http": f"irm '{http}/agent/windows.ps1?key={key}' | iex" if http else "",
        "linux": f"curl -fsSL '{base}/agent/linux.sh?key={key}' | sudo sh",
        "linux_http": f"curl -fsSL '{http}/agent/linux.sh?key={key}' | sudo sh" if http else "",
    }


@app.route("/parc")
@login_required
def fleet():
    rows = _rows()
    counts = {c: sum(1 for r in rows if r["color"] == c) for c in ("green", "orange", "red", "grey")}
    signature = hashlib.sha256(json.dumps([[r["d"]["id"], r["d"]["last_seen"][:16], r["color"], r["reason"],
                                            len(r["pending"])] for r in rows]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    key = enroll_key() if can("fleet.enroll") else ""
    msi, msi_state = agent_msi()
    return render_template("fleet.html", rows=rows, counts=counts, signature=signature, families=FAMILIES,
                           actions=ACTIONS, to_update=sum(1 for r in rows if r["needs_update"]),
                           commands=_commands(key) if key else {}, checkin=CHECKIN, enroll_key=key,
                           ca_sha256=_ca_fingerprint() if key else "",
                           red_threshold=red_threshold(),
                           msi_error="" if msi else "téléchargé depuis GitHub à la première installation")


@app.route("/parc/seuil", methods=["POST"])
@login_required
def fleet_threshold():
    try:
        value = int(request.form.get("threshold", ""))
        if not 2 <= value <= 10000:
            raise ValueError
    except ValueError:
        flash("Le seuil rouge doit être compris entre 2 et 10000 mises à jour.", "error")
        return back("fleet")
    set_setting("fleet_red_threshold", str(value))
    audit("parc.seuil", f"rouge à partir de {value} mises à jour")
    flash(f"Pastille rouge à partir de {value} mises à jour (système et logiciels).", "ok")
    return back("fleet")


def _get(dev_id):
    dev = db().execute("SELECT * FROM devices WHERE id=? AND state != 'removed'", (dev_id,)).fetchone()
    if dev is None:
        abort(404)
    return dev


@app.route("/parc/<int:dev_id>")
@login_required
def fleet_device(dev_id):
    dev = _get(dev_id)
    inv = inventory(dev)
    color, reason = status(dev)
    history = db().execute("SELECT * FROM device_commands WHERE device_id=? ORDER BY id DESC LIMIT 30",
                           (dev_id,)).fetchall()
    signature = hashlib.sha256(json.dumps([dev["last_seen"][:16], dev["last_scan"],
                                           [(c["id"], c["state"]) for c in history]]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    apps = catalog_state(inv) if dev["os_family"] == "windows" else []
    return render_template("fleet_device.html", dev=dev, inv=inv, color=color, reason=reason, history=history,
                           remote_ready=version_key(dev["agent_version"]) >= (2, 4),
                           action_label=action_label,
                           apps=apps, families=FAMILIES, actions=ACTIONS, signature=signature,
                           needs_update=needs_update(dev), pending=pending(dev_id))


@app.route("/parc/<int:dev_id>/action", methods=["POST"])
@login_required
def fleet_action(dev_id):
    dev = _get(dev_id)
    action = request.form.get("action", "")
    if action not in permissions.ACTION_PERMISSIONS:
        abort(400)
    require(permissions.ACTION_PERMISSIONS[action])
    if request.form.get("reboot") == "1":
        require("fleet.reboot")
    if action == "forget":
        db().execute("DELETE FROM devices WHERE id=?", (dev_id,))
        db().commit()
        audit("parc.oubli", dev["hostname"])
        flash(f"{dev['hostname']} retiré de la liste (l'agent éventuel n'est plus accepté).", "ok")
        return back("fleet")
    if action not in ACTIONS or dev["state"] != "active":
        abort(400)
    params = {}
    if action == "update":
        params["reboot"] = request.form.get("reboot") == "1"
    if action == "install-app":
        slug = request.form.get("slug", "")
        if not config.SLUG_RE.match(slug) or dev["os_family"] != "windows" or not db().execute(
                "SELECT 1 FROM win_apps WHERE slug=? AND status='ready'", (slug,)).fetchone():
            abort(400)
        params["slug"] = slug
    if queue(dev, action, params):
        db().commit()
        audit(f"parc.{action}", f"{dev['hostname']}{' : ' + params['slug'] if 'slug' in params else ''}")
        flash(f"{ACTIONS[action]} demandée sur {dev['hostname']} : exécutée au prochain contact "
              f"(moins de {CHECKIN // 60} min).", "ok")
    else:
        flash("Action déjà en attente sur ce poste.", "error")
    return back("fleet_device", dev_id=dev_id)


@app.route("/parc/tout-mettre-a-jour", methods=["POST"])
@login_required
def fleet_update_all():
    apps = catalog()
    reboot = request.form.get("reboot") == "1"
    if reboot:
        require("fleet.reboot")
    count = 0
    for r in _rows(apps):
        if r["needs_update"] and queue(r["d"], "update", {"reboot": reboot}):
            count += 1
    db().commit()
    audit("parc.tout-mettre-a-jour", f"{count} poste(s){', avec redémarrage' if reboot else ''}")
    flash(f"Mise à jour demandée sur {count} poste(s)." if count else "Aucun poste à mettre à jour.", "ok")
    return back("fleet")


@app.route("/parc/cle", methods=["POST"])
@login_required
def fleet_rotate_key():
    set_setting("enroll_key", secrets.token_urlsafe(18))
    audit("parc.cle", "nouvelle clé d'inscription")
    flash("Nouvelle clé d'inscription : les postes déjà inscrits ne sont pas concernés.", "ok")
    return redirect(url_for("fleet"))
