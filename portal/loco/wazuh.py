"""Wazuh (fonctionnalité facultative) : installer l'agent Wazuh sur les postes du parc et suivre son état.

loco ne remplace pas Wazuh : il pose son agent sur les postes déjà inscrits, réglé pour votre manager. Le paquet officiel
(deb, rpm ou msi) est téléchargé une fois par loco depuis packages.wazuh.com, contrôlé par l'empreinte SHA-512 que Wazuh
publie, puis remis aux postes par le canal de l'agent loco : un poste n'a pas besoin d'Internet. Le mot de passe
d'enrôlement, s'il y en a un, est gardé chiffré, n'est jamais réaffiché ni journalisé, et ne voyage que dans la commande
remise à l'agent ; sur le poste il est écrit dans le fichier que l'agent Wazuh lit (authd.pass), jamais passé en argument.

Quand l'agent est retiré d'un poste, loco retire aussi son entrée du manager, s'il y a accès : par SSH, avec une clé qui
lui est propre, que le manager n'autorise qu'à lancer `manage_agents -r <numéro>` (commande imposée dans authorized_keys,
seuls les chiffres reçus sont gardés). La clé d'hôte du manager est confirmée par un administrateur avant tout échange.
"""
import gzip
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request

from flask import abort, flash, render_template, request, send_file, session

from . import app, config, features, fleet, homeassistant, scope
from .auth import audit, can, login_required, require
from .db import connect, db
from .i18n import _, n_
from .util import back, sealer

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
KEY_DIR = os.path.join(config.STATE, "wazuh")
KEY_PATH = os.path.join(KEY_DIR, "id_ed25519")
MANAGE_AGENTS = "/var/ossec/bin/manage_agents"
PROBE_ID = "99999999"                       # numéro qu'aucun agent ne porte : l'essai de l'accès ne retire rien
SHELL_MARK = "loco-shell-ouvert"            # sans chiffre : écarté par la commande imposée, affiché par un shell complet
AGENT_ID_RE = re.compile(r"[0-9]{1,8}")


# --------------------------------------------------------------------------- réglages

_seal, _open = sealer(b"loco-wazuh-v1")          # secrets scellés avec la clé du serveur


def load(conn=None):
    """Réglages effectifs : manager, groupe par défaut, version de l'agent, mot de passe d'enrôlement (déchiffré)."""
    cfg = {"manager": "", "group": "", "version": "", "password": "", "ssh_user": "root", "ssh_port": "22", "host_key": ""}
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
    version = str(raw.get("version") or "").lstrip("v")
    if raw.get("role") == "manager":            # le poste est le manager Wazuh lui-même : pas d'agent à y poser
        return {"installed": False, "role": "manager", "version": version if VERSION_RE.fullmatch(version) else "",
                "active": bool(raw.get("active"))}
    if not raw.get("installed"):
        return {"installed": False}
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


def stored(dev):
    """État gardé pour le poste (dernier remonté), ou None."""
    try:
        state = json.loads(dev["wazuh"]) if dev["wazuh"] else None
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def is_server(dev):
    """Le poste est le manager Wazuh (d'après ce qu'il remonte) : loco n'y pose ni n'y retire d'agent."""
    return (stored(dev) or {}).get("role") == "manager"


def supported(dev):
    return dev["os_family"] in FAMILIES


def agent_ready(dev):
    return fleet.version_key(dev["agent_version"]) >= AGENT_VERSION


def view(dev, cfg=None):
    """État de l'agent Wazuh d'un poste, prêt à afficher : niveau (ok, warn, err, off), libellé, précision, actions."""
    cfg = cfg or load()
    state = stored(dev)
    out = {"state": state, "level": "off", "label": _("Inconnu"), "detail": "", "installed": False, "outdated": False, "server": False}
    if not agent_ready(dev):
        out["detail"] = _("l'agent loco du poste doit d'abord se mettre à jour")
        return out
    if state is None:
        out["detail"] = _("pas encore remonté par le poste")
        return out
    if state.get("role") == "manager":
        out.update(server=True, version=state.get("version", ""), label=_("Manager Wazuh"))
        if state.get("active"):
            out.update(level="ok", detail=_("ce poste est le manager : pas d'agent Wazuh à y poser"))
        else:
            out.update(level="err", detail=_("le service du manager Wazuh ne tourne pas"))
        return out
    if not state.get("installed"):
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
    if is_server(dev):
        return _("Ce poste est le manager Wazuh : loco n'y pose ni n'y retire d'agent.")
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


