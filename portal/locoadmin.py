"""Administration du serveur (root) : mises à jour du système (et, sur l'appliance Alpine, de loco), réglages, redémarrage.

Module hors du paquet loco : l'agent tourne en root sans charger l'application web (ni base, ni sessions).
  locoadmin.py agent                 service loco-agent : exécute les demandes déposées par le portail
  locoadmin.py run-job ID            opération détachée lancée par l'agent
  locoadmin.py update [check|loco|system|security|reboot|status]   commande loco-update
  locoadmin.py config [show|set CLÉ=VALEUR…]                 commande loco-config

Le portail (compte loco, sans droits root) dépose des demandes dans REQUESTS avec request() ; l'agent les
revalide, lance une opération à la fois et publie l'état (STATUS, JOBS) en lecture seule pour le portail.
L'état de l'agent est dans un dossier appartenant à root : le compte loco ne peut pas y substituer de liens.
"""

import base64
import contextlib
import datetime
import fcntl
import hashlib
import ipaddress
import importlib.util
import json
import os
import pty
import re
import secrets
import select
import shlex
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import termios
import threading
import time
import urllib.parse
import urllib.request
import uuid

ETC = "/etc/loco"
CONF = os.path.join(ETC, "loco.conf")
PREFIX = "/opt/loco"
AGENT_DIR = "/var/lib/loco-agent"
REQUESTS = os.path.join(AGENT_DIR, "requests")
JOBS = os.path.join(AGENT_DIR, "jobs")
STATUS = os.path.join(AGENT_DIR, "status.json")
LOCK = os.path.join(AGENT_DIR, "lock")
HEARTBEAT = os.path.join(AGENT_DIR, "alive")
SOURCES = "/usr/local/src"
STATE = "/var/lib/loco"
HERE = os.path.abspath(__file__)
# OpenRC et les CLI utilisent python -I : charger uniquement le module voisin installé avec ce programme.
_snapshot_spec = importlib.util.spec_from_file_location("locosnapshot", os.path.join(os.path.dirname(HERE), "locosnapshot.py"))
locosnapshot = importlib.util.module_from_spec(_snapshot_spec)
_snapshot_spec.loader.exec_module(locosnapshot)
DEFAULT_REPO = "ShaNouhAr/loco"
GITHUB_RE = re.compile(r"[\w.-]+/[\w.-]+")                            # « owner/repo » sur GitHub
GITLAB_RE = re.compile(r"https://[\w.-]+(?::\d+)?/[\w./-]+/[\w.-]+")  # URL d'un projet GitLab (même interne)


def valid_source(value):
    """Source de mise à jour acceptée : « owner/repo » GitHub, ou l'URL https d'un projet GitLab."""
    value = (value or "").strip()
    return bool(GITLAB_RE.fullmatch(value) if "://" in value else GITHUB_RE.fullmatch(value))
ACTIONS = ("check", "update-loco", "update-system", "update-security", "reboot", "configure", "set-repo", "set-time", "install-qemu-agent", "restore", "set-backup-keep", "delete-backup", "set-shell", "renew-cert", "import-cert", "remove-imported-cert",
           "import-ca", "remove-ca", "ca-csr", "import-enterprise-cert", "set-tls-mode", "set-https-port", "set-portal-url", "set-internal-hosts",
           "ha-enable", "ha-join", "ha-peer", "ha-failover", "ha-disable", "ha-reconfigure")
HA_ACTIONS = ("ha-enable", "ha-join", "ha-peer", "ha-failover", "ha-disable", "ha-reconfigure")
# Opérations qui redémarrent le portail : en haute disponibilité, le contrôle de santé ne doit pas y voir une panne.
HA_MAINTENANCE = ("update-loco", "update-system", "update-security", "configure", "restore", "ha-reconfigure")
TZ_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
# Réservées à l'appliance Alpine (réglages gérés par loco via install.sh) ; ailleurs, install.sh s'en charge.
# La mise à jour de loco et le choix du dépôt GitHub valent pour tout serveur Linux.
APPLIANCE_ACTIONS = ("configure",)
JOB_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
CHECK_EVERY = 86400
RETRY_EVERY = 3600
MAX_REQUEST = 16384

# Mêmes règles qu'install.sh, plus strictes là où loco.conf (relu par le shell) pourrait être détourné.
SIZE_RE = re.compile(r"^[0-9]+[kKmMgGtT]?$")
UPLOAD_RE = re.compile(r"^[0-9]+[kKmMgG]?$")
PROXY_RE = re.compile(r"^http://([A-Za-z0-9._~%!*+,;=:-]+@)?([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?/?$")


def _unscoped(value):
    # Les zones IPv6 ne sont pas utilisables dans les ACL / résolveurs nginx ;
    # ipaddress accepte sinon des suffixes libres qui ne doivent pas atteindre loco.conf.
    if "%" in value:
        raise ValueError("zone IPv6 non prise en charge")
    return True


def _networks(value):
    items = value.split()
    if not items:
        raise ValueError(t("au moins un réseau attendu", "at least one network expected", "se espera al menos una red"))
    try:
        return " ".join(str(ipaddress.ip_network(n, strict=False)) for n in items if _unscoped(n))
    except ValueError:
        raise ValueError(t("réseau invalide (ex. 192.168.1.0/24)", "invalid network (e.g. 192.168.1.0/24)", "red no válida (ej. 192.168.1.0/24)")) from None


def _ips(value):
    items = value.split()
    if not items:
        raise ValueError("au moins une adresse attendue")
    try:
        return " ".join(str(ipaddress.ip_address(i)) for i in items if _unscoped(i))
    except ValueError:
        raise ValueError("adresse IP invalide") from None


def _switch(value):
    if value not in ("0", "1"):
        raise ValueError("0 ou 1 attendu")
    return value


def _cache_size(value):
    if value and not SIZE_RE.fullmatch(value):
        raise ValueError("taille attendue, par exemple 50g (vide : automatique)")
    return value


def _upload(value):
    if not UPLOAD_RE.fullmatch(value):
        raise ValueError("taille attendue, par exemple 8g ou 500m")
    return value


def _count(value):
    if not value.isdigit() or not 1 <= int(value) <= 365:
        raise ValueError("nombre entre 1 et 365 attendu")
    return str(int(value))


def _proxy(value):
    if value and not PROXY_RE.fullmatch(value):
        raise ValueError(t("adresse http://hôte:port attendue (vide : accès direct)", "http://host:port address expected (empty: direct access)", "se espera una dirección http://host:puerto (vacío: acceso directo)"))
    return value


def _onoff(option):
    return lambda v: [f"--{option}" if v == "1" else f"--no-{option}"]


# Réglages modifiables (portail et loco-config) : clé de loco.conf -> (libellé, validation, options d'install.sh).
SETTINGS = {
    "ALLOW": ("Réseaux des postes", _networks, lambda v: ["--allow", v]),
    "ADMIN_ALLOW": ("Réseaux de l'interface", _networks, lambda v: ["--admin-allow", v]),
    "RESOLVERS": ("DNS du cache Windows Update", _ips, lambda v: ["--resolvers", v]),
    "UPSTREAM_PROXY": ("Proxy de sortie", _proxy, lambda v: ["--proxy", v] if v else ["--no-proxy"]),
    "HTTP_ENABLED": ("Accès HTTP des postes", _switch, _onoff("http")),
    "WU_ENABLED": ("Cache Windows Update", _switch, _onoff("wu")),
    "RPM_ENABLED": ("Famille Red Hat (RPM)", _switch, _onoff("rpm")),
    "FLATPAK_ENABLED": ("Flatpak", _switch, _onoff("flatpak")),
    "APT_CACHE_SIZE": ("Cache APT, taille max", _cache_size, lambda v: ["--apt-cache-size", v]),
    "RPM_CACHE_SIZE": ("Cache RPM, taille max", _cache_size, lambda v: ["--rpm-cache-size", v]),
    "FLATPAK_CACHE_SIZE": ("Cache Flathub, taille max", _cache_size, lambda v: ["--flatpak-cache-size", v]),
    "WU_CACHE_SIZE": ("Cache Windows Update, taille max", _cache_size, lambda v: ["--wu-cache-size", v]),
    "MAX_UPLOAD": ("Envoi de fichier, taille max", _upload, lambda v: ["--max-upload", v]),
    "BACKUP_KEEP": ("Sauvegardes conservées", _count, lambda v: ["--backup-keep", v]),
}


# Ports que les navigateurs refusent d'ouvrir (« bad ports » de la norme Fetch) : l'interface y serait inaccessible.
BROWSER_BLOCKED_PORTS = frozenset((
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111,
    113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548,
    554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566,
    6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080))
APP_PORT = 8089
ACNG_PORT = 3142


def ha_configured():
    return os.path.exists(os.path.join(ETC, "ha", "node.json"))


def _port_listening(port):
    """Un service écoute déjà sur ce port TCP, d'après /proc/net (lisible sans droits, propre au conteneur)."""
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f, None)
                for line in f:
                    fields = line.split()
                    if len(fields) > 3 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                        return True
        except (OSError, ValueError):
            continue
    return False


def https_port_params(params, conf=None):
    """Nouveau port de l'interface, ou fermeture des anciens ; revalidé par l'agent (ValueError sinon)."""
    conf = read_conf() if conf is None else conf
    p = params or {}
    if ha_configured():
        raise ValueError(t("quittez d'abord la haute disponibilité : les deux serveurs partagent le même port",
                           "leave high availability first: both servers share the same port",
                           "salga primero de la alta disponibilidad: ambos servidores comparten el mismo puerto"))
    old = conf.get("HTTPS_OLD_PORTS", "").split()
    if str(p.get("close", "")) == "1":
        if not old:
            raise ValueError(t("aucun ancien port n'est ouvert", "no old port is open", "no hay ningún puerto antiguo abierto"))
        return {"close": "1"}
    value = str(p.get("port", "")).strip()
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(t("port entre 1 et 65535 attendu", "port between 1 and 65535 expected", "se espera un puerto entre 1 y 65535"))
    port = int(value)
    if str(port) == conf.get("HTTPS_PORT", "8443"):
        raise ValueError(t(f"l'interface utilise déjà le port {port}", f"the interface already uses port {port}", f"la interfaz ya usa el puerto {port}"))
    if port in BROWSER_BLOCKED_PORTS:
        raise ValueError(t(f"les navigateurs refusent d'ouvrir le port {port}", f"browsers refuse to open port {port}", f"los navegadores se niegan a abrir el puerto {port}"))
    taken = {ACNG_PORT: t("cache APT", "APT cache", "caché APT"), APP_PORT: t("application loco", "loco application", "aplicación loco")}
    if conf.get("HTTP_ENABLED", "1") != "0":
        taken[int(conf.get("PORTAL_PORT", "8088") or 8088)] = t("accès HTTP des postes", "HTTP access for machines", "acceso HTTP de los equipos")
    if conf.get("WU_ENABLED", "1") != "0":
        taken.update({80: "Windows Update", 443: "Windows Update", int(conf.get("WU_PROXY_PORT", "3128") or 3128): "Windows Update"})
    if port in taken:
        raise ValueError(t(f"le port {port} sert déjà à loco ({taken[port]})", f"port {port} is already used by loco ({taken[port]})", f"el puerto {port} ya lo usa loco ({taken[port]})"))
    if str(port) not in old and _port_listening(port):
        raise ValueError(t(f"le port {port} est déjà utilisé par un autre service", f"port {port} is already used by another service", f"el puerto {port} ya lo usa otro servicio"))
    return {"port": str(port)}


def read_conf(path=None):
    """Réglages de l'installation (KEY=VALEUR ou KEY="VALEUR", écrits par install.sh)."""
    out = {}
    try:
        with open(path or CONF) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                try:
                    parts = shlex.split(value)
                except ValueError:
                    continue
                out[key] = parts[0] if parts else ""
    except OSError:
        pass
    return out


def validate(changes, current=None):
    """Changements validés et normalisés (ceux identiques à la configuration actuelle sont écartés)."""
    if not isinstance(changes, dict):
        raise ValueError(t("réglages attendus", "settings expected", "se esperan ajustes"))
    current = read_conf() if current is None else current
    clean = {}
    for key, value in changes.items():
        if key not in SETTINGS:
            raise ValueError(t(f"réglage inconnu : {key}", f"unknown setting: {key}", f"ajuste desconocido: {key}"))
        label, check, _ = SETTINGS[key]
        try:
            value = check(str(value).strip())
        except ValueError as e:
            raise ValueError(f"{label} : {e}") from None
        if value != current.get(key, ""):
            clean[key] = value
    return clean


