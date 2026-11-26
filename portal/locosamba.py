"""Partages de fichiers Samba servis par le serveur loco (fonctionnalité facultative « Partages de fichiers »).

Côté root, un fil de l'agent loco-agent (worker()) : installe Samba, écrit sa configuration d'après la description
envoyée par le portail, tient un compte Samba par utilisateur Samba de loco — lié à un compte loco ou seul —
(comptes Unix « lsmb<numéro> », sans connexion ni dossier personnel), mesure l'occupation de chaque partage et le passe en lecture seule au-delà de sa taille limite,
puis publie l'état (STATUS, lisible par le portail).
Côté portail (compte loco, sans droits root) : request() dépose une demande, status() lit l'état publié.

Un mot de passe de partage n'existe que dans sa demande (fichier 0600 du compte loco, effacé dès sa lecture) et dans
la base de Samba : il est remis à smbpasswd par son entrée standard, jamais en argument, dans l'état ni dans un journal.
Fichiers des partages ouverts dans le navigateur : un service à part (files_serve), lancé par l'agent sous le compte
loco-share, fait les opérations du gestionnaire de fichiers du portail (voir « fichiers des partages » plus bas).
Module hors du paquet loco : chargé à la demande par locoadmin (mode isolé) et importé par le portail.
"""

import contextlib
import errno
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import zipfile

# The portal also imports this module on Windows, where the Samba worker is unavailable.
if os.name == "posix":
    import grp
    import pwd

AGENT_DIR = "/var/lib/loco-agent"
REQUESTS = os.path.join(AGENT_DIR, "samba")            # demandes du portail (0730 root:loco)
STATUS = os.path.join(AGENT_DIR, "samba.json")         # état publié (0644)
SPEC = os.path.join(AGENT_DIR, "samba-spec.json")      # description appliquée, relue au démarrage (0600)
# Taille d'un partage vue par les postes : Samba lance son « dfree command » comme un programme seul, sans arguments
# à lui (il n'y ajoute que le dossier) ; ce petit script, écrit par l'agent, appelle dfree() ci-dessous.
DFREE = os.path.join(AGENT_DIR, "samba-dfree")
CONF = "/etc/samba/smb.conf"
USER_MAP = "/etc/samba/loco-users.map"
OPENRC_CONF = "/etc/conf.d/samba"                      # Alpine : démons lancés par le service OpenRC « samba »
LOCO_CONF = "/etc/loco/loco.conf"
MARK = "# managed-by-loco"
OWNER = "loco-share"                                   # propriétaire des fichiers de tous les partages
HERE = os.path.abspath(__file__)
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

UNIX_RE = re.compile(r"lsmb[1-9][0-9]{0,8}")           # lsmb<n> : utilisateur Samba n de loco
LOGIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}")
NAME_RE = re.compile(r"\w[\w.-]{0,31}")                # lettres accentuées admises, sans espace ni « $ »
FOLDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RESERVED = {"global", "homes", "printers", "print$", "ipc$", "netlogon", "sysvol"}
FS_TYPES = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "jfs", "zfs"})
# Partitions montées dans ces dossiers du système : jamais proposées (la partition racine l'est, partages sous /srv).
SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/opt", "/proc", "/root", "/run",
               "/sbin", "/snap", "/sys", "/tmp", "/usr", "/var")
MIN_LIMIT = 1 << 30
MAX_SHARES, MAX_USERS = 64, 500
MAX_REQUEST = 262144
MEASURE_EVERY = 120                                    # occupation des partages relevée toutes les deux minutes
SESSIONS_EVERY = 15
GLOBAL = (("server string", "loco"), ("server role", "standalone server"), ("workgroup", "WORKGROUP"),
          ("security", "user"), ("passdb backend", "tdbsam"), ("map to guest", "never"), ("restrict anonymous", "2"),
          ("username map", USER_MAP), ("server min protocol", "SMB2_10"), ("smb ports", "445"),
          ("disable netbios", "yes"), ("load printers", "no"), ("printing", "bsd"), ("printcap name", "/dev/null"),
          ("disable spoolss", "yes"), ("dfree command", DFREE), ("dfree cache time", "10"),
          ("log file", "/var/log/samba/log.%m"), ("max log size", "1000"), ("logging", "file"))


# --------------------------------------------------------------------------- portail (compte loco)

def request(op, **payload):
    """Dépôt d'une demande par le portail ; l'agent la revalide. Fichier 0600 : il peut porter un mot de passe."""
    base = os.path.join(REQUESTS, f"{time.time():.6f}-{uuid.uuid4().hex[:8]}")
    try:
        fd = os.open(base + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dict(payload, op=op), f, ensure_ascii=False)
        os.rename(base + ".tmp", base + ".json")
    except OSError:
        raise ValueError("agent d'administration indisponible (service loco-agent)") from None


def status():
    """État publié par l'agent (dict vide si Samba n'a jamais été géré ici)."""
    return _read_json(STATUS)


def digest(spec):
    """Empreinte d'une description : le portail la compare à celle que l'agent a reçue en dernier (« applied »)."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# --------------------------------------------------------------------------- outils

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, text, mode=0o644):
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _run(cmd, stdin=None, timeout=120, env=None):
    """(code, sortie) ; la sortie n'est jamais celle d'une commande à laquelle un mot de passe a été remis."""
    try:
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, errors="replace", timeout=timeout,
                              env=env or {"PATH": PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    return done.returncode, (done.stdout + done.stderr).strip()


def _which(name):
    return shutil.which(name, path=PATH)


def alpine():
    return os.path.exists("/etc/alpine-release")


def installed():
    return bool(_which("smbd") and _which("smbpasswd") and _which("pdbedit"))


def support():
    """Raison pour laquelle loco ne sait pas gérer Samba ici ("" s'il le sait) : Alpine (appliance), Debian, Ubuntu."""
    if alpine() or _which("apt-get"):
        return ""
    return "system"


def _version():
    code, out = _run(["smbd", "--version"], timeout=10)
    found = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)", out) if code == 0 else None
    return found.group(1) if found else ""


def _allowed_networks():
    """Réseaux des postes réglés dans loco (ALLOW de loco.conf) : seuls autorisés à joindre les partages."""
    try:
        with open(LOCO_CONF, encoding="utf-8") as f:
            for line in f:
                key, _sep, value = line.strip().partition("=")
                if key == "ALLOW":
                    nets = []
                    for item in " ".join(shlex.split(value)).split():
                        try:
                            nets.append(str(ipaddress.ip_network(item, strict=False)))
                        except ValueError:
                            pass
                    return nets
    except (OSError, ValueError):
        pass
    return []


# --------------------------------------------------------------------------- pare-feu du serveur
# Samba ne sert que SMB 2 et plus récents sur le port 445 (NetBIOS, 137-139, est coupé) : c'est le seul port à ouvrir.
# Un pare-feu actif sur le serveur (ufw sur Debian, Ubuntu ou l'appliance, firewalld) le reçoit d'office pour les
# réseaux des postes tant que les partages sont servis, et le perd quand on les coupe. Les règles de loco portent leur
# marque ; celles de l'administrateur ne sont jamais touchées.

FW_TAG = "loco-samba"
SMB_PORT = "445"
FIREWALL_EVERY = 600


def firewall_kind():
    """Pare-feu actif sur le serveur que loco sait régler : « ufw », « firewalld », ou "" (aucun)."""
    if _which("ufw"):
        code, out = _run(["ufw", "status"], timeout=30)
        if code == 0 and re.search(r"(?im)^Status:\s*active\b", out):
            return "ufw"
    if _which("firewall-cmd"):
        code, out = _run(["firewall-cmd", "--state"], timeout=30)
        if code == 0 and out.strip() == "running":
            return "firewalld"
    return ""


def _rich(net):
    family = "ipv6" if ":" in net else "ipv4"
    return f'rule family="{family}" source address="{net}" port port="{SMB_PORT}" protocol="tcp" accept'


def firewall_close(kind=None):
    """Retire les règles de loco pour Samba (ufw : celles qui portent sa marque ; firewalld : ses règles riches)."""
    kind = firewall_kind() if kind is None else kind
    if kind == "ufw":
        code, out = _run(["ufw", "status", "numbered"], timeout=30)
        numbers = [int(m.group(1)) for m in re.finditer(r"(?m)^\[\s*(\d+)\].*#\s*" + FW_TAG + r"\s*$", out)] if code == 0 else []
        for number in sorted(numbers, reverse=True):              # du dernier au premier : les numéros ne bougent pas
            _run(["ufw", "--force", "delete", str(number)], timeout=30)
    elif kind == "firewalld":
        code, out = _run(["firewall-cmd", "--permanent", "--list-rich-rules"], timeout=30)
        for rule in (out.splitlines() if code == 0 else []):
            if f'port port="{SMB_PORT}" protocol="tcp" accept' in rule and "source address=" in rule:
                _run(["firewall-cmd", "--permanent", "--remove-rich-rule", rule.strip()], timeout=30)
        _run(["firewall-cmd", "--reload"], timeout=60)


def firewall_has(kind):
    """Les règles de loco pour Samba sont-elles en place sur ce pare-feu ?"""
    if kind == "ufw":
        code, out = _run(["ufw", "status", "numbered"], timeout=30)
        return code == 0 and re.search(r"#\s*" + FW_TAG + r"\s*$", out, re.M) is not None
    if kind == "firewalld":
        code, out = _run(["firewall-cmd", "--permanent", "--list-rich-rules"], timeout=30)
        return code == 0 and f'port port="{SMB_PORT}" protocol="tcp" accept' in out
    return False


