"""Proxmox VE : gestion du système d'un serveur relié (mises à jour, redémarrage, terminal), en plus de ses sauvegardes.

L'API de Proxmox sait dire quelles mises à jour attendent, pas les installer : son bouton « Upgrade » ouvre un shell
réservé à root@pam connecté, refusé à tout jeton d'API. loco se relie donc aussi par SSH, comme à Home Assistant, avec
une clé qui lui est propre (la clé publique s'ajoute aux clés autorisées de root sur Proxmox, la clé privée ne quitte
pas ce serveur) ; la clé d'hôte n'est jamais acceptée d'office, un administrateur confirme son empreinte. Le jeton
d'API reste celui des sauvegardes, et donne aussi les ressources du serveur montrées dans Mon parc.

Une mise à jour est `apt-get dist-upgrade` — ce que lance l'interface de Proxmox (pveupgrade), jamais `apt-get upgrade`
qu'il déconseille. Elle tourne détachée sur l'hôte (systemd-run) : elle va au bout même si la liaison tombe ou si loco
redémarre — loco peut tourner sur ce Proxmox — et loco en suit le journal et le code de sortie. Un redémarrage de
l'hôte réussit quand Proxmox répond de nouveau avec un autre identifiant de démarrage.
"""
import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time

from flask import abort, flash, g, redirect, render_template, request, session, url_for

from . import app, config, homeassistant, proxmox
from .i18n import L_, _
from .auth import audit, can, login_required, require
from .db import connect, db
from .util import back

KEY_DIR = os.path.join(config.STATE, "proxmox")
KEY_PATH = os.path.join(KEY_DIR, "id_ed25519")
LinkError = homeassistant.LinkError
UNREACHED = homeassistant.UNREACHED
MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
BOOT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KERNEL_PKG = re.compile(r"^(?:proxmox|pve)-kernel-|^proxmox-default-kernel$")
STALE = 120                     # relevé plus ancien : refait en arrière-plan tant que la page est affichée
OVERVIEW_STALE = 1800           # … et tant que Mon parc ou la vue d'ensemble l'est, bien moins souvent
API_STALE = 600                 # ressources (API) montrées dans Mon parc : relevé refait au-delà
QUICK, APT_UPDATE = 40, 600     # secondes : un relevé ; la relecture des dépôts (apt-get update)
POLL, LOST, UPGRADE_MAX = 10, 900, 4 * 3600     # suivi d'une mise à jour : pas, coupure tolérée, durée au plus
BACK, STEP = 1200, 15           # après un redémarrage : retour attendu au plus, pas des essais
FORGOTTEN = 600                 # tâche « en cours » reprise après un arrêt de loco sans avoir rien lancé : abandonnée
MAX_UPDATES = 3000
ACTIONS = {
    "update": L_("Mise à jour du système"),
    "check": L_("Recherche des mises à jour"),
    "reboot": L_("Redémarrage du serveur"),
    "reboot-after": L_("Redémarrage après la mise à jour"),
    "reboot-wait": L_("Redémarrage après les mises à jour du parc"),
    "reboot-needed": L_("Redémarrage si nécessaire, après les mises à jour du parc"),
}
REBOOTS = {"reboot", "reboot-after", "reboot-wait", "reboot-needed"}
# Redémarrages décidés depuis Mon parc : l'hyperviseur arrête toutes ses machines avec lui, loco compris ; il attend
# d'abord la fin des mises à jour en cours sur les postes du parc (pas d'installation coupée en route), une heure au plus.
FLEET_WAIT, FLEET_STEP = 3600, 30
_WORKERS, _REFRESH, _GUARD = {}, set(), threading.Lock()

# Lancement de la mise à jour, détachée de la liaison : script et journal dans /var/lib/loco-upgrade (root seul), code
# de sortie écrit à la fin. Relancer la même tâche ne refait rien (reprise après une coupure ou un redémarrage de loco).
UPGRADE_START = r'''d=/var/lib/loco-upgrade; id=__ID__
mkdir -p "$d" && chmod 700 "$d" || exit 1
if [ -e "$d/$id.rc" ] || [ -e "$d/$id.log" ]; then echo "@@started"; exit 0; fi
cat > "$d/$id.sh" <<'LOCO_UPGRADE'
export LC_ALL=C DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
d=/var/lib/loco-upgrade; id=__ID__
echo "== apt-get update"
apt-get update -q || echo "loco: apt-get update failed, upgrading from the package lists already there"
echo "== apt-get dist-upgrade"
apt-get -y -q -o DPkg::Lock::Timeout=900 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade
rc=$?
echo "== exit code $rc"
echo "$rc" > "$d/$id.rc.new" && mv "$d/$id.rc.new" "$d/$id.rc"
LOCO_UPGRADE
: > "$d/$id.log"
find "$d" -name '*.log' -mtime +30 -delete 2>/dev/null
if command -v systemd-run >/dev/null 2>&1; then
  systemd-run --quiet --collect --unit="loco-upgrade-$id" --description="loco: system upgrade" \
    -p StandardOutput="append:$d/$id.log" -p StandardError="append:$d/$id.log" /bin/sh "$d/$id.sh" || exit 1
else
  setsid nohup /bin/sh "$d/$id.sh" >>"$d/$id.log" 2>&1 </dev/null &
fi
echo "@@started"
'''
UPGRADE_POLL = r'''d=/var/lib/loco-upgrade; id=__ID__
if [ -e "$d/$id.log" ]; then echo "@@log yes"; else echo "@@log no"; fi
if [ -f "$d/$id.rc" ]; then echo "@@rc $(cat "$d/$id.rc")"; fi
if command -v systemctl >/dev/null 2>&1; then echo "@@active $(systemctl is-active "loco-upgrade-$id" 2>/dev/null)"; else echo "@@active none"; fi
echo "@@tail"
tail -c 12000 "$d/$id.log" 2>/dev/null
'''
UPGRADE_DONE = r'''rm -f /var/lib/loco-upgrade/__ID__.sh /var/lib/loco-upgrade/__ID__.rc'''
# Redémarrage différé de quelques secondes : la commande rend la main (et ssh se termine proprement) avant.
REBOOT = (r'''if command -v systemd-run >/dev/null 2>&1; then systemd-run --quiet --on-active=5 --unit="loco-reboot-$$" /bin/systemctl reboot; '''
          r'''else (sleep 5; reboot) </dev/null >/dev/null 2>&1 & fi; echo "@@reboot"''')
