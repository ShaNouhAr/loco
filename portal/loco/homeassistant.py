"""Home Assistant (fonctionnalité facultative) : versions, mises à jour, sauvegardes, santé, redémarrages et terminal.

loco se relie à Home Assistant par SSH, avec une clé qui lui est propre : la clé privée ne quitte pas le serveur, la clé
publique est à ajouter au module SSH de Home Assistant (« Terminal & SSH », « Advanced SSH & Web Terminal »). Aucun
jeton de Home Assistant n'est demandé ni gardé. La clé d'hôte de Home Assistant n'est jamais acceptée d'office : son
empreinte est montrée, et un administrateur la confirme avant tout échange.

Tout passe par la commande `ha` de Home Assistant (`ha core info --raw-json`, `ha core update`…), lancée dans un shell de
connexion : c'est lui qui donne à `ha` son accès au Supervisor. Les opérations longues (mise à jour, sauvegarde,
redémarrage) sont des tâches suivies une à une ; le terminal est une session distante ordinaire, tenue par loco.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

from flask import abort, flash, g, redirect, render_template, request, url_for

from . import app, config
from .i18n import L_, _
from .auth import audit, can, login_required, require
from .db import connect, db
from .util import back

KEY_DIR = os.path.join(config.STATE, "homeassistant")
KEY_PATH = os.path.join(KEY_DIR, "id_ed25519")
HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,45})$")     # nom, IPv4 ou IPv6
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")
SECTIONS = ("info", "core info", "os info", "supervisor info", "host info", "addons", "backups", "resolution info")
# Les modules complémentaires s'appellent « apps » dans la commande ha récente, où « addons » n'est plus qu'un alias
# déprécié (un avertissement précède alors la réponse) ; les versions plus anciennes ne connaissent que « addons ».
ADDONS = 'if ha apps --help >/dev/null 2>&1; then ha apps {args}; else ha addons {args}; fi'
STALE = 120                     # relevé plus ancien : refait en arrière-plan tant que la page est affichée
OVERVIEW_STALE = 1800           # … et tant que la vue d'ensemble l'est, bien moins souvent
QUICK, LONG = 40, 3600          # secondes : un relevé ; une mise à jour ou une sauvegarde
MAX_HOSTS = 8
# Après un redémarrage du système : secondes laissées à Home Assistant pour partir, puis pour revenir, et pas des essais.
DOWN, BACK, STEPS = 90, 900, (5, 15)
# action -> (libellé, commande `ha`). {slug} : module complémentaire ; {backup} : option « sauvegarde avant ».
ACTIONS = {
    "update-core": (L_("Mise à jour de Home Assistant"), "ha core update{backup} --no-progress"),
    "update-os": (L_("Mise à jour du système"), "ha os update --no-progress"),
    "update-supervisor": (L_("Mise à jour du Supervisor"), "ha supervisor update --no-progress"),
    "update-addon": (L_("Mise à jour d'un module"), ADDONS.format(args="update {slug}{backup} --no-progress")),
    "backup": (L_("Sauvegarde complète"), "ha backups new --name {name} --no-progress"),
    "restart-core": (L_("Redémarrage de Home Assistant"), "ha core restart --no-progress"),
    "reboot-host": (L_("Redémarrage du système"), "ha host reboot --no-progress"),
}
# Problèmes que signale le Supervisor (`ha resolution info`), dits en clair ; les autres gardent leur nom.
ISSUES = {
    "no_current_backup": L_("Aucune sauvegarde récente"),
    "free_space": L_("Espace disque presque épuisé"),
    "update_failed": L_("Une mise à jour a échoué"),
    "update_rollback": L_("Une mise à jour a été annulée"),
    "corrupt_docker": L_("Images Docker endommagées"),
    "corrupt_repository": L_("Dépôt de modules endommagé"),
    "dns_server_failed": L_("Serveur DNS en échec"),
    "ipv4_connection_problem": L_("Connexion réseau IPv4 en défaut"),
    "trust": L_("Contenu non vérifié"),
    "security": L_("Problème de sécurité"),
    "pwned": L_("Mot de passe compromis dans la configuration d'un module"),
    "docker_config": L_("Configuration Docker à corriger"),
    "multiple_data_disks": L_("Plusieurs disques de données"),
    "reboot_required": L_("Redémarrage du système requis"),
    "detached_addon_missing": L_("Module dont le dépôt a disparu"),
    "detached_addon_removed": L_("Module retiré de son dépôt"),
}
# La liaison tombe quand le système redémarre : pour ces actions, ce n'est pas un échec, on attend son retour.
REBOOTS = {"update-os", "reboot-host"}
_WORKERS, _REFRESH, _GUARD = {}, {}, threading.Lock()


class LinkError(Exception):
    """Liaison SSH impossible ou commande refusée ; le message se montre tel quel."""


# --------------------------------------------------------------------------- clé de loco et clé d'hôte

def ssh_binary():
    return shutil.which("ssh")


def public_key():
    """Clé publique de loco, à ajouter au module SSH de Home Assistant. La paire est créée au premier besoin ; la clé
    privée reste sur ce serveur, lisible par le seul compte du portail."""
    if not os.path.isfile(KEY_PATH) or not os.path.isfile(KEY_PATH + ".pub"):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
        for path, data, mode in ((KEY_PATH, private, 0o600), (KEY_PATH + ".pub", public + b" loco@" + config.HOST.encode() + b"\n", 0o644)):
            fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(path + ".new", path)
    with open(KEY_PATH + ".pub", encoding="ascii") as f:
        return f.read().strip()


def fingerprint(key_b64):
    """Empreinte SHA-256 d'une clé d'hôte, écrite comme le fait ssh (« SHA256:… »)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(key_b64)).digest()).decode().rstrip("=")


