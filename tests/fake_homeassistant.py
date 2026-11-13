"""Faux Home Assistant pour les essais : une commande `ha` qui répond comme la vraie (mêmes formes de JSON, relevées sur
un Home Assistant OS réel) et tient son état dans un dossier, et un faux client `ssh` qui la lance sur place.

    install(dossier) -> chemin du faux ssh ; l'état est dans dossier/state (un fichier JSON par relevé).

La commande `ha` sert aussi telle quelle dans un conteneur muni d'un vrai serveur SSH (banc d'essai de bout en bout) :
python3 tests/fake_homeassistant.py DOSSIER y écrit `ha` et l'état de départ.
"""
import base64
import json
import os
import stat
import sys

_BLOB = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
HOST_KEY = base64.b64encode(_BLOB + bytes(range(32))).decode()            # clé d'hôte du faux Home Assistant
OTHER_KEY = base64.b64encode(_BLOB + bytes(range(32, 64))).decode()       # celle d'un module SSH réinstallé (ou d'un autre)

STATE = {
    "info": {"supervisor": "2026.11.0", "homeassistant": "2026.11.1", "hassos": "18.4", "hostname": "maison",
             "operating_system": "Home Assistant OS 18.4", "machine": "qemux86-64", "arch": "amd64", "state": "running",
             "supported": True, "channel": "stable", "timezone": "Europe/Paris"},
    "core info": {"version": "2026.11.1", "version_latest": "2026.11.2", "update_available": True, "machine": "qemux86-64",
                  "ip_address": "172.30.32.1", "arch": "amd64", "boot": True, "port": 8123, "ssl": False, "watchdog": True},
    "os info": {"version": "18.4", "version_latest": "18.5", "version_pending": None, "update_available": True,
                "board": "ova", "boot": "A", "data_disk": "QEMU-HARDDISK"},
    "supervisor info": {"version": "2026.11.0", "version_latest": "2026.11.0", "update_available": False, "channel": "stable",
                        "arch": "amd64", "supported": True, "healthy": True, "auto_update": True},
    "host info": {"agent_version": "1.8.1", "chassis": "vm", "virtualization": "kvm", "deployment": "production",
                  "disk_free": 40.2, "disk_total": 62.3, "disk_used": 19.4, "hostname": "maison", "kernel": "6.12.51-haos",
                  "operating_system": "Home Assistant OS 18.4", "timezone": "Europe/Paris"},
    "addons": {"addons": [
        {"name": "Mosquitto broker", "slug": "core_mosquitto", "description": "MQTT", "version": "6.5.0", "version_latest": "6.5.1",
         "update_available": True, "available": True, "state": "started", "repository": "core"},
        {"name": "Advanced SSH & Web Terminal", "slug": "a0d7b954_ssh", "description": "SSH", "version": "22.0.3",
         "version_latest": "22.0.3", "update_available": False, "available": True, "state": "started", "repository": "a0d7b954"},
        {"name": "Zigbee2MQTT", "slug": "45df7312_zigbee2mqtt", "description": "Zigbee", "version": "2.6.3-1",
         "version_latest": "2.6.3-1", "update_available": False, "available": True, "state": "stopped", "repository": "45df7312"},
    ]},
    "backups": {"backups": [
        {"slug": "1f3a9c20", "name": "Automatique", "date": "2026-11-07T03:00:12.168778+00:00", "type": "full", "size": 412.6,
         "size_bytes": 432642457, "protected": True, "compressed": True},
        {"slug": "9b2e4d71", "name": "core_2026.11.0", "date": "2026-11-02T10:09:18.168778+00:00", "type": "partial", "size": 0.01,
         "size_bytes": 10240, "protected": False, "compressed": True},
    ], "days_until_stale": 30},
    "resolution info": {"unsupported": [], "unhealthy": [], "suggestions": [], "checks": [],
                        "issues": [{"type": "no_current_backup", "context": "system", "reference": None, "uuid": "5b0c"}]},
}