def firewall_open(networks):
    """Ouvre le port 445/TCP aux réseaux des postes (à tous s'ils ne sont pas réglés) ; les anciennes règles de loco
    sont d'abord retirées (réseaux changés). Rend l'état à publier."""
    kind = firewall_kind()
    if not kind:
        return {"kind": "", "open": False}
    firewall_close(kind)
    sources = list(networks) or (["any"] if kind == "ufw" else [])
    ok = True
    if kind == "ufw":
        for net in sources:
            code, _out = _run(["ufw", "allow", "from", net, "to", "any", "port", SMB_PORT, "proto", "tcp",
                               "comment", FW_TAG], timeout=30)
            ok = ok and code == 0
    else:
        if sources:
            for net in sources:
                code, _out = _run(["firewall-cmd", "--permanent", "--add-rich-rule", _rich(net)], timeout=30)
                ok = ok and code == 0
        else:
            code, _out = _run(["firewall-cmd", "--permanent", "--add-rich-rule",
                               f'rule port port="{SMB_PORT}" protocol="tcp" accept'], timeout=30)
            ok = code == 0
        _run(["firewall-cmd", "--reload"], timeout=60)
    return {"kind": kind, "open": ok, "networks": sources}


# --------------------------------------------------------------------------- partitions et dossiers

def _unescape(field):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def base_dir(mount):
    """Dossier des partages d'une partition : /srv/partages sur la partition racine, <point de montage>/partages sinon."""
    return "/srv/partages" if mount == "/" else mount.rstrip("/") + "/partages"


def partitions(mountinfo="/proc/self/mountinfo"):
    """Systèmes de fichiers en service où un partage peut vivre : montés en écriture, à droits Unix (ext4, XFS, Btrfs…),
    hors des dossiers du système. Jamais de formatage ni de montage : loco ne propose que ce qui est déjà là."""
    found, seen = [], set()
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return found
    for line in lines:
        parts = line.split()
        if "-" not in parts[6:]:
            continue
        sep = parts.index("-", 6)
        if len(parts) < sep + 3 or len(parts) < 6:
            continue
        device_id, root, mount, options = parts[2], _unescape(parts[3]), _unescape(parts[4]), parts[5].split(",")
        fstype, source = parts[sep + 1], _unescape(parts[sep + 2])
        if fstype not in FS_TYPES or root != "/" or "ro" in options or device_id in seen:
            continue
        if mount != "/" and any(mount == d or mount.startswith(d + "/") for d in SYSTEM_DIRS):
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        seen.add(device_id)
        found.append({"mount": mount, "device": source, "fstype": fstype, "size": st.f_blocks * st.f_frsize,
                      "free": st.f_bavail * st.f_frsize, "used": (st.f_blocks - st.f_bfree) * st.f_frsize,
                      "base": base_dir(mount)})
    return sorted(found, key=lambda p: (p["mount"] != "/", p["mount"]))


def _owner_ids():
    entry = pwd.getpwnam(OWNER)
    return entry.pw_uid, entry.pw_gid


def prepare_folder(path, mount):
    """Dossier d'un partage, créé par loco (propriétaire loco-share, 2770) sur sa partition. Un dossier existant qui
    contient des fichiers d'un autre propriétaire est refusé : loco ne change jamais les droits de données qui ne sont
    pas à lui. ValueError avec un texte du portail (%(path)s) sinon."""
    uid, gid = _owner_ids()
    device = os.stat(mount).st_dev
    base = os.path.dirname(path)
    os.makedirs(base, mode=0o755, exist_ok=True)
    if os.path.realpath(base) != base or os.stat(base).st_dev != device:
        raise ValueError("Le dossier %(path)s n'est pas sur la partition choisie.")
    if os.path.lexists(path):
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError("%(path)s existe déjà et n'est pas un dossier.")
        if st.st_uid != uid and os.listdir(path):
            raise ValueError("Le dossier %(path)s existe déjà et contient des fichiers qui ne viennent pas de loco.")
    else:
        os.mkdir(path, 0o770)
    if os.stat(path).st_dev != device:
        raise ValueError("Le dossier %(path)s n'est pas sur la partition choisie.")
    os.chown(path, uid, gid)
    os.chmod(path, 0o2770)


def measure(path, timeout=600):
    """Place occupée par un partage, en octets (blocs alloués, sans suivre les autres partitions) ; None si illisible."""
    code, out = _run(["du", "-s", "-x", "-k", path], timeout=timeout)
    found = re.match(r"([0-9]+)\s", out + " ") if code == 0 else None
    return int(found.group(1)) * 1024 if found else None


# --------------------------------------------------------------------------- description et configuration

def check_spec(raw, parts):
    """Description reçue du portail, revalidée ici : comptes (Unix ↔ identifiant loco) et partages (nom, partition,
    dossier, taille limite, comptes en lecture et en écriture). Un partage dont la partition n'est plus montée est
    gardé avec une erreur, hors de la configuration."""
    raw = raw if isinstance(raw, dict) else {}
    users, logins, disabled = {}, set(), set()
    for item in (raw.get("users") or [])[:MAX_USERS]:
        item = item if isinstance(item, dict) else {}
        unix, login = str(item.get("unix", "")), str(item.get("login", ""))
        if UNIX_RE.fullmatch(unix) and LOGIN_RE.fullmatch(login) and login.casefold() not in logins and unix not in users:
            users[unix] = login
            logins.add(login.casefold())
            if item.get("disabled"):
                disabled.add(unix)
    mounts = {p["mount"] for p in parts}
    shares, names = [], set()
    for item in (raw.get("shares") or [])[:MAX_SHARES]:
        item = item if isinstance(item, dict) else {}
        name, mount, folder = str(item.get("name", "")), str(item.get("mount", "")), str(item.get("folder", ""))
        if (not NAME_RE.fullmatch(name) or name.casefold() in RESERVED or name.casefold() in names
                or not FOLDER_RE.fullmatch(folder) or not mount.startswith("/") or "\n" in mount):
            continue
        try:
            limit = int(item.get("limit", 0))
        except (TypeError, ValueError):
            continue
        if limit < MIN_LIMIT:
            continue
        names.add(name.casefold())
        writers = sorted({u for u in item.get("write") or [] if u in users})
        readers = sorted({u for u in item.get("read") or [] if u in users} - set(writers))
        comment = re.sub(r"[\x00-\x1f\x7f%\\]", "", str(item.get("comment", "")))[:120].strip()
        shares.append({"name": name, "mount": mount, "folder": folder, "path": base_dir(mount) + "/" + folder,
                       "limit": limit, "read": readers, "write": writers, "comment": comment,
                       "error": "" if mount in mounts else "La partition %(mount)s n'est plus montée."})
    return {"users": users, "disabled": sorted(disabled), "shares": shares}


def render(spec, full=(), networks=()):
    """smb.conf complet : serveur autonome, comptes seulement (aucun invité), SMB 2 minimum, sans NetBIOS ni
    imprimantes ; chaque partage écrit sous le compte loco-share, en lecture seule pour tous une fois plein."""
    out = [MARK, "# Écrit par loco (Partages de fichiers) : toute modification faite ici sera remplacée.", "", "[global]"]
    out += [f"   {key} = {value}" for key, value in GLOBAL]
    if networks:
        out.append("   hosts allow = 127.0.0.1 ::1 " + " ".join(networks))
    off = set(spec.get("disabled") or ())                      # comptes désactivés : hors de tous les partages
    for share in spec["shares"]:
        if share["error"]:
            continue
        readers = [u for u in share["read"] if u not in off]
        writers = [u for u in share["write"] if u not in off]
        everyone = readers + writers
        out += ["", f"[{share['name']}]", f"   path = {share['path']}"]
        if share["comment"]:
            out.append(f"   comment = {share['comment']}")
        out += [f"   valid users = {' '.join(everyone)}" if everyone else "   available = no",
                "   read only = yes", f"   force user = {OWNER}", f"   force group = {OWNER}", "   create mask = 0660",
                "   directory mask = 2770", "   browseable = yes", "   access based share enum = yes"]
        if writers and share["name"] not in full:
            out.append(f"   write list = {' '.join(writers)}")
    return "\n".join(out) + "\n"


def render_map(spec):
    lines = [MARK] + [f"{unix} = {login}" for unix, login in sorted(spec["users"].items())]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- comptes

def _nologin():
    return next((p for p in ("/usr/sbin/nologin", "/sbin/nologin") if os.path.exists(p)), "/bin/false")


def _exists(name):
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


# Commentaire des comptes (champ GECOS) : sans « : », séparateur des champs de /etc/passwd que useradd refuse.
def ensure_owner():
    try:
        grp.getgrnam(OWNER)
    except KeyError:
        _run(["groupadd", "--system", OWNER])
    if not _exists(OWNER):
        _run(["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent", "--shell", _nologin(),
              "--gid", OWNER, "--comment", "loco partages de fichiers", OWNER])


def ensure_unix(name):
    if UNIX_RE.fullmatch(name) and not _exists(name):
        _run(["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent", "--shell", _nologin(),
              "--gid", OWNER, "--comment", "loco compte de partage", name])


def loco_accounts():
    """Comptes Unix des partages créés par loco (jamais un autre compte du système)."""
    return {e.pw_name for e in pwd.getpwall() if UNIX_RE.fullmatch(e.pw_name) and e.pw_gecos.startswith("loco")}


def drop_unix(name):
    if name in loco_accounts():
        forget(name)
        _run(["userdel", name])


def with_password():
    """Comptes qui ont un mot de passe de partage dans la base de Samba."""
    code, out = _run(["pdbedit", "-L"], timeout=20)
    return sorted({line.split(":", 1)[0] for line in out.splitlines() if ":" in line}) if code == 0 else []


def set_password(name, password):
    """Mot de passe de partage d'un compte (smbpasswd, par l'entrée standard). Rien de ce qu'il écrit n'est gardé."""
    if not UNIX_RE.fullmatch(name) or not isinstance(password, str) or not 1 <= len(password) <= 256 \
            or any(c in password for c in "\r\n\x00"):
        return False
    ensure_owner()
    ensure_unix(name)
    code, _out = _run(["smbpasswd", "-s", "-a", name], stdin=f"{password}\n{password}\n", timeout=30)
    if code == 0:
        _run(["smbpasswd", "-e", name], timeout=30)
    return code == 0


def forget(name):
    if UNIX_RE.fullmatch(name):
        _run(["smbpasswd", "-x", name], timeout=30)