def _ha():
    """Haute disponibilité (portal/locoha.py), importée à la demande : locoadmin tourne en mode isolé (python -I)."""
    here = os.path.dirname(HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    import locoha
    return locoha


def ha_params(action, params):
    """Paramètres d'une opération de haute disponibilité, revalidés par l'agent (ValueError sinon)."""
    p = params or {}
    if action == "ha-enable":
        vip = str(p.get("vip", "")).strip()
        if not re.fullmatch(r"[0-9a-fA-F:.]{3,45}(/[0-9]{1,3})?", vip):
            raise ValueError("IP virtuelle invalide")
        return {"vip": vip}
    if action == "ha-join":
        primary, code = str(p.get("primary", "")).strip()[:200], str(p.get("code", "")).strip()
        if not re.fullmatch(r"(https?://)?[0-9a-fA-F:.\[\]]{3,60}(:[0-9]{1,5})?/?", primary) or not re.fullmatch(r"LHA-[A-Za-z0-9_-]{20,64}", code):
            raise ValueError("adresse du principal ou code d'appairage invalide")
        return {"primary": primary, "code": code}
    if action == "ha-peer":
        address, name = str(p.get("address", "")).strip(), str(p.get("name", ""))[:63]
        if not re.fullmatch(r"[0-9a-fA-F:.]{3,45}", address):
            raise ValueError("adresse du secours invalide")
        return {"address": address, "name": name if re.fullmatch(r"[A-Za-z0-9.-]{0,63}", name) else ""}
    return {}


def current_version():
    try:
        with open(os.path.join(PREFIX, "portal", "loco", "config.py")) as f:
            m = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _lang():
    """Langue du serveur (variables d'environnement, sinon fichier de locale) : les journaux d'opération s'écrivent
    dans la langue du serveur ; l'état renvoyé au portail est, lui, traduit à l'affichage dans la langue de chacun."""
    value = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    if not value:
        for path in ("/etc/default/locale", "/etc/locale.conf"):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        for key in ("LC_ALL=", "LC_MESSAGES=", "LANG="):
                            if line.strip().startswith(key):
                                value = line.strip()[len(key):].strip().strip('"')
                                break
                        if value:
                            break
            except OSError:
                pass
            if value:
                break
    value = value.lower()
    return "fr" if value.startswith("fr") else "es" if value.startswith("es") else "en"


LANG = _lang()


viewer_lang = None      # portail : langue de la personne connectée, pour les refus affichés sur sa page


def t(fr, en, es=None):
    """Texte d'un journal d'opération, dans la langue du serveur (anglais par défaut)."""
    lang = (viewer_lang() if viewer_lang else "") or LANG
    if lang == "fr":
        return fr
    if lang == "es" and es is not None:
        return es
    return en


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _proxies():
    proxy = read_conf().get("UPSTREAM_PROXY", "")
    return {"http": proxy, "https": proxy} if proxy else {}


def _system_env():
    env = os.environ.copy()
    proxy = read_conf().get("UPSTREAM_PROXY", "")
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(key, None)
    if proxy:
        env.update(http_proxy=proxy, https_proxy=proxy)
    env.update(no_proxy="localhost,127.0.0.1,::1", NO_PROXY="localhost,127.0.0.1,::1")
    return env


def _get(url, timeout=15, accept="application/vnd.github+json"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(_proxies()))
    req = urllib.request.Request(url, headers={"User-Agent": f"loco/{current_version()}", "Accept": accept})
    return opener.open(req, timeout=timeout)


def source():
    """Source des mises à jour de loco (réglage loco.conf GITHUB_REPO, défaut DEFAULT_REPO) : « owner/repo »
    GitHub, ou l'URL d'un projet GitLab."""
    configured = read_conf().get("GITHUB_REPO", "").strip()
    return configured if valid_source(configured) else DEFAULT_REPO


def _check_version(version, tag):
    if not re.fullmatch(r"[0-9]+(\.[0-9]+){1,3}", version):
        raise ValueError(t(f"version publiée inattendue : {tag}", f"unexpected published version: {tag}", f"versión publicada inesperada: {tag}"))


def latest_release():
    src = source()
    return _gitlab_release(src) if "://" in src else _github_release(src)


def _github_release(repo):
    with _get(f"https://api.github.com/repos/{repo}/releases/latest") as r:
        data = json.load(r)
    version = data["tag_name"].lstrip("v")
    _check_version(version, data["tag_name"])
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    return {"version": version, "tag": data["tag_name"], "url": data.get("html_url", ""), "published": data.get("published_at", ""),
            "notes": (data.get("body") or "")[:6000],
            "archive": assets.get(f"loco-{version}.tar.gz", ""), "sums": assets.get("SHA256SUMS", "")}


def _gitlab_release(url):
    parts = urllib.parse.urlsplit(url)
    project = urllib.parse.quote(parts.path.strip("/"), safe="")
    with _get(f"https://{parts.netloc}/api/v4/projects/{project}/releases") as r:
        data = json.load(r)
    if not data:
        raise ValueError(t("aucune version publiée sur ce dépôt GitLab", "no published release on this GitLab project", "ninguna versión publicada en este proyecto GitLab"))
    rel = data[0]
    version = rel["tag_name"].lstrip("v")
    _check_version(version, rel["tag_name"])
    links = {a["name"]: (a.get("direct_asset_url") or a.get("url")) for a in rel.get("assets", {}).get("links", [])}
    return {"version": version, "tag": rel["tag_name"], "url": rel.get("_links", {}).get("self", url),
            "published": rel.get("released_at", ""), "notes": (rel.get("description") or "")[:6000],
            "archive": links.get(f"loco-{version}.tar.gz", ""), "sums": links.get("SHA256SUMS", "")}


CHANGELOG_MAX = 60_000              # nouveautés gardées dans l'état (status.json relu à chaque affichage de la page)
_SECTION_RE = re.compile(r"^## +(\S+)", re.M)


def newer_sections(text, current):
    """Sections « ## X.Y.Z » d'un CHANGELOG.md plus récentes que la version installée (ordre du fichier conservé)."""
    starts = [(m.start(), m.group(1)) for m in _SECTION_RE.finditer(text or "")]
    kept = []
    for i, (start, version) in enumerate(starts):
        if re.fullmatch(r"[0-9]+(\.[0-9]+){1,3}", version) and version_key(version) > version_key(current or "0"):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            kept.append(text[start:end].strip())
    return "\n\n".join(kept)[:CHANGELOG_MAX]


def release_changelog(release):
    """Journal des versions publié avec la nouvelle version (CHANGELOG.md au tag), par la même API que la version
    elle-même : pas de nouveau flux sortant. Chaîne vide si le dépôt n'en a pas ou s'il est injoignable."""
    src, tag = source(), release.get("tag") or "v" + release.get("version", "")
    if "://" in src:
        parts = urllib.parse.urlsplit(src)
        project = urllib.parse.quote(parts.path.strip("/"), safe="")
        url = (f"https://{parts.netloc}/api/v4/projects/{project}/repository/files/CHANGELOG.md/raw"
               f"?ref={urllib.parse.quote(tag, safe='')}")
        accept = "text/plain"
    else:
        url = f"https://api.github.com/repos/{src}/contents/CHANGELOG.md?ref={urllib.parse.quote(tag, safe='')}"
        accept = "application/vnd.github.raw+json"
    with _get(url, accept=accept) as r:
        return r.read(1_000_000).decode("utf-8", "replace")




def _write_json(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_status():
    return _read_json(STATUS)


def _update_status(**values):
    status = read_status()
    status.update(values)
    _write_json(STATUS, status)
    return status


def _run(cmd, log, **kw):
    log("$ " + " ".join(shlex.quote(c) for c in cmd))
    kw.setdefault("env", _system_env())
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          text=True, errors="replace", **kw) as proc:
        for line in proc.stdout:
            log(line.rstrip("\n"))
        return proc.wait()


def check(log=print):
    """Versions disponibles (loco publié sur GitHub, paquets du système) ; résultat gardé dans STATUS."""
    values = {"checked": _now(), "current": current_version(), "repo": source(), "errors": []}
    try:
        values["latest"] = latest_release()
    except (OSError, ValueError, KeyError) as e:
        values["errors"].append({"code": "loco-release", "detail": str(e)})
    latest = values.get("latest") or {}
    if latest and version_key(latest["version"]) > version_key(values["current"] or "0"):
        # Nouveautés de la version disponible, à lire avant d'installer : facultatif, sans erreur bloquante.
        try:
            latest["changelog"] = newer_sections(release_changelog(latest), values["current"])
        except (OSError, ValueError) as e:
            log(t(f"journal des versions de {latest['version']} indisponible : {e}",
                  f"changelog of {latest['version']} unavailable: {e}",
                  f"historial de versiones de {latest['version']} no disponible: {e}"))
    values.update(system_state())
    # État du système publié tout de suite : la liste des paquets peut prendre plusieurs minutes (miroirs lents).
    _update_status(**{k: v for k, v in values.items() if k not in ("checked", "errors")})
    try:
        packages = pending_packages()
        values.update(packages=packages, system_updates=[p["name"] for p in packages],
                      security_count=sum(1 for p in packages if p["security"]), system_error=False)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        values["errors"].append({"code": "system-packages", "detail": str(e)})
        values["system_error"] = True
    status = _update_status(**values)
    latest = status.get("latest", {}).get("version", "")
    os_label = status["os"] or t("Système", "System", "Sistema")
    log(t(f"loco installé : {status['current']} ; dernière version publiée : {latest or 'inconnue'}",
          f"loco installed: {status['current']} ; latest published version: {latest or 'unknown'}",
          f"loco instalado: {status['current']} ; última versión publicada: {latest or 'desconocida'}"))
    count = len(status.get("system_updates", []))
    log(t(f"{os_label}, paquets à mettre à jour : {count}", f"{os_label}, packages to update: {count}",
          f"{os_label}, paquetes por actualizar: {count}")
        + (t(f", dont correctifs de sécurité : {status.get('security_count', 0)}",
             f", of which security fixes: {status.get('security_count', 0)}",
             f", de los cuales parches de seguridad: {status.get('security_count', 0)}") if status["security_supported"] else ""))
    for reason in status["reboot_reasons"]:
        log(t("Redémarrage nécessaire : ", "Reboot required: ", "Reinicio necesario: ") + _reason_log(reason))
    if status["stale_services"]:
        log(t("Services qui utilisent d'anciennes bibliothèques : ", "Services using old libraries: ",
              "Servicios que usan bibliotecas antiguas: ") + ", ".join(sorted(status["stale_services"])))
    for e in status["errors"]:
        log(_error_log(e))
    return not status["errors"]


def _error_log(e):
    """Un message d'erreur d'état en toutes lettres, pour le journal (langue du serveur)."""
    if isinstance(e, str):  # État conservé avant les messages structurés.
        return e
    if e.get("code") == "loco-release":
        return t(f"Version publiée de loco introuvable : {e['detail']}",
                 f"Published loco version not found: {e['detail']}",
                 f"Versión publicada de loco no encontrada: {e['detail']}")
    if e.get("code") == "system-packages":
        return t(f"Paquets du système : {e['detail']}.", f"System packages: {e['detail']}.",
                 f"Paquetes del sistema: {e['detail']}.")
    return e.get("detail", "")


# --------------------------------------------------------------------------- paquets du système

# Gestionnaire de paquets du serveur : Alpine (appliance), Debian et Ubuntu, Rocky Linux.
ALPINE = os.path.exists("/etc/alpine-release")
BOOT = "/boot"
MODULES = "/lib/modules"
REBOOT_FLAG = "/run/reboot-required"
APT_OPTS = ["-q", "-o", "APT::Update::Error-Mode=any", "-o", "DPkg::Lock::Timeout=300", "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold"]
APT_INST_RE = re.compile(r"^Inst (\S+) (?:\[([^\]]+)\] )?\((\S+) (.*?) \[[^\]]+\]\)")
RPM_ARCH_RE = re.compile(r"\.(x86_64|aarch64|noarch|i686|ppc64le|s390x)$")
SEVERITIES = ("Critical", "Important", "Moderate", "Low")
# Miroir injoignable : abandon rapide plutôt que plusieurs minutes d'essais par dépôt.
DNF_NET = ["--setopt=timeout=20", "--setopt=retries=2"]
# Paquets à repérer dans la liste : noyau, services que loco fait tourner, socle de sécurité du système.
KEY_PACKAGES = (
    ("noyau", re.compile(r"^(linux-image-.+|linux-(lts|virt|edge)|linux-firmware.*|kernel(-core|-modules.*)?)$")),
    ("loco", re.compile(r"^(squid(-common)?|nginx(-.+)?|apt-cacher-ng|python3(\.[0-9]+)?(-minimal)?|libpython3.*"
                        r"|python3-(flask|gunicorn|werkzeug|jinja2|ldap.*)|py3-(flask|gunicorn|werkzeug|jinja2|ldap.*)"
                        r"|gunicorn|flatpak|ostree|createrepo.*)$")),
    ("socle", re.compile(r"^(openssl(-libs)?|libssl.*|openssh.*|libc6|libc-bin|glibc(-.+)?|musl|sudo|systemd|libsystemd.*"
                         r"|ca-certificates|curl|libcurl.*|gnutls|libgnutls.*|zlib|zlib1g|libz.*|xz-libs|liblzma.*|dbus.*|polkit.*)$")),
)
# Redémarrés après une mise à jour s'ils utilisent encore d'anciennes bibliothèques (services de loco uniquement).
OWN_UNITS = ("loco-portal.service", "nginx.service", "squid.service", "apt-cacher-ng.service")
# Jamais redémarrés à chaud : un redémarrage du serveur s'impose.
CORE_UNITS = ("init", "dbus.service", "dbus-broker.service")


def package_manager():
    if ALPINE:
        return "apk"
    if os.path.exists("/etc/debian_version") and shutil.which("apt-get"):
        return "apt"
    if shutil.which("dnf"):
        return "dnf"
    return ""


def os_name():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return shlex.split(line.split("=", 1)[1])[0]
    except (OSError, ValueError, IndexError):
        pass
    return ""  # inconnu : le portail affiche « Système » traduit


def in_container():
    """Conteneur LXC ou autre : le noyau est celui de l'hôte, il n'est pas mis à jour ici."""
    if os.path.exists("/run/systemd/container"):
        return True
    try:
        with open("/proc/1/environ", "rb") as f:
            return b"container=" in f.read()
    except OSError:
        return False


def _kernel_key(release):
    return tuple(int(x) for x in re.findall(r"[0-9]+", release))


def installed_kernels():
    """Noyaux installés dans /boot, du plus récent au plus ancien (Debian, Ubuntu, Rocky)."""
    try:
        names = [n[len("vmlinuz-"):] for n in os.listdir(BOOT) if n.startswith("vmlinuz-") and "rescue" not in n]
    except OSError:
        return []
    return sorted((n for n in names if re.match(r"^[0-9]", n)), key=_kernel_key, reverse=True)


def _stale_mapping(line):
    if not line.endswith(" (deleted)\n"):
        return False
    parts = line.split(None, 5)
    return len(parts) == 6 and parts[5].startswith(("/usr/", "/lib", "/bin/", "/sbin/"))


def _unit(pid):
    if pid == "1":
        return "init"
    with open(f"/proc/{pid}/cgroup") as f:
        for line in f:
            path = line.rstrip("\n").split(":", 2)[-1]
            for part in reversed(path.split("/")):
                if part.endswith((".service", ".scope")):
                    return "session utilisateur" if part.startswith("session-") else part
    with open(f"/proc/{pid}/comm") as f:
        return f.read().strip()


def stale_services():
    """Services qui utilisent encore des bibliothèques ou programmes remplacés par une mise à jour."""
    found = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/maps") as f:
                if not any(_stale_mapping(line) for line in f):
                    continue
            unit = _unit(pid)
            with open(f"/proc/{pid}/comm") as f:
                found.setdefault(unit, set()).add(f.read().strip())
        except OSError:
            continue
    return {unit: sorted(names) for unit, names in found.items()}


def reboot_reasons(stale=None):
    """Raisons de redémarrage, chacune {« code », …données} : le portail les traduit à l'affichage (langue de
    chaque personne) ; _reason_log() les met en mots pour le journal (langue du serveur)."""
    reasons = []
    container = in_container()
    running = os.uname().release
    if not container:
        installed = installed_kernels()
        if not os.path.isdir(os.path.join(MODULES, running)):
            reasons.append({"code": "kernel-modules"})
        elif installed and _kernel_key(installed[0]) > _kernel_key(running):
            reasons.append({"code": "kernel-newer", "installed": installed[0], "running": running})
    try:
        with open(REBOOT_FLAG + ".pkgs") as f:
            pkgs = sorted({line.strip() for line in f if line.strip()})
    except OSError:
        pkgs = []
    if os.path.exists(REBOOT_FLAG):
        reasons.append({"code": "requested", "packages": ", ".join(pkgs[:6])} if pkgs else {"code": "requested"})
    core = sorted(u for u in (stale or {}) if u in CORE_UNITS)
    if core:
        reasons.append({"code": "stale-core", "units": ", ".join(core)})
    return reasons


def _reason_log(r):
    """Une raison de redémarrage en toutes lettres, pour le journal (langue du serveur)."""
    code = r.get("code")
    if code == "kernel-modules":
        return t("noyau mis à jour (les modules du noyau en cours ont été remplacés)",
                 "kernel updated (the running kernel's modules were replaced)",
                 "kernel actualizado (los módulos del kernel en curso se han reemplazado)")
    if code == "kernel-newer":
        return t(f"noyau {r['installed']} installé, {r['running']} en cours",
                 f"kernel {r['installed']} installed, {r['running']} running",
                 f"kernel {r['installed']} instalado, {r['running']} en curso")
    if code == "requested":
        return (t(f"demandé par {r['packages']}", f"requested by {r['packages']}", f"solicitado por {r['packages']}")
                if r.get("packages") else
                t("demandé par une mise à jour", "requested by an update", "solicitado por una actualización"))
    if code == "stale-core":
        return t(f"d'anciennes bibliothèques restent chargées par {r['units']}",
                 f"old libraries are still loaded by {r['units']}",
                 f"bibliotecas antiguas siguen cargadas por {r['units']}")
    return ""


def reboot_required():
    return bool(reboot_reasons(stale_services()))


def system_state():
    """État du système, sans réseau : noyau, redémarrage, services à relancer (calculé en root)."""
    stale = stale_services()
    container = in_container()
    installed = [] if container else installed_kernels()
    reasons = reboot_reasons(stale)
    return {"os": os_name(), "package_manager": package_manager(), "container": container,
            "kernel": {"running": os.uname().release, "installed": installed[0] if installed else ""},
            "security_supported": package_manager() in ("apt", "dnf"),
            "reboot_required": bool(reasons), "reboot_reasons": reasons,
            "stale_services": {u: n for u, n in stale.items() if u not in CORE_UNITS}}


def _tags(name):
    return [tag for tag, pattern in KEY_PACKAGES if pattern.match(name)]


def _apt_env():
    env = _system_env()
    env.update(DEBIAN_FRONTEND="noninteractive", APT_LISTCHANGES_FRONTEND="none", NEEDRESTART_SUSPEND="1",
               UCF_FORCE_CONFFOLD="1")
    return env


def _apt_pending():
    env = _apt_env()
    if subprocess.run(["apt-get", "update", *APT_OPTS], capture_output=True, timeout=600, env=env).returncode != 0:
        raise ValueError(t("dépôts APT injoignables", "APT repositories unreachable", "repositorios APT inaccesibles"))
    out = subprocess.run(["apt-get", "-s", *APT_OPTS, "upgrade", "--with-new-pkgs"], capture_output=True, text=True,
                         timeout=300, env=env)
    if out.returncode != 0:
        raise ValueError(t("simulation de mise à jour APT impossible", "APT upgrade simulation failed", "no se pudo simular la actualización APT"))
    packages = []
    for line in out.stdout.splitlines():
        m = APT_INST_RE.match(line)
        if m:
            name, current, new, origin = m.groups()
            packages.append({"name": name, "current": current or "", "new": new, "origin": origin[:120],
                             "security": "security" in origin.lower(), "severity": ""})
    return packages


def _rpm_name(nevra):
    base = RPM_ARCH_RE.sub("", nevra)
    return base.rsplit("-", 2)[0] if base.count("-") >= 2 else base


def _dnf_pending():
    env = _system_env()
    r = subprocess.run(["dnf", "-q", "--refresh", *DNF_NET, "check-update"], capture_output=True, text=True, timeout=600, env=env)
    if r.returncode not in (0, 100):
        raise ValueError(t("dépôts DNF injoignables", "DNF repositories unreachable", "repositorios DNF inaccesibles"))
    found = {}
    for line in r.stdout.splitlines():
        if line.startswith("Obsoleting"):
            break
        fields = line.split()
        if len(fields) == 3 and not line[0].isspace() and RPM_ARCH_RE.search(fields[0]):
            name = RPM_ARCH_RE.sub("", fields[0])
            found.setdefault(name, {"name": name, "current": "", "new": fields[1], "origin": fields[2],
                                    "security": False, "severity": ""})
    r = subprocess.run(["dnf", "-q", *DNF_NET, "updateinfo", "list", "--updates", "--security"], capture_output=True, text=True,
                       timeout=300, env=env)
    for line in r.stdout.splitlines():
        fields = line.replace("/", " ").split()
        nevra = next((f for f in reversed(fields) if RPM_ARCH_RE.search(f)), "")
        pkg = found.get(_rpm_name(nevra)) if nevra else None
        if pkg:
            pkg["security"] = True
            # Plusieurs avis pour un paquet : la gravité la plus haute.
            severity = next((s for s in SEVERITIES if s in fields), "")
            if severity and (not pkg["severity"] or SEVERITIES.index(severity) < SEVERITIES.index(pkg["severity"])):
                pkg["severity"] = severity
    if found:
        r = subprocess.run(["rpm", "-q", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\\n", *found], capture_output=True,
                           text=True, timeout=60)
        for line in r.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] in found:
                found[fields[0]]["current"] = fields[1]
    return list(found.values())


def _apk_pending():
    env = _system_env()
    if subprocess.run(["apk", "update", "--quiet"], capture_output=True, timeout=120, env=env).returncode != 0:
        raise ValueError(t("dépôts Alpine injoignables", "Alpine repositories unreachable", "repositorios Alpine inaccesibles"))
    out = subprocess.run(["apk", "list", "--upgradable"], capture_output=True, text=True, timeout=60).stdout
    packages = []
    for line in out.splitlines():
        m = re.match(r"^(\S+)-([^-\s]+-r[0-9]+) .*\[upgradable from: (\S+)\]", line)
        if m:
            name, new, old = m.groups()
            packages.append({"name": name, "current": old[len(name) + 1:] if old.startswith(name + "-") else old,
                             "new": new, "origin": "", "security": False, "severity": ""})
    return packages


def pending_packages():
    """Paquets à mettre à jour, correctifs de sécurité et paquets importants en tête."""
    manager = package_manager()
    if manager == "apt":
        packages = _apt_pending()
    elif manager == "dnf":
        packages = _dnf_pending()
    elif manager == "apk":
        packages = _apk_pending()
    else:
        raise ValueError("gestionnaire de paquets non pris en charge")
    for p in packages:
        p["tags"] = _tags(p["name"])
    return sorted(packages, key=lambda p: (not p["security"], not p["tags"], p["name"]))


def _install(source, args, log):
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"}
    proxy = read_conf().get("UPSTREAM_PROXY", "")
    if proxy:
        env.update(http_proxy=proxy, https_proxy=proxy, no_proxy="localhost,127.0.0.1,::1")
    return _run(["bash", os.path.join(source, "linux", "install.sh"), *args], log, env=env, cwd="/")


def _safe_extract(archive, dest, top):
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            parts = m.name.rstrip("/").split("/")
            if m.name.startswith("/") or ".." in parts or parts[0] != top:
                raise ValueError(t(f"archive refusée : chemin inattendu {m.name}", f"archive refused: unexpected path {m.name}", f"archivo rechazado: ruta inesperada {m.name}"))
            if not (m.isfile() or m.isdir()):
                raise ValueError(t(f"archive refusée : {m.name} n'est ni un fichier ni un dossier", f"archive refused: {m.name} is neither a file nor a directory", f"archivo rechazado: {m.name} no es ni un archivo ni una carpeta"))
        # Extraction explicite : aucun propriétaire, lien ni permission spéciale de l'archive.
        for m in tar.getmembers():
            path = os.path.join(dest, m.name)
            if m.isdir():
                os.makedirs(path, mode=0o755, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
                with tar.extractfile(m) as src, open(path, "xb") as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(path, 0o755 if m.mode & 0o111 else 0o644)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _snapshot():
    """Configuration et base avant toute modification ; dossier root, supprimé après l'opération."""
    os.makedirs(AGENT_DIR, mode=0o755, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rollback-", dir=AGENT_DIR) as work:
        shutil.copy2(CONF, os.path.join(work, "loco.conf"))
        db = os.path.join(STATE, "portal.db")
        if os.path.isfile(db):
            locosnapshot.snapshot(db, os.path.join(work, "portal.db"))
        yield work


def _rollback(work, source, log):
    """Relance les sources précédentes avec la configuration et la base antérieures."""
    log(t("Échec : restauration de la configuration et de la version précédentes.", "Failed: restoring the previous configuration and version.", "Error: restaurando la configuración y la versión anteriores."))
    # Le portail tient la base ouverte : l'arrêter avant de la réécrire, quel que soit le système (l'installation qui
    # suit le relance).
    _run(["rc-service", "loco", "stop"] if ALPINE else ["systemctl", "stop", "loco-portal"], log)
    shutil.copy2(os.path.join(work, "loco.conf"), CONF)
    snapshot_db = os.path.join(work, "portal.db")
    if os.path.isfile(snapshot_db):
        db = os.path.join(STATE, "portal.db")
        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(db + suffix)
        shutil.copyfile(snapshot_db, db)
    rc = _install(source, [], log)
    if rc != 0 and not ALPINE:
        _run(["systemctl", "start", "loco-portal"], log)      # installation en échec avant sa relance : portail remis en service
    log(t("Version précédente rétablie.", "Previous version restored.", "Versión anterior restablecida.") if rc == 0 else t("Retour arrière en échec : intervention nécessaire sur la console.", "Rollback failed: action needed on the console.", "Error en la reversión: se requiere intervención en la consola."))
    return rc == 0


def update_loco(log=print):
    """Dernière version publiée : archive vérifiée (SHA-256), installation, retour arrière en cas d'échec."""
    latest = latest_release()
    current = current_version()
    if version_key(latest["version"]) <= version_key(current):
        log(t(f"loco {current} est à jour.", f"loco {current} is up to date.", f"loco {current} está actualizado."))
        return True
    if not latest["archive"] or not latest["sums"]:
        raise ValueError(f"la version {latest['version']} ne fournit pas loco-{latest['version']}.tar.gz et SHA256SUMS")
    name = f"loco-{latest['version']}"
    target = os.path.join(SOURCES, name)
    previous = read_conf().get("SOURCE_DIR", "")
    if not previous or not os.path.isfile(os.path.join(previous, "linux", "install.sh")):
        raise ValueError(t("sources précédentes introuvables : mise à jour refusée sans possibilité de retour arrière", "previous sources not found: update refused without rollback capability", "fuentes anteriores no encontradas: actualización rechazada sin posibilidad de reversión"))
    if os.path.realpath(previous) == os.path.realpath(target):
        raise ValueError(t("le dossier des sources précédentes correspond à la version cible", "the previous sources directory matches the target version", "la carpeta de fuentes anteriores coincide con la versión de destino"))
    os.makedirs(SOURCES, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loco-update-") as work:
        archive = os.path.join(work, name + ".tar.gz")
        log(t(f"Téléchargement de {latest['archive']}", f"Downloading {latest['archive']}", f"Descargando {latest['archive']}"))
        with _get(latest["archive"], timeout=300) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f)
        with _get(latest["sums"]) as r:
            sums = r.read(65536).decode(errors="replace")
        expected = ""
        for line in sums.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*") == name + ".tar.gz":
                expected = fields[0].lower()
        digest = _sha256(archive)
        if not expected or digest != expected:
            raise ValueError(t("empreinte SHA-256 de l'archive incorrecte : mise à jour refusée", "incorrect archive SHA-256 fingerprint: update refused", "huella SHA-256 del archivo incorrecta: actualización rechazada"))
        log(t(f"Empreinte SHA-256 vérifiée : {digest}", f"SHA-256 fingerprint verified: {digest}", f"Huella SHA-256 verificada: {digest}"))
        unpack = os.path.join(work, "src")
        os.mkdir(unpack)
        _safe_extract(archive, unpack, name)
        if not os.path.isfile(os.path.join(unpack, name, "linux", "install.sh")):
            raise ValueError(t("archive incomplète : linux/install.sh absent", "incomplete archive: linux/install.sh missing", "archivo incompleto: falta linux/install.sh"))
        if os.path.exists(target):
            if not os.path.isfile(os.path.join(target, ".loco-managed")):
                raise ValueError(t(f"dossier existant non géré par loco : {target}", f"existing directory not managed by loco: {target}", f"carpeta existente no gestionada por loco: {target}"))
            shutil.rmtree(target)
        shutil.move(os.path.join(unpack, name), target)
        with open(os.path.join(target, ".loco-managed"), "w"):
            pass
    log(f"Installation de loco {latest['version']} (de {current})")
    with _snapshot() as snapshot:
        try:
            ok = _install(target, [], log) == 0
        except (OSError, subprocess.SubprocessError) as e:
            log(f"Installation interrompue : {e}")
            ok = False
        if not ok:
            _rollback(snapshot, previous, log)
            return False
    log(t(f"loco {latest['version']} installé.", f"loco {latest['version']} installed.", f"loco {latest['version']} instalado."))
    _cleanup_sources(keep={target, previous})
    return True


def _cleanup_sources(keep):
    for entry in os.listdir(SOURCES):
        path = os.path.join(SOURCES, entry)
        if re.fullmatch(r"loco-[0-9.]+", entry) and path not in keep and os.path.isfile(os.path.join(path, ".loco-managed")):
            shutil.rmtree(path, ignore_errors=True)


def _wait_healthy(log):
    for _ in range(60):
        result = subprocess.run(["curl", "-fsk", "--connect-timeout", "2", "--max-time", "5",
                                 "https://127.0.0.1:" + read_conf().get("HTTPS_PORT", "8443") + "/healthz"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7)
        if result.returncode == 0:
            log(t("Portail HTTPS prêt.", "HTTPS portal ready.", "Portal HTTPS listo."))
            return True
        time.sleep(2)
    log(t("Le portail ne répond pas après la mise à jour : consultez /var/log/loco/.", "The portal does not respond after the update: see /var/log/loco/.", "El portal no responde tras la actualización: consulte /var/log/loco/."))
    return False


def update_system(log=print, security_only=False):
    """Paquets du système (tous, ou les seuls correctifs de sécurité), puis relance des services de loco concernés."""
    manager = package_manager()
    if manager == "apk":
        if security_only:
            raise ValueError(t("Alpine ne distingue pas les correctifs de sécurité : mettez tout à jour", "Alpine does not distinguish security fixes: update everything", "Alpine no distingue los parches de seguridad: actualice todo"))
        if _run(["apk", "update", "--no-progress"], log) != 0:
            raise ValueError(t("dépôts Alpine injoignables", "Alpine repositories unreachable", "repositorios Alpine inaccesibles"))
        ok = _run(["apk", "upgrade", "--no-progress"], log) == 0
        # Python, nginx ou les bibliothèques ont pu changer : relancer les services avec les nouvelles versions.
        ok = _run(["rc-service", "loco", "restart"], log) == 0 and ok
    elif manager == "apt":
        env = _apt_env()
        if _run(["apt-get", "update", *APT_OPTS], log, env=env) != 0:
            raise ValueError(t("dépôts APT injoignables", "APT repositories unreachable", "repositorios APT inaccesibles"))
        if security_only:
            names = [p["name"] for p in _apt_pending() if p["security"] and p["current"]]
            if not names:
                log(t("Aucun correctif de sécurité en attente.", "No security fix pending.", "Ningún parche de seguridad pendiente."))
                return True
            cmd = ["apt-get", "-y", *APT_OPTS, "install", "--only-upgrade", *names]
        else:
            cmd = ["apt-get", "-y", *APT_OPTS, "upgrade", "--with-new-pkgs"]
        ok = _run(cmd, log, env=env) == 0
        ok = _restart_stale(log) and ok
    elif manager == "dnf":
        ok = _run(["dnf", "-y", "--refresh", "upgrade", *(["--security"] if security_only else [])], log) == 0
        ok = _restart_stale(log) and ok
    else:
        raise ValueError("gestionnaire de paquets non pris en charge")
    ok = _wait_healthy(log) and ok
    stale = stale_services()
    for reason in reboot_reasons(stale):
        log(t("Redémarrage nécessaire : ", "Reboot required: ", "Reinicio necesario: ") + _reason_log(reason) + ".")
    others = sorted(u for u in stale if u not in CORE_UNITS and u not in OWN_UNITS and u != "loco-agent.service")
    if others:
        log(t("Utilisent encore d'anciennes bibliothèques (redémarrez-les ou redémarrez le serveur) : ", "Still using old libraries (restart them or reboot the server): ", "Aún usan bibliotecas antiguas (reiníchelos o reinicie el servidor): ") + ", ".join(others))
    return ok


def _restart_stale(log):
    """systemd : recharge systemd et relance les services de loco qui utilisent d'anciennes bibliothèques (l'agent en
    dernier). Les autres services sont seulement signalés."""
    stale = stale_services()
    ok = True
    for unit in OWN_UNITS:
        if unit in stale:
            log(t(f"Relance de {unit} (bibliothèques mises à jour).", f"Restarting {unit} (libraries updated).", f"Reiniciando {unit} (bibliotecas actualizadas)."))
            ok = _run(["systemctl", "restart", unit], log) == 0 and ok
    if "init" in stale:
        log(t("systemd utilise d'anciennes bibliothèques : rechargement (systemctl daemon-reexec).", "systemd is using old libraries: reloading (systemctl daemon-reexec).", "systemd usa bibliotecas antiguas: recargando (systemctl daemon-reexec)."))
        ok = _run(["systemctl", "daemon-reexec"], log) == 0 and ok
    if "loco-agent.service" in stale:
        # KillMode=process : l'opération en cours survit à la relance de l'agent.
        _run(["systemctl", "restart", "--no-block", "loco-agent.service"], log)
    return ok


def _reinstall(args, log):
    """Relance install.sh avec ces options ; en cas d'échec, retour à la configuration précédente."""
    source = read_conf().get("SOURCE_DIR", "")
    if not source or not os.path.isfile(os.path.join(source, "linux", "install.sh")):
        raise ValueError(f"sources de loco introuvables (SOURCE_DIR de {CONF})")
    with _snapshot() as snapshot:
        try:
            ok = _install(source, args, log) == 0
        except (OSError, subprocess.SubprocessError) as e:
            log(f"Application interrompue : {e}")
            ok = False
        if not ok:
            _rollback(snapshot, source, log)
        return ok


def configure(changes, log=print):
    clean = validate(changes)
    if not clean:
        log("Aucun changement.")
        return True
    args = []
    for key, value in clean.items():
        args += SETTINGS[key][2](value)
        log(f"{SETTINGS[key][0]} : {value or 'automatique'}")
    return _reinstall(args, log)


def set_https_port(params, log=print):
    """Déplace l'interface sans rien couper : l'ancien port reste servi jusqu'à sa fermeture explicite."""
    conf = read_conf()
    params = https_port_params(params, conf)
    current, old = conf.get("HTTPS_PORT", "8443"), conf.get("HTTPS_OLD_PORTS", "").split()
    if params.get("close"):
        log(t(f"Fermeture de l'ancien port : {' '.join(old)}", f"Closing the old port: {' '.join(old)}", f"Cierre del puerto antiguo: {' '.join(old)}"))
        return _reinstall(["--https-old-ports", ""], log)
    kept = " ".join(p for p in old + [current] if p != params["port"])
    log(t(f"Port de l'interface : {params['port']} (reste ouvert : {kept})", f"Interface port: {params['port']} (stays open: {kept})", f"Puerto de la interfaz: {params['port']} (sigue abierto: {kept})"))
    return _reinstall(["--https-port", params["port"], "--https-old-ports", kept], log)


def _set_conf(key, value):
    """Écrit (ou remplace) KEY="value" dans loco.conf (root) ; install.sh source ce fichier et conserve la valeur."""
    try:
        with open(CONF, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f'{key}="{value}"')
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f'{key}="{value}"')
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, CONF)


def set_repo(params, log=print):
    """Change la source des mises à jour puis revérifie : « owner/repo » GitHub, ou l'URL d'un projet GitLab."""
    new = str((params or {}).get("repo", "")).strip()
    if not valid_source(new):
        raise ValueError(t("source invalide (attendu : « owner/repo » GitHub, ou l'URL d'un projet GitLab)",
                           "invalid source (expected: GitHub \"owner/repo\", or a GitLab project URL)",
                           "origen no válido (se espera: «owner/repo» de GitHub, o la URL de un proyecto GitLab)"))
    _set_conf("GITHUB_REPO", new)
    log(t(f"Source des mises à jour : {new}", f"Update source: {new}", f"Origen de las actualizaciones: {new}"))
    return check(log)


def _valid_tz(tz):
    return bool(TZ_RE.fullmatch(tz)) and os.path.isfile(os.path.join("/usr/share/zoneinfo", tz))


def _is_container():
    """Conteneur (LXC, Docker…) : l'horloge est celle de l'hôte, la synchro NTP ne se règle pas ici."""
    try:
        out = subprocess.run(["systemd-detect-virt", "--container"], capture_output=True, text=True, timeout=3).stdout
        return out.strip() not in ("none", "")
    except (OSError, subprocess.SubprocessError):
        return False


def _ntp_backend():
    """Mécanisme NTP du système : systemd-timesyncd (Debian/Ubuntu classiques) ou chrony (Red Hat, Alpine, Ubuntu récents)."""
    if os.path.exists("/lib/systemd/systemd-timesyncd") or os.path.exists("/usr/lib/systemd/systemd-timesyncd"):
        return "timesyncd"
    if shutil.which("chronyd"):
        return "chrony"
    return ""


def _write_conf_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(content)
    os.chmod(path + ".tmp", 0o644)
    os.replace(path + ".tmp", path)


def _set_timezone(tz, log):
    if not _valid_tz(tz):
        raise ValueError(t(f"fuseau horaire inconnu : {tz}", f"unknown time zone: {tz}", f"zona horaria desconocida: {tz}"))
    if shutil.which("timedatectl"):
        _run(["timedatectl", "set-timezone", tz], log)
    else:                                                 # Alpine/OpenRC : lien + fichier
        try:
            os.remove("/etc/localtime.tmp")
        except OSError:
            pass
        os.symlink(os.path.join("/usr/share/zoneinfo", tz), "/etc/localtime.tmp")
        os.replace("/etc/localtime.tmp", "/etc/localtime")
        with open("/etc/timezone", "w") as f:
            f.write(tz + "\n")
    log(t(f"Fuseau horaire : {tz}", f"Time zone: {tz}", f"Zona horaria: {tz}"))


NTP_HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?")
NTP_MAX_SERVERS = 4
TIMESYNCD_DROPIN = "/etc/systemd/timesyncd.conf.d/loco.conf"


def ntp_servers(text):
    """Serveurs NTP saisis : adresses IPv4, IPv6 ou noms, séparés par des espaces, virgules ou points-virgules."""
    out = []
    for item in re.split(r"[\s,;]+", str(text or "").strip()):
        item = item.strip().strip("[]")
        if not item:
            continue
        try:
            item = str(ipaddress.ip_address(item))
        except ValueError:
            if not NTP_HOST_RE.fullmatch(item):
                raise ValueError(t(f"serveur NTP invalide : {item}", f"invalid NTP server: {item}",
                                   f"servidor NTP no válido: {item}")) from None
            item = item.rstrip(".").lower()
        if item not in out:
            out.append(item)
    if len(out) > NTP_MAX_SERVERS:
        raise ValueError(t(f"au plus {NTP_MAX_SERVERS} serveurs NTP", f"at most {NTP_MAX_SERVERS} NTP servers",
                           f"como máximo {NTP_MAX_SERVERS} servidores NTP"))
    return out


def _chrony_conf():
    for path in ("/etc/chrony/chrony.conf", "/etc/chrony.conf"):
        if os.path.isfile(path):
            return path
    return ""


def _chrony_dropin():
    """(fichier de loco, chrony.conf à compléter d'un « include » ou vide). Debian et Ubuntu lisent un dossier
    « confdir » : le fichier y est déposé. Mais confdir est lu à SA position dans chrony.conf : sur Alpine 3.24, le
    « cmdport 0 » placé plus bas l'emporterait sur le nôtre. Sans confdir (Rocky, AlmaLinux) ou avec un cmdport 0
    après lui, le fichier est donc gardé hors du dossier et inclus à la FIN de chrony.conf, pour avoir le dernier mot."""
    conf = _chrony_conf()
    text = ""
    try:
        if conf:
            with open(conf) as f:
                text = f.read()
    except OSError:
        text = ""
    m = re.search(r"^\s*confdir\s+(\S+)", text, re.M)
    if m and not re.search(r"^\s*cmdport\s+0\b", text[m.end():], re.M):
        return os.path.join(m.group(1), "loco.conf"), ""
    return os.path.join(os.path.dirname(conf) or "/etc", "chrony.loco.conf"), conf


def _drop_stale_confdir_file(conf):
    """Retire le fichier de loco laissé dans confdir par la 2.68 quand il est désormais inclus en fin de fichier."""
    try:
        with open(conf) as f:
            m = re.search(r"^\s*confdir\s+(\S+)", f.read(), re.M)
        stale = os.path.join(m.group(1), "loco.conf") if m else ""
        if stale:
            with open(stale) as f:
                managed = f.readline().startswith("# Géré par loco")
            if managed:
                os.remove(stale)
    except OSError:
        pass


def _configured_ntp():
    """Serveurs actuellement réglés par loco (lus dans son fichier)."""
    paths = [TIMESYNCD_DROPIN] if _ntp_backend() == "timesyncd" else [_chrony_dropin()[0]]
    servers = []
    for path in paths:
        try:
            with open(path) as f:
                for line in f:
                    m = re.match(r"\s*NTP=(.*)", line) or re.match(r"\s*server\s+(\S+)", line)
                    if m:
                        servers += m.group(1).split()
        except OSError:
            pass
    return servers


def _chrony_cmd_blocked():
    """Vrai si chrony.conf désactive le port de commande (« cmdport 0 » : le défaut Alpine de l'appliance). Il faut
    alors le réactiver, sinon même root ne peut pas lire l'état via chronyc (ni port UDP ni socket Unix)."""
    conf = _chrony_conf()
    try:
        with open(conf) as f:
            return bool(re.search(r"^\s*cmdport\s+0\b", f.read(), re.M)) if conf else False
    except OSError:
        return False


def _write_chrony(servers, log):
    """Fichier chrony géré par loco : serveurs saisis (« prefer », prioritaires sur le pool de la distribution) et, si
    besoin, réactivation du port de commande pour que l'agent root puisse lire l'état. Inclus via confdir, ou via un
    « include » ajouté une seule fois à chrony.conf (Rocky/AlmaLinux, qui n'ont pas de confdir)."""
    dropin, conf = _chrony_dropin()
    header = "cmdport 323\n" if _chrony_cmd_blocked() else ""       # localhost seulement (défaut chrony)
    _write_conf_file(dropin, "# Géré par loco : serveurs NTP et accès à l'état pour l'agent root\n" + header
                     + "".join(f"server {s} iburst prefer\n" for s in servers))
    if conf:
        _drop_stale_confdir_file(conf)
        with open(conf) as f:
            text = f.read()
        if not re.search(r"^\s*include\s+" + re.escape(dropin) + r"\s*$", text, re.M):
            with open(conf, "a") as f:
                f.write(("" if text.endswith("\n") else "\n") + f"# Réglages NTP de loco\ninclude {dropin}\n")
            log(t(f"{conf} : inclusion de {dropin}", f"{conf}: includes {dropin}", f"{conf}: incluye {dropin}"))


NTP_ONLY_MARK = "#loco-ntp-only# "
DISTRO_SOURCE_RE = re.compile(r"^(\s*)(pool|server|peer|sourcedir)\b", re.M)


def _chrony_only(on, log):
    """« Uniquement ces serveurs » : commente (marque réversible) les sources de la distribution dans chrony.conf —
    pool, server, peer, sourcedir (serveurs reçus par DHCP) — ou les rétablit."""
    conf = _chrony_conf()
    if not conf:
        return
    with open(conf) as f:
        text = f.read()
    if on:
        new = DISTRO_SOURCE_RE.sub(lambda m: NTP_ONLY_MARK + m.group(0), text)
    else:
        new = text.replace(NTP_ONLY_MARK, "")
    if new != text:
        _write_conf_file(conf, new)
        log(t(f"{conf} : sources de la distribution {'désactivées' if on else 'rétablies'}",
              f"{conf}: distribution sources {'disabled' if on else 'restored'}",
              f"{conf}: fuentes de la distribución {'desactivadas' if on else 'restablecidas'}"))


def _ntp_only_active():
    if _ntp_backend() == "timesyncd":
        try:
            with open(TIMESYNCD_DROPIN) as f:
                return bool(re.search(r"^FallbackNTP=\s*$", f.read(), re.M))
        except OSError:
            return False
    conf = _chrony_conf()
    try:
        with open(conf) as f:
            return NTP_ONLY_MARK in f.read()
    except OSError:
        return False


def _ensure_chrony_readable(log=lambda *_: None):
    """Au démarrage de l'agent : sur l'appliance (chrony + cmdport 0) l'état est illisible même pour root. Si chronyc
    ne répond pas alors que c'est dû à cmdport 0, réactiver le port de commande puis recharger chronyd (une seule fois :
    aux démarrages suivants chronyc répond et rien n'est fait)."""
    if _is_container() or _ntp_backend() != "chrony":
        return
    if _quiet(["chronyc", "-n", "-c", "tracking"])[0] == 0 or not _chrony_cmd_blocked():
        return
    _write_chrony(_configured_ntp(), log)
    if shutil.which("systemctl"):
        service = "chronyd" if os.path.exists("/usr/lib/systemd/system/chronyd.service") else "chrony"
        _run(["systemctl", "reload-or-restart", service], log)
    else:
        _run(["rc-service", "chronyd", "restart"], log)
    log(t("Accès à l'état de l'heure réactivé (chrony).", "Time state access re-enabled (chrony).",
          "Acceso al estado de la hora reactivado (chrony)."))


def _set_ntp(enabled, servers, log, only=False):
    """Client NTP du serveur, par timesyncd ou chrony. Les serveurs saisis sont prioritaires sur ceux de la distribution."""
    if _is_container():
        log(t("Conteneur : l'horloge est gérée par l'hôte, synchronisation NTP ignorée.",
              "Container: the clock is managed by the host, NTP synchronisation skipped.",
              "Contenedor: el reloj lo gestiona el anfitrión, sincronización NTP omitida."))
        return True
    backend, ok = _ntp_backend(), True
    if backend == "timesyncd":
        if servers:
            # FallbackNTP vide : jamais les serveurs de secours compilés dans systemd (« uniquement ces serveurs »).
            _write_conf_file(TIMESYNCD_DROPIN, "# Géré par loco\n[Time]\nNTP=" + " ".join(servers) + "\n"
                             + ("FallbackNTP=\n" if only else ""))
        elif os.path.exists(TIMESYNCD_DROPIN):
            os.remove(TIMESYNCD_DROPIN)
        ok = _run(["timedatectl", "set-ntp", "true" if enabled else "false"], log) == 0
        if enabled:
            ok = _run(["systemctl", "restart", "systemd-timesyncd"], log) == 0 and ok
    elif backend == "chrony":
        _write_chrony(servers, log)
        _chrony_only(bool(only and servers), log)            # sans serveur saisi, jamais sans source
        if shutil.which("systemctl"):
            if shutil.which("timedatectl"):
                ok = _run(["timedatectl", "set-ntp", "true" if enabled else "false"], log) == 0
            service = "chronyd" if os.path.exists("/usr/lib/systemd/system/chronyd.service") else "chrony"
            ok = _run(["systemctl", "restart" if enabled else "stop", service], log) == 0 and ok
        else:                                             # Alpine/OpenRC (appliance) : réglage gardé au redémarrage
            _run(["rc-update", "add" if enabled else "del", "chronyd", "default"], log)
            ok = _run(["rc-service", "chronyd", "restart" if enabled else "stop"], log) == 0
    else:
        log(t("Synchronisation NTP non gérée sur ce système.", "NTP synchronisation not managed on this system.", "Sincronización NTP no gestionada en este sistema."))
        return False
    state = t("activée", "enabled", "activada") if enabled else t("désactivée", "disabled", "desactivada")
    via = f" ({', '.join(servers)})" if servers and enabled else ""
    log(t(f"Synchronisation NTP {state}{via}.", f"NTP synchronisation {state}{via}.", f"Sincronización NTP {state}{via}."))
    return ok


def _current_tz():
    try:
        return os.readlink("/etc/localtime").split("zoneinfo/", 1)[-1]
    except OSError:
        try:
            with open("/etc/timezone") as f:
                return f.read().strip()
        except OSError:
            return ""


def set_time(params, log=print):
    """Synchronisation NTP puis fuseau horaire : le NTP est appliqué d'abord et ne dépend pas du fuseau."""
    params = params or {}
    tz = str(params.get("timezone", "")).strip()
    servers = ntp_servers(params.get("ntp_server", ""))
    enabled = str(params.get("ntp", "")) in ("1", "true", "on", "yes")
    only = str(params.get("ntp_only", "")) in ("1", "true", "on", "yes")
    ok = _set_ntp(enabled, servers, log, only=only)
    error = None
    if tz and tz != _current_tz():
        try:
            _set_timezone(tz, log)
        except (OSError, ValueError) as e:
            error = e
    write_time_status()
    if error:
        raise ValueError(t(f"synchronisation NTP appliquée, mais fuseau horaire refusé : {error}",
                           f"NTP synchronisation applied, but time zone refused: {error}",
                           f"sincronización NTP aplicada, pero zona horaria rechazada: {error}"))
    return ok


# --------------------------------------------------------------------------- état de l'heure (lu par le portail)
# Mesuré en root (chronyc n'est pas accessible à l'utilisateur du portail : « cmdport 0 » sur Alpine et Debian) et
# écrit dans un fichier lisible par le portail ; rafraîchi par l'agent toutes les 15 s et après chaque réglage.
TIME_STATUS = os.path.join(AGENT_DIR, "time.json")
CHRONY_STATES = {"*": "used", "+": "ok", "-": "ok", "?": "unreachable", "x": "falseticker", "~": "unstable"}


def _quiet(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def time_status():
    st = {"at": time.time(), "container": _is_container(), "backend": _ntp_backend(), "enabled": False,
          "synchronized": False, "source": "", "stratum": None, "offset_ms": None, "servers": _configured_ntp(),
          "sources": [], "only": False, "configured": []}
    if st["container"]:
        return st
    if st["backend"] == "timesyncd" or (shutil.which("timedatectl") and st["backend"] != "chrony"):
        code, out = _quiet(["timedatectl", "show", "-p", "NTP", "-p", "NTPSynchronized"])
        values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        st["enabled"], st["synchronized"] = values.get("NTP") == "yes", values.get("NTPSynchronized") == "yes"
        if st["enabled"]:                                 # inutile (et lent) d'interroger la source si timesyncd est arrêté
            code, out = _quiet(["timedatectl", "show-timesync", "-p", "ServerAddress", "-p", "ServerName"])
            values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            st["source"] = values.get("ServerAddress") or values.get("ServerName", "")
        used = {values.get("ServerAddress", "").lower(), values.get("ServerName", "").lower()} if st["synchronized"] else set()
        st["only"] = _ntp_only_active()
        st["configured"] = [{"name": s, "state": "used" if s.lower() in used else "unknown"} for s in st["servers"]]
        return st
    if st["backend"] == "chrony":
        code, out = _quiet(["chronyc", "-n", "-c", "tracking"])
        if code != 0 or not out.strip():
            return st                                     # chronyd arrêté : synchronisation désactivée
        st["enabled"] = True
        f = out.strip().split(",")
        # -c tracking : ID de référence, adresse, strate, heure de référence, écart du système (s), …, état de saut
        ref, addr = f[0], f[1] if len(f) > 1 else ""
        st["stratum"] = int(f[2]) if len(f) > 2 and f[2].isdigit() else None
        try:
            st["offset_ms"] = round(float(f[4]) * 1000, 3)
        except (IndexError, ValueError):
            pass
        st["synchronized"] = ref not in ("00000000", "") and (f[-1].strip() if f else "") == "Normal"
        st["source"] = addr if st["synchronized"] else ""
        code, out = _quiet(["chronyc", "-n", "-c", "sources"])
        rows = [line.split(",") for line in out.splitlines()]
        # -N : nom tel qu'il a été saisi (même ordre), pour relier chaque serveur saisi à son état.
        named = [line.split(",") for line in _quiet(["chronyc", "-N", "-c", "sources"])[1].splitlines()]
        if len(named) != len(rows):
            named = rows
        for c, n in zip(rows, named):
            if len(c) >= 6 and c[0] in ("^", "="):
                st["sources"].append({"address": c[2], "name": n[2] if len(n) > 2 else c[2],
                                      "state": CHRONY_STATES.get(c[1], "unknown"),
                                      "stratum": int(c[3]) if c[3].isdigit() else None, "reach": c[5]})
    st["only"] = _ntp_only_active()
    states = {}
    for src in st["sources"]:
        for key in (src["name"].lower(), src["address"].lower()):
            states.setdefault(key, src["state"])
    st["configured"] = [{"name": s, "state": states.get(s.lower(), "unknown")} for s in st["servers"]]
    return st


def write_time_status():
    try:
        os.makedirs(AGENT_DIR, exist_ok=True)
        tmp = TIME_STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(time_status(), f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, TIME_STATUS)
    except OSError:
        pass


def install_qemu_agent(log=print):
    """Installe et active l'agent invité QEMU (qemu-guest-agent), utile quand loco tourne dans une VM Proxmox/QEMU."""
    if shutil.which("apt-get"):
        _run(["apt-get", "install", "-y", "qemu-guest-agent"], log, env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    elif shutil.which("dnf"):
        _run(["dnf", "install", "-y", "qemu-guest-agent"], log)
    elif shutil.which("apk"):
        _run(["apk", "add", "qemu-guest-agent"], log)
        initd = "/etc/init.d/qemu-guest-agent"       # Alpine ne fournit que le binaire : service OpenRC minimal.
        if not os.path.exists(initd):
            with open(initd, "w") as f:
                f.write('#!/sbin/openrc-run\nname="QEMU Guest Agent"\n'
                        'command="/usr/bin/qemu-ga"\ncommand_background=true\npidfile="/run/qemu-ga.pid"\n')
            os.chmod(initd, 0o755)
    else:
        raise ValueError(t("gestionnaire de paquets inconnu", "unknown package manager", "gestor de paquetes desconocido"))
    if shutil.which("systemctl"):
        _run(["systemctl", "enable", "--now", "qemu-guest-agent"], log)
    elif shutil.which("rc-update"):
        _run(["rc-update", "add", "qemu-guest-agent"], log)
        _run(["rc-service", "qemu-guest-agent", "start"], log)
    log(t("Agent invité QEMU installé et activé.", "QEMU guest agent installed and started.", "Agente invitado QEMU instalado y activado."))
    return True


def reboot(log=print):
    log(t("Redémarrage du serveur dans 5 secondes.", "Server rebooting in 5 seconds.", "El servidor se reinicia en 5 segundos."))
    subprocess.Popen(["sh", "-c", "sleep 5; exec reboot"], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# --------------------------------------------------------------------------- shell root du serveur
# Désactivé par défaut. L'activation enregistre le jeton du démarrage en cours (dans /run, vidé à chaque démarrage) :
# le shell ne devient disponible qu'après un redémarrage du serveur. La désactivation est immédiate.
# Le portail (utilisateur loco) parle au broker de l'agent root par un socket Unix réservé au groupe loco.
SHELL_FLAG = os.path.join(ETC, "server-shell")
SHELL_RUN = "/run/loco-shell"
SHELL_BOOT = os.path.join(SHELL_RUN, "boot")
SHELL_SOCK = os.path.join(SHELL_RUN, "shell.sock")
SHELL_SID_RE = re.compile(r"[0-9a-f]{32}")
SHELL_MAX = 4                      # sessions simultanées
SHELL_IDLE = 120                   # sans échange du navigateur (onglet fermé) : session fermée
SHELL_TTL = 8 * 3600               # durée maximale d'une session
SHELL_BUFFER = 2 * 1024 * 1024     # sortie non acquittée gardée au plus (au-delà, le shell attend)
SHELL_REPLY = 1024 * 1024          # sortie renvoyée au plus par échange


def shell_status():
    """« off » (désactivé), « pending » (activé, en attente d'un redémarrage) ou « active »."""
    try:
        with open(SHELL_FLAG) as f:
            flag = json.load(f)
    except (OSError, ValueError):
        return "off"
    try:
        with open(SHELL_BOOT) as f:
            boot = f.read().strip()
    except OSError:
        boot = ""                  # /run vidé depuis l'activation : le serveur a redémarré
    return "pending" if boot and isinstance(flag, dict) and flag.get("boot") == boot else "active"


def _shell_runtime():
    """Dossier d'exécution (root:loco 0750) et jeton du démarrage en cours ; renvoie le jeton."""
    os.makedirs(SHELL_RUN, exist_ok=True)
    shutil.chown(SHELL_RUN, "root", "loco")
    os.chmod(SHELL_RUN, 0o750)
    if not os.path.exists(SHELL_BOOT):
        with open(SHELL_BOOT + ".tmp", "w") as f:
            f.write(uuid.uuid4().hex)
        os.chmod(SHELL_BOOT + ".tmp", 0o644)
        os.replace(SHELL_BOOT + ".tmp", SHELL_BOOT)
    with open(SHELL_BOOT) as f:
        return f.read().strip()


def set_shell(params, log=print):
    """Active (effectif au prochain redémarrage) ou désactive (immédiat) le shell root du portail."""
    if str((params or {}).get("enabled", "")) == "1":
        boot = _shell_runtime()
        _write_conf_file(SHELL_FLAG, json.dumps({"boot": boot, "at": _now()}) + "\n")
        log(t("Shell root activé : il sera disponible après le redémarrage du serveur.",
              "Root shell enabled: it will be available after the server reboots.",
              "Shell root activado: estará disponible tras reiniciar el servidor."))
    else:
        try:
            os.remove(SHELL_FLAG)
        except FileNotFoundError:
            pass
        log(t("Shell root désactivé ; les sessions ouvertes sont fermées.",
              "Root shell disabled; open sessions are closed.",
              "Shell root desactivado; las sesiones abiertas se cierran."))
    return True


class _ShellSession:
    """Shell de connexion root dans un pseudo-terminal ; sortie découpée en paquets numérotés à acquitter."""

    def __init__(self, cols, rows):
        self.created = self.seen = time.time()
        self.in_seq = self.out_seq = 0
        self.packets = []          # [(seq, octets)] non acquittés
        self.alive, self.error = True, ""
        pid, fd = pty.fork()
        if pid == 0:               # enfant : rien de l'agent ne doit fuir dans le shell
            try:
                os.closerange(3, 65536)
                try:
                    os.chdir("/root")
                except OSError:
                    os.chdir("/")
                shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
                env = {"TERM": "xterm-256color", "HOME": "/root", "USER": "root", "LOGNAME": "root", "SHELL": shell,
                       "LANG": "C.UTF-8", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
                os.execve(shell, [shell, "-l"], env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        os.set_blocking(fd, False)
        self.resize(cols, rows)

    def resize(self, cols, rows):
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def pump(self):
        """Lit la sortie disponible (sans dépasser le tampon) et constate la fin du shell."""
        pending = sum(len(d) for _, d in self.packets)
        while self.alive and pending < SHELL_BUFFER:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError:        # EIO : le shell est terminé
                data = b""
            if not data:
                self.alive = False
                break
            self.out_seq += 1
            self.packets.append((self.out_seq, data))
            pending += len(data)
        if self.alive:
            try:
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    self.alive = False
            except ChildProcessError:
                self.alive = False

    def write(self, data):
        view = memoryview(data)
        deadline = time.time() + 2
        while view and time.time() < deadline:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                select.select([], [self.fd], [], 0.1)
            except OSError:
                self.alive = False
                return

    def close(self):
        for sig in (1, 9):         # SIGHUP puis SIGKILL
            try:
                os.killpg(self.pid, sig)
            except OSError:
                pass
            time.sleep(0.05)
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.alive = False


def _shell_number(value, low, high, default):
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return default


def shell_handle(req, sessions, active=None):
    """Une demande du portail : open / io / close. Réponse au format du relais des terminaux (remote.js)."""
    active = shell_status() == "active" if active is None else active
    sid = str(req.get("sid", ""))
    if not SHELL_SID_RE.fullmatch(sid):
        return {"state": "closed", "error": "session invalide", "packets": [], "ack": 0}
    op = req.get("op")
    cols = _shell_number(req.get("cols"), 20, 500, 100)
    rows = _shell_number(req.get("rows"), 5, 200, 30)
    session = sessions.get(sid)
    if op == "open":
        if not active:
            return {"state": "closed", "error": t("Shell root non activé.", "Root shell not enabled.", "Shell root no activado."),
                    "packets": [], "ack": 0}
        if session is None:
            if len(sessions) >= SHELL_MAX:
                return {"state": "closed", "error": t("Trop de shells ouverts.", "Too many open shells.", "Demasiados shells abiertos."),
                        "packets": [], "ack": 0}
            sessions[sid] = _ShellSession(cols, rows)
        return {"state": "active", "error": "", "packets": [], "ack": 0}
    created = False
    if session is None and op == "io" and req.get("create") and active and len(sessions) < SHELL_MAX:
        # Premier échange du terminal (le portail ne l'autorise qu'avant toute sortie reçue) : le shell démarre ici.
        session = sessions[sid] = _ShellSession(cols, rows)
        created = True
    if session is None:
        return {"state": "closed", "error": t("Session terminée (agent redémarré ?) : ouvrez un nouveau shell.",
                                              "Session ended (agent restarted?): open a new shell.",
                                              "Sesión terminada (¿agente reiniciado?): abra un nuevo shell."), "packets": [], "ack": 0}
    if op == "close" or not active:
        session.close()
        del sessions[sid]
        return {"state": "closed", "error": "" if op == "close" else t("Shell root désactivé.", "Root shell disabled.", "Shell root desactivado."),
                "packets": [], "ack": session.in_seq}
    if op != "io":
        return {"state": "closed", "error": "opération inconnue", "packets": [], "ack": session.in_seq}
    session.seen = time.time()
    seq = _shell_number(req.get("seq"), 0, 2**53, 0)
    if seq and seq == session.in_seq + 1 and session.alive:
        try:
            data = base64.b64decode(str(req.get("data", "")), validate=True)
        except ValueError:
            data = b""
        session.write(data[:256 * 1024])
        session.in_seq = seq
    session.resize(cols, rows)
    ack = _shell_number(req.get("ack"), 0, 2**53, 0)
    session.packets = [(s, d) for s, d in session.packets if s > ack]
    session.pump()
    out, size = [], 0
    for s, d in session.packets:
        if out and size + len(d) > SHELL_REPLY:
            break
        out.append({"seq": s, "data": base64.b64encode(d).decode()})
        size += len(d)
    state = "active" if session.alive or session.packets else "closed"
    if state == "closed":
        session.close()
        del sessions[sid]
    return {"state": state, "error": "" if state == "active" else t("Shell terminé.", "Shell ended.", "Shell terminado."),
            "packets": out, "ack": session.in_seq, "created": created}


def _shell_housekeeping(sessions, active):
    now = time.time()
    for sid, session in list(sessions.items()):
        if active:
            session.pump()
        if not active or now - session.seen > SHELL_IDLE or now - session.created > SHELL_TTL:
            session.close()
            del sessions[sid]


def shell_broker(path=None):
    """Service du shell root (fil de l'agent) : une demande JSON par connexion sur le socket Unix."""
    path = path or SHELL_SOCK
    sessions = {}
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    # À l'écoute dès que le fichier existe (sinon un client arrivé entre les deux est refusé) ; jusqu'aux droits
    # ci-dessous, le socket appartient à root et lui seul peut s'y connecter.
    server.listen(8)
    if path == SHELL_SOCK:
        shutil.chown(path, "root", "loco")
    os.chmod(path, 0o660)
    checked, active = 0, False
    while True:
        if time.time() - checked > 0.5:
            checked, active = time.time(), shell_status() == "active"
            _shell_housekeeping(sessions, active)
        ready, _, _ = select.select([server], [], [], 0.2)
        if not ready:
            continue
        conn, _ = server.accept()
        try:
            conn.settimeout(3)
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 600 * 1024:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            try:
                req = json.loads(buf)
                # État relu à chaque demande : une désactivation vaut tout de suite, sans fenêtre de course.
                reply = shell_handle(req, sessions) if isinstance(req, dict) else {"state": "closed", "error": "demande invalide"}
            except ValueError:
                reply = {"state": "closed", "error": "demande invalide", "packets": [], "ack": 0}
            conn.sendall(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass
        finally:
            conn.close()


def shell_call(req, path=None, timeout=5):
    """Côté portail : envoie une demande au broker et renvoie sa réponse."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(path or SHELL_SOCK)
        client.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = client.recv(262144)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 4 * 1024 * 1024:
                raise ValueError("réponse trop longue")
    finally:
        client.close()
    return json.loads(buf)


# Noms d'archives produits par loco-backup : « loco-HÔTE-DATE-HEURE.tar.gz » (et local-repo-… d'avant le renommage).
BACKUP_NAME_RE = re.compile(r"(?:loco|local-repo)-[A-Za-z0-9._-]+-\d{8}-\d{6}\.tar\.gz")


def _share_backups():
    """Rend les sauvegardes lisibles par le groupe loco (640) pour que le portail puisse les proposer au téléchargement."""
    directory = read_conf().get("BACKUP_DIR", "/var/backups/loco")
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not BACKUP_NAME_RE.fullmatch(name):
            continue
        path = os.path.join(directory, name)
        try:
            if stat.S_ISREG(os.lstat(path).st_mode):
                shutil.chown(path, group="loco")
                os.chmod(path, 0o640)
        except OSError:
            continue


UPLOAD_SPOOL = os.path.join(STATE, "restore-uploads")
UPLOAD_NAME_RE = re.compile(r"[0-9a-f]{32}\.tar\.gz")


def _cleanup_upload(upload):
    if upload and UPLOAD_NAME_RE.fullmatch(upload):
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(UPLOAD_SPOOL, upload))


def restore(params, log=print):
    """Restaure une sauvegarde loco (même serveur) : base, comptes, clés, dépôts et catalogue, puis régénère services
    et configuration via install.sh. La configuration propre au serveur (IP, ports, réseaux) est conservée. La
    sauvegarde vient de BACKUP_DIR (« archive ») ou d'un fichier téléversé dans le spool (« upload »)."""
    params = params or {}
    upload, name = str(params.get("upload", "")), str(params.get("archive", ""))
    conf = read_conf()
    data, source = conf.get("DATA", ""), conf.get("SOURCE_DIR", "")
    if upload:
        if not UPLOAD_NAME_RE.fullmatch(upload):
            raise ValueError("fichier téléversé invalide")
        archive, label = os.path.join(UPLOAD_SPOOL, upload), t("la sauvegarde téléversée", "the uploaded backup", "la copia subida")
    else:
        if not BACKUP_NAME_RE.fullmatch(name):
            raise ValueError("nom de sauvegarde invalide")
        archive, label = os.path.join(conf.get("BACKUP_DIR", "/var/backups/loco"), name), name
    if not data:
        raise ValueError("DATA absent de la configuration")
    if not source or not os.path.isfile(os.path.join(source, "linux", "install.sh")):
        raise ValueError(f"sources de loco introuvables (SOURCE_DIR de {CONF})")
    try:                                          # fichier ordinaire, jamais un lien symbolique
        if not stat.S_ISREG(os.lstat(archive).st_mode):
            raise ValueError("archive")
    except OSError:
        raise ValueError(t("sauvegarde introuvable", "backup not found", "copia de seguridad no encontrada")) from None
    backup_py = os.path.join(os.path.dirname(HERE), "loco", "backup.py")
    # Vérifier l'archive avant toute écriture (indispensable pour un fichier téléversé, non produit par ce serveur).
    if _run([sys.executable, "-I", backup_py, "--verify", archive], log) != 0:
        _cleanup_upload(upload)
        raise ValueError(t("archive invalide : ce n'est pas une sauvegarde loco valide", "invalid archive: not a valid loco backup",
                           "archivo no válido: no es una copia de seguridad de loco válida"))
    log(t(f"Restauration de {label}.", f"Restoring {label}.", f"Restaurando {label}."))
    # Arrêter le portail avant de réécrire la base et les données.
    _run(["rc-service", "loco", "stop"] if ALPINE else ["systemctl", "stop", "loco-portal"], log)
    with _snapshot() as snapshot:
        try:
            if _run([sys.executable, "-I", backup_py, archive, data], log) != 0:
                raise RuntimeError("échec de l'extraction de la sauvegarde")
            _restore_ownership(data, log)
            log(t("Régénération des services et de la configuration.", "Regenerating services and configuration.",
                  "Regenerando servicios y configuración."))
            ok = _install(source, [], log) == 0
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
            log(f"Restauration interrompue : {e}")
            ok = False
        if not ok:
            _rollback(snapshot, source, log)
            _cleanup_upload(upload)
            return False
    _cleanup_upload(upload)
    log(t("Sauvegarde restaurée.", "Backup restored.", "Copia de seguridad restaurada."))
    return True


def _restore_ownership(data, log):
    """Rend les fichiers restaurés au bon propriétaire (loco), comme le fait loco-backup après une restauration."""
    with contextlib.suppress(OSError):
        shutil.chown(os.path.join(ETC, "portal.secret"), "root", "loco")
    for suffix in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(STATE, "portal.db" + suffix))
    targets = [STATE, os.path.join(data, "apt"), os.path.join(data, "win")]
    targets += [os.path.join(data, extra) for extra in ("flatpak", "rpm") if os.path.isdir(os.path.join(data, extra))]
    for target in targets:
        if os.path.exists(target):
            _run(["chown", "-R", "loco:loco", target], log)


def set_backup_keep(params, log=print):
    """Nombre de sauvegardes quotidiennes conservées : écrit BACKUP_KEEP dans loco.conf (lu par loco-backup) et
    supprime tout de suite les plus anciennes au-delà de la limite."""
    try:
        keep = int(str((params or {}).get("keep", "")))
    except ValueError:
        raise ValueError("nombre de sauvegardes invalide") from None
    if not 1 <= keep <= 60:
        raise ValueError("nombre de sauvegardes hors limites (1 à 60)")
    _set_conf("BACKUP_KEEP", keep)
    log(t(f"Sauvegardes conservées : {keep}.", f"Backups kept: {keep}.", f"Copias conservadas: {keep}."))
    directory = read_conf().get("BACKUP_DIR", "/var/backups/loco")
    try:                                          # purge immédiate, comme loco-backup : seulement les « loco-*.tar.gz »
        archives = sorted((os.path.join(directory, n) for n in os.listdir(directory)
                           if BACKUP_NAME_RE.fullmatch(n) and n.startswith("loco-")),
                          key=os.path.getmtime, reverse=True)
    except OSError:
        archives = []
    for path in archives[keep:]:
        with contextlib.suppress(OSError):
            os.unlink(path)
        log(t(f"Supprimée : {os.path.basename(path)}", f"Removed: {os.path.basename(path)}", f"Eliminada: {os.path.basename(path)}"))
    return True


# --------------------------------------------------------------------------- certificat HTTPS
# Les agents du parc épinglent l'autorité loco : le certificat présenté à l'adresse du serveur doit toujours en venir.
# Un certificat importé (autorité d'entreprise, Let's Encrypt…) n'est donc servi que pour SES noms DNS, par SNI, dans
# un second bloc nginx dérivé de celui du portail ; le bloc principal reste le serveur par défaut.
TLS = os.path.join(ETC, "tls")
TLS_SPOOL = os.path.join(STATE, "tls-uploads")
TLS_UPLOAD_RE = re.compile(r"[0-9a-f]{32}")
PORTAL_SITE = "/etc/nginx/sites-available/loco-portal.conf"
SNI_SITE = "/etc/nginx/sites-available/loco-portal.sni.conf"    # trié APRÈS loco-portal.conf (« . » + « s » > « . » + « c »)
SNI_LINK = "/etc/nginx/sites-enabled/loco-portal.sni.conf"
# Nom court accepté (« loco ») : une autorité d'entreprise l'émet, et install.sh le met déjà dans le certificat de loco. Joker : deux niveaux au moins.
CERT_NAME_RE = re.compile(r"\*\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
                          r"|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")


def _ssl(args, log=None, data=None):
    p = subprocess.run(["openssl", *args], input=data, capture_output=True, text=True, timeout=60)
    if p.returncode != 0 and log:
        log((p.stderr or p.stdout).strip()[-400:])
    return p.returncode, p.stdout


def _cert_dns_names(path):
    code, out = _ssl(["x509", "-in", path, "-noout", "-ext", "subjectAltName"])
    return [n for n in re.findall(r"DNS:([^\s,]+)", out) if CERT_NAME_RE.fullmatch(n)] if code == 0 else []


def _name_covers(pattern, host):
    pattern, host = pattern.lower(), host.lower()
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host.count(".") == pattern.count(".")
    return pattern == host


def _reload_nginx(log):
    if _run(["nginx", "-t"], log) != 0:
        return False
    if shutil.which("systemctl") and subprocess.run(["systemctl", "is-active", "-q", "nginx"]).returncode == 0:
        return _run(["systemctl", "reload", "nginx"], log) == 0
    return _run(["nginx", "-s", "reload"], log) == 0                # Alpine : nginx lancé par supervisord


def _https_block(text):
    """Bloc « server { … } » HTTPS du portail (celui qui sert /etc/loco/tls/server.crt)."""
    lines, depth, start = text.splitlines(keepends=True), 0, None
    for i, line in enumerate(lines):
        code = line.split("#", 1)[0]
        if depth == 0 and re.match(r"\s*server\s*\{", code):
            start = i
        depth += code.count("{") - code.count("}")
        if depth == 0 and start is not None:
            block = "".join(lines[start:i + 1])
            if os.path.join(TLS, "server.crt") in block:
                return block
            start = None
    return ""


def write_sni_site(log=print):
    """(Re)génère le bloc nginx du certificat importé à partir du bloc du portail ; le retire s'il n'y en a pas.
    Appelé après un import et par install.sh, pour ne jamais garder une copie périmée de la configuration."""
    crt = os.path.join(TLS, "imported.crt")
    names = _cert_dns_names(crt) if os.path.isfile(crt) and os.path.isfile(os.path.join(TLS, "imported.key")) else []
    if not names:
        for path in (SNI_LINK, SNI_SITE):
            with contextlib.suppress(OSError):
                os.unlink(path)
        return False
    try:
        with open(PORTAL_SITE) as f:
            block = _https_block(f.read())
    except OSError:
        block = ""
    if not block:
        raise ValueError(t("configuration nginx du portail introuvable", "portal nginx configuration not found",
                           "configuración nginx del portal no encontrada"))
    block = re.sub(r"server_name\s+[^;]*;", "server_name " + " ".join(names) + ";", block, count=1)
    block = block.replace(os.path.join(TLS, "server.crt"), crt).replace(os.path.join(TLS, "server.key"),
                                                                         os.path.join(TLS, "imported.key"))
    _write_conf_file(SNI_SITE, "# Géré par loco : certificat importé, servi seulement pour ses noms (SNI).\n"
                     "# Régénéré depuis loco-portal.conf ; ne pas modifier.\n" + block)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SNI_LINK)
    os.symlink("../sites-available/loco-portal.sni.conf", SNI_LINK)
    return True


def _swap_tls(files, log, after=None):
    """Remplace des fichiers de /etc/loco/tls (nom → fichier préparé), applique, recharge nginx ; sinon restaure tout."""
    saved = {}
    for name, src in files.items():
        dest = os.path.join(TLS, name)
        if os.path.exists(dest):
            saved[dest] = dest + ".loco-old"
            shutil.copy2(dest, saved[dest])
        else:
            saved[dest] = None
        if src is None:
            os.unlink(dest)
        else:
            os.chmod(src, 0o600 if name.endswith(".key") else 0o644)
            os.replace(src, dest)
    ok = False
    try:
        if after:
            after(log)
        ok = _reload_nginx(log)
    except (OSError, ValueError) as e:
        log(str(e))
    if not ok:
        for dest, backup in saved.items():
            if backup:
                os.replace(backup, dest)
            else:
                with contextlib.suppress(OSError):
                    os.unlink(dest)
        with contextlib.suppress(OSError, ValueError):
            write_sni_site(log)
        _reload_nginx(log)
        raise ValueError(t("nginx a refusé le certificat : configuration précédente rétablie",
                           "nginx rejected the certificate: previous configuration restored",
                           "nginx rechazó el certificado: configuración anterior restablecida"))
    for backup in saved.values():
        if backup:
            with contextlib.suppress(OSError):
                os.unlink(backup)


def renew_cert(params=None, log=print):
    """Nouveau certificat serveur signé par l'autorité loco (adresse et noms actuels), comme install.sh : les postes
    qui font confiance à l'autorité ne voient aucune différence."""
    if os.path.exists(os.path.join(TLS, "custom")):
        raise ValueError(t("certificat fourni à l'installation (--tls-cert) : relancez install.sh avec le nouveau",
                           "certificate supplied at installation (--tls-cert): rerun install.sh with the new one",
                           "certificado proporcionado en la instalación (--tls-cert): vuelva a ejecutar install.sh con el nuevo"))
    if tls_mode() == "enterprise":
        raise ValueError(t("certificat émis par votre autorité d'entreprise : importez le nouveau certificat",
                           "certificate issued by your enterprise authority: import the new certificate",
                           "certificado emitido por su autoridad de empresa: importe el nuevo certificado"))
    with tempfile.TemporaryDirectory(dir=TLS, prefix=".renew-") as tmp:
        key, crt, san = _loco_cert(tmp, log, extra=_ha().cluster_addresses(_ha().node()) if ha_configured() else ())
        _swap_tls({"server.key": key, "server.crt": crt}, log)
    log(t(f"Certificat renouvelé pour {', '.join(s.split(':', 1)[1] for s in san)} (825 jours).",
          f"Certificate renewed for {', '.join(s.split(':', 1)[1] for s in san)} (825 days).",
          f"Certificado renovado para {', '.join(s.split(':', 1)[1] for s in san)} (825 días)."))
    return True


def import_cert(params, log=print):
    """Installe un certificat fourni (chaîne PEM + clé), servi par SNI pour ses seuls noms DNS."""
    upload = str((params or {}).get("upload", ""))
    if not TLS_UPLOAD_RE.fullmatch(upload):
        raise ValueError("fichier téléversé invalide")
    crt, key = (os.path.join(TLS_SPOOL, upload + ext) for ext in (".crt", ".key"))
    try:
        for path in (crt, key):
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise ValueError("fichier")
        if _ssl(["x509", "-in", crt, "-noout", "-checkend", "0"], log)[0] != 0:
            raise ValueError(t("certificat illisible ou expiré", "certificate unreadable or expired", "certificado ilegible o caducado"))
        if _ssl(["x509", "-in", crt, "-noout", "-pubkey"])[1] != _ssl(["pkey", "-in", key, "-pubout"])[1]:
            raise ValueError(t("la clé ne correspond pas au certificat", "the key does not match the certificate",
                               "la clave no corresponde al certificado"))
        names = _cert_dns_names(crt)
        if not names:
            raise ValueError(t("aucun nom DNS dans le certificat", "no DNS name in the certificate", "ningún nombre DNS en el certificado"))
        host = read_conf().get("HOST", "")
        if host and any(_name_covers(n, host) for n in names):
            raise ValueError(t(f"il couvre {host}, l'adresse utilisée par les agents : ils perdraient la connexion",
                               f"it covers {host}, the address used by the agents: they would lose the connection",
                               f"cubre {host}, la dirección usada por los agentes: perderían la conexión"))
        portal = urllib.parse.urlsplit(read_conf().get("PORTAL_URL", "")).hostname
        if portal and not _covers_host(_read(crt), portal) and not _covers_host(_read(os.path.join(TLS, "server.crt")), portal):
            raise ValueError(t(f"ni lui ni le certificat du serveur ne couvrent {portal}, l'adresse du portail",
                               f"neither it nor the server certificate covers {portal}, the portal address",
                               f"ni este ni el certificado del servidor cubren {portal}, la dirección del portal"))
        with tempfile.TemporaryDirectory(dir=TLS, prefix=".import-") as tmp:
            staged = {}
            for name, src in (("imported.crt", crt), ("imported.key", key)):
                staged[name] = os.path.join(tmp, name)
                shutil.copyfile(src, staged[name])
            _swap_tls(staged, log, after=write_sni_site)
    finally:
        for path in (crt, key):
            with contextlib.suppress(OSError):
                os.unlink(path)
    log(t(f"Certificat importé, servi pour : {', '.join(names)}.", f"Certificate imported, served for: {', '.join(names)}.",
          f"Certificado importado, servido para: {', '.join(names)}."))
    return True


def remove_imported_cert(params=None, log=print):
    portal = urllib.parse.urlsplit(read_conf().get("PORTAL_URL", "")).hostname
    if portal and not _covers_host(_read(os.path.join(TLS, "server.crt")), portal):
        raise ValueError(t(f"c'est lui qui couvre {portal}, l'adresse du portail : changez d'abord cette adresse (Système, onglet Réseau)",
                           f"it is the one covering {portal}, the portal address: change that address first (System, Network tab)",
                           f"es el que cubre {portal}, la dirección del portal: cambie primero esa dirección (Sistema, pestaña Red)"))
    _swap_tls({n: None for n in ("imported.crt", "imported.key") if os.path.exists(os.path.join(TLS, n))}, log,
              after=write_sni_site)
    log(t("Certificat importé retiré.", "Imported certificate removed.", "Certificado importado retirado."))
    return True


# --------------------------------------------------------------------------- autorité de certification au choix
# Deux autorités possibles pour le certificat du portail : celle de loco (par défaut) ou celle de l'entreprise (AD CS,
# PKI interne…). Les agents épinglent un FAISCEAU (trust.pem : autorité loco en tête, puis l'autorité d'entreprise),
# livré au fil de leur canal déjà épinglé : la bascule ne coupe que les postes qui ne l'ont pas encore reçu, et le
# retour à l'autorité loco est toujours sûr (elle reste dans le faisceau).
TLS_FILES = {"ENTERPRISE_CA": "enterprise-ca.pem", "ENTERPRISE_CRT": "enterprise.crt", "ENTERPRISE_KEY": "enterprise.key",
             "REQUEST_KEY": "enterprise-request.key", "REQUEST_CSR": "enterprise.csr", "TRUST_PEM": "trust.pem",
             "TLS_MODE": "mode"}


def tls_path(what):
    """Chemin d'un fichier de /etc/loco/tls, évalué à l'appel (TLS peut être déplacé par les tests)."""
    return os.path.join(TLS, TLS_FILES[what])
PEM_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def tls_mode():
    try:
        with open(tls_path("TLS_MODE")) as f:
            return "enterprise" if f.read().strip() == "enterprise" else "loco"
    except OSError:
        return "loco"


def tls_params(action, params):
    """Paramètres des actions de certificat, validés par le portail puis revalidés par l'agent root."""
    p = params or {}
    if action == "import-ca":
        upload = str(p.get("upload", ""))
        if not TLS_UPLOAD_RE.fullmatch(upload):
            raise ValueError("fichier téléversé invalide")
        return {"upload": upload}
    if action == "import-enterprise-cert":
        upload = str(p.get("upload", ""))
        if not TLS_UPLOAD_RE.fullmatch(upload):
            raise ValueError("fichier téléversé invalide")
        return {"upload": upload, "key": "1" if str(p.get("key", "")) == "1" else ""}
    if action == "ca-csr":
        names = [n.lower() for n in str(p.get("names", "")).replace(",", " ").split()]
        if len(names) > 8 or any(not CERT_NAME_RE.fullmatch(n) for n in names):
            raise ValueError("noms DNS invalides")
        return {"names": " ".join(dict.fromkeys(names))}
    if action == "set-tls-mode":
        mode = str(p.get("mode", ""))
        if mode not in ("loco", "enterprise"):
            raise ValueError("autorité inconnue")
        return {"mode": mode}
    return {}


def _pem_blocks(text):
    return PEM_BLOCK_RE.findall(text or "")


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _x509(pem, *args):
    """openssl x509 sur un certificat PEM en mémoire : (code, sortie)."""
    return _ssl(["x509", "-noout", *args], data=pem)


def _is_ca(pem):
    return "CA:TRUE" in _x509(pem, "-ext", "basicConstraints")[1].replace(" ", "")


def _self_signed(pem):
    subject, issuer = _x509(pem, "-subject")[1].split("=", 1)[-1], _x509(pem, "-issuer")[1].split("=", 1)[-1]
    return subject.strip() == issuer.strip()


def _write_public(path, text, mode=0o644):
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)


def write_trust(log=print):
    """trust.pem = autorité loco + autorité d'entreprise importée : ce que les agents épinglent, ce que sert /ca.crt."""
    if os.path.exists(os.path.join(TLS, "custom")):
        return False
    blocks = _pem_blocks(_read(os.path.join(TLS, "ca.crt")))[:1] + _pem_blocks(_read(tls_path("ENTERPRISE_CA")))
    if not blocks:
        return False
    _write_public(tls_path("TRUST_PEM"), "\n".join(dict.fromkeys(blocks)) + "\n")
    return True


def _covers_host(pem, host):
    """Le certificat couvre-t-il l'adresse des agents (IP ou nom DNS) ?"""
    out = _x509(pem, "-ext", "subjectAltName")[1]
    try:
        ip = ipaddress.ip_address(host)
        return any(ipaddress.ip_address(a) == ip for a in re.findall(r"IP Address:([0-9A-Fa-f:.]+)", out)
                   if _valid_ip(a))
    except ValueError:
        return any(_name_covers(n, host) for n in re.findall(r"DNS:([^\s,]+)", out))


PORTAL_URL_RE = re.compile(r"https://([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
                           r"|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?")


def _served_cert_covers(host):
    """Le certificat présenté aux navigateurs couvre-t-il ce nom : celui du serveur, ou le certificat importé (SNI) ?"""
    return any(_covers_host(pem, host) for pem in (_read(os.path.join(TLS, n)) for n in ("server.crt", "imported.crt")) if pem)


def portal_params(params, conf=None):
    """Adresse du portail, validée par le portail puis revalidée par l'agent root ; adresse vide : celle des agents."""
    conf = read_conf() if conf is None else conf
    p = params or {}
    url = str(p.get("url", "")).strip().rstrip("/")
    if not url:
        return {"url": "", "extend": ""}
    try:
        if len(url) > 255 or not PORTAL_URL_RE.fullmatch(url):
            raise ValueError
        parts = urllib.parse.urlsplit(url)
        port = str(parts.port or 443)
    except ValueError:
        raise ValueError(t("adresse attendue : https://nom:port, sans chemin", "expected address: https://name:port, without a path",
                           "dirección esperada: https://nombre:puerto, sin ruta")) from None
    expected = conf.get("HTTPS_PORT", "8443")
    if port != expected:
        raise ValueError(t(f"le portail écoute sur le port {expected} : indiquez https://nom:{expected}",
                           f"the portal listens on port {expected}: enter https://name:{expected}",
                           f"el portal escucha en el puerto {expected}: indique https://nombre:{expected}"))
    url, host = url.lower().removesuffix(":443"), parts.hostname.lower()
    if host == conf.get("HOST", "").lower():
        return {"url": "", "extend": ""}             # l'adresse des agents : c'est le réglage par défaut
    extend = "1" if str(p.get("extend", "")) == "1" else ""
    if _served_cert_covers(host):
        return {"url": url, "extend": ""}
    if os.path.exists(os.path.join(TLS, "custom")):
        raise ValueError(t(f"le certificat fourni à l'installation (--tls-cert) ne couvre pas {host} : relancez install.sh avec un certificat qui le couvre",
                           f"the certificate supplied at installation (--tls-cert) does not cover {host}: rerun install.sh with one that does",
                           f"el certificado proporcionado en la instalación (--tls-cert) no cubre {host}: vuelva a ejecutar install.sh con uno que lo cubra"))
    if tls_mode() == "enterprise":
        raise ValueError(t(f"le certificat de votre autorité d'entreprise ne couvre pas {host} : demandez-en un qui le couvre (Système, onglet Certificat)",
                           f"your enterprise authority's certificate does not cover {host}: request one that does (System, Certificate tab)",
                           f"el certificado de su autoridad de empresa no cubre {host}: solicite uno que lo cubra (Sistema, pestaña Certificado)"))
    if not extend:
        raise ValueError(t(f"le certificat de loco ne couvre pas {host} : cochez « Ajouter ce nom au certificat de loco » (loco-config : --add-to-certificate)",
                           f"loco's certificate does not cover {host}: tick \"Add this name to loco's certificate\" (loco-config: --add-to-certificate)",
                           f"el certificado de loco no cubre {host}: marque «Añadir este nombre al certificado de loco» (loco-config: --add-to-certificate)"))
    return {"url": url, "extend": "1"}


def set_portal_url(params, log=print):
    """Enregistre l'adresse du portail ; les agents et l'enrôlement ne changent pas. Aucun redémarrage : le portail relit loco.conf."""
    conf = read_conf()
    p = portal_params(params, conf)
    if ha_configured() and _ha().role() == "standby":
        raise ValueError(t("serveur de secours : changez l'adresse sur le principal, elle sera répliquée",
                           "standby server: change the address on the primary, it will be replicated",
                           "servidor de respaldo: cambie la dirección en el principal, se replicará"))
    previous = conf.get("PORTAL_URL", "")
    _set_conf("PORTAL_URL", p["url"])
    if p["extend"]:
        try:
            renew_cert(log=log)
        except (ValueError, OSError, subprocess.SubprocessError):
            _set_conf("PORTAL_URL", previous)
            raise
    shown = p["url"] or t("celle des agents", "the agents' address", "la de los agentes")
    log(t(f"Adresse du portail : {shown}. Redéclarez l'URL de retour chez chaque fournisseur SSO (Annuaires et SSO).",
          f"Portal address: {shown}. Declare the redirect URL again with each SSO provider (Directories and SSO).",
          f"Dirección del portal: {shown}. Vuelva a declarar la URL de retorno en cada proveedor SSO (Directorios y SSO)."))
    return True


# Noms internes : ceux que le DNS de ce serveur ne résout pas (émetteur SSO de l'entreprise). La liste vit dans /etc/loco, donc
# sauvegardée et répliquée en haute disponibilité ; /etc/hosts porte un bloc dédié, refait dès qu'install.sh ou setup-loco réécrivent
# ce fichier. RESOLVERS et resolv.conf, ceux du cache Windows Update, ne sont pas touchés.
HOSTS_FILE = "/etc/hosts"
INTERNAL_HOSTS = os.path.join(ETC, "internal-hosts")
HOSTS_BEGIN, HOSTS_END = "# BEGIN loco : noms internes, gérés depuis le portail", "# END loco"
MAX_INTERNAL_HOSTS = 64
HOST_ENTRY_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


def hosts_params(params):
    """Ajout ou retrait d'un nom interne, validé par le portail puis revalidé par l'agent root."""
    p = params or {}
    name = str(p.get("name", "")).strip().lower().rstrip(".")
    if len(name) > 253 or not HOST_ENTRY_RE.fullmatch(name):
        raise ValueError(t("nom invalide : lettres, chiffres, tirets et points", "invalid name: letters, digits, hyphens and dots",
                           "nombre no válido: letras, cifras, guiones y puntos"))
    if p.get("op") == "remove":
        return {"op": "remove", "name": name}
    if p.get("op") != "add":
        raise ValueError(t("opération inconnue", "unknown operation", "operación desconocida"))
    if name == "localhost" or name.endswith(".localhost") or name == socket.gethostname().lower():
        raise ValueError(t(f"{name} est réservé à ce serveur", f"{name} is reserved for this server", f"{name} está reservado para este servidor"))
    try:
        address = ipaddress.ip_address(str(p.get("address", "")).strip())
    except ValueError:
        raise ValueError(t("adresse IP attendue", "IP address expected", "se espera una dirección IP")) from None
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError(t(f"{address} ne peut pas être l'adresse d'un serveur", f"{address} cannot be a server's address",
                           f"{address} no puede ser la dirección de un servidor"))
    return {"op": "add", "name": name, "address": str(address)}


def read_internal_hosts():
    """{nom : adresse}. Une ligne mal formée est ignorée : elle n'atteint jamais /etc/hosts."""
    found = {}
    try:
        with open(INTERNAL_HOSTS, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and HOST_ENTRY_RE.fullmatch(parts[0]) and _valid_ip(parts[1]):
                    found[parts[0]] = parts[1]
    except OSError:
        pass
    return found


def _with_hosts_block(text, entries):
    """Texte de /etc/hosts avec le bloc de loco à jour ; le reste du fichier est conservé tel quel."""
    lines = text.splitlines()
    if HOSTS_BEGIN in lines:
        start = lines.index(HOSTS_BEGIN)
        end = lines.index(HOSTS_END, start) if HOSTS_END in lines[start:] else start
        lines[start:end + 1] = []
    elif not entries:
        return text
    block = [HOSTS_BEGIN, *(f"{address}\t{name}" for name, address in sorted(entries.items())), HOSTS_END] if entries else []
    return "\n".join(lines + block) + "\n"


def apply_internal_hosts():
    """Met le bloc de /etc/hosts en accord avec la liste ; n'écrit rien s'il l'est déjà."""
    try:
        with open(HOSTS_FILE, encoding="utf-8") as f:
            current = f.read()
    except OSError:
        return False
    wanted = _with_hosts_block(current, read_internal_hosts())
    if wanted == current:
        return False
    tmp = HOSTS_FILE + ".loco-tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(wanted)
        os.chmod(tmp, 0o644)
        os.replace(tmp, HOSTS_FILE)
    except OSError:                                          # fichier monté : pas de remplacement possible
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        with open(HOSTS_FILE, "w", encoding="utf-8") as f:
            f.write(wanted)
    return True


def _hosts_stamp():
    stamp = []
    for path in (INTERNAL_HOSTS, HOSTS_FILE):
        try:
            stamp.append(os.stat(path).st_mtime_ns)
        except OSError:
            stamp.append(0)
    return stamp


def set_internal_hosts(params, log=print):
    p = hosts_params(params)
    if ha_configured() and _ha().role() == "standby":
        raise ValueError(t("serveur de secours : changez ces noms sur le principal, ils seront répliqués",
                           "standby server: change these names on the primary, they will be replicated",
                           "servidor de respaldo: cambie estos nombres en el principal, se replicarán"))
    entries = read_internal_hosts()
    if p["op"] == "remove":
        entries.pop(p["name"], None)
        log(t(f"Nom retiré : {p['name']}", f"Name removed: {p['name']}", f"Nombre retirado: {p['name']}"))
    else:
        if p["name"] not in entries and len(entries) >= MAX_INTERNAL_HOSTS:
            raise ValueError(t(f"{MAX_INTERNAL_HOSTS} noms au plus : utilisez plutôt un DNS interne", f"{MAX_INTERNAL_HOSTS} names at most: use an internal DNS instead",
                               f"{MAX_INTERNAL_HOSTS} nombres como máximo: use mejor un DNS interno"))
        entries[p["name"]] = p["address"]
        log(t(f"{p['name']} : {p['address']}", f"{p['name']}: {p['address']}", f"{p['name']}: {p['address']}"))
    tmp = INTERNAL_HOSTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(f"{name} {address}\n" for name, address in sorted(entries.items()))
    os.chmod(tmp, 0o644)
    os.replace(tmp, INTERNAL_HOSTS)
    apply_internal_hosts()
    return True


def _valid_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _check_enterprise_pair(crt_pem, key_path, log):
    """Certificat d'entreprise utilisable par les agents : clé assortie, non expiré, adresse des agents couverte,
    chaîné à l'autorité d'entreprise importée. Renvoie la chaîne à servir (certificat + intermédiaires)."""
    blocks = _pem_blocks(crt_pem)
    if not blocks:
        raise ValueError(t("certificat illisible", "unreadable certificate", "certificado ilegible"))
    leaf = blocks[0]
    if _x509(leaf, "-checkend", "0")[0] != 0:
        raise ValueError(t("certificat expiré", "certificate expired", "certificado caducado"))
    if _x509(leaf, "-pubkey")[1] != _ssl(["pkey", "-in", key_path, "-pubout"])[1]:
        raise ValueError(t("la clé ne correspond pas au certificat", "the key does not match the certificate",
                           "la clave no corresponde al certificado"))
    host = read_conf().get("HOST", "")
    covered = ", ".join(re.findall(r"(?:DNS:|IP Address:)([^\s,]+)", _x509(leaf, "-ext", "subjectAltName")[1])) or "—"
    if host and not _covers_host(leaf, host):
        raise ValueError(t(f"SAN absent : il couvre {covered}, mais pas {host}, l'adresse utilisée par les agents. Votre autorité l'a peut-être retiré (certains profils, comme EJBCA, ne gardent pas les IP) : demandez un profil qui le conserve",
                           f"missing SAN: it covers {covered}, but not {host}, the address used by the agents. Your authority may have removed it (some profiles, such as EJBCA, drop IPs): ask for a profile that keeps it",
                           f"falta el SAN: cubre {covered}, pero no {host}, la dirección usada por los agentes. Puede que su autoridad lo haya retirado (algunos perfiles, como EJBCA, no conservan las IP): solicite un perfil que lo mantenga"))
    portal = urllib.parse.urlsplit(read_conf().get("PORTAL_URL", "")).hostname
    if portal and not _covers_host(leaf, portal):
        raise ValueError(t(f"SAN absent : il couvre {covered}, mais pas {portal}, l'adresse du portail",
                           f"missing SAN: it covers {covered}, but not {portal}, the portal address",
                           f"falta el SAN: cubre {covered}, pero no {portal}, la dirección del portal"))
    authority = _pem_blocks(_read(tls_path("ENTERPRISE_CA")))
    if not authority:
        raise ValueError(t("importez d'abord l'autorité d'entreprise", "import the enterprise authority first",
                           "importe primero la autoridad de empresa"))
    problem, intermediates = chain_problem(leaf, blocks[1:], authority, log, TLS)
    if problem:
        raise ValueError(problem)
    return "\n".join([leaf] + intermediates) + "\n"


def chain_problem(leaf, given, authority, log=None, workdir=None):
    """(raison du refus ou None, intermédiaires à servir). Le portail l'appelle aussi : le refus s'affiche aussitôt sur la page,
    sans attendre l'opération de l'agent root, qui revalide de toute façon."""
    intermediates = [b for b in dict.fromkeys(b for b in given + authority if not _self_signed(b)) if b != leaf]
    with tempfile.TemporaryDirectory(dir=workdir, prefix=".verify-") as tmp:
        files = {}
        for name, text in (("leaf.pem", leaf), ("ca.pem", "\n".join(authority)), ("chain.pem", "\n".join(intermediates))):
            files[name] = os.path.join(tmp, name)
            with open(files[name], "w", encoding="utf-8") as f:
                f.write(text + "\n")
        args = ["verify", "-CAfile", files["ca.pem"]] + (["-untrusted", files["chain.pem"]] if intermediates else [])
        if _ssl(args + [files["leaf.pem"]], log)[0] != 0:
            return _chain_refusal(leaf, intermediates, authority), intermediates
    return None, intermediates


def _dn(pem, which):
    """Sujet ou émetteur d'un certificat PEM (RFC 2253), pour comparer les maillons d'une chaîne."""
    return _x509(pem, f"-{which}", "-nameopt", "RFC2253")[1].strip().split("=", 1)[-1].strip()


def _cn_of(dn):
    found = re.search(r"(?:^|,)CN=((?:[^,\\]|\\.)+)", dn)
    return found.group(1) if found else dn


def _chain_refusal(leaf, intermediates, authority):
    """Pourquoi la chaîne ne remonte pas à l'autorité importée : intermédiaire manquant, ou mauvaise autorité."""
    imported = ", ".join(_cn_of(_dn(a, "subject")) for a in authority[:3])
    pool, current, seen = intermediates + authority, leaf, []
    while current not in seen and not _self_signed(current):
        seen.append(current)
        issuer = _dn(current, "issuer")
        parent = next((b for b in pool if _dn(b, "subject") == issuer and b != current), None)
        if parent is None:
            name = _cn_of(issuer)
            if current == leaf:
                return t(f"intermédiaire manquant : le certificat est signé par {name}, absent de l'autorité importée ({imported}). Joignez cet intermédiaire en complément. Si {name} est en fait la racine, importez-la : l'autorité importée n'est pas la bonne",
                         f"missing intermediate: the certificate is signed by {name}, not in the imported authority ({imported}). Attach this intermediate as a complement. If {name} is actually the root, import it: the imported authority is the wrong one",
                         f"falta el intermedio: el certificado está firmado por {name}, ausente de la autoridad importada ({imported}). Adjunte ese intermedio como complemento. Si {name} es en realidad la raíz, impórtela: la autoridad importada no es la correcta")
            below = _cn_of(_dn(current, "subject"))
            return t(f"mauvaise autorité : {below} est signé par {name}, mais l'autorité importée est {imported}. Importez {name}",
                     f"wrong authority: {below} is signed by {name}, but the imported authority is {imported}. Import {name}",
                     f"autoridad incorrecta: {below} está firmado por {name}, pero la autoridad importada es {imported}. Importe {name}")
        current = parent
    return t(f"mauvaise autorité : le nom correspond à {imported}, mais pas la clé qui a signé. Importez la bonne autorité",
             f"wrong authority: the name matches {imported}, but not the signing key. Import the right authority",
             f"autoridad incorrecta: el nombre coincide con {imported}, pero no la clave que firmó. Importe la autoridad correcta")


def import_ca(params, log=print):
    """Installe la chaîne de l'autorité d'entreprise (racine, intermédiaires) et l'ajoute au faisceau des agents."""
    path = os.path.join(TLS_SPOOL, tls_params("import-ca", params)["upload"] + ".pem")
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ValueError("fichier")
        blocks = _pem_blocks(_read(path))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    if not blocks:
        raise ValueError(t("aucun certificat dans le fichier", "no certificate in the file", "ningún certificado en el archivo"))
    loco = _pem_blocks(_read(os.path.join(TLS, "ca.crt")))[:1]
    blocks = [b for b in dict.fromkeys(blocks) if b not in loco]
    if not blocks or not all(_is_ca(b) for b in blocks):
        raise ValueError(t("ce ne sont pas des certificats d'autorité (CA:TRUE attendu)",
                           "these are not authority certificates (CA:TRUE expected)",
                           "no son certificados de autoridad (se espera CA:TRUE)"))
    if not any(_self_signed(b) for b in blocks):
        log(t("Aucune racine dans la chaîne : l'autorité intermédiaire fournie servira d'ancre de confiance.",
              "No root in the chain: the supplied intermediate authority will be the trust anchor.",
              "Ninguna raíz en la cadena: la autoridad intermedia proporcionada será el ancla de confianza."))
    previous = _read(tls_path("ENTERPRISE_CA"))
    _write_public(tls_path("ENTERPRISE_CA"), "\n".join(blocks) + "\n")
    if tls_mode() == "enterprise":
        try:                                  # en service : la nouvelle chaîne doit toujours valider le certificat présenté
            _check_enterprise_pair(_read(os.path.join(TLS, "server.crt")), os.path.join(TLS, "server.key"), log)
        except ValueError:
            if previous:
                _write_public(tls_path("ENTERPRISE_CA"), previous)
            else:
                os.unlink(tls_path("ENTERPRISE_CA"))
            raise ValueError(t("cette autorité ne valide pas le certificat actuellement servi : les agents perdraient la "
                               "connexion", "this authority does not validate the certificate being served: the agents "
                               "would lose the connection", "esta autoridad no valida el certificado servido: los agentes "
                               "perderían la conexión")) from None
    write_trust(log)
    for b in blocks:
        log(t("Autorité importée : ", "Authority imported: ", "Autoridad importada: ") + _x509(b, "-subject")[1].strip())
    log(t("Le faisceau d'autorités est livré à chaque agent à son prochain contact.",
          "The authority bundle is delivered to each agent at its next check-in.",
          "El conjunto de autoridades se entrega a cada agente en su próximo contacto."))
    return True


def remove_ca(params=None, log=print):
    if tls_mode() == "enterprise":
        raise ValueError(t("revenez d'abord à l'autorité loco", "switch back to the loco authority first",
                           "vuelva primero a la autoridad loco"))
    for path in (tls_path("ENTERPRISE_CA"), tls_path("ENTERPRISE_CRT"), tls_path("ENTERPRISE_KEY"), tls_path("REQUEST_KEY"), tls_path("REQUEST_CSR")):
        with contextlib.suppress(OSError):
            os.unlink(path)
    write_trust(log)
    log(t("Autorité d'entreprise retirée du faisceau des agents.", "Enterprise authority removed from the agents' bundle.",
          "Autoridad de empresa retirada del conjunto de los agentes."))
    return True


def _agent_names(extra=()):
    """Noms à couvrir : adresse des agents (IP ou DNS), nom et nom complet du serveur, noms et adresses ajoutés (les
    deux serveurs d'une grappe de haute disponibilité, en plus de son IP virtuelle)."""
    host, name, fqdn = read_conf().get("HOST", "") or "127.0.0.1", socket.gethostname(), socket.getfqdn()
    san = [f"IP:{host}"] if _valid_ip(host) else [f"DNS:{host}"]
    portal = urllib.parse.urlsplit(read_conf().get("PORTAL_URL", "")).hostname
    if portal:
        san.append(f"IP:{portal}" if _valid_ip(portal) else f"DNS:{portal}")
    san += [f"IP:{n}" for n in extra if _valid_ip(n)]
    san += [f"DNS:{n}" for n in (fqdn, name, *extra) if n and n != "localhost" and not _valid_ip(n) and CERT_NAME_RE.fullmatch(n)]
    return list(dict.fromkeys(san))


def ca_csr(params, log=print):
    """Demande de certificat (CSR) à faire signer par l'autorité d'entreprise ; la clé privée ne quitte pas le serveur."""
    names = tls_params("ca-csr", params)["names"].split()
    san = _agent_names(names)
    cn = next((n for n in (s.split(":", 1)[1] for s in san if s.startswith("DNS:")) if len(n) <= 64), "") or _short_cn()
    with tempfile.TemporaryDirectory(dir=TLS, prefix=".csr-") as tmp:
        key, csr, cnf = (os.path.join(tmp, n) for n in ("request.key", "request.csr", "req.cnf"))
        with open(cnf, "w") as f:           # configuration minimale : OpenSSL 1.1 (Rocky 8) cumule sinon celle du système
            f.write(f"[req]\nprompt=no\ndistinguished_name=dn\nreq_extensions=ext\n[dn]\nCN={cn}\nO=loco\n"
                    f"[ext]\nsubjectAltName={','.join(san)}\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\n")
        if _ssl(["req", "-new", "-newkey", "rsa:2048", "-nodes", "-config", cnf, "-keyout", key, "-out", csr], log)[0] != 0:
            raise ValueError(t("génération de la demande impossible", "request generation failed", "generación de la solicitud imposible"))
        os.chmod(key, 0o600)
        os.replace(key, tls_path("REQUEST_KEY"))
        os.chmod(csr, 0o644)
        os.replace(csr, tls_path("REQUEST_CSR"))
    log(t(f"Demande prête pour {', '.join(s.split(':', 1)[1] for s in san)} : téléchargez-la, faites-la signer par "
          "votre autorité, puis importez le certificat émis.",
          f"Request ready for {', '.join(s.split(':', 1)[1] for s in san)}: download it, have it signed by your "
          "authority, then import the issued certificate.",
          f"Solicitud lista para {', '.join(s.split(':', 1)[1] for s in san)}: descárguela, hágala firmar por su "
          "autoridad e importe el certificado emitido."))
    return True


def import_enterprise_cert(params, log=print):
    """Certificat émis par l'autorité d'entreprise (avec sa clé, ou pour la demande en attente). Préparé pour la
    bascule ; déjà en service sous cette autorité, il remplace aussitôt l'ancien (renouvellement)."""
    p = tls_params("import-enterprise-cert", params)
    crt_path, key_upload = (os.path.join(TLS_SPOOL, p["upload"] + ext) for ext in (".crt", ".key"))
    try:
        for path in (crt_path, key_upload) if p["key"] else (crt_path,):
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise ValueError("fichier")
        key_path = key_upload if p["key"] else tls_path("REQUEST_KEY")
        if not os.path.isfile(key_path):
            raise ValueError(t("aucune demande en attente : générez une demande ou joignez la clé privée",
                               "no pending request: generate a request or attach the private key",
                               "ninguna solicitud pendiente: genere una solicitud o adjunte la clave privada"))
        chain = _check_enterprise_pair(_read(crt_path), key_path, log)
        with tempfile.TemporaryDirectory(dir=TLS, prefix=".enterprise-") as tmp:
            crt, key = os.path.join(tmp, "enterprise.crt"), os.path.join(tmp, "enterprise.key")
            _write_public(crt, chain)
            shutil.copyfile(key_path, key)
            os.chmod(key, 0o600)
            os.replace(crt, tls_path("ENTERPRISE_CRT"))
            os.replace(key, tls_path("ENTERPRISE_KEY"))
        if not p["key"]:
            for path in (tls_path("REQUEST_KEY"), tls_path("REQUEST_CSR")):
                with contextlib.suppress(OSError):
                    os.unlink(path)
    finally:
        for path in (crt_path, key_upload):
            with contextlib.suppress(OSError):
                os.unlink(path)
    log(t("Certificat d'entreprise importé : ", "Enterprise certificate imported: ", "Certificado de empresa importado: ")
        + ", ".join(s.split(":", 1)[1] for s in _agent_names()) + ".")
    if tls_mode() == "enterprise":
        return set_tls_mode({"mode": "enterprise"}, log)
    return True


def _short_cn():
    """Nom commun d'un certificat : 64 caractères au plus (au-delà, OpenSSL refuse la demande ; un nom complet de
    machine dans un nuage les dépasse souvent). Les noms qui comptent sont dans subjectAltName."""
    name = socket.gethostname() or "loco"
    return name if len(name) <= 64 else name.split(".")[0][:63] or "loco"


def _loco_cert(tmp, log, extra=()):
    """Certificat serveur signé par l'autorité loco (adresse et noms actuels) : (clé, certificat) dans tmp."""
    ca_crt, ca_key = os.path.join(TLS, "ca.crt"), os.path.join(TLS, "ca.key")
    if not (os.path.isfile(ca_crt) and os.path.isfile(ca_key)):
        raise ValueError(t("autorité locale introuvable", "local authority not found", "autoridad local no encontrada"))
    san = _agent_names(extra)
    san += [s for s in ("IP:127.0.0.1", "DNS:localhost") if s not in san]
    key, csr, crt, ext = (os.path.join(tmp, n) for n in ("server.key", "server.csr", "server.crt", "ext.cnf"))
    with open(ext, "w") as f:
        f.write("subjectAltName=" + ",".join(san) + "\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                "extendedKeyUsage=serverAuth\n")
    steps = (["req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
              "-subj", f"/O=loco/CN={_short_cn()}", "-keyout", key, "-out", csr],
             ["x509", "-req", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key, "-set_serial", "0x" + secrets.token_hex(16),
              "-days", "825", "-out", crt, "-extfile", ext],
             ["verify", "-CAfile", ca_crt, crt])
    for step in steps:
        if _ssl(step, log)[0] != 0:
            raise ValueError(t("génération du certificat impossible", "certificate generation failed", "generación del certificado imposible"))
    return key, crt, san


def set_tls_mode(params, log=print):
    """Bascule le certificat du portail sur l'autorité choisie ; nginx rechargé, ou tout est rétabli."""
    mode = tls_params("set-tls-mode", params)["mode"]
    if os.path.exists(os.path.join(TLS, "custom")):
        raise ValueError(t("certificat fourni à l'installation (--tls-cert)", "certificate supplied at installation (--tls-cert)",
                           "certificado proporcionado en la instalación (--tls-cert)"))
    with tempfile.TemporaryDirectory(dir=TLS, prefix=".mode-") as tmp:
        if mode == "enterprise":
            chain = _check_enterprise_pair(_read(tls_path("ENTERPRISE_CRT")), tls_path("ENTERPRISE_KEY"), log)
            crt, key = os.path.join(tmp, "server.crt"), os.path.join(tmp, "server.key")
            _write_public(crt, chain)
            shutil.copyfile(tls_path("ENTERPRISE_KEY"), key)
            names = [s.split(":", 1)[1] for s in _agent_names()]
        else:
            key, crt, san = _loco_cert(tmp, log)
            names = [s.split(":", 1)[1] for s in san]
        write_trust(log)
        _swap_tls({"server.key": key, "server.crt": crt}, log)
    if mode == "enterprise":
        _write_public(tls_path("TLS_MODE"), "enterprise\n")
        log(t("Le portail présente désormais le certificat de votre autorité d'entreprise (",
              "The portal now presents your enterprise authority's certificate (",
              "El portal presenta ahora el certificado de su autoridad de empresa (") + ", ".join(names) + ").")
    else:
        with contextlib.suppress(OSError):
            os.unlink(tls_path("TLS_MODE"))
        log(t("Retour à l'autorité loco : nouveau certificat pour ", "Back to the loco authority: new certificate for ",
              "Vuelta a la autoridad loco: nuevo certificado para ") + ", ".join(names) + ".")
    return True


def delete_backup(params, log=print):
    """Supprime une sauvegarde nommée de BACKUP_DIR (fichier ordinaire, jamais un lien symbolique)."""
    name = str((params or {}).get("name", ""))
    if not BACKUP_NAME_RE.fullmatch(name):
        raise ValueError("nom de sauvegarde invalide")
    path = os.path.join(read_conf().get("BACKUP_DIR", "/var/backups/loco"), name)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ValueError("pas un fichier ordinaire")
        os.unlink(path)
    except OSError:
        raise ValueError(t("sauvegarde introuvable", "backup not found", "copia de seguridad no encontrada")) from None
    log(t(f"Sauvegarde supprimée : {name}.", f"Backup deleted: {name}.", f"Copia eliminada: {name}."))
    return True


# --------------------------------------------------------------------------- caches (root)
# Le portail (compte loco) ne peut pas lire les caches nginx (700, propriété nginx) ni le cache
# apt-cacher-ng : l'agent les parcourt, publie un index lisible (0644) et applique les purges.

CACHE_REQUESTS = os.path.join(AGENT_DIR, "cache")      # demandes du portail (0730 root:loco)
CACHE_INDEX = os.path.join(AGENT_DIR, "cache.json")    # index publié (0644)
CACHE_NAMES = ("wu", "rpm", "flatpak", "apt")
CACHE_MAX_ENTRIES = 300        # objets listés par cache (les plus récents)
CACHE_MAX_HEADERS = 8000       # en-têtes nginx lus par analyse (borne le coût d'un gros cache)
_CACHE_ID_RE = re.compile(r"[0-9a-f]{16}")
_SLICE_RE = re.compile(rb"[ /]?bytes=\d+-\d*\Z")       # suffixe de tranche nginx (WU) à fusionner


def _cache_dir(name):
    data = read_conf().get("DATA", "/srv/loco")
    if name == "apt":
        return os.path.join(data, "aptcache") if ALPINE else "/var/cache/apt-cacher-ng"
    return os.path.join(data, {"wu": "wucache", "rpm": "rpmcache", "flatpak": "flatpakcache"}[name])


def _cache_kind(name):
    return "acng" if (name == "apt" and not ALPINE) else "nginx"


def _cache_walk(root):
    """Fichiers de données d'un cache, hors écritures en cours."""
    for path, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("tmp", "_actmp")]   # nginx / apt-cacher-ng : transferts en cours
        for name in files:
            yield os.path.join(path, name)


def _nginx_key(path):
    """Clé de cache nginx, stockée en clair en tête du fichier : « \\nKEY: <clé>\\n »."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    i = head.find(b"\nKEY: ")
    if i < 0:
        return None
    end = head.find(b"\n", i + 6)
    return head[i + 6:end] if end > 0 else None


def _logical(key):
    """Objet logique : les tranches (bytes=…) d'un même fichier sont fusionnées."""
    return _SLICE_RE.sub(b"", key)


def _entry_id(logical):
    return hashlib.sha256(logical).hexdigest()[:16]


def _entry_name(logical):
    text = urllib.parse.unquote(logical.decode("utf-8", "replace")).split("?", 1)[0].rstrip("/")
    name = text.rsplit("/", 1)[-1] or text
    context = text[: len(text) - len(name)].strip("/")
    return name[:160], context[:200]


def _cache_iso(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _scan_nginx(root):
    files, total, count = [], 0, 0
    for path in _cache_walk(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        total += st.st_size
        count += 1
        files.append((st.st_mtime, st.st_size, path))
    files.sort(reverse=True)                            # en-têtes lus sur les fichiers les plus récents d'abord
    objs = {}
    for mtime, size, path in files[:CACHE_MAX_HEADERS]:
        key = _nginx_key(path)
        if not key:
            continue
        logical = _logical(key)
        eid = _entry_id(logical)
        o = objs.get(eid)
        if o is None:
            name, context = _entry_name(logical)
            o = objs[eid] = {"id": eid, "name": name, "context": context, "bytes": 0, "parts": 0, "at": 0}
        o["bytes"] += size
        o["parts"] += 1
        o["at"] = max(o["at"], mtime)
    return total, count, objs


def _scan_acng(root):
    """apt-cacher-ng : l'arborescence reflète l'URL ; chaque paquet a un fichier d'en-têtes « .head » à part."""
    objs, total, count = {}, 0, 0
    for path in _cache_walk(root):
        rel = os.path.relpath(path, root)
        base = os.path.basename(rel)
        if base.endswith(".head") or rel.startswith("_") or base.startswith("."):
            continue                                   # _xstore, index internes et fichiers de contrôle
        try:
            st = os.stat(path)
        except OSError:
            continue
        total += st.st_size
        count += 1
        eid = _entry_id(rel.encode())
        objs[eid] = {"id": eid, "name": base[:160], "context": rel[: len(rel) - len(base)].strip("/")[:200],
                     "bytes": st.st_size, "parts": 1, "at": st.st_mtime}
    return total, count, objs


def _entry_kind(area, name, context):
    """Catégorie lisible d'un objet (le portail traduit le code en libellé) : à quoi le fichier correspond."""
    low = name.lower() + " " + context.lower()
    if area == "wu":
        if any(k in low for k in ("defender", "mpam", "nis_", "am_delta", "am_base", "safeos")):
            return "win-defender"
        if "msedge" in low or "microsoftedge" in low:
            return "win-edge"
        if "office" in low or "officecdn" in low:
            return "win-office"
        if name.lower().endswith(".esd"):
            return "win-image"
        return "win-update"
    if area in ("apt", "rpm"):
        return "package" if name.lower().endswith((".deb", ".udeb", ".ddeb", ".rpm")) else "repo"
    if area == "flatpak":
        return "flatpak"
    return ""


def _cache_state(name):
    root = _cache_dir(name)
    if not os.path.isdir(root):
        return {"present": False}
    total, count, objs = (_scan_nginx if _cache_kind(name) == "nginx" else _scan_acng)(root)
    entries = sorted(objs.values(), key=lambda o: o["at"], reverse=True)[:CACHE_MAX_ENTRIES]
    for o in entries:
        o["kind"] = _entry_kind(name, o["name"], o["context"])
        o["at"] = _cache_iso(o["at"])
    return {"present": True, "bytes": total, "count": count, "shown": len(entries),
            "scanned": _now(), "entries": entries}


def write_cache_index(names=None):
    """(Re)construit l'index des caches demandés et le fusionne dans le fichier publié (0644)."""
    index = _read_json(CACHE_INDEX)
    caches = index.get("caches") if isinstance(index.get("caches"), dict) else {}
    for name in (names or CACHE_NAMES):
        if name in CACHE_NAMES:
            try:
                caches[name] = _cache_state(name)
            except OSError as e:
                caches[name] = {"present": False, "error": str(e)}
    _write_json(CACHE_INDEX, {"updated": _now(), "caches": caches})


def _unlink_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        os.unlink(path)
    except OSError:
        return 0
    return size


def purge_cache(name, entry_id=None, everything=False):
    """Supprime un objet (toutes ses tranches) ou vide le cache. Retourne les octets libérés."""
    if name not in CACHE_NAMES:
        return 0
    root = _cache_dir(name)
    if not os.path.isdir(root):
        return 0
    freed = 0
    if everything:
        for path in _cache_walk(root):
            freed += _unlink_size(path)
        for path, _dirs, _files in os.walk(root, topdown=False):
            if path != root:
                with contextlib.suppress(OSError):
                    os.rmdir(path)
        return freed
    if not entry_id or not _CACHE_ID_RE.fullmatch(entry_id):
        return 0
    nginx = _cache_kind(name) == "nginx"
    for path in _cache_walk(root):
        if nginx:
            key = _nginx_key(path)
            if key and _entry_id(_logical(key)) == entry_id:
                freed += _unlink_size(path)
        elif not path.endswith(".head") and _entry_id(os.path.relpath(path, root).encode()) == entry_id:
            freed += _unlink_size(path)
            _unlink_size(path + ".head")               # en-têtes apt-cacher-ng associés
    return freed


def _cache_prepare():
    os.makedirs(CACHE_REQUESTS, exist_ok=True)
    shutil.chown(CACHE_REQUESTS, "root", "loco")       # le portail y dépose, sans lister ni lire les autres
    os.chmod(CACHE_REQUESTS, 0o730)


def cache_worker():
    """Fil dédié de l'agent : applique les demandes d'analyse et de purge, hors du verrou des opérations."""
    try:
        _cache_prepare()
    except OSError as e:
        print(f"gestion du cache indisponible : {e}", file=sys.stderr)
        return
    # Régénérer au démarrage (fil de fond) : après une mise à jour de loco, l'index reflète le nouveau format
    # (nouveaux champs comme « kind » des libellés) sans attendre une demande d'analyse du portail.
    with contextlib.suppress(OSError):
        write_cache_index()
    while True:
        try:
            names = sorted(os.listdir(CACHE_REQUESTS))
        except OSError:
            names = []
        for fn in names:
            path = os.path.join(CACHE_REQUESTS, fn)
            if not fn.endswith(".json"):
                if fn.endswith(".tmp") and time.time() - os.lstat(path).st_mtime > 300:
                    _unlink_size(path)
                continue
            req = _read_request(path)
            _unlink_size(path)
            if not req:
                continue
            op, cache = req.get("op"), req.get("cache")
            if cache is not None and cache != "" and cache not in CACHE_NAMES:
                continue
            try:
                if op == "purge" and cache:
                    purge_cache(cache, req.get("id"), bool(req.get("all")))
                    write_cache_index([cache])
                elif op == "scan":
                    write_cache_index([cache] if cache else None)
            except OSError:
                pass
        time.sleep(1.5)


# -------- côté portail (compte loco) : dépôt de demandes et lecture de l'index

def cache_request(op, cache="", entry_id="", everything=False, user=""):
    """Dépôt d'une demande de cache par le portail ; l'agent la revalide et l'applique."""
    if op not in ("scan", "purge"):
        raise ValueError("opération inconnue")
    if cache and cache not in CACHE_NAMES:
        raise ValueError("cache inconnu")
    payload = {"op": op, "cache": cache, "user": str(user)[:64]}
    if op == "purge":
        if not cache:
            raise ValueError("cache requis")
        if everything:
            payload["all"] = True
        elif _CACHE_ID_RE.fullmatch(str(entry_id)):
            payload["id"] = str(entry_id)
        else:
            raise ValueError("élément invalide")
    base = os.path.join(CACHE_REQUESTS, f"{time.time():.6f}-{uuid.uuid4().hex[:6]}")
    try:
        with open(base + ".tmp", "x") as f:
            json.dump(payload, f)
        os.rename(base + ".tmp", base + ".json")
    except OSError:
        raise ValueError("agent d'administration indisponible (service loco-agent)") from None


def cache_index():
    """Index publié des caches, pour le portail (dict éventuellement vide)."""
    return _read_json(CACHE_INDEX)


def _operation(action, params, log):
    if action == "check":
        return check(log)
    if action == "update-loco":
        return update_loco(log)
    if action == "update-system":
        return update_system(log)
    if action == "update-security":
        return update_system(log, security_only=True)
    if action == "configure":
        return configure(params, log)
    if action == "set-https-port":
        return set_https_port(params, log)
    if action == "set-portal-url":
        return set_portal_url(params, log)
    if action == "set-internal-hosts":
        return set_internal_hosts(params, log)
    if action == "reboot":
        return reboot(log)
    if action == "set-repo":
        return set_repo(params, log)
    if action == "set-time":
        return set_time(params, log)
    if action == "install-qemu-agent":
        return install_qemu_agent(log)
    if action == "restore":
        return restore(params, log)
    if action == "set-backup-keep":
        return set_backup_keep(params, log)
    if action == "delete-backup":
        return delete_backup(params, log)
    if action == "set-shell":
        return set_shell(params, log)
    if action == "renew-cert":
        return renew_cert(params, log)
    if action == "import-cert":
        return import_cert(params, log)
    if action == "remove-imported-cert":
        return remove_imported_cert(params, log)
    if action == "import-ca":
        return import_ca(params, log)
    if action == "remove-ca":
        return remove_ca(params, log)
    if action == "ca-csr":
        return ca_csr(params, log)
    if action == "import-enterprise-cert":
        return import_enterprise_cert(params, log)
    if action == "set-tls-mode":
        return set_tls_mode(params, log)
    if action == "ha-enable":
        return _ha().enable(params, log, sys.modules[__name__])
    if action == "ha-join":
        return _ha().join(params, log, sys.modules[__name__])
    if action == "ha-peer":
        return _ha().add_peer(params, log, sys.modules[__name__])
    if action == "ha-failover":
        return _ha().failover(log)
    if action == "ha-disable":
        return _ha().disable(log, sys.modules[__name__])
    if action == "ha-reconfigure":
        return _install(read_conf().get("SOURCE_DIR", ""), [], log) == 0
    raise ValueError(f"action inconnue : {action}")


class Busy(Exception):
    pass


def ha_worker():
    """Haute disponibilité : réplication (principal → secours) et état publié, toutes les ~10 s."""
    ha, cache = _ha(), {}

    def reconfigure():
        if running_job() is None:
            _submit("ha-reconfigure", user="loco-ha")
    hooks = {"reconfigure": reconfigure, "reload_nginx": lambda: _reload_nginx(lambda _text: None),
             "restart_portal": lambda: ha.service("loco" if ALPINE else "loco-portal", "restart")}
    while True:
        try:
            ha.cycle(current_version(), hooks, cache, log=ha.event)
        except Exception as e:  # noqa: BLE001 - le fil ne doit jamais s'arrêter
            print(f"haute disponibilité : {e}", file=sys.stderr)
        time.sleep(ha.CYCLE)


def _lock(wait):
    """Une seule opération à la fois (agent et commandes)."""
    os.makedirs(AGENT_DIR, mode=0o755, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        raise Busy("une autre opération est en cours (portail ou console)") from None
    return fd


# --------------------------------------------------------------------------- agent (root)

def _job_paths(job_id):
    return os.path.join(JOBS, job_id + ".json"), os.path.join(JOBS, job_id + ".log")


def run_job(job_id):
    """Opération détachée : survit au redémarrage du portail et de l'agent pendant une mise à jour."""
    if not JOB_RE.fullmatch(job_id):
        raise SystemExit("identifiant invalide")
    meta_path, log_path = _job_paths(job_id)
    job = _read_json(meta_path)
    with open(log_path, "a", buffering=1) as log_file:
        def log(text):
            log_file.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        job["pid"] = os.getpid()
        _write_json(meta_path, job)
        fd = _lock(wait=True)
        job.update(state="running", started=_now())
        _write_json(meta_path, job)
        ok = False
        try:
            with contextlib.ExitStack() as stack:
                if job["action"] in HA_MAINTENANCE and ha_configured():
                    stack.enter_context(_ha().maintenance())
                ok = _operation(job["action"], job.get("params") or {}, log)
        except Exception as e:  # noqa: BLE001 - tout échec est rapporté dans le journal de l'opération
            log(f"Erreur : {e}")
        job.update(state="success" if ok else "failed", finished=_now())
        _write_json(meta_path, job)
        if job["action"] in ("update-loco", "update-system", "update-security", "configure"):
            try:
                check(lambda _text: None)
            except Exception as e:  # noqa: BLE001
                log(t(f"Vérification des versions impossible : {e}", f"Version check failed: {e}", f"No se pudo comprobar las versiones: {e}"))
        os.close(fd)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def jobs(limit=10):
    try:
        names = sorted((n for n in os.listdir(JOBS) if n.endswith(".json")), reverse=True)[:limit]
    except OSError:
        return []
    return [j for j in (_read_json(os.path.join(JOBS, n)) for n in names) if j]


def running_job():
    for job in jobs(limit=5):
        if job.get("state") in ("queued", "running"):
            return job
    return None


def _submit(action, params=None, user=""):
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    meta_path, log_path = _job_paths(job_id)
    job = {"id": job_id, "action": action, "params": params or {}, "user": user[:64], "state": "queued",
           "queued": _now()}
    _write_json(meta_path, job)
    with open(log_path, "a"):
        pass
    os.chmod(log_path, 0o644)
    subprocess.Popen([sys.executable, "-I", HERE, "run-job", job_id], start_new_session=True, cwd="/",
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Laisser à l'opération le temps d'enregistrer son processus (voir le nettoyage au démarrage de l'agent).
    for _ in range(50):
        if _read_json(meta_path).get("pid"):
            break
        time.sleep(0.1)
    return job_id


def _read_request(path):
    """Demande déposée par le compte loco : fichier ordinaire, sans suivre de lien, taille bornée."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_REQUEST:
            return None
        data = json.loads(os.read(fd, MAX_REQUEST))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _prepare_dirs():
    os.makedirs(AGENT_DIR, mode=0o755, exist_ok=True)
    os.makedirs(JOBS, mode=0o755, exist_ok=True)
    os.makedirs(REQUESTS, exist_ok=True)
    for path, mode in ((AGENT_DIR, 0o755), (JOBS, 0o755)):
        os.chown(path, 0, 0)
        os.chmod(path, mode)
    # Le portail y dépose des fichiers sans pouvoir lister ni lire ceux des autres.
    shutil.chown(REQUESTS, "root", "loco")
    os.chmod(REQUESTS, 0o730)
    with contextlib.suppress(OSError):
        _cache_prepare()


def _prune_jobs(keep=40):
    try:
        names = sorted(n[:-5] for n in os.listdir(JOBS) if n.endswith(".json"))
    except OSError:
        return
    for job_id in names[:-keep]:
        for path in _job_paths(job_id):
            try:
                os.unlink(path)
            except OSError:
                pass


def agent():
    """Service loco-agent : traite les demandes du portail et vérifie les versions chaque jour."""
    _prepare_dirs()
    _share_backups()
    # Shell root du portail : jeton de ce démarrage, puis broker (refuse tout tant que le shell n'est pas actif).
    try:
        _shell_runtime()
        threading.Thread(target=shell_broker, name="loco-shell", daemon=True).start()
    except OSError as e:
        print(f"shell root indisponible : {e}", file=sys.stderr)
    threading.Thread(target=cache_worker, name="loco-cache", daemon=True).start()
    threading.Thread(target=ha_worker, name="loco-ha", daemon=True).start()
    code = os.stat(HERE).st_mtime_ns
    beat = timecheck = 0
    # Opérations interrompues (arrêt brutal) : marquées comme échouées.
    for job in jobs(limit=40):
        if job.get("state") in ("queued", "running") and not _alive(job.get("pid")):
            job.update(state="failed", finished=_now())
            _write_json(_job_paths(job["id"])[0], job)
    # Après un redémarrage (ou une relance de l'agent), l'état du noyau et des services a pu changer : nouvelle
    # vérification une minute plus tard, le temps que le réseau soit prêt.
    status = read_status()
    recheck = time.time() + 60 if (status.get("reboot_required") or status.get("stale_services") or "os" not in status) else 0
    # Sur l'appliance (chrony + cmdport 0), rendre l'état de l'heure lisible pour l'agent, puis le publier aussitôt.
    try:
        _ensure_chrony_readable()
        write_time_status()
    except Exception as e:                                 # l'heure ne doit jamais empêcher l'agent de démarrer
        print(f"état de l'heure indisponible : {e}", file=sys.stderr)
    hosts_stamp = None
    while True:
        # Noms internes : à appliquer après une réplication, une restauration, ou si /etc/hosts a été réécrit (setup-loco).
        if _hosts_stamp() != hosts_stamp:
            with contextlib.suppress(OSError):
                apply_internal_hosts()
            hosts_stamp = _hosts_stamp()
        for job in jobs(limit=40):
            if job.get("state") in ("queued", "running") and job.get("pid") and not _alive(job["pid"]):
                job.update(state="failed", finished=_now())
                _write_json(_job_paths(job["id"])[0], job)
        busy = running_job() is not None
        if not busy:
            for name in sorted(os.listdir(REQUESTS)):
                path = os.path.join(REQUESTS, name)
                if not name.endswith(".json"):
                    if name.endswith(".tmp") and time.time() - os.lstat(path).st_mtime > 300:
                        os.unlink(path)
                    continue
                req = _read_request(path)
                try:
                    os.unlink(path)
                except OSError:
                    continue
                if not req or req.get("action") not in ACTIONS or (req["action"] in APPLIANCE_ACTIONS and not ALPINE):
                    continue
                params = {}
                if req["action"] == "configure":
                    try:
                        params = validate(req.get("params"))
                    except ValueError:
                        continue
                elif req["action"] == "set-repo":
                    new = str((req.get("params") or {}).get("repo", "")).strip()
                    if not valid_source(new):
                        continue
                    params = {"repo": new}
                elif req["action"] == "set-time":
                    p = req.get("params") or {}
                    tz = str(p.get("timezone", "")).strip()
                    try:
                        server = " ".join(ntp_servers(p.get("ntp_server", "")))
                    except ValueError:
                        continue
                    if tz and not _valid_tz(tz):
                        continue
                    params = {"timezone": tz, "ntp_server": server,
                              "ntp": "1" if str(p.get("ntp", "")) in ("1", "true", "on", "yes") else "",
                              "ntp_only": "1" if str(p.get("ntp_only", "")) == "1" else ""}
                elif req["action"] == "restore":
                    p = req.get("params") or {}
                    upload, name = str(p.get("upload", "")).strip(), str(p.get("archive", "")).strip()
                    if upload and UPLOAD_NAME_RE.fullmatch(upload):
                        params = {"upload": upload}
                    elif BACKUP_NAME_RE.fullmatch(name):
                        params = {"archive": name}
                    else:
                        continue
                elif req["action"] == "set-backup-keep":
                    try:
                        keep = int(str((req.get("params") or {}).get("keep", "")))
                    except ValueError:
                        continue
                    if not 1 <= keep <= 60:
                        continue
                    params = {"keep": keep}
                elif req["action"] == "delete-backup":
                    name = str((req.get("params") or {}).get("name", "")).strip()
                    if not BACKUP_NAME_RE.fullmatch(name):
                        continue
                    params = {"name": name}
                elif req["action"] == "set-shell":
                    params = {"enabled": "1" if str((req.get("params") or {}).get("enabled", "")) == "1" else ""}
                elif req["action"] == "import-cert":
                    upload = str((req.get("params") or {}).get("upload", ""))
                    if not TLS_UPLOAD_RE.fullmatch(upload):
                        continue
                    params = {"upload": upload}
                elif req["action"] in ("import-ca", "remove-ca", "ca-csr", "import-enterprise-cert", "set-tls-mode"):
                    try:
                        params = tls_params(req["action"], req.get("params"))
                    except ValueError:
                        continue
                elif req["action"] == "set-https-port":
                    try:
                        params = https_port_params(req.get("params"))
                    except ValueError:
                        continue
                elif req["action"] == "set-portal-url":
                    try:
                        params = portal_params(req.get("params"))
                    except ValueError:
                        continue
                elif req["action"] == "set-internal-hosts":
                    try:
                        params = hosts_params(req.get("params"))
                    except ValueError:
                        continue
                elif req["action"] in HA_ACTIONS:
                    try:
                        params = ha_params(req["action"], req.get("params"))
                    except ValueError:
                        continue
                _submit(req["action"], params, str(req.get("user", "")))
                busy = True
                break
        status = read_status()
        checked = status.get("checked", "")
        # Dépôts du système injoignables : nouvel essai au bout d'une heure plutôt que le lendemain (GitHub injoignable,
        # fréquent sur un réseau fermé, ne compte pas).
        every = RETRY_EVERY if status.get("system_error") else CHECK_EVERY
        if not busy and (not checked or time.time() - _ts(checked) > every or (recheck and time.time() > recheck)):
            _submit("check", user="loco-agent")
            recheck = 0
        _prune_jobs()
        if time.time() - timecheck > 15:
            timecheck = time.time()
            write_time_status()
        if time.time() - beat > 20:
            beat = time.time()
            with open(HEARTBEAT, "w") as f:
                f.write(_now())
        # Code remplacé par une mise à jour : quitter, supervise-daemon relance la nouvelle version.
        try:
            if os.stat(HERE).st_mtime_ns != code:
                return
        except OSError:
            pass
        time.sleep(2)


def _ts(text):
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0


# --------------------------------------------------------------------------- portail (compte loco)

def request(action, params=None, user=""):
    """Dépôt d'une demande par le portail ; l'agent la revalide avant de l'exécuter."""
    if action not in ACTIONS or (action in APPLIANCE_ACTIONS and not ALPINE):
        raise ValueError("action inconnue")
    if action == "configure":
        params = validate(params or {})
        if not params:
            raise ValueError("aucun changement")
    elif action == "set-repo":
        new = str((params or {}).get("repo", "")).strip()
        if not valid_source(new):
            raise ValueError("dépôt GitHub invalide")
        params = {"repo": new}
    elif action == "set-time":
        tz = str((params or {}).get("timezone", "")).strip()
        server = " ".join(ntp_servers((params or {}).get("ntp_server", "")))
        if tz and not _valid_tz(tz):
            raise ValueError("fuseau horaire inconnu")
        params = {"timezone": tz, "ntp_server": server,
                  "ntp": "1" if str((params or {}).get("ntp", "")) in ("1", "true", "on", "yes") else "",
                  "ntp_only": "1" if str((params or {}).get("ntp_only", "")) == "1" else ""}
    elif action == "restore":
        p = params or {}
        upload, name = str(p.get("upload", "")).strip(), str(p.get("archive", "")).strip()
        if upload:
            if not UPLOAD_NAME_RE.fullmatch(upload):
                raise ValueError("fichier téléversé invalide")
            params = {"upload": upload}
        elif BACKUP_NAME_RE.fullmatch(name):
            params = {"archive": name}
        else:
            raise ValueError("nom de sauvegarde invalide")
    elif action == "set-backup-keep":
        try:
            keep = int(str((params or {}).get("keep", "")))
        except ValueError:
            raise ValueError("nombre de sauvegardes invalide") from None
        if not 1 <= keep <= 60:
            raise ValueError("nombre de sauvegardes hors limites (1 à 60)")
        params = {"keep": keep}
    elif action == "delete-backup":
        name = str((params or {}).get("name", "")).strip()
        if not BACKUP_NAME_RE.fullmatch(name):
            raise ValueError("nom de sauvegarde invalide")
        params = {"name": name}
    elif action == "set-shell":
        params = {"enabled": "1" if str((params or {}).get("enabled", "")) == "1" else ""}
    elif action == "import-cert":
        upload = str((params or {}).get("upload", ""))
        if not TLS_UPLOAD_RE.fullmatch(upload):
            raise ValueError("fichier téléversé invalide")
        params = {"upload": upload}
    elif action in ("import-ca", "remove-ca", "ca-csr", "import-enterprise-cert", "set-tls-mode"):
        params = tls_params(action, params)
    elif action == "set-https-port":
        params = https_port_params(params)
    elif action == "set-portal-url":
        params = portal_params(params)
    elif action == "set-internal-hosts":
        params = hosts_params(params)
    elif action in HA_ACTIONS:
        params = ha_params(action, params)
    else:
        params = {}
    path = os.path.join(REQUESTS, f"{time.time():.6f}-{uuid.uuid4().hex[:6]}")
    try:
        with open(path + ".tmp", "x") as f:
            json.dump({"action": action, "params": params, "user": user}, f)
        os.rename(path + ".tmp", path + ".json")
    except OSError:
        raise ValueError("agent d'administration indisponible (service loco-agent)") from None


def job_log(job_id, lines=400):
    if not JOB_RE.fullmatch(job_id or ""):
        return ""
    try:
        with open(_job_paths(job_id)[1], errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def agent_running():
    """Vrai si l'agent tourne (le portail, sans droits root, ne peut pas interroger OpenRC)."""
    try:
        return time.time() - os.stat(HEARTBEAT).st_mtime < 60
    except OSError:
        return False


# --------------------------------------------------------------------------- commandes (console, root)

def _console(action, params=None):
    try:
        fd = _lock(wait=False)
    except Busy as e:
        print(f"Erreur : {e}.", file=sys.stderr)
        return 1
    try:
        ok = _operation(action, params or {}, lambda text: print(text, flush=True))
    except (OSError, ValueError) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        ok = False
    finally:
        os.close(fd)
    if ok and action in ("update-loco", "update-system", "update-security", "configure"):
        try:
            check(lambda _text: None)
        except Exception:  # noqa: BLE001 - la vérification reste facultative
            pass
    return 0 if ok else 1


UPDATE_HELP = """Usage : loco-update [COMMANDE]
  status    versions installées et disponibles (dernière vérification)
  check     vérifie les versions disponibles (loco et paquets du système)
  loco      installe la dernière version publiée de loco (appliance Alpine ; retour arrière en cas d'échec)
  system    met à jour tous les paquets du système (noyau compris)
  security  installe les seuls correctifs de sécurité (Debian, Ubuntu, Rocky Linux)
  all       loco puis le système (appliance Alpine)
  reboot    redémarre le serveur"""

CONFIG_HELP = """Usage : loco-config [show | set CLÉ=VALEUR… | portal-url […]]
  show                  réglages actuels (appliance)
  set CLÉ=VALEUR…       applique les réglages, en relançant l'installation (appliance)
  portal-url            adresse du portail pour les navigateurs (tout serveur Linux), sans réinstallation :
                          portal-url https://nom:port [--add-to-certificate]    portal-url --default
Réseau, nom et mot de passe root : setup-loco (console)."""


def _print_status():
    status = read_status()
    latest = status.get("latest", {})
    print(f"loco installé      : {current_version()}")
    print(f"Dernière publiée   : {latest.get('version', 'inconnue')}"
          + (" (mise à jour disponible)" if latest and version_key(latest["version"]) > version_key(current_version()) else ""))
    print(f"Paquets du système : {len(status.get('system_updates', []))} à mettre à jour"
          + (f" dont {status.get('security_count', 0)} de sécurité" if status.get("security_supported") else ""))
    reasons = [_reason_log(r) for r in reboot_reasons(stale_services())]
    print(f"Redémarrage requis : {'oui (' + '; '.join(reasons) + ')' if reasons else 'non'}")
    print(f"Vérifié le         : {status.get('checked', 'jamais')}")
    for e in status.get("errors", []):
        print(f"  ! {_error_log(e)}")


def main(argv):
    if os.geteuid() != 0:
        print("Erreur : lancez cette commande en root.", file=sys.stderr)
        return 1
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "agent":
        agent()
        return 0
    if cmd == "hosts-apply":                               # install.sh : bloc des noms internes dans /etc/hosts
        apply_internal_hosts()
        return 0
    if cmd == "tls-sni":                                   # install.sh : bloc nginx du certificat importé, à jour
        write_sni_site()
        return 0
    if cmd == "tls-trust":                                 # install.sh : faisceau des agents, même format que l'agent root
        write_trust()
        return 0
    if cmd == "run-job" and len(args) == 1:
        run_job(args[0])
        return 0
    if cmd == "ha-check":                                  # keepalived : ce serveur peut-il porter l'IP virtuelle ?
        return _ha().check()
    if cmd == "ha-notify":                                 # keepalived : « INSTANCE loco MASTER 150 »
        _ha().notify(args)
        return 0
    if cmd == "update":
        sub = args[0] if args else "status"
        if sub == "status":
            _print_status()
            return 0
        if sub in ("all", "loco") and not ALPINE:
            print("Erreur : hors appliance, mettez loco à jour avec install.sh de la nouvelle version.", file=sys.stderr)
            return 1
        if sub == "all":
            return _console("update-loco") or _console("update-system")
        actions = {"check": "check", "loco": "update-loco", "system": "update-system", "security": "update-security",
                   "reboot": "reboot"}
        if sub not in actions or len(args) > 1:
            print(UPDATE_HELP, file=sys.stderr)
            return 0 if sub in ("-h", "--help") else 2
        return _console(actions[sub])
    if cmd == "config" and args[:1] == ["portal-url"]:
        wanted = [a for a in args[1:] if not a.startswith("--")]
        if not wanted and "--default" not in args:
            print(read_conf().get("PORTAL_URL", "") or t("(adresse des agents)", "(agents' address)", "(dirección de los agentes)"))
            return 0
        return _console("set-portal-url", {"url": (wanted or [""])[0], "extend": "1" if "--add-to-certificate" in args else ""})
    if cmd == "config":
        if not ALPINE and not set(args) & {"-h", "--help"}:
            print("Erreur : réglages de l'appliance Alpine ; ailleurs, relancez install.sh avec les options voulues.",
                  file=sys.stderr)
            return 1
        sub = args[0] if args else "show"
        if sub == "show":
            conf = read_conf()
            width = max(len(k) for k in SETTINGS)
            for key, (label, _, _) in SETTINGS.items():
                print(f"{key:<{width}}  {conf.get(key, '') or '(automatique)':<40}  {label}")
            return 0
        if sub == "set" and len(args) > 1 and all("=" in a for a in args[1:]):
            return _console("configure", dict(a.split("=", 1) for a in args[1:]))
        print(CONFIG_HELP, file=sys.stderr)
        return 0 if sub in ("-h", "--help") else 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
