"""Wazuh (fonctionnalité facultative) : installer l'agent Wazuh sur les postes du parc et suivre son état.

loco ne remplace pas Wazuh : il pose son agent sur les postes déjà inscrits, réglé pour votre manager. Le paquet officiel
(deb, rpm ou msi) est téléchargé une fois par loco depuis packages.wazuh.com, contrôlé par l'empreinte SHA-512 que Wazuh
publie, puis remis aux postes par le canal de l'agent loco : un poste n'a pas besoin d'Internet. Le mot de passe
d'enrôlement, s'il y en a un, est gardé chiffré, n'est jamais réaffiché ni journalisé, et ne voyage que dans la commande
remise à l'agent ; sur le poste il est écrit dans le fichier que l'agent Wazuh lit (authd.pass), jamais passé en argument.
"""
import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import urllib.request

from flask import abort, flash, render_template, request, send_file

from . import app, config, features, fleet, scope
from .auth import audit, can, login_required, require
from .db import db
from .i18n import _, n_
from .util import back

SETTING = "wazuh_config"
VERSIONS_SETTING = "wazuh_versions"
SITE = "https://packages.wazuh.com"
WAZUH_DIR = os.path.join(config.DATA, "wazuh")
AGENT_VERSION = (2, 166)                    # premier agent loco qui sait installer l'agent Wazuh et remonter son état (2.166.0)
VERSION_RE = re.compile(r"[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}")
HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?|\[?[0-9A-Fa-f:]{2,45}\]?")
GROUP_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")
NAME_RE = re.compile(r"wazuh-agent[_-][0-9.]+-1[_.](amd64|arm64|x86_64|aarch64)\.(deb|rpm)|wazuh-agent-[0-9.]+-1\.msi")
MAX_PACKAGE = 200 * 1024 * 1024
VERSIONS_TTL = 24 * 3600
FAMILIES = ("debian", "rhel", "windows")
MASK = "••••••••"


# --------------------------------------------------------------------------- réglages

def _cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-wazuh-v1", hashlib.sha256).digest()))


def _seal(value):
    return _cipher().encrypt(value.encode()).decode() if value else ""


def _open(value):
    try:
        return _cipher().decrypt(value.encode()).decode() if value else ""
    except Exception:                           # clé du serveur changée ou valeur altérée : mot de passe à ressaisir
        return ""


def load(conn=None):
    """Réglages effectifs : manager, groupe par défaut, version de l'agent, mot de passe d'enrôlement (déchiffré)."""
    cfg = {"manager": "", "group": "", "version": "", "password": ""}
    row = (conn or db()).execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    if row:
        try:
            stored = json.loads(row[0])
        except (TypeError, ValueError):
            stored = {}
        cfg.update({k: str(v) for k, v in stored.items() if k in cfg})
        cfg["password"] = _open(cfg["password"])
    return cfg


def _save(conn, cfg):
    stored = dict(cfg, password=_seal(cfg["password"]))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTING, json.dumps(stored)))


def clean_groups(raw):
    """« servers, docker » -> « servers,docker » ; ValueError si un nom de groupe n'est pas acceptable."""
    names = [g.strip() for g in str(raw or "").split(",") if g.strip()]
    if len(names) > 8 or any(not GROUP_RE.fullmatch(g) for g in names):
        raise ValueError(_("Groupe Wazuh invalide : lettres, chiffres, point, tiret et tiret bas, séparés par des virgules."))
    return ",".join(names)


def ready(cfg=None):
    cfg = cfg or load()
    return bool(cfg["manager"] and cfg["version"])


# --------------------------------------------------------------------------- versions et paquets officiels

def _version_key(version):
    return tuple(int(p) for p in version.split("."))


def _get(url, limit):
    request_ = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request_, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(_("réponse trop volumineuse"))
    return data


def _branch(version):
    return f"{SITE}/{version.split('.')[0]}.x"


def fetch_versions():
    """Versions de l'agent publiées par Wazuh, lues dans l'index de son dépôt APT (de la plus récente à la plus ancienne)."""
    found = set()
    for branch in ("4.x", "5.x"):
        try:
            raw = _get(f"{SITE}/{branch}/apt/dists/stable/main/binary-amd64/Packages.gz", 8 * 1024 * 1024)
            text = gzip.decompress(raw).decode("utf-8", "replace")
        except (OSError, ValueError, EOFError):
            continue
        name = ""
        for line in text.splitlines():
            if line.startswith("Package: "):
                name = line[9:].strip()
            elif line.startswith("Version: ") and name == "wazuh-agent":
                version = line[9:].strip().partition("-")[0]
                if VERSION_RE.fullmatch(version):
                    found.add(version)
    if not found:
        raise ValueError(_("index des versions de Wazuh injoignable"))
    return sorted(found, key=_version_key, reverse=True)