# --------------------------------------------------------------------------- retrait des entrées du manager

class LinkError(Exception):
    """Accès SSH au manager impossible ou refusé ; le message se montre tel quel."""


def linked(cfg):
    """loco a accès au manager pour y retirer les entrées des postes : clé d'hôte confirmée pour l'adresse réglée."""
    return bool(cfg["manager"] and cfg["host_key"])


def _ssh_host(cfg):
    return cfg["manager"].strip("[]")


def _ssh_port(cfg):
    try:
        port = int(cfg["ssh_port"] or 22)
    except ValueError:
        return 22
    return port if 1 <= port <= 65535 else 22


def public_key():
    """Clé publique de loco pour le manager (distincte de celle de Home Assistant)."""
    return homeassistant.public_key(KEY_PATH)


def authorized_line():
    """Ligne à ajouter aux clés autorisées du compte, sur le manager : avec elle, la clé de loco ne peut que lancer
    manage_agents -r, avec les seuls chiffres de ce qu'elle envoie ; ni shell, ni redirection, ni autre commande."""
    return (f'restrict,command="{MANAGE_AGENTS} -r $(printf %s \\"$SSH_ORIGINAL_COMMAND\\" | tr -cd 0-9)" {public_key()}')


def _explain(output, cfg):
    """Refus ou échec dit clairement : clé non autorisée, clé d'hôte changée, manage_agents absent, hôte injoignable."""
    text = output.strip()
    low = text.lower()
    if "permission denied (" in low or "too many authentication failures" in low:
        return _("Le manager refuse la clé de loco : ajoutez la ligne donnée par loco aux clés autorisées de %(user)s, sur le manager.",
                 user=cfg["ssh_user"])
    if "host key verification failed" in low or "remote host identification has changed" in low:
        return _("La clé d'hôte du manager n'est plus celle qui a été confirmée : relevez-la de nouveau et vérifiez-la avant de la confirmer.")
    if "manage_agents" in low and ("no such file" in low or "not found" in low):
        return _("manage_agents est introuvable à cette adresse : ce n'est pas un manager Wazuh.")
    return (text.splitlines()[-1] if text else _("liaison SSH interrompue"))[:240]


def _ssh(cfg, remote, timeout=40):
    """Lance une commande sur le manager avec la clé de loco, la clé d'hôte confirmée et elle seule : (code, sortie)."""
    if not homeassistant.ssh_binary():
        raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
    if not linked(cfg):
        raise LinkError(_("loco n'a pas accès au manager : la clé d'hôte n'est pas confirmée."))
    public_key()
    host, port = _ssh_host(cfg), _ssh_port(cfg)
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    fd, known = tempfile.mkstemp(prefix="known-", dir=KEY_DIR)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"{homeassistant._pattern(host, port)} {cfg['host_key']}\n")
        argv = (homeassistant._base(host, port, cfg["ssh_user"], known, key=KEY_PATH)
                + ["-o", "StrictHostKeyChecking=yes", "-T", "--", host, remote])
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise LinkError(_("Le manager n'a pas répondu dans le délai (%(n)s s).", n=timeout)) from e
        except OSError as e:
            raise LinkError(str(e)[:200]) from e
    finally:
        try:
            os.unlink(known)
        except OSError:
            pass
    return done.returncode, done.stdout.decode("utf-8", "replace")


