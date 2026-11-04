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
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile

from flask import Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import addresses, agentpkg, app, config, features, permissions, scope, wake
from .i18n import L_, _, n_
from .auth import audit, can, login_required, require
from .db import db
from .util import back, date_fr, safe_next, counted

CHECKIN = 120                   # secondes entre deux contacts de l'agent
ONLINE_SECONDS = 3 * CHECKIN     # disponibilité : laisser passer trois cycles avant de déclarer le poste hors ligne
SCAN_HOURS = 6                  # inventaire complet des mises à jour (et après chaque action)
STALE_DAYS = 1                  # sans contact depuis : orange
SENT_TIMEOUT_HOURS = 6          # action transmise sans résultat : échec
MAX_BODY = 4 * 1024 * 1024
UID_RE = re.compile(r"^[0-9a-f]{32}$")
FAMILIES = {"windows": L_("Windows"), "debian": L_("Debian / Ubuntu"), "rhel": L_("Red Hat")}
ACTIONS = {"scan": L_("Inventaire"), "update": L_("Mise à jour"), "install-app": L_("Installation"), "install-package": L_("Installation de paquet"),
           "run-script": L_("Script"), "reboot": L_("Redémarrage"), "uninstall": L_("Retrait de l'agent"),
           "wake": L_("Réveil"), "wake-relay": L_("Envoi d'un réveil")}
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


# Réseaux privés laissés en accès direct (jamais via le proxy) : le proxy ne sert qu'à atteindre Internet (Windows Update).
_PROXY_BYPASS = ("<local>;10.*;192.168.*;169.254.*;"
                 + ";".join(f"172.{n}.*" for n in range(16, 32)))


def wu_proxy_directive():
    """Réglage proxy WinHTTP à pousser aux postes Windows (cache WU sans DNS), ou False pour le retirer.
    Actif par défaut ; coupé par la fonctionnalité « Proxy Windows Update » (ou le cache WU / le parc)."""
    if "wu_proxy" in features.disabled(db()):
        return False
    return {"host": config.HOST, "port": int(config.WU_PROXY_PORT), "bypass": config.HOST + ";" + _PROXY_BYPASS}


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


GROUP_KEY_RE = re.compile(r"g([1-9][0-9]{0,8})-([A-Za-z0-9_-]{16,64})")


def group_key(group_id):
    """Clé d'inscription d'un groupe de machines, « g<id>-<secret> » : un poste inscrit avec elle rejoint le groupe.
    Acceptée partout où la clé du serveur l'est (MSI, paquets .deb/.rpm, scripts) ; aucun agent à mettre à jour."""
    conn = db()
    if conn.execute("SELECT 1 FROM device_groups WHERE id=? AND enroll_key=''", (group_id,)).fetchone():
        conn.execute("UPDATE device_groups SET enroll_key=? WHERE id=? AND enroll_key=''", (secrets.token_urlsafe(18), group_id))
        conn.commit()
    row = conn.execute("SELECT enroll_key FROM device_groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        abort(404)
    return f"g{group_id}-{row['enroll_key']}"


def key_group(key):
    """Groupe désigné par une clé d'inscription : None pour la clé du serveur, l'identifiant du groupe pour la clé
    d'un groupe ; 403 pour toute autre clé (groupe supprimé ou clé renouvelée)."""
    key = str(key or "")
    if hmac.compare_digest(key.encode(), enroll_key().encode()):
        return None
    m = GROUP_KEY_RE.fullmatch(key)
    row = db().execute("SELECT id, enroll_key FROM device_groups WHERE id=?", (int(m.group(1)),)).fetchone() if m else None
    if row is None or not row["enroll_key"] or not hmac.compare_digest(row["enroll_key"].encode(), m.group(2).encode()):
        abort(403)
    return row["id"]


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
    for dev in db().execute("SELECT inventory FROM devices WHERE state='active' AND os_family='windows'" + scope.sql()):
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


def presence(dev):
    """Disponibilité d'après les contacts de l'agent, indépendante de son inventaire et des mises à jour."""
    seen = _ago(dev["last_seen"])
    online = (dev["state"] != "removed" and seen is not None
              and datetime.timedelta(0) <= seen <= datetime.timedelta(seconds=ONLINE_SECONDS))
    label = _("En ligne") if online else _("Hors ligne")
    detail = (_("Contact de l'agent dans les %(n)s dernières minutes", n=ONLINE_SECONDS // 60) if online
              else _("Aucun contact de l'agent depuis %(n)s minutes", n=ONLINE_SECONDS // 60)
              if seen is not None else _("Aucun contact de l'agent enregistré"))
    return {"online": online, "label": label, "detail": detail}


def record_contact(dev, conn):
    """Un échange valide prouve la présence même pendant une longue installation.
    Borner l'écriture au rythme habituel : le relais noVNC peut appeler cette fonction plusieurs fois par seconde.
    La transaction du point d'entrée reste responsable du commit."""
    age = _ago(dev["last_seen"])
    if age is None or age < datetime.timedelta(0) or age >= datetime.timedelta(seconds=CHECKIN):
        conn.execute("UPDATE devices SET last_seen=? WHERE id=? AND last_seen=?",
                     (_now(), dev["id"], dev["last_seen"]))


def status(dev, apps=None):
    """Voyant du volume de mises à jour : aucune / quelques-unes / au moins le seuil rouge.
    Une information de santé (sécurité, échec, redémarrage) ne change pas ce comptage."""
    if dev["state"] == "removing":
        return "grey", _("retrait de l'agent demandé")
    seen = _ago(dev["last_seen"])
    pending_apps = len(app_updates(dev, apps))
    reasons = []
    if dev["security_updates"]:
        reasons.append(n_("%(num)s mise à jour de sécurité en attente", "%(num)s mises à jour de sécurité en attente", dev["security_updates"]))
    other = dev["updates"] - dev["security_updates"]
    if other > 0:
        reasons.append(n_("%(num)s autre mise à jour du système", "%(num)s autres mises à jour du système", other) if dev["security_updates"]
                       else n_("%(num)s mise à jour du système", "%(num)s mises à jour du système", other))
    if pending_apps:
        reasons.append(n_("%(num)s logiciel à mettre à jour", "%(num)s logiciels à mettre à jour", pending_apps))
    if dev["reboot_required"]:
        reasons.append(_("redémarrage nécessaire"))
    if dev["blocked_updates"]:
        reasons.append(n_("%(num)s mise à jour bloquée", "%(num)s mises à jour bloquées", dev["blocked_updates"]))
    last = last_result(dev["id"])
    if last and last["state"] == "failed":
        reasons.append(_("échec : %(action)s", action=ACTIONS.get(last["action"], last["action"]).lower()))
    error = inventory(dev).get("error")
    if error:
        reasons.append(_("inventaire incomplet : %(error)s", error=error))
    warning = inventory(dev).get("warning")
    if warning:
        reasons.append(warning)     # un dépôt en panne parmi d'autres : le voyant reste celui du reste des mises à jour
    if seen is None or seen > datetime.timedelta(days=STALE_DAYS):
        reasons.append(_("poste sans contact récent"))
    if error or not dev["last_scan"] or seen is None or seen > datetime.timedelta(days=STALE_DAYS):
        return "grey", ", ".join(reasons) or _("inventaire en cours")
    total = dev["updates"] + pending_apps
    color = "green" if total == 0 else "red" if total >= red_threshold() else "orange"
    return color, ", ".join(reasons) or _("à jour")


def status_label(dev, color):
    """Libellé court du voyant (pastille de la fiche) ; le détail reste dans l'explication complète."""
    if color != "grey":
        return {"green": _("à jour"), "orange": _("mises à jour en attente"), "red": _("à traiter")}[color]
    seen = _ago(dev["last_seen"])
    if dev["state"] == "removing":
        return _("retrait demandé")
    if seen is None or seen > datetime.timedelta(days=STALE_DAYS):
        return _("sans contact récent")
    if inventory(dev).get("error"):
        return _("inventaire incomplet")
    return _("inventaire en cours")


def action_label(cmd):
    """« Installation : 7-Zip » plutôt que « Installation »."""
    label = ACTIONS.get(cmd["action"], cmd["action"])
    try:
        params = json.loads(cmd["params"] or "{}")
    except ValueError:
        params = {}
    if params.get("slug"):
        row = db().execute("SELECT name FROM win_apps WHERE slug=?", (params["slug"],)).fetchone()
        label = _("%(action)s : %(name)s", action=label, name=row["name"] if row else params["slug"])
    if params.get("app_snapshots"):
        names = [a.get("name", a.get("slug", "")) for a in params["app_snapshots"]]
        label = _("%(action)s : %(name)s", action=label, name=", ".join(names[:3]) + ("…" if len(names) > 3 else ""))
    only = params.get("only") if isinstance(params.get("only"), dict) else None
    if only:
        count = len(only.get("packages") or only.get("updates") or []) + (1 if only.get("flatpak") else 0)
        label = n_("%(action)s : %(num)s sélectionnée", "%(action)s : %(num)s sélectionnées", count, action=label)
    if params.get("reboot"):
        label = _("%(action)s (avec redémarrage)", action=label)
    return label


UPDATE_ID_RE = re.compile(r"[0-9A-Za-z-]{8,64}")       # identifiant Windows Update (GUID)
SELECT_UPDATES = (2, 120)                                # agents qui appliquent une sélection (les autres feraient tout)
CONSENT_VERSION = (2, 137)                               # agents qui savent demander l'accord avant le bureau distant


def selectable(dev):
    """Mises à jour du système choisissables une par une sur ce poste : agent assez récent et, sous Windows,
    inventaire avec les identifiants Windows Update."""
    if version_key(dev["agent_version"]) < SELECT_UPDATES:
        return False
    updates = inventory(dev).get("updates") or []
    return dev["os_family"] != "windows" or all(u.get("id") for u in updates)


def select_updates(dev, wanted, flatpak=False):
    """Mises à jour choisies une par une, limitées à celles que l'inventaire du poste annonce : paquets (Linux) ou
    identifiants Windows Update, désignés par identifiant, nom, ou numéro KB. ValueError si rien ne correspond."""
    if not selectable(dev):
        raise ValueError(_("Choisir les mises à jour une par une demande l'agent %(v)s.0 ou plus récent sur ce poste.",
                           v=".".join(map(str, SELECT_UPDATES))))
    from . import update_blocks
    updates = [u for u in update_blocks.annotate(db(), dev, inventory(dev).get("updates") or []) if not u.get("blocked")]
    wanted = [str(w).strip() for w in wanted if str(w).strip()]
    if dev["os_family"] == "windows":
        def matches(u, w):
            return w.lower() in (u.get("id", "").lower(), u.get("name", "").lower()) or w.upper() in u.get("version", "").split()
        chosen = [u["id"] for u in updates if u.get("id") and any(matches(u, w) for w in wanted)]
        only = {"updates": chosen}
    else:
        names = set(wanted)
        chosen = [u["name"] for u in updates if u.get("name") in names]
        only = {"packages": chosen, "flatpak": bool(flatpak) and bool(inventory(dev).get("flatpak"))}
    if not chosen and not only.get("flatpak"):
        raise ValueError(_("Aucune mise à jour en attente ne correspond à la sélection."))
    return only


def update_selection(dev, form):
    """Mises à jour cochées sur la fiche du poste (400 si aucune ne correspond à l'inventaire)."""
    try:
        return select_updates(dev, form.getlist("only"), form.get("only_flatpak") == "1")
    except ValueError as e:
        abort(400, str(e))


def needs_update(dev, apps=None):
    return dev["state"] == "active" and bool(dev["updates"] or dev["security_updates"] or app_updates(dev, apps))


def pending(dev_id):
    return db().execute("SELECT * FROM device_commands WHERE device_id=? AND state IN ('queued', 'sent', 'running') "
                        "ORDER BY id", (dev_id,)).fetchall()


def queue(dev, action, params=None, user=None, run_after=""):
    """Ajoute une action (sans doublon d'une action identique déjà en attente) ; renvoie True si ajoutée.
    run_after non vide = commande transmise seulement à partir de cet instant (planification ponctuelle)."""
    params = json.dumps(params or {}, sort_keys=True)
    for c in pending(dev["id"]):
        if c["action"] == action and c["params"] == params and c["run_after"] == run_after:
            return False
    db().execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by, run_after) "
                 "VALUES (?,?,?,?,?,?)",
                 (dev["id"], action, params, _now(), user if user is not None else g.user["username"], run_after))
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


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


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
            "addresses": " ".join(_text(a, 45) for a in addresses), "net_info": wake.clean_info(data)}