def versions(conn=None, refresh=False):
    """(versions connues, instant du relevé, erreur du dernier essai). Relevé gardé un jour ; l'ancien sert si le site ne répond pas."""
    conn = conn or db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (VERSIONS_SETTING,)).fetchone()
    try:
        cached = json.loads(row[0]) if row else {}
    except (TypeError, ValueError):
        cached = {}
    known = [v for v in cached.get("versions", []) if isinstance(v, str) and VERSION_RE.fullmatch(v)]
    at = cached.get("at", 0) if isinstance(cached.get("at"), (int, float)) else 0
    if not refresh and known and time.time() - at < VERSIONS_TTL:
        return known, at, ""
    try:
        known, at = fetch_versions(), time.time()
    except (OSError, ValueError) as e:
        return known, at, str(e)[:200] or e.__class__.__name__
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (VERSIONS_SETTING, json.dumps({"versions": known, "at": at})))
    conn.commit()
    return known, at, ""


def arch_of(dev):
    """Architecture d'un poste Linux d'après son inventaire (amd64 par défaut)."""
    try:
        arch = (fleet.software(dev).get("system") or {}).get("arch", "")
    except Exception:
        arch = ""
    return "arm64" if arch in ("aarch64", "arm64") else "amd64"


def package_name(family, arch, version):
    if family == "debian":
        return f"wazuh-agent_{version}-1_{arch}.deb"
    if family == "rhel":
        return f"wazuh-agent-{version}-1.{'aarch64' if arch == 'arm64' else 'x86_64'}.rpm"
    return f"wazuh-agent-{version}-1.msi"


def package_url(name, version):
    if name.endswith(".deb"):
        return f"{_branch(version)}/apt/pool/main/w/wazuh-agent/{name}"
    if name.endswith(".rpm"):
        return f"{_branch(version)}/yum/{name}"
    return f"{_branch(version)}/windows/{name}"


def package_path(name):
    return os.path.join(WAZUH_DIR, name)


_STATE = {}                     # nom du paquet -> ("running" | message d'erreur, instant)
_GUARD = threading.Lock()