def lock(name, locked):
    """Compte désactivé (smbpasswd -d) ou réactivé (-e) dans Samba : son mot de passe est gardé."""
    if UNIX_RE.fullmatch(name):
        _run(["smbpasswd", "-d" if locked else "-e", name], timeout=30)


# --------------------------------------------------------------------------- service

def running():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm", encoding="ascii", errors="replace") as f:
                    if f.read().strip() == "smbd":
                        return True
            except OSError:
                continue
    return False


def no_netbios():
    """Debian, Ubuntu : nmbd (NetBIOS), lancé par le paquet, arrêté et désactivé ; loco ne sert que SMB sur le port 445."""
    if not alpine() and _which("systemctl"):
        _run(["systemctl", "disable", "--now", "nmbd"], timeout=60)


def service(action):
    """Démarrer (et activer au démarrage du serveur), redémarrer, arrêter (et désactiver), ou relire la configuration.
    Une relecture ne change pas les ports d'écoute : après une reprise de configuration, c'est un redémarrage."""
    if action == "reload" and running():
        if _run(["smbcontrol", "smbd", "reload-config"], timeout=30)[0] == 0:
            return True
        action = "restart"
    if alpine():
        if action in ("start", "restart", "reload"):
            _run(["rc-update", "add", "samba", "default"])
            return _run(["rc-service", "samba", "restart" if action != "start" or running() else "start"], timeout=60)[0] == 0
        _run(["rc-update", "del", "samba", "default"])
        return _run(["rc-service", "samba", "stop"], timeout=60)[0] == 0
    no_netbios()
    if action in ("start", "restart", "reload"):
        _run(["systemctl", "enable", "smbd"], timeout=60)
        return _run(["systemctl", "restart" if action != "start" else "start", "smbd"], timeout=60)[0] == 0
    _run(["systemctl", "disable", "smbd"], timeout=60)
    return _run(["systemctl", "stop", "smbd"], timeout=60)[0] == 0


def install(env=None):
    """Installe Samba depuis les dépôts du système (seuls smbd et ses outils). (vrai, "") ou (faux, fin du journal)."""
    if alpine():
        code, out = _run(["apk", "add", "--no-progress", "samba-server", "samba-common-tools"], timeout=900, env=env)
    else:
        env = dict(env or {"PATH": PATH, "LANG": "C.UTF-8"}, DEBIAN_FRONTEND="noninteractive")
        code, out = _run(["apt-get", "-q", "-y", "-o", "DPkg::Lock::Timeout=300", "install", "--no-install-recommends",
                          "samba"], timeout=1800, env=env)
        if code != 0:
            _run(["apt-get", "-q", "update"], timeout=900, env=env)
            code, out = _run(["apt-get", "-q", "-y", "-o", "DPkg::Lock::Timeout=300", "install",
                              "--no-install-recommends", "samba"], timeout=1800, env=env)
    return code == 0, "\n".join(out.splitlines()[-8:])


def managed():
    """Vrai si smb.conf est celui de loco (ou absent) ; faux s'il a été écrit par quelqu'un d'autre."""
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            return f.readline().strip() == MARK
    except FileNotFoundError:
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- connexions des postes
# Samba n'applique sa configuration qu'aux connexions qui s'ouvrent : un compte désactivé, un droit retiré, un partage
# retiré ou passé en lecture seule (plein) restent sans effet sur une connexion déjà ouverte — vérifié avec Samba 4.23.
# L'agent referme donc lui-même ce qui n'est plus permis (Worker.enforce) ; le poste qui y a encore droit rouvre le
# partage de lui-même, avec ses nouveaux droits.

def _smbstatus():
    """Sessions et partages ouverts, tels que Samba les voit (smbstatus --json, Samba 4.16 et suivants) ; {} sinon."""
    code, out = _run(["smbstatus", "--json"], timeout=20)
    try:
        data = json.loads(out) if code == 0 else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _server(item):
    """Processus smbd qui sert une session ou un partage ouvert : numéro et identifiant unique (un numéro repris
    entre-temps par un autre processus ne correspond plus)."""
    server = item.get("server_id") if isinstance(item, dict) else None
    server = server if isinstance(server, dict) else {}
    pid = str(server.get("pid", ""))
    return (pid, str(server.get("unique_id", ""))) if pid.isdigit() else ("", "")


def _shares_of(data):
    """Partages ouverts (hors IPC$ et autres partages cachés du serveur), par session."""
    found = {}
    for tcon in (data.get("tcons") or {}).values():
        name = str(tcon.get("service", "")) if isinstance(tcon, dict) else ""
        if name and not name.endswith("$"):
            found.setdefault(str(tcon.get("session_id", "")), []).append((name, tcon))
    return found


def connections(data):
    """Une entrée par session ouverte : processus smbd qui la sert, compte Unix, machine, partages ouverts."""
    shares, found = _shares_of(data), []
    for key, session in (data.get("sessions") or {}).items():
        pid, uid = _server(session)
        if pid:
            found.append({"pid": pid, "uid": uid, "unix": str(session.get("username", "")),
                          "machine": str(session.get("remote_machine", "")),
                          "shares": [name for name, _tcon in shares.get(str(key), [])]})
    return found


def _sessions(logins, data=None):
    """Connexions en cours, pour le portail : une ligne par partage ouvert (compte loco, partage, machine, depuis quand)
    avec le processus smbd qui sert la connexion, pour la couper."""
    data = _smbstatus() if data is None else data
    sessions, found = data.get("sessions") or {}, []
    for key, opened in _shares_of(data).items():
        session = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
        unix = str(session.get("username", ""))
        for name, tcon in opened:
            pid, uid = _server(tcon)
            if not pid:
                pid, uid = _server(session)
            found.append({"user": logins.get(unix, unix), "share": name,
                          "machine": str(tcon.get("machine", "") or session.get("remote_machine", "")),
                          "since": str(tcon.get("connected_at", ""))[:19].replace("T", " "), "pid": pid, "uid": uid})
    return sorted(found, key=lambda s: (s["share"], s["user"]))


