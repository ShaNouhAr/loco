"""Haute disponibilité de loco : deux serveurs, un principal et un secours, et une IP virtuelle (keepalived, VRRP).

Rôles. Le serveur qui porte l'IP virtuelle est le principal. keepalived (VRRP en unicast entre les deux serveurs) la
déplace : il appelle « locoadmin.py ha-check » pour juger de la santé du portail et « locoadmin.py ha-notify » à chaque
changement d'état (MASTER : principal ; BACKUP, FAULT ou STOP : secours). « nopreempt » : un serveur qui revient ne
reprend pas l'IP à celui qui l'a gardée.

Réplication (fil de loco-agent, root, toutes les ~10 s). Le principal prépare dans ha-out/, le secours tire par le
portail du principal (HTTPS, autorité de la grappe épinglée, jeton dérivé du secret partagé) :
- la base : instantané SQLite, appliqué chez le secours par l'API de sauvegarde (sûr pendant les lectures) ;
- un paquet de configuration (/etc/loco, clé de signature des dépôts, apparence) chiffré en AES-GCM avec une clé dérivée
  du secret : le portail le sert sans pouvoir le lire ;
- les dépôts (apt, rpm, flatpak, win), par différence de fichiers.
Les caches ne sont pas répliqués (ils se reconstituent). /etc/loco/ha, propre à chaque serveur, ne l'est jamais.

Appairage. Le principal publie un code à usage unique (distinct du secret) ; le secours l'envoie une fois et reçoit, scellés
avec ce code, le secret et la configuration de la grappe. Le secret ne quitte jamais l'agent root.
"""
import contextlib
import gzip
import hashlib
import io
import ipaddress
import json
import os
import pwd
import grp
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

ETC = "/etc/loco"
STATE = "/var/lib/loco"
DATA = "/srv/loco"
AGENT_DIR = "/var/lib/loco-agent"
RUN = "/run/loco-ha"
KEEPALIVED_CONF = "/etc/keepalived/keepalived.conf"

AREAS = ("apt", "rpm", "flatpak", "win")               # dépôts répliqués (les caches ne le sont pas)
ETC_SKIP = {"ha", "server-shell", "appliance"}         # propres à chaque serveur
STATE_DIRS = ("gnupg", "appearance")                   # clé de signature des dépôts, logo et fonds
CYCLE = 10                                             # secondes entre deux passages du fil
DB_EVERY = 15                                          # instantané de la base au plus toutes les 15 s
FILES_EVERY = 30                                       # relevé des dépôts au plus toutes les 30 s
CODE_RE = re.compile(r"LHA-[A-Za-z0-9_-]{20,64}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")
PATH_RE = re.compile(r"[A-Za-z0-9._+~@=,%-][A-Za-z0-9._+~@=,%/-]{0,1000}")


# --------------------------------------------------------------------------- chemins (modifiables par les tests)

def ha_dir():
    return os.path.join(ETC, "ha")


def node_path():
    return os.path.join(ha_dir(), "node.json")


def secret_path():
    return os.path.join(ha_dir(), "secret")


def verifier_path():
    return os.path.join(ha_dir(), "verifier")


def join_verifier_path():
    return os.path.join(ha_dir(), "join-verifier")


def join_code_path():
    return os.path.join(ha_dir(), "join-code")


def ca_path():
    return os.path.join(ha_dir(), "ca.pem")


def out_dir():
    return os.path.join(AGENT_DIR, "ha-out")


def status_path():
    return os.path.join(AGENT_DIR, "ha.json")


def applied_path():
    return os.path.join(AGENT_DIR, "ha-applied.json")


def db_path():
    return os.path.join(STATE, "portal.db")


def maintenance_flag():
    return os.path.join(RUN, "maintenance")


def yield_flag():
    return os.path.join(RUN, "yield")


# --------------------------------------------------------------------------- secrets et chiffrement

def derive(secret, purpose):
    return hashlib.sha256(b"loco-ha-" + purpose.encode() + b"\0" + secret.encode()).digest()


def auth_token(secret):
    """Jeton présenté au portail du pair (en-tête X-Loco-HA) ; le portail n'en garde que l'empreinte."""
    return derive(secret, "auth").hex()


def verifier(token):
    return hashlib.sha256(token.encode()).hexdigest()


def seal(data, key):
    """AES-256-GCM : « LHA1 » + nonce + chiffré authentifié."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return b"LHA1" + nonce + AESGCM(key).encrypt(nonce, data, b"loco-ha")


def unseal(blob, key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(b"LHA1") or len(blob) < 32:
        raise ValueError("format inconnu")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], b"loco-ha")
    except InvalidTag:
        raise ValueError("secret différent ou données altérées")


# --------------------------------------------------------------------------- configuration de ce serveur

def _write(path, data, mode=0o644, group=None):
    """Écriture atomique (fichier temporaire puis renommage), droits posés avant le renommage."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    os.chmod(tmp, mode)
    if group:
        with contextlib.suppress(KeyError, PermissionError):
            os.chown(tmp, 0, grp.getgrnam(group).gr_gid)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def node():
    """Configuration HA de ce serveur ({} si la haute disponibilité n'est pas configurée)."""
    return read_json(node_path(), {}) or {}


def save_node(conf):
    os.makedirs(ha_dir(), mode=0o750, exist_ok=True)
    with contextlib.suppress(KeyError, PermissionError):
        os.chown(ha_dir(), 0, grp.getgrnam("loco").gr_gid)
    os.chmod(ha_dir(), 0o750)
    _write(node_path(), json.dumps(conf, indent=1, sort_keys=True), 0o640, "loco")


