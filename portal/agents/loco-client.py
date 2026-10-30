#!/usr/bin/env python3
"""loco-client @VERSION@ : agent loco des postes Linux (Debian, Ubuntu, Rocky Linux, AlmaLinux, RHEL).

Lancé toutes les deux minutes par loco-client.timer (root). Contacte le serveur loco avec son identifiant et son
jeton (indépendants de l'adresse IP du poste), envoie l'inventaire des mises à jour et exécute les actions
demandées depuis le portail : inventaire, mise à jour, redémarrage, retrait de l'agent. Terminal interactif séparé, réservé aux administrateurs du portail.
  loco-client run                         contact du serveur (service)
  loco-client enroll --server URL --key CLÉ
  loco-client status
  loco-client remove                      retrait local (le poste reste à oublier dans le portail)
Bibliothèque standard de Python uniquement ; autorité du serveur épinglée (/etc/loco-client/ca.crt).
"""

import base64
import ctypes
import datetime
import fcntl
import http.client
import hashlib
import json
import os
import platform
import pty
import pwd
import re
import shutil
import socket
import signal
import stat
import struct
import ssl
import subprocess
import sys
import tempfile
import termios
import time
import zlib
import urllib.error
import urllib.request
import urllib.parse
import uuid

VERSION = "@VERSION@"
CONSOLE_LIB = "@CONSOLE_LIB@"
ETC = "/etc/loco-client"
CONFIG = os.path.join(ETC, "client.json")
CA = os.path.join(ETC, "ca.crt")
STATE = "/var/lib/loco-client/state.json"
LOCK = "/run/loco-client.lock"
PROGRAM = "/usr/local/lib/loco-client/loco-client.py"
UNITS = ("/etc/systemd/system/loco-client.service", "/etc/systemd/system/loco-client.timer",
         "/etc/systemd/system/loco-client-remote.service")
OUTPUT_LIMIT = 60000
ENV = dict(os.environ, LC_ALL="C", LANG="C", DEBIAN_FRONTEND="noninteractive")


def _system_lang():
    """Langue du poste : variables d'environnement, sinon le fichier de locale du système (service sans LANG)."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            return val
    for path in ("/etc/default/locale", "/etc/locale.conf"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    for var in ("LC_ALL=", "LC_MESSAGES=", "LANG="):
                        if line.strip().startswith(var):
                            return line.strip()[len(var):].strip().strip('"')
        except OSError:
            pass
    return ""


def _lang():
    value = _system_lang().lower()
    if value.startswith("fr"):
        return "fr"
    if value.startswith("es"):
        return "es"
    return "en"


LANG = _lang()


def t(fr, en, es=None):
    """Texte vu par l'admin ou l'utilisateur du poste, dans la langue du poste (anglais par défaut)."""
    if LANG == "fr":
        return fr
    if LANG == "es":
        return es if es is not None else en
    return en



def log(text):
    print(f"loco-client : {text}", flush=True)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- serveur

def context():
    ctx = ssl.create_default_context(cafile=CA if os.path.isfile(CA) else None)
    if hasattr(ssl, "TLSVersion"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    else:  # Python 3.6 livré avec Rocky / RHEL 8
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    return ctx


def call(server, path, data, token=None, timeout=60):
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "User-Agent": f"loco-client/{VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(server.rstrip("/") + path, data=body, headers=headers, method="POST")
    # Le serveur loco est sur le réseau local : pas de proxy, même si le poste en utilise un.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context()))
    with opener.open(req, timeout=timeout) as r:
        return json.load(r)


SERVER_RE = re.compile(r"^https://[A-Za-z0-9.\[\]:-]+(:[0-9]{1,5})?$")


def known_servers(conf):
    """Adresse habituelle du serveur, puis celles qu'il a annoncées (changement d'adresse prévu)."""
    out = []
    for url in [conf.get("server", "")] + list(conf.get("servers") or []):
        url = url.rstrip("/") if isinstance(url, str) else ""
        if SERVER_RE.match(url) and url not in out:
            out.append(url)
    return out[:8]


def checkin(conf, data):
    """Contact du serveur. Si son adresse habituelle ne répond plus (réseau, certificat), les adresses annoncées
    sont essayées ; la première qui répond avec un certificat de l'autorité loco épinglée devient l'adresse du poste."""
    data["servers"] = known_servers(conf)
    failure = None
    for server in data["servers"]:
        try:
            answer = call(server, "/agent/api/checkin", data, conf["token"])
        except urllib.error.HTTPError:
            raise                           # le serveur a répondu : ce n'est pas une question d'adresse
        except (OSError, ValueError) as e:
            failure = failure or e
            continue
        announced = [u.rstrip("/") for u in answer.get("servers") or [] if isinstance(u, str) and SERVER_RE.match(u.rstrip("/"))][:8]
        if server != conf["server"]:
            log(t(f"serveur joint à sa nouvelle adresse : {server} (au lieu de {conf['server']})", f"server reached at its new address: {server} (instead of {conf['server']})", f"servidor contactado en su nueva dirección: {server} (en lugar de {conf['server']})"))
        if server != conf["server"] or (announced and announced != conf.get("servers")):
            conf["server"] = server
            if announced:
                conf["servers"] = announced
            save(CONFIG, conf)
        return answer
    raise failure or OSError(t("aucune adresse de serveur connue", "no known server address", "ninguna dirección de servidor conocida"))


def download(server, path, timeout=120):
    req = urllib.request.Request(server.rstrip("/") + path, headers={"User-Agent": f"loco-client/{VERSION}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context()))
    with opener.open(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- poste

def os_release():
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                info[k] = v.strip('"')
    except OSError:
        pass
    return info


def family():
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf") or shutil.which("yum"):
        return "rhel"
    return ""


def addresses():
    out = []
    try:
        text = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=10).stdout
        for line in text.splitlines():
            fields = line.split()
            if len(fields) > 3 and fields[2] in ("inet", "inet6"):
                out.append(fields[3].split("/")[0])
    except (OSError, subprocess.SubprocessError):
        pass
    return out[:16]


def identity():
    rel = os_release()
    return {"hostname": socket.gethostname(), "agent_version": VERSION, "addresses": addresses(), "trust": trust_sha(),
            "os": {"family": family(), "name": rel.get("PRETTY_NAME") or platform.platform()}}


def trust_sha():
    """Empreinte du faisceau d'autorités épinglé (vide : magasin du système)."""
    try:
        with open(CA, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def apply_trust(answer):
    """Faisceau d'autorités envoyé par le serveur au fil du canal déjà épinglé (autorité loco + autorité d'entreprise
    éventuelle) : remplace ca.crt s'il a changé. Agent installé sans autorité épinglée : rien à faire."""
    bundle = answer.get("trust")
    if not isinstance(bundle, dict) or not os.path.isfile(CA):
        return
    pem, sha = bundle.get("pem") or "", (bundle.get("sha256") or "").lower()
    if not pem or "-----BEGIN CERTIFICATE-----" not in pem or sha == trust_sha():
        return
    raw = pem.encode()
    if hashlib.sha256(raw).hexdigest() != sha:
        return
    try:
        ssl.create_default_context(cadata=pem)          # faisceau lisible par OpenSSL avant de remplacer l'ancien
    except (ssl.SSLError, ValueError) as e:
        log(t(f"faisceau d'autorités refusé : {e}", f"authority bundle refused: {e}", f"conjunto de autoridades rechazado: {e}"))
        return
    tmp = CA + ".new"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.chmod(tmp, 0o644)
    os.replace(tmp, CA)
    log(t("autorités de confiance mises à jour", "trusted authorities updated", "autoridades de confianza actualizadas"))


def run(cmd, timeout=7200):
    """(code, sortie) ; la sortie est tronquée au début pour garder la fin, la plus utile."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           env=ENV, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable\n"
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else t("délai dépassé\n", "timed out\n", "tiempo agotado\n")


def booted_since(timestamp):
    try:
        with open("/proc/uptime") as f:
            return time.time() - float(f.read().split()[0]) > timestamp
    except (OSError, ValueError, IndexError):
        return False


def flatpak_updates():
    if not shutil.which("flatpak"):
        return 0
    code, out = run(["flatpak", "remote-ls", "--updates", "--system", "--columns=application"], timeout=600)
    return len([line for line in out.splitlines() if line.strip()]) if code == 0 else 0


def reboot_required():
    if os.path.exists("/var/run/reboot-required"):
        return True
    for cmd in (["needs-restarting", "-r"], ["dnf", "needs-restarting", "-r"]):
        if shutil.which(cmd[0]):
            code, _ = run(cmd, timeout=300)
            if code in (0, 1):
                return code == 1
    return False


def installed_packages(fam):
    """Paquets installés (nom, version, architecture, taille en octets) ; « manual » : installé à la demande d'un
    administrateur, les autres n'étant que des dépendances. Commandes locales seulement, sans accès réseau."""
    packages = []
    if fam == "debian":
        code, out = run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\t${Installed-Size}\n"],
                        timeout=300)
        if code != 0:
            return []
        mcode, mout = run(["apt-mark", "showmanual"], timeout=300)
        manual = set(mout.split()) if mcode == 0 else set()
        for line in out.splitlines():
            f = line.split("\t")
            if len(f) < 5 or not f[3].startswith("ii"):
                continue
            packages.append({"name": f[0], "version": f[1], "arch": f[2], "manual": f[0] in manual or f"{f[0]}:{f[2]}" in manual,
                             "size": int(f[4]) * 1024 if f[4].strip().isdigit() else 0})
    elif fam == "rhel":
        code, out = run(["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCHNUM}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{SIZE}\n"], timeout=300)
        if code != 0:
            return []
        tool = "dnf" if shutil.which("dnf") else "yum"
        mcode, mout = run([tool, "-q", "-C", "--disablerepo=*", "repoquery", "--userinstalled", "--qf", "%{name}\n"], timeout=300)
        manual = set(mout.split()) if mcode == 0 else set()
        for line in out.splitlines():
            f = line.split("\t")
            if len(f) < 5 or f[0] == "gpg-pubkey":
                continue
            packages.append({"name": f[0], "version": f[2] if f[1] in ("", "0", "(none)") else f"{f[1]}:{f[2]}", "arch": f[3],
                             "manual": f[0] in manual, "size": int(f[4]) if f[4].strip().isdigit() else 0})
    return packages[:10000]