@app.route("/agent/api/enroll", methods=["POST"])
def agent_enroll():
    data = _json()
    group = key_group(data.get("key", ""))
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
        dev_id = row["id"]
    else:
        dev_id = conn.execute("INSERT INTO devices (uid, token_hash, enrolled_at, last_seen, ip, hostname, os_family, os_name, "
                              "agent_version, addresses) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (uid, _hash(token), _now(), _now(), _client_ip(), ident["hostname"], ident["os_family"],
                               ident["os_name"], ident["agent_version"], ident["addresses"])).lastrowid
    if ident["net_info"] is not None:
        conn.execute("UPDATE devices SET net_info=? WHERE id=?", (ident["net_info"], dev_id))
    joined = ""
    if group:                                   # clé d'un groupe : le poste le rejoint (ses autres groupes restent)
        conn.execute("INSERT OR IGNORE INTO device_group_members (group_id, device_id) VALUES (?,?)", (group, dev_id))
        joined = ", groupe " + conn.execute("SELECT name FROM device_groups WHERE id=?", (group,)).fetchone()[0]
    conn.commit()
    audit("parc.inscription", f"{ident['hostname']} ({FAMILIES[ident['os_family']]}, {_client_ip()}{joined})", username="")
    return {"token": f"{uid}.{token}", "interval": CHECKIN}


def _store_inventory(dev, inv):
    updates = [u for u in (inv.get("updates") or []) if isinstance(u, dict)][:500]
    apps = [a for a in (inv.get("apps") or []) if isinstance(a, dict)][:2000]
    clean = {
        "updates": [{"name": _text(u.get("name")), "version": _text(u.get("version"), 80),
                     "security": bool(u.get("security")), "driver": bool(u.get("driver")),
                     **({"id": _text(u.get("id"), 64)} if UPDATE_ID_RE.fullmatch(str(u.get("id") or "")) else {})}
                    for u in updates],
        "apps": [{"name": _text(a.get("name")), "version": _text(a.get("version"), 80),
                  "publisher": _text(a.get("publisher"), 120)} for a in apps],
        "flatpak": int(inv.get("flatpak") or 0) if str(inv.get("flatpak") or 0).isdigit() else 0,
        "error": _text(inv.get("error"), 500),
        "warning": _text(inv.get("warning"), 500),
    }
    from . import update_blocks
    total, security, blocked = update_blocks.counts(db(), dev, clean)
    db().execute("UPDATE devices SET inventory=?, last_scan=?, updates=?, security_updates=?, blocked_updates=?, reboot_required=? "
                 "WHERE id=?", (json.dumps(clean), _now(), total, security, blocked,
                                int(bool(inv.get("reboot_required"))), dev["id"]))
    _store_software(dev, inv)