def _pattern(host, port):
    return host if int(port) == 22 else f"[{host}]:{port}"


def _base(host, port, username, known):
    """Arguments communs : la clé de loco et elle seule, la clé d'hôte du fichier donné et elle seule, aucun réglage
    hérité du compte ni de la machine."""
    return [ssh_binary() or "ssh", "-F", os.devnull, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", KEY_PATH,
            "-o", "UserKnownHostsFile=" + known, "-o", "GlobalKnownHostsFile=" + os.devnull,
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
            "-o", "LogLevel=ERROR", "-p", str(int(port)), "-l", username]


def probe_host_key(host, port):
    """Clé d'hôte que présente Home Assistant, relevée sans rien authentifier ni accepter : (type, clé en base64).
    L'appelant la montre à l'administrateur, qui la confirme."""
    if not ssh_binary():
        raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
    with tempfile.TemporaryDirectory(prefix="loco-ha-") as work:
        known = os.path.join(work, "known")
        argv = [ssh_binary(), "-F", os.devnull, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "UserKnownHostsFile=" + known, "-o", "GlobalKnownHostsFile=" + os.devnull,
                "-o", "PreferredAuthentications=none", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR",
                "-p", str(int(port)), "-l", "loco", "--", host, "true"]
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LinkError(_("Home Assistant ne répond pas en SSH à cette adresse : %(e)s", e=str(e)[:160])) from e
        try:
            with open(known, encoding="ascii", errors="replace") as f:
                fields = [line.split() for line in f if line.strip() and not line.startswith("#")]
        except OSError:
            fields = []
        for entry in fields:
            if len(entry) >= 3 and re.fullmatch(r"[A-Za-z0-9+/=]{40,}", entry[2]):
                return entry[1], entry[2]
        reason = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise LinkError(_("Home Assistant ne répond pas en SSH à cette adresse : %(e)s", e=(reason[-1] if reason else "?")[:160]))


class _Known:
    """Fichier des clés d'hôte d'une instance, écrit depuis la base (c'est elle qui fait foi) le temps d'une commande."""
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
        fd, self.path = tempfile.mkstemp(prefix="known-", dir=KEY_DIR)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"{_pattern(self.row['host'], self.row['port'])} {self.row['host_key']}\n")
        return self.path

    def __exit__(self, *_exc):
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _argv(row, known, remote, tty=False):
    return (_base(row["host"], row["port"], row["username"], known) + ["-o", "StrictHostKeyChecking=yes"]
            + (["-tt"] if tty else ["-T"]) + ["--", row["host"]] + ([remote] if remote else []))