def _absent(output):
    """Le manager n'a pas (ou plus) cette entrée : numéro inconnu, ou aucun agent inscrit du tout."""
    low = output.lower()
    return "9011" in output or "agent id not found" in low or "no agent available" in low


def check(cfg):
    """Essaie l'accès sans rien retirer (numéro qu'aucun agent ne porte) : (niveau, message). LinkError en cas d'échec."""
    _code, output = _ssh(cfg, f"echo {SHELL_MARK}; {MANAGE_AGENTS} -r {PROBE_ID}")
    if not _absent(output):
        raise LinkError(_explain(output, cfg))
    if SHELL_MARK in output:
        return "warn", _("Accès établi, mais la clé de loco ouvre un shell complet sur le manager : remplacez sa ligne par celle "
                         "que donne loco, qui ne permet que le retrait d'un agent.")
    return "ok", _("Accès établi : loco peut retirer un agent du manager, et rien d'autre.")


def unenroll(cfg, agent_id):
    """Retire l'entrée d'un agent du manager : message du résultat. LinkError si l'accès échoue ou si rien n'est sûr."""
    if not AGENT_ID_RE.fullmatch(agent_id) or int(agent_id) == 0:
        raise LinkError(_("numéro d'agent Wazuh invalide"))
    _code, output = _ssh(cfg, f"{MANAGE_AGENTS} -r {agent_id}")
    if re.search(r"Agent '0*%s' removed" % int(agent_id), output):
        return _("Entrée %(id)s retirée du manager Wazuh.", id=agent_id)
    if _absent(output):
        return _("Entrée %(id)s déjà absente du manager Wazuh.", id=agent_id)
    raise LinkError(_explain(output, cfg))


def _append(conn, cmd_id, line):
    conn.execute("UPDATE device_commands SET output = output || CASE WHEN output = '' THEN '' ELSE char(10) END || ? WHERE id=?",
                 (line, cmd_id))


def removed(conn, dev, cmd, reported):
    """Compte rendu d'un retrait réussi de l'agent Wazuh : renvoie la tâche de retrait de l'entrée du manager, ou None
    (la raison s'ajoute alors à la sortie de la commande). L'entrée est lue dans l'état gardé AVANT ce compte rendu.
    Prudence : rien n'est retiré si le poste n'était pas relié au manager réglé, ou s'il dit avoir encore l'agent
    (sur l'hôte même du manager, un agent loco ancien remontait le premier agent inscrit comme identité)."""
    try:
        params = json.loads(cmd["params"] or "{}")
    except ValueError:
        return None
    if cmd["action"] != "wazuh" or params.get("op") != "remove":
        return None
    state, cfg = stored(dev) or {}, load(conn)
    agent_id = str(state.get("id") or "")
    now = clean_state(reported) or {}
    if not state.get("installed") or not AGENT_ID_RE.fullmatch(agent_id) or int(agent_id) == 0:
        note = _("Aucune entrée connue chez Wazuh pour ce poste : rien à retirer du manager.")
    elif now.get("installed") or now.get("role") or not isinstance(reported, dict):
        note = _("L'agent Wazuh est encore présent sur le poste : son entrée reste sur le manager.")
    elif not cfg["manager"] or (state.get("manager") or "").lower() != cfg["manager"].lower():
        note = _("Le poste était relié à %(m)s, pas au manager réglé : son entrée n'y a pas été retirée.", m=state.get("manager") or "?")
    elif not linked(cfg):
        note = _("Entrée %(id)s laissée sur le manager : loco n'y a pas accès (page Wazuh, « Retrait des entrées du manager »).", id=agent_id)
    else:
        return {"cmd": cmd["id"], "agent": agent_id, "name": str(state.get("name") or ""), "host": dev["hostname"],
                "user": cmd["created_by"]}
    _append(conn, cmd["id"], note)
    return None


def unenroll_soon(job):
    """Retrait de l'entrée du manager en arrière-plan : le compte rendu de l'agent n'attend pas la liaison SSH."""
    threading.Thread(target=_unenroll, args=(job,), name="loco-wazuh-unenroll", daemon=True).start()


