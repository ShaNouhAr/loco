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

import contextlib
import datetime
import fcntl
import hashlib
import ipaddress
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
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
REPO = "ShaNouhAr/loco"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ACTIONS = ("check", "update-loco", "update-system", "update-security", "reboot", "configure")
# Réservées à l'appliance Alpine (sources et réglages gérés par loco) ; ailleurs, install.sh s'en charge.
APPLIANCE_ACTIONS = ("update-loco", "configure")
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
        raise ValueError("au moins un réseau attendu")
    try:
        return " ".join(str(ipaddress.ip_network(n, strict=False)) for n in items if _unscoped(n))
    except ValueError:
        raise ValueError("réseau invalide (ex. 192.168.1.0/24)") from None


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
        raise ValueError("adresse http://hôte:port attendue (vide : accès direct)")
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
        raise ValueError("réglages attendus")
    current = read_conf() if current is None else current
    clean = {}
    for key, value in changes.items():
        if key not in SETTINGS:
            raise ValueError(f"réglage inconnu : {key}")
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


def latest_release():
    with _get(API) as r:
        data = json.load(r)
    version = data["tag_name"].lstrip("v")
    if not re.fullmatch(r"[0-9]+(\.[0-9]+){1,3}", version):
        raise ValueError(f"version publiée inattendue : {data['tag_name']}")
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    return {"version": version, "url": data.get("html_url", ""), "published": data.get("published_at", ""),
            "notes": (data.get("body") or "")[:6000],
            "archive": assets.get(f"loco-{version}.tar.gz", ""), "sums": assets.get("SHA256SUMS", "")}




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
    values = {"checked": _now(), "current": current_version(), "errors": []}
    try:
        values["latest"] = latest_release()
    except (OSError, ValueError, KeyError) as e:
        values["errors"].append(f"Version publiée de loco introuvable : {e}")
    values.update(system_state())
    # État du système publié tout de suite : la liste des paquets peut prendre plusieurs minutes (miroirs lents).
    _update_status(**{k: v for k, v in values.items() if k not in ("checked", "errors")})
    try:
        packages = pending_packages()
        values.update(packages=packages, system_updates=[p["name"] for p in packages],
                      security_count=sum(1 for p in packages if p["security"]), system_error=False)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        values["errors"].append(f"Paquets du système : {e}.")
        values["system_error"] = True
    status = _update_status(**values)
    latest = status.get("latest", {}).get("version", "")
    log(f"loco installé : {status['current']} ; dernière version publiée : {latest or 'inconnue'}")
    log(f"{status['os']} : {len(status.get('system_updates', []))} paquet(s) à mettre à jour"
        + (f", dont {status.get('security_count', 0)} correctif(s) de sécurité" if status["security_supported"] else ""))
    for reason in status["reboot_reasons"]:
        log(f"Redémarrage nécessaire : {reason}")
    if status["stale_services"]:
        log("Services qui utilisent d'anciennes bibliothèques : " + ", ".join(sorted(status["stale_services"])))
    for e in status["errors"]:
        log(e)
    return not status["errors"]


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
    return "Système"


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
    reasons = []
    container = in_container()
    running = os.uname().release
    if not container:
        installed = installed_kernels()
        if not os.path.isdir(os.path.join(MODULES, running)):
            reasons.append("noyau mis à jour (les modules du noyau en cours ont été remplacés)")
        elif installed and _kernel_key(installed[0]) > _kernel_key(running):
            reasons.append(f"noyau {installed[0]} installé, {running} en cours")
    try:
        with open(REBOOT_FLAG + ".pkgs") as f:
            pkgs = sorted({line.strip() for line in f if line.strip()})
    except OSError:
        pkgs = []
    if os.path.exists(REBOOT_FLAG):
        reasons.append("demandé par " + ", ".join(pkgs[:6]) if pkgs else "demandé par une mise à jour")
    core = sorted(u for u in (stale or {}) if u in CORE_UNITS)
    if core:
        reasons.append("d'anciennes bibliothèques restent chargées par " + ", ".join(core))
    return reasons


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
        raise ValueError("dépôts APT injoignables")
    out = subprocess.run(["apt-get", "-s", *APT_OPTS, "upgrade", "--with-new-pkgs"], capture_output=True, text=True,
                         timeout=300, env=env)
    if out.returncode != 0:
        raise ValueError("simulation de mise à jour APT impossible")
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
        raise ValueError("dépôts DNF injoignables")
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
        raise ValueError("dépôts Alpine injoignables")
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
                raise ValueError(f"archive refusée : chemin inattendu {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"archive refusée : {m.name} n'est ni un fichier ni un dossier")
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
    log("Échec : restauration de la configuration et de la version précédentes.")
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
    log("Version précédente rétablie." if rc == 0 else "Retour arrière en échec : intervention nécessaire sur la console.")
    return rc == 0


def update_loco(log=print):
    """Dernière version publiée : archive vérifiée (SHA-256), installation, retour arrière en cas d'échec."""
    latest = latest_release()
    current = current_version()
    if version_key(latest["version"]) <= version_key(current):
        log(f"loco {current} est à jour.")
        return True
    if not latest["archive"] or not latest["sums"]:
        raise ValueError(f"la version {latest['version']} ne fournit pas loco-{latest['version']}.tar.gz et SHA256SUMS")
    name = f"loco-{latest['version']}"
    target = os.path.join(SOURCES, name)
    previous = read_conf().get("SOURCE_DIR", "")
    if not previous or not os.path.isfile(os.path.join(previous, "linux", "install.sh")):
        raise ValueError("sources précédentes introuvables : mise à jour refusée sans possibilité de retour arrière")
    if os.path.realpath(previous) == os.path.realpath(target):
        raise ValueError("le dossier des sources précédentes correspond à la version cible")
    os.makedirs(SOURCES, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loco-update-") as work:
        archive = os.path.join(work, name + ".tar.gz")
        log(f"Téléchargement de {latest['archive']}")
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
            raise ValueError("empreinte SHA-256 de l'archive incorrecte : mise à jour refusée")
        log(f"Empreinte SHA-256 vérifiée : {digest}")
        unpack = os.path.join(work, "src")
        os.mkdir(unpack)
        _safe_extract(archive, unpack, name)
        if not os.path.isfile(os.path.join(unpack, name, "linux", "install.sh")):
            raise ValueError("archive incomplète : linux/install.sh absent")
        if os.path.exists(target):
            if not os.path.isfile(os.path.join(target, ".loco-managed")):
                raise ValueError(f"dossier existant non géré par loco : {target}")
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
    log(f"loco {latest['version']} installé.")
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
            log("Portail HTTPS prêt.")
            return True
        time.sleep(2)
    log("Le portail ne répond pas après la mise à jour : consultez /var/log/loco/.")
    return False


def update_system(log=print, security_only=False):
    """Paquets du système (tous, ou les seuls correctifs de sécurité), puis relance des services de loco concernés."""
    manager = package_manager()
    if manager == "apk":
        if security_only:
            raise ValueError("Alpine ne distingue pas les correctifs de sécurité : mettez tout à jour")
        if _run(["apk", "update", "--no-progress"], log) != 0:
            raise ValueError("dépôts Alpine injoignables")
        ok = _run(["apk", "upgrade", "--no-progress"], log) == 0
        # Python, nginx ou les bibliothèques ont pu changer : relancer les services avec les nouvelles versions.
        ok = _run(["rc-service", "loco", "restart"], log) == 0 and ok
    elif manager == "apt":
        env = _apt_env()
        if _run(["apt-get", "update", *APT_OPTS], log, env=env) != 0:
            raise ValueError("dépôts APT injoignables")
        if security_only:
            names = [p["name"] for p in _apt_pending() if p["security"] and p["current"]]
            if not names:
                log("Aucun correctif de sécurité en attente.")
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
        log(f"Redémarrage nécessaire : {reason}.")
    others = sorted(u for u in stale if u not in CORE_UNITS and u not in OWN_UNITS and u != "loco-agent.service")
    if others:
        log("Utilisent encore d'anciennes bibliothèques (redémarrez-les ou redémarrez le serveur) : " + ", ".join(others))
    return ok


def _restart_stale(log):
    """systemd : recharge systemd et relance les services de loco qui utilisent d'anciennes bibliothèques (l'agent en
    dernier). Les autres services sont seulement signalés."""
    stale = stale_services()
    ok = True
    for unit in OWN_UNITS:
        if unit in stale:
            log(f"Relance de {unit} (bibliothèques mises à jour).")
            ok = _run(["systemctl", "restart", unit], log) == 0 and ok
    if "init" in stale:
        log("systemd utilise d'anciennes bibliothèques : rechargement (systemctl daemon-reexec).")
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


def reboot(log=print):
    log("Redémarrage du serveur dans 5 secondes.")
    subprocess.Popen(["sh", "-c", "sleep 5; exec reboot"], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                log(f"Vérification des versions impossible : {e}")
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
    params = validate(params or {}) if action == "configure" else {}
    if action == "configure" and not params:
        raise ValueError("aucun changement")
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
    reasons = reboot_reasons(stale_services())
    print(f"Redémarrage requis : {'oui (' + '; '.join(reasons) + ')' if reasons else 'non'}")
    print(f"Vérifié le         : {status.get('checked', 'jamais')}")
    for e in status.get("errors", []):
        print(f"  ! {e}")


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