def _fetch_package(name, version):
    """Télécharge le paquet officiel et le garde seulement s'il a l'empreinte SHA-512 publiée par Wazuh."""
    try:
        os.makedirs(WAZUH_DIR, exist_ok=True)
        published = _get(f"{_branch(version)}/checksums/wazuh/{version}/{name}.sha512", 4096).decode("ascii", "replace").split()
        if not published or not re.fullmatch(r"[0-9a-f]{128}", published[0]):
            raise ValueError(_("empreinte publiée par Wazuh illisible"))
        fd, tmp = tempfile.mkstemp(dir=WAZUH_DIR, prefix=".dl-")
        try:
            sha512, sha256, done = hashlib.sha512(), hashlib.sha256(), 0
            request_ = urllib.request.Request(package_url(name, version), headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(request_, timeout=120) as r, os.fdopen(fd, "wb") as out:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_PACKAGE:
                        raise ValueError(_("paquet trop volumineux"))
                    sha512.update(chunk)
                    sha256.update(chunk)
                    out.write(chunk)
            if sha512.hexdigest() != published[0]:
                raise ValueError(_("empreinte SHA-512 différente de celle publiée par Wazuh : paquet refusé"))
            with open(package_path(name) + ".json", "w") as f:
                json.dump({"sha256": sha256.hexdigest(), "size": done}, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, package_path(name))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        with _GUARD:
            _STATE.pop(name, None)
    except (OSError, ValueError, EOFError) as e:
        with _GUARD:
            _STATE[name] = (str(e)[:200] or e.__class__.__name__, time.time())


def package_info(name):
    """{sha256, size} du paquet déjà téléchargé et vérifié, ou None."""
    try:
        with open(package_path(name) + ".json") as f:
            info = json.load(f)
        if os.path.getsize(package_path(name)) == info["size"] and re.fullmatch(r"[0-9a-f]{64}", info["sha256"]):
            return info
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def package_state(name, version):
    """« ready », « pending » (téléchargement lancé ou en cours) ou le message d'une erreur récente."""
    if not NAME_RE.fullmatch(name) or not VERSION_RE.fullmatch(version):
        return _("paquet Wazuh inconnu")
    if package_info(name):
        return "ready"
    with _GUARD:
        state = _STATE.get(name)
        if state and state[0] == "running":
            return "pending"
        if state and time.time() - state[1] < 600:
            return _("téléchargement de l'agent Wazuh impossible : %(e)s", e=state[0])
        _STATE[name] = ("running", time.time())
    threading.Thread(target=_fetch_package, args=(name, version), name="loco-wazuh-package", daemon=True).start()
    return "pending"


def cached_packages():
    """Paquets gardés sur loco : [(nom, taille)]."""
    try:
        names = sorted(n for n in os.listdir(WAZUH_DIR) if NAME_RE.fullmatch(n))
    except OSError:
        return []
    return [(n, os.path.getsize(package_path(n))) for n in names if package_info(n)]


# --------------------------------------------------------------------------- état remonté par les postes

def clean_state(raw):
    """État de l'agent Wazuh remonté par l'agent loco, borné ; None si le poste ne dit rien (agent loco plus ancien)."""
    if not isinstance(raw, dict):
        return None
    if not raw.get("installed"):
        return {"installed": False}
    version = str(raw.get("version") or "").lstrip("v")
    status = raw.get("status") if raw.get("status") in ("connected", "pending", "disconnected") else ""
    return {"installed": True, "version": version if VERSION_RE.fullmatch(version) else "",
            "active": bool(raw.get("active")), "status": status,
            "manager": fleet._text(raw.get("manager"), 255), "id": fleet._text(raw.get("id"), 8),
            "name": fleet._text(raw.get("name"), 128)}


def record(conn, dev, raw):
    """Garde l'état remonté par le poste, seulement s'il a changé (pas d'écriture à chaque contact)."""
    state = clean_state(raw)
    if state is None:
        return
    text = json.dumps(state, sort_keys=True)
    if text != dev["wazuh"]:
        conn.execute("UPDATE devices SET wazuh=? WHERE id=?", (text, dev["id"]))


def supported(dev):
    return dev["os_family"] in FAMILIES


def agent_ready(dev):
    return fleet.version_key(dev["agent_version"]) >= AGENT_VERSION


def view(dev, cfg=None):
    """État de l'agent Wazuh d'un poste, prêt à afficher : niveau (ok, warn, err, off), libellé, précision, actions."""
    cfg = cfg or load()
    try:
        state = json.loads(dev["wazuh"]) if dev["wazuh"] else None
    except ValueError:
        state = None
    out = {"state": state, "level": "off", "label": _("Inconnu"), "detail": "", "installed": False, "outdated": False}
    if not agent_ready(dev):
        out["detail"] = _("l'agent loco du poste doit d'abord se mettre à jour")
        return out
    if state is None:
        out["detail"] = _("pas encore remonté par le poste")
        return out
    if not state["installed"]:
        out["label"] = _("Non installé")
        return out
    out["installed"] = True
    version = state["version"]
    out["outdated"] = bool(version and cfg["version"] and _version_key(version) < _version_key(cfg["version"]))
    elsewhere = bool(cfg["manager"] and state["manager"] and state["manager"].lower() != cfg["manager"].lower())
    if not state["active"]:
        out.update(level="err", label=_("Arrêté"), detail=_("le service de l'agent Wazuh ne tourne pas"))
    elif state["status"] == "connected":
        out.update(level="ok", label=_("Connecté"))
    elif state["status"] == "pending":
        out.update(level="warn", label=_("En attente du manager"), detail=_("installé, pas encore accepté par le manager"))
    else:
        out.update(level="warn", label=_("Déconnecté"), detail=_("le manager ne répond pas à ce poste"))
    notes = [out["detail"]] if out["detail"] else []
    if elsewhere:
        notes.append(_("relié à %(manager)s", manager=state["manager"]))
        if out["level"] == "ok":
            out["level"] = "warn"
    if out["outdated"]:
        notes.append(_("version %(v)s à mettre à jour", v=version))
    out["detail"] = " · ".join(notes)
    out["version"] = version
    return out


def busy(conn, dev_id):
    """Opération Wazuh déjà demandée pour ce poste et pas encore terminée : « install », « remove » ou None."""
    row = conn.execute("SELECT params FROM device_commands WHERE device_id=? AND action='wazuh' AND state IN ('queued','sent','running') "
                       "ORDER BY id DESC LIMIT 1", (dev_id,)).fetchone()
    try:
        return json.loads(row["params"]).get("op") if row else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- commande remise à l'agent

def payload(dev, params, out):
    """Complète la commande « wazuh » au moment de la remettre : paquet vérifié, manager, groupe, mot de passe.
    Renvoie la commande, fleet.WAIT (paquet en cours de téléchargement, agent loco pas encore à jour) ou un message d'échec."""
    if not supported(dev):
        return _("L'agent Wazuh ne s'installe pas sur ce système.")
    if not agent_ready(dev):
        return fleet.WAIT                   # l'agent loco se met à jour seul : la commande l'attend
    op = params.get("op")
    if op == "remove":
        out["wazuh"] = {"op": "remove"}
        return out
    cfg = load()
    if op != "install" or not ready(cfg):
        return _("Wazuh n'est pas réglé : adresse du manager et version de l'agent attendues.")
    name = package_name(dev["os_family"], arch_of(dev), cfg["version"])
    state = package_state(name, cfg["version"])
    if state == "pending":
        return fleet.WAIT
    if state != "ready":
        return state
    info = package_info(name)
    out["wazuh"] = {"op": "install", "manager": cfg["manager"], "group": params.get("group") or "", "password": cfg["password"],
                    "package": {"file": name, "version": cfg["version"], "sha256": info["sha256"], "size": info["size"],
                                "url": f"/agent/wazuh/{name}"}}
    return out


@app.route("/agent/wazuh/<name>")
def agent_wazuh_package(name):
    """Paquet de l'agent Wazuh, téléchargé par l'agent loco du poste. Sans secret : c'est le paquet public de Wazuh,
    servi comme les fichiers de l'agent loco (réseaux des postes) ; le poste vérifie l'empreinte reçue avec sa commande."""
    if not NAME_RE.fullmatch(name) or not package_info(name):
        abort(404)
    return send_file(package_path(name), mimetype="application/octet-stream", as_attachment=True, download_name=name)


# --------------------------------------------------------------------------- portail

def _devices(conn):
    rows = conn.execute("SELECT * FROM devices WHERE state='active' AND os_family IN ('debian','rhel','windows')" + scope.sql()
                        + " ORDER BY hostname COLLATE NOCASE").fetchall()
    return rows


@app.route("/fleet/wazuh")
@login_required
def wazuh():
    conn = db()
    cfg = load(conn)
    known, checked, version_error = versions(conn) if can("wazuh.manage") or not cfg["version"] else ([], 0, "")
    rows = []
    for dev in _devices(conn):
        rows.append({"d": dev, "w": view(dev, cfg), "busy": busy(conn, dev["id"]), "ready": agent_ready(dev),
                     "os": fleet.short_os(dev["os_name"]) or str(fleet.FAMILIES.get(dev["os_family"], ""))})
    counts = {"installed": sum(1 for r in rows if r["w"]["installed"]), "connected": sum(1 for r in rows if r["w"]["level"] == "ok"),
              "total": len(rows)}
    return render_template("wazuh.html", cfg=dict(cfg, password=MASK if cfg["password"] else ""), ready=ready(cfg), rows=rows,
                           counts=counts, versions=known, versions_at=checked, version_error=version_error,
                           packages=cached_packages(), agent_version=".".join(map(str, AGENT_VERSION)) + ".0")


@app.route("/fleet/wazuh/settings", methods=["POST"])
@login_required
def wazuh_settings():
    require("wazuh.manage")
    conn = db()
    cfg = load(conn)
    manager = request.form.get("manager", "").strip()
    version = request.form.get("version", "").strip()
    try:
        if not HOST_RE.fullmatch(manager):
            raise ValueError(_("Adresse du manager attendue : un nom ou une adresse IP, sans « https:// » ni port."))
        if not VERSION_RE.fullmatch(version):
            raise ValueError(_("Version de l'agent attendue, par exemple 4.14.10."))
        group = clean_groups(request.form.get("group"))
    except ValueError as e:
        flash(str(e), "error")
        return back("wazuh")
    password = request.form.get("password", "")
    if request.form.get("clear_password") == "1":
        password = ""
    elif password in ("", MASK):
        password = cfg["password"]              # champ laissé tel quel : le mot de passe enregistré est gardé
    elif len(password) > 256 or any(ord(c) < 32 for c in password):
        flash(_("Mot de passe d'enrôlement invalide."), "error")
        return back("wazuh")
    changed = [k for k, v in (("manager", manager), ("group", group), ("version", version)) if cfg[k] != v]
    if password != cfg["password"]:
        changed.append("password")
    _save(conn, {"manager": manager, "group": group, "version": version, "password": password})
    # Le mot de passe n'est jamais écrit au journal : seulement le fait qu'il a changé.
    audit("parc.wazuh.reglages", f"manager {manager}, agent {version}{f', groupe {group}' if group else ''}"
          + (", mot de passe d'enrôlement modifié" if "password" in changed else ""))
    if "version" in changed:
        cleanup_packages(conn, version)
    flash(_("Réglages Wazuh enregistrés."), "ok")
    return back("wazuh")


@app.route("/fleet/wazuh/versions", methods=["POST"])
@login_required
def wazuh_versions():
    require("wazuh.manage")
    known, _at, error = versions(db(), refresh=True)
    if error:
        flash(_("Versions de Wazuh non relevées : %(e)s", e=error), "error")
    else:
        flash(n_("%(num)s version de l'agent Wazuh disponible.", "%(num)s versions de l'agent Wazuh disponibles.", len(known)), "ok")
    return back("wazuh")


def _targets(conn):
    """Postes visés par le formulaire (bouton d'une ligne, ou cases cochées), limités au parc du compte."""
    raw = [request.form.get("only")] if request.form.get("only") else request.form.getlist("ids")
    try:
        ids = {int(i) for i in raw}
    except (TypeError, ValueError):
        abort(400)
    if len(ids) > 5000:
        abort(400)
    return [d for d in _devices(conn) if d["id"] in ids]


@app.route("/fleet/wazuh/install", methods=["POST"])
@login_required
def wazuh_install():
    """Installe l'agent Wazuh (ou le met à la version réglée) sur les postes choisis."""
    require("wazuh.install")
    conn = db()
    cfg = load(conn)
    if not ready(cfg):
        flash(_("Réglez d'abord l'adresse du manager et la version de l'agent."), "error")
        return back("wazuh")
    try:
        group = clean_groups(request.form.get("group")) if "group" in request.form else cfg["group"]
    except ValueError as e:
        flash(str(e), "error")
        return back("wazuh")
    devices = _targets(conn)
    names = [d["hostname"] for d in devices if not busy(conn, d["id"]) and fleet.queue(d, "wazuh", {"op": "install", "group": group})]
    conn.commit()
    if names:
        audit("parc.wazuh.installation", f"{', '.join(names[:20])}{'…' if len(names) > 20 else ''} → {cfg['manager']}"
              + (f", groupe {group}" if group else ""))
        flash(n_("Installation de l'agent Wazuh demandée sur %(num)s poste : elle part à son prochain contact.",
                 "Installation de l'agent Wazuh demandée sur %(num)s postes : elle part à leur prochain contact.", len(names)), "ok")
    else:
        flash(_("Aucun poste à équiper : cochez des postes, ou une installation est déjà en cours."), "error")
    return _return(devices)


@app.route("/fleet/wazuh/remove", methods=["POST"])
@login_required
def wazuh_remove():
    """Retire l'agent Wazuh des postes choisis."""
    require("wazuh.install")
    conn = db()
    devices = _targets(conn)
    names = [d["hostname"] for d in devices if not busy(conn, d["id"]) and fleet.queue(d, "wazuh", {"op": "remove"})]
    conn.commit()
    if names:
        audit("parc.wazuh.retrait", f"{', '.join(names[:20])}{'…' if len(names) > 20 else ''}")
        flash(n_("Retrait de l'agent Wazuh demandé sur %(num)s poste.", "Retrait de l'agent Wazuh demandé sur %(num)s postes.", len(names)), "ok")
    else:
        flash(_("Aucun poste concerné : cochez des postes, ou une opération est déjà en cours."), "error")
    return _return(devices)


def _return(devices):
    """Retour à la fiche du poste quand l'action en vient, sinon à la page Wazuh."""
    if request.form.get("from") == "device" and len(devices) == 1:
        return back("fleet_device", dev_id=devices[0]["id"])
    return back("wazuh")


def device_panel(dev):
    """Ce que la fiche d'un poste montre de Wazuh, ou None (fonctionnalité coupée, système sans agent Wazuh, droit absent)."""
    if not features.enabled("wazuh") or not supported(dev) or not can("wazuh.view"):
        return None
    cfg = load()
    return {"w": view(dev, cfg), "busy": busy(db(), dev["id"]), "ready": ready(cfg), "group": cfg["group"]}


def cleanup_packages(conn, version):
    """Paquets d'une autre version que celle réglée, sans installation en attente : retirés (ils se retéléchargent au besoin)."""
    waiting = conn.execute("SELECT 1 FROM device_commands WHERE action='wazuh' AND state IN ('queued','sent','running') LIMIT 1").fetchone()
    if waiting or not os.path.isdir(WAZUH_DIR):
        return
    for name in os.listdir(WAZUH_DIR):
        path = os.path.join(WAZUH_DIR, name)
        stale = name.startswith(".dl-") and time.time() - os.path.getmtime(path) > 6 * 3600
        other = NAME_RE.fullmatch(name.removesuffix(".json")) and version and f"{version}-1" not in name
        if stale or other:
            try:
                os.unlink(path)
            except OSError:
                pass