# La commande `ha` : relevés (--raw-json) lus dans l'état, opérations qui le modifient, tout appel noté dans commands.log.
HA = r'''#!/usr/bin/env python3
import datetime, json, os, sys, time
STATE = os.environ.get("FAKE_HA_STATE") or "__STATE__"
ALIASES = {"apps": "addons", "app": "addons", "addon": "addons", "backup": "backups"}


def load(name):
    with open(os.path.join(STATE, name.replace(" ", "-") + ".json")) as f:
        return json.load(f)


def save(name, data):
    path = os.path.join(STATE, name.replace(" ", "-") + ".json")
    with open(path + ".new", "w") as f:
        json.dump(data, f)
    os.replace(path + ".new", path)


def flag(name):
    return os.path.exists(os.path.join(STATE, name))


def backup(name, kind):
    data = load("backups")
    data["backups"].append({"slug": "%08x" % (int(time.time() * 1000) & 0xffffffff), "name": name, "type": kind, "size": 1.5,
                            "date": datetime.datetime.now(datetime.timezone.utc).isoformat(), "size_bytes": 1572864,
                            "protected": False, "compressed": True})
    save("backups", data)


def reboot():
    """Banc d'essai avec un vrai serveur SSH : la commande donnée (FAKE_HA_REBOOT) fait tomber la machine un instant."""
    if os.environ.get("FAKE_HA_REBOOT"):
        import subprocess
        subprocess.Popen(os.environ["FAKE_HA_REBOOT"], shell=True, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def done(text="Command completed successfully."):
    print(text)
    sys.exit(0)


def fail(text):
    print("Error: " + text)
    sys.exit(1)


with open(os.path.join(STATE, "commands.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
# Commande récente : les modules s'appellent « apps », « addons » est un alias déprécié qui avertit. L'ancienne (marque
# old-cli dans l'état) ne connaît que « addons ».
if sys.argv[1:2] == ["apps"] and flag("old-cli"):
    print("Error: unknown command \"apps\" for \"ha\"")
    sys.exit(1)
if sys.argv[1:2] == ["addons"] and not flag("old-cli"):
    sys.stderr.write("The use of 'addons' is deprecated, please use 'apps' instead!\n")
if "--help" in sys.argv:
    print("Usage:\n  ha " + " ".join(a for a in sys.argv[1:] if a != "--help") + " [flags]")
    sys.exit(0)
args = [ALIASES.get(a, a) for a in sys.argv[1:]]
if not os.environ.get("SUPERVISOR_TOKEN"):
    fail("unauthorized: missing or invalid API token")          # comme la vraie hors d'un shell de connexion
words = [a for a in args if not a.startswith("--")]
options = {a for a in args if a.startswith("--")}
if "--name" in args:
    words.remove(args[args.index("--name") + 1])
section = " ".join(words)
if "--raw-json" in options:
    if flag("noisy"):
        print("Avertissement quelconque de la commande, avant sa réponse")
    try:
        print(json.dumps({"result": "ok", "data": load(section)}))
    except OSError:
        print(json.dumps({"result": "error", "message": "unknown command"}))
        sys.exit(1)
    sys.exit(0)
if flag("slow"):
    time.sleep(float(open(os.path.join(STATE, "slow")).read() or 1))
if section in ("core update", "os update", "supervisor update"):
    part = section.split()[0]
    if flag("fail-" + part):
        fail("Updating %s failed, check the logs" % part)
    data = load(part + " info")
    if "--backup" in options and part == "core":
        backup("core_" + data["version"], "partial")
    data.update(version=data["version_latest"], update_available=False)
    save(part + " info", data)
    if part == "os":
        open(os.path.join(STATE, "reboot"), "w").close()
        reboot()
    done()
if len(words) == 3 and words[:2] == ["addons", "update"]:
    data = load("addons")
    found = [a for a in data["addons"] if a["slug"] == words[2]]
    if not found:
        fail("Addon %s does not exist" % words[2])
    if flag("fail-addon"):
        fail("Can't update add-on %s: image pull failed" % words[2])
    if "--backup" in options:
        backup("addon_%s_%s" % (words[2], found[0]["version"]), "partial")
    found[0].update(version=found[0]["version_latest"], update_available=False)
    save("addons", data)
    done()
if section == "backups new":
    backup(args[args.index("--name") + 1] if "--name" in args else "sans nom", "full")
    done()
if section == "core restart":
    done()
if section == "host reboot":
    open(os.path.join(STATE, "reboot"), "w").close()
    reboot()
    done()
if section == "core logs":
    done("2026-11-13 09:00:01.120 INFO (MainThread) [homeassistant.core] Starting Home Assistant\n"
         "2026-11-13 09:00:04.512 WARNING (MainThread) [homeassistant.components.mqtt] Broker lent\n"
         "2026-11-13 09:00:09.004 INFO (MainThread) [homeassistant.bootstrap] Home Assistant initialized in 8.1s")
fail("unknown command: " + section)
'''