# Identifiant de démarrage et durée d'allumage (secondes) : l'un ou l'autre dit qu'un redémarrage a eu lieu (dans un
# conteneur, l'identifiant est celui de l'hôte, seule la durée repart de zéro).
BOOT_ID = 'echo "$(cat /proc/sys/kernel/random/boot_id) $(cut -d. -f1 /proc/uptime)"'


# --------------------------------------------------------------------------- liaison SSH

def public_key():
    """Clé publique de loco pour Proxmox (paire à part de celle de Home Assistant : la retirer de l'un ne touche pas l'autre)."""
    return homeassistant.public_key(KEY_PATH)


def authorized_line():
    """Commande à lancer sur Proxmox (shell du nœud, en root) pour autoriser la clé de loco."""
    return "mkdir -p /root/.ssh && echo " + shlex.quote(public_key()) + " >> /root/.ssh/authorized_keys"


def ssh_host(row):
    return row["ssh_host"] or row["host"]


def _argv(row, known, remote, tty=False):
    host = ssh_host(row)
    return (homeassistant._base(host, row["ssh_port"], row["ssh_user"], known, key=KEY_PATH) + ["-o", "StrictHostKeyChecking=yes"]
            + (["-tt"] if tty else ["-T"]) + ["--", host] + ([remote] if remote else []))


def run(row, script, timeout=QUICK):
    """Lance un script sur Proxmox par SSH : (code de sortie, sortie et erreurs mêlées). 255 : ssh lui-même a échoué."""
    if not homeassistant.ssh_binary():
        raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
    if not row["host_key"]:
        raise LinkError(_("La clé d'hôte de Proxmox n'a pas encore été confirmée."))
    public_key()
    with homeassistant.KnownHosts(KEY_DIR, ssh_host(row), row["ssh_port"], row["host_key"]) as known:
        try:
            done = subprocess.run(_argv(row, known, "sh -c " + shlex.quote(script)), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise LinkError(_("Proxmox n'a pas répondu dans le délai (%(n)s s).", n=timeout)) from e
        except OSError as e:
            raise LinkError(str(e)[:200]) from e
    return done.returncode, done.stdout.decode("utf-8", "replace")


def explain(output):
    """Refus de ssh dit clairement : clé de loco non autorisée, clé d'hôte changée, hôte injoignable."""
    text = output.strip()
    low = text.lower()
    if "permission denied" in low:
        return _("Proxmox refuse la clé de loco : ajoutez-la aux clés autorisées de root (/root/.ssh/authorized_keys).")
    if "host key verification failed" in low or "remote host identification has changed" in low:
        return _("La clé d'hôte de Proxmox n'est plus celle qui a été confirmée : vérifiez qu'il s'agit bien de lui, puis confirmez la nouvelle.")
    return (text.splitlines()[-1] if text else _("liaison SSH interrompue"))[:240]


# --------------------------------------------------------------------------- relevé

def own_macs():
    """Adresses MAC de ce serveur : retrouvées dans la configuration d'une machine de Proxmox, elles disent que loco y tourne."""
    found = set()
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return []
    for name in names:
        if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr", "tap", "fwpr", "fwln")):
            continue
        try:
            with open(f"/sys/class/net/{name}/address", encoding="ascii") as f:
                mac = f.read().strip().lower()
        except OSError:
            continue
        if MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
            found.add(mac)
    return sorted(found)[:8]


def collect_script(macs=()):
    """Relevé en une seule commande, sans rien changer : versions, noyaux, démarrage, et ce que ferait la mise à jour
    (simulation d'apt-get dist-upgrade, sans verrou). Les adresses MAC données repèrent loco parmi les machines."""
    macs = [m for m in macs if MAC_RE.match(m)]
    lines = ['export LC_ALL=C PATH="$PATH:/usr/sbin:/sbin"',
             'echo "@@host $(hostname)"', 'echo "@@kernel $(uname -r)"', 'echo "@@boot $(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"',
             'echo "@@uptime $(cut -d" " -f1 /proc/uptime)"', 'echo "@@pve $(pveversion 2>/dev/null | head -n 1)"',
             'for f in /boot/vmlinuz-*; do [ -e "$f" ] && echo "@@kernelfile ${f#/boot/vmlinuz-}"; done',
             'echo "@@pin $(cat /etc/kernel/proxmox-boot-pin 2>/dev/null)"', 'echo "@@nextpin $(cat /etc/kernel/next-boot-pin 2>/dev/null)"',
             'echo "@@lists $(stat -c %Y /var/lib/apt/lists/*Release 2>/dev/null | sort -n | tail -n 1)"']
    if macs:
        lines.append('for f in /etc/pve/qemu-server/*.conf /etc/pve/lxc/*.conf; do [ -f "$f" ] && grep -qiE '
                     + shlex.quote("|".join(macs)) + ' "$f" && echo "@@self ${f##*/}"; done')
    lines += ['echo "@@sim"', "apt-get -s -q -o Debug::NoLocking=1 dist-upgrade 2>&1", 'echo "@@simrc $?"']
    return "\n".join(lines) + "\n"


def _vkey(version):
    return [int(x) for x in re.findall(r"\d+", version)]


def _clean(value, limit=200):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]