def _unenroll(job):
    conn = connect()
    try:
        cfg = load(conn)
        try:
            line = unenroll(cfg, job["agent"])
        except LinkError as e:
            line = _("Entrée %(id)s non retirée du manager : %(e)s", id=job["agent"], e=str(e))
        _append(conn, job["cmd"], line)
        conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?, ?, '', 'parc.wazuh.desenrolement', ?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), job["user"] or "loco",
                      f"{job['host']} — agent {job['agent']} {job['name']} : {line}"[:500]))
        conn.commit()
    finally:
        conn.close()


def _pending():
    """Accès au manager en cours de réglage : clé d'hôte relevée, en attente de confirmation (dans la session)."""
    data = session.get("wazuh_pending")
    if not isinstance(data, dict) or time.time() - data.get("at", 0) > 900:
        return None
    return dict(data, fingerprint=homeassistant.fingerprint(data["key"]))


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
    rows = []
    for dev in _devices(conn):
        rows.append({"d": dev, "w": view(dev, cfg), "busy": busy(conn, dev["id"]), "ready": agent_ready(dev),
                     "os": fleet.short_os(dev["os_name"]) or str(fleet.FAMILIES.get(dev["os_family"], ""))})
    counts = {"installed": sum(1 for r in rows if r["w"]["installed"]),
              "connected": sum(1 for r in rows if r["w"]["installed"] and r["w"]["level"] == "ok"),
              "total": sum(1 for r in rows if not r["w"]["server"])}
    # Ce qui se voit des postes : la page le suit en direct (installations demandées, états remontés par les agents).
    signature = hashlib.sha256(json.dumps([[cfg["manager"], cfg["version"], cfg["group"], ready(cfg)],
                                           [[r["d"]["id"], r["d"]["label"], r["d"]["hostname"], r["busy"], r["os"],
                                             {k: v for k, v in r["w"].items() if k != "state"}, r["w"].get("state")] for r in rows]],
                                          sort_keys=True, default=str).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}, 200, {"Cache-Control": "no-store"}
    known, checked, version_error = versions(conn) if can("wazuh.manage") or not cfg["version"] else ([], 0, "")
    manage = can("wazuh.manage")
    access = None
    if manage and ready(cfg):
        access = {"linked": linked(cfg), "pending": _pending(), "line": authorized_line(), "ssh": bool(homeassistant.ssh_binary()),
                  "fingerprint": homeassistant.fingerprint(cfg["host_key"].split()[-1]) if linked(cfg) else ""}
    return render_template("wazuh.html", cfg=dict(cfg, password=MASK if cfg["password"] else ""), ready=ready(cfg), rows=rows,
                           counts=counts, versions=known, versions_at=checked, version_error=version_error,
                           packages=cached_packages(), agent_version=".".join(map(str, AGENT_VERSION)) + ".0",
                           linked=linked(cfg), access=access, signature=signature)


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
    host_key = cfg["host_key"] if manager.lower() == cfg["manager"].lower() else ""      # autre adresse : clé d'hôte à reconfirmer
    _save(conn, dict(cfg, manager=manager, group=group, version=version, password=password, host_key=host_key))
    # Le mot de passe n'est jamais écrit au journal : seulement le fait qu'il a changé.
    audit("parc.wazuh.reglages", f"manager {manager}, agent {version}{f', groupe {group}' if group else ''}"
          + (", mot de passe d'enrôlement modifié" if "password" in changed else ""))
    if "version" in changed:
        cleanup_packages(conn, version)
    flash(_("Réglages Wazuh enregistrés.") if host_key or not cfg["host_key"] else
          _("Réglages Wazuh enregistrés. Le manager a changé d'adresse : confirmez de nouveau sa clé d'hôte pour que loco y retire les entrées."), "ok")
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