def read_secret():
    try:
        with open(secret_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_secret(secret):
    _write(secret_path(), secret + "\n", 0o600)
    _write(verifier_path(), verifier(auth_token(secret)) + "\n", 0o640, "loco")


def role():
    return node().get("role", "")


def set_role(new, log=print):
    conf = node()
    if not conf or conf.get("role") == new:
        return False
    conf["role"], conf["since"] = new, time.strftime("%Y-%m-%dT%H:%M:%S")
    if new == "primary":
        conf["epoch"] = int(conf.get("epoch") or 0) + 1
    save_node(conf)
    event(f"rôle : {'principal' if new == 'primary' else 'secours'}")
    log(f"Ce serveur devient {'le principal' if new == 'primary' else 'le secours'}.")
    return True


def event(text):
    """Événement gardé dans l'état publié (20 derniers)."""
    status = read_json(status_path(), {}) or {}
    events = (status.get("events") or [])[-19:]
    events.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text})
    status["events"] = events
    _write(status_path(), json.dumps(status, indent=1), 0o644)


def publish(**values):
    status = read_json(status_path(), {}) or {}
    status.update(values, updated=time.strftime("%Y-%m-%dT%H:%M:%S"))
    _write(status_path(), json.dumps(status, indent=1), 0o644)


# --------------------------------------------------------------------------- réseau et keepalived