def installed_flatpaks():
    """Applications Flatpak installées pour tout le système (identifiant, nom, version, branche, dépôt d'origine)."""
    if not shutil.which("flatpak"):
        return []
    code, out = run(["flatpak", "list", "--app", "--system", "--columns=application,name,version,branch,origin"], timeout=300)
    if code != 0:
        return []
    apps = []
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) >= 5 and "." in f[0]:
            apps.append({"id": f[0], "name": f[1], "version": f[2], "branch": f[3], "origin": f[4]})
    return apps[:500]


def system_info():
    """Caractéristiques du poste : noyau, processeur, mémoire, disque racine, démarrage et virtualisation."""
    info = {"kernel": platform.release(), "arch": platform.machine(), "cores": os.cpu_count() or 0}
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith(("model name", "Hardware", "cpu model")):   # x86, ARM, autres architectures
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, value = line.partition(":")
                if value.split():
                    mem[key] = int(value.split()[0]) * 1024
        info["memory"], info["memory_available"] = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    except (OSError, ValueError):
        pass
    try:
        st = os.statvfs("/")
        info["disk"], info["disk_free"] = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    except OSError:
        pass
    try:
        with open("/proc/uptime") as f:
            info["boot"] = int(time.time() - float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    if shutil.which("systemd-detect-virt"):
        code, out = run(["systemd-detect-virt"], timeout=30)
        info["virtualization"] = out.strip().splitlines()[0] if code == 0 and out.strip() else "none"
    return info


# Systèmes de fichiers locaux supervisés (les pseudo-systèmes, réseaux et images en lecture seule sont ignorés).
LOCAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk"}


def _cpu_times():
    with open("/proc/stat") as f:
        values = [int(v) for v in f.readline().split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)          # idle + iowait
    return sum(values[:8]), idle


def _net_bytes():
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, _, rest = line.partition(":")
            fields = rest.split()
            if name.strip() != "lo" and len(fields) >= 9:
                rx, tx = rx + int(fields[0]), tx + int(fields[8])
    return rx, tx


def metrics(state):
    """Relevé de supervision joint à chaque contact : processeur et réseau en moyenne depuis le contact précédent
    (relevés gardés dans l'état), mémoire, swap, disques locaux, charge et durée depuis le démarrage."""
    out, now = {"cores": os.cpu_count() or 0}, time.time()
    previous = state.get("metrics_sample") or {}
    sample = {"at": now}
    try:
        total, idle = _cpu_times()
        sample.update(cpu_total=total, cpu_idle=idle)
        spent = total - previous.get("cpu_total", total)
        if spent > 0 and now - previous.get("at", 0) < 1800:
            out["cpu"] = round(100 * (1 - (idle - previous.get("cpu_idle", idle)) / spent), 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        rx, tx = _net_bytes()
        sample.update(net_rx=rx, net_tx=tx)
        elapsed = now - previous.get("at", now)
        if 0 < elapsed < 1800 and rx >= previous.get("net_rx", rx) and tx >= previous.get("net_tx", tx):
            out["net_rx"] = int((rx - previous["net_rx"]) / elapsed)
            out["net_tx"] = int((tx - previous["net_tx"]) / elapsed)
    except (OSError, ValueError, KeyError):
        pass
    state["metrics_sample"] = sample
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, value = line.partition(":")
                if value.split():
                    mem[key] = int(value.split()[0]) * 1024
        out["mem_total"] = mem.get("MemTotal", 0)
        out["mem_used"] = max(0, mem.get("MemTotal", 0) - mem.get("MemAvailable", mem.get("MemFree", 0)))
        out["swap_total"] = mem.get("SwapTotal", 0)
        out["swap_used"] = max(0, mem.get("SwapTotal", 0) - mem.get("SwapFree", 0))
    except (OSError, ValueError):
        pass
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        pass
    try:
        with open("/proc/uptime") as f:
            out["uptime"] = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    disks, seen = [], set()
    try:
        with open("/proc/mounts") as f:
            mounts = [line.split() for line in f]
    except OSError:
        mounts = []
    # Un même périphérique monté plusieurs fois (montages liés) : garder « / » puis le chemin le plus court.
    for m in sorted((m for m in mounts if len(m) >= 3), key=lambda m: (m[1] != "/", len(m[1]))):
        if m[2] not in LOCAL_FS or m[0] in seen or len(disks) >= 16:
            continue
        mount = m[1].replace("\\040", " ")
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total < 64 * 1024 * 1024:                                      # petites partitions (EFI, boot) : peu utiles
            continue
        seen.add(m[0])
        disks.append({"mount": mount, "fs": m[2], "total": total, "used": total - st.f_bfree * st.f_frsize})
    out["disks"] = disks
    return out


class LiveSampler:
    """Relevé léger du mode direct (processeur, mémoire, réseau), en moyenne depuis le relevé précédent : envoyé à chaque
    échange du canal distant tant qu'une fiche de ce poste est regardée."""
    def __init__(self):
        self.previous = None

    def sample(self):
        now, out = time.monotonic(), {}
        try:
            total, idle = _cpu_times()
            rx, tx = _net_bytes()
        except (OSError, ValueError, IndexError):
            return out
        p = self.previous
        if p and now > p[0] and total > p[1]:
            out["cpu"] = round(100 * (1 - (idle - p[2]) / (total - p[1])), 1)
            if rx >= p[3] and tx >= p[4]:
                out["net_rx"], out["net_tx"] = int((rx - p[3]) / (now - p[0])), int((tx - p[4]) / (now - p[0]))
        self.previous = (now, total, idle, rx, tx)
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    key, _, value = line.partition(":")
                    if key in ("MemTotal", "MemAvailable", "MemFree") and value.split():
                        mem[key] = int(value.split()[0]) * 1024
            out["mem_total"] = mem.get("MemTotal", 0)
            out["mem_used"] = max(0, mem.get("MemTotal", 0) - mem.get("MemAvailable", mem.get("MemFree", 0)))
        except (OSError, ValueError):
            pass
        return out


def scan():
    """Mises à jour disponibles (nom, version, sécurité), Flatpak et redémarrage nécessaire ; paquets et applications
    installés, caractéristiques du poste."""
    inv = {"updates": [], "flatpak": 0, "reboot_required": False, "error": ""}
    fam = family()
    if fam == "debian":
        code, out = run(["apt-get", "update", "-q"], timeout=1800)
        if code != 0:
            inv["error"] = "apt-get update : " + out.strip().splitlines()[-1][:300] if out.strip() else "apt-get update"
        code, out = run(["apt-get", "-s", "-q", "-o", "Debug::NoLocking=1", "upgrade", "--with-new-pkgs"])
        for line in out.splitlines():
            m = re.match(r"^Inst (\S+) (?:\[[^\]]*\] )?\((\S+) ([^)]*)\)", line)
            if m:
                inv["updates"].append({"name": m.group(1), "version": m.group(2),
                                       "security": "-security" in m.group(3) or "Security" in m.group(3)})
    elif fam == "rhel":
        tool = "dnf" if shutil.which("dnf") else "yum"
        # -y : accepte l'import de la clé d'un dépôt signé ajouté depuis (dépôt loco du paquet complet, par exemple) ;
        # sans terminal, dnf répondrait non et signalerait une « mauvaise signature ». Rien n'est installé ici.
        code, out = run([tool, "-y", "-q", "--refresh", "check-update"], timeout=1800)
        if code not in (0, 100):
            inv["error"] = f"{tool} check-update : code {code}"
        security = set()
        scode, sout = run([tool, "-y", "-q", "updateinfo", "list", "--security", "--available"], timeout=600)
        if scode == 0:
            for line in sout.splitlines():
                fields = line.split()
                if len(fields) >= 3:
                    security.add(re.sub(r"-[^-]+-[^-]+$", "", fields[-1]))
        for line in out.splitlines():
            fields = line.split()
            if len(fields) == 3 and "." in fields[0] and not line.startswith(" "):
                name = fields[0].rsplit(".", 1)[0]
                inv["updates"].append({"name": name, "version": fields[1], "security": name in security})
            if line.startswith("Obsoleting"):
                break
    else:
        inv["error"] = t("gestionnaire de paquets non reconnu", "unrecognised package manager", "gestor de paquetes no reconocido")
    inv["flatpak"] = flatpak_updates()
    inv["reboot_required"] = reboot_required()
    inv["packages"] = installed_packages(fam)
    inv["flatpaks"] = installed_flatpaks()
    inv["system"] = system_info()
    return inv


def update():
    fam = family()
    logs = []
    if fam == "debian":
        code, out = run(["apt-get", "update", "-q"], timeout=1800)
        logs.append(out)
        if code == 0:
            code, out = run(["apt-get", "-y", "-q", "-o", "Dpkg::Options::=--force-confdef",
                             "-o", "Dpkg::Options::=--force-confold", "upgrade", "--with-new-pkgs"])
            logs.append(out)
    elif fam == "rhel":
        code, out = run(["dnf" if shutil.which("dnf") else "yum", "-y", "upgrade"])
        logs.append(out)
    else:
        code, logs = 1, ["gestionnaire de paquets non reconnu"]
    if shutil.which("flatpak"):
        fcode, out = run(["flatpak", "update", "-y", "--noninteractive", "--system"], timeout=3600)
        logs.append(out)
        code = code or fcode
    return code, "\n".join(logs)


def install_package(cmd):
    """Installe (ou met à niveau) des paquets nommés depuis les dépôts déjà configurés sur le poste (dépôt loco)."""
    names = [n for n in cmd.get("packages", []) if isinstance(n, str) and n]
    if not names:
        return 1, t("Aucun paquet indiqué.", "No package given.", "Ningún paquete indicado.")
    fam = family()
    logs = []
    if fam == "debian":
        code, out = run(["apt-get", "update", "-q"], timeout=1800)
        logs.append(out)
        if code == 0:
            code, out = run(["apt-get", "-y", "-q", "-o", "Dpkg::Options::=--force-confdef",
                             "-o", "Dpkg::Options::=--force-confold", "install", *names])
            logs.append(out)
    elif fam == "rhel":
        code, out = run(["dnf" if shutil.which("dnf") else "yum", "-y", "install", *names])
        logs.append(out)
    else:
        return 1, t("gestionnaire de paquets non reconnu", "unrecognised package manager", "gestor de paquetes no reconocido")
    return code, "\n".join(logs)


# --------------------------------------------------------------------------- service

def report(conf, cmd_id, state, output="", code=None, inventory=None):
    data = {"id": cmd_id, "state": state, "output": output[-OUTPUT_LIMIT:], "exit_code": code}
    if inventory is not None:
        data["inventory"] = inventory
    try:
        call(conf["server"], "/agent/api/result", data, conf["token"])
    except (OSError, ValueError) as e:
        log(t(f"résultat non transmis : {e}", f"result not sent: {e}", f"resultado no transmitido: {e}"))


def run_script(cmd):
    """Script de la bibliothèque loco : contenu vérifié par son empreinte, exécuté en root (sh, Python, ou l'interpréteur du #!)."""
    script = cmd.get("script") or {}
    content = script.get("content") or ""
    platform = script.get("platform")
    if platform not in ("linux", "python"):
        return 1, t("Script destiné à un autre système.", "Script intended for another system.", "Script destinado a otro sistema.")
    if hashlib.sha256(content.encode()).hexdigest() != (script.get("sha256") or "").lower():
        return 1, t("Empreinte du script invalide : non exécuté.", "Invalid script fingerprint: not run.", "Huella del script no válida: no ejecutado.")
    python = shutil.which("python3") or shutil.which("python")
    if platform == "python" and not python:
        return 1, t("Python introuvable sur ce poste.", "Python not found on this machine.", "Python no se encuentra en este equipo.")
    fd, path = tempfile.mkstemp(prefix="loco-script-", suffix=".py" if platform == "python" else ".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(path, 0o700)
        if platform == "python":
            argv = [python, path]
        else:
            argv = [path] if content.startswith("#!") else ["/bin/sh", path]
        try:
            done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=1800, cwd="/")
        except subprocess.TimeoutExpired as e:
            return -1, ((e.output or b"").decode("utf-8", "replace") + t("\nArrêté : pas terminé après 1800 s.", "\nStopped: not finished after 1800 s.", "\nDetenido: no terminó tras 1800 s.")).strip()
        except OSError as e:
            return 1, t(f"Exécution impossible : {e}", f"Cannot run: {e}", f"No se puede ejecutar: {e}")
        return done.returncode, done.stdout.decode("utf-8", "replace").strip() or "(aucune sortie)"
    finally:
        os.unlink(path)


def self_update(conf, wanted):
    """Nouvelle version de loco sur le serveur : l'agent se met à jour (code servi par le serveur épinglé)."""
    def key(v):
        return tuple(int(x) for x in re.findall(r"\d+", v or ""))
    if not wanted or key(wanted) <= key(VERSION):
        return
    try:
        code = download(conf["server"], "/agent/files/loco-client.py").decode()
        compile(code, PROGRAM, "exec")
    except (OSError, ValueError, SyntaxError) as e:
        log(t(f"mise à jour de l'agent impossible : {e}", f"agent update failed: {e}", f"no se pudo actualizar el agente: {e}"))
        return
    if f'VERSION = "{wanted}"' not in code:
        return
    tmp = PROGRAM + ".new"
    with open(tmp, "w") as f:
        f.write(code)
    os.chmod(tmp, 0o755)
    os.replace(tmp, PROGRAM)
    log(t(f"agent mis à jour : {VERSION} -> {wanted}", f"agent updated: {VERSION} -> {wanted}", f"agente actualizado: {VERSION} -> {wanted}"))


def remove_local():
    subprocess.run(["systemctl", "disable", "--now", "loco-client-remote.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["systemctl", "disable", "--now", "loco-client.timer"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for path in UNITS:
        if os.path.exists(path):
            os.unlink(path)
    subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for path in (ETC, os.path.dirname(STATE), os.path.dirname(PROGRAM)):
        shutil.rmtree(path, ignore_errors=True)
    for link in ("/usr/local/sbin/loco-client",):
        if os.path.islink(link):
            os.unlink(link)


def service():
    conf = load(CONFIG)
    if not conf.get("token"):
        log(t("poste non inscrit : relancez la commande d'installation de l'agent.", "machine not enrolled: run the agent install command again.", "equipo no inscrito: vuelva a ejecutar el comando de instalación del agente."))
        return 1
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    ensure_remote_service()
    state = load(STATE)
    data = identity()
    data["metrics"] = metrics(state)
    last = state.get("last_scan", 0)
    # Inventaire refait après un redémarrage (celui d'avant ne reflète plus les mises à jour installées) et après une
    # mise à jour de l'agent (une nouvelle version peut remonter des informations de plus, comme les paquets installés).
    if (time.time() - last > state.get("scan_hours", 6) * 3600 or state.get("rescan") or booted_since(last)
            or state.get("inventory_version") != VERSION):
        data["inventory"] = scan()
        state.update(last_scan=time.time(), rescan=False, inventory_version=VERSION)
    try:
        answer = checkin(conf, data)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log(t("jeton refusé par le serveur (poste retiré du parc ?) : réinstallez l'agent pour le réinscrire.", "token refused by the server (machine removed from the fleet?): reinstall the agent to re-enroll it.", "token rechazado por el servidor (¿equipo retirado del parque?): reinstale el agente para volver a inscribirlo."))
        else:
            log(t(f"serveur : HTTP {e.code}", f"server: HTTP {e.code}", f"servidor: HTTP {e.code}"))
        save(STATE, state)
        return 1
    except (OSError, ValueError) as e:
        log(t(f"serveur injoignable : {e}", f"server unreachable: {e}", f"servidor inaccesible: {e}"))
        # Inventaire non transmis : à refaire au prochain contact.
        if "inventory" in data:
            state["rescan"] = True
        save(STATE, state)
        return 1
    state["scan_hours"] = answer.get("scan_hours", 6)
    apply_trust(answer)
    for cmd in answer.get("commands", []):
        action = cmd.get("action")
        log(t(f"action {cmd.get('id')} : {action}", f"action {cmd.get('id')}: {action}", f"acción {cmd.get('id')}: {action}"))
        report(conf, cmd["id"], "running")
        if action == "scan":
            inv = scan()
            state["last_scan"] = time.time()
            report(conf, cmd["id"], "success" if not inv["error"] else "failed", inv["error"] or t("Inventaire transmis.", "Inventory sent.", "Inventario transmitido."),
                   0, inv)
        elif action == "update":
            code, out = update()
            inv = scan()
            state["last_scan"] = time.time()
            reboot = cmd.get("reboot") and inv["reboot_required"] and code == 0
            report(conf, cmd["id"], "success" if code == 0 else "failed",
                   out + (t("\nRedémarrage dans une minute.", "\nRebooting in one minute.", "\nReinicio en un minuto.") if reboot else ""), code, inv)
            if reboot:
                save(STATE, state)
                subprocess.run(["shutdown", "-r", "+1", t("loco : redémarrage après mise à jour", "loco: reboot after update", "loco: reinicio tras la actualización")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return 0
        elif action == "install-package":
            code, out = install_package(cmd)
            inv = scan()
            state["last_scan"] = time.time()
            report(conf, cmd["id"], "success" if code == 0 else "failed", out, code, inv)
        elif action == "run-script":
            code, out = run_script(cmd)
            report(conf, cmd["id"], "success" if code == 0 else "failed", out, code)
        elif action == "reboot":
            report(conf, cmd["id"], "success", t("Redémarrage dans une minute.", "Rebooting in one minute.", "Reinicio en un minuto."), 0)
            save(STATE, state)
            subprocess.run(["shutdown", "-r", "+1", t("loco : redémarrage demandé", "loco: reboot requested", "loco: reinicio solicitado")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return 0
        elif action == "uninstall":
            report(conf, cmd["id"], "success", t("Agent retiré du poste.", "Agent removed from the machine.", "Agente retirado del equipo."), 0)
            remove_local()
            log(t("agent retiré à la demande du serveur.", "agent removed at the server's request.", "agente retirado a petición del servidor."))
            return 0
        else:
            report(conf, cmd["id"], "failed", t(f"Action non prise en charge sur Linux : {action}", f"Action not supported on Linux: {action}", f"Acción no admitida en Linux: {action}"), 1)
    save(STATE, state)
    self_update(conf, answer.get("agent_version"))
    return 0


def enroll(server, key):
    os.makedirs(ETC, mode=0o700, exist_ok=True)
    conf = load(CONFIG)
    uid = conf.get("uid") or uuid.uuid4().hex
    data = identity()
    if not data["os"]["family"]:
        log(t("système non pris en charge (APT ou DNF requis).", "unsupported system (APT or DNF required).", "sistema no admitido (se requiere APT o DNF)."))
        return 1
    data.update(key=key, uid=uid)
    try:
        answer = call(server, "/agent/api/enroll", data)
    except urllib.error.HTTPError as e:
        log(t("clé d'inscription refusée.", "enrollment key refused.", "clave de inscripción rechazada.") if e.code == 403 else t(f"inscription refusée : HTTP {e.code}", f"enrollment refused: HTTP {e.code}", f"inscripción rechazada: HTTP {e.code}"))
        return 1
    except (OSError, ValueError) as e:
        log(t(f"serveur injoignable : {e}", f"server unreachable: {e}", f"servidor inaccesible: {e}"))
        return 1
    save(CONFIG, {"server": server.rstrip("/"), "uid": uid, "token": answer["token"],
                  "enrolled": datetime.datetime.now().isoformat(timespec="seconds")})
    save(STATE, {"rescan": True})
    log(t(f"poste inscrit auprès de {server}.", f"machine enrolled with {server}.", f"equipo inscrito en {server}."))
    return 0


def status_cmd():
    conf = load(CONFIG)
    state = load(STATE)
    scanned = state.get("last_scan")
    rows = [(t("Agent", "Agent", "Agente"), f"loco-client {VERSION}"),
            (t("Serveur", "Server", "Servidor"), conf.get("server") or t("non inscrit", "not enrolled", "no inscrito"))]
    others = known_servers(conf)[1:] if conf.get("server") else []
    if others:
        rows.append((t("De secours", "Fallback", "De reserva"), " ".join(others)))
    rows.append((t("Identifiant", "ID", "Identificador"), conf.get("uid", "-")))
    rows.append((t("Inventaire", "Inventory", "Inventario"),
                 datetime.datetime.fromtimestamp(scanned).isoformat(timespec="seconds") if scanned else t("jamais", "never", "nunca")))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label.ljust(width)} : {value}")
    return 0



# --------------------------------------------------------------------------- sessions distantes natives

REMOTE_UNIT = """[Unit]
Description=loco : sessions distantes de l'agent
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/lib/loco-client/loco-client.py remote
Restart=always
RestartSec=5
TimeoutStopSec=10
KillMode=control-group
[Install]
WantedBy=multi-user.target
"""


def ensure_remote_service():
    path = UNITS[2]
    try:
        with open(path) as f:
            if f.read() == REMOTE_UNIT:
                return
    except OSError:
        pass
    with open(path, "w") as f:
        f.write(REMOTE_UNIT)
    os.chmod(path, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["systemctl", "enable", "--now", "loco-client-remote.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class RemoteProcess:
    """PTY ou flux RFB privé, avec acquittements pour ne pas rejouer une frappe après une coupure."""
    def __init__(self, descriptor):
        self.sid, self.kind = descriptor["id"], descriptor["kind"]
        self.ack, self.seq, self.pending, self.incoming = 0, 0, b"", []
        self.state, self.error, self.process, self.master = "active", "", None, None
        try:
            if self.kind == "terminal":
                master, slave = pty.openpty()
                self.master = master
                try:
                    home = pwd.getpwuid(os.geteuid()).pw_dir
                    env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor", HOME=home)
                    # Le shell de connexion joue /etc/profile puis le ~/.bashrc du poste (couleurs, alias, invite).
                    # S'il n'en ressort aucune invite colorée, en poser une par défaut pour une vraie allure de
                    # terminal — un PS1 déjà coloré ou un PROMPT_COMMAND du poste restent prioritaires.
                    default_ps1 = r"\[\e[1;32m\]\u@\h\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ "
                    env["PROMPT_COMMAND"] = "case \"$PS1\" in *'\\['*) ;; *) PS1='%s' ;; esac" % default_ps1
                    shell = "/bin/bash" if os.path.isfile("/bin/bash") else "/bin/sh"
                    bootstrap = "import fcntl,termios,os; fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(%r,[%r,'-l'])" % (shell, shell)
                    self.process = subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave,
                                                    start_new_session=True, close_fds=True, env=env, cwd=home)
                finally:
                    os.close(slave)
                self.reader = self.writer = master
            elif self.kind == "desktop":
                command = [sys.executable, PROGRAM, "desktop"]
                if descriptor.get("terminal_fallback"):
                    command.append("--terminal-fallback")
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
                self.reader, self.writer = self.process.stdout.fileno(), self.process.stdin.fileno()
            elif self.kind == "files":
                self.process = subprocess.Popen([sys.executable, PROGRAM, "files"], stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                start_new_session=True, close_fds=True)
                self.reader, self.writer = self.process.stdout.fileno(), self.process.stdin.fileno()
            else:
                raise ValueError(t("Type de session inconnu", "Unknown session type", "Tipo de sesión desconocido"))
            for fd in set((self.reader, self.writer)):
                fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
            self.resize(descriptor)
        except Exception as e:
            self.state, self.error = "closed", str(e)[:300]
            self.close()

    def resize(self, descriptor):
        if self.master is not None:
            cols, rows = max(20, min(500, int(descriptor["cols"]))), max(5, min(200, int(descriptor["rows"])))
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def update(self, descriptor):
        if descriptor["ack"] == self.seq:
            self.pending = b""
        if descriptor["state"] == "closed":
            self.close()
            return
        if self.state != "active":
            return
        self.resize(descriptor)
        for packet in descriptor["packets"]:
            seq = packet["seq"]
            if seq <= self.ack or any(seq == item[0] for item in self.incoming):
                continue
            data = base64.b64decode(packet["data"], validate=True)
            if len(data) > 256 * 1024 or sum(len(item[1]) for item in self.incoming) + len(data) > 2 * 1024 * 1024:
                raise ValueError("Saisie distante trop volumineuse")
            self.incoming.append([seq, data])
        while self.incoming:
            item = self.incoming[0]
            try:
                count = os.write(self.writer, item[1])
            except BlockingIOError:
                break
            item[1] = item[1][count:]
            if item[1]:
                break
            self.ack = item[0]
            self.incoming.pop(0)

    def payload(self):
        if self.state == "active" and not self.pending:
            try:
                parts, size, eof = [], 0, False
                while size < 256 * 1024:
                    try:
                        part = os.read(self.reader, 256 * 1024 - size)
                    except BlockingIOError:
                        break
                    if not part:
                        eof = True
                        break
                    parts.append(part); size += len(part)
                data = b"".join(parts)
                if data:
                    self.pending, self.seq = data, self.seq + 1
                elif eof:
                    self.state = "closed"
            except BlockingIOError:
                pass
            except OSError:
                self.state = "closed"
            if self.process.poll() is not None and not self.pending:
                self.state = "closed"
            if self.state == "closed":
                if self.kind in ("desktop", "files") and self.process.stderr:
                    self.error = self.process.stderr.read(1000).decode("utf-8", "replace").strip()[-300:]
                self.error = self.error or t("Session terminée.", "Session ended.", "Sesión finalizada.")
        return {"id": self.sid, "ack": self.ack, "seq": self.seq,
                "data": base64.b64encode(self.pending).decode(), "state": self.state, "error": self.error}

    def close(self):
        self.state = "closed"
        if self.process:
            try:
                os.killpg(self.process.pid, signal.SIGHUP if self.kind == "terminal" else signal.SIGTERM)
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    stream.close()
            self.process = None
        if self.master is not None:
            os.close(self.master)
            self.master = None


class RemoteTransport:
    """Connexion HTTPS persistante dédiée au flux distant (CA et nom toujours vérifiés)."""
    def __init__(self):
        self.connection, self.key = None, None

    def close(self):
        if self.connection:
            self.connection.close()
        self.connection, self.key = None, None

    def exchange(self, server, data, token):
        parsed = urllib.parse.urlsplit(server)
        ca_stamp = os.stat(CA).st_mtime_ns if os.path.isfile(CA) else None
        key = (server, ca_stamp)
        try:
            if self.key != key:
                self.close()
                if parsed.scheme == "https":
                    self.connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=10, context=context())
                elif parsed.scheme == "http":
                    self.connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
                else:
                    raise ValueError(t("Adresse du serveur invalide", "Invalid server address", "Dirección del servidor no válida"))
                self.key = key
            self.connection.request("POST", parsed.path.rstrip("/") + "/agent/api/remote", json.dumps(data).encode(),
                                    {"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                     "User-Agent": "loco-client/" + VERSION})
            response = self.connection.getresponse()
            body = response.read(4 * 1024 * 1024 + 1)
            if response.status != 200:
                raise urllib.error.HTTPError(server, response.status, response.reason, response.headers, None)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError(t("Réponse distante trop volumineuse", "Remote response too large", "Respuesta remota demasiado grande"))
            return json.loads(body)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.close()
            if isinstance(exc, http.client.HTTPException):
                raise OSError(str(exc)) from exc
            raise


def remote_service():
    stopping = [False]
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__(0, True))
    with open("/run/loco-client-remote.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        sessions, last_ok = {}, time.monotonic()
        transport = RemoteTransport()
        live, sampler = False, LiveSampler()
        loaded = os.stat(PROGRAM)
        signature = (loaded.st_ino, loaded.st_mtime_ns, loaded.st_size)
        try:
            while not stopping[0]:
                current = os.stat(PROGRAM)
                if (current.st_ino, current.st_mtime_ns, current.st_size) != signature:
                    # Le service systemd relance le nouveau code après mise à jour automatique ou réinstallation.
                    break
                delay = 5
                try:
                    conf = load(CONFIG)
                    if not conf.get("token"):
                        break
                    payload = {"sessions": [s.payload() for s in sessions.values()]}
                    if live:
                        payload["metrics"] = sampler.sample()
                    answer = transport.exchange(conf["server"], payload, conf["token"])
                    last_ok = time.monotonic()
                    if not answer.get("live"):
                        sampler.previous = None                # prochain direct : repartir d'un relevé frais
                    live = bool(answer.get("live"))
                    descriptors = {d["id"]: d for d in answer["sessions"]}
                    for sid in list(sessions):
                        if sid not in descriptors or descriptors[sid]["state"] == "closed":
                            sessions.pop(sid).close()
                    for sid, descriptor in descriptors.items():
                        if descriptor["state"] not in ("waiting", "active"):
                            continue
                        if sid not in sessions:
                            sessions[sid] = RemoteProcess(descriptor)
                        try:
                            sessions[sid].update(descriptor)
                        except (OSError, ValueError) as e:
                            sessions[sid].error = str(e)[:300]
                            sessions[sid].close()
                    busy = any(item.pending for item in sessions.values()) or any(d.get("packets") for d in descriptors.values())
                    delay = (0 if busy else 0.025) if sessions else 5
                    if live:                                   # fiche regardée : un relevé toutes les 2 s
                        delay = min(delay, max(1.0, float(answer.get("live") or 2)))
                except (OSError, ValueError, KeyError) as e:
                    if getattr(e, "code", 0) == 429:
                        # Le navigateur doit vider sa file : réessayer le même paquet, sans coupure ni rejeu.
                        last_ok, delay = time.monotonic(), 0.05
                    else:
                        log("session distante : " + str(e)[:200])
                        if time.monotonic() - last_ok > 20 or getattr(e, "code", 0) in (401, 403):
                            for item in sessions.values():
                                item.close()
                            sessions.clear()
                until = time.monotonic() + delay
                while not stopping[0] and time.monotonic() < until:
                    time.sleep(min(0.1, max(0, until - time.monotonic())))
        finally:
            transport.close()
            for item in sessions.values():
                item.close()
    return 0


class XImage(ctypes.Structure):
    _fields_ = [(n, t) for n, t in (("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong))]


def x11_environment():
    wayland = False
    # Seulement les displays locaux ; aucun hôte / mot de passe fourni par le navigateur.
    candidates = [dict(os.environ)]
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/environ", "rb") as f:
                values = f.read(65536).split(b"\0")
            candidates.append(dict(v.decode("utf-8", "replace").split("=", 1) for v in values if b"=" in v))
        except OSError:
            pass
    wayland_displays = {e.get("DISPLAY") for e in candidates if e.get("XDG_SESSION_TYPE") == "wayland"}
    for env in candidates:
        if env.get("XDG_SESSION_TYPE") == "wayland":
            wayland = True
            continue
        if env.get("DISPLAY") not in wayland_displays and re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", env.get("DISPLAY", "")):
            return env
    raise RuntimeError(t("Bureau Wayland non pris en charge ; une session X11 est nécessaire.", "Wayland desktop not supported; an X11 session is required.", "Escritorio Wayland no admitido; se necesita una sesión X11.") if wayland else
                       t("Aucune session graphique X11 ouverte sur ce poste.", "No X11 graphical session open on this machine.", "Ninguna sesión gráfica X11 abierta en este equipo."))


class XFixesCursorImage(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
                ("xhot", ctypes.c_ushort), ("yhot", ctypes.c_ushort), ("cursor_serial", ctypes.c_ulong),
                ("pixels", ctypes.POINTER(ctypes.c_ulong))]


def cursor_rect(xhot, yhot, width, height, argb):
    """Pseudo-rectangle « curseur VMware alpha » : pixels RGBA non prémultipliés (pixels X11 ARGB prémultipliés)."""
    if not 1 <= width <= 256 or not 1 <= height <= 256 or len(argb) != width * height:
        raise ValueError("Curseur invalide")
    rgba = bytearray(width * height * 4)
    for i, value in enumerate(argb):
        alpha = (value >> 24) & 255
        if alpha:
            rgba[i * 4:i * 4 + 4] = bytes((min(255, ((value >> 16) & 255) * 255 // alpha),
                                           min(255, ((value >> 8) & 255) * 255 // alpha),
                                           min(255, (value & 255) * 255 // alpha), alpha))
    return struct.pack(">HHHHI", min(xhot, width - 1), min(yhot, height - 1), width, height, 0x574D5664) + b"\x01\x00" + bytes(rgba)


def rfb_frame(data, width, height, previous=None, incremental=True, tight=True, rgb_order=False, cursor=None):
    """Rectangles natifs modifiés, sans perte ; les buffers restent bornés à une capture.
    cursor : pseudo-rectangle de forme du curseur, joint quand elle change."""
    if not 1 <= width <= 65535 or not 1 <= height <= 65535 or width * height > 16777216 or len(data) != width * height * 4:
        raise ValueError("Capture RFB invalide")
    rectangles = []
    for y in range(0, height, 128):
        for x in range(0, width, 128):
            w, h = min(128, width - x), min(128, height - y)
            offsets = [(row * width + x) * 4 for row in range(y, y + h)]
            if incremental and previous is not None and all(data[p:p + w * 4] == previous[p:p + w * 4] for p in offsets):
                continue
            raw = b"".join(data[p:p + w * 4] for p in offsets)
            header = struct.pack(">HHHHi", x, y, w, h, 7 if tight else 0)
            if tight:
                rgb = bytearray(w * h * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
                if rgb == rgb[:3] * (w * h):
                    payload = b"\x80" + rgb[:3]
                elif len(rgb) < 12:
                    payload = b"\x01" + rgb
                else:
                    compressed = zlib.compress(rgb, 1)
                    n = len(compressed)
                    size = bytes([(n & 127) | (128 if n > 127 else 0)])
                    if n > 127:
                        size += bytes([((n >> 7) & 127) | (128 if n > 16383 else 0)])
                    if n > 16383:
                        size += bytes([n >> 14])
                    payload = b"\x01" + size + compressed
            else:
                payload = bytearray(raw)
                if rgb_order:
                    payload[0::4], payload[2::4] = raw[2::4], raw[0::4]
            rectangles.append(header + payload)
    if cursor:
        rectangles.insert(0, cursor)
    return b"\0\0" + struct.pack(">H", len(rectangles)) + b"".join(rectangles)


def text_console():
    """Bibliothèques libres embarquées, vérifiées avant import ; aucun paquet à télécharger sur le poste."""
    if CONSOLE_LIB.startswith("@"):
        # Sources du dépôt, pour les essais directs avant génération de l'agent autonome.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "console"))
    else:
        import tempfile
        data = base64.b64decode(CONSOLE_LIB, validate=True)
        digest = hashlib.sha256(data).hexdigest()
        # Répertoire privé au processus : aucun fichier persistant ni collision entre deux consoles.
        with tempfile.TemporaryDirectory(prefix="loco-console-") as directory:
            path = os.path.join(directory, "console.zip")
            with open(path, "wb") as f:
                f.write(data)
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != digest:
                    raise RuntimeError(t("Bibliothèque de console altérée", "Console library tampered with", "Biblioteca de consola alterada"))
            sys.path.insert(0, path)
            from console_rfb import serve
            return serve(rfb_frame)
    from console_rfb import serve
    return serve(rfb_frame)


# Table X keysym -> code touche evdev (disposition US), pour la saisie Wayland via /dev/uinput.
def _build_evdev_keys():
    k = {}
    letters = {30: "aA", 48: "bB", 46: "cC", 32: "dD", 18: "eE", 33: "fF", 34: "gG", 35: "hH",
               23: "iI", 36: "jJ", 37: "kK", 38: "lL", 50: "mM", 49: "nN", 24: "oO", 25: "pP",
               16: "qQ", 19: "rR", 31: "sS", 20: "tT", 22: "uU", 47: "vV", 17: "wW", 45: "xX",
               21: "yY", 44: "zZ"}
    # chiffres et symboles (base + Maj, disposition US), ponctuation : chaque caractère vise sa touche physique.
    digits = {2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(", 11: "0)"}
    punct = {12: "-_", 13: "=+", 26: "[{", 27: "]}", 43: "\\|", 39: ";:", 40: "'\"",
             41: "`~", 51: ",<", 52: ".>", 53: "/?", 57: " "}
    for table in (letters, digits, punct):
        for code, chars in table.items():
            for ch in chars:
                k[ord(ch)] = code
    specials = {0xff08: 14, 0xff09: 15, 0xff0d: 28, 0xff1b: 1, 0xffff: 111, 0xff8d: 96,
                0xff50: 102, 0xff51: 105, 0xff52: 103, 0xff53: 106, 0xff54: 108,
                0xff55: 104, 0xff56: 109, 0xff57: 107, 0xff63: 110,
                0xffe1: 42, 0xffe2: 54, 0xffe3: 29, 0xffe4: 97, 0xffe5: 58,
                0xffe7: 125, 0xffe8: 126, 0xffe9: 56, 0xffea: 100, 0xffeb: 125, 0xffec: 126,
                0xff7f: 69, 0xff14: 70, 0xff67: 127, 0xfe03: 100,
                0xffaa: 55, 0xffab: 78, 0xffad: 74, 0xffae: 83, 0xffaf: 98,
                0xffb0: 82, 0xffb1: 79, 0xffb2: 80, 0xffb3: 81, 0xffb4: 75, 0xffb5: 76,
                0xffb6: 77, 0xffb7: 71, 0xffb8: 72, 0xffb9: 73}
    for n in range(12):     # F1..F12
        specials[0xffbe + n] = (59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88)[n]
    k.update(specials)
    return k


EVDEV_KEYS = _build_evdev_keys()


def _parse_ppm(buf):
    """En-tête PPM binaire (P6) de grim : largeur, hauteur, puis pixels RGB 8 bits."""
    if buf[:2] != b"P6":
        raise RuntimeError(t("Capture Wayland illisible (format inattendu).", "Unreadable Wayland capture (unexpected format).", "Captura Wayland ilegible (formato inesperado)."))
    values, idx = [], 2
    while len(values) < 3:
        while idx < len(buf) and buf[idx:idx + 1].isspace():
            idx += 1
        if idx < len(buf) and buf[idx:idx + 1] == b"#":           # commentaire PPM : jusqu'à la fin de ligne
            while idx < len(buf) and buf[idx:idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < len(buf) and not buf[idx:idx + 1].isspace():
            idx += 1
        if start == idx:
            raise RuntimeError(t("Capture Wayland illisible (en-tête tronqué).", "Unreadable Wayland capture (truncated header).", "Captura Wayland ilegible (encabezado truncado)."))
        values.append(int(buf[start:idx]))
    width, height, maxval = values
    data = buf[idx + 1:idx + 1 + width * height * 3]              # un seul espace sépare l'en-tête des pixels
    if maxval != 255 or len(data) != width * height * 3:
        raise RuntimeError(t("Capture Wayland illisible (pixels incomplets).", "Unreadable Wayland capture (incomplete pixels).", "Captura Wayland ilegible (píxeles incompletos)."))
    return width, height, data


def _rgb_to_bgrx(rgb):
    """RGB (grim) -> BGRX attendu par rfb_frame ; copies à pas natif, bornées à une capture."""
    out = bytearray(len(rgb) // 3 * 4)
    out[0::4], out[1::4], out[2::4] = rgb[2::3], rgb[1::3], rgb[0::3]
    return bytes(out)


class UInput:
    """Périphérique d'entrée virtuel (/dev/uinput) : pointeur absolu type tablette + clavier, indépendant du compositeur."""
    EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
    SYN_REPORT, REL_WHEEL, ABS_X, ABS_Y = 0, 8, 0, 1
    ABS_RANGE = 32767                                             # plage d'une tablette absolue (convention machine virtuelle)
    BUTTONS = {1: 0x110, 2: 0x112, 3: 0x111}                      # BTN_LEFT, BTN_MIDDLE, BTN_RIGHT

    def __init__(self, width, height):
        import fcntl
        self._fcntl = fcntl
        self._event = struct.Struct("@llHHi")                    # struct input_event : timeval, type, code, value
        self.width, self.height = width, height
        base = ord("U")
        iow = lambda nr, size: (1 << 30) | (size << 16) | (base << 8) | nr
        self._create, self._destroy = (base << 8) | 1, (base << 8) | 2
        set_evbit, set_keybit, set_relbit, set_absbit = iow(100, 4), iow(101, 4), iow(102, 4), iow(103, 4)
        self._fd = os.open("/dev/uinput", os.O_WRONLY)
        try:
            for ev in (self.EV_SYN, self.EV_KEY, self.EV_REL, self.EV_ABS):
                fcntl.ioctl(self._fd, set_evbit, ev)
            fcntl.ioctl(self._fd, set_relbit, self.REL_WHEEL)
            for axis in (self.ABS_X, self.ABS_Y):
                fcntl.ioctl(self._fd, set_absbit, axis)
            for code in set(EVDEV_KEYS.values()) | set(self.BUTTONS.values()):
                fcntl.ioctl(self._fd, set_keybit, code)
            absmax, absmin, zeros = [0] * 64, [0] * 64, [0] * 64
            absmax[self.ABS_X] = absmax[self.ABS_Y] = self.ABS_RANGE
            setup = (struct.pack("@80sHHHHI", b"loco remote desktop", 0x06, 0x1234, 0x5678, 1, 0)
                     + struct.pack("@64i", *absmax) + struct.pack("@64i", *absmin)
                     + struct.pack("@64i", *zeros) + struct.pack("@64i", *zeros))
            os.write(self._fd, setup)
            fcntl.ioctl(self._fd, self._create)
        except OSError:
            os.close(self._fd)
            raise
        time.sleep(0.4)                                          # laisse le compositeur (libinput) adopter le périphérique

    def _emit(self, etype, code, value):
        os.write(self._fd, self._event.pack(0, 0, etype, code, value))

    def _syn(self):
        self._emit(self.EV_SYN, self.SYN_REPORT, 0)

    def motion(self, x, y):
        self._emit(self.EV_ABS, self.ABS_X, min(self.ABS_RANGE, max(0, round(x * self.ABS_RANGE / max(1, self.width - 1)))))
        self._emit(self.EV_ABS, self.ABS_Y, min(self.ABS_RANGE, max(0, round(y * self.ABS_RANGE / max(1, self.height - 1)))))
        self._syn()

    def button(self, number, down):
        code = self.BUTTONS.get(number)
        if code is not None:
            self._emit(self.EV_KEY, code, 1 if down else 0)
            self._syn()

    def wheel(self, amount):
        self._emit(self.EV_REL, self.REL_WHEEL, amount)
        self._syn()

    def key(self, code, down):
        self._emit(self.EV_KEY, code, 1 if down else 0)
        self._syn()

    def close(self):
        try:
            self._fcntl.ioctl(self._fd, self._destroy)
        except OSError:
            pass
        os.close(self._fd)


def wayland_environment():
    """Session Wayland locale : répertoire d'exécution + socket du compositeur, pour grim et le périphérique d'entrée."""
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/environ", "rb") as f:
                values = f.read(65536).split(b"\0")
            env = dict(v.decode("utf-8", "replace").split("=", 1) for v in values if b"=" in v)
        except OSError:
            continue
        runtime, display = env.get("XDG_RUNTIME_DIR", ""), env.get("WAYLAND_DISPLAY", "")
        if env.get("XDG_SESSION_TYPE") == "wayland" and re.fullmatch(r"/run/user/[0-9]+", runtime) and display:
            socket_path = display if os.path.isabs(display) else runtime.rstrip("/") + "/" + display
            if (os.path.isabs(display) or re.fullmatch(r"wayland-[0-9]+", display)) and os.path.exists(socket_path):
                return {"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": display}
    raise RuntimeError(t("Aucune session Wayland active accessible à l'agent.", "No active Wayland session accessible to the agent.", "Ninguna sesión Wayland activa accesible para el agente."))


class X11Backend:
    """Capture XGetImage + saisie XTest sur une session X11 locale."""
    interval = 0.04

    def __init__(self, env):
        os.environ["DISPLAY"] = env["DISPLAY"]
        if env.get("XAUTHORITY"):
            os.environ["XAUTHORITY"] = env["XAUTHORITY"]
        x, xt = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
        def bind(lib, name, result, args):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
            return fn
        ptr, integer, ulong = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
        self._d = bind(x, "XOpenDisplay", ptr, [ctypes.c_char_p])(env["DISPLAY"].encode())
        if not self._d:
            raise RuntimeError(t("La session X11 n'est pas accessible à l'agent.", "The X11 session is not accessible to the agent.", "La sesión X11 no es accesible para el agente."))
        self._root = bind(x, "XDefaultRootWindow", ulong, [ptr])(self._d)
        self._screen = bind(x, "XDefaultScreen", integer, [ptr])(self._d)
        self.width = bind(x, "XDisplayWidth", integer, [ptr, integer])(self._d, self._screen)
        self.height = bind(x, "XDisplayHeight", integer, [ptr, integer])(self._d, self._screen)
        if not 1 <= self.width <= 4096 or not 1 <= self.height <= 2160:
            raise RuntimeError(t("Résolution X11 supérieure à 4096 × 2160.", "X11 resolution larger than 4096 × 2160.", "Resolución X11 superior a 4096 × 2160."))
        self._get_image = bind(x, "XGetImage", ctypes.POINTER(XImage), [ptr, ulong, integer, integer, ctypes.c_uint, ctypes.c_uint, ulong, integer])
        self._destroy = bind(x, "XDestroyImage", integer, [ctypes.POINTER(XImage)])
        self._keycode = bind(x, "XKeysymToKeycode", ctypes.c_ubyte, [ptr, ulong])
        self._fkey = bind(xt, "XTestFakeKeyEvent", integer, [ptr, ctypes.c_uint, integer, ulong])
        self._fbutton = bind(xt, "XTestFakeButtonEvent", integer, [ptr, ctypes.c_uint, integer, ulong])
        self._fmotion = bind(xt, "XTestFakeMotionEvent", integer, [ptr, integer, integer, integer, ulong])
        self._flush = bind(x, "XFlush", integer, [ptr])
        self._xfree = bind(x, "XFree", integer, [ptr])
        try:
            # Forme du curseur (XFixes) : facultative, le bureau reste utilisable sans elle.
            self._get_cursor = bind(ctypes.CDLL("libXfixes.so.3"), "XFixesGetCursorImage", ctypes.POINTER(XFixesCursorImage), [ptr])
        except OSError:
            self._get_cursor = None
        self._close = bind(x, "XCloseDisplay", integer, [ptr])
        self._cursor_shape, self._cursor_serial = False, None

    def grab(self):
        image = self._get_image(self._d, self._root, 0, 0, self.width, self.height, ctypes.c_ulong(-1).value, 2)
        if not image:
            raise RuntimeError(t("Capture de la session X11 impossible", "Cannot capture the X11 session", "No se puede capturar la sesión X11"))
        try:
            im = image.contents
            if im.bits_per_pixel != 32 or im.byte_order != 0 or (im.red_mask, im.green_mask, im.blue_mask) != (0xff0000, 0xff00, 0xff):
                raise RuntimeError(t("Format X11 non pris en charge (RGB 24/32 bits requis)", "Unsupported X11 format (RGB 24/32-bit required)", "Formato X11 no admitido (se requiere RGB 24/32 bits)"))
            data = ctypes.string_at(im.data, im.bytes_per_line * self.height)
            if im.bytes_per_line != self.width * 4:
                data = b"".join(data[y * im.bytes_per_line:y * im.bytes_per_line + self.width * 4] for y in range(self.height))
        finally:
            self._destroy(image)
        return data

    def key(self, sym, down):
        code = self._keycode(self._d, sym)
        if code:
            self._fkey(self._d, code, bool(down), 0)
            return True
        return False

    def motion(self, x, y):
        self._fmotion(self._d, self._screen, x, y, 0)

    def button(self, number, down):
        self._fbutton(self._d, number, bool(down), 0)

    def set_cursor(self, enabled):
        self._cursor_shape, self._cursor_serial = enabled, None

    def cursor(self):
        if not (self._cursor_shape and self._get_cursor):
            return None
        image = self._get_cursor(self._d)
        if not image:
            return None
        try:
            im = image.contents
            if im.cursor_serial == self._cursor_serial or not 1 <= im.width <= 256 or not 1 <= im.height <= 256:
                return None
            self._cursor_serial = im.cursor_serial
            return cursor_rect(im.xhot, im.yhot, im.width, im.height, im.pixels[:im.width * im.height])
        finally:
            self._xfree(image)

    def flush(self):
        self._flush(self._d)

    def close(self):
        self._close(self._d)


class WaylandBackend:
    """Capture via grim (compositeurs wlroots) + saisie par périphérique d'entrée virtuel, pour une session Wayland."""
    interval = 0.1                                               # grim est relancé par trame : ~10 i/s suffisent à l'administration

    def __init__(self, env):
        self._env = dict(os.environ)
        self._env["XDG_RUNTIME_DIR"], self._env["WAYLAND_DISPLAY"] = env["XDG_RUNTIME_DIR"], env["WAYLAND_DISPLAY"]
        width, height, _ = self._capture()
        if not 1 <= width <= 4096 or not 1 <= height <= 2160:
            raise RuntimeError(t("Résolution Wayland supérieure à 4096 × 2160.", "Wayland resolution larger than 4096 × 2160.", "Resolución Wayland superior a 4096 × 2160."))
        self.width, self.height = width, height
        self._uinput = UInput(width, height)

    def _capture(self):
        try:
            proc = subprocess.run(["grim", "-t", "ppm", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env, timeout=10)
        except FileNotFoundError:
            raise RuntimeError(t("grim est requis pour le bureau distant Wayland (installez le paquet « grim » sur ce poste).",
                                 "grim is required for the Wayland remote desktop (install the \"grim\" package on this machine).",
                                 "grim es necesario para el escritorio remoto Wayland (instale el paquete «grim» en este equipo)."))
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(t("Capture Wayland impossible : %s", "Wayland capture failed: %s", "Captura Wayland imposible: %s") % e)
        if proc.returncode != 0 or not proc.stdout:
            detail = proc.stderr.decode("utf-8", "replace").strip()[-200:]
            raise RuntimeError(t("Capture Wayland impossible : le compositeur ne gère peut-être pas wlr-screencopy (GNOME/KDE exigent une session X11). %s",
                                 "Wayland capture failed: the compositor may not support wlr-screencopy (GNOME/KDE require an X11 session). %s",
                                 "Captura Wayland imposible: el compositor quizá no admite wlr-screencopy (GNOME/KDE requieren una sesión X11). %s") % detail)
        return _parse_ppm(proc.stdout)

    def grab(self):
        width, height, rgb = self._capture()
        bgrx = _rgb_to_bgrx(rgb)
        if (width, height) == (self.width, self.height):
            return bgrx
        out = bytearray(self.width * self.height * 4)            # résolution changée en session : rognage/complément vers la taille négociée
        cols = min(width, self.width) * 4
        for y in range(min(height, self.height)):
            out[y * self.width * 4:y * self.width * 4 + cols] = bgrx[y * width * 4:y * width * 4 + cols]
        return bytes(out)

    def key(self, sym, down):
        code = EVDEV_KEYS.get(sym)
        if code is None:
            return False
        self._uinput.key(code, down)
        return True

    def motion(self, x, y):
        self._uinput.motion(x, y)

    def button(self, number, down):
        if number in (4, 5):                                     # molette : une encoche par clic signalé
            if down:
                self._uinput.wheel(1 if number == 4 else -1)
        else:
            self._uinput.button(number, down)

    def set_cursor(self, enabled):
        pass                                                     # curseur local du navigateur conservé (aucune forme renvoyée)

    def cursor(self):
        return None

    def flush(self):
        pass

    def close(self):
        self._uinput.close()


def _rfb_serve(backend):
    """Serveur RFB 3.8 : relais stdin/stdout branché sur noVNC par le portail ; capture et saisie déléguées au backend."""
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    def read(n):
        data = b""
        while len(data) < n:
            part = inp.read(n - len(data))
            if not part:
                raise EOFError()
            data += part
        return data
    def write(data):
        out.write(data)
        out.flush()
    pressed, mask, tight, last_frame, rgb_order, previous = set(), 0, False, 0, False, None
    def interrupted(_signum, _frame):
        raise EOFError()
    # SIGTERM doit aussi relâcher les modificateurs si la console est fermée pendant une frappe.
    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        write(b"RFB 003.008\n")
        if read(12) != b"RFB 003.008\n":
            raise ValueError(t("Version RFB non prise en charge", "Unsupported RFB version", "Versión RFB no admitida"))
        write(b"\x01\x01")
        if read(1) != b"\x01":
            raise ValueError(t("Négociation RFB invalide", "Invalid RFB negotiation", "Negociación RFB no válida"))
        write(b"\0\0\0\0")
        read(1)
        name = socket.gethostname().encode()
        pixel = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        write(struct.pack(">HH", backend.width, backend.height) + pixel + struct.pack(">I", len(name)) + name)
        while True:
            kind = read(1)[0]
            if kind == 0:
                fmt = read(19)[3:]
                if fmt[:10] != pixel[:10] or fmt[10:13] not in (b"\x10\x08\x00", b"\x00\x08\x10"):
                    raise ValueError(t("Format de pixels RFB non pris en charge", "Unsupported RFB pixel format", "Formato de píxeles RFB no admitido"))
                rgb_order = fmt[10] == 0
            elif kind == 2:
                count = struct.unpack(">xH", read(3))[0]
                if count > 128:
                    raise ValueError("Trop d'encodages RFB")
                encodings = struct.unpack(">" + "i" * count, read(count * 4))
                tight = 7 in encodings
                backend.set_cursor(0x574D5664 in encodings)
            elif kind == 3:
                incremental = read(9)[0] != 0
                time.sleep(max(0, backend.interval - (time.monotonic() - last_frame)))
                data = backend.grab()
                write(rfb_frame(data, backend.width, backend.height, previous, incremental, tight, rgb_order, backend.cursor()))
                previous = data
                last_frame = time.monotonic()
            elif kind == 4:
                down, sym = struct.unpack(">BxxI", read(7))
                if backend.key(sym, down):
                    (pressed.add if down else pressed.discard)(sym)
            elif kind == 5:
                next_mask, pos_x, pos_y = struct.unpack(">BHH", read(5))
                backend.motion(min(backend.width - 1, pos_x), min(backend.height - 1, pos_y))
                for bit, number in ((1, 1), (2, 2), (4, 3)):
                    if (mask ^ next_mask) & bit:
                        backend.button(number, bool(next_mask & bit))
                for bit, number in ((8, 4), (16, 5)):
                    if next_mask & bit and not mask & bit:
                        backend.button(number, True)
                        backend.button(number, False)
                mask = next_mask
            elif kind == 6:
                length = struct.unpack(">xxxI", read(7))[0]
                if length > 1024 * 1024:
                    raise ValueError("Presse-papiers trop volumineux")
                read(length)
            else:
                raise ValueError(t("Message RFB non pris en charge", "Unsupported RFB message", "Mensaje RFB no admitido"))
            backend.flush()
    except EOFError:
        pass
    finally:
        for sym in pressed:
            backend.key(sym, False)
        for bit, number in ((1, 1), (2, 2), (4, 3)):
            if mask & bit:
                backend.button(number, False)
        backend.flush()
        backend.close()
        signal.signal(signal.SIGTERM, previous_term)


def linux_desktop(terminal_fallback=False):
    """Serveur RFB 3.8 intégré, Tight/RGB compressé, sans port VNC : X11/XTest, ou Wayland via grim + périphérique d'entrée virtuel."""
    try:
        env = x11_environment()
    except RuntimeError as e:
        # Wayland est un bureau existant, pas une machine sans interface graphique.
        if "Wayland" in str(e):
            return _rfb_serve(WaylandBackend(wayland_environment()))
        if not terminal_fallback:
            raise RuntimeError(t("Aucun bureau X11. Le droit Terminal distant est requis pour ouvrir la console texte noVNC.", "No X11 desktop. The Remote terminal permission is required to open the noVNC text console.", "Ningún escritorio X11. Se requiere el permiso Terminal remoto para abrir la consola de texto noVNC."))
        return text_console()
    return _rfb_serve(X11Backend(env))


# --------------------------------------------------------------------------- gestionnaire de fichiers

# Trame : [u32 longueur en-tête][u32 longueur données] + en-tête JSON + données binaires.
# Les requêtes du navigateur et les réponses de l'agent empruntent le même relais d'octets que les terminaux.
FILE_HEADER_MAX = 64 * 1024
FILE_DATA_MAX = 1024 * 1024
FILE_CHUNK = 256 * 1024
FILE_LIST_MAX = 5000


def _readn(fd, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _writeall(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _read_frame(fd):
    prefix = _readn(fd, 8)
    if not prefix:
        return None                                  # fin de flux propre : le navigateur a fermé la session
    if len(prefix) < 8:
        raise ValueError("flux interrompu")
    hlen, dlen = struct.unpack(">II", prefix)
    if hlen > FILE_HEADER_MAX or dlen > FILE_DATA_MAX:
        raise ValueError("trame trop grande")
    header, data = _readn(fd, hlen), _readn(fd, dlen)
    if len(header) < hlen or len(data) < dlen:
        raise ValueError("flux interrompu")
    return json.loads(header.decode()), data


def _write_frame(fd, header, data=b""):
    blob = json.dumps(header, ensure_ascii=False).encode()
    _writeall(fd, struct.pack(">II", len(blob), len(data)) + blob + bytes(data))


class FileSession:
    """Opérations de fichiers exécutées en root sur le poste, pour le gestionnaire du portail.

    Chaque opération renvoie (en-tête, données) ; « read » porte un fragment du fichier, « write » reçoit un
    fragment d'un téléversement écrit dans un fichier temporaire renommé à la fin (remplacement atomique)."""
    def __init__(self):
        self.uploads = {}                            # chemin -> (fichier temporaire ouvert, nom temporaire)

    def cleanup(self):
        for handle, tmp in self.uploads.values():
            try:
                handle.close()
                os.unlink(tmp)
            except OSError:
                pass
        self.uploads.clear()

    def handle(self, header, data):
        op = header.get("op")
        method = getattr(self, "op_" + op, None) if isinstance(op, str) and op.isidentifier() else None
        if method is None:
            raise ValueError(t("opération inconnue", "unknown operation", "operación desconocida"))
        return method(header, data)

    # ------------------------------------------------------------------ inspection
    def _entry(self, name, full):
        link = os.path.islink(full)
        try:
            st = os.stat(full)                       # suit le lien : un lien vers un dossier se parcourt
            mode = st.st_mode
            kind = "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            try:
                st = os.lstat(full)
                mode, size, mtime = st.st_mode, st.st_size, int(st.st_mtime)
            except OSError:
                mode, size, mtime = 0, 0, 0
            kind = "other"
        return {"name": name, "type": kind, "size": size, "mtime": mtime, "mode": stat.S_IMODE(mode), "link": link}

    def op_hello(self, h, data):
        info = pwd.getpwuid(os.getuid())
        roots = [{"name": t("Racine", "Root", "Raíz"), "path": "/"}, {"name": t("Dossier personnel", "Home folder", "Carpeta personal"), "path": info.pw_dir}]
        return {"os": "linux", "sep": "/", "home": info.pw_dir, "user": info.pw_name,
                "host": socket.gethostname(), "roots": roots}, b""

    def op_list(self, h, data):
        path = os.path.abspath(h["path"])
        names = sorted(os.listdir(path))
        entries = [self._entry(name, os.path.join(path, name)) for name in names[:FILE_LIST_MAX]]
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        parent = None if path == "/" else os.path.dirname(path)
        return {"path": path, "parent": parent, "entries": entries, "truncated": len(names) > FILE_LIST_MAX}, b""

    def op_stat(self, h, data):
        path = os.path.abspath(h["path"])
        reply = {"path": path, "exists": os.path.lexists(path)}
        if reply["exists"]:
            reply.update(self._entry(os.path.basename(path), path))
        return reply, b""

    # ------------------------------------------------------------------ modifications
    def op_mkdir(self, h, data):
        os.mkdir(os.path.abspath(h["path"]))
        return {}, b""

    def op_rename(self, h, data):
        path = os.path.abspath(h["path"])
        dest = os.path.join(os.path.dirname(path), os.path.basename(h["name"]))
        if os.path.lexists(dest):
            raise ValueError(t("Un élément porte déjà ce nom.", "An item already has that name.", "Un elemento ya tiene ese nombre."))
        os.rename(path, dest)
        return {"path": dest}, b""

    def op_move(self, h, data):
        path = os.path.abspath(h["path"])
        dest = os.path.join(os.path.abspath(h["to"]), os.path.basename(path))
        if os.path.lexists(dest):
            raise ValueError(t("Un élément porte déjà ce nom à destination.", "An item already has that name at the destination.", "Un elemento ya tiene ese nombre en el destino."))
        shutil.move(path, dest)
        return {"path": dest}, b""

    def op_copy(self, h, data):
        path = os.path.abspath(h["path"])
        dest = os.path.join(os.path.abspath(h["to"]), os.path.basename(path))
        if os.path.lexists(dest):
            raise ValueError(t("Un élément porte déjà ce nom à destination.", "An item already has that name at the destination.", "Un elemento ya tiene ese nombre en el destino."))
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.copytree(path, dest, symlinks=True)
        else:
            shutil.copy2(path, dest, follow_symlinks=False)
        return {"path": dest}, b""

    def op_delete(self, h, data):
        path = os.path.abspath(h["path"])
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        return {}, b""

    def op_chmod(self, h, data):
        os.chmod(os.path.abspath(h["path"]), int(h["mode"]) & 0o7777)
        return {}, b""

    # ------------------------------------------------------------------ transfert
    def op_read(self, h, data):
        path = os.path.abspath(h["path"])
        offset = max(0, int(h.get("offset", 0)))
        length = max(0, min(int(h.get("length", FILE_CHUNK)), FILE_CHUNK))
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(offset)
            chunk = f.read(length)
        return {"offset": offset, "size": size, "eof": offset + len(chunk) >= size}, chunk

    def op_write(self, h, data):
        path = os.path.abspath(h["path"])
        offset = max(0, int(h.get("offset", 0)))
        entry = self.uploads.get(path)
        if offset == 0:
            if entry:
                entry[0].close()
            tmp = path + ".loco-part"
            entry = (open(tmp, "wb"), tmp)
            self.uploads[path] = entry
        elif entry is None:
            raise ValueError(t("Téléversement sans début.", "Upload without a start.", "Subida sin inicio."))
        handle = entry[0]
        handle.seek(offset)
        handle.write(data)
        if h.get("final"):
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(entry[1], path)
            self.uploads.pop(path, None)
        return {"offset": offset, "written": len(data), "final": bool(h.get("final"))}, b""

    def op_write_cancel(self, h, data):
        path = os.path.abspath(h["path"])
        entry = self.uploads.pop(path, None)
        if entry:
            entry[0].close()
            try:
                os.unlink(entry[1])
            except OSError:
                pass
        return {}, b""


def files_service():
    session = FileSession()
    try:
        while True:
            frame = _read_frame(0)
            if frame is None:
                break
            header, data = frame
            rid = header.get("id")
            try:
                reply, payload = session.handle(header, data)
                reply = dict(reply); reply["id"], reply["ok"] = rid, True
            except (OSError, ValueError, KeyError, TypeError) as e:
                reply = {"id": rid, "ok": False, "error": str(e)[:500], "errno": getattr(e, "errno", None)}
                payload = b""
            _write_frame(1, reply, payload)
    finally:
        session.cleanup()
    return 0


def main(argv):
    if os.geteuid() != 0:
        print(t("loco-client : lancez cette commande en root.", "loco-client: run this command as root.", "loco-client: ejecute este comando como root."), file=sys.stderr)
        return 1
    cmd = argv[0] if argv else "run"
    if cmd == "remote":
        return remote_service()
    if cmd == "desktop":
        try:
            linux_desktop(terminal_fallback="--terminal-fallback" in argv[1:])
            return 0
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 1
    if cmd == "files":
        return files_service()
    if cmd == "run":
        return service()
    if cmd == "enroll" and len(argv) == 5 and argv[1] == "--server" and argv[3] == "--key":
        return enroll(argv[2], argv[4])
    if cmd == "status":
        return status_cmd()
    if cmd == "remove":
        conf = load(CONFIG)
        remove_local()
        print(t("Agent loco retiré de ce poste.", "loco agent removed from this machine.", "Agente loco retirado de este equipo.")
              + (t(" Retirez-le aussi de la liste du parc dans le portail.", " Also remove it from the fleet list in the portal.", " Retírelo también de la lista del parque en el portal.") if conf.get("token") else ""))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
