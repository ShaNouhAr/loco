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
ACTIONS = ("check", "update-loco", "update-system", "update-security", "reboot", "configure", "set-repo", "set-time", "install-qemu-agent", "restore", "set-backup-keep", "delete-backup", "set-shell")
TZ_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
NTP_SERVER_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.:_-]*[A-Za-z0-9])?")
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


def t(fr, en, es=None):
    """Texte d'un journal d'opération, dans la langue du serveur (anglais par défaut)."""
    if LANG == "fr":
        return fr
    if LANG == "es" and es is not None:
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


def _get(url, timeout=15):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(_proxies()))
    req = urllib.request.Request(url, headers={"User-Agent": f"loco/{current_version()}",
                                               "Accept": "application/vnd.github+json"})
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
    return {"version": version, "url": data.get("html_url", ""), "published": data.get("published_at", ""),
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
    return {"version": version, "url": rel.get("_links", {}).get("self", url), "published": rel.get("released_at", ""),
            "notes": (rel.get("description") or "")[:6000],
            "archive": links.get(f"loco-{version}.tar.gz", ""), "sums": links.get("SHA256SUMS", "")}




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
    log(t(f"{os_label} : {count} paquet(s) à mettre à jour", f"{os_label}: {count} package(s) to update",
          f"{os_label}: {count} paquete(s) por actualizar")
        + (t(f", dont {status.get('security_count', 0)} correctif(s) de sécurité",
             f", of which {status.get('security_count', 0)} security fix(es)",
             f", de los cuales {status.get('security_count', 0)} parche(s) de seguridad") if status["security_supported"] else ""))
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
    if ALPINE:
        _run(["rc-service", "loco", "stop"], log)
    shutil.copy2(os.path.join(work, "loco.conf"), CONF)
    snapshot_db = os.path.join(work, "portal.db")
    if os.path.isfile(snapshot_db):
        db = os.path.join(STATE, "portal.db")
        for suffix in ("-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(db + suffix)
        shutil.copyfile(snapshot_db, db)
    rc = _install(source, [], log)
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


def configure(changes, log=print):
    clean = validate(changes)
    if not clean:
        log("Aucun changement.")
        return True
    source = read_conf().get("SOURCE_DIR", "")
    if not source or not os.path.isfile(os.path.join(source, "linux", "install.sh")):
        raise ValueError(f"sources de loco introuvables (SOURCE_DIR de {CONF})")
    args = []
    for key, value in clean.items():
        args += SETTINGS[key][2](value)
        log(f"{SETTINGS[key][0]} : {value or 'automatique'}")
    with _snapshot() as snapshot:
        try:
            ok = _install(source, args, log) == 0
        except (OSError, subprocess.SubprocessError) as e:
            log(f"Application interrompue : {e}")
            ok = False
        if not ok:
            _rollback(snapshot, source, log)
        return ok


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
    """Mécanisme NTP du système : systemd-timesyncd (Debian/Ubuntu) ou chrony (famille Red Hat)."""
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


def _set_ntp(enabled, server, log):
    """Synchronise l'horloge sur un serveur NTP amont (client NTP), par timesyncd ou chrony selon le système."""
    if _is_container():
        log(t("Conteneur : l'horloge est gérée par l'hôte, synchronisation NTP ignorée.",
              "Container: the clock is managed by the host, NTP synchronisation skipped.",
              "Contenedor: el reloj lo gestiona el anfitrión, sincronización NTP omitida."))
        return
    backend = _ntp_backend()
    if backend == "timesyncd":
        dropin = "/etc/systemd/timesyncd.conf.d/loco.conf"
        if server:
            _write_conf_file(dropin, f"# Géré par loco\n[Time]\nNTP={server}\n")
        elif os.path.exists(dropin):
            os.remove(dropin)
        _run(["timedatectl", "set-ntp", "true" if enabled else "false"], log)
        if enabled:
            _run(["systemctl", "restart", "systemd-timesyncd"], log)
    elif backend == "chrony":
        confdir = "/etc/chrony/conf.d" if os.path.isdir("/etc/chrony/conf.d") else "/etc/chrony.d"
        dropin = os.path.join(confdir, "loco.conf")
        if server:
            _write_conf_file(dropin, f"# Géré par loco\nserver {server} iburst\n")
        elif os.path.exists(dropin):
            os.remove(dropin)
        if shutil.which("timedatectl"):
            _run(["timedatectl", "set-ntp", "true" if enabled else "false"], log)
        if shutil.which("systemctl"):
            service = "chronyd" if os.path.exists("/usr/lib/systemd/system/chronyd.service") else "chrony"
            _run(["systemctl", "restart" if enabled else "stop", service], log)
        else:                                             # Alpine/OpenRC (appliance) : pas de systemctl
            _run(["rc-service", "chronyd", "restart" if enabled else "stop"], log)
    else:
        log(t("Synchronisation NTP non gérée sur ce système.", "NTP synchronisation not managed on this system.", "Sincronización NTP no gestionada en este sistema."))
        return
    state = t("activée", "enabled", "activada") if enabled else t("désactivée", "disabled", "desactivada")
    via = f" ({server})" if server and enabled else ""
    log(t(f"Synchronisation NTP {state}{via}.", f"NTP synchronisation {state}{via}.", f"Sincronización NTP {state}{via}."))


def set_time(params, log=print):
    """Règle le fuseau horaire du serveur et sa synchronisation NTP (le serveur se base sur un serveur NTP amont)."""
    params = params or {}
    tz = str(params.get("timezone", "")).strip()
    server = str(params.get("ntp_server", "")).strip()
    enabled = str(params.get("ntp", "")) in ("1", "true", "on", "yes")
    if server and not NTP_SERVER_RE.fullmatch(server):
        raise ValueError(t("serveur NTP invalide", "invalid NTP server", "servidor NTP no válido"))
    if tz:
        _set_timezone(tz, log)
    _set_ntp(enabled, server, log)
    return True


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
    if path == SHELL_SOCK:
        shutil.chown(path, "root", "loco")
    os.chmod(path, 0o660)
    server.listen(8)
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
    raise ValueError(f"action inconnue : {action}")


class Busy(Exception):
    pass


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
    code = os.stat(HERE).st_mtime_ns
    beat = 0
    # Opérations interrompues (arrêt brutal) : marquées comme échouées.
    for job in jobs(limit=40):
        if job.get("state") in ("queued", "running") and not _alive(job.get("pid")):
            job.update(state="failed", finished=_now())
            _write_json(_job_paths(job["id"])[0], job)
    # Après un redémarrage (ou une relance de l'agent), l'état du noyau et des services a pu changer : nouvelle
    # vérification une minute plus tard, le temps que le réseau soit prêt.
    status = read_status()
    recheck = time.time() + 60 if (status.get("reboot_required") or status.get("stale_services") or "os" not in status) else 0
    while True:
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
                    server = str(p.get("ntp_server", "")).strip()
                    if (tz and not _valid_tz(tz)) or (server and not NTP_SERVER_RE.fullmatch(server)):
                        continue
                    params = {"timezone": tz, "ntp_server": server,
                              "ntp": "1" if str(p.get("ntp", "")) in ("1", "true", "on", "yes") else ""}
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
        server = str((params or {}).get("ntp_server", "")).strip()
        if tz and not _valid_tz(tz):
            raise ValueError("fuseau horaire inconnu")
        if server and not NTP_SERVER_RE.fullmatch(server):
            raise ValueError("serveur NTP invalide")
        params = {"timezone": tz, "ntp_server": server,
                  "ntp": "1" if str((params or {}).get("ntp", "")) in ("1", "true", "on", "yes") else ""}
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

CONFIG_HELP = """Usage : loco-config [show | set CLÉ=VALEUR…]
  show                  réglages actuels
  set CLÉ=VALEUR…       applique les réglages (relance l'installation avec les nouvelles valeurs)
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
    if cmd == "run-job" and len(args) == 1:
        run_job(args[0])
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
    if cmd == "config":
        if not ALPINE:
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