def summarize(text):
    """Relevé lisible d'après la sortie de collect_script."""
    out = {"hostname": "", "kernel": "", "boot": "", "uptime": 0, "pve": "", "kernels": [], "lists": 0, "self": None, "pinned": "", "next_pin": "",
           "updates": [], "removals": [], "held": 0, "sim_error": ""}
    sim, simrc, in_sim = [], 0, False
    for line in text.splitlines():
        if in_sim:
            if line.startswith("@@simrc "):
                in_sim = False
                simrc = int(line[8:].strip() or 0) if line[8:].strip().isdigit() else 1
            else:
                sim.append(line)
            continue
        if line == "@@sim":
            in_sim = True
            continue
        key, _sep, value = line.partition(" ")
        value = value.strip()
        if key == "@@host":
            out["hostname"] = _clean(value, 64)
        elif key == "@@kernel":
            out["kernel"] = _clean(value, 64)
        elif key == "@@boot" and BOOT_RE.match(value):
            out["boot"] = value
        elif key == "@@uptime":
            try:
                out["uptime"] = int(float(value))
            except ValueError:
                pass
        elif key == "@@pve":
            m = re.search(r"pve-manager/([0-9][^/\s]*)", value)
            out["pve"] = m.group(1)[:32] if m else ""
        elif key in ("@@pin", "@@nextpin") and re.fullmatch(r"[0-9][A-Za-z0-9.+~_-]{0,63}", value):
            out["pinned" if key == "@@pin" else "next_pin"] = value
        elif key == "@@kernelfile" and re.fullmatch(r"[0-9][A-Za-z0-9.+~_-]{0,63}", value):
            out["kernels"].append(value)
        elif key == "@@lists" and value.isdigit():
            out["lists"] = int(value)
        elif key == "@@self":
            m = re.fullmatch(r"(\d+)\.conf", value)
            if m and out["self"] is None:
                out["self"] = int(m.group(1))
    for line in sim:
        m = re.match(r"^Inst (\S+) (?:\[([^\]]*)\] )?\((\S+) ([^)]*)\)", line)
        if m and len(out["updates"]) < MAX_UPDATES:
            origin = m.group(4)
            out["updates"].append({"name": _clean(m.group(1), 120), "old": _clean(m.group(2) or "", 80), "new": _clean(m.group(3), 80),
                                   "security": "-security" in origin or "Security" in origin, "fresh": m.group(2) is None})
            continue
        m = re.match(r"^Remv (\S+)", line)
        if m:
            out["removals"].append(_clean(m.group(1), 120))
            continue
        m = re.search(r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded", line)
        if m:
            out["held"] = int(m.group(4))
    if simrc:
        errors = [line for line in sim if line.startswith(("E:", "W:"))] or sim[-3:]
        out["sim_error"] = _clean(" ".join(errors), 400) or _("la simulation d'apt-get a échoué")
    out["kernels"].sort(key=_vkey)
    newest = out["kernels"][-1] if out["kernels"] else ""
    out["newest_kernel"] = newest
    # Noyau du prochain démarrage : celui épinglé (proxmox-boot-tool kernel pin, éventuellement pour ce seul démarrage),
    # sinon le plus récent. Un noyau épinglé qui tourne déjà ne demande aucun redémarrage, même si un plus récent est installé.
    pin = out.get("next_pin") or out.get("pinned") or ""
    out["boot_kernel"] = pin or newest
    if pin:
        out["reboot_required"] = bool(out["kernel"] and pin != out["kernel"])
    else:
        out["reboot_required"] = bool(newest and out["kernel"] and newest != out["kernel"] and _vkey(newest) > _vkey(out["kernel"]))
    out["security"] = sum(1 for u in out["updates"] if u["security"])
    out["kernel_update"] = any(KERNEL_PKG.match(u["name"]) for u in out["updates"])
    out["updates"].sort(key=lambda u: (not u["security"], u["name"]))
    return out


def refresh(host_id, conn=None):
    """Refait le relevé du système d'un Proxmox relié par SSH ; renvoie le message d'erreur, ou une chaîne vide."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
        if row is None or not row["host_key"]:
            return ""
        info, error = row["sys_info"], ""
        try:
            code, output = run(row, collect_script(own_macs()))
            if code == 255:
                error = explain(output)
            else:
                found = summarize(output)
                if not found["pve"]:
                    error = _("Ce serveur n'est pas un Proxmox VE (la commande pveversion n'y existe pas).")
                else:
                    info = json.dumps(found)
        except LinkError as e:
            error = str(e)
        with conn:
            conn.execute("UPDATE proxmox_hosts SET sys_info=?, sys_error=?, sys_checked=? WHERE id=?", (info, error, time.time(), host_id))
        return error
    finally:
        if own:
            conn.close()


def refresh_soon(host_id):
    with _GUARD:
        if host_id in _REFRESH:
            return
        _REFRESH.add(host_id)

    def go():
        try:
            refresh(host_id)
        except Exception:
            pass
        finally:
            with _GUARD:
                _REFRESH.discard(host_id)
    threading.Thread(target=go, name="loco-proxmox-systeme", daemon=True).start()


def sys_info(row):
    try:
        data = json.loads(row["sys_info"]) if row["sys_info"] else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("updates"), list) and "reboot_required" in data else None


# --------------------------------------------------------------------------- tâches

def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def active(conn, host_id):
    return conn.execute("SELECT * FROM proxmox_system_jobs WHERE host_id=? AND state IN ('queued','running') ORDER BY id",
                        (host_id,)).fetchall()


def queue(conn, host_id, action, user=""):
    """Ajoute une tâche, sauf si la même est déjà en attente ou en cours. Renvoie vrai si elle est ajoutée."""
    if any(j["action"] == action for j in active(conn, host_id)):
        return False
    conn.execute("INSERT INTO proxmox_system_jobs (host_id, action, label, created_at, created_by) VALUES (?,?,?,?,?)",
                 (host_id, action, "", _stamp(), user))
    return True


def work_soon(host_id):
    with _GUARD:
        if host_id in _WORKERS:
            return
        worker = threading.Thread(target=_work, args=(host_id,), name="loco-proxmox-taches", daemon=True)
        _WORKERS[host_id] = worker
    worker.start()


def _save_output(conn, job_id, text):
    with conn:
        conn.execute("UPDATE proxmox_system_jobs SET output=? WHERE id=?", (text[-6000:], job_id))


def _age(job):
    try:
        return time.time() - time.mktime(time.strptime(job["started_at"], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def tidy(text):
    """Journal lisible : d'une ligne réécrite sur place (progression de dpkg, « … 55% ») ne reste que l'état final."""
    return "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))


def _poll(out):
    """(log présent, code de sortie ou None, état de l'unité, fin du journal) d'après UPGRADE_POLL."""
    head, _sep, log = out.partition("@@tail\n")
    log = tidy(log)
    exists, rc, unit = False, None, ""
    for line in head.splitlines():
        if line == "@@log yes":
            exists = True
        elif line.startswith("@@rc ") and line[5:].strip().lstrip("-").isdigit():
            rc = int(line[5:].strip())
        elif line.startswith("@@active "):
            unit = line[9:].strip()
    return exists, rc, unit, log


def _upgrade(conn, row, job, resumed):
    """apt-get dist-upgrade détaché sur l'hôte, suivi jusqu'à son code de sortie."""
    jid = str(int(job["id"]))
    script = UPGRADE_POLL.replace("__ID__", jid)
    if resumed:
        code, out = run(row, script)
        if code == 0 and not _poll(out)[0] and _age(job) > FORGOTTEN:
            return "failed", _("Interrompue par l'arrêt de loco avant d'être lancée : rien n'a été changé sur Proxmox.")
    code, out = run(row, UPGRADE_START.replace("__ID__", jid))
    if code != 0 and "@@started" not in out and not (code == 255 and not UNREACHED.search(out)):
        return "failed", explain(out) if code == 255 else out.strip()[-2000:] or _("La mise à jour n'a pas pu être lancée.")
    deadline, lost, missing, gone, last = time.time() + UPGRADE_MAX, None, 0, 0, job["output"] or ""
    while time.time() < deadline:
        time.sleep(POLL)
        try:
            code, out = run(row, script)
        except LinkError:
            code, out = 255, ""
        if code != 0:
            # Liaison coupée (réseau, services de Proxmox relancés par la mise à jour) : la mise à jour continue là-bas.
            lost = lost or time.time()
            if time.time() - lost > LOST:
                return "failed", (last + "\n" + _("Plus de nouvelles de Proxmox depuis un quart d'heure : vérifiez la mise à jour sur le serveur (journal : /var/lib/loco-upgrade/%(id)s.log).", id=jid)).strip()
            continue
        lost = None
        exists, rc, unit, log = _poll(out)
        if log.strip() and log != last:
            last = log
            _save_output(conn, job["id"], log)
        if rc is not None:
            try:
                run(row, UPGRADE_DONE.replace("__ID__", jid))
            except LinkError:
                pass
            return ("success" if rc == 0 else "failed"), log.strip() or last
        missing = 0 if exists else missing + 1
        if missing >= 3:
            return "failed", _("La mise à jour n'a pas démarré sur Proxmox.")
        # Unité systemd partie sans écrire de code de sortie : processus tué, ou serveur redémarré en cours de route.
        gone = gone + 1 if unit not in ("active", "activating", "reloading", "none", "") else 0
        if gone >= 3:
            return "failed", (log.strip() + "\n" + _("Mise à jour interrompue : son processus s'est arrêté sans conclure. Sur Proxmox : dpkg --configure -a puis apt-get dist-upgrade.")).strip()
    return "failed", (last + "\n" + _("Mise à jour toujours en cours au bout de quatre heures : vérifiez-la sur le serveur (journal : /var/lib/loco-upgrade/%(id)s.log).", id=jid)).strip()


def _check(conn, row, job, resumed):
    """Relit les dépôts (apt-get update), comme le bouton « Rafraîchir » de Proxmox."""
    if resumed:
        return "failed", _("Interrompue par l'arrêt de loco : relancez la recherche.")
    code, out = run(row, "export LC_ALL=C DEBIAN_FRONTEND=noninteractive; apt-get update -q 2>&1", timeout=APT_UPDATE)
    if code == 255 and UNREACHED.search(out):
        return "failed", explain(out)
    return ("success" if code == 0 else "failed"), tidy(out).strip()[-4000:]


def _boot(out):
    """(identifiant de démarrage, durée d'allumage en secondes ou None) d'après BOOT_ID."""
    parts = out.split()
    bid = parts[0] if parts and BOOT_RE.match(parts[0]) else ""
    return bid, int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def _reboot(conn, row, job, resumed):
    """Redémarre l'hôte, puis attend qu'il réponde redémarré : autre identifiant de démarrage, ou allumé depuis moins
    longtemps que la demande de redémarrage."""
    before = job["boot_id"]
    if not before:
        if resumed and _age(job) > FORGOTTEN:
            return "failed", _("Interrompu par l'arrêt de loco avant l'envoi : Proxmox n'a pas été redémarré.")
        code, out = run(row, BOOT_ID)
        before = _boot(out)[0]
        if code != 0 or not before:
            return "failed", explain(out) if code == 255 else _("Identifiant de démarrage illisible : redémarrage non lancé.")
        with conn:
            conn.execute("UPDATE proxmox_system_jobs SET boot_id=? WHERE id=?", (before, job["id"]))
        code, out = run(row, REBOOT)
        if code != 0 and "@@reboot" not in out and not (code == 255 and not UNREACHED.search(out)):
            return "failed", explain(out) if code == 255 else out.strip()[-2000:]
    deadline = time.time() + BACK
    while time.time() < deadline:
        time.sleep(STEP)
        try:
            code, out = run(row, BOOT_ID, timeout=25)
        except LinkError:
            continue
        bid, up = _boot(out) if code == 0 else ("", None)
        if bid and (bid != before or up is not None and up < _age(job) + 30):
            return "success", _("Proxmox a redémarré et répond de nouveau.")
    return "failed", _("Proxmox n'a pas répondu dans les vingt minutes qui ont suivi le redémarrage.")


def fleet_busy(conn):
    """Postes du parc dont une mise à jour (ou une montée de version) est en cours, ou va partir — demandée, due, et le
    poste en ligne : un redémarrage de l'hyperviseur couperait une machine virtuelle en pleine installation."""
    from . import fleet
    now, busy = time.strftime("%Y-%m-%d %H:%M:%S"), set()
    for c in conn.execute("SELECT c.device_id, c.state, d.last_seen, d.state AS device_state FROM device_commands c "
                          "JOIN devices d ON d.id=c.device_id WHERE c.action IN ('update', 'os-upgrade') "
                          "AND c.state IN ('queued', 'sent', 'running') AND d.state='active' AND (c.run_after='' OR c.run_after<=?)",
                          (now,)):
        if c["state"] == "running" or fleet.is_online({"last_seen": c["last_seen"], "state": c["device_state"]}):
            busy.add(c["device_id"])
    return len(busy)


def _reboot_fleet(conn, row, job, resumed):
    """Redémarrage décidé depuis Mon parc (sélection, ligne, « redémarrer si nécessaire ») : comme pour un poste, mais
    après la fin des mises à jour en cours sur les postes du parc, une heure au plus. « reboot-needed » ne redémarre que
    si le relevé fait après la mise à jour le demande (nouveau noyau, sans épinglage contraire)."""
    if not job["boot_id"]:                                  # rien d'envoyé encore : une reprise continue d'attendre
        if job["action"] == "reboot-needed":
            info = sys_info(row)
            if info is not None and not info["reboot_required"]:
                return "success", _("Aucun redémarrage nécessaire après la mise à jour.")
        deadline = time.time() + FLEET_WAIT - _age(job)
        busy = fleet_busy(conn)
        while busy and time.time() < deadline:
            _save_output(conn, job["id"], _("En attente de la fin des mises à jour du parc : %(n)s poste en cours.", n=busy) if busy == 1
                         else _("En attente de la fin des mises à jour du parc : %(n)s postes en cours.", n=busy))
            time.sleep(FLEET_STEP)
            busy = fleet_busy(conn)
        resumed = False
    return _reboot(conn, row, job, resumed)


HANDLERS = {"update": _upgrade, "check": _check, "reboot": _reboot, "reboot-after": _reboot,
            "reboot-wait": _reboot_fleet, "reboot-needed": _reboot_fleet}


def _work(host_id):
    """Exécute les tâches d'un serveur l'une après l'autre ; celle restée « en cours » (loco arrêté) est reprise d'abord."""
    conn = connect()
    try:
        while True:
            with conn:
                job = conn.execute("SELECT * FROM proxmox_system_jobs WHERE host_id=? AND state='running' ORDER BY id LIMIT 1",
                                   (host_id,)).fetchone()
                resumed = job is not None
                if job is None:
                    job = conn.execute("SELECT * FROM proxmox_system_jobs WHERE host_id=? AND state='queued' ORDER BY id LIMIT 1",
                                       (host_id,)).fetchone()
                    if job is None:
                        return
                    conn.execute("UPDATE proxmox_system_jobs SET state='running', started_at=? WHERE id=?", (_stamp(), job["id"]))
                    job = conn.execute("SELECT * FROM proxmox_system_jobs WHERE id=?", (job["id"],)).fetchone()
            row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
            state, output = "failed", ""
            try:
                if row is None or not row["host_key"]:
                    raise LinkError(_("Liaison SSH retirée."))
                state, output = HANDLERS[job["action"]](conn, row, job, resumed)
            except LinkError as e:
                output = str(e)
            except Exception as e:          # une tâche ne doit jamais rester « en cours » pour toujours
                output = f"{type(e).__name__}: {e}"[:500]
            with conn:
                conn.execute("UPDATE proxmox_system_jobs SET state=?, output=?, finished_at=? WHERE id=?",
                             (state, output.strip()[-6000:], _stamp(), job["id"]))
                if job["action"] == "update" and state != "success":
                    # « Redémarrer ensuite » ne vaut qu'après une mise à jour réussie.
                    conn.execute("UPDATE proxmox_system_jobs SET state='failed', finished_at=?, output=? WHERE host_id=? "
                                 "AND action IN ('reboot-after', 'reboot-needed') AND state='queued'",
                                 (_stamp(), _("Annulé : la mise à jour qui le précédait a échoué, Proxmox n'a pas été redémarré."), host_id))
            refresh(host_id, conn)
    finally:
        conn.close()
        with _GUARD:
            _WORKERS.pop(host_id, None)
        check = connect()
        try:
            again = check.execute("SELECT 1 FROM proxmox_system_jobs WHERE host_id=? AND state IN ('queued','running')", (host_id,)).fetchone()
        finally:
            check.close()
        if again:
            work_soon(host_id)


def start():
    """Au démarrage du portail : les tâches en attente repartent, et celles restées « en cours » sont reprises — une mise
    à jour a continué sur Proxmox, un redémarrage de l'hôte a pu emporter loco avec lui."""
    from . import ha
    try:
        for name in os.listdir(KEY_DIR):
            if name.startswith(("session-", "known-")):
                os.unlink(os.path.join(KEY_DIR, name))
    except OSError:
        pass
    if ha.skip_background():
        return
    conn = connect()
    try:
        waiting = [r[0] for r in conn.execute("SELECT DISTINCT host_id FROM proxmox_system_jobs WHERE state IN ('queued','running')")]
    except Exception:            # base d'avant la migration : rien à reprendre
        waiting = []
    finally:
        conn.close()
    for host_id in waiting:
        work_soon(host_id)


# --------------------------------------------------------------------------- vues

def _node(api, info):
    """Ressources (API) du nœud géré : celui dont le nom est celui de l'hôte SSH, ou le seul du serveur."""
    nodes = (api or {}).get("node_status") or {}
    name = (info or {}).get("hostname") or ""
    if name in nodes:
        return nodes[name]
    return next(iter(nodes.values())) if len(nodes) == 1 else None


def resources(node, checked):
    """Ressources du nœud au format des lignes de Mon parc (macro resources)."""
    from . import monitoring
    if not node:
        return None
    pct = lambda used, total: round(100 * used / total, 1) if total else None      # noqa: E731
    m = {"cpu": node.get("cpu"), "mem": pct(node.get("mem", 0), node.get("mem_total", 0)),
         "disk": pct(node.get("disk", 0), node.get("disk_total", 0)), "disk_mount": "/",
         "stale": time.time() - (checked or 0) > 2 * API_STALE,
         "age_text": time.strftime("%H:%M", time.localtime(checked)) if checked else "—"}
    m["levels"] = {k: monitoring.level(k, m[k]) for k in ("cpu", "mem", "disk")}
    return m


def state_of(row, info, running):
    """(couleur, libellé) de l'état d'un serveur, pour Mon parc et sa page."""
    if running:
        job = running[0]
        return "orange", ACTIONS[job["action"]].lower() + " · " + (_("en cours") if job["state"] == "running" else _("en attente"))
    if not row["host_key"]:
        return ("red", _("liaison en échec")) if row["error"] else ("grey", _("sauvegardes seulement"))
    if row["sys_error"]:
        return "red", _("liaison SSH en échec")
    if info is None:
        return "grey", _("relevé en cours")
    n = len(info["updates"])
    if n:
        return "orange", _("%(n)s mise à jour", n=n) if n == 1 else _("%(n)s mises à jour", n=n)
    # Redémarrage requis sans rien à installer : « à jour », la note « redémarrage nécessaire » le dit à part, comme
    # pour un poste (Mon parc, vue d'ensemble).
    return "green", _("à jour")


def view(row):
    """Un serveur tel que sa page le montre : relevé du système, ressources (API), tâches en cours et récentes."""
    info = sys_info(row)
    api = proxmox.info(row)
    jobs = db().execute("SELECT * FROM proxmox_system_jobs WHERE host_id=? ORDER BY id DESC LIMIT 12", (row["id"],)).fetchall()
    running = [j for j in reversed(jobs) if j["state"] in ("queued", "running")]
    busy = {j["action"] for j in running}
    node = _node(api, info)
    color, label = state_of(row, info, running)
    guests = (api or {}).get("guests") or []
    me = next((v for v in guests if info and v["vmid"] == info.get("self")), None)
    return {"row": row, "info": info, "api": api, "node": node, "jobs": jobs, "running": running,
            "updating": "update" in busy, "checking": "check" in busy,
            "rebooting": any(j["action"] in REBOOTS and j["state"] == "running" for j in running),
            "reboot_planned": bool(busy & REBOOTS),
            "color": color, "label": label, "guests": guests, "running_guests": sum(1 for v in guests if v["status"] == "running"),
            "self": me or ({"vmid": info["self"], "name": ""} if info and info.get("self") else None),
            "backing_up": bool(proxmox.active(db(), row["id"])),
            "blocked": bool(info and "proxmox-ve" in info["removals"]),
            "fingerprint": homeassistant.fingerprint(row["host_key"].split()[-1]) if row["host_key"] else "",
            "checked": time.strftime("%H:%M", time.localtime(row["sys_checked"])) if row["sys_checked"] else "",
            "lists": time.strftime("%Y-%m-%d %H:%M", time.localtime(info["lists"])) if info and info.get("lists") else ""}


def signature(v):
    import hashlib
    return hashlib.sha256(json.dumps([v["row"]["sys_checked"], v["row"]["sys_error"], bool(v["row"]["host_key"]), v["row"]["checked"],
                                      v["row"]["error"], [(j["id"], j["state"], len(j["output"] or "")) for j in v["jobs"]]]).encode()).hexdigest()[:16]


def fleet_rows():
    """Serveurs Proxmox montrés comme des lignes de Mon parc (fonctionnalité active, compte qui peut les consulter)."""
    from . import features
    if not features.enabled("proxmox") or not can("proxmox.view"):
        return []
    out = []
    for row in db().execute("SELECT * FROM proxmox_hosts WHERE fingerprint!='' OR host_key!='' ORDER BY name COLLATE NOCASE, id").fetchall():
        now = time.time()
        if row["host_key"] and now - (row["sys_checked"] or 0) > OVERVIEW_STALE:
            refresh_soon(row["id"])
        if row["fingerprint"] and now - (row["checked"] or 0) > API_STALE:
            proxmox.refresh_soon(row["id"])
        info, api = sys_info(row), proxmox.info(row)
        running = active(db(), row["id"])
        color, label = state_of(row, info, running)
        updates = len(info["updates"]) if info and row["host_key"] else 0
        out.append({"id": row["id"], "name": row["name"], "address": ssh_host(row), "version": (info or {}).get("pve") or (api or {}).get("version") or "",
                    "node": (info or {}).get("hostname") or "", "updates": updates, "busy": bool(running), "ssh": bool(row["host_key"]),
                    "online": (not row["error"]) if not row["host_key"] else not row["sys_error"],
                    "blocked": bool(info and "proxmox-ve" in info["removals"]),
                    "reboot_required": bool(info and row["host_key"] and info["reboot_required"]),
                    "rebooting": any(j["action"] in REBOOTS for j in running), "updating": any(j["action"] == "update" for j in running),
                    "self": bool(info and info.get("self")),
                    "color": color, "label": label, "mon": resources(_node(api, info), row["checked"])})
    return out


def update_host(host_id, reboot=False, conn=None, user=None):
    """Lance la mise à jour d'un serveur depuis Mon parc (sélection, « Tout mettre à jour », planification), suivie au
    besoin d'un redémarrage s'il devient nécessaire (après les mises à jour du parc). Renvoie le nombre de tâches lancées."""
    conn = conn or db()
    row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
    info = sys_info(row) if row is not None and row["host_key"] else None
    if not info or not info["updates"] or "proxmox-ve" in info["removals"]:
        return 0
    return _launch(row, ["update"] + (["reboot-needed"] if reboot else []), conn, user)


def reboot_host(host_id):
    """Redémarre un serveur depuis Mon parc (sélection, bouton de sa ligne), après les mises à jour en cours du parc.
    Renvoie le nombre de tâches lancées (0 si déjà prévu ou sans liaison SSH)."""
    row = db().execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None or not row["host_key"] or any(j["action"] in REBOOTS for j in active(db(), host_id)):
        return 0
    return _launch(row, ["reboot-wait"])


def pending_updates(conn=None):
    """Serveurs reliés en SSH qui ont des mises à jour à installer et rien en cours : ce que « Tout mettre à jour » et
    la planification du parc lancent."""
    conn = conn or db()
    found = []
    for row in conn.execute("SELECT * FROM proxmox_hosts WHERE host_key!='' ORDER BY name COLLATE NOCASE, id").fetchall():
        info = sys_info(row)
        if info and info["updates"] and "proxmox-ve" not in info["removals"] and not active(conn, row["id"]):
            found.append(row["id"])
    return found


def attention():
    """Pour « À traiter » de la vue d'ensemble : liaison SSH en échec, mises à jour disponibles, redémarrage requis."""
    rows = []
    for row in db().execute("SELECT * FROM proxmox_hosts WHERE host_key!='' ORDER BY name COLLATE NOCASE, id").fetchall():
        if time.time() - (row["sys_checked"] or 0) > OVERVIEW_STALE:
            refresh_soon(row["id"])
        url, info = url_for("proxmox_system_host", host_id=row["id"]), sys_info(row)
        if row["sys_error"]:
            rows.append({"tone": "warn", "url": url, "text": _("Proxmox « %(name)s » : la liaison SSH échoue.", name=row["name"])})
        elif info:
            count = len(info["updates"])
            if count:
                rows.append({"tone": "warn", "url": url, "text":
                             _("Proxmox « %(name)s » : %(n)s mise à jour disponible.", name=row["name"], n=count) if count == 1
                             else _("Proxmox « %(name)s » : %(n)s mises à jour disponibles.", name=row["name"], n=count)})
            if info["reboot_required"]:
                rows.append({"tone": "warn", "url": url, "text": _("Proxmox « %(name)s » : redémarrage requis (noyau %(k)s installé).", name=row["name"], k=info.get("boot_kernel") or info["newest_kernel"])})
    return rows


# --------------------------------------------------------------------------- pages

def _host(host_id):
    return proxmox._host(host_id)


@app.route("/proxmox/<int:host_id>")
@login_required
def proxmox_system_host(host_id):
    row = _host(host_id)
    if row["host_key"] and (time.time() - (row["sys_checked"] or 0) > STALE or (sys_info(row) is None and not row["sys_error"])):
        refresh_soon(row["id"])
    if row["fingerprint"] and time.time() - (row["checked"] or 0) > proxmox.STALE:
        proxmox.refresh_soon(row["id"])
    v = view(row)
    sig = signature(v)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": sig}
    return render_template("proxmox_host.html", v=v, signature=sig, ssh=bool(homeassistant.ssh_binary()), actions=ACTIONS,
                           terminal=homeassistant.terminal_ready(), pending=_pending(host_id),
                           public_key=public_key() if can("proxmox.manage") and not row["host_key"] else "",
                           authorized=authorized_line() if can("proxmox.manage") and not row["host_key"] else "")


def _pending(host_id):
    data = session.get("proxmox_ssh_pending")
    if not isinstance(data, dict) or data.get("host_id") != host_id or time.time() - data.get("at", 0) > 900:
        return None
    return dict(data, fingerprint=homeassistant.fingerprint(data["key"]))


@app.route("/proxmox/<int:host_id>/ssh/link", methods=["POST"])
@login_required
def proxmox_ssh_link(host_id):
    """Première étape : relever la clé d'hôte que présente l'adresse SSH du serveur, et la montrer pour confirmation."""
    row = _host(host_id)
    host = request.form.get("ssh_host", "").strip() or row["host"]
    username = request.form.get("ssh_user", "").strip() or "root"
    try:
        port = int(request.form.get("ssh_port", "22") or 22)
    except ValueError:
        port = 0
    try:
        if not proxmox.HOST_RE.match(host) or host.startswith("-"):
            raise ValueError(_("Adresse SSH invalide : un nom ou une adresse IP, sans port."))
        if not homeassistant.USER_RE.match(username):
            raise ValueError(_("Nom d'utilisateur SSH invalide."))
        if not 1 <= port <= 65535:
            raise ValueError(_("Port SSH invalide."))
        kind, key = homeassistant.probe_host_key(host, port, lambda e: _("Proxmox ne répond pas en SSH à cette adresse : %(e)s", e=e))
    except (ValueError, LinkError) as e:
        flash(str(e), "error")
        return redirect(url_for("proxmox_system_host", host_id=host_id) + "#ssh")
    session["proxmox_ssh_pending"] = {"host_id": host_id, "host": host, "port": port, "username": username, "type": kind, "key": key,
                                      "at": time.time(), "nonce": secrets.token_hex(8), "same": row["host_key"].split()[-1:] == [key]}
    return redirect(url_for("proxmox_system_host", host_id=host_id) + "#ssh")


@app.route("/proxmox/<int:host_id>/ssh/confirm", methods=["POST"])
@login_required
def proxmox_ssh_confirm(host_id):
    """Seconde étape : l'administrateur a comparé l'empreinte ; loco ne parlera qu'à un hôte qui présente cette clé-là."""
    row = _host(host_id)
    data = _pending(host_id)
    session.pop("proxmox_ssh_pending", None)
    if request.form.get("cancel") == "1" or data is None:
        return redirect(url_for("proxmox_system_host", host_id=host_id))
    if not secrets.compare_digest(request.form.get("nonce", ""), data["nonce"]):
        abort(400)
    conn = db()
    conn.execute("UPDATE proxmox_hosts SET ssh_host=?, ssh_port=?, ssh_user=?, host_key=?, sys_error='' WHERE id=?",
                 (data["host"] if data["host"] != row["host"] else "", data["port"], data["username"], f"{data['type']} {data['key']}", host_id))
    audit("proxmox.ssh-liaison", f"{row['name']} ({data['username']}@{data['host']}:{data['port']}), clé d'hôte {data['fingerprint']}", commit=False)
    conn.commit()
    error = refresh(host_id)
    flash(_("Liaison SSH établie : le système de Proxmox se gère maintenant depuis loco.") if not error
          else _("Clé d'hôte enregistrée, mais la liaison échoue : %(e)s", e=error), "ok" if not error else "error")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


@app.route("/proxmox/<int:host_id>/ssh/unlink", methods=["POST"])
@login_required
def proxmox_ssh_unlink(host_id):
    row = _host(host_id)
    conn = db()
    if active(conn, host_id):
        flash(_("Une tâche est en cours sur ce serveur : attendez sa fin avant de retirer la liaison SSH."), "error")
        return redirect(url_for("proxmox_system_host", host_id=host_id))
    close_sessions(conn, host_id, "Liaison SSH retirée.")
    conn.execute("UPDATE proxmox_hosts SET host_key='', ssh_host='', sys_info='', sys_error='', sys_checked=0 WHERE id=?", (host_id,))
    audit("proxmox.ssh-retrait", row["name"], commit=False)
    conn.commit()
    flash(_("Liaison SSH retirée : les sauvegardes continuent par le jeton d'API. Pensez à retirer la clé de loco de /root/.ssh/authorized_keys sur Proxmox."), "ok")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


def close_sessions(conn, host_id, reason):
    from . import remote
    remote.close_server_sessions(conn, f"proxmox:{host_id}", reason)


@app.route("/proxmox/<int:host_id>/system/refresh", methods=["POST"])
@login_required
def proxmox_system_refresh(host_id):
    row = _host(host_id)
    error = refresh(row["id"], db()) if row["host_key"] else ""
    if row["fingerprint"]:
        proxmox.refresh_soon(row["id"])
    if error:
        flash(error, "error")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


def _launch(row, actions, conn=None, user=None):
    """Met des tâches en file et lance leur exécution ; renvoie le nombre ajouté. Hors d'une requête (planification),
    la connexion et l'auteur sont donnés."""
    conn = conn or db()
    user = user or g.user["username"]
    count = 0
    for action in actions:
        if queue(conn, row["id"], action, user):
            audit("proxmox." + action, row["name"], username=user, commit=False)
            count += 1
    conn.commit()
    if count:
        work_soon(row["id"])
    return count


def _ready(row):
    """Mise à jour possible : liaison SSH, relevé lisible, et rien qui retirerait Proxmox lui-même."""
    info = sys_info(row) if row["host_key"] else None
    if not info:
        return None, _("Le système de ce serveur n'a pas encore été relevé : actualisez.")
    if "proxmox-ve" in info["removals"]:
        return None, _("La mise à jour retirerait le paquet proxmox-ve : les dépôts de Proxmox sont sans doute mal réglés. Rien n'est lancé ; vérifiez-les (nœud › Mises à jour › Dépôts).")
    if not info["updates"]:
        return None, _("Aucune mise à jour à installer.")
    return info, ""


@app.route("/proxmox/<int:host_id>/system/update", methods=["POST"])
@login_required
def proxmox_system_update(host_id):
    row = _host(host_id)
    info, error = _ready(row)
    if info is None:
        flash(error, "error")
        return redirect(url_for("proxmox_system_host", host_id=host_id))
    then_reboot = request.form.get("reboot") == "1"
    if then_reboot:
        require("proxmox.restart")
    count = _launch(row, ["update"] + (["reboot-after"] if then_reboot else []))
    flash((_("Mise à jour lancée, puis redémarrage si elle réussit.") if then_reboot else _("Mise à jour lancée.")) if count
          else _("Une mise à jour est déjà en cours."), "ok" if count else "error")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


@app.route("/proxmox/<int:host_id>/fleet-update", methods=["POST"])
@login_required
def proxmox_fleet_update(host_id):
    """Bouton « Mettre à jour » de la ligne Proxmox dans Mon parc : la même mise à jour, puis retour sur Mon parc."""
    row = _host(host_id)
    info, error = _ready(row)
    count = _launch(row, ["update"]) if info else 0
    flash(_("Mise à jour lancée.") if count else error or _("Une mise à jour est déjà en cours."), "ok" if count else "error")
    return back("fleet")


@app.route("/proxmox/<int:host_id>/fleet-reboot", methods=["POST"])
@login_required
def proxmox_fleet_reboot(host_id):
    """Bouton « Redémarrer » de la ligne Proxmox dans Mon parc : redémarrage après les mises à jour en cours du parc."""
    _host(host_id)
    count = reboot_host(host_id)
    flash(_("Redémarrage de Proxmox prévu : il part dès que les mises à jour en cours sur les postes du parc sont finies (une heure au plus).")
          if count else _("Ce redémarrage est déjà prévu."), "ok" if count else "error")
    return back("fleet")


@app.route("/proxmox/<int:host_id>/system/check", methods=["POST"])
@login_required
def proxmox_system_check(host_id):
    row = _host(host_id)
    if not row["host_key"]:
        abort(409)
    count = _launch(row, ["check"])
    flash(_("Recherche des mises à jour lancée.") if count else _("Une recherche est déjà en cours."), "ok" if count else "error")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


@app.route("/proxmox/<int:host_id>/system/reboot", methods=["POST"])
@login_required
def proxmox_system_reboot(host_id):
    row = _host(host_id)
    if not row["host_key"]:
        abort(409)
    count = _launch(row, ["reboot"])
    flash(_("Redémarrage de Proxmox lancé : loco suit son retour.") if count else _("Ce redémarrage est déjà en cours."), "ok" if count else "error")
    return redirect(url_for("proxmox_system_host", host_id=host_id))


@app.route("/proxmox/<int:host_id>/shell", methods=["POST"])
@login_required
def proxmox_shell(host_id):
    """Terminal root sur Proxmox : une session distante tenue par loco, comme celle de Home Assistant."""
    from . import remote
    row = _host(host_id)
    if not homeassistant.ssh_binary() or not row["host_key"] or not homeassistant.terminal_ready():
        abort(409, _("La liaison SSH avec Proxmox n'est pas prête."))
    public_key()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": config.STATE, "TERM": "xterm-256color", "LANG": "C.UTF-8"}
    return remote.open_server_session(f"proxmox:{host_id}", row["name"], "proxmox.terminal", KEY_DIR,
                                      f"{homeassistant._pattern(ssh_host(row), row['ssh_port'])} {row['host_key']}\n",
                                      lambda known: _argv(row, known, "", tty=True), env)
