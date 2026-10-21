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


LANG_EN = not _system_lang().lower().startswith("fr")


def t(fr, en):
    """Texte vu par l'admin ou l'utilisateur du poste, dans la langue du poste (anglais par défaut)."""
    return en if LANG_EN else fr



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
            log(t(f"serveur joint à sa nouvelle adresse : {server} (au lieu de {conf['server']})", f"server reached at its new address: {server} (instead of {conf['server']})"))
        if server != conf["server"] or (announced and announced != conf.get("servers")):
            conf["server"] = server
            if announced:
                conf["servers"] = announced
            save(CONFIG, conf)
        return answer
    raise failure or OSError(t("aucune adresse de serveur connue", "no known server address"))


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
    return {"hostname": socket.gethostname(), "agent_version": VERSION, "addresses": addresses(),
            "os": {"family": family(), "name": rel.get("PRETTY_NAME") or platform.platform()}}


def run(cmd, timeout=7200):
    """(code, sortie) ; la sortie est tronquée au début pour garder la fin, la plus utile."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           env=ENV, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable\n"
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else t("délai dépassé\n", "timed out\n")


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
        inv["error"] = t("gestionnaire de paquets non reconnu", "unrecognised package manager")
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


# --------------------------------------------------------------------------- service

def report(conf, cmd_id, state, output="", code=None, inventory=None):
    data = {"id": cmd_id, "state": state, "output": output[-OUTPUT_LIMIT:], "exit_code": code}
    if inventory is not None:
        data["inventory"] = inventory
    try:
        call(conf["server"], "/agent/api/result", data, conf["token"])
    except (OSError, ValueError) as e:
        log(t(f"résultat non transmis : {e}", f"result not sent: {e}"))


def run_script(cmd):
    """Script de la bibliothèque loco : contenu vérifié par son empreinte, exécuté en root (sh, ou l'interpréteur du #!)."""
    script = cmd.get("script") or {}
    content = script.get("content") or ""
    if script.get("platform") != "linux":
        return 1, t("Script destiné à un autre système.", "Script intended for another system.")
    if hashlib.sha256(content.encode()).hexdigest() != (script.get("sha256") or "").lower():
        return 1, t("Empreinte du script invalide : non exécuté.", "Invalid script fingerprint: not run.")
    fd, path = tempfile.mkstemp(prefix="loco-script-", suffix=".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(path, 0o700)
        argv = [path] if content.startswith("#!") else ["/bin/sh", path]
        try:
            done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=1800, cwd="/")
        except subprocess.TimeoutExpired as e:
            return -1, ((e.output or b"").decode("utf-8", "replace") + t("\nArrêté : pas terminé après 1800 s.", "\nStopped: not finished after 1800 s.")).strip()
        except OSError as e:
            return 1, t(f"Exécution impossible : {e}", f"Cannot run: {e}")
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
        log(t(f"mise à jour de l'agent impossible : {e}", f"agent update failed: {e}"))
        return
    if f'VERSION = "{wanted}"' not in code:
        return
    tmp = PROGRAM + ".new"
    with open(tmp, "w") as f:
        f.write(code)
    os.chmod(tmp, 0o755)
    os.replace(tmp, PROGRAM)
    log(t(f"agent mis à jour : {VERSION} -> {wanted}", f"agent updated: {VERSION} -> {wanted}"))


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
        log(t("poste non inscrit : relancez la commande d'installation de l'agent.", "machine not enrolled: run the agent install command again."))
        return 1
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    ensure_remote_service()
    state = load(STATE)
    data = identity()
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
            log(t("jeton refusé par le serveur (poste retiré du parc ?) : réinstallez l'agent pour le réinscrire.", "token refused by the server (machine removed from the fleet?): reinstall the agent to re-enroll it."))
        else:
            log(t(f"serveur : HTTP {e.code}", f"server: HTTP {e.code}"))
        save(STATE, state)
        return 1
    except (OSError, ValueError) as e:
        log(t(f"serveur injoignable : {e}", f"server unreachable: {e}"))
        # Inventaire non transmis : à refaire au prochain contact.
        if "inventory" in data:
            state["rescan"] = True
        save(STATE, state)
        return 1
    state["scan_hours"] = answer.get("scan_hours", 6)
    for cmd in answer.get("commands", []):
        action = cmd.get("action")
        log(t(f"action {cmd.get('id')} : {action}", f"action {cmd.get('id')}: {action}"))
        report(conf, cmd["id"], "running")
        if action == "scan":
            inv = scan()
            state["last_scan"] = time.time()
            report(conf, cmd["id"], "success" if not inv["error"] else "failed", inv["error"] or t("Inventaire transmis.", "Inventory sent."),
                   0, inv)
        elif action == "update":
            code, out = update()
            inv = scan()
            state["last_scan"] = time.time()
            reboot = cmd.get("reboot") and inv["reboot_required"] and code == 0
            report(conf, cmd["id"], "success" if code == 0 else "failed",
                   out + (t("\nRedémarrage dans une minute.", "\nRebooting in one minute.") if reboot else ""), code, inv)
            if reboot:
                save(STATE, state)
                subprocess.run(["shutdown", "-r", "+1", t("loco : redémarrage après mise à jour", "loco: reboot after update")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return 0
        elif action == "run-script":
            code, out = run_script(cmd)
            report(conf, cmd["id"], "success" if code == 0 else "failed", out, code)
        elif action == "reboot":
            report(conf, cmd["id"], "success", t("Redémarrage dans une minute.", "Rebooting in one minute."), 0)
            save(STATE, state)
            subprocess.run(["shutdown", "-r", "+1", t("loco : redémarrage demandé", "loco: reboot requested")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return 0
        elif action == "uninstall":
            report(conf, cmd["id"], "success", t("Agent retiré du poste.", "Agent removed from the machine."), 0)
            remove_local()
            log(t("agent retiré à la demande du serveur.", "agent removed at the server's request."))
            return 0
        else:
            report(conf, cmd["id"], "failed", t(f"Action non prise en charge sur Linux : {action}", f"Action not supported on Linux: {action}"), 1)
    save(STATE, state)
    self_update(conf, answer.get("agent_version"))
    return 0


def enroll(server, key):
    os.makedirs(ETC, mode=0o700, exist_ok=True)
    conf = load(CONFIG)
    uid = conf.get("uid") or uuid.uuid4().hex
    data = identity()
    if not data["os"]["family"]:
        log(t("système non pris en charge (APT ou DNF requis).", "unsupported system (APT or DNF required)."))
        return 1
    data.update(key=key, uid=uid)
    try:
        answer = call(server, "/agent/api/enroll", data)
    except urllib.error.HTTPError as e:
        log(t("clé d'inscription refusée.", "enrollment key refused.") if e.code == 403 else t(f"inscription refusée : HTTP {e.code}", f"enrollment refused: HTTP {e.code}"))
        return 1
    except (OSError, ValueError) as e:
        log(t(f"serveur injoignable : {e}", f"server unreachable: {e}"))
        return 1
    save(CONFIG, {"server": server.rstrip("/"), "uid": uid, "token": answer["token"],
                  "enrolled": datetime.datetime.now().isoformat(timespec="seconds")})
    save(STATE, {"rescan": True})
    log(t(f"poste inscrit auprès de {server}.", f"machine enrolled with {server}."))
    return 0


def status_cmd():
    conf = load(CONFIG)
    state = load(STATE)
    scanned = state.get("last_scan")
    rows = [(t("Agent", "Agent"), f"loco-client {VERSION}"),
            (t("Serveur", "Server"), conf.get("server") or t("non inscrit", "not enrolled"))]
    others = known_servers(conf)[1:] if conf.get("server") else []
    if others:
        rows.append((t("De secours", "Fallback"), " ".join(others)))
    rows.append((t("Identifiant", "ID"), conf.get("uid", "-")))
    rows.append((t("Inventaire", "Inventory"),
                 datetime.datetime.fromtimestamp(scanned).isoformat(timespec="seconds") if scanned else t("jamais", "never")))
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
                raise ValueError(t("Type de session inconnu", "Unknown session type"))
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
                self.error = self.error or t("Session terminée.", "Session ended.")
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
                    raise ValueError(t("Adresse du serveur invalide", "Invalid server address"))
                self.key = key
            self.connection.request("POST", parsed.path.rstrip("/") + "/agent/api/remote", json.dumps(data).encode(),
                                    {"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                     "User-Agent": "loco-client/" + VERSION})
            response = self.connection.getresponse()
            body = response.read(4 * 1024 * 1024 + 1)
            if response.status != 200:
                raise urllib.error.HTTPError(server, response.status, response.reason, response.headers, None)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError(t("Réponse distante trop volumineuse", "Remote response too large"))
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
                    answer = transport.exchange(conf["server"], {"sessions": [s.payload() for s in sessions.values()]}, conf["token"])
                    last_ok = time.monotonic()
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
    raise RuntimeError(t("Bureau Wayland non pris en charge ; une session X11 est nécessaire.", "Wayland desktop not supported; an X11 session is required.") if wayland else
                       t("Aucune session graphique X11 ouverte sur ce poste.", "No X11 graphical session open on this machine."))


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
                    raise RuntimeError(t("Bibliothèque de console altérée", "Console library tampered with"))
            sys.path.insert(0, path)
            from console_rfb import serve
            return serve(rfb_frame)
    from console_rfb import serve
    return serve(rfb_frame)


def linux_desktop(terminal_fallback=False):
    """Serveur RFB 3.8 intégré, Tight/RGB compressé, directement sur X11/XTest (aucun port VNC)."""
    try:
        env = x11_environment()
    except RuntimeError as e:
        # Wayland est un bureau existant, pas une machine sans interface graphique.
        if "Wayland" in str(e):
            raise
        if not terminal_fallback:
            raise RuntimeError(t("Aucun bureau X11. Le droit Terminal distant est requis pour ouvrir la console texte noVNC.", "No X11 desktop. The Remote terminal permission is required to open the noVNC text console."))
        return text_console()
    os.environ["DISPLAY"] = env["DISPLAY"]
    if env.get("XAUTHORITY"):
        os.environ["XAUTHORITY"] = env["XAUTHORITY"]
    x, xt = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
    def bind(lib, name, result, args):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
        return fn
    ptr, integer, ulong = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
    display = bind(x, "XOpenDisplay", ptr, [ctypes.c_char_p])(env["DISPLAY"].encode())
    if not display:
        raise RuntimeError(t("La session X11 n'est pas accessible à l'agent.", "The X11 session is not accessible to the agent."))
    root = bind(x, "XDefaultRootWindow", ulong, [ptr])(display)
    screen = bind(x, "XDefaultScreen", integer, [ptr])(display)
    width = bind(x, "XDisplayWidth", integer, [ptr, integer])(display, screen)
    height = bind(x, "XDisplayHeight", integer, [ptr, integer])(display, screen)
    if not 1 <= width <= 4096 or not 1 <= height <= 2160:
        raise RuntimeError(t("Résolution X11 supérieure à 4096 × 2160.", "X11 resolution larger than 4096 × 2160."))
    get_image = bind(x, "XGetImage", ctypes.POINTER(XImage), [ptr, ulong, integer, integer, ctypes.c_uint, ctypes.c_uint, ulong, integer])
    destroy = bind(x, "XDestroyImage", integer, [ctypes.POINTER(XImage)])
    keycode = bind(x, "XKeysymToKeycode", ctypes.c_ubyte, [ptr, ulong])
    key = bind(xt, "XTestFakeKeyEvent", integer, [ptr, ctypes.c_uint, integer, ulong])
    button = bind(xt, "XTestFakeButtonEvent", integer, [ptr, ctypes.c_uint, integer, ulong])
    motion = bind(xt, "XTestFakeMotionEvent", integer, [ptr, integer, integer, integer, ulong])
    flush = bind(x, "XFlush", integer, [ptr])
    xfree = bind(x, "XFree", integer, [ptr])
    try:
        # Forme du curseur (XFixes) : facultative, le bureau reste utilisable sans elle.
        get_cursor = bind(ctypes.CDLL("libXfixes.so.3"), "XFixesGetCursorImage", ctypes.POINTER(XFixesCursorImage), [ptr])
    except OSError:
        get_cursor = None
    close = bind(x, "XCloseDisplay", integer, [ptr])
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
    pressed, mask, tight, last_frame, rgb_order = set(), 0, False, 0, False
    cursor_shape, cursor_serial = False, None

    def cursor_change():
        nonlocal cursor_serial
        if not (cursor_shape and get_cursor):
            return None
        image = get_cursor(display)
        if not image:
            return None
        try:
            im = image.contents
            if im.cursor_serial == cursor_serial or not 1 <= im.width <= 256 or not 1 <= im.height <= 256:
                return None
            cursor_serial = im.cursor_serial
            return cursor_rect(im.xhot, im.yhot, im.width, im.height, im.pixels[:im.width * im.height])
        finally:
            xfree(image)
    previous = None
    def interrupted(_signum, _frame):
        raise EOFError()
    # SIGTERM doit aussi relâcher les modificateurs si la console est fermée pendant une frappe.
    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        write(b"RFB 003.008\n")
        if read(12) != b"RFB 003.008\n":
            raise ValueError(t("Version RFB non prise en charge", "Unsupported RFB version"))
        write(b"\x01\x01")
        if read(1) != b"\x01":
            raise ValueError(t("Négociation RFB invalide", "Invalid RFB negotiation"))
        write(b"\0\0\0\0")
        read(1)
        name = socket.gethostname().encode()
        pixel = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        write(struct.pack(">HH", width, height) + pixel + struct.pack(">I", len(name)) + name)
        while True:
            kind = read(1)[0]
            if kind == 0:
                fmt = read(19)[3:]
                if fmt[:10] != pixel[:10] or fmt[10:13] not in (b"\x10\x08\x00", b"\x00\x08\x10"):
                    raise ValueError(t("Format de pixels RFB non pris en charge", "Unsupported RFB pixel format"))
                rgb_order = fmt[10] == 0
            elif kind == 2:
                count = struct.unpack(">xH", read(3))[0]
                if count > 128:
                    raise ValueError("Trop d'encodages RFB")
                encodings = struct.unpack(">" + "i" * count, read(count * 4))
                tight, cursor_shape, cursor_serial = 7 in encodings, 0x574D5664 in encodings, None
            elif kind == 3:
                incremental = read(9)[0] != 0
                time.sleep(max(0, 0.04 - (time.monotonic() - last_frame)))
                image = get_image(display, root, 0, 0, width, height, ctypes.c_ulong(-1).value, 2)
                if not image:
                    raise RuntimeError(t("Capture de la session X11 impossible", "Cannot capture the X11 session"))
                try:
                    im = image.contents
                    if im.bits_per_pixel != 32 or im.byte_order != 0 or (im.red_mask, im.green_mask, im.blue_mask) != (0xff0000, 0xff00, 0xff):
                        raise RuntimeError(t("Format X11 non pris en charge (RGB 24/32 bits requis)", "Unsupported X11 format (RGB 24/32-bit required)"))
                    data = ctypes.string_at(im.data, im.bytes_per_line * height)
                    if im.bytes_per_line != width * 4:
                        data = b"".join(data[y * im.bytes_per_line:y * im.bytes_per_line + width * 4] for y in range(height))
                finally:
                    destroy(image)
                write(rfb_frame(data, width, height, previous, incremental, tight, rgb_order, cursor_change()))
                previous = data
                last_frame = time.monotonic()
            elif kind == 4:
                down, sym = struct.unpack(">BxxI", read(7))
                code = keycode(display, sym)
                if code:
                    key(display, code, bool(down), 0)
                    (pressed.add if down else pressed.discard)(code)
            elif kind == 5:
                next_mask, pos_x, pos_y = struct.unpack(">BHH", read(5))
                motion(display, screen, min(width - 1, pos_x), min(height - 1, pos_y), 0)
                for bit, number in ((1, 1), (2, 2), (4, 3)):
                    if (mask ^ next_mask) & bit:
                        button(display, number, bool(next_mask & bit), 0)
                for bit, number in ((8, 4), (16, 5)):
                    if next_mask & bit and not mask & bit:
                        button(display, number, 1, 0)
                        button(display, number, 0, 0)
                mask = next_mask
            elif kind == 6:
                length = struct.unpack(">xxxI", read(7))[0]
                if length > 1024 * 1024:
                    raise ValueError("Presse-papiers trop volumineux")
                read(length)
            else:
                raise ValueError(t("Message RFB non pris en charge", "Unsupported RFB message"))
            flush(display)
    except EOFError:
        pass
    finally:
        for code in pressed:
            key(display, code, 0, 0)
        for bit, number in ((1, 1), (2, 2), (4, 3)):
            if mask & bit:
                button(display, number, 0, 0)
        flush(display)
        close(display)
        signal.signal(signal.SIGTERM, previous_term)


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
            raise ValueError(t("opération inconnue", "unknown operation"))
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
        roots = [{"name": t("Racine", "Root"), "path": "/"}, {"name": t("Dossier personnel", "Home folder"), "path": info.pw_dir}]
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
            raise ValueError(t("Un élément porte déjà ce nom.", "An item already has that name."))
        os.rename(path, dest)
        return {"path": dest}, b""

    def op_move(self, h, data):
        path = os.path.abspath(h["path"])
        dest = os.path.join(os.path.abspath(h["to"]), os.path.basename(path))
        if os.path.lexists(dest):
            raise ValueError(t("Un élément porte déjà ce nom à destination.", "An item already has that name at the destination."))
        shutil.move(path, dest)
        return {"path": dest}, b""

    def op_copy(self, h, data):
        path = os.path.abspath(h["path"])
        dest = os.path.join(os.path.abspath(h["to"]), os.path.basename(path))
        if os.path.lexists(dest):
            raise ValueError(t("Un élément porte déjà ce nom à destination.", "An item already has that name at the destination."))
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
            raise ValueError(t("Téléversement sans début.", "Upload without a start."))
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
        print(t("loco-client : lancez cette commande en root.", "loco-client: run this command as root."), file=sys.stderr)
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
        print(t("Agent loco retiré de ce poste.", "loco agent removed from this machine.")
              + (t(" Retirez-le aussi de la liste du parc dans le portail.", " Also remove it from the fleet list in the portal.") if conf.get("token") else ""))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