def interface_of(address):
    """Interface qui porte l'adresse (ip -o addr), et son préfixe ; (None, None) sinon."""
    out = subprocess.run(["ip", "-o", "addr", "show"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         universal_newlines=True).stdout
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[2] in ("inet", "inet6"):
            iface = ipaddress.ip_interface(fields[3])
            if str(iface.ip) == address:
                return fields[1].split("@")[0], iface.network.prefixlen
    return None, None


def holds(vip):
    """Ce serveur porte-t-il l'IP virtuelle ?"""
    if not vip:
        return False
    address = str(ipaddress.ip_interface(vip).ip)
    return interface_of(address)[0] is not None


def keepalived_conf(conf, python, script):
    """Configuration keepalived : une instance VRRP en unicast, nopreempt, contrôle du portail, notification à loco."""
    me, peer = conf["self"], conf.get("peer") or {}
    vip = ipaddress.ip_interface(conf["vip"])
    lines = [
        "# Généré par loco (haute disponibilité) : modifier depuis Administration > Haute disponibilité.",
        "global_defs {",
        f"    router_id loco_{me.get('name') or 'node'}",
        "    script_user root",
        "    enable_script_security",
        "}",
        "vrrp_script loco_check {",
        f'    script "{python} {script} ha-check"',
        "    interval 5",
        "    timeout 4",
        "    fall 2",
        "    rise 2",
        "}",
        "vrrp_instance loco {",
        "    state BACKUP",
        f"    interface {conf['interface']}",
        f"    virtual_router_id {int(conf['vrid'])}",
        f"    priority {int(me.get('priority') or 100)}",
        "    nopreempt",
        "    advert_int 1",
    ]
    # Unicast seulement quand un pair existe : keepalived refuse « unicast sans pair » (sinon multicast,
    # suffisant pour le principal seul en attente d'un secours).
    if peer.get("address"):
        lines += [f"    unicast_src_ip {me['address']}", "    unicast_peer {", f"        {peer['address']}", "    }"]
    lines += [
        "    authentication {",
        "        auth_type PASS",
        f"        auth_pass {conf['auth_pass']}",
        "    }",
        "    virtual_ipaddress {",
        f"        {vip.ip}/{vip.network.prefixlen} dev {conf['interface']}",
        "    }",
        "    track_script {",
        "        loco_check",
        "    }",
        f'    notify "{python} {script} ha-notify"',
        "}",
    ]
    return "\n".join(lines) + "\n"


def _openrc():
    return shutil.which("rc-service") is not None and not os.path.isdir("/run/systemd/system")


def service(name, action, log=print):
    """start / restart / reload / stop / enable / disable, sous OpenRC (appliance) ou systemd."""
    if _openrc():
        cmd = {"enable": ["rc-update", "add", name, "default"], "disable": ["rc-update", "del", name, "default"]}.get(
            action, ["rc-service", name, action])
    else:
        cmd = {"enable": ["systemctl", "enable", name], "disable": ["systemctl", "disable", name]}.get(
            action, ["systemctl", action, name])
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if p.returncode != 0 and action not in ("stop", "disable"):
        log(p.stdout.strip()[-500:])
    return p.returncode == 0


def install_keepalived(log=print):
    if shutil.which("keepalived"):
        return True
    log("Installation de keepalived…")
    for tool, args in (("apk", ["add", "--no-cache", "keepalived"]), ("apt-get", ["install", "-y", "-q", "keepalived"]),
                       ("dnf", ["install", "-y", "keepalived"]), ("yum", ["install", "-y", "keepalived"])):
        if shutil.which(tool):
            env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
            p = subprocess.run([tool, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=env)
            log(p.stdout.strip()[-800:])
            return p.returncode == 0 and shutil.which("keepalived") is not None
    log("Aucun gestionnaire de paquets connu pour installer keepalived.")
    return False


def apply_keepalived(conf, python, script, log=print):
    """Écrit la configuration, la vérifie (keepalived -t) puis (re)démarre le service et l'active au démarrage."""
    text = keepalived_conf(conf, python, script)
    _write(KEEPALIVED_CONF, text, 0o600)
    check = subprocess.run(["keepalived", "-t", "-f", KEEPALIVED_CONF], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
    # keepalived 2.3 rend un code non nul même pour un simple avertissement de dépréciation : on ne refuse
    # que sur une vraie erreur de configuration.
    out = check.stdout.strip()
    fatal = any(w in out.lower() for w in ("error", "unknown keyword", "invalid", "no such", "cannot", "fault", "unexpected"))
    if check.returncode != 0 and fatal:
        log(out[-800:])
        raise ValueError("configuration keepalived refusée")
    if out:
        log(out[-800:])
    service("keepalived", "enable", log)
    return service("keepalived", "restart", log)


# --------------------------------------------------------------------------- client HTTPS vers le pair

def _context(pinned=True):
    if not pinned:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                 # premier contact : la réponse est scellée par le code
        return ctx
    ctx = ssl.create_default_context(cafile=ca_path())
    ctx.check_hostname = False                          # autorité de la grappe épinglée : l'adresse importe peu
    return ctx


def https_port():
    """Port HTTPS de ce serveur, le même sur les deux une fois appairés : loco.conf est répliqué."""
    try:
        with open(os.path.join(ETC, "loco.conf")) as f:
            found = re.search(r"^HTTPS_PORT=([0-9]+)$", f.read(), re.M)
    except OSError:
        found = None
    return found.group(1) if found else "8443"


def call(address, path, token, data=None, pinned=True, timeout=30, into=None, port=None):
    """Requête au portail du pair. data : JSON envoyé (POST) ; into : fichier où écrire une réponse volumineuse."""
    # Un secours en cours d'appairage n'a pas encore le port du principal : il l'a noté dans sa configuration.
    url = f"https://{_host(address)}:{port or node().get('peer_port') or https_port()}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"X-Loco-HA": token, "Content-Type": "application/json",
                                          "User-Agent": "loco-ha"})
    with urllib.request.urlopen(req, context=_context(pinned), timeout=timeout) as r:
        if into is not None:
            shutil.copyfileobj(r, into, 1 << 20)
            return None
        return r.read()


def _host(address):
    try:
        return f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
    except ValueError:
        return address


def _split_address(value):
    """(adresse, port) d'une saisie « 192.168.1.11 », « 192.168.1.11:9443 », « [fd00::1]:9443 » ou « https://… » ; port vide si absent."""
    value = re.sub(r"^https?://", "", value.strip()).split("/")[0]
    if value.count(":") == 1 or "]:" in value:
        value, _, port = value.rpartition(":")
    else:
        port = ""
    return value.strip("[]"), port


# --------------------------------------------------------------------------- paquet de configuration

def _tar_add(tar, path, arcname):
    st = os.lstat(path)
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        return                                          # sockets de gpg-agent, liens… : jamais répliqués
    info = tar.gettarinfo(path, arcname)
    info.mtime = int(st.st_mtime)
    if stat.S_ISREG(st.st_mode):
        with open(path, "rb") as f:
            tar.addfile(info, f)
    else:
        tar.addfile(info)


def _walk(root):
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            yield os.path.join(base, name)
        for name in dirs:
            yield os.path.join(base, name)


def bundle():
    """Paquet de configuration (tar.gz, déterministe) : etc/… et state/…, sans ce qui est propre à ce serveur."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(os.listdir(ETC)) if os.path.isdir(ETC) else []:
            if name in ETC_SKIP or name.endswith(".tmp"):
                continue
            path = os.path.join(ETC, name)
            _tar_add(tar, path, "etc/" + name)
            if os.path.isdir(path) and not os.path.islink(path):
                for item in _walk(path):
                    _tar_add(tar, item, "etc/" + os.path.relpath(item, ETC))
        for name in STATE_DIRS:
            path = os.path.join(STATE, name)
            if os.path.isdir(path) and not os.path.islink(path):
                _tar_add(tar, path, "state/" + name)
                for item in _walk(path):
                    if not item.endswith("~"):
                        _tar_add(tar, item, "state/" + os.path.relpath(item, STATE))
    return gzip.compress(raw.getvalue(), mtime=0)


def _owned(info):
    try:
        uid = pwd.getpwnam(info.uname).pw_uid if info.uname else info.uid
    except KeyError:
        uid = 0
    try:
        gid = grp.getgrnam(info.gname).gr_gid if info.gname else info.gid
    except KeyError:
        gid = 0
    return uid, gid


def apply_bundle(blob):
    """Installe un paquet de configuration ; renvoie les zones modifiées (« loco.conf », « tls », « secret »…)."""
    changed, seen = set(), {"etc": set(), "state": set()}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob)), mode="r") as tar:
        for info in tar.getmembers():
            name = info.name
            parts = name.split("/")
            if parts[0] not in ("etc", "state") or len(parts) < 2 or ".." in parts or name.startswith("/") \
                    or not (info.isreg() or info.isdir()):
                continue
            if parts[0] == "etc" and parts[1] in ETC_SKIP:
                continue
            if parts[0] == "state" and parts[1] not in STATE_DIRS:
                continue
            target = os.path.join(ETC if parts[0] == "etc" else STATE, *parts[1:])
            seen[parts[0]].add(target)
            uid, gid = _owned(info)
            if info.isdir():
                os.makedirs(target, exist_ok=True)
                os.chmod(target, info.mode & 0o7777)
                with contextlib.suppress(OSError):
                    os.chown(target, uid, gid)
                continue
            data = tar.extractfile(info).read()
            try:
                with open(target, "rb") as f:
                    same = f.read() == data
            except OSError:
                same = False
            st = os.stat(target) if os.path.exists(target) else None
            if same and st and (st.st_mode & 0o7777) == (info.mode & 0o7777) and (st.st_uid, st.st_gid) == (uid, gid):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".ha-tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.chmod(tmp, info.mode & 0o7777)
            with contextlib.suppress(OSError):
                os.chown(tmp, uid, gid)
            os.replace(tmp, target)
            if not same:
                changed.add(_area(parts))
    # Fichiers retirés chez le principal (certificat d'entreprise supprimé…) : retirés ici aussi.
    for root, kind in ((ETC, "etc"), *((os.path.join(STATE, d), "state") for d in STATE_DIRS)):
        if not os.path.isdir(root):
            continue
        for path in list(_walk(root)):
            rel = os.path.relpath(path, ETC if kind == "etc" else STATE).split(os.sep)
            if kind == "etc" and rel[0] in ETC_SKIP or path.endswith((".tmp", ".ha-tmp")) or path in seen[kind]:
                continue
            st = os.lstat(path)
            if stat.S_ISREG(st.st_mode):
                os.unlink(path)
                changed.add(_area([kind, *rel]))
    return changed


def _area(parts):
    if parts[0] == "state":
        return parts[1]
    return {"loco.conf": "loco.conf", "portal.secret": "secret", "tls": "tls"}.get(parts[1], parts[1])


# --------------------------------------------------------------------------- base

def snapshot_db(target):
    """Instantané cohérent de la base du portail (API de sauvegarde SQLite)."""
    src = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def apply_db(path):
    """Copie un instantané dans la base du portail par l'API de sauvegarde : verrous respectés, lecteurs servis."""
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    dst = sqlite3.connect(db_path(), timeout=60)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def db_stamp():
    """Empreinte de modification de la base (fichier et journal WAL) : sans changement, pas de nouvel instantané."""
    out = []
    for suffix in ("", "-wal"):
        with contextlib.suppress(OSError):
            st = os.stat(db_path() + suffix)
            out.append(f"{st.st_mtime_ns}:{st.st_size}")
    return "|".join(out)


# --------------------------------------------------------------------------- dépôts (différence de fichiers)

def manifest(root=None):
    """Relevé des dépôts : chemin relatif à DATA -> [type, taille, mtime_ns, mode, propriétaire, groupe, cible]."""
    root = root or DATA
    out = {}
    for area in AREAS:
        base = os.path.join(root, area)
        if not os.path.isdir(base) or os.path.islink(base):
            continue
        for current, dirs, files in os.walk(base):
            for name in dirs + files:
                path = os.path.join(current, name)
                st = os.lstat(path)
                rel = os.path.relpath(path, root)
                owner = _name(pwd.getpwuid, st.st_uid)
                group = _name(grp.getgrgid, st.st_gid)
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = ["l", 0, 0, 0, owner, group, os.readlink(path)]
                elif stat.S_ISDIR(st.st_mode):
                    out[rel] = ["d", 0, 0, st.st_mode & 0o7777, owner, group, ""]
                elif stat.S_ISREG(st.st_mode):
                    out[rel] = ["f", st.st_size, st.st_mtime_ns, st.st_mode & 0o7777, owner, group, ""]
    return out


def _name(lookup, ident):
    try:
        return lookup(ident)[0]
    except KeyError:
        return str(ident)


def _ids(owner, group):
    try:
        uid = pwd.getpwnam(owner).pw_uid
    except KeyError:
        uid = int(owner) if owner.isdigit() else 0
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        gid = int(group) if group.isdigit() else 0
    return uid, gid


def safe_rel(rel):
    """Chemin relatif d'un dépôt, sans « .. » ni chemin absolu : premier élément dans AREAS."""
    if not isinstance(rel, str) or not PATH_RE.fullmatch(rel) or rel.startswith("/") or "//" in rel:
        return None
    parts = rel.split("/")
    if parts[0] not in AREAS or any(p in ("", ".", "..") for p in parts):
        return None
    return rel


def sync_files(remote, fetch, root=None, log=print):
    """Rend les dépôts locaux identiques au relevé distant ; fetch(rel, fichier) télécharge un fichier.
    Renvoie (fichiers copiés, octets, éléments supprimés)."""
    root = root or DATA
    local = manifest(root)
    copied = size = removed = 0
    # Dossiers d'abord (du plus court au plus long), puis fichiers et liens.
    for rel in sorted((r for r, e in remote.items() if e[0] == "d" and safe_rel(r)), key=len):
        path = os.path.join(root, rel)
        if os.path.islink(path) or (os.path.exists(path) and not os.path.isdir(path)):
            _remove(path)
        os.makedirs(path, exist_ok=True)
        entry = remote[rel]
        os.chmod(path, entry[3])
        with contextlib.suppress(OSError):
            os.chown(path, *_ids(entry[4], entry[5]))
    for rel, entry in sorted(remote.items()):
        if entry[0] == "d" or not safe_rel(rel):
            continue
        path = os.path.join(root, rel)
        mine = local.get(rel)
        if entry[0] == "l":
            if mine != entry:
                _remove(path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(entry[6], path)
            continue
        if mine and mine[0] == "f" and mine[1] == entry[1] and mine[2] == entry[2]:
            if mine[3:6] != entry[3:6]:
                os.chmod(path, entry[3])
                with contextlib.suppress(OSError):
                    os.chown(path, *_ids(entry[4], entry[5]))
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".ha-")
        try:
            with os.fdopen(fd, "wb") as f:
                fetch(rel, f)
            os.chmod(tmp, entry[3])
            with contextlib.suppress(OSError):
                os.chown(tmp, *_ids(entry[4], entry[5]))
            os.utime(tmp, ns=(entry[2], entry[2]))
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        copied += 1
        size += entry[1]
    for rel in sorted((r for r in local if r not in remote), key=len, reverse=True):
        _remove(os.path.join(root, rel))
        removed += 1
    return copied, size, removed


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


# --------------------------------------------------------------------------- cycle du fil de réplication

def _out(name):
    return os.path.join(out_dir(), name)


def _prepare_out():
    os.makedirs(out_dir(), exist_ok=True)
    os.chmod(out_dir(), 0o750)
    with contextlib.suppress(KeyError, PermissionError):
        os.chown(out_dir(), 0, grp.getgrnam("loco").gr_gid)


def produce(conf, secret, version, cache):
    """Principal : prépare ha-out/ (paquet chiffré, base chiffrée, relevé des dépôts) et l'état présenté au pair."""
    _prepare_out()
    key = derive(secret, "enc")
    state = read_json(_out("state.json"), {}) or {}
    now = time.time()
    blob = bundle()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != state.get("bundle") or not os.path.exists(_out("bundle.bin")):
        _write(_out("bundle.bin"), seal(blob, key), 0o640, "loco")
        state.update(bundle=digest, bundle_at=now)
    stamp = db_stamp()
    if (stamp != cache.get("db_stamp") or not os.path.exists(_out("db.bin"))) and now - cache.get("db_time", 0) >= DB_EVERY:
        with tempfile.TemporaryDirectory(dir=out_dir()) as tmp:
            snap = os.path.join(tmp, "portal.db")
            snapshot_db(snap)
            with open(snap, "rb") as f:
                raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != state.get("db"):
            _write(_out("db.bin"), seal(gzip.compress(raw, 6, mtime=0), key), 0o640, "loco")
            state["db"] = digest
        state["db_at"] = now                            # données à jour de cet instant
        cache.update(db_stamp=stamp, db_time=now)
    if now - cache.get("files_time", 0) >= FILES_EVERY or not os.path.exists(_out("manifest.json.gz")):
        files = manifest()
        raw = json.dumps(files, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != state.get("files"):
            _write(_out("manifest.json.gz"), gzip.compress(raw, 6, mtime=0), 0o640, "loco")
            state["files"] = digest
        state.update(files_count=sum(1 for e in files.values() if e[0] == "f"),
                     files_bytes=sum(e[1] for e in files.values() if e[0] == "f"))
        cache["files_time"] = now
    state.update(role="primary", version=version, epoch=conf.get("epoch", 0), address=conf["self"]["address"],
                 name=conf["self"].get("name", ""), at=now)
    _write(_out("state.json"), json.dumps(state), 0o640, "loco")
    return state


def pull(conf, secret, version, hooks, log=print):
    """Secours : tire du principal ce qui a changé et l'applique. hooks : reconfigure / reload_nginx / restart_portal."""
    peer, token, key = conf["peer"]["address"], auth_token(secret), derive(secret, "enc")
    remote = json.loads(call(peer, "/ha/api/state", token, timeout=10))
    if remote.get("role") != "primary":
        return remote, "le pair n'est pas principal"
    if remote.get("version") != version:
        return remote, f"versions différentes (principal {remote.get('version')}, ce serveur {version}) : mettez les deux à jour"
    applied = read_json(applied_path(), {}) or {}
    if remote.get("bundle") and remote["bundle"] != applied.get("bundle"):
        changed = apply_bundle(unseal(call(peer, "/ha/api/bundle", token), key))
        if "tls" in changed:
            shutil.copyfile(os.path.join(ETC, "tls", "ca.crt"), ca_path())
            hooks["reload_nginx"]()
        if "secret" in changed:
            hooks["restart_portal"]()
        if "loco.conf" in changed:
            hooks["reconfigure"]()
        applied.update(bundle=remote["bundle"], bundle_at=time.time())
        if changed:
            log("Configuration du principal appliquée : " + ", ".join(sorted(changed)) + ".")
    if remote.get("db") and remote["db"] != applied.get("db"):
        with tempfile.TemporaryDirectory(dir=AGENT_DIR) as tmp:
            sealed = os.path.join(tmp, "db.bin")
            with open(sealed, "wb") as f:
                call(peer, "/ha/api/db", token, into=f, timeout=600)
            with open(sealed, "rb") as f:
                raw = gzip.decompress(unseal(f.read(), key))
            snap = os.path.join(tmp, "portal.db")
            with open(snap, "wb") as f:
                f.write(raw)
            apply_db(snap)
        applied["db"] = remote["db"]
    if remote.get("db_at"):
        applied["db_at"] = remote["db_at"]
    if remote.get("files") and remote["files"] != applied.get("files"):
        files = json.loads(gzip.decompress(call(peer, "/ha/api/manifest", token, timeout=120)))

        def fetch(rel, f):
            call(peer, "/ha/api/file?p=" + urllib.request.quote(rel, safe="/"), token, into=f, timeout=1800)
        copied, size, removed = sync_files(files, fetch, log=log)
        applied.update(files=remote["files"], files_at=time.time())
        if copied or removed:
            log(f"Dépôts : fichiers copiés : {copied} ({size} octets), éléments retirés : {removed}.")
    applied["pulled_at"] = time.time()
    _write(applied_path(), json.dumps(applied), 0o644)
    return remote, ""


def standby_state(conf, version):
    """Secours : état présenté au principal (ce qui est appliqué, et depuis quand)."""
    _prepare_out()
    applied = read_json(applied_path(), {}) or {}
    state = {"role": conf.get("role", "standby"), "version": version, "epoch": conf.get("epoch", 0),
             "address": conf["self"]["address"], "name": conf["self"].get("name", ""), "at": time.time(),
             "applied_db_at": applied.get("db_at"), "pulled_at": applied.get("pulled_at")}
    _write(_out("state.json"), json.dumps(state), 0o640, "loco")
    for name in ("bundle.bin", "db.bin", "manifest.json.gz"):     # rien à servir : ce serveur n'est pas la référence
        with contextlib.suppress(OSError):
            os.unlink(_out(name))
    return state


def cycle(version, hooks, cache, log=print):
    """Un passage du fil : produire (principal) ou tirer (secours), interroger le pair, publier l'état."""
    conf = node()
    if not conf.get("vip"):
        return
    secret = read_secret()
    if not secret:
        return
    if os.path.exists(yield_flag()) and time.time() - os.path.getmtime(yield_flag()) > 90:
        os.unlink(yield_flag())                        # bascule volontaire terminée : de nouveau candidat
    vip_held = holds(conf["vip"])
    info = {"role": conf.get("role"), "vip": conf["vip"], "vip_held": vip_held, "self": conf["self"],
            "peer": dict(conf.get("peer") or {}), "keepalived": _keepalived_running(), "error": ""}
    try:
        if conf.get("role") == "primary":
            produced = produce(conf, secret, version, cache)
            info["replication"] = {"db_at": produced.get("db_at"), "files_count": produced.get("files_count"),
                                   "files_bytes": produced.get("files_bytes")}
        else:
            standby_state(conf, version)
    except Exception as e:                              # l'état publié dit ce qui ne va pas, le fil continue
        info["error"] = f"préparation : {e}"[:300]
    peer = conf.get("peer") or {}
    if peer.get("address"):
        try:
            if conf.get("role") == "primary":
                remote = json.loads(call(peer["address"], "/ha/api/state", auth_token(secret), timeout=8))
                pause = ""
            else:
                remote, pause = pull(conf, secret, version, hooks, log)
                applied = read_json(applied_path(), {}) or {}
                info["replication"] = {"db_at": applied.get("db_at"), "pulled_at": applied.get("pulled_at"),
                                       "paused": pause}
            info["peer"].update(reachable=True, role=remote.get("role"), version=remote.get("version"),
                                epoch=remote.get("epoch"), seen=time.time(),
                                applied_db_at=remote.get("applied_db_at"), pulled_at=remote.get("pulled_at"))
        except (OSError, ValueError, urllib.error.URLError) as e:
            info["peer"].update(reachable=False, error=str(getattr(e, "reason", e))[:200])
    publish(**info)


def _keepalived_running():
    p = subprocess.run(["pidof", "keepalived"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0


# --------------------------------------------------------------------------- keepalived : santé et rôle

def check():
    """Script de contrôle de keepalived : 0 = ce serveur peut porter l'IP virtuelle."""
    if os.path.exists(maintenance_flag()) and time.time() - os.path.getmtime(maintenance_flag()) < 1800:
        return 0                                        # mise à jour en cours : le portail redémarre, pas une panne
    if os.path.exists(yield_flag()):
        return 1                                        # bascule volontaire vers le pair
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(f"https://127.0.0.1:{https_port()}/healthz", context=ctx, timeout=3) as r:
            return 0 if r.status == 200 else 1
    except (OSError, urllib.error.URLError):
        return 1


def notify(args, log=print):
    """Appelé par keepalived : « INSTANCE loco MASTER 150 ». MASTER → principal ; BACKUP, FAULT, STOP → secours."""
    state = args[2] if len(args) >= 3 else ""
    if state == "MASTER":
        set_role("primary", log)
    elif state in ("BACKUP", "FAULT", "STOP"):
        set_role("standby", log)
    event(f"keepalived : {state.lower() or '?'}")


@contextlib.contextmanager
def maintenance():
    """Le temps d'une mise à jour ou d'une reconfiguration : le contrôle de santé ne fait pas basculer."""
    os.makedirs(RUN, exist_ok=True)
    with open(maintenance_flag(), "w") as f:
        f.write(str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(maintenance_flag())


# --------------------------------------------------------------------------- opérations (jobs de loco-agent)

def _python():
    return sys.executable or "/usr/bin/python3"


def _check_vip(vip, address, log):
    try:
        iface = ipaddress.ip_interface(vip if "/" in vip else vip + "/24")
    except ValueError:
        raise ValueError("IP virtuelle invalide (ex. 192.168.1.20/24)")
    if str(iface.ip) == address:
        raise ValueError("l'IP virtuelle doit être différente de l'adresse de ce serveur")
    if ipaddress.ip_address(address) not in iface.network:
        raise ValueError(f"l'IP virtuelle doit être dans le réseau de ce serveur ({address})")
    if subprocess.run(["ping", "-c", "1", "-W", "1", str(iface.ip)], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0 and not holds(str(iface)):
        raise ValueError(f"{iface.ip} répond déjà sur le réseau : choisissez une adresse libre")
    return iface


def enable(params, log, admin):
    """Principal : IP virtuelle, secret, code d'appairage, keepalived, puis adresse de loco = IP virtuelle."""
    if node().get("vip"):
        raise ValueError("haute disponibilité déjà configurée sur ce serveur")
    address = admin.read_conf().get("HOST", "")
    iface_name, _prefix = interface_of(address)
    if not iface_name:
        raise ValueError(f"adresse de ce serveur introuvable sur ses interfaces : {address}")
    vip = _check_vip(str(params.get("vip", "")).strip(), address, log)
    if not install_keepalived(log):
        raise ValueError("keepalived n'a pas pu être installé")
    secret = "S-" + secrets.token_urlsafe(32)
    conf = {"vip": str(vip), "interface": iface_name, "vrid": int(vip.ip.packed[-1]) or 51,
            "auth_pass": secrets.token_hex(4), "role": "primary", "epoch": 1,
            "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "self": {"address": address, "name": socket.gethostname().split(".")[0], "priority": 150}, "peer": None}
    save_secret(secret)
    save_node(conf)
    shutil.copyfile(os.path.join(ETC, "tls", "ca.crt"), ca_path())
    new_join_code(conf, secret)
    log(f"IP virtuelle {vip} sur {iface_name} (VRRP {conf['vrid']}) : démarrage de keepalived.")
    apply_keepalived(conf, _python(), admin.HERE, log)
    for _ in range(30):
        if holds(str(vip)):
            break
        time.sleep(1)
    else:
        raise ValueError("l'IP virtuelle n'est pas apparue sur ce serveur (voir le journal de keepalived)")
    log(f"Ce serveur porte {vip.ip}. Adresse de loco : {vip.ip} (portail, dépôts, agents).")
    with maintenance():
        if admin._install(admin.read_conf().get("SOURCE_DIR", ""), ["--host", str(vip.ip)], log) != 0:
            raise ValueError("reconfiguration de loco sur l'IP virtuelle impossible (voir le journal)")
        cluster_cert(conf, admin, log)
    event(f"haute disponibilité activée ({vip})")
    return True


def new_join_code(conf, secret):
    """Code d'appairage à usage unique : le portail ne connaît que lui et son empreinte, jamais le secret."""
    code = "LHA-" + secrets.token_urlsafe(24)
    _write(join_code_path(), code + "\n", 0o640, "loco")
    _write(join_verifier_path(), verifier(auth_token(code)) + "\n", 0o640, "loco")
    with open(os.path.join(ETC, "tls", "ca.crt"), encoding="utf-8") as f:
        ca = f.read()
    package = {"secret": secret, "vip": conf["vip"], "vrid": conf["vrid"], "auth_pass": conf["auth_pass"],
               "primary": conf["self"], "ca": ca}
    _prepare_out()
    _write(_out("join.bin"), seal(json.dumps(package).encode(), derive(code, "join")), 0o640, "loco")
    return code


def cluster_addresses(conf):
    """Adresses des deux serveurs, à couvrir en plus de l'IP virtuelle."""
    return [conf["self"]["address"]] + ([conf["peer"]["address"]] if conf.get("peer") else [])


def cluster_cert(conf, admin, log):
    """Certificat de loco pour l'IP virtuelle et les deux serveurs (signé par l'autorité de la grappe)."""
    if admin.tls_mode() == "enterprise" or os.path.exists(os.path.join(ETC, "tls", "custom")):
        log("Certificat d'entreprise ou fourni : ajoutez-y l'IP virtuelle et les deux serveurs vous-même.")
        return
    with tempfile.TemporaryDirectory(dir=os.path.join(ETC, "tls"), prefix=".ha-") as tmp:
        key, crt, san = admin._loco_cert(tmp, log, extra=cluster_addresses(conf))
        admin._swap_tls({"server.key": key, "server.crt": crt}, log)
    log("Certificat : " + ", ".join(s.split(":", 1)[1] for s in san) + ".")


def add_peer(params, log, admin):
    """Principal : enregistre le secours qui vient de rejoindre (adresse), keepalived en unicast vers lui."""
    conf = node()
    address = str(params.get("address", "")).strip()
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise ValueError("adresse du secours invalide")
    name = str(params.get("name", ""))[:63]
    conf["peer"] = {"address": address, "name": name if NAME_RE.fullmatch(name) else "", "priority": 100}
    save_node(conf)
    for path in (join_code_path(), join_verifier_path(), _out("join.bin")):    # code à usage unique
        with contextlib.suppress(OSError):
            os.unlink(path)
    apply_keepalived(conf, _python(), admin.HERE, log)
    cluster_cert(conf, admin, log)
    event(f"secours {address} enregistré")
    log(f"Serveur de secours {conf['peer']['name'] or address} ({address}) enregistré.")
    return True


def join(params, log, admin):
    """Secours : appairage avec le code, copie complète du principal, puis keepalived."""
    if node().get("vip"):
        raise ValueError("haute disponibilité déjà configurée sur ce serveur")
    primary, port = _split_address(str(params.get("primary", "")))
    code = str(params.get("code", "")).strip()
    if not CODE_RE.fullmatch(code):
        raise ValueError("code d'appairage invalide")
    try:
        ipaddress.ip_address(primary)
    except ValueError:
        raise ValueError("adresse IP du principal attendue")
    address = admin.read_conf().get("HOST", "")
    iface_name, _prefix = interface_of(address)
    if not iface_name:
        raise ValueError(f"adresse de ce serveur introuvable sur ses interfaces : {address}")
    version = admin.current_version()
    log(f"Appairage avec {primary}…")
    try:
        blob = call(primary, "/ha/api/join", auth_token(code), {"address": address, "version": version,
                                                                  "name": socket.gethostname().split(".")[0]},
                    pinned=False, timeout=30, port=port)
    except urllib.error.HTTPError as e:
        raise ValueError({403: "code refusé (déjà utilisé, ou principal sans appairage en attente)",
                          409: "versions de loco différentes : mettez les deux serveurs à jour"}.get(e.code, f"HTTP {e.code}"))
    except (OSError, urllib.error.URLError) as e:
        raise ValueError(f"principal injoignable sur le port {port or https_port()} (autre port : adresse:port) : {getattr(e, 'reason', e)}")
    package = json.loads(unseal(blob, derive(code, "join")))
    vip = ipaddress.ip_interface(package["vip"])
    if ipaddress.ip_address(address) not in vip.network:
        raise ValueError(f"ce serveur ({address}) n'est pas dans le réseau de l'IP virtuelle {vip}")
    if not install_keepalived(log):
        raise ValueError("keepalived n'a pas pu être installé")
    conf = {"vip": package["vip"], "interface": iface_name, "vrid": package["vrid"], "auth_pass": package["auth_pass"],
            "role": "standby", "epoch": 0, "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "self": {"address": address, "name": socket.gethostname().split(".")[0], "priority": 100},
            "peer": {"address": package["primary"]["address"], "name": package["primary"].get("name", ""), "priority": 150},
            "peer_port": port or https_port()}
    save_secret(package["secret"])
    save_node(conf)
    _write(ca_path(), package["ca"], 0o644)
    log("Copie du principal (configuration, base, dépôts)…")
    reconfigure = []
    hooks = {"reconfigure": lambda: reconfigure.append(1), "reload_nginx": lambda: None, "restart_portal": lambda: None}
    with maintenance():
        _remote, pause = pull(conf, package["secret"], version, hooks, log)
        if pause:
            raise ValueError(pause)
        log("Reconfiguration de loco avec la configuration du principal…")
        if admin._install(admin.read_conf().get("SOURCE_DIR", ""), [], log) != 0:
            raise ValueError("reconfiguration impossible (voir le journal)")
    apply_keepalived(conf, _python(), admin.HERE, log)
    event(f"secours de {conf['peer']['address']}")
    log(f"Ce serveur est le secours de {conf['peer']['name'] or conf['peer']['address']} ; il prendra l'IP "
        f"{vip.ip} si le principal tombe.")
    return True


def failover(log):
    """Principal : cède l'IP virtuelle au secours (contrôle de santé en échec 90 s, nopreempt ensuite)."""
    conf = node()
    if conf.get("role") != "primary" or not (conf.get("peer") or {}).get("address"):
        raise ValueError("bascule possible seulement depuis le principal, avec un secours")
    os.makedirs(RUN, exist_ok=True)
    with open(yield_flag(), "w") as f:
        f.write("1")
    for _ in range(30):
        if not holds(conf["vip"]):
            break
        time.sleep(1)
    else:
        os.unlink(yield_flag())
        raise ValueError("le secours n'a pas pris l'IP virtuelle (est-il en marche ?)")
    event("bascule volontaire vers le secours")
    log("IP virtuelle cédée au secours ; ce serveur devient le secours.")
    return True


def disable(log, admin):
    """Quitte la haute disponibilité : keepalived arrêté, configuration HA retirée. Le principal reprend son adresse."""
    conf = node()
    if not conf:
        raise ValueError("haute disponibilité non configurée")
    peer = conf.get("peer") or {}
    if conf.get("role") == "primary" and peer.get("address"):
        secret = read_secret()                          # le secours quitte d'abord, sinon il garderait l'IP virtuelle
        if secret:
            log(f"Dissolution de la grappe : le serveur de secours {peer.get('name') or peer['address']} quitte…")
            with contextlib.suppress(OSError, ValueError, urllib.error.URLError):
                call(peer["address"], "/ha/api/leave", auth_token(secret), {}, timeout=15)
            for _ in range(30):                         # attendre que son portail s'arrête de répondre
                try:
                    call(peer["address"], "/ha/api/state", auth_token(secret), timeout=5)
                    time.sleep(1)
                except (OSError, ValueError, urllib.error.URLError):
                    break
    service("keepalived", "stop", log)
    service("keepalived", "disable", log)
    with contextlib.suppress(OSError):
        os.unlink(KEEPALIVED_CONF)
    shutil.rmtree(ha_dir(), ignore_errors=True)
    shutil.rmtree(out_dir(), ignore_errors=True)
    for path in (applied_path(), status_path()):
        with contextlib.suppress(OSError):
            os.unlink(path)
    # Reprendre son adresse de nœud : en haute disponibilité, loco.conf portait l'IP virtuelle sur les deux serveurs
    # (le secours l'hérite par réplication). En quittant, chacun réannonce sa propre adresse.
    own = conf.get("self", {}).get("address", "")
    if own and own != admin.read_conf().get("HOST", ""):
        with maintenance():
            admin._install(admin.read_conf().get("SOURCE_DIR", ""), ["--host", own], log)
    if conf.get("role") == "primary":
        log(f"Adresse de loco : {own}. Les postes qui utilisaient l'IP virtuelle suivent par l'adresse annoncée ; "
            "mettez à jour les sources des dépôts si besoin.")
    else:
        log(f"Ce serveur n'est plus le secours : copie indépendante du principal, sur son adresse {own} "
            "(arrêtez-le ou réinstallez-le).")
    return True