def _proc_stat(pid):
    """(état, processus parent) d'un processus, d'après /proc ; None s'il n'existe plus."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return fields[0], fields[1]
    except (OSError, IndexError):
        return None


def _comm(pid):
    try:
        with open(f"/proc/{pid}/comm", encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def served_by_smbd(pid):
    """Vrai pour un processus fils de smbd (celui qui sert la connexion d'un poste) ; jamais smbd lui-même ni un autre."""
    st = _proc_stat(pid)
    return bool(st) and st[0] not in ("Z", "X") and st[1].isdigit() and int(st[1]) > 1 \
        and _comm(st[1]) == "smbd" and _comm(pid).startswith("smbd")


def _gone(pid):
    st = _proc_stat(pid)
    return st is None or st[0] in ("Z", "X")


def end_connection(pid, uid="", data=None):
    """Coupe la connexion d'un poste : le processus smbd qui la sert — celui que smbstatus donne, fils de smbd, jamais
    un autre — se termine proprement (smbcontrol … shutdown), au besoin par SIGTERM. Vrai s'il n'est plus là."""
    pid, uid = str(pid), str(uid or "")
    known = {c["pid"]: c["uid"] for c in connections(_smbstatus() if data is None else data)}
    if not pid.isdigit() or pid not in known or (uid and known[pid] != uid) or not served_by_smbd(pid):
        return False
    _run(["smbcontrol", pid, "shutdown"], timeout=15)
    for last in (False, True):
        deadline = time.time() + 3
        while time.time() < deadline:
            if _gone(pid):
                return True
            time.sleep(0.1)
        if not last and served_by_smbd(pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
    return _gone(pid)


def close_share(name, pid="smbd"):
    """Referme un partage ouvert, sur une connexion (son processus smbd) ou sur toutes (« smbd ») ; un poste qui y a
    encore droit le rouvre de lui-même, avec les droits du moment."""
    if NAME_RE.fullmatch(name) and (pid == "smbd" or str(pid).isdigit()):
        _run(["smbcontrol", str(pid), "close-share", name], timeout=15)


def access(spec, full=()):
    """Droits que donne la configuration écrite (render) : {partage: {compte Unix: "write" ou "read"}} ; un partage
    plein ne laisse que la lecture, un compte désactivé n'a rien."""
    off, out = set(spec.get("disabled") or ()), {}
    for share in spec["shares"]:
        if share["error"]:
            continue
        rights = {u: "read" for u in share["read"] if u not in off}
        rights.update({u: "read" if share["name"] in full else "write" for u in share["write"] if u not in off})
        out[share["name"].casefold()] = rights
    return out


# --------------------------------------------------------------------------- agent (root)

class Worker:
    """État tenu par le fil de l'agent : description appliquée, occupation et partages pleins."""

    def __init__(self, env=None):
        self.env = env
        self.lock = threading.RLock()
        self.spec = {"users": {}, "disabled": [], "shares": []}
        self.enabled = False
        self.usage, self.full, self.errors = {}, set(), {}
        self.state = {"busy": "", "error": "", "install_log": ""}
        self.sessions, self.sessions_at, self.passwords = [], 0.0, []
        self.raw, self.applied = None, ""
        self.firewall = {"kind": "", "open": False}
        self.access = {}                                              # droits de la configuration écrite en dernier
        self.files_proc, self.files_sock, self.files_at = None, None, 0.0
        saved = _read_json(SPEC)
        if saved:
            self.enabled = bool(saved.get("enabled"))
            self.raw, self.applied = saved.get("raw"), str(saved.get("applied", ""))
            self.spec = check_spec(self.raw, partitions())
            self.full = set(saved.get("full") or [])

    # -- publication

    def publish(self):
        with self.lock:
            parts = partitions()
            shares = {}
            for share in self.spec["shares"]:
                use = self.usage.get(share["name"]) or {}
                shares[share["name"]] = {"path": share["path"], "mount": share["mount"], "limit": share["limit"],
                                         "used": use.get("used"), "measured": use.get("at"),
                                         "full": share["name"] in self.full,
                                         "error": share["error"] or self.errors.get(share["name"], "")}
            data = {"at": time.time(), "support": support(), "installed": installed(), "running": running(),
                    "managed": managed(), "enabled": self.enabled, "version": _version() if installed() else "",
                    "partitions": parts, "shares": shares, "passwords": self.passwords, "sessions": self.sessions,
                    "networks": _allowed_networks(), "applied": self.applied, "firewall": self.firewall}
            data.update(self.state)
            _write(STATUS, json.dumps(data, ensure_ascii=False))

    def save(self):
        _write(SPEC, json.dumps({"enabled": self.enabled, "raw": self.raw, "applied": self.applied,
                                 "full": sorted(self.full)}, ensure_ascii=False), 0o600)

    # -- application

    def apply(self, raw=None, enabled=None, takeover=False):
        """Écrit la configuration (après vérification par testparm), crée comptes et dossiers, relance smbd."""
        with self.lock:
            if raw is not None:
                self.raw, self.applied = raw, digest(raw)
            # Relue à chaque application : une partition remontée (ou démontée) change l'état des partages.
            self.spec = check_spec(self.raw, partitions())
            if enabled is not None:
                self.enabled = enabled
            self.save()
            if not installed():
                return
            if not managed() and not takeover:
                self.state["error"] = "foreign"
                return
            if not self.enabled:
                service("stop")
                firewall_close()
                self.firewall = {"kind": firewall_kind(), "open": False}
                self.state["error"] = ""
                return
            ensure_owner()
            for unix in self.spec["users"]:
                ensure_unix(unix)
            for unix in loco_accounts() - set(self.spec["users"]):
                drop_unix(unix)
            self.errors = {}
            for share in self.spec["shares"]:
                if share["error"]:
                    continue
                try:
                    prepare_folder(share["path"], share["mount"])
                except (OSError, ValueError, KeyError) as e:
                    self.errors[share["name"]] = str(e) if isinstance(e, ValueError) else "Le dossier %(path)s ne peut pas être créé."
            self.full &= {s["name"] for s in self.spec["shares"]}
            self.write_config(takeover)
            self.firewall = firewall_open(_allowed_networks())
            self.passwords = with_password()
            for unix in self.passwords:
                if unix in self.spec["users"]:
                    lock(unix, unix in self.spec["disabled"])

    def write_config(self, takeover=False):
        spec = {"users": self.spec["users"], "disabled": self.spec["disabled"],
                "shares": [s for s in self.spec["shares"] if s["name"] not in self.errors]}
        text = render(spec, self.full, _allowed_networks())
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        previous = None
        if os.path.exists(CONF):
            with open(CONF, encoding="utf-8", errors="replace") as f:
                previous = f.read()
            if takeover and not previous.startswith(MARK):
                _write(CONF + ".avant-loco", previous)                 # configuration d'avant, gardée à côté
        _write(USER_MAP, render_map(self.spec))
        _write(DFREE, f"#!/bin/sh\n# Écrit par loco : taille des partages vue par les postes (dfree command de Samba).\n"
                      f"exec {sys.executable} -I {HERE} dfree \"$@\"\n", 0o755)
        _write(CONF, text)
        code, out = _run(["testparm", "-s", CONF], timeout=30)
        if code != 0:
            if previous is not None and previous.startswith(MARK):
                _write(CONF, previous)
            self.state["error"] = "config"
            self.state["install_log"] = "\n".join(out.splitlines()[-6:])
            return
        if alpine() and os.path.isdir(os.path.dirname(OPENRC_CONF)):
            _write(OPENRC_CONF, 'daemon_list="smbd"\n')                  # smbd seul : pas de NetBIOS (nmbd)
        self.state["error"] = "" if service("restart" if takeover else "reload") else "service"
        if not self.state["error"]:
            self.enforce(spec)

    def enforce(self, spec):
        """Ce que la configuration vient de changer vaut aussi pour les connexions déjà ouvertes : un compte désactivé
        ou retiré perd ses connexions ; un partage retiré, un droit retiré ou changé (lecture seule d'un partage plein,
        et retour) referme le partage sur la connexion, que le poste rouvre de lui-même s'il y a encore droit."""
        before, now = self.access, access(spec, self.full)
        self.access = now
        changed = {(name, unix) for name, rights in before.items() for unix, right in rights.items()
                   if name in now and now[name].get(unix) not in (None, right)}
        active = set(spec["users"]) - set(spec.get("disabled") or ())
        data = _smbstatus()
        touched, ended = False, set()
        # Un processus peut servir plusieurs sessions (poste à plusieurs utilisateurs) : les connexions à arrêter passent
        # d'abord, et un processus arrêté n'est plus visé d'après ce relevé — son numéro a pu être repris depuis.
        for conn in sorted(connections(data), key=lambda c: UNIX_RE.fullmatch(c["unix"]) is None or c["unix"] in active):
            unix, pid = conn["unix"], conn["pid"]
            if pid in ended:
                continue
            if UNIX_RE.fullmatch(unix) and unix not in active:
                ended.add(pid)
                touched = end_connection(pid, conn["uid"], data) or touched
                continue
            for name in conn["shares"]:
                key = name.casefold()
                if key not in now or unix not in now[key] or (key, unix) in changed:
                    close_share(name, pid)
                    touched = True
        if touched:
            self.sessions, self.sessions_at = _sessions(spec["users"]), time.time()

    # -- fichiers des partages ouverts dans le navigateur

    def files_keep(self):
        """Service des fichiers des partages (files_serve), lancé sous le compte loco-share tant que la fonctionnalité
        est active et Samba installé ; relancé s'il s'arrête (au plus toutes les 15 secondes), arrêté sinon."""
        proc = self.files_proc
        if not (self.enabled and installed() and _exists(OWNER)):
            if proc and proc.poll() is None:
                proc.terminate()
            self.files_proc = None
            if self.files_sock is not None:
                self.files_sock.close()
                self.files_sock = None
                with contextlib.suppress(OSError):
                    os.unlink(FILES_SOCK)
            return
        if (proc and proc.poll() is None) or time.time() - self.files_at < 15:
            return
        self.files_at = time.time()
        try:
            if self.files_sock is None:
                self.files_sock = files_socket()
            uid, gid = _owner_ids()
            fd = self.files_sock.fileno()
            self.files_proc = subprocess.Popen(
                [sys.executable, "-I", HERE, "files", str(fd)], pass_fds=(fd,), user=uid, group=gid, extra_groups=[],
                umask=0o007, cwd="/", env={"PATH": PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except (OSError, KeyError, ValueError, subprocess.SubprocessError) as e:
            print(f"fichiers des partages indisponibles : {e}", file=sys.stderr)

    # -- demandes

    def handle(self, req):
        op = req.get("op")
        if op == "install":
            if support():
                self.state["error"] = "system"
            elif installed():
                self.apply(enabled=True)                              # déjà là : repris seulement s'il est à loco
            else:
                self.state.update(busy="install", error="", install_log="")
                self.publish()
                ok, tail = install(self.env)
                self.state.update(busy="", error="" if ok else "install", install_log="" if ok else tail)
                if ok:
                    self.apply(enabled=True, takeover=True)           # installé par loco : sa configuration est à lui
        elif op == "apply":
            self.apply(req.get("spec"), enabled=bool(req.get("enabled", True)))
        elif op == "takeover":
            self.apply(enabled=True, takeover=True)
        elif op == "stop":
            self.apply(enabled=False)
        elif op == "password":
            unix = str(req.get("unix", ""))
            if unix in self.spec["users"] and installed():
                if set_password(unix, req.get("password")) and unix in self.spec["disabled"]:
                    lock(unix, True)                                  # nouveau mot de passe, compte toujours désactivé
                self.passwords = with_password()
        elif op == "forget":
            unix = str(req.get("unix", ""))
            if UNIX_RE.fullmatch(unix) and installed():
                forget(unix)
                self.passwords = with_password()
        elif op == "disconnect":
            if installed():
                end_connection(str(req.get("pid", "")), str(req.get("uid", "")))
                self.sessions, self.sessions_at = _sessions(self.spec["users"]), time.time()
        self.publish()

    def requests(self):
        try:
            names = sorted(os.listdir(REQUESTS))
        except OSError:
            return
        for name in names:
            path = os.path.join(REQUESTS, name)
            if not name.endswith(".json"):
                if name.endswith(".tmp") and time.time() - os.lstat(path).st_mtime > 300:
                    os.unlink(path)
                continue
            req = _read_request(path)
            try:
                os.unlink(path)                                       # effacée avant d'être traitée
            except OSError:
                continue
            if req:
                try:
                    self.handle(req)
                except Exception as e:                                # l'agent ne doit jamais tomber pour Samba
                    print(f"partages : {type(e).__name__}", file=sys.stderr)
                    self.state.update(busy="", error="internal")

    # -- relevés

    def measure_all(self):
        with self.lock:
            shares = [s for s in self.spec["shares"] if not s["error"] and s["name"] not in self.errors]
        changed = False
        for share in shares:
            used = measure(share["path"]) if os.path.isdir(share["path"]) else None
            with self.lock:
                self.usage[share["name"]] = {"used": used, "at": time.time()}
                if used is None:
                    continue
                full = used >= share["limit"]
                if full != (share["name"] in self.full):
                    (self.full.add if full else self.full.discard)(share["name"])
                    changed = True
        if changed:
            with self.lock:
                self.save()
                if self.enabled and installed() and managed():
                    self.write_config()

    def loop(self):
        os.makedirs(REQUESTS, exist_ok=True)
        shutil.chown(REQUESTS, "root", "loco")
        os.chmod(REQUESTS, 0o730)
        if self.enabled and installed() and managed():
            with self.lock:
                self.apply()
        self.publish()
        measured = published = walled = 0.0
        while True:
            self.requests()
            self.files_keep()
            now = time.time()
            # Pare-feu allumé (ou règles retirées) depuis : le port des partages est rouvert dans les dix minutes.
            if self.enabled and installed() and managed() and now - walled > FIREWALL_EVERY:
                walled = now
                kind = firewall_kind()
                with self.lock:
                    if kind and not firewall_has(kind):
                        self.firewall = firewall_open(_allowed_networks())
                    elif self.firewall.get("kind") != kind:
                        self.firewall = {"kind": kind, "open": bool(kind)}
            if self.enabled and installed() and now - measured > MEASURE_EVERY:
                measured = now
                threading.Thread(target=self.measure_all, name="loco-samba-du", daemon=True).start()
            if self.enabled and installed() and now - self.sessions_at > SESSIONS_EVERY:
                self.sessions_at = now
                self.sessions = _sessions(self.spec["users"])
            if now - published > 10:
                published = now
                self.publish()
            time.sleep(1.5)


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
        data = json.loads(os.read(fd, MAX_REQUEST).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def worker(env=None):
    """Fil de l'agent loco-agent (root)."""
    try:
        Worker(env).loop()
    except Exception as e:                                            # Samba ne doit jamais arrêter l'agent
        print(f"partages de fichiers indisponibles : {e}", file=sys.stderr)


# --------------------------------------------------------------------------- fichiers des partages, dans le navigateur
# Le portail (compte loco) ouvre un partage dans son gestionnaire de fichiers sans jamais toucher lui-même aux dossiers
# des partages. Un service à part, lancé par l'agent sous le compte loco-share — propriétaire de tous les partages, sans
# aucun droit root — fait chaque opération dans le dossier d'un partage publié par l'agent : un chemin y est suivi un
# dossier après l'autre (descripteurs ouverts sans suivre de lien), si bien qu'aucun lien symbolique n'en fait sortir.
# Pour lire ou écrire un fichier, il passe au portail le fichier déjà ouvert (descripteur, SCM_RIGHTS). Les droits de la
# personne (lecture, ou lecture et écriture, partage par partage) sont décidés par le portail ; le service refuse en plus
# d'agrandir un partage plein ou au-delà de sa taille limite. Les refus sont des codes que le portail traduit.

FILES_RUN = "/run/loco-shares"
FILES_SOCK = os.path.join(FILES_RUN, "files.sock")
PORTAL_USER = "loco"
STAGING = ".loco-envois"                     # téléversements en cours, à la racine de chaque partage
STAGING_TTL = 86400                          # téléversement abandonné (portail relancé…) : effacé au bout d'un jour
FILES_MAX = 10000                            # éléments d'un dossier rendus au plus
FILES_REQUEST_MAX = 1 << 20
FILES_BUDGET = 20.0                          # secondes : taille d'un dossier, espace disque, recherche
FILES_FOUND = 500                            # résultats d'une recherche
FILES_ITEMS = 2000                           # éléments d'une même opération (sélection)
FILES_CONNECTIONS = 32
FILES_JOBS = 8                               # copies, déplacements et suppressions en cours à la fois
JOB_KEEP = 600                               # tâche terminée : son bilan reste lisible dix minutes
SPACE_MARGIN = 64 << 20                      # place toujours laissée libre sur la partition
COPY_CHUNK = 8 << 20
O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
O_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
O_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
WINDOWS_BAD = frozenset('\\/:*?"<>|')
WINDOWS_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?")
TOKEN_RE = re.compile(r"[0-9a-f]{32}")
JOB_RE = re.compile(r"[0-9a-f]{16}")
CONFLICTS = ("replace", "rename", "skip")


class FileError(Exception):
    """Refus d'une opération : code connu du portail (qui le traduit) et valeurs de son message."""

    def __init__(self, code, **values):
        super().__init__(code)
        self.code, self.values = code, values


ERRNO_CODES = {errno.ENOENT: "not-found", errno.ELOOP: "link", errno.ENOTDIR: "not-dir", errno.EISDIR: "is-dir",
               errno.EEXIST: "exists", errno.ENOTEMPTY: "exists", errno.ENOSPC: "disk", errno.EDQUOT: "disk",
               errno.EACCES: "denied", errno.EPERM: "denied", errno.EROFS: "denied", errno.ENAMETOOLONG: "bad-name",
               errno.EMFILE: "busy", errno.ENFILE: "busy"}


def _failure(e):
    if isinstance(e, FileError):
        return e
    if isinstance(e, OSError) and e.errno in ERRNO_CODES:
        return FileError(ERRNO_CODES[e.errno])
    if isinstance(e, OSError) and e.errno:
        return FileError("io", error=os.strerror(e.errno))
    return FileError("io", error=type(e).__name__)


def _parts(path):
    """Chemin relatif au partage (« Photos/2026/a.jpg ») : ses éléments, un par un — jamais « . » ni « .. »."""
    if not isinstance(path, str) or len(path) > 4096 or "\x00" in path:
        raise FileError("bad-path")
    parts = [p for p in path.split("/") if p]
    for part in parts:
        try:
            size = len(os.fsencode(part))
        except UnicodeError:
            raise FileError("bad-path") from None
        if part in (".", "..") or size > 255:
            raise FileError("bad-path")
    return parts


def _new_name(name, top=False):
    """Nom choisi dans le navigateur (dossier, fichier, renommage, téléversement) : un élément, lisible aussi par les
    postes Windows (ni « \\ / : * ? \" < > | », ni caractère de contrôle, ni point ou espace final, ni nom réservé)."""
    if not isinstance(name, str):
        raise FileError("bad-name")
    try:
        size = len(os.fsencode(name))
    except UnicodeError:
        raise FileError("bad-name") from None
    if (not name or size > 255 or name in (".", "..") or name[-1] in ". " or WINDOWS_RESERVED.fullmatch(name)
            or any(c in WINDOWS_BAD or ord(c) < 32 or ord(c) == 127 for c in name) or (top and name == STAGING)):
        raise FileError("bad-name")
    return name


def _fold(text):
    """Texte comparé sans casse ni accents (recherche)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _lstat(dirfd, name):
    try:
        return os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _describe(name, st):
    mode = st.st_mode
    kind = "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
    return {"name": name, "type": kind, "size": st.st_size if kind == "file" else 0, "mtime": int(st.st_mtime),
            "link": stat.S_ISLNK(mode)}


def _free_name(dirfd, name):
    """« photo (2).jpg », « Dossier (3) »… : premier nom libre à côté de « name »."""
    stem, ext = os.path.splitext(name)
    if not stem:
        stem, ext = name, ""
    for n in range(2, 10000):
        candidate = f"{stem} ({n}){ext}"
        if len(os.fsencode(candidate)) > 255:
            candidate = f"{stem[:200]} ({n}){ext[:40]}"
        if _lstat(dirfd, candidate) is None:
            return candidate
    raise FileError("exists")


class _Partial(Exception):
    """Parcours arrêté au bout de son temps : le résultat est partiel."""


def _walk(fd, deadline, skip=None, path=()):
    """Chaque élément sous le dossier ouvert fd, en profondeur : (dossiers, nom, stat, dossier ouvert). Aucun lien n'est
    suivi ; un dossier illisible est passé ; « skip » est ignoré au premier niveau. Au-delà de deadline : _Partial."""
    try:
        with os.scandir(fd) as it:
            items = [e for e in it if not (skip and not path and e.name == skip)]
    except OSError:
        return
    subdirs = []
    for entry in items:
        if time.monotonic() > deadline:
            raise _Partial()
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        yield path, entry.name, st, fd
        if stat.S_ISDIR(st.st_mode):
            subdirs.append(entry.name)
    for name in subdirs:
        try:
            sub = os.open(name, O_DIR, dir_fd=fd)
        except OSError:
            continue
        try:
            yield from _walk(sub, deadline, skip, path + (name,))
        finally:
            os.close(sub)


def _tree_size(dirfd, name, st, deadline):
    """Octets sous un élément (dossier parcouru, fichier, lien : 0)."""
    if stat.S_ISREG(st.st_mode):
        return st.st_size
    if not stat.S_ISDIR(st.st_mode):
        return 0
    total = 0
    sub = os.open(name, O_DIR, dir_fd=dirfd)
    try:
        walker = _walk(sub, deadline)
        try:
            for _path, _name, child, _fd in walker:
                if stat.S_ISREG(child.st_mode):
                    total += child.st_size
        finally:
            walker.close()
    finally:
        os.close(sub)
    return total


def _remove(dirfd, name, job=None):
    """Retire un élément ; un dossier avec tout son contenu, sans jamais suivre de lien."""
    st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dirfd)
        if job:
            job.step()
        return
    fd = os.open(name, O_DIR, dir_fd=dirfd)
    try:
        with os.scandir(fd) as it:
            children = list(it)
        for child in children:
            if job and job.cancelled:
                raise FileError("cancelled")
            if child.is_dir(follow_symlinks=False):
                _remove(fd, child.name, job)
            else:
                os.unlink(child.name, dir_fd=fd)
                if job:
                    job.step()
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dirfd)
    if job:
        job.step()


def _pump(src, dst, job=None):
    """Copie le contenu d'un fichier ouvert dans un autre (copy_file_range, sinon lecture/écriture)."""
    fast = hasattr(os, "copy_file_range")                     # selon la bibliothèque C de Python (musl, glibc)
    while True:
        if job and job.cancelled:
            raise FileError("cancelled")
        if fast:
            try:
                n = os.copy_file_range(src, dst, COPY_CHUNK)
            except OSError as e:
                if e.errno not in (errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EPERM):
                    raise
                fast = False
                continue
        else:
            data = os.read(src, 1 << 20)
            n = len(data)
            view = memoryview(data)
            while view:
                view = view[os.write(dst, view):]
        if not n:
            return
        if job:
            job.done += n


def _copy(sfd, name, dfd, target, job=None):
    """Copie un élément (fichier, dossier et son contenu, lien recopié tel quel) ; dates de modification gardées."""
    st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        os.symlink(os.readlink(name, dir_fd=sfd), target, dir_fd=dfd)
    elif stat.S_ISREG(st.st_mode):
        src = os.open(name, O_READ, dir_fd=sfd)
        try:
            dst = os.open(target, O_NEW, 0o660, dir_fd=dfd)
            try:
                _pump(src, dst, job)
                os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
            except BaseException:
                os.close(dst)
                with contextlib.suppress(OSError):
                    os.unlink(target, dir_fd=dfd)
                raise
            os.close(dst)
        finally:
            os.close(src)
    elif stat.S_ISDIR(st.st_mode):
        os.mkdir(target, 0o2770, dir_fd=dfd)
        src = os.open(name, O_DIR, dir_fd=sfd)
        try:
            dst = os.open(target, O_DIR, dir_fd=dfd)
            try:
                os.fchmod(dst, 0o2770)
                with os.scandir(src) as it:
                    children = sorted(e.name for e in it)
                for child in children:
                    if job and job.cancelled:
                        raise FileError("cancelled")
                    _copy(src, child, dst, child, job)
                os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
            finally:
                os.close(dst)
        finally:
            os.close(src)
    if job:
        job.step()


def _zip_time(mtime):
    t = time.localtime(max(mtime, 315532800))                 # une archive ZIP ne date rien d'avant 1980
    return t[:6]


def _zip_name(text):
    return os.fsencode(text).decode("utf-8", "replace")


class Job:
    """Copie, déplacement ou suppression en cours dans le service : avancement lu par le portail."""

    def __init__(self, kind, items):
        self.id, self.kind, self.items = secrets.token_hex(8), kind, items
        self.state, self.done, self.total, self.count = "running", 0, 0, 0
        self.current, self.errors, self.cancelled, self.finished = "", [], False, 0.0
        self.result = {}

    def step(self):
        self.count += 1

    def view(self):
        return {"id": self.id, "kind": self.kind, "state": self.state, "done": self.done, "total": self.total,
                "count": self.count, "items": self.items, "current": self.current, "errors": self.errors[:50],
                "result": self.result}


class ShareFiles:
    """Opérations du gestionnaire de fichiers dans les partages publiés par l'agent, sous le compte loco-share."""

    def __init__(self, status_path=None):
        self.status_path = status_path or STATUS
        self.lock = threading.Lock()
        self.added = {}                          # partage -> [(instant, octets)] ajoutés depuis son dernier relevé
        self.jobs = {}
        self.uploads = {}                        # jeton -> partage : téléversements en cours

    # -- partages et dossiers

    @staticmethod
    def parts(path):
        """Chemin d'une demande : comme _parts, sans jamais atteindre le dossier des téléversements en cours."""
        parts = _parts(path)
        if parts and parts[0] == STAGING:
            raise FileError("not-found")
        return parts

    def share(self, name):
        shares = _read_json(self.status_path).get("shares") or {}
        found = shares.get(name) if isinstance(name, str) else None
        if not isinstance(found, dict) or found.get("error") or not str(found.get("path") or "").startswith("/"):
            raise FileError("no-share")
        return dict(found, name=name)

    @contextlib.contextmanager
    def folder(self, share, parts):
        """Dossier « parts » du partage, ouvert un élément après l'autre sans jamais suivre de lien."""
        try:
            fd = os.open(share["path"], O_DIR)
        except OSError:
            raise FileError("no-share") from None
        try:
            for part in parts:
                try:
                    sub = os.open(part, O_DIR, dir_fd=fd)
                except OSError as e:
                    raise _failure(e) from None
                os.close(fd)
                fd = sub
            yield fd
        finally:
            os.close(fd)

    def grow(self, share, size, fd):
        """Place demandée par un ajout (téléversement, copie) : refusée si le partage est plein, si elle dépasse ce qu'il
        reste avant sa taille limite (relevé de l'agent et ajouts faits depuis), ou la place libre de la partition."""
        if share.get("full"):
            raise FileError("full")
        limit, used = int(share.get("limit") or 0), int(share.get("used") or 0)
        measured = float(share.get("measured") or 0)
        try:
            st = os.fstatvfs(fd)
            disk = st.f_bavail * st.f_frsize - SPACE_MARGIN
        except OSError:
            disk = None
        with self.lock:
            recent = [(at, n) for at, n in self.added.get(share["name"], []) if at > measured]
            left = limit - used - sum(n for _at, n in recent) if limit else None
            if left is not None and size > left:
                raise FileError("quota", free=max(0, left))
            if disk is not None and size > disk:
                raise FileError("disk")
            recent.append((time.time(), size))
            self.added[share["name"]] = recent

    def _item_paths(self, req, key="paths"):
        paths = req.get(key)
        if not isinstance(paths, list) or not 1 <= len(paths) <= FILES_ITEMS:
            raise FileError("bad-path")
        found = [self.parts(p) for p in paths]
        if any(not p for p in found):
            raise FileError("bad-path")
        return found

    # -- lecture

    def op_list(self, req):
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        with self.folder(share, parts) as fd:
            names = sorted((n for n in os.listdir(fd) if parts or n != STAGING), key=lambda n: (n.casefold(), n))
            entries = []
            for name in names[:FILES_MAX]:
                st = _lstat(fd, name)
                if st is not None:
                    entries.append(_describe(name, st))
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].casefold(), e["name"]))
        return {"path": "/".join(parts), "entries": entries, "truncated": len(names) > FILES_MAX}

    def op_stat(self, req):
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        if not parts:
            with self.folder(share, []) as fd:
                return dict(_describe(share["name"], os.fstat(fd)), path="")
        with self.folder(share, parts[:-1]) as fd:
            st = _lstat(fd, parts[-1])
        if st is None:
            raise FileError("not-found")
        return dict(_describe(parts[-1], st), path="/".join(parts))

    def op_size(self, req):
        """Taille d'un dossier, nombre de fichiers et de dossiers (fenêtre des propriétés)."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        deadline = time.monotonic() + FILES_BUDGET
        total = files = dirs = 0
        partial = False
        with self.folder(share, parts) as fd:
            walker = _walk(fd, deadline, STAGING if not parts else None)
            try:
                for _path, _name, st, _fd in walker:
                    if stat.S_ISDIR(st.st_mode):
                        dirs += 1
                    elif stat.S_ISREG(st.st_mode):
                        files += 1
                        total += st.st_size
            except (_Partial, RecursionError):
                partial = True
            finally:
                walker.close()
        return {"path": "/".join(parts), "size": total, "files": files, "dirs": dirs, "partial": partial}

    def op_usage(self, req):
        """Espace disque (façon TreeSize) : taille de chaque élément du dossier, du plus gros au plus petit."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        deadline = time.monotonic() + FILES_BUDGET
        entries, total, partial = [], 0, False
        with self.folder(share, parts) as fd:
            names = [n for n in os.listdir(fd) if parts or n != STAGING]
            partial = len(names) > FILES_MAX
            for name in names[:FILES_MAX]:
                st = _lstat(fd, name)
                if st is None:
                    continue
                try:
                    size = _tree_size(fd, name, st, deadline)
                except (_Partial, RecursionError):
                    size, partial = 0, True
                except OSError:
                    size = 0
                total += size
                entries.append(dict(_describe(name, st), size=size))
        entries.sort(key=lambda e: e["size"], reverse=True)
        return {"path": "/".join(parts), "total": total, "entries": entries, "partial": partial}

    def op_search(self, req):
        """Éléments dont le nom contient le texte cherché (sans casse ni accents), dans le dossier et ses sous-dossiers."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        query = _fold(str(req.get("q") or "")).strip()[:200]
        if not query:
            raise FileError("bad-query")
        deadline = time.monotonic() + FILES_BUDGET
        found, partial = [], False
        with self.folder(share, parts) as fd:
            walker = _walk(fd, deadline, STAGING if not parts else None)
            try:
                for path, name, st, _fd in walker:
                    if query in _fold(name):
                        found.append(dict(_describe(name, st), dir="/".join(parts + list(path))))
                        if len(found) >= FILES_FOUND:
                            partial = True
                            break
            except (_Partial, RecursionError):
                partial = True
            finally:
                walker.close()
        return {"path": "/".join(parts), "entries": found, "partial": partial}

    def op_open(self, req):
        """Fichier ouvert en lecture, passé au portail (téléchargement, aperçu)."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        if not parts:
            raise FileError("is-dir")
        with self.folder(share, parts[:-1]) as fd:
            try:
                handle = os.open(parts[-1], O_READ, dir_fd=fd)
            except OSError as e:
                raise _failure(e) from None
        st = os.fstat(handle)
        if not stat.S_ISREG(st.st_mode):
            os.close(handle)
            raise FileError("is-dir" if stat.S_ISDIR(st.st_mode) else "not-found")
        os.set_blocking(handle, True)
        return {"name": parts[-1], "size": st.st_size, "mtime": st.st_mtime_ns // 1000000}, [handle]

    def op_conflicts(self, req):
        """Éléments d'une copie ou d'un déplacement qui portent déjà leur nom à destination (à faire choisir avant)."""
        src = self.share(req.get("share"))
        dst = self.share(req.get("to_share") or req.get("share"))
        items, to = self._item_paths(req), self.parts(req.get("to", ""))
        with self.folder(dst, to) as dfd:
            taken = [p[-1] for p in items if _lstat(dfd, p[-1]) is not None
                     and not (dst["name"] == src["name"] and p[:-1] == to)]
        return {"conflicts": taken}

    # -- modifications

    def op_mkdir(self, req):
        """Nouveau dossier ; avec « parents », les dossiers manquants du chemin aussi (dossier téléversé)."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        if not parts:
            raise FileError("bad-path")
        if share.get("full"):
            raise FileError("full")
        parents = bool(req.get("parents"))
        with self.folder(share, []) as root:
            fd = os.dup(root)
            try:
                for i, part in enumerate(parts):
                    last = i == len(parts) - 1
                    if _lstat(fd, part) is None:
                        if not (last or parents):
                            raise FileError("not-found")
                        _new_name(part, top=not i)
                        with contextlib.suppress(FileExistsError):
                            os.mkdir(part, 0o2770, dir_fd=fd)
                        created = True
                    elif last and not parents:
                        raise FileError("exists")
                    else:
                        created = False
                    sub = os.open(part, O_DIR, dir_fd=fd)        # un fichier ou un lien à ce nom : refus
                    os.close(fd)
                    fd = sub
                    if created:
                        os.fchmod(fd, 0o2770)
            finally:
                os.close(fd)
        return {"path": "/".join(parts)}

    def op_newfile(self, req):
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        if not parts:
            raise FileError("bad-path")
        if share.get("full"):
            raise FileError("full")
        name = _new_name(parts[-1], top=len(parts) == 1)
        with self.folder(share, parts[:-1]) as fd:
            try:
                os.close(os.open(name, O_NEW, 0o660, dir_fd=fd))
            except OSError as e:
                raise _failure(e) from None
        return {"path": "/".join(parts)}

    def op_rename(self, req):
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        if not parts:
            raise FileError("bad-path")
        name = _new_name(req.get("name"), top=len(parts) == 1)
        with self.folder(share, parts[:-1]) as fd:
            old = _lstat(fd, parts[-1])
            if old is None:
                raise FileError("not-found")
            if name != parts[-1]:
                other = _lstat(fd, name)
                if other is not None and (other.st_ino, other.st_dev) != (old.st_ino, old.st_dev):
                    raise FileError("exists")
                try:
                    os.rename(parts[-1], name, src_dir_fd=fd, dst_dir_fd=fd)
                except OSError as e:
                    raise _failure(e) from None
        return {"path": "/".join(parts[:-1] + [name]), "name": name}

    def op_delete(self, req):
        share, items = self.share(req.get("share")), self._item_paths(req)

        def run(job):
            for parts in items:
                if job.cancelled:
                    raise FileError("cancelled")
                job.current = parts[-1]
                try:
                    with self.folder(share, parts[:-1]) as fd:
                        _remove(fd, parts[-1], job)
                except (OSError, FileError) as e:
                    if isinstance(e, FileError) and e.code == "cancelled":
                        raise
                    job.errors.append({"name": parts[-1], "error": _failure(e).code})
        return self.start("delete", len(items), run)

    def op_copy(self, req):
        return self._transfer(req, move=False)

    def op_move(self, req):
        return self._transfer(req, move=True)

    def _transfer(self, req, move):
        """Copie ou déplacement d'éléments (même partage ou un autre) vers un dossier ; pour chaque nom déjà pris à
        destination, la décision venue du navigateur : remplacer, garder les deux (nom libre), ou passer."""
        src = self.share(req.get("share"))
        dst = self.share(req.get("to_share") or req.get("share"))
        items, to = self._item_paths(req), self.parts(req.get("to", ""))
        decisions = req.get("decisions") if isinstance(req.get("decisions"), dict) else {}
        same = src["name"] == dst["name"]
        for parts in items:
            if same and to[:len(parts)] == parts:
                raise FileError("into-itself")
        with self.folder(dst, to):
            pass                                                  # la destination existe (sinon : refus immédiat)
        # Une copie, ou un déplacement vers un autre partage, agrandit la destination : place vérifiée d'abord.
        total = 0
        if not move or not same:
            deadline = time.monotonic() + FILES_BUDGET
            for parts in items:
                try:
                    with self.folder(src, parts[:-1]) as fd:
                        st = _lstat(fd, parts[-1])
                        total += _tree_size(fd, parts[-1], st, deadline) if st else 0
                except (_Partial, RecursionError):
                    break
            with self.folder(dst, to) as dfd:
                self.grow(dst, total, dfd)

        def run(job):
            job.total = total
            for parts in items:
                if job.cancelled:
                    raise FileError("cancelled")
                name = parts[-1]
                job.current = name
                decision = decisions.get(name)
                try:
                    self._transfer_one(job, src, parts, dst, to, move, decision if decision in CONFLICTS else None)
                except (OSError, FileError) as e:
                    if isinstance(e, FileError) and e.code == "cancelled":
                        raise
                    job.errors.append({"name": name, "error": _failure(e).code})
        return self.start("move" if move else "copy", len(items), run)

    def _transfer_one(self, job, src, parts, dst, to, move, decision):
        name, same_dir = parts[-1], src["name"] == dst["name"] and parts[:-1] == to
        with self.folder(src, parts[:-1]) as sfd, self.folder(dst, to) as dfd:
            st = _lstat(sfd, name)
            if st is None:
                raise FileError("not-found")
            target, existing = name, _lstat(dfd, name)
            if existing is not None:
                if same_dir and move:
                    return                                        # déplacé là où il est déjà : rien à faire
                if same_dir or decision == "rename":
                    target = _free_name(dfd, name)                # copie dans son propre dossier : « nom (2) »
                elif decision == "skip":
                    return
                elif decision != "replace":
                    raise FileError("exists")
            if move:
                try:
                    if target == name and existing is not None:
                        if stat.S_ISDIR(existing.st_mode) or stat.S_ISDIR(st.st_mode):
                            _remove(dfd, name)
                    os.rename(name, target, src_dir_fd=sfd, dst_dir_fd=dfd)
                    job.step()
                    return
                except OSError as e:
                    if e.errno != errno.EXDEV:
                        raise
            if target == name and existing is not None:
                # Remplacer : copie sous un nom provisoire, puis l'ancien laisse sa place (rien n'est perdu si la
                # copie échoue en route).
                provisional = f".loco-copie-{secrets.token_hex(6)}"
                _copy(sfd, name, dfd, provisional, job)
                try:
                    if stat.S_ISDIR(existing.st_mode):
                        _remove(dfd, name)
                    os.rename(provisional, name, src_dir_fd=dfd, dst_dir_fd=dfd)
                except BaseException:
                    with contextlib.suppress(OSError):
                        _remove(dfd, provisional)
                    raise
            else:
                _copy(sfd, name, dfd, target, job)
            if move:
                _remove(sfd, name)

    # -- téléversements

    def staging(self, share):
        """Dossier des téléversements en cours, à la racine du partage (créé au besoin)."""
        with self.folder(share, []) as root:
            try:
                os.mkdir(STAGING, 0o2770, dir_fd=root)
            except FileExistsError:
                pass
            except OSError as e:
                raise _failure(e) from None
            try:
                return os.open(STAGING, O_DIR, dir_fd=root)
            except OSError as e:
                raise _failure(e) from None

    def op_upload(self, req):
        """Début d'un téléversement : place vérifiée, fichier provisoire ouvert et passé au portail, qui y écrit."""
        share, parts = self.share(req.get("share")), self.parts(req.get("dir", ""))
        _new_name(req.get("name"), top=not parts)
        try:
            size = int(req.get("size"))
        except (TypeError, ValueError):
            raise FileError("size") from None
        if size < 0:
            raise FileError("size")
        with self.folder(share, parts) as fd:
            self.grow(share, size, fd)
        token = secrets.token_hex(16)
        sfd = self.staging(share)
        try:
            handle = os.open(token + ".part", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o660, dir_fd=sfd)
        except OSError as e:
            raise _failure(e) from None
        finally:
            os.close(sfd)
        with self.lock:
            self.uploads[token] = share["name"]
        return {"token": token}, [handle]

    def op_upload_done(self, req):
        """Fin d'un téléversement : le fichier provisoire prend sa place (remplace, garde les deux, ou refus si le nom
        est pris), avec sa date de modification d'origine."""
        share, parts = self.share(req.get("share")), self.parts(req.get("dir", ""))
        name = _new_name(req.get("name"), top=not parts)
        token = str(req.get("token", ""))
        if not TOKEN_RE.fullmatch(token):
            raise FileError("bad-path")
        conflict = req.get("conflict") if req.get("conflict") in ("replace", "rename") else None
        sfd = self.staging(share)
        try:
            st = _lstat(sfd, token + ".part")
            if st is None or not stat.S_ISREG(st.st_mode):
                raise FileError("not-found")
            if "size" in req and st.st_size != int(req.get("size") or 0):
                raise FileError("size")
            mtime = req.get("mtime")
            if isinstance(mtime, (int, float)) and 0 < mtime < 2 ** 42:
                os.utime(token + ".part", ns=(time.time_ns(), int(mtime) * 1000000), dir_fd=sfd, follow_symlinks=False)
            with self.folder(share, parts) as dfd:
                target, existing = name, _lstat(dfd, name)
                if existing is not None:
                    if conflict == "rename":
                        target = _free_name(dfd, name)
                    elif conflict != "replace":
                        raise FileError("exists")
                    elif stat.S_ISDIR(existing.st_mode):
                        raise FileError("is-dir")
                    elif isinstance(req.get("expect"), int) and existing.st_mtime_ns // 1000000 != req["expect"]:
                        raise FileError("changed")              # enregistré depuis l'éditeur, modifié entre-temps
                try:
                    os.rename(token + ".part", target, src_dir_fd=sfd, dst_dir_fd=dfd)
                except OSError as e:
                    raise _failure(e) from None
                placed = _lstat(dfd, target)
        finally:
            os.close(sfd)
        with self.lock:
            self.uploads.pop(token, None)
        return {"name": target, "path": "/".join(parts + [target]),
                "mtime": placed.st_mtime_ns // 1000000 if placed else None}

    def op_upload_cancel(self, req):
        share, token = self.share(req.get("share")), str(req.get("token", ""))
        if TOKEN_RE.fullmatch(token):
            sfd = self.staging(share)
            try:
                with contextlib.suppress(OSError):
                    os.unlink(token + ".part", dir_fd=sfd)
            finally:
                os.close(sfd)
        with self.lock:
            self.uploads.pop(token, None)
        return {}

    # -- archive ZIP (téléchargement d'un dossier ou d'une sélection)

    def op_zip(self, req):
        """Archive ZIP de la sélection (ou de tout le dossier), écrite à la volée dans un tube passé au portail."""
        share, parts = self.share(req.get("share")), self.parts(req.get("path", ""))
        names = req.get("names") or []
        if not isinstance(names, list) or len(names) > FILES_ITEMS or any(
                not isinstance(n, str) or len(_parts(n)) != 1 or (not parts and n == STAGING) for n in names):
            raise FileError("bad-path")
        with self.folder(share, parts) as fd:
            if not names:
                names = sorted(n for n in os.listdir(fd) if parts or n != STAGING)
            elif any(_lstat(fd, n) is None for n in names):
                raise FileError("not-found")
        read, write = os.pipe2(os.O_CLOEXEC)
        threading.Thread(target=self._zip_write, args=(share, parts, names, write), name="loco-files-zip",
                         daemon=True).start()
        return {"names": len(names)}, [read]

    def _zip_write(self, share, parts, names, write):
        out = os.fdopen(write, "wb", buffering=1 << 20)
        try:
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                with self.folder(share, parts) as fd:
                    for name in names:
                        self._zip_add(archive, fd, name, name)
        except (OSError, FileError, ValueError, RecursionError, zipfile.LargeZipFile):
            pass                                                  # navigateur parti, ou dossier illisible
        finally:
            with contextlib.suppress(OSError):
                out.close()

    def _zip_add(self, archive, dirfd, name, arc):
        st = _lstat(dirfd, name)
        if st is None:
            return
        if stat.S_ISDIR(st.st_mode):
            info = zipfile.ZipInfo(_zip_name(arc) + "/", date_time=_zip_time(st.st_mtime))
            info.external_attr = (0o40775 << 16) | 0x10
            archive.writestr(info, b"")
            sub = os.open(name, O_DIR, dir_fd=dirfd)
            try:
                for child in sorted(os.listdir(sub)):
                    self._zip_add(archive, sub, child, arc + "/" + child)
            finally:
                os.close(sub)
        elif stat.S_ISREG(st.st_mode):
            info = zipfile.ZipInfo(_zip_name(arc), date_time=_zip_time(st.st_mtime))
            info.external_attr = 0o100664 << 16
            info.file_size = st.st_size
            handle = os.open(name, O_READ, dir_fd=dirfd)
            with os.fdopen(handle, "rb") as source, archive.open(info, "w", force_zip64=st.st_size > (1 << 31)) as dest:
                shutil.copyfileobj(source, dest, 1 << 20)

    # -- tâches (copie, déplacement, suppression)

    def start(self, kind, items, run):
        with self.lock:
            now = time.time()
            for key in [k for k, j in self.jobs.items() if j.finished and now - j.finished > JOB_KEEP]:
                del self.jobs[key]
            if sum(1 for j in self.jobs.values() if not j.finished) >= FILES_JOBS:
                raise FileError("busy")
            job = Job(kind, items)
            self.jobs[job.id] = job

        def body():
            try:
                run(job)
                job.state = "done"
            except FileError as e:
                job.state = "cancelled" if e.code == "cancelled" else "failed"
                if e.code != "cancelled":
                    job.errors.append({"name": job.current, "error": e.code})
            except Exception as e:                                # une tâche ne doit jamais arrêter le service
                job.state = "failed"
                job.errors.append({"name": job.current, "error": _failure(e).code})
            finally:
                job.current, job.finished = "", time.time()
        threading.Thread(target=body, name=f"loco-files-{kind}", daemon=True).start()
        return {"job": job.id}

    def _job(self, req):
        key = str(req.get("job", ""))
        job = self.jobs.get(key) if JOB_RE.fullmatch(key) else None
        if job is None:
            raise FileError("not-found")
        return job

    def op_job(self, req):
        return self._job(req).view()

    def op_job_cancel(self, req):
        self._job(req).cancelled = True
        return {}

    # -- service

    def handle(self, req):
        """(réponse, descripteurs à passer) ; un refus est une réponse {"ok": false, "error": code}."""
        op = req.get("op") if isinstance(req, dict) else None
        method = getattr(self, "op_" + op, None) if isinstance(op, str) and re.fullmatch(r"[a-z_]{2,20}", op) else None
        try:
            if method is None:
                raise FileError("bad-op")
            reply = method(req)
        except Exception as e:                                    # refus, ou demande mal formée : jamais une chute
            failure = _failure(e)
            return {"ok": False, "error": failure.code, "values": failure.values}, []
        reply, fds = reply if isinstance(reply, tuple) else (reply, [])
        return dict(reply, ok=True), fds

    def tidy(self):
        """Téléversements abandonnés depuis un jour (portail relancé en route) : effacés ; vieilles tâches oubliées."""
        now = time.time()
        for name in list((_read_json(self.status_path).get("shares") or {})):
            try:
                share = self.share(name)
                with self.folder(share, []) as root:
                    try:
                        sfd = os.open(STAGING, O_DIR, dir_fd=root)
                    except OSError:
                        continue
                    try:
                        with self.lock:
                            active = {t for t, s in self.uploads.items() if s == name}
                        for entry in os.listdir(sfd):
                            st = _lstat(sfd, entry)
                            if st and entry[:32] not in active and now - st.st_mtime > STAGING_TTL:
                                with contextlib.suppress(OSError):
                                    _remove(sfd, entry)
                    finally:
                        os.close(sfd)
            except (FileError, OSError):
                continue
        with self.lock:
            for key in [k for k, j in self.jobs.items() if j.finished and now - j.finished > JOB_KEEP]:
                del self.jobs[key]


def _peer_uid(conn):
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid
    except OSError:
        return None


def _files_connection(files, conn, slots, allowed):
    try:
        conn.settimeout(30)
        if _peer_uid(conn) not in allowed:
            return
        buf = b""
        while not buf.endswith(b"\n") and len(buf) <= FILES_REQUEST_MAX:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        try:
            req = json.loads(buf) if len(buf) <= FILES_REQUEST_MAX else None
        except ValueError:
            req = None
        reply, fds = files.handle(req) if isinstance(req, dict) else ({"ok": False, "error": "bad-op"}, [])
        data = json.dumps(reply).encode() + b"\n"
        try:
            if fds:
                socket.send_fds(conn, [data], fds)
            else:
                conn.sendall(data)
        finally:
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
    except OSError:
        pass
    finally:
        conn.close()
        slots.release()


def files_serve(listen_fd, allowed=None, files=None):
    """Service des fichiers des partages (processus à part, compte loco-share) : une demande JSON par connexion sur le
    socket hérité de l'agent ; seul le portail (compte loco) y est entendu. Il s'arrête avec l'agent qui l'a lancé."""
    server = socket.socket(fileno=listen_fd)
    files = files or ShareFiles()
    if allowed is None:
        allowed = {0}
        with contextlib.suppress(KeyError):
            allowed.add(pwd.getpwnam(PORTAL_USER).pw_uid)
    parent = os.getppid()
    stopped = threading.Event()

    def watch():
        tidied = 0.0
        while not stopped.wait(2):
            if os.getppid() != parent:                            # agent arrêté ou relancé : ce service s'en va aussi
                os._exit(0)
            if time.time() - tidied > 3600:
                tidied = time.time()
                with contextlib.suppress(Exception):
                    files.tidy()
    threading.Thread(target=watch, name="loco-files-watch", daemon=True).start()
    slots = threading.BoundedSemaphore(FILES_CONNECTIONS)
    while True:
        try:
            conn, _addr = server.accept()
        except OSError as error:
            if error.errno in (errno.EBADF, errno.EINVAL):
                stopped.set()
                server.close()
                return
            time.sleep(0.2)
            continue
        if not slots.acquire(blocking=False):
            with contextlib.suppress(OSError):
                conn.sendall(json.dumps({"ok": False, "error": "busy"}).encode() + b"\n")
            conn.close()
            continue
        threading.Thread(target=_files_connection, args=(files, conn, slots, allowed), name="loco-files",
                         daemon=True).start()


def files_socket(path=None, group=PORTAL_USER):
    """Socket d'écoute du service (créé par l'agent root) : dossier et socket réservés au groupe du portail."""
    path = path or FILES_SOCK
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o750, exist_ok=True)
    if group:
        shutil.chown(folder, "root", group)
    os.chmod(folder, 0o750)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(64)                                               # root seul s'y connecte jusqu'aux droits ci-dessous
    if group:
        shutil.chown(path, "root", group)
    os.chmod(path, 0o660)
    return sock


def files_call(req, timeout=30, path=None):
    """Côté portail : une demande au service des fichiers. Rend (réponse, descripteurs reçus) — l'appelant ferme
    ces derniers. OSError si le service ne répond pas."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    fds, buf = [], b""
    try:
        client.connect(path or FILES_SOCK)
        client.sendall(json.dumps(req).encode() + b"\n")
        while not buf.endswith(b"\n"):
            data, got, _flags, _addr = socket.recv_fds(client, 1 << 20, 4)
            fds.extend(got)
            if not data:
                break
            buf += data
            if len(buf) > 64 * 1024 * 1024:
                raise OSError(errno.EMSGSIZE, "réponse trop longue")
        reply = json.loads(buf)
        if not isinstance(reply, dict):
            raise ValueError("réponse invalide")
    except BaseException:
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        raise
    finally:
        client.close()
    return reply, fds


# --------------------------------------------------------------------------- dfree (appelé par smbd)

def dfree(directory="."):
    """Taille et place libre d'un partage vues par les postes : sa taille limite, et ce qu'il en reste (sans dépasser la
    place libre de la partition). Ligne « total libre » en blocs de 1024 octets, pour le « dfree command » de Samba."""
    path = os.path.realpath(directory or ".")
    try:
        st = os.statvfs(path)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    except OSError:
        total = free = 0
    for share in (status().get("shares") or {}).values():
        root = share.get("path") or ""
        if root and (path == root or path.startswith(root + "/")):
            limit, used = int(share.get("limit") or 0), int(share.get("used") or 0)
            if limit:
                total, free = limit, max(0, min(limit - used, free))
            break
    return f"{total // 1024} {free // 1024}"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "dfree":
        print(dfree(sys.argv[2] if len(sys.argv) > 2 else "."))
    elif len(sys.argv) == 3 and sys.argv[1] == "files" and sys.argv[2].isdigit():
        files_serve(int(sys.argv[2]))