@app.route("/fleet/wazuh/access", methods=["POST"])
@login_required
def wazuh_access():
    """Première étape : relever la clé d'hôte que présente le manager, et la montrer pour confirmation."""
    require("wazuh.manage")
    cfg = load()
    username = request.form.get("ssh_user", "").strip()
    try:
        port = int(request.form.get("ssh_port", "22") or 22)
    except ValueError:
        port = 0
    try:
        if not ready(cfg):
            raise LinkError(_("Réglez d'abord l'adresse du manager."))
        if not homeassistant.USER_RE.match(username):
            raise LinkError(_("Nom d'utilisateur SSH invalide."))
        if not 1 <= port <= 65535:
            raise LinkError(_("Port SSH invalide."))
        public_key()
        kind, key = homeassistant.probe_host_key(
            _ssh_host(cfg), port, lambda e: _("Le manager Wazuh ne répond pas en SSH à cette adresse : %(e)s", e=e))
    except (LinkError, homeassistant.LinkError) as e:
        flash(str(e), "error")
        return back("wazuh", _anchor="acces")
    session["wazuh_pending"] = {"manager": cfg["manager"], "user": username, "port": port, "type": kind, "key": key,
                                "at": time.time(), "nonce": secrets.token_hex(8),
                                "same": cfg["host_key"].split()[-1:] == [key] if cfg["host_key"] else None}
    return back("wazuh", _anchor="acces")


@app.route("/fleet/wazuh/access/confirm", methods=["POST"])
@login_required
def wazuh_access_confirm():
    """Seconde étape : l'administrateur a comparé l'empreinte. L'accès est enregistré avec cette clé d'hôte et elle seule,
    puis essayé sans rien retirer."""
    require("wazuh.manage")
    data = _pending()
    session.pop("wazuh_pending", None)
    if request.form.get("cancel") == "1" or data is None:
        return back("wazuh", _anchor="acces")
    if not secrets.compare_digest(request.form.get("nonce", ""), data["nonce"]):
        abort(400)
    conn = db()
    cfg = load(conn)
    if cfg["manager"] != data["manager"]:
        flash(_("L'adresse du manager a changé entre-temps : relevez de nouveau sa clé d'hôte."), "error")
        return back("wazuh", _anchor="acces")
    cfg.update(ssh_user=data["user"], ssh_port=str(data["port"]), host_key=f"{data['type']} {data['key']}")
    _save(conn, cfg)
    audit("parc.wazuh.acces", f"{data['user']}@{cfg['manager']}:{data['port']}, clé d'hôte {data['fingerprint']}")
    _flash_check(cfg)
    return back("wazuh", _anchor="acces")


@app.route("/fleet/wazuh/access/test", methods=["POST"])
@login_required
def wazuh_access_test():
    require("wazuh.manage")
    _flash_check(load())
    return back("wazuh", _anchor="acces")


@app.route("/fleet/wazuh/access/delete", methods=["POST"])
@login_required
def wazuh_access_delete():
    require("wazuh.manage")
    conn = db()
    cfg = load(conn)
    if cfg["host_key"]:
        _save(conn, dict(cfg, host_key=""))
        audit("parc.wazuh.acces", f"accès au manager {cfg['manager']} retiré")
    flash(_("loco n'accède plus au manager. Retirez aussi sa clé des clés autorisées, sur le manager."), "ok")
    return back("wazuh", _anchor="acces")


def _flash_check(cfg):
    try:
        level, message = check(cfg)
    except LinkError as e:
        flash(_("Accès au manager en échec : %(e)s", e=str(e)), "error")
        return
    flash(message, "ok" if level == "ok" else "error")


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
    names = [d["hostname"] for d in devices if not is_server(d) and not busy(conn, d["id"])
             and fleet.queue(d, "wazuh", {"op": "install", "group": group})]
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
    names = [d["hostname"] for d in devices if not is_server(d) and not busy(conn, d["id"]) and fleet.queue(d, "wazuh", {"op": "remove"})]
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
    return {"w": view(dev, cfg), "busy": busy(db(), dev["id"]), "ready": ready(cfg), "group": cfg["group"], "linked": linked(cfg)}


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