def run(row, script, timeout=QUICK):
    """Lance un script dans un shell de connexion de Home Assistant : (code de sortie, sortie)."""
    if not ssh_binary():
        raise LinkError(_("Le client SSH (commande ssh) n'est pas installé sur ce serveur."))
    if not row["host_key"]:
        raise LinkError(_("La clé d'hôte de Home Assistant n'a pas encore été confirmée."))
    public_key()
    with _Known(row) as known:
        try:
            done = subprocess.run(_argv(row, known, "sh -lc " + shlex.quote(script)), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise LinkError(_("Home Assistant n'a pas répondu dans le délai (%(n)s s).", n=timeout)) from e
        except OSError as e:
            raise LinkError(str(e)[:200]) from e
    return done.returncode, done.stdout.decode("utf-8", "replace")


# ssh n'a pas atteint Home Assistant (ou n'y est pas entré) : la commande n'y a pas été lancée.
UNREACHED = re.compile(r"permission denied|host key verification failed|remote host identification has changed|connection refused|"
                       r"connection timed out|could not resolve|no route to host|network is unreachable", re.I)


def _explain(output):
    """Refus de ssh dit clairement : la clé de loco n'est pas autorisée, la clé d'hôte a changé, l'hôte ne répond pas."""
    text = output.strip()
    low = text.lower()
    if "permission denied" in low:
        return _("Home Assistant refuse la clé de loco : ajoutez-la aux clés autorisées de son module SSH, puis redémarrez ce module.")
    if "host key verification failed" in low or "remote host identification has changed" in low:
        return _("La clé d'hôte de Home Assistant n'est plus celle qui a été confirmée : vérifiez qu'il s'agit bien de lui, puis confirmez la nouvelle.")
    if "command not found" in low or "ha: not found" in low:
        return _("La commande ha n'existe pas à cette adresse : ce n'est pas le module SSH d'un Home Assistant.")
    return (text.splitlines()[-1] if text else _("liaison SSH interrompue"))[:240]


# --------------------------------------------------------------------------- relevé

def _collect(row):
    script = "; ".join("echo '@@loco %s'; %s 2>&1; echo" % (name, ADDONS.format(args="--raw-json") if name == "addons" else f"ha {name} --raw-json")
                       for name in SECTIONS)
    code, output = run(row, script)
    found = {}
    for block in output.split("@@loco ")[1:]:
        name, _sep, body = block.partition("\n")
        for line in body.splitlines():              # la réponse tient sur une ligne ; un avertissement peut la précéder
            if not line.startswith("{"):
                continue
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            if isinstance(answer, dict) and answer.get("result") == "ok" and isinstance(answer.get("data"), dict):
                found[name.strip()] = answer["data"]
                break
    if "core info" not in found:
        raise LinkError(_explain(output) if code else _("Réponse de Home Assistant illisible."))
    return found


def _text(value, limit=120):
    return str(value if value is not None else "")[:limit]


def _local(stamp):
    """Date ISO de Home Assistant (UTC) -> « AAAA-MM-JJ HH:MM » à l'heure du serveur."""
    try:
        return datetime.datetime.fromisoformat(_text(stamp, 40).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return _text(stamp, 16).replace("T", " ")


def summarize(found):
    """Ce que la page montre, tiré du relevé brut : versions, mises à jour disponibles, modules, sauvegardes, santé."""
    # « outdated » et non « update » : dans un gabarit, part.update serait la méthode du dictionnaire.
    def part(key, name, target):
        data = found.get(key) or {}
        return {"target": target, "name": name, "version": _text(data.get("version"), 40),
                "latest": _text(data.get("version_latest"), 40), "outdated": bool(data.get("update_available"))}
    core = part("core info", "Home Assistant Core", "core")
    system = part("os info", "Home Assistant OS", "os")
    supervisor = part("supervisor info", "Supervisor", "supervisor")
    addons = []
    for a in (found.get("addons") or {}).get("addons") or []:
        if isinstance(a, dict) and SLUG_RE.match(_text(a.get("slug"), 80)):
            addons.append({"target": "addon:" + a["slug"], "slug": a["slug"], "name": _text(a.get("name"), 80) or a["slug"],
                           "version": _text(a.get("version"), 40), "latest": _text(a.get("version_latest"), 40),
                           "outdated": bool(a.get("update_available")), "state": _text(a.get("state"), 20)})
    addons.sort(key=lambda a: a["name"].lower())
    backups = []
    for b in (found.get("backups") or {}).get("backups") or []:
        if isinstance(b, dict):
            size = b.get("size_bytes") if isinstance(b.get("size_bytes"), int) else (
                int(b["size"] * 1048576) if isinstance(b.get("size"), (int, float)) else None)
            backups.append({"name": _text(b.get("name"), 80), "date": _local(b.get("date")), "type": _text(b.get("type"), 12),
                            "size": size, "protected": bool(b.get("protected"))})
    backups.sort(key=lambda b: b["date"], reverse=True)
    host = found.get("host info") or {}

    def gigabytes(value):                       # `ha host info` compte en gigaoctets
        return int(value * 1073741824) if isinstance(value, (int, float)) and value >= 0 else None
    health = found.get("resolution info") or {}
    supervisor_info = found.get("supervisor info") or {}
    updates = [p for p in (supervisor, core, system) if p["outdated"] and p["version"]] + [a for a in addons if a["outdated"]]
    return {"core": core, "os": system, "supervisor": supervisor, "addons": addons, "backups": backups, "updates": updates,
            "hostname": _text(host.get("hostname"), 60), "system": _text(host.get("operating_system"), 60),
            "disk_free": gigabytes(host.get("disk_free")), "disk_total": gigabytes(host.get("disk_total")),
            "healthy": supervisor_info.get("healthy") is not False, "supported": supervisor_info.get("supported") is not False,
            "issues": [{"type": _text(i.get("type"), 60), "context": _text(i.get("context"), 40)}
                       for i in health.get("issues") or [] if isinstance(i, dict)][:20],
            "unhealthy": [_text(x, 60) for x in health.get("unhealthy") or []][:10],
            "unsupported": [_text(x, 60) for x in health.get("unsupported") or []][:10]}


def refresh(host_id, conn=None):
    """Relève l'état d'une instance et le garde en base. Renvoie l'erreur éventuelle (vide si tout va bien)."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT * FROM homeassistant_hosts WHERE id=?", (host_id,)).fetchone()
        if row is None:
            return ""
        try:
            info, error = json.dumps(summarize(_collect(row))), ""
        except LinkError as e:
            info, error = None, str(e)[:300]
        with conn:
            if info is None:
                conn.execute("UPDATE homeassistant_hosts SET error=?, checked=? WHERE id=?", (error, time.time(), host_id))
            else:
                conn.execute("UPDATE homeassistant_hosts SET info=?, error='', checked=? WHERE id=?", (info, time.time(), host_id))
        return error
    finally:
        if own:
            conn.close()


def refresh_soon(host_id):
    """Relevé en arrière-plan, un seul à la fois par instance."""
    with _GUARD:
        if _REFRESH.get(host_id):
            return
        _REFRESH[host_id] = True

    def work():
        try:
            refresh(host_id)
        finally:
            with _GUARD:
                _REFRESH.pop(host_id, None)
    threading.Thread(target=work, name="loco-ha-releve", daemon=True).start()


# --------------------------------------------------------------------------- tâches (mises à jour, sauvegardes, redémarrages)

def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def command(job):
    backup = " --backup" if job["backup"] else ""
    name = shlex.quote("loco " + time.strftime("%Y-%m-%d %H:%M"))
    slug = job["target"].partition(":")[2]
    if job["action"] == "update-addon" and not SLUG_RE.match(slug):
        raise LinkError(_("Module inconnu."))
    return ACTIONS[job["action"]][1].format(backup=backup, slug=slug, name=name)


def active(conn, host_id):
    """Tâches en attente ou en cours d'une instance : ce qui est lancé ne se relance pas."""
    return conn.execute("SELECT * FROM homeassistant_jobs WHERE host_id=? AND state IN ('queued','running') ORDER BY id",
                        (host_id,)).fetchall()


def queue(conn, host_id, action, target="", label="", backup=False, user=""):
    """Ajoute une tâche, sauf si la même est déjà en attente ou en cours. Renvoie vrai si elle est ajoutée."""
    if any(j["action"] == action and j["target"] == target for j in active(conn, host_id)):
        return False
    conn.execute("INSERT INTO homeassistant_jobs (host_id, action, target, label, backup, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                 (host_id, action, target, label[:120], int(bool(backup)), _stamp(), user))
    return True


def work_soon(host_id):
    with _GUARD:
        if host_id in _WORKERS:
            return
        worker = threading.Thread(target=_work, args=(host_id,), name="loco-ha-taches", daemon=True)
        _WORKERS[host_id] = worker
    worker.start()


def _wait_back(conn, host_id):
    """Après un redémarrage du système : le voir partir (au plus DOWN secondes — sinon c'est qu'il est déjà revenu),
    puis attendre qu'il réponde de nouveau (au plus BACK secondes)."""
    until = time.time() + DOWN
    while time.time() < until and not refresh(host_id, conn):
        time.sleep(STEPS[0])
    until = time.time() + BACK
    while time.time() < until:
        if not refresh(host_id, conn):
            return True
        time.sleep(STEPS[1])
    return False


def _work(host_id):
    """Exécute les tâches d'une instance l'une après l'autre, dans l'ordre où elles ont été demandées."""
    conn = connect()
    try:
        while True:
            with conn:
                job = conn.execute("SELECT * FROM homeassistant_jobs WHERE host_id=? AND state='queued' ORDER BY id LIMIT 1",
                                   (host_id,)).fetchone()
                if job is None:
                    return
                conn.execute("UPDATE homeassistant_jobs SET state='running', started_at=? WHERE id=?", (_stamp(), job["id"]))
            row = conn.execute("SELECT * FROM homeassistant_hosts WHERE id=?", (host_id,)).fetchone()
            state, output = "failed", ""
            try:
                if row is None:
                    raise LinkError(_("Instance retirée."))
                code, output = run(row, command(job) + " 2>&1", timeout=LONG)
                if job["action"] in REBOOTS and (code == 0 or code == 255 and not UNREACHED.search(output)):
                    # Le système redémarre et emporte la liaison (ssh sort alors en 255, sans que ce soit un refus) :
                    # la tâche réussit s'il revient.
                    state = "success" if _wait_back(conn, host_id) else "failed"
                    output = (output.strip() + "\n" + (_("Home Assistant a redémarré et répond de nouveau.") if state == "success"
                                                       else _("Home Assistant n'a pas répondu dans le quart d'heure qui a suivi."))).strip()
                elif code == 0:
                    state = "success"
                elif code == 255:
                    output = _explain(output)
            except LinkError as e:
                output = str(e)
            with conn:
                conn.execute("UPDATE homeassistant_jobs SET state=?, output=?, finished_at=? WHERE id=?",
                             (state, output.strip()[-4000:], _stamp(), job["id"]))
            refresh(host_id, conn)
    finally:
        conn.close()
        with _GUARD:
            _WORKERS.pop(host_id, None)
        # Une tâche a pu être ajoutée entre la dernière lecture et la sortie : la reprendre.
        check = connect()
        try:
            again = check.execute("SELECT 1 FROM homeassistant_jobs WHERE host_id=? AND state='queued'", (host_id,)).fetchone()
        finally:
            check.close()
        if again:
            work_soon(host_id)


def start():
    """Au démarrage du portail : une tâche restée « en cours » a été coupée par l'arrêt ; celles en attente repartent.
    Les fichiers de clés d'hôte laissés par des sessions ou des commandes coupées net sont retirés."""
    try:
        for name in os.listdir(KEY_DIR):
            if name.startswith(("session-", "known-")):
                os.unlink(os.path.join(KEY_DIR, name))
    except OSError:
        pass
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE homeassistant_jobs SET state='failed', finished_at=?, output=? WHERE state='running'",
                         (_stamp(), "Interrompue par le redémarrage de loco : vérifiez l'état dans Home Assistant."))
        waiting = [r[0] for r in conn.execute("SELECT DISTINCT host_id FROM homeassistant_jobs WHERE state='queued'")]
    except Exception:            # base d'avant la migration, ou table absente : rien à reprendre
        waiting = []
    finally:
        conn.close()
    for host_id in waiting:
        work_soon(host_id)


# --------------------------------------------------------------------------- pages

def _host(host_id):
    row = db().execute("SELECT * FROM homeassistant_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _info(row):
    try:
        info = json.loads(row["info"]) if row["info"] else None
    except ValueError:
        return None
    return info if isinstance(info, dict) and "core" in info and "outdated" in info["core"] else None     # relevé d'une autre forme : refait


def _view(row):
    """Une instance telle que la page la montre : relevé, tâches en cours et récentes, ce qui est déjà lancé."""
    info = _info(row)
    jobs = db().execute("SELECT * FROM homeassistant_jobs WHERE host_id=? ORDER BY id DESC LIMIT 12", (row["id"],)).fetchall()
    running = [j for j in jobs if j["state"] in ("queued", "running")]
    busy = {j["action"] + "|" + j["target"]: j["state"] for j in running}
    if info:
        # Ce qui est déjà lancé se montre comme tel et ne se relance pas : état de la tâche sur chaque mise à jour.
        for u in [info["core"], info["os"], info["supervisor"]] + info["updates"] + info["addons"]:
            key = ("update-addon|" + u["target"]) if u["target"].startswith("addon:") else ("update-" + u["target"] + "|")
            u["job"] = busy.get(key, "")
    return {"row": row, "info": info, "jobs": jobs, "running": running,
            "updating": any(j["action"].startswith("update-") for j in running),
            "rebooting": any(j["action"] in ("reboot-host", "update-os") for j in running),
            "restarting": "restart-core|" in busy, "saving": "backup|" in busy,
            "fingerprint": fingerprint(row["host_key"].split()[-1]) if row["host_key"] else "",
            "checked": time.strftime("%H:%M", time.localtime(row["checked"])) if row["checked"] else "",
            "pending": sum(1 for u in info["updates"] if not u["job"]) if info else 0}


def attention():
    """Ce que la vue d'ensemble signale dans « À traiter » : liaison en échec, installation dégradée, mises à jour
    disponibles. Lu dans le dernier relevé, lui-même refait en arrière-plan quand il date."""
    rows, url = [], url_for("homeassistant")
    for row in db().execute("SELECT * FROM homeassistant_hosts ORDER BY name COLLATE NOCASE, id").fetchall():
        if row["host_key"] and time.time() - (row["checked"] or 0) > OVERVIEW_STALE:
            refresh_soon(row["id"])
        info = _info(row)
        if row["error"]:
            rows.append({"tone": "warn", "text": _("Home Assistant « %(name)s » : la liaison échoue.", name=row["name"]), "url": url})
        elif info:
            if not info["healthy"]:
                rows.append({"tone": "err", "text": _("Home Assistant « %(name)s » : installation dégradée.", name=row["name"]), "url": url})
            count = len(info["updates"])
            if count:
                rows.append({"tone": "warn", "url": url, "text":
                             _("Home Assistant « %(name)s » : %(n)s mise à jour disponible.", name=row["name"], n=count) if count == 1
                             else _("Home Assistant « %(name)s » : %(n)s mises à jour disponibles.", name=row["name"], n=count)})
    return rows


def _signature(views):
    return hashlib.sha256(json.dumps([[v["row"]["id"], v["row"]["checked"], v["row"]["error"], bool(v["row"]["host_key"]),
                                       [(j["id"], j["state"]) for j in v["jobs"]]] for v in views]).encode()).hexdigest()[:16]


@app.route("/homeassistant")
@login_required
def homeassistant():
    rows = db().execute("SELECT * FROM homeassistant_hosts ORDER BY name COLLATE NOCASE, id").fetchall()
    for row in rows:
        # Relevé trop ancien, ou gardé sous une forme que cette version ne lit plus (mise à jour de loco) : refait.
        if row["host_key"] and (time.time() - (row["checked"] or 0) > STALE or (_info(row) is None and not row["error"])):
            refresh_soon(row["id"])                # la page montre le dernier relevé, puis se met à jour toute seule
    views = [_view(row) for row in rows]
    signature = _signature(views)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    return render_template("homeassistant.html", views=views, signature=signature, ssh=bool(ssh_binary()),
                           public_key=public_key() if can("homeassistant.manage") else "", actions=ACTIONS, issues=ISSUES,
                           pending=_pending() if can("homeassistant.manage") else None, max_hosts=MAX_HOSTS,
                           busy=any(v["running"] for v in views),
                           terminal=terminal_ready())


def terminal_ready():
    """Le terminal est une session distante tenue par le serveur dans un pseudo-terminal : il lui faut la fonctionnalité
    Accès distant (et Mon parc, dont elle dépend), et un serveur loco sous Linux."""
    from . import adb, features
    return adb.pty is not None and not config.WINDOWS and features.enabled("remote") and features.enabled("fleet")


def _pending():
    """Liaison en cours de création : adresse saisie et clé d'hôte relevée, en attente de confirmation (dans la session)."""
    from flask import session
    data = session.get("homeassistant_pending")
    if not isinstance(data, dict) or time.time() - data.get("at", 0) > 900:
        return None
    return dict(data, fingerprint=fingerprint(data["key"]))


def _fields():
    name = request.form.get("name", "").strip()[:60]
    host = request.form.get("host", "").strip()
    username = request.form.get("username", "").strip()
    try:
        port = int(request.form.get("port", "22") or 22)
    except ValueError:
        port = 0
    if not HOST_RE.match(host) or host.startswith("-"):
        raise ValueError(_("Adresse de Home Assistant invalide : un nom ou une adresse IP, sans « http:// » ni port."))
    if not USER_RE.match(username):
        raise ValueError(_("Nom d'utilisateur SSH invalide."))
    if not 1 <= port <= 65535:
        raise ValueError(_("Port SSH invalide."))
    return name or host, host, port, username


@app.route("/homeassistant/link", methods=["POST"])
@login_required
def homeassistant_link():
    """Première étape : relever la clé d'hôte que présente l'adresse donnée, et la montrer pour confirmation."""
    from flask import session
    if db().execute("SELECT COUNT(*) FROM homeassistant_hosts").fetchone()[0] >= MAX_HOSTS:
        abort(409, _("Nombre maximal d'instances atteint."))
    try:
        name, host, port, username = _fields()
        kind, key = probe_host_key(host, port)
    except (ValueError, LinkError) as e:
        flash(str(e), "error")
        return back("homeassistant")
    session["homeassistant_pending"] = {"name": name, "host": host, "port": port, "username": username,
                                        "type": kind, "key": key, "at": time.time(), "nonce": secrets.token_hex(8)}
    return back("homeassistant")


@app.route("/homeassistant/confirm", methods=["POST"])
@login_required
def homeassistant_confirm():
    """Seconde étape : l'administrateur a comparé l'empreinte. L'instance est créée avec cette clé d'hôte et elle seule —
    ou, pour une instance déjà reliée dont la clé a changé, sa clé d'hôte est remplacée."""
    from flask import session
    data = _pending()
    session.pop("homeassistant_pending", None)
    if request.form.get("cancel") == "1" or data is None:
        return back("homeassistant")
    if not secrets.compare_digest(request.form.get("nonce", ""), data["nonce"]):
        abort(400)
    conn = db()
    if data.get("host_id"):
        row = _host(data["host_id"])
        conn.execute("UPDATE homeassistant_hosts SET host_key=?, error='' WHERE id=?", (f"{data['type']} {data['key']}", row["id"]))
        audit("homeassistant.cle-hote", f"{row['name']} : {data['fingerprint']}", commit=False)
        host_id = row["id"]
    else:
        host_id = conn.execute("INSERT INTO homeassistant_hosts (name, host, port, username, host_key, created_at, created_by) "
                               "VALUES (?,?,?,?,?,?,?)", (data["name"], data["host"], data["port"], data["username"],
                                                          f"{data['type']} {data['key']}", _stamp(), g.user["username"])).lastrowid
        audit("homeassistant.liaison", f"{data['name']} ({data['username']}@{data['host']}:{data['port']}), clé d'hôte {data['fingerprint']}", commit=False)
    conn.commit()
    error = refresh(host_id)
    flash(_("Home Assistant relié.") if not error else _("Instance enregistrée, mais la liaison échoue : %(e)s", e=error), "ok" if not error else "error")
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/hostkey", methods=["POST"])
@login_required
def homeassistant_hostkey(host_id):
    """Clé d'hôte changée (module SSH réinstallé) : la relever de nouveau ; elle sera confirmée comme à la création."""
    from flask import session
    row = _host(host_id)
    try:
        kind, key = probe_host_key(row["host"], row["port"])
    except LinkError as e:
        flash(str(e), "error")
        return back("homeassistant")
    session["homeassistant_pending"] = {"host_id": row["id"], "name": row["name"], "host": row["host"], "port": row["port"],
                                        "username": row["username"], "type": kind, "key": key, "at": time.time(),
                                        "nonce": secrets.token_hex(8), "same": row["host_key"].split()[-1:] == [key]}
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/delete", methods=["POST"])
@login_required
def homeassistant_delete(host_id):
    row = _host(host_id)
    conn = db()
    for session_row in conn.execute("SELECT id FROM remote_sessions WHERE target=? AND state!='closed'", (f"homeassistant:{host_id}",)).fetchall():
        conn.execute("UPDATE remote_sessions SET state='closed', error='Instance retirée.' WHERE id=?", (session_row["id"],))
    conn.execute("DELETE FROM homeassistant_hosts WHERE id=?", (host_id,))
    audit("homeassistant.retrait", row["name"], commit=False)
    conn.commit()
    flash(_("%(name)s retiré de loco. La clé de loco reste autorisée dans Home Assistant tant que vous ne l'y retirez pas.", name=row["name"]), "ok")
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/refresh", methods=["POST"])
@login_required
def homeassistant_refresh(host_id):
    row = _host(host_id)
    error = refresh(row["id"], db())
    if error:
        flash(error, "error")
    return back("homeassistant")


def _launch(row, items, backup=False):
    """Met des tâches en file et lance leur exécution ; renvoie le nombre de tâches ajoutées."""
    conn, count = db(), 0
    for action, target, label in items:
        if queue(conn, row["id"], action, target, label, backup and action in ("update-core", "update-addon"), g.user["username"]):
            audit("homeassistant." + action, f"{row['name']}" + (f" : {label}" if label else "") + (", sauvegarde avant" if backup and action in ("update-core", "update-addon") else ""), commit=False)
            count += 1
    conn.commit()
    if count:
        work_soon(row["id"])
    return count


def _update_items(info, target):
    """(action, cible, libellé) d'une mise à jour disponible, ou de toutes (target vide) — dans l'ordre conseillé :
    le Supervisor, les modules, le cœur, puis le système, qui redémarre."""
    found = []
    for u in info["updates"]:
        action = "update-addon" if u["target"].startswith("addon:") else "update-" + u["target"]
        found.append((action, u["target"] if action == "update-addon" else "", f"{u['name']} {u['version']} → {u['latest']}", u["target"]))
    order = {"update-supervisor": 0, "update-addon": 1, "update-core": 2, "update-os": 3}
    found.sort(key=lambda item: order[item[0]])
    return [item[:3] for item in found if not target or item[3] == target]


@app.route("/homeassistant/update", methods=["POST"])
@login_required
def homeassistant_update():
    """Une mise à jour (target : core, os, supervisor, addon:<nom>) ou toutes (target vide), avec ou sans sauvegarde avant.
    L'instance vient du formulaire : la fenêtre de confirmation est commune à toutes."""
    row = _host(request.form.get("host_id", type=int) or 0)
    info = _info(row)
    target = request.form.get("target", "")
    items = _update_items(info, target) if info else []
    if not items:
        flash(_("Aucune mise à jour à lancer : actualisez l'état de Home Assistant."), "error")
        return back("homeassistant")
    count = _launch(row, items, request.form.get("backup") == "1")
    flash(_("Mise à jour lancée.") if count == 1 else _("%(n)s mises à jour lancées, l'une après l'autre.", n=count) if count
          else _("Cette mise à jour est déjà en cours."), "ok" if count else "error")
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/backup", methods=["POST"])
@login_required
def homeassistant_backup(host_id):
    row = _host(host_id)
    count = _launch(row, [("backup", "", "")])
    flash(_("Sauvegarde complète lancée.") if count else _("Une sauvegarde est déjà en cours."), "ok" if count else "error")
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/restart", methods=["POST"])
@login_required
def homeassistant_restart(host_id):
    row = _host(host_id)
    action = {"core": "restart-core", "host": "reboot-host"}.get(request.form.get("what", ""))
    if action is None:
        abort(400)
    count = _launch(row, [(action, "", "")])
    flash(_("Redémarrage lancé.") if count else _("Ce redémarrage est déjà en cours."), "ok" if count else "error")
    return back("homeassistant")


@app.route("/homeassistant/<int:host_id>/log")
@login_required
def homeassistant_log(host_id):
    """Fin du journal de Home Assistant (cœur), lue à la demande."""
    row = _host(host_id)
    try:
        code, output = run(row, "ha core logs 2>&1 | tail -n 200")
        text = output if code == 0 else _explain(output)
    except LinkError as e:
        text = str(e)
    return {"log": text[-60000:]}, 200, {"Cache-Control": "no-store"}


@app.route("/homeassistant/<int:host_id>/shell", methods=["POST"])
@login_required
def homeassistant_shell(host_id):
    """Terminal SSH : une session distante tenue par loco, comme le terminal adb d'un appareil Android."""
    from . import adb, remote
    row = _host(host_id)
    require("homeassistant.shell")
    if not ssh_binary() or not row["host_key"] or not terminal_ready():
        abort(409, _("La liaison SSH avec Home Assistant n'est pas prête."))
    public_key()
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    remote._prune()
    if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE state IN ('waiting','active')").fetchone()[0] >= remote.SERVER_SESSIONS:
        abort(409, _('Huit sessions sont déjà ouvertes.'))
    target = f"homeassistant:{host_id}"
    if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE target=? AND state IN ('waiting','active')", (target,)).fetchone()[0] >= remote.KIND_SESSIONS:
        abort(409, _('Deux sessions de ce type sont déjà ouvertes sur ce poste.'))
    sid, now = secrets.token_hex(16), time.time()
    conn.execute("INSERT INTO remote_sessions (id,user_id,owner,pw_version,kind,created,browser_seen,expires,target) VALUES (?,?,?,?,?,?,?,?,?)",
                 (sid, g.user["id"], remote._owner(), g.user["pw_version"], "ssh", now, now, now + 3600, target))
    audit("homeassistant.terminal", f"{row['name']}, {sid}")
    # Fichier des clés d'hôte de la session : écrit depuis la base (dossier d'état privé), retiré à la fin de la session.
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    known = os.path.join(KEY_DIR, "session-" + sid)
    with open(os.open(known, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="ascii") as f:
        f.write(f"{_pattern(row['host'], row['port'])} {row['host_key']}\n")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": config.STATE, "TERM": "xterm-256color", "LANG": "C.UTF-8"}
    adb.start_command(sid, _argv(row, known, "", tty=True), env, "ssh", after=lambda: os.unlink(known))
    return redirect(url_for("remote_session", sid=sid, **({"view": "console"} if request.form.get("view") == "console" else {})))