def _store_software(dev, inv):
    """Inventaire détaillé des postes Linux (paquets, Flatpak, caractéristiques) rangé à part : la liste du parc relit
    l'inventaire de chaque appareil et n'a pas à porter des milliers de paquets. Les postes Windows n'en remontent pas."""
    packages = [p for p in (inv.get("packages") or []) if isinstance(p, dict)][:10000]
    flatpaks = [f for f in (inv.get("flatpaks") or []) if isinstance(f, dict)][:500]
    system = inv.get("system") if isinstance(inv.get("system"), dict) else {}
    if not packages and not flatpaks and not system:
        return
    clean = {
        "packages": [{"name": _text(p.get("name"), 120), "version": _text(p.get("version"), 80),
                      "arch": _text(p.get("arch"), 30), "manual": bool(p.get("manual")),
                      "size": _int(p.get("size"))} for p in packages],
        "flatpaks": [{"id": _text(f.get("id"), 200), "name": _text(f.get("name"), 120),
                      "version": _text(f.get("version"), 80), "branch": _text(f.get("branch"), 60),
                      "origin": _text(f.get("origin"), 60)} for f in flatpaks],
        "system": {
            "kernel": _text(system.get("kernel"), 120), "arch": _text(system.get("arch"), 30),
            "cpu": _text(system.get("cpu"), 120), "virtualization": _text(system.get("virtualization"), 40),
            "cores": _int(system.get("cores")), "memory": _int(system.get("memory")),
            "memory_available": _int(system.get("memory_available")), "disk": _int(system.get("disk")),
            "disk_free": _int(system.get("disk_free")), "boot": _int(system.get("boot")),
        },
    }
    db().execute("INSERT INTO device_software (device_id, data, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(device_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                 (dev["id"], json.dumps(clean), _now()))


def software(dev):
    row = db().execute("SELECT data FROM device_software WHERE device_id=?", (dev["id"],)).fetchone()
    try:
        data = json.loads(row["data"]) if row else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def expire_commands(conn, dev_id=None):
    """Commandes envoyées restées sans réponse. Le fil de fond s'en charge aussi : un poste mort ne reviendrait jamais les solder."""
    limit = (datetime.datetime.now() - datetime.timedelta(hours=SENT_TIMEOUT_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE device_commands SET state='failed', finished_at=?, output='Pas de réponse du poste.' "
                 "WHERE state IN ('sent', 'running') AND action != 'wake' AND sent_at < ?" + (" AND device_id=?" if dev_id else ""),
                 (_now(), limit, *([dev_id] if dev_id else [])))
    wake.expire(conn, dev_id)


def _payload(dev, cmd):
    """Action transmise à l'agent, avec ce qu'il lui faut (fichiers du catalogue vérifiés par SHA-256)."""
    params = json.loads(cmd["params"] or "{}")
    out = {"id": cmd["id"], "action": cmd["action"], "reboot": bool(params.get("reboot"))}
    if cmd["action"] == "update":
        from . import update_blocks                 # à l'envoi : un blocage posé depuis la demande s'applique
        out["blocks"] = update_blocks.patterns_for(db(), dev)
    if cmd["action"] == "update" and isinstance(params.get("only"), dict):
        out["only"] = update_blocks.trim(db(), dev, params["only"])   # mises à jour choisies une par une (agents 2.120.0 et plus)
    elif cmd["action"] == "update" and dev["os_family"] == "windows":
        out["apps"] = [app_payload(a) for a in app_updates(dev)]
    if cmd["action"] == "wake-relay":
        out["wake"] = {"broadcast": params.get("broadcast", ""), "macs": params.get("macs", [])}
    if cmd["action"] == "run-script":
        from . import scripts
        run = db().execute("SELECT * FROM script_runs WHERE id=?", (params.get("run_id"),)).fetchone()
        if run is None or not scripts.runs_on(run["platform"], dev["os_family"]):
            return None
        out["script"] = {"name": run["name"], "platform": run["platform"], "content": run["content"], "sha256": run["sha256"]}
        return out
    if cmd["action"] == "install-app":
        if dev["os_family"] != "windows":
            return None
        if params.get("app_snapshots"):
            from .deployments import _already_installed
            out["apps"] = [a for a in params["app_snapshots"] if params.get("force") or not _already_installed(dev, a)]
            return out
        a = db().execute("SELECT * FROM win_apps WHERE slug=? AND status='ready'", (params.get("slug", ""),)).fetchone()
        if a is None:
            return None
        out["apps"] = [app_payload(a)]
    if cmd["action"] == "install-package":
        if dev["os_family"] not in ("debian", "rhel"):
            return None
        packages = [p for p in params.get("packages", []) if isinstance(p, str) and p]
        if not packages:
            return None
        out["packages"] = packages
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
    if ident["net_info"] is not None:   # agents 2.134.0 et plus : cartes et réseaux, pour le réveil à distance
        conn.execute("UPDATE devices SET net_info=? WHERE id=?", (ident["net_info"], dev["id"]))
    wake.arrived(conn, dev["id"])
    if "servers" in data:           # agents 2.9.0 et plus : adresses du serveur qu'ils connaissent
        conn.execute("UPDATE devices SET servers=? WHERE id=?", (addresses.reported(data["servers"]), dev["id"]))
    reported_trust = data.get("trust") if isinstance(data.get("trust"), str) else None
    if reported_trust is not None:  # agents 2.84.0 et plus : empreinte du faisceau d'autorités épinglé
        conn.execute("UPDATE devices SET trust_sha256=? WHERE id=?", (reported_trust[:64].lower(), dev["id"]))
    if isinstance(data.get("inventory"), dict):
        _store_inventory(dev, data["inventory"])
    if isinstance(data.get("metrics"), dict):           # agents 2.71.0 et plus : supervision
        from . import monitoring
        monitoring.record(conn, dev["id"], data["metrics"])
    expire_commands(db(), dev["id"])
    dev = conn.execute("SELECT * FROM devices WHERE id=?", (dev["id"],)).fetchone()
    from . import schedules
    schedules.materialize(conn)       # planifications dues : commandes créées, occurrence suivante
    from . import update_blocks
    commands = []
    for c in conn.execute("SELECT * FROM device_commands WHERE device_id=? AND state='queued' "
                          "AND (run_after='' OR run_after<=?) ORDER BY id", (dev["id"], _now())).fetchall():
        if (c["action"] == "install-app" and "windows_apps" in features.disabled(conn)
                or c["action"] == "run-script" and "scripts" in features.disabled(conn)):
            continue                # fonctionnalité coupée : la commande attend sa réactivation
        actor = conn.execute("SELECT role,disabled,must_change,pending FROM users WHERE username=?", (c["created_by"],)).fetchone()
        grants = permissions.role_permissions(conn, actor["role"]) if actor and not actor["disabled"] and not actor["pending"] and not actor["must_change"] else frozenset()
        required = permissions.ACTION_PERMISSIONS.get(c["action"])
        params = json.loads(c["params"] or "{}")
        if (required not in grants or params.get("reboot") and "fleet.reboot" not in grants
                or params.get("bulk_install") and "fleet.bulk_install" not in grants):
            conn.execute("UPDATE device_commands SET state='cancelled',finished_at=?,output=? WHERE id=?",
                         (_now(), "Droits insuffisants avant transmission à l'agent.", c["id"]))
            if c["action"] == "uninstall":
                conn.execute("UPDATE devices SET state='active' WHERE id=? AND state='removing'", (dev["id"],))
            continue
        if c["action"] == "update" and version_key(dev["agent_version"]) < update_blocks.AGENT_VERSION and update_blocks.for_device(conn, dev):
            continue                # agent trop ancien pour respecter un blocage : il se met à jour seul, la commande l'attend
        payload = _payload(dev, c)
        if payload is None:
            conn.execute("UPDATE device_commands SET state='failed', finished_at=?, output=? WHERE id=?",
                         (_now(), "Script indisponible ou incompatible avec ce système." if c["action"] == "run-script"
                          else "Logiciel absent du catalogue.", c["id"]))
            continue
        conn.execute("UPDATE device_commands SET state='sent', sent_at=? WHERE id=?", (_now(), c["id"]))
        commands.append(payload)
        if c["action"] == "uninstall":
            break                   # rien après le retrait de l'agent
    conn.commit()
    answer = {"interval": CHECKIN, "scan_hours": SCAN_HOURS, "agent_version": config.VERSION, "commands": commands,
              "servers": addresses.servers(conn)}
    bundle = trust_pem()
    if bundle:
        # Faisceau d'autorités (loco + autorité d'entreprise éventuelle) livré par ce canal déjà épinglé, seulement à
        # l'agent qui ne l'a pas encore : il pourra suivre une bascule d'autorité sans perdre la connexion.
        sha = hashlib.sha256(bundle.encode()).hexdigest()
        answer["trust"] = {"sha256": sha, **({"pem": bundle} if reported_trust != sha else {})}
    from . import logforward
    forwarding = logforward.directive(conn, dev)
    if forwarding:
        answer["syslog"] = forwarding   # agents 2.124.0 et plus : remonter les journaux du poste
    if dev["wake_policy"]:
        answer["wake"] = {"enabled": dev["wake_policy"] == "on"}   # agents 2.135.0 et plus
    answer["update_blocks"] = {"patterns": update_blocks.patterns_for(conn, dev)}   # agents 2.137.0 et plus
    color, reason = status(dev)
    answer["indicator"] = {"color": color, "reason": reason}
    if dev["os_family"] == "windows":
        answer["wu_proxy"] = wu_proxy_directive()
        answer["windows_update"] = {"blocked": bool(dev["block_windows_update"])}
        path, _raw = agent_msi()
        data, sha = canonical_msi() if path else (None, "")
        if data:
            # Empreinte du MSI réellement servi par /agent/files/loco-client.msi (emplacements d'inscription vidés),
            # pas celle du fichier brut : sinon l'agent refuse chaque mise à jour (« empreinte du MSI incorrecte »).
            answer["agent_msi"] = {"version": config.VERSION, "sha256": sha}
        elif version_key(dev["agent_version"]) < version_key(config.VERSION):
            prefetch_msi()          # proposé au contact suivant, sans faire attendre l'agent
    return answer


PROGRESS_STEPS = {"download": L_("Téléchargement"), "verify": L_("Vérification de l'empreinte"), "install": L_("Installation"),
                  "windows_update": L_("Mises à jour Windows"), "updates": L_("Mises à jour du système"),
                  "flatpak": L_("Applications Flatpak")}
# Phases d'une mise à jour du système (étape « updates ») : préparation, téléchargement puis installation.
UPDATE_PHASES = {"prepare": L_("Préparation"), "download": L_("Téléchargement"), "install": L_("Installation")}


def _progress(value):
    """Étape remontée par l'agent, bornée : {step, phase, app, index, total, percent}."""
    if not isinstance(value, dict) or value.get("step") not in PROGRESS_STEPS:
        return None
    out = {"step": value["step"], "app": _text(value.get("app"), 100)}
    if value.get("phase") in UPDATE_PHASES:
        out["phase"] = value["phase"]
    for key, low, high in (("index", 1, 5000), ("total", 1, 5000), ("percent", 0, 100)):
        number = value.get(key)
        if isinstance(number, int) and not isinstance(number, bool) and low <= number <= high:
            out[key] = number
    items = value.get("items")
    if isinstance(items, list) and items:
        clean = [{"name": _text(it.get("name"), 120), "state": it["state"]}
                 for it in items[:120] if isinstance(it, dict) and it.get("state") in ("running", "ok", "failed") and it.get("name")]
        if clean:
            out["items"] = clean
    counts = value.get("counts")
    if isinstance(counts, dict):
        out["counts"] = {k: counts[k] for k in ("ok", "failed", "running", "total")
                         if isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool) and 0 <= counts[k] <= 100000}
    return out


def progress_view(raw):
    """Étape lisible et part accomplie (0-100) d'une commande en cours, d'après la dernière remontée de l'agent."""
    try:
        p = json.loads(raw) if raw else None
    except ValueError:
        p = None
    if not isinstance(p, dict) or p.get("step") not in PROGRESS_STEPS:
        return None
    extra = {k: p[k] for k in ("items", "counts") if k in p}      # liste des paquets et compteurs (affichage en direct)
    total, index = p.get("total") or 1, min(p.get("index") or 1, p.get("total") or 1)
    if p["step"] in ("updates", "flatpak"):
        # Mises à jour du système : pourcentage de l'agent s'il le connaît, sinon la part des éléments traités.
        phase = p.get("phase", "install")
        percent = p["percent"] if "percent" in p else round(100 * (index - 1) / total)
        label = str(UPDATE_PHASES[phase]) if p["step"] == "updates" else str(PROGRESS_STEPS["flatpak"])
        if p.get("app"):
            label += f" · {p['app']}"
        if total > 1:
            label += f" · {index}/{total}"
        return {"label": label, "percent": max(1, min(99, percent)), "step": p["step"], "app": p.get("app", ""),
                "phase": phase, **extra}
    # Part de chaque logiciel : téléchargement 0-60 %, vérification 60-70 %, installation 70-100 %.
    within = {"download": 0.6 * (p.get("percent") or 0) / 100, "verify": 0.6, "install": 0.7, "windows_update": 0.0}[p["step"]]
    overall = round(100 * ((index - 1) + within) / total)
    label = PROGRESS_STEPS[p["step"]] + (f" de {p['app']}" if p.get("app") else "")
    if p["step"] == "download" and "percent" in p:
        label += f" · {p['percent']} %"
    if total > 1 and p["step"] != "windows_update":
        label += f" · logiciel {index}/{total}"
    return {"label": label, "percent": max(1, min(99, overall)), "step": p["step"]}


app.jinja_env.globals["progress_view"] = progress_view

# Déroulé global des mises à jour du parc : les mises à jour et les redémarrages qui les accompagnent.
ACTIVITY_FINISHED_HOURS = 24    # ensuite, seulement dans l'historique du poste
ACTIVITY_FINISHED_LIMIT = 100
FAILURE_LINE_RE = re.compile(r"^\s*(E:|Err:|dpkg: error|error\b|Error\b|ÉCHEC|FAILED|FALLO)")


def _activity_rows():
    """Commandes suivies par le déroulé, dans le parc du compte : (actives, terminées des dernières 24 h)."""
    base = ("SELECT c.*, d.hostname, d.os_family, d.os_name, d.state AS device_state, d.last_seen "
            "FROM device_commands c JOIN devices d ON d.id = c.device_id "
            "WHERE c.action IN ('update', 'reboot') AND d.state != 'removed'" + scope.sql("d.id"))
    since = (datetime.datetime.now() - datetime.timedelta(hours=ACTIVITY_FINISHED_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    active = db().execute(base + " AND c.state IN ('queued', 'sent', 'running') ORDER BY c.id").fetchall()
    finished = db().execute(base + " AND c.state IN ('success', 'failed') AND c.finished_at >= ? "
                            "ORDER BY c.finished_at DESC, c.id DESC LIMIT ?", (since, ACTIVITY_FINISHED_LIMIT)).fetchall()
    return active, finished


def _command_online(c):
    return presence({"last_seen": c["last_seen"], "state": c["device_state"]})["online"]


def _activity_signature(active, finished):
    """Change quand une ligne change d'état ou qu'un poste en attente apparaît ou disparaît, et toutes les 5 min pour
    garder justes les « il y a … » ; pas à chaque paquet (les barres et les listes sont réécrites en place)."""
    return hashlib.sha256(json.dumps([[[c["id"], c["state"], _command_online(c)] for c in active],
                                      [[c["id"], c["state"]] for c in finished],
                                      int(time.time() // 300)]).encode()).hexdigest()[:16]


def _failure_reason(view, output):
    """Pourquoi une commande a échoué : les éléments en échec, sinon la ligne d'erreur du journal."""
    failed = [i["name"] for i in (view or {}).get("items", []) if i["state"] == "failed"]
    if failed:
        return ", ".join(failed[:3]) + ("…" if len(failed) > 3 else "")
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    line = next((line for line in reversed(lines) if FAILURE_LINE_RE.match(line)), lines[-1] if lines else "")
    return re.sub(r"^(ÉCHEC|FAILED|FALLO)\s*", "", line)[:160]         # la ligne affiche déjà « Échec »


def _success_summary(c, view):
    """Ce qu'une commande réussie a fait ; rien plutôt que la dernière ligne du journal, souvent sans rapport."""
    if c["action"] == "reboot":
        return (c["output"] or "").strip()[:160]
    n = ((view or {}).get("counts") or {}).get("ok", 0)
    if not n:
        return ""
    if c["os_family"] == "windows":
        return _("%(n)s mise à jour installée", n=n) if n == 1 else _("%(n)s mises à jour installées", n=n)
    return _("%(n)s paquet installé", n=n) if n == 1 else _("%(n)s paquets installés", n=n)


def update_activity(active, finished):
    """Lignes du déroulé : en cours (avancement), en attente (ce qu'elles attendent vraiment), terminées (échecs d'abord)."""
    running, waiting, done = [], [], []
    now = _now()

    def line(c):
        return {"id": c["id"], "dev_id": c["device_id"], "hostname": c["hostname"] or str(c["device_id"]),
                "os_short": short_os(c["os_name"]), "action": c["action"], "label": action_label(c), "state": c["state"],
                "by": c["created_by"]}
    for c in active:
        item, online = line(c), _command_online(c)
        item["online"] = online
        if c["state"] == "running":
            item["progress"] = progress_view(c["progress"]) if c["action"] == "update" else None
            item["since"] = ago_text(c["sent_at"] or c["created_at"])
            item["seen"] = ago_text(c["last_seen"])
            running.append(item)
            continue
        item["since"] = ago_text(c["created_at"])
        if c["run_after"] and c["run_after"] > now:
            item.update(tone="", status=_("Planifiée"), detail=_("le %(date)s", date=date_fr(c["run_after"][:16])))
        elif not online and c["state"] == "sent":       # déjà remise à l'agent : plus rien à attendre de ce côté
            item.update(tone="warn", status=_("Sans réponse du poste"), detail=_("vu %(seen)s", seen=ago_text(c["last_seen"])))
        elif not online:
            item.update(tone="", status=_("Poste hors ligne"),
                        detail=_("vu %(seen)s · partira à son retour", seen=ago_text(c["last_seen"])))
        elif c["state"] == "sent":
            item.update(tone="warn", status=_("Transmise à l'agent"), detail=_("démarrage imminent"))
        else:
            item.update(tone="warn", status=_("Au prochain contact"), detail=_("moins de %(v)s min", v=CHECKIN // 60))
        waiting.append(item)
    waiting.sort(key=lambda r: not r["online"])         # d'abord ce qui va partir
    for c in finished:
        item, view = line(c), progress_view(c["progress"]) if c["action"] == "update" else None
        item.update(ok=c["state"] == "success", view=view, since=ago_text(c["finished_at"]))
        item["detail"] = _success_summary(c, view) if item["ok"] else _failure_reason(view, c["output"])
        done.append(item)
    done.sort(key=lambda r: r["ok"])                    # tri stable : les échecs d'abord, puis du plus récent au plus ancien
    return {"running": running, "waiting": waiting, "done": done,
            "offline": sum(1 for r in waiting if not r["online"]), "failures": sum(1 for r in done if not r["ok"])}


def updating_count():
    """Pastille de navigation : ce qui bouge vraiment, sans les postes hors ligne ni les planifications à venir."""
    now = datetime.datetime.now()
    alive = (now - datetime.timedelta(seconds=ONLINE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        return db().execute("SELECT COUNT(*) FROM device_commands c JOIN devices d ON d.id = c.device_id "
                            "WHERE c.action IN ('update', 'reboot') AND c.state IN ('queued', 'sent', 'running') "
                            "AND d.state != 'removed' AND d.last_seen >= ? AND (c.run_after = '' OR c.run_after <= ?)"
                            + scope.sql("d.id"), (alive, _now())).fetchone()[0]
    except Exception:  # noqa: BLE001 - une pastille de navigation ne doit jamais faire tomber une page
        return 0


def failed_updates_count():
    """Postes dont une mise à jour a échoué depuis 24 h, sans réussite depuis."""
    since = (datetime.datetime.now() - datetime.timedelta(hours=ACTIVITY_FINISHED_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    return db().execute("SELECT COUNT(DISTINCT c.device_id) FROM device_commands c JOIN devices d ON d.id = c.device_id "
                        "WHERE c.action = 'update' AND c.state = 'failed' AND c.finished_at >= ? AND d.state != 'removed' "
                        "AND NOT EXISTS (SELECT 1 FROM device_commands n WHERE n.device_id = c.device_id "
                        "AND n.action = 'update' AND n.state = 'success' AND n.id > c.id)" + scope.sql("d.id"),
                        (since,)).fetchone()[0]


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
        progress = _progress(data.get("progress"))
        if progress:
            conn.execute("UPDATE device_commands SET progress=? WHERE id=? AND state='running'", (json.dumps(progress), cmd_id))
    else:
        conn.execute("UPDATE device_commands SET state=?, finished_at=?, output=?, exit_code=? WHERE id=?",
                     (state, _now(), output, code, cmd_id))
    if isinstance(data.get("inventory"), dict):
        _store_inventory(dev, data["inventory"])
    if cmd["action"] == "uninstall" and state == "success":
        conn.execute("UPDATE devices SET state='removed', token_hash='' WHERE id=?", (dev["id"],))
    if state in ("success", "failed"):
        from .deployments import cleanup_artifacts
        cleanup_artifacts(conn)
    record_contact(dev, conn)
    conn.commit()
    dev = conn.execute("SELECT * FROM devices WHERE id=?", (dev["id"],)).fetchone()
    color, reason = status(dev)
    return {"ok": True, "indicator": {"color": color, "reason": reason}}


PEM_CERT_RE = re.compile(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)


def trust_pem():
    """Faisceau d'autorités que les agents épinglent : l'autorité loco en tête, puis l'autorité d'entreprise choisie
    (trust.pem, tenu à jour par l'agent root) ; sinon l'autorité loco seule. Vide : certificat fourni à l'installation
    (--tls-cert), les agents s'en remettent au magasin du système."""
    for name in ("trust.pem", "ca.crt"):
        try:
            with open(os.path.join(config.TLS_DIR, name)) as f:
                text = f.read()
        except OSError:
            continue
        if PEM_CERT_RE.search(text):
            return text
    return ""


def _ca_pem():
    return trust_pem().strip()


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


_PREFETCH = {"last": 0.0}


def prefetch_msi():
    """Télécharge en arrière-plan le MSI de cette version (au plus une tentative toutes les dix minutes).
    Sans lui, les agents Windows plus anciens ne se voient proposer aucune mise à jour après celle du serveur."""
    if time.time() - _PREFETCH["last"] < 600:
        return
    _PREFETCH["last"] = time.time()
    threading.Thread(target=agent_msi, kwargs={"fetch": True}, name="loco-msi", daemon=True).start()


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
        # Nom temporaire unique : plusieurs processus du portail peuvent télécharger en même temps.
        fd, tmp = tempfile.mkstemp(prefix=".msi-", dir=os.path.dirname(_msi_cache()))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, _msi_cache())
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        _SHA.pop(_msi_cache(), None)
        return _msi_cache(), expected


# Emplacements gravés dans le MSI (mêmes chaînes et longueurs que windows/client/loco-client.wxs).
_MSI_FIELDS = (
    (b"@LOCO_PROVISION_SERVER@" + b"." * 137, "server"),
    (b"@LOCO_PROVISION_KEY@" + b"." * 76, "key"),
    (b"@LOCO_PROVISION_CA@" + b"." * 109, "ca"),
)


def _stamp_msi(data, server="", key="", ca=""):
    """Grave l'adresse, la clé d'inscription et l'empreinte de l'autorité dans les emplacements du MSI (complétés
    d'espaces, que l'agent rogne). Valeurs vides = emplacements vidés (MSI générique pour ps1/GPO). Un MSI sans
    emplacement (ancienne version) est renvoyé inchangé."""
    values = {"server": server, "key": key, "ca": ca}
    out = bytearray(data)
    for placeholder, field in _MSI_FIELDS:
        encoded = values[field].encode()
        if len(encoded) > len(placeholder):
            raise ValueError(f"{field} trop long pour le MSI")
        index = out.find(placeholder)
        if index >= 0:
            out[index:index + len(placeholder)] = encoded.ljust(len(placeholder), b" ")
    return bytes(out)


_CANON = {}


def canonical_msi():
    """MSI servi par défaut (ps1, GPO) : emplacements d'inscription vidés. Déterministe → empreinte SHA-256 stable."""
    path, reason = agent_msi(fetch=True)
    if path is None:
        return None, reason
    raw = _file_sha256(path)
    if _CANON.get("raw") != raw:
        with open(path, "rb") as f:
            data = _stamp_msi(f.read())
        _CANON.clear()
        _CANON.update(raw=raw, data=data, sha=hashlib.sha256(data).hexdigest())
    return _CANON["data"], _CANON["sha"]


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
    """Empreinte épinglée à l'installation des agents : celle de l'autorité loco, en tête du faisceau."""
    m = PEM_CERT_RE.search(_ca_pem())
    return hashlib.sha256(base64.b64decode("".join(m.group(1).split()))).hexdigest() if m else ""


def _check_key():
    """Clé du serveur ou d'un groupe (403 sinon), reprise telle quelle dans le script ou le paquet servi."""
    key_group(request.args.get("key", ""))
    return request.args["key"]


@app.route("/agent/linux.sh")
def agent_linux_installer():
    return Response(_linux_script(_check_key()), mimetype="text/x-shellscript")


def _linux_script(key):
    """Script d'installation de l'agent Linux pour ce serveur : agent, autorité épinglée, adresse et clé."""
    return render_template("agent-linux.sh", server=config.HTTPS_URL, key=key, ca=_ca_pem(), version=config.VERSION,
                           agent=_agent_source("loco-client.py"))


def repos_available(kind):
    """Le paquet « agent et dépôts » suppose la fonctionnalité correspondante active sur ce serveur."""
    return features.enabled("apt") if kind == "deb" else config.RPM_ENABLED and features.enabled("rpm")


def _repos_script(kind):
    """Configuration des dépôts embarquée dans le paquet : adresse HTTPS, le paquet confiant l'autorité au système."""
    if not repos_available(kind):
        abort(404)
    if kind == "deb":
        return render_template("setup.sh", base=config.HTTPS_URL, https_url=config.HTTPS_URL,
                               host=config.HOST, acng_port=config.ACNG_PORT)
    return render_template("rpm-setup.sh", base=config.HTTPS_URL, https_url=config.HTTPS_URL)


def _agent_package(kind):
    key = _check_key()
    repos = _repos_script(kind) if request.args.get("depots") == "1" else None
    build, name, mime = {"deb": (agentpkg.build_deb, agentpkg.deb_name, "application/vnd.debian.binary-package"),
                         "rpm": (agentpkg.build_rpm, agentpkg.rpm_name, "application/x-rpm")}[kind]
    data = build(_linux_script(key), config.VERSION, config.HTTPS_URL, repos=repos, ca=_ca_pem() if repos else "")
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name(config.VERSION, bool(repos))}"'})


@app.route("/agent/loco-client.deb")
def agent_deb():
    """Paquet Debian / Ubuntu de l'agent (clé d'inscription incluse) ; avec depots=1, configure aussi APT."""
    return _agent_package("deb")


@app.route("/agent/loco-client.rpm")
def agent_rpm():
    """Paquet Rocky Linux / AlmaLinux / RHEL de l'agent ; avec depots=1, configure aussi DNF."""
    return _agent_package("rpm")


@app.route("/agent/windows.ps1")
def agent_windows_installer():
    """Script d'amorçage : télécharge le MSI depuis loco (empreinte vérifiée) et l'installe avec les réglages."""
    key = _check_key()
    data, sha = canonical_msi()
    if data is None:
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
        data, reason = canonical_msi()
        if data is None:
            # Indisponible pour l'instant (GitHub injoignable…) : le dire au lieu d'un 404 muet.
            current_app.logger.warning("MSI de l'agent %s indisponible : %s", config.VERSION, reason)
            return Response(f"loco-client.msi {config.VERSION} indisponible : {reason}. Réessayez dans un instant.\n",
                            status=503, mimetype="text/plain", headers={"Retry-After": "30"})
        return Response(data, mimetype="application/x-msi",
                        headers={"Content-Disposition": "attachment; filename=loco-client.msi"})
    abort(404)


@app.route("/agent/windows/enroll.msi")
@login_required
def agent_enroll_msi():
    """MSI prêt pour ce serveur : un double-clic installe le service ET inscrit le poste (adresse, clé et empreinte
    de l'autorité gravées dans le MSI). Réservé aux comptes autorisés à enrôler (voir permissions.ENDPOINTS).
    Avec « groupe », la clé de ce groupe : le poste le rejoint à l'inscription."""
    group = enrollment_group(request.args.get("groupe", ""))
    path, reason = agent_msi(fetch=True)
    if path is None:
        current_app.logger.warning("MSI de l'agent %s indisponible : %s", config.VERSION, reason)
        return Response(f"loco-client.msi {config.VERSION} indisponible : {reason}. Réessayez dans un instant.\n",
                        status=503, mimetype="text/plain", headers={"Retry-After": "30"})
    with open(path, "rb") as f:
        data = _stamp_msi(f.read(), server=config.HTTPS_URL, key=group_key(group["id"]) if group else enroll_key(),
                          ca=_ca_fingerprint())
    return Response(data, mimetype="application/x-msi",
                    headers={"Content-Disposition": "attachment; filename=loco-client.msi"})


def enrollment_groups():
    """Groupes où le compte peut inscrire des postes : tous pour tout le parc ; sinon ceux attribués à son profil
    (un poste inscrit ailleurs, ou sans groupe, sortirait de son parc)."""
    from . import groups
    rows = groups.listing("device")
    if scope.whole():
        return rows
    _whole, declared, _devices = scope.declared(db(), g.user["role"])
    return [r for r in rows if r["id"] in declared]


def enrollment_group(value):
    """Groupe d'inscription (paramètre « groupe ») : sans choix, aucun pour tout le parc et le premier de ses groupes
    pour un profil limité ; 404 pour un groupe hors de sa portée, 403 si un profil limité n'a aucun groupe."""
    choices = enrollment_groups()
    if value:
        group = next((c for c in choices if str(c["id"]) == str(value)), None)
        if group is None:
            abort(404)
        return group
    if scope.whole():
        return None
    if not choices:
        abort(403)
    return choices[0]


@app.route("/fleet/groups/<int:group_id>/enroll-key", methods=["POST"])
@login_required
def fleet_group_rotate_key(group_id):
    """Nouvelle clé pour un groupe : MSI, paquets et commandes déjà distribués pour ce groupe cessent d'inscrire."""
    conn = db()
    row = conn.execute("SELECT name FROM device_groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        abort(404)
    conn.execute("UPDATE device_groups SET enroll_key=? WHERE id=?", (secrets.token_urlsafe(18), group_id))
    audit("parc.cle-groupe", row["name"], commit=False)
    conn.commit()
    flash(_("Nouvelle clé d'inscription pour le groupe %(name)s : les fichiers et commandes précédents de ce groupe "
            "n'inscrivent plus de poste.", name=row["name"]), "ok")
    return redirect(url_for("enrollment", groupe=group_id))


# --------------------------------------------------------------------------- portail

def _rows(apps=None):
    apps = catalog() if apps is None else apps
    out = []
    for d in db().execute("SELECT * FROM devices WHERE state != 'removed'" + scope.sql() + " ORDER BY hostname COLLATE NOCASE"):
        color, reason = status(d, apps)
        cmds = pending(d["id"])
        out.append({"d": d, "color": color, "reason": reason, "apps": len(app_updates(d, apps)),
                    "pending": cmds, "needs_update": needs_update(d, apps),
                    # Redémarrage demandé (bouton ou mise à jour avec redémarrage) et pas encore effectué.
                    "rebooting": any(c["action"] == "reboot" or (c["action"] == "update" and '"reboot": true' in (c["params"] or "")) for c in cmds)})
    return out


def _commands(key):
    base, http = config.HTTPS_URL, config.HTTP_URL
    ca = _ca_fingerprint()
    commands = {
        "windows": f"irm '{base}/agent/windows.ps1?key={key}' | iex",
        "windows_http": f"irm '{http}/agent/windows.ps1?key={key}' | iex" if http else "",
        # MSI téléchargé à la main : sans ces paramètres, il installe le service sans l'inscrire.
        "msiexec": (f'msiexec /i "%USERPROFILE%\\Downloads\\loco-client.msi" SERVER={base} KEY={key}'
                    + (f" CA_SHA256={ca}" if ca else "") + " /qb"),
        "msi_urls": [u + "/agent/files/loco-client.msi" for u in (base, http) if u],
        "linux": f"curl -fsSL '{base}/agent/linux.sh?key={key}' | sudo sh",
        "linux_http": f"curl -fsSL '{http}/agent/linux.sh?key={key}' | sudo sh" if http else "",
    }
    # Paquets de l'agent, seuls ou avec les dépôts (« _full ») : téléchargés depuis le portail puis copiés sur le
    # poste, ou récupérés directement par le poste.
    for kind, tool, name in (("deb", "apt", agentpkg.deb_name), ("rpm", "dnf", agentpkg.rpm_name)):
        for full in (False, True):
            tag, file = kind + ("_full" if full else ""), name(config.VERSION, full)
            path = f"/agent/loco-client.{kind}?key={key}" + ("&depots=1" if full else "")
            commands.update({f"{tag}_path": path, f"{tag}_install": f"sudo {tool} install ./{file}",
                             f"{tag}_fetch": f"curl -fsSLo {file} '{base}{path}' && sudo {tool} install ./{file}",
                             f"{tag}_fetch_http": f"curl -fsSLo {file} '{http}{path}' && sudo {tool} install ./{file}"
                             if http else ""})
    return commands


SEVERITY = {"red": 0, "orange": 1, "grey": 2, "green": 3}
PAGE_SIZE = 50


def main_address(dev):
    """Adresse à afficher : celle par laquelle loco voit le poste s'il la déclare, sinon la première déclarée.
    L'ordre seul ne suffit pas : le pont d'un poste qui a Docker peut passer devant sa carte réseau."""
    own = (dev["addresses"] or "").split()
    return dev["ip"] if dev["ip"] in own else own[0] if own else dev["ip"] or ""


def short_os(name):
    """Système sans l'éditeur ni le numéro de build, pour la liste du parc : « Windows Server 2025 Standard »,
    « Debian 13 (trixie) » (le nom complet reste en infobulle)."""
    name = re.sub(r"^Microsoft\s+", "", name or "")
    name = re.sub(r"\s*\(\d+(\.\d+){1,3}\)$", "", name)
    return name.replace(" GNU/Linux", "").strip()


def _group_names():
    names = {}
    for r in db().execute("SELECT m.device_id, g.id, g.name FROM device_group_members m JOIN device_groups g ON g.id=m.group_id "
                          "ORDER BY g.name COLLATE NOCASE"):
        names.setdefault(r[0], []).append({"id": r[1], "name": r[2]})
    return names


def _urgency(row):
    return SEVERITY.get(row["color"], 9), (row["d"]["hostname"] or "").lower()


def _enrich(rows, names=None):
    """Colonnes de la liste : adresse du poste, compteurs, groupes, contact en clair ; postes à traiter d'abord."""
    names = _group_names() if names is None else names
    for r in rows:
        d = r["d"]
        own = (d["addresses"] or "").split()
        r.update(label=status_label(d, r["color"]), presence=presence(d), address=main_address(d),
                 os_short=short_os(d["os_name"]) or FAMILIES.get(d["os_family"], ""),
                 via=d["ip"] if d["ip"] and own and d["ip"] not in own else "",
                 system=d["updates"], security=d["security_updates"],
                 linux=d["os_family"] != "windows", seen=ago_text(d["last_seen"]), groups=names.get(d["id"], []))
    rows.sort(key=_urgency)
    return rows


def _fleet_view(rows, view):
    """Filtre « Hors ligne » ou « Redémarrage nécessaire » : (lignes gardées, filtre reconnu, nombre de postes de chacun)."""
    tests = {"hors-ligne": lambda r: not presence(r["d"])["online"], "redemarrage": lambda r: bool(r["d"]["reboot_required"])}
    extras = {key: sum(1 for r in rows if test(r)) for key, test in tests.items()}
    if view not in tests:
        return rows, "", extras
    return [r for r in rows if tests[view](r)], view, extras


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()


def _search(rows, query, names):
    """Postes dont le nom, le système, une adresse ou un groupe contient tous les mots recherchés."""
    terms = _fold(query).split()
    if not terms:
        return rows
    found = []
    for r in rows:
        d = r["d"]
        text = _fold(" ".join([d["hostname"] or "", d["os_name"] or "", d["addresses"] or "", d["ip"] or ""]
                              + [g["name"] for g in names.get(d["id"], [])]))
        if all(term in text for term in terms):
            found.append(r)
    return found


@app.route("/fleet")
@login_required
def fleet():
    rows = _rows()
    from . import groups
    group_id = request.args.get("group", "")
    selected_group = None
    if group_id:
        group_id = groups.identifiers([group_id])[0]
        selected_group = groups._group("device", group_id)
        member_ids = {r[0] for r in db().execute("SELECT device_id FROM device_group_members WHERE group_id=?", (group_id,))}
        rows = [r for r in rows if r["d"]["id"] in member_ids]
    # Ce que « Tout mettre à jour » fera vraiment : tout le groupe ou le parc, quels que soient les filtres affichés.
    to_update = sum(1 for r in rows if r["needs_update"])
    names = _group_names()
    query = request.args.get("q", "").strip()[:100]
    rows = _search(rows, query, names)
    counts = {c: sum(1 for r in rows if r["color"] == c) for c in ("green", "orange", "red", "grey")}
    systems = {"windows": sum(1 for r in rows if r["d"]["os_family"] == "windows")}
    systems["linux"] = len(rows) - systems["windows"]
    total = len(rows)
    state = request.args.get("statut", "")
    system = request.args.get("os", "")
    if state in counts:
        rows = [r for r in rows if r["color"] == state]
    if system in systems:
        rows = [r for r in rows if (r["d"]["os_family"] == "windows") == (system == "windows")]
    rows, view, extras = _fleet_view(rows, request.args.get("voir", ""))
    rows.sort(key=_urgency)
    shown = len(rows)
    pages = max(1, (shown + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        page = min(pages, max(1, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    rows = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    signature = hashlib.sha256(json.dumps([[[r["d"]["id"], r["d"]["last_seen"][:16], r["color"], r["reason"],
                                             presence(r["d"])["online"], len(r["pending"])] for r in rows],
                                           counts, systems, extras, shown]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    rows = _enrich(rows, names)
    from . import monitoring
    senders = wake.context() if can("fleet.wake") else {}
    for r in rows:
        r["mon"] = monitoring.latest(r["d"])
        # Proposé seulement si le poste est éteint, branché en Ethernet, et si quelque chose peut émettre sur son réseau.
        r["wake"] = (can("fleet.wake") and r["d"]["state"] == "active" and not presence(r["d"])["online"] and wake.offer(r["d"], senders)["ok"])
    from .auth import scope_summary
    limit = None if scope.whole() else scope_summary(db(), *scope.declared(db(), g.user["role"]))
    return render_template("fleet.html", device_groups=groups.listing("device"), selected_group=selected_group, rows=rows,
                           fleet_limit=limit, query=query, page=page, pages=pages, shown=shown, view=view, extras=extras,
                           counts=counts, systems=systems, total=total, state=state, system=system,
                           signature=signature, families=FAMILIES, actions=ACTIONS, to_update=to_update,
                           checkin=CHECKIN, online_minutes=ONLINE_SECONDS // 60, red_threshold=red_threshold())


@app.route("/fleet/search.json")
@login_required
def fleet_search():
    """Postes du parc pour la palette de recherche (Ctrl+K) : nom, système et adresse, lien vers la fiche."""
    devices = []
    for d in db().execute("SELECT id, hostname, os_name, os_family, ip, addresses FROM devices "
                          "WHERE state != 'removed'" + scope.sql() + " ORDER BY hostname COLLATE NOCASE"):
        sub = " · ".join(p for p in (d["os_name"] or d["os_family"], main_address(d)) if p)
        devices.append({"title": d["hostname"] or _("Poste sans nom"),
                        "sub": sub, "href": url_for("fleet_device", dev_id=d["id"])})
    return {"devices": devices}


@app.route("/fleet/settings")
@login_required
def fleet_settings():
    return render_template("fleet_settings.html", red_threshold=red_threshold(), checkin=CHECKIN,
                           server_coverage=addresses.coverage(db()), enroll_key=enroll_key())


@app.route("/fleet/addresses", methods=["POST"])
@login_required
def fleet_server_address():
    """Annonce (ou retrait) d'une future adresse du serveur, transmise aux agents avant le changement."""
    conn = db()
    url = request.form.get("url", "")
    try:
        if request.form.get("action") == "forget":
            if addresses.forget(conn, url):
                audit("parc.adresse-retiree", url, commit=False)
                flash(_("%(url)s n'est plus annoncée aux agents.", url=url), "ok")
        else:
            url = addresses.announce(conn, url)
            audit("parc.adresse-annoncee", url, commit=False)
            flash(_("%(url)s annoncée : chaque agent la reçoit à son prochain contact (moins de %(v)s min). Changez l'adresse du serveur quand tous les postes sont prévenus.", url=url, v=CHECKIN // 60), "ok")
        conn.commit()
    except ValueError as e:
        flash(_('Adresse refusée : %(e)s.', e=e), "error")
    return back("fleet_settings")


@app.route("/fleet/threshold", methods=["POST"])
@login_required
def fleet_threshold():
    try:
        value = int(request.form.get("threshold", ""))
        if not 2 <= value <= 10000:
            raise ValueError
    except ValueError:
        flash(_('Le seuil rouge doit être compris entre 2 et 10000 mises à jour.'), "error")
        return back("fleet_settings")
    set_setting("fleet_red_threshold", str(value))
    audit("parc.seuil", f"rouge à partir de {value} mises à jour")
    flash(_('Pastille rouge à partir de %(value)s mises à jour (système et logiciels).', value=value), "ok")
    return back("fleet_settings")


def ago_text(stamp):
    """« à l'instant », « il y a 5 min », « il y a 3 h », sinon la date : plus parlant qu'un horodatage brut."""
    delta = _ago(stamp)
    if delta is None:
        return _("jamais")
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return _("à l'instant")
    if minutes < 60:
        return _("il y a %(n)s min", n=minutes)
    if minutes < 24 * 60:
        return _("il y a %(n)s h", n=minutes // 60)
    return _("le %(date)s", date=date_fr(stamp[:16]))


def _get(dev_id):
    """Poste du parc du compte courant (404 sinon : un poste hors parc n'existe pas pour lui)."""
    dev = db().execute("SELECT * FROM devices WHERE id=? AND state != 'removed'", (dev_id,)).fetchone()
    if dev is None or not scope.allows(dev["id"]):
        abort(404)
    return dev


@app.route("/fleet/<int:dev_id>/live")
@login_required
def fleet_device_live(dev_id):
    """Supervision en direct : la page signale qu'elle regarde (l'agent passe à un relevé toutes les 2 s) et reçoit
    les relevés arrivés depuis « since »."""
    from . import monitoring
    dev = _get(dev_id)
    conn = db()
    monitoring.watch(conn, dev["id"])
    conn.commit()
    try:
        since = float(request.args.get("since", 0))
    except ValueError:
        since = 0.0
    return ({"points": monitoring.live_points(conn, dev["id"], since), "interval": monitoring.LIVE_INTERVAL,
             "now": time.time()}, 200, {"Cache-Control": "no-store"})


@app.route("/fleet/<int:dev_id>")
@login_required
def fleet_device(dev_id):
    dev = _get(dev_id)
    inv = inventory(dev)
    color, reason = status(dev)
    availability = presence(dev)
    history = db().execute("SELECT * FROM device_commands WHERE device_id=? ORDER BY id DESC LIMIT 30",
                           (dev_id,)).fetchall()
    signature = hashlib.sha256(json.dumps([dev["last_seen"][:16], dev["last_scan"], availability["online"],
                                           [(c["id"], c["state"]) for c in history]]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    apps = catalog_state(inv) if dev["os_family"] == "windows" else []
    from . import update_blocks
    updates = inv["updates"] = update_blocks.annotate(db(), dev, inv.get("updates") or [])
    soft = software(dev) if dev["os_family"] != "windows" else {}
    pkg_updates = {u.get("name"): u.get("version") for u in updates if u.get("name")}
    pkgs = soft.get("packages") or []
    pkg_stats = {"total": len(pkgs), "manual": sum(1 for p in pkgs if p.get("manual")),
                 "outdated": sum(1 for p in pkgs if p.get("name") in pkg_updates)}
    boot = (soft.get("system") or {}).get("boot")
    try:
        boot_text = ago_text(datetime.datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S")) if boot else ""
    except (OverflowError, OSError, ValueError):                              # horodatage de démarrage incohérent
        boot_text = ""
    summary = {"updates": dev["updates"], "security": dev["security_updates"], "blocked": dev["blocked_updates"],
               "apps": sum(1 for _, _, outdated in apps if outdated), "seen": ago_text(dev["last_seen"]),
               "scanned": ago_text(dev["last_scan"]), "enrolled": ago_text(dev["enrolled_at"])}
    from . import groups, schedules
    from . import monitoring
    mon = monitoring.view(db(), dev, request.args.get("periode", "24h"))
    return render_template("fleet_device.html", address=main_address(dev), mon=mon, device_groups=groups.listing("device"), memberships=groups.memberships("device", dev_id), dev=dev, inv=inv, color=color, reason=reason, history=history,
                           wake_offer=wake.offer(dev, wake.context()), net=wake.info(dev), wake_controls=wake.controls(dev),
                           remote_ready=version_key(dev["agent_version"]) >= (2, 4),
                           consent_ready=version_key(dev["agent_version"]) >= CONSENT_VERSION,
                           consent_version=".".join(map(str, CONSENT_VERSION)),
                           files_ready=version_key(dev["agent_version"]) >= (2, 23) and can("remote.files"),
                           action_label=action_label,
                           apps=apps, families=FAMILIES, actions=ACTIONS, signature=signature,
                           soft=soft, pkg_updates=pkg_updates, pkg_stats=pkg_stats, boot_text=boot_text,
                           needs_update=needs_update(dev), pending=pending(dev_id), summary=summary,
                           selectable=selectable(dev) and can("fleet.update") and dev["state"] == "active",
                           select_version=".".join(map(str, SELECT_UPDATES)),
                           label=status_label(dev, color), presence=availability,
                           planned=schedules.covering(db(), dev_id))


@app.route("/fleet/<int:dev_id>/progress")
@login_required
def fleet_device_progress(dev_id):
    """Actions en attente ou en cours d'un poste et leur avancement, relus toutes les 2 s par sa fiche."""
    _get(dev_id)
    out = []
    for c in pending(dev_id):
        live = progress_view(c["progress"]) if c["state"] == "running" else None
        out.append({"id": c["id"], "state": c["state"], "label": live["label"] if live else "",
                    "percent": live["percent"] if live else 0, "app": live.get("app", "") if live else "",
                    "items": live.get("items", []) if live else [], "counts": live.get("counts") if live else None})
    return {"commands": out}, 200, {"Cache-Control": "no-store"}


@app.route("/fleet/updates")
@login_required
def fleet_updates():
    """Déroulé global des mises à jour du parc : en cours (avancement en direct), en attente, terminées."""
    require("fleet.view")
    active, finished = _activity_rows()
    signature = _activity_signature(active, finished)
    if request.headers.get("X-Requested-With") == "fetch":
        # Barres et listes réécrites en place ; la page ne se recharge que si l'ensemble change (signature).
        live = []
        for c in active:
            if c["state"] == "running":
                view = (progress_view(c["progress"]) if c["action"] == "update" else None) or {}
                live.append({"id": c["id"], "percent": view.get("percent", 0), "label": view.get("label", ""),
                             "items": view.get("items", []), "counts": view.get("counts")})
        return {"signature": signature, "running": live}, 200, {"Cache-Control": "no-store"}
    return render_template("fleet_updates.html", signature=signature, finished_hours=ACTIVITY_FINISHED_HOURS,
                           **update_activity(active, finished))


def _back_to_origin(dev_id):
    """Depuis une liste (Mon parc, déroulé), y rester avec un message ; sinon aller à la fiche du poste."""
    dest = request.form.get("return", "")
    if dest:
        target = safe_next(dest)
        return {"redirect": target} if request.headers.get("X-Requested-With") == "fetch" else redirect(target)
    return back("fleet_device", dev_id=dev_id)


@app.route("/fleet/<int:dev_id>/action", methods=["POST"])
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
        flash(_("%(dev)s retiré de la liste (l'agent éventuel n'est plus accepté).", dev=dev['hostname']), "ok")
        return back("fleet")
    if action not in ACTIONS or action == "wake-relay" or dev["state"] != "active":
        abort(400)
    if action == "wake":
        result = wake.wake([dev])
        _report_wake(result)
        audit("parc.wake", dev["hostname"] + (f" ({result['skipped'][0][1]})" if result["skipped"] else ""))
        return _back_to_origin(dev_id)
    params = {}
    if action == "update":
        params["reboot"] = request.form.get("reboot") == "1"
        if request.form.get("scope") == "selection":
            params["only"] = update_selection(dev, request.form)
    if action == "install-app":
        slug = request.form.get("slug", "")
        if not config.SLUG_RE.match(slug) or dev["os_family"] != "windows" or not db().execute(
                "SELECT 1 FROM win_apps WHERE slug=? AND status='ready'", (slug,)).fetchone():
            abort(400)
        params["slug"] = slug
    if queue(dev, action, params):
        db().commit()
        audit(f"parc.{action}", f"{dev['hostname']}{' : ' + params['slug'] if 'slug' in params else ''}")
        flash(_('%(ACTIONS)s demandée sur %(dev)s : exécutée au prochain contact (moins de %(v)s min).', ACTIONS=ACTIONS[action], dev=dev['hostname'], v=CHECKIN // 60), "ok")
    else:
        flash(_('Action déjà en attente sur ce poste.'), "error")
    return _back_to_origin(dev_id)


@app.route("/fleet/<int:dev_id>/windows-update", methods=["POST"])
@login_required
def fleet_windows_update(dev_id):
    require("fleet.update")
    dev = _get(dev_id)
    if dev["os_family"] != "windows":
        abort(400)
    blocked = request.form.get("block") == "1"
    db().execute("UPDATE devices SET block_windows_update=? WHERE id=?", (1 if blocked else 0, dev_id))
    db().commit()
    audit("parc.windows-update", f"{dev['hostname']} : {'bloquées' if blocked else 'rendues à Windows'}")
    flash(_("Mises à jour Windows bloquées sur %(dev)s : appliqué au prochain contact (moins de %(v)s min).", dev=dev["hostname"], v=CHECKIN // 60)
          if blocked else _("Mises à jour Windows rendues à %(dev)s : appliqué au prochain contact.", dev=dev["hostname"]), "ok")
    return back("fleet_device", dev_id=dev_id, _anchor="maj")


@app.route("/fleet/<int:dev_id>/cancel/<int:cmd_id>", methods=["POST"])
@login_required
def fleet_cancel_command(dev_id, cmd_id):
    dev = _get(dev_id)
    cmd = db().execute("SELECT * FROM device_commands WHERE id=? AND device_id=?", (cmd_id, dev_id)).fetchone()
    if cmd is None or cmd["state"] != "queued":
        abort(404)                              # déjà transmise à l'agent ou terminée : plus annulable ici
    require(permissions.ACTION_PERMISSIONS.get(cmd["action"], "fleet.view"))
    db().execute("UPDATE device_commands SET state='cancelled', finished_at=?, output=? WHERE id=?",
                 (_now(), "Annulée avant transmission à l'agent.", cmd_id))
    if cmd["action"] == "uninstall":
        db().execute("UPDATE devices SET state='active' WHERE id=? AND state='removing'", (dev_id,))
    db().commit()
    audit(f"parc.{cmd['action']}.annulation", dev["hostname"])
    flash(_('Action planifiée annulée.'), "ok")
    return _back_to_origin(dev_id)


@app.route("/fleet/update-all", methods=["POST"])
@login_required
def fleet_update_all():
    apps = catalog()
    from . import groups
    group_id = request.form.get("group", "")
    member_ids = None
    if group_id:
        group_id = groups.identifiers([group_id])[0]
        group = groups._group("device", group_id)
        member_ids = {r[0] for r in db().execute("SELECT device_id FROM device_group_members WHERE group_id=?", (group_id,))}
    reboot = request.form.get("reboot") == "1"
    if reboot:
        require("fleet.reboot")
    if request.form.get("mode") == "waves":
        from .deployments import start_update_rollout
        devices = [r["d"] for r in _rows(apps) if r["needs_update"] and (member_ids is None or r["d"]["id"] in member_ids)]
        if not devices:
            flash(_("Aucun poste à mettre à jour."), "ok")
            return back("fleet", **({"group": group_id} if group_id else {}))
        try:
            deployment_id = start_update_rollout(db(), devices, request.form, reboot, group["name"] if group_id else "")
        except ValueError as e:
            db().rollback()
            flash(str(e), "error")
            return back("fleet", **({"group": group_id} if group_id else {}))
        db().commit()
        return back("deployment_detail", deployment_id=deployment_id)
    count = 0
    for r in _rows(apps):
        if member_ids is not None and r["d"]["id"] not in member_ids:
            continue
        if r["needs_update"] and queue(r["d"], "update", {"reboot": reboot}):
            count += 1
    db().commit()
    audit("parc.tout-mettre-a-jour", f"{counted(count, 'poste')}{', avec redémarrage' if reboot else ''}")
    flash((n_('Mise à jour demandée sur %(num)s poste.', 'Mise à jour demandée sur %(num)s postes.', count) if count else _('Aucun poste à mettre à jour.')), "ok")
    # S'il y a quelque chose à suivre, aller au déroulé des mises à jour ; sinon rester sur la liste.
    return back("fleet_updates") if count else back("fleet", **({"group": group_id} if group_id else {}))


@app.route("/fleet/groups/<int:group_id>/windows-update", methods=["POST"])
@login_required
def fleet_group_windows_update(group_id):
    """Gèle (ou rend) les mises à jour Windows sur tous les postes Windows d'un groupe, en une fois."""
    require("fleet.update")
    from . import groups
    group = groups._group("device", group_id)
    blocked = request.form.get("block") == "1"
    cur = db().execute("UPDATE devices SET block_windows_update=? WHERE os_family='windows' AND state!='removed' "
                       "AND id IN (SELECT device_id FROM device_group_members WHERE group_id=?)" + scope.sql(),
                       (1 if blocked else 0, group_id))
    db().commit()
    n = cur.rowcount
    audit("parc.windows-update-groupe", f"{group['name']} : {counted(n, 'poste')}, {'Windows Update bloqué' if blocked else 'Windows Update rendu'}")
    if not n:
        flash(_("Aucun poste Windows dans le groupe %(name)s.", name=group["name"]), "error")
    elif blocked:
        flash(n_("Mises à jour Windows bloquées sur %(num)s poste du groupe %(name)s : appliqué au prochain contact.", "Mises à jour Windows bloquées sur %(num)s postes du groupe %(name)s : appliqué au prochain contact.", n, name=group["name"]), "ok")
    else:
        flash(n_("Mises à jour Windows rendues à %(num)s poste du groupe %(name)s : appliqué au prochain contact.", "Mises à jour Windows rendues à %(num)s postes du groupe %(name)s : appliqué au prochain contact.", n, name=group["name"]), "ok")
    return back("fleet_groups", _anchor=f"group-{group_id}")


@app.route("/fleet/update-selected", methods=["POST"])
@login_required
def fleet_update_selected():
    """Met à jour les postes cochés dans la liste (ids). Mêmes droits qu'une mise à jour de poste."""
    require("fleet.update")
    try:
        ids = {int(i) for i in request.form.getlist("ids")}
    except ValueError:
        abort(400)
    if not ids or len(ids) > 5000:
        abort(400)
    reboot = request.form.get("reboot") == "1"
    if reboot:
        require("fleet.reboot")
    rows = {r["d"]["id"]: r for r in _rows() if r["d"]["id"] in ids}
    count = 0
    for dev_id in ids:
        r = rows.get(dev_id)
        if r and r["d"]["state"] == "active" and queue(r["d"], "update", {"reboot": reboot}):
            count += 1
    db().commit()
    audit("parc.mise-a-jour-selection", f"{counted(count, 'poste')}{', avec redémarrage' if reboot else ''}")
    return {"ok": True, "count": count}


def _report_wake(result):
    for name in result["direct"]:
        flash(_("Réveil de %(dev)s envoyé : il devrait reprendre contact d'ici quelques minutes.", dev=name), "ok")
    for name, relay in result["relayed"]:
        flash(_("Réveil de %(dev)s confié à %(relay)s : envoyé à son prochain contact (moins de %(v)s min).", dev=name, relay=relay, v=CHECKIN // 60), "ok")
    for name, reason in result["skipped"]:
        flash(_("%(dev)s n'a pas été réveillé : %(reason)s", dev=name, reason=reason), "error")


@app.route("/fleet/<int:dev_id>/wake-policy", methods=["POST"])
@login_required
def fleet_wake_policy(dev_id):
    """Demande à l'agent d'activer le réveil sur ses cartes Ethernet, et de le maintenir ; ou de l'arrêter."""
    require("fleet.wake")
    dev = _get(dev_id)
    enabled = request.form.get("enabled") == "1"
    allowed = wake.controls(dev)
    if not (allowed["enable"] if enabled else allowed["disable"]):
        abort(400)
    wake.set_policy(db(), [dev_id], enabled)
    db().commit()
    audit("parc.wake-reglage", f"{dev['hostname']} : {'activé' if enabled else 'désactivé'}")
    flash(_("Réveil demandé sur %(dev)s : l'agent l'active à son prochain contact (moins de %(v)s min), puis le maintient. Il doit aussi être autorisé dans le BIOS du poste.", dev=dev["hostname"], v=CHECKIN // 60) if enabled
          else _("Réveil coupé sur %(dev)s à son prochain contact (moins de %(v)s min).", dev=dev["hostname"], v=CHECKIN // 60), "ok")
    return _back_to_origin(dev_id)


@app.route("/fleet/wake-policy-selected", methods=["POST"])
@login_required
def fleet_wake_policy_selected():
    """Active le réveil sur les postes sélectionnés dont l'agent en est capable ; les autres sont comptés."""
    require("fleet.wake")
    try:
        ids = {int(i) for i in request.form.getlist("ids")}
    except ValueError:
        abort(400)
    if not ids or len(ids) > 5000:
        abort(400)
    devices = [d for d in db().execute("SELECT * FROM devices WHERE state='active'" + scope.sql()).fetchall() if d["id"] in ids]
    chosen = [d["id"] for d in devices if wake.controls(d)["enable"]]
    wake.set_policy(db(), chosen, True)
    db().commit()
    audit("parc.wake-reglage", f"{counted(len(chosen), 'poste')} sur {counted(len(ids), 'sélectionné')} : activé")
    if chosen:
        flash(n_("Réveil demandé sur %(num)s poste : l'agent l'active à son prochain contact, puis le maintient. Il doit aussi être autorisé dans le BIOS.", "Réveil demandé sur %(num)s postes : les agents l'activent à leur prochain contact, puis le maintiennent. Il doit aussi être autorisé dans le BIOS.", len(chosen)), "ok")
    if len(ids) > len(chosen):
        flash(n_("%(num)s poste laissé tel quel : réveil déjà actif, carte Ethernet absente ou sans réveil, ou agent trop ancien.", "%(num)s postes laissés tels quels : réveil déjà actif, carte Ethernet absente ou sans réveil, ou agent trop ancien.", len(ids) - len(chosen)), "error")
    return {"ok": True, "count": len(chosen), "skipped": len(ids) - len(chosen)}


@app.route("/fleet/wake-selected", methods=["POST"])
@login_required
def fleet_wake_selected():
    """Réveille les postes sélectionnés ; ceux qui ne peuvent pas l'être sont comptés, sans bloquer les autres."""
    require("fleet.wake")
    try:
        ids = {int(i) for i in request.form.getlist("ids")}
    except ValueError:
        abort(400)
    if not ids or len(ids) > 5000:
        abort(400)
    devices = [d for d in db().execute("SELECT * FROM devices WHERE state='active'" + scope.sql()).fetchall() if d["id"] in ids]
    result = wake.wake(devices)
    done = len(result["direct"]) + len(result["relayed"])
    audit("parc.wake", f"{counted(done, 'poste')} sur {counted(len(ids), 'sélectionné')}")
    if done:
        flash(n_("Réveil envoyé à %(num)s poste : il devrait reprendre contact d'ici quelques minutes.", "Réveil envoyé à %(num)s postes : ils devraient reprendre contact d'ici quelques minutes.", done), "ok")
    if result["skipped"]:
        flash(n_("%(num)s poste n'a pas été réveillé : déjà allumé, ou pas branché en Ethernet sur un réseau joignable.", "%(num)s postes n'ont pas été réveillés : déjà allumés, ou pas branchés en Ethernet sur un réseau joignable.", len(result["skipped"])), "error")
    return {"ok": True, "count": done, "skipped": len(result["skipped"])}


@app.route("/fleet/key", methods=["POST"])
@login_required
def fleet_rotate_key():
    set_setting("enroll_key", secrets.token_urlsafe(18))
    audit("parc.cle", "nouvelle clé d'inscription")
    flash(_("Nouvelle clé d'inscription : les postes déjà inscrits ne sont pas concernés."), "ok")
    return redirect(url_for("fleet_settings"))