# Le faux ssh : ne se connecte nulle part. Il applique ce que ferait un vrai (clé d'hôte connue ou non, clé acceptée ou
# non, hôte qui ne répond pas) d'après l'état, note ses arguments, puis lance la commande demandée sur place.
SSH = r'''#!/usr/bin/env python3
import json, os, shlex, subprocess, sys
STATE = "__STATE__"
args, options, flags, port, user, identity = sys.argv[1:], {}, [], "22", "", ""
while args and args[0] != "--":
    a = args.pop(0)
    if a == "-o":
        key, _sep, value = args.pop(0).partition("=")
        options[key] = value
    elif a == "-p":
        port = args.pop(0)
    elif a == "-l":
        user = args.pop(0)
    elif a == "-i":
        identity = args.pop(0)
    elif a == "-F":
        options["-F"] = args.pop(0)
    else:
        flags.append(a)
host, command = args[1], (args[2] if len(args) > 2 else "")
with open(os.path.join(STATE, "ssh.log"), "a") as f:
    f.write(json.dumps({"options": options, "flags": flags, "port": port, "user": user, "identity": identity, "host": host,
                        "command": command}) + "\n")


def flag(name):
    return os.path.exists(os.path.join(STATE, name))


def refuse(text):
    sys.stderr.write(text + "\n")
    sys.exit(255)


down = os.path.join(STATE, "down")
if flag("down"):
    left = int(open(down).read() or 0)
    if left > 1:
        open(down, "w").write(str(left - 1))
    elif left == 1:
        os.unlink(down)
    refuse("ssh: connect to host %s port %s: Connection refused" % (host, port))
with open(os.path.join(STATE, "hostkey")) as f:
    hostkey = f.read().strip()
pattern = host if port == "22" else "[%s]:%s" % (host, port)
known = options.get("UserKnownHostsFile", "")
if options.get("PreferredAuthentications") == "none":            # relevé de la clé d'hôte : rien n'est authentifié
    with open(known, "w") as f:
        f.write("%s ssh-ed25519 %s\n" % (pattern, hostkey))
    refuse("%s@%s: Permission denied (publickey,keyboard-interactive)." % (user, host))
try:
    with open(known) as f:
        trusted = f.read()
except OSError:
    trusted = ""
if "%s ssh-ed25519 %s" % (pattern, hostkey) not in trusted:
    refuse("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.")
if flag("denied") or not os.path.isfile(identity) or os.stat(identity).st_mode & 0o077:
    refuse("%s@%s: Permission denied (publickey)." % (user, host))
env = dict(os.environ, PATH=os.path.join(STATE, "bin") + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
           FAKE_HA_STATE=STATE, SUPERVISOR_TOKEN="jeton-du-module", PS1="maison$ ")
if not command:                                                  # terminal : un vrai shell, sur place
    os.execve("/bin/sh", ["sh", "-i"], env)
words = shlex.split(command)
login = words[:2] == ["sh", "-lc"]
if not login:
    env.pop("SUPERVISOR_TOKEN")                                  # hors d'un shell de connexion, `ha` n'a pas son accès
code = subprocess.call(["sh", "-c", words[2] if login else command], env=env)
if flag("reboot"):                                               # le système redémarre : la liaison tombe, puis revient
    os.unlink(os.path.join(STATE, "reboot"))
    open(down, "w").write("1000000" if flag("never-back") else "2")
    refuse("Connection to %s closed by remote host." % host)
sys.exit(code)
'''


def write_state(folder, state=None):
    os.makedirs(folder, exist_ok=True)
    for name, data in (state or STATE).items():
        with open(os.path.join(folder, name.replace(" ", "-") + ".json"), "w") as f:
            json.dump(data, f)


def _script(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def install(folder):
    """Écrit l'état, la commande `ha` et le faux ssh ; renvoie le chemin du faux ssh."""
    state = os.path.join(folder, "state")
    write_state(state)
    os.makedirs(os.path.join(state, "bin"), exist_ok=True)
    with open(os.path.join(state, "hostkey"), "w") as f:
        f.write(HOST_KEY + "\n")
    _script(os.path.join(state, "bin", "ha"), HA.replace("__STATE__", state))
    _script(os.path.join(folder, "ssh"), SSH.replace("__STATE__", state))
    return os.path.join(folder, "ssh")


if __name__ == "__main__":
    target = sys.argv[1]
    write_state(os.path.join(target, "state"))
    _script(os.path.join(target, "ha"), HA.replace("__STATE__", "/var/lib/fake-ha"))
