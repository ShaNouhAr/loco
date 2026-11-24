"""Équipements réseau : switchs Cisco (IOS, IOS-XE, Small Business) et Extreme (EXOS / Switch Engine, VOSS / Fabric
Engine), pare-feu Stormshield SNS — reliés par loco sans agent, avec un compte de l'équipement.

loco s'y connecte comme un administrateur : SSH et ligne de commande pour les switchs, API HTTPS pour Stormshield (son
SSH, s'il est activé, sert au terminal). Le mot de passe est scellé dans la base ; la clé d'hôte SSH et le certificat
du pare-feu ne sont jamais acceptés d'office, un administrateur en confirme l'empreinte.

Ce qu'il en fait : un relevé régulier (modèle, numéro de série, version, ports actifs, processeur, mémoire,
température) montré dans Mon parc ; la sauvegarde de la configuration, planifiée ou à la demande, gardée seulement
quand elle change (fichiers chiffrés dans l'état de loco, donc dans ses propres sauvegardes), avec l'historique et les
différences ; le terminal ; le redémarrage ; et l'installation d'un firmware déposé dans la bibliothèque de loco, avec
la configuration sauvegardée juste avant. Chaque famille a sa façon de faire (netfamilies) : Cisco IOS et Extreme
reçoivent l'image par scp/sftp puis l'activent eux-mêmes, Stormshield par son API ; un Cisco Small Business ne charge
un firmware qu'en TFTP depuis sa ligne de commande — loco le signale, et l'installation se fait sur l'équipement.
"""
import difflib
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from flask import Response, abort, flash, g, redirect, render_template, request, session, url_for

from . import app, config, homeassistant, netcli, netfamilies, sns
from .auth import audit, can, login_required, require
from .db import connect, db, lease
from .i18n import L_, _
from .netfamilies import FAMILIES
from .util import cipher, sealer

CONFIG_DIR = os.path.join(config.STATE, "network", "configs")
FIRMWARE_DIR = os.path.join(config.DATA, "network-firmware")
STALE = 120                     # relevé plus ancien : refait en arrière-plan tant que la page est affichée
POLL_EVERY = 900                # relevé de fond de chaque équipement relié
RETRY_BACKUP = 3600             # sauvegarde planifiée en échec : nouvel essai
TICK = 60
BACK, STEP, FIRST = 1800, 20, 30   # après un redémarrage : retour attendu au plus, pas des essais, premier essai
FORGOTTEN = 600
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f<>]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_.@\\-]{1,64}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._()+-]{1,40}$")
MODELS_RE = re.compile(r"^[A-Za-z0-9*?._/ ,+-]{0,200}$")
EVERY = (0, 6 * 3600, 86400, 7 * 86400)
KEEP = (10, 30, 90, 365)
ACTIONS = {
    "backup": L_("Sauvegarde de la configuration"),
    "firmware": L_("Installation du firmware"),
    "reboot": L_("Redémarrage"),
}
MASK = "••••••••"
_WORKERS, _REFRESH, _TRIED, _GUARD = {}, set(), {}, threading.Lock()


# --------------------------------------------------------------------------- secrets

seal, unseal = sealer(b"loco-network-v1")             # mot de passe scellé avec la clé du serveur


def _cipher():
    return cipher(b"loco-network-v1")


def _password(row):
    password = unseal(row["secret"])
    if row["secret"] and not password:
        raise netcli.LinkError(_("Le mot de passe enregistré est illisible (clé du serveur changée) : saisissez-le de nouveau."))
    return password


# --------------------------------------------------------------------------- liaison

def fam(row):
    return FAMILIES[row["family"]]


def linked(row):
    """Relevé possible : clé d'hôte confirmée (switchs), certificat confirmé (Stormshield)."""
    return bool(row["api_cert"] if fam(row).api else row["host_key"])


def cli(row, timeout=30):
    return netcli.Session(row["host"], row["port"], row["username"], _password(row), row["host_key"], fam(row),
                          enable=unseal(row["enable_secret"]), timeout=timeout)


def api(row, timeout=30):
    return sns.Client(row["host"], row["api_port"], row["api_cert"], row["username"], _password(row), timeout=timeout)


def info_of(row):
    try:
        data = json.loads(row["info"]) if row["info"] else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def collect(row):
    """Relevé de l'équipement (dict), ou LinkError."""
    family = fam(row)
    if family.api:
        with api(row) as fw:
            return sns.collect(fw)
    with cli(row) as s:
        out = {}
        for command in family.inventory:
            try:
                out[command] = s.run(command, check=False, timeout=45)
            except netcli.CliError as e:
                out[command] = str(e)
        privileged = s.privileged
    found = netfamilies.parse(family.key, out)
    found["privileged"] = privileged
    return found


def refresh(dev_id, conn=None):
    """Refait le relevé d'un équipement ; renvoie le message d'erreur, ou une chaîne vide."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT * FROM net_devices WHERE id=?", (dev_id,)).fetchone()
        if row is None or not linked(row):
            return ""
        now, error = time.time(), ""
        try:
            found = collect(row)
            found["at"] = now
            with conn:
                conn.execute("UPDATE net_devices SET info=?, error='', checked=?, seen=? WHERE id=?", (json.dumps(found), now, now, dev_id))
        except netcli.LinkError as e:
            error = str(e)[:400] or _("liaison interrompue")
            with conn:
                conn.execute("UPDATE net_devices SET error=?, checked=? WHERE id=?", (error, now, dev_id))
        return error
    finally:
        if own:
            conn.close()


def refresh_soon(dev_id):
    with _GUARD:
        if dev_id in _REFRESH:
            return
        _REFRESH.add(dev_id)

    def go():
        try:
            refresh(dev_id)
        except Exception:
            pass
        finally:
            with _GUARD:
                _REFRESH.discard(dev_id)
    threading.Thread(target=go, name="loco-reseau-releve", daemon=True).start()


# --------------------------------------------------------------------------- sauvegardes de la configuration

def _write_secret_file(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(_cipher().encrypt(data))
    os.replace(path + ".new", path)


def read_backup(b):
    """Contenu d'une sauvegarde (octets), ou None si le fichier manque ou ne se déchiffre plus."""
    try:
        with open(os.path.join(CONFIG_DIR, b["file"]), "rb") as f:
            return _cipher().decrypt(f.read())
    except Exception:
        return None


def _counts(old, new):
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def store_backup(conn, row, data, binary, reason, user=""):
    """Garde une configuration si elle diffère de la dernière gardée. Renvoie la ligne ajoutée, ou None (inchangée)."""
    now = time.time()
    digest = hashlib.sha256(data).hexdigest()
    last = conn.execute("SELECT * FROM net_backups WHERE device_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
    if last is not None and last["sha256"] == digest:
        with conn:
            conn.execute("UPDATE net_devices SET last_backup=?, backup_error='' WHERE id=?", (now, row["id"]))
        return None
    added = removed = 0
    if not binary and last is not None and not last["binary"]:
        previous = read_backup(last)
        if previous is not None:
            added, removed = _counts(previous.decode("utf-8", "replace"), data.decode("utf-8", "replace"))
    elif not binary:
        added = data.count(b"\n")
    name = f"{int(row['id'])}/{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{digest[:12]}.{'bin' if binary else 'cfg'}"
    _write_secret_file(os.path.join(CONFIG_DIR, name), data)
    with conn:
        cur = conn.execute("INSERT INTO net_backups (device_id, taken_at, sha256, size, binary, added, removed, reason, created_by, file) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (row["id"], now, digest, len(data), int(binary), added, removed, reason, user, name))
        conn.execute("UPDATE net_devices SET last_backup=?, backup_error='' WHERE id=?", (now, row["id"]))
    prune(conn, row)
    return conn.execute("SELECT * FROM net_backups WHERE id=?", (cur.lastrowid,)).fetchone()


def prune(conn, row):
    """Conservation : les N configurations les plus récentes de l'équipement."""
    keep = max(1, int(row["backup_keep"] or 30))
    old = conn.execute("SELECT * FROM net_backups WHERE device_id=? ORDER BY id DESC LIMIT -1 OFFSET ?", (row["id"], keep)).fetchall()
    for b in old:
        netcli.remove(os.path.join(CONFIG_DIR, b["file"]))
    if old:
        with conn:
            conn.execute(f"DELETE FROM net_backups WHERE id IN ({','.join('?' * len(old))})", [b["id"] for b in old])


def take_backup(conn, row, reason, user=""):
    """Lit la configuration de l'équipement et la garde si elle a changé. (ligne ajoutée ou None, texte du résultat)."""
    family = fam(row)
    if family.api:
        with api(row, timeout=60) as fw:
            data = fw.download("CONFIG BACKUP list=all")
        binary = True
    else:
        with cli(row) as s:
            text = s.run(family.config, timeout=120)
        if len(text.strip()) < 20:
            raise netcli.CliError(_("Configuration vide ou illisible : rien n'a été gardé."))
        data, binary = netfamilies.normalize_config(family.key, text).encode("utf-8"), False
    added = store_backup(conn, row, data, binary, reason, user)
    if added is None:
        return None, _("Configuration inchangée depuis la dernière sauvegarde.")
    if binary:
        return added, _("Sauvegarde gardée (%(n)s octets).", n=added["size"])
    return added, _("Configuration gardée : %(a)s lignes ajoutées, %(r)s retirées.", a=added["added"], r=added["removed"])


def diff_lines(old, new, limit=4000):
    """Différences lisibles : [(classe, texte)] (« add », « del », « hunk », « ctx »)."""
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3):
        if line.startswith(("+++", "---")):
            continue
        kind = "hunk" if line.startswith("@@") else "add" if line.startswith("+") else "del" if line.startswith("-") else "ctx"
        out.append((kind, line))
        if len(out) >= limit:
            out.append(("hunk", _("… différences tronquées")))
            break
    return out


# --------------------------------------------------------------------------- bibliothèque de firmwares

def firmware_path(fw):
    return os.path.join(FIRMWARE_DIR, fw["file"])


def _matches(models, model):
    patterns = [p.strip() for p in (models or "").split(",") if p.strip()]
    return not patterns or any(fnmatch.fnmatchcase((model or "").lower(), p.lower()) for p in patterns)


def compatible(fw, row, info):
    """Ce firmware vaut-il pour cet équipement ? Les modèles indiqués au dépôt décident ; sinon, pour Cisco, l'image doit
    être de la même plateforme que celle qui tourne (une image de Catalyst 9300 n'est jamais proposée à un 2960X). Les
    autres systèmes refusent d'eux-mêmes une image d'une autre plateforme."""
    if fw["models"].strip():
        return _matches(fw["models"], (info or {}).get("model"))
    current = netfamilies.platform(row["family"], info)
    if row["family"] in ("cisco-ios", "cisco-sb"):
        return bool(current) and netfamilies.stem(fw["filename"]) == current
    return True


def offers(row, info):
    """Firmwares de la bibliothèque plus récents que celui de l'équipement (même famille, même plateforme ou modèle)."""
    version = (info or {}).get("version") or ""
    if not version:
        return []
    found = []
    for fw in db().execute("SELECT * FROM net_firmware WHERE family=? ORDER BY id DESC", (row["family"],)).fetchall():
        if compatible(fw, row, info) and netfamilies.version_key(fw["version"]) > netfamilies.version_key(version):
            found.append(fw)
    found.sort(key=lambda f: netfamilies.version_key(f["version"]), reverse=True)
    return found


# --------------------------------------------------------------------------- tâches

def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def active(conn, dev_id):
    return conn.execute("SELECT * FROM net_jobs WHERE device_id=? AND state IN ('queued','running') ORDER BY id", (dev_id,)).fetchall()


def queue(conn, dev_id, action, params=None, user=""):
    if any(j["action"] == action for j in active(conn, dev_id)):
        return False
    conn.execute("INSERT INTO net_jobs (device_id, action, params, created_at, created_by) VALUES (?,?,?,?,?)",
                 (dev_id, action, json.dumps(params or {}), _stamp(), user))
    return True


def work_soon(dev_id):
    with _GUARD:
        if dev_id in _WORKERS:
            return
        worker = threading.Thread(target=_work, args=(dev_id,), name="loco-reseau-taches", daemon=True)
        _WORKERS[dev_id] = worker
    worker.start()


class Log:
    """Journal d'une tâche, enregistré au fil de l'eau (la page le montre pendant qu'elle tourne)."""
    def __init__(self, conn, job_id):
        self.conn, self.job_id, self.lines = conn, job_id, []

    def __call__(self, text):
        self.lines.append(f"{time.strftime('%H:%M:%S')}  {text}")
        with self.conn:
            self.conn.execute("UPDATE net_jobs SET output=? WHERE id=?", (self.text()[-8000:], self.job_id))

    def text(self):
        return "\n".join(self.lines)


def _backup_job(conn, row, job, log, params):
    added, message = take_backup(conn, row, params.get("reason") or "manuelle", job["created_by"])
    log(message)
    return "success"


def _wait_back(conn, row, log, since, version=""):
    """Attend que l'équipement réponde de nouveau après un redémarrage : allumé depuis moins longtemps que la demande,
    ou passé à la version attendue. Rend le relevé obtenu."""
    deadline, gone = time.time() + BACK, False
    log(_("Attente du retour de l'équipement (jusqu'à %(n)s minutes)…", n=BACK // 60))
    time.sleep(FIRST)
    while time.time() < deadline:
        try:
            found = collect(row)
        except netcli.LinkError:
            gone = True
            time.sleep(STEP)
            continue
        up = found.get("uptime")
        fresh = up is not None and up < time.time() - since + 60
        if fresh or (version and netfamilies.same_version(found.get("version", ""), version)) or (gone and up is None):
            found["at"] = time.time()
            with conn:
                conn.execute("UPDATE net_devices SET info=?, error='', checked=?, seen=? WHERE id=?",
                             (json.dumps(found), found["at"], found["at"], row["id"]))
            return found
        time.sleep(STEP)
    raise netcli.LinkError(_("L'équipement n'a pas répondu dans la demi-heure qui a suivi le redémarrage."))


def _voss_commit(row, log):
    """VOSS / Fabric Engine : une version activée doit être validée après le redémarrage, sinon le switch revient de
    lui-même à l'ancienne (validation automatique). loco l'a rejoint : la liaison est donc bonne, il valide."""
    try:
        with cli(row) as s:
            out = s.run("software commit", check=False, timeout=60)
        log(_("Version validée (software commit) : %(o)s", o=(out.strip().splitlines() or ["ok"])[-1][:200]))
    except netcli.LinkError as e:
        log(_("Validation (software commit) impossible : %(e)s — faites-la sur le switch avant la fin du délai, sinon il reviendra à l'ancienne version.", e=e))


def _reboot_device(conn, row, log):
    family, since = fam(row), time.time()
    if family.api:
        with api(row) as fw:
            fw.modify()
            fw.command("SYSTEM REBOOT", check=False)
        log(_("Redémarrage demandé au pare-feu."))
    else:
        command, answers = family.reboot
        try:
            with cli(row) as s:
                out = s.run_interactive(command, answers, timeout=90)
            log(_("Redémarrage demandé : %(o)s", o=(out.strip().splitlines() or [command])[-1][:200]))
        except netcli.LinkError as e:
            if "délai" in str(e):
                raise
            log(_("Liaison coupée par le redémarrage."))
    found = _wait_back(conn, row, log, since)
    if family.key == "voss":
        _voss_commit(row, log)
    log(_("L'équipement a redémarré et répond de nouveau (version %(v)s).", v=found.get("version") or "?"))


def _reboot_job(conn, row, job, log, params):
    _reboot_device(conn, row, log)
    return "success"


def _check_file(fw, log):
    path = firmware_path(fw)
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        raise netcli.LinkError(_("Le fichier du firmware a disparu de la bibliothèque de loco.")) from None
    if digest.hexdigest() != fw["sha256"]:
        raise netcli.LinkError(_("Le fichier du firmware a changé depuis son dépôt (empreinte SHA-256 différente) : supprimez-le et déposez-le de nouveau."))
    log(_("Fichier vérifié : %(f)s (SHA-256 %(h)s…).", f=fw["filename"], h=fw["sha256"][:16]))
    return path


def _push(row, log, local, remote, legacy=True):
    log(_("Copie de l'image vers l'équipement (%(f)s)…", f=remote))
    ok, out = netcli.push_file(row["host"], row["port"], row["username"], _password(row), row["host_key"], local, remote, legacy=legacy)
    if not ok:
        raise netcli.LinkError(_("Copie refusée par l'équipement : %(e)s", e=netcli.explain(out)))
    log(_("Image copiée."))


def _firmware_ios(conn, row, job, log, fw, reboot):
    with cli(row) as s:
        listing = s.run("dir flash:", check=False, timeout=60)
        version = s.run("show version", check=False, timeout=45)
    found = netfamilies.parse_ios({"show version": version, "dir flash:": listing})
    if found.get("flash_free") is not None and found["flash_free"] < fw["size"] + 1024 * 1024:
        raise netcli.CliError(_("Place insuffisante dans la mémoire flash : %(free)s octets libres, l'image en fait %(size)s. Supprimez une ancienne image (delete flash:…).",
                                free=found["flash_free"], size=fw["size"]))
    if found.get("install_mode") and not reboot:
        raise netcli.CliError(_("Ce switch est en mode INSTALL : l'activation d'un firmware le redémarre. Cochez « Redémarrer »."))
    _push(row, log, firmware_path(fw), "flash:" + fw["filename"])
    since = time.time()
    with cli(row, timeout=60) as s:
        out = s.run(f"verify /md5 flash:{fw['filename']} {fw['md5']}", check=False, timeout=1200)
        if "verified" not in out.lower():
            raise netcli.CliError(_("Le switch ne confirme pas l'empreinte MD5 de l'image copiée : %(o)s", o=(out.strip().splitlines() or ["?"])[-1][:200]))
        log(_("Empreinte MD5 vérifiée par le switch."))
        if found.get("install_mode"):
            log(_("Mode INSTALL : install add file … activate commit (le switch redémarre de lui-même)."))
            out = s.run_interactive(f"install add file flash:{fw['filename']} activate commit prompt-level none",
                                    ((r"\[y/n\]\s*$", "y\r"), (r"\[confirm\]\s*$", "\r")), timeout=3600)
            log((out.strip().splitlines() or [""])[-1][:300])
        else:
            s.run("configure terminal")
            s.run("no boot system", check=False)
            s.run(f"boot system flash:{fw['filename']}")
            s.run("end")
            out = s.run_interactive("write memory", ((r"\[confirm\]\s*$", "\r"),), timeout=300)
            log(_("Démarrage réglé sur la nouvelle image et configuration enregistrée (boot system flash:%(f)s).", f=fw["filename"]))
    if found.get("install_mode"):
        found = _wait_back(conn, row, log, since, fw["version"])
        return found
    if reboot:
        _reboot_device(conn, row, log)
    return None


def _firmware_exos(conn, row, job, log, fw, reboot):
    _push(row, log, firmware_path(fw), fw["filename"])
    with cli(row, timeout=60) as s:
        out = s.run_interactive(f"install image {fw['filename']}",
                                ((r"\(y/[nN]\)\s*:?\s*$", "y"), (r"\(y - yes, n - no[^)]*\)\s*:?\s*$", "y")), timeout=1800)
        if re.search(r"^\s*(?:Error|%)", out, re.M):
            raise netcli.CliError(_("Installation refusée : %(o)s", o=out.strip().splitlines()[-1][:300]))
        log(_("Image installée sur la partition de secours."))
        switch = s.run("show switch", check=False, timeout=45)
        found = netfamilies.parse_exos({"show switch": switch})
        target = "secondary" if netfamilies.same_version(found.get("secondary", ""), fw["version"]) else \
            "primary" if netfamilies.same_version(found.get("primary", ""), fw["version"]) else ""
        if target and found.get("image_selected") != target:
            s.run(f"use image partition {target}", check=False)
        log(_("Prochain démarrage sur la partition %(p)s.", p=target or found.get("image_selected") or "?"))
    if reboot:
        _reboot_device(conn, row, log)


def _firmware_voss(conn, row, job, log, fw, reboot):
    _push(row, log, firmware_path(fw), "/intflash/" + fw["filename"], legacy=False)
    with cli(row, timeout=60) as s:
        out = s.run_interactive(f"software add {fw['filename']}", ((r"\(y/n\)\s*\??\s*$", "y\r"),), timeout=1800)
        if re.search(r"^\s*(?:Error|%)", out, re.M):
            raise netcli.CliError(_("Ajout refusé : %(o)s", o=out.strip().splitlines()[-1][:300]))
        soft = netfamilies.parse_voss({"show software": s.run("show software", check=False, timeout=45)})
        release = next((r for r in soft.get("releases", []) if fw["version"] in r and r != soft.get("primary")), "")
        if not release:
            raise netcli.CliError(_("Version %(v)s introuvable dans les versions du switch après l'ajout (show software).", v=fw["version"]))
        out = s.run_interactive(f"software activate {release}", ((r"\(y/n\)\s*\??\s*$", "y\r"),), timeout=600)
        if re.search(r"^\s*(?:Error|%)", out, re.M):
            raise netcli.CliError(_("Activation refusée : %(o)s", o=out.strip().splitlines()[-1][:300]))
        log(_("Version %(r)s activée pour le prochain démarrage.", r=release))
    if reboot:
        _reboot_device(conn, row, log)
    else:
        log(_("Au prochain redémarrage, validez la version (software commit) dans le délai de validation automatique, sinon le switch reviendra à l'ancienne. Un redémarrage lancé depuis loco la valide de lui-même."))


def _firmware_sns(conn, row, job, log, fw, reboot):
    since = time.time()
    with api(row, timeout=120) as client:
        client.modify()
        log(_("Envoi de l'image au pare-feu…"))
        client.upload("SYSTEM UPDATE UPLOAD", firmware_path(fw), timeout=3600)
        status = client.command("SYSTEM UPDATE STATUS", check=False).section()
        log(_("Image déposée sur le pare-feu%(v)s.", v=(" (" + status.get("version") + ")") if status.get("version") else ""))
        if not reboot:
            log(_("Elle s'installera quand vous l'activerez : relancez l'installation avec « Redémarrer » coché."))
            return None
        client.command("SYSTEM UPDATE ACTIVATE", check=True, timeout=120)
        log(_("Activation lancée : le pare-feu installe le firmware puis redémarre."))
    found = _wait_back(conn, row, log, since, fw["version"])
    return found


FIRMWARE = {"ios": _firmware_ios, "exos": _firmware_exos, "voss": _firmware_voss, "sns": _firmware_sns}


def _firmware_job(conn, row, job, log, params):
    fw = conn.execute("SELECT * FROM net_firmware WHERE id=?", (params.get("firmware_id"),)).fetchone()
    family = fam(row)
    if fw is None or fw["family"] != row["family"]:
        raise netcli.LinkError(_("Ce firmware n'est plus dans la bibliothèque."))
    if not family.firmware:
        raise netcli.LinkError(_("loco n'installe pas de firmware sur cette famille d'équipements."))
    _check_file(fw, log)
    log(_("Sauvegarde de la configuration avant l'installation…"))
    _added, message = take_backup(conn, row, "avant le firmware " + fw["version"], job["created_by"])
    log(message)
    found = FIRMWARE[family.firmware](conn, row, job, log, fw, bool(params.get("reboot")))
    if found is None and params.get("reboot"):
        found = info_of(conn.execute("SELECT * FROM net_devices WHERE id=?", (row["id"],)).fetchone())
    if found is not None and params.get("reboot"):
        if not netfamilies.same_version(found.get("version", ""), fw["version"]):
            log(_("Attention : l'équipement annonce la version %(v)s, pas %(w)s.", v=found.get("version") or "?", w=fw["version"]))
            return "failed"
        log(_("Firmware %(v)s en service.", v=fw["version"]))
    return "success"


HANDLERS = {"backup": _backup_job, "firmware": _firmware_job, "reboot": _reboot_job}


def _work(dev_id):
    conn = connect()
    try:
        while True:
            with conn:
                job = conn.execute("SELECT * FROM net_jobs WHERE device_id=? AND state='queued' ORDER BY id LIMIT 1", (dev_id,)).fetchone()
                if job is None:
                    return
                conn.execute("UPDATE net_jobs SET state='running', started_at=? WHERE id=?", (_stamp(), job["id"]))
            row = conn.execute("SELECT * FROM net_devices WHERE id=?", (dev_id,)).fetchone()
            log, state = Log(conn, job["id"]), "failed"
            try:
                params = json.loads(job["params"] or "{}")
            except ValueError:
                params = {}
            try:
                if row is None or not linked(row):
                    raise netcli.LinkError(_("Liaison de l'équipement retirée."))
                state = HANDLERS[job["action"]](conn, row, job, log, params)
            except netcli.LinkError as e:
                log(str(e))
                if job["action"] == "backup":
                    with conn:
                        conn.execute("UPDATE net_devices SET backup_error=? WHERE id=?", (str(e)[:400], dev_id))
            except Exception as e:          # une tâche ne doit jamais rester « en cours » pour toujours
                log(f"{type(e).__name__}: {e}"[:500])
            with conn:
                conn.execute("UPDATE net_jobs SET state=?, output=?, finished_at=? WHERE id=?",
                             (state, log.text()[-8000:], _stamp(), job["id"]))
            if job["action"] != "backup":
                refresh(dev_id, conn)
    finally:
        conn.close()
        with _GUARD:
            _WORKERS.pop(dev_id, None)


def _tick():
    """Relevé régulier et sauvegardes planifiées des équipements reliés."""
    conn = connect()
    try:
        now, started = time.time(), set()
        for row in conn.execute("SELECT * FROM net_devices ORDER BY id").fetchall():
            if not linked(row):
                continue
            if now - (row["checked"] or 0) > POLL_EVERY:
                refresh_soon(row["id"])
            every = row["backup_every"] or 0
            if every and now - (row["last_backup"] or 0) > every and now - _TRIED.get(row["id"], 0) > RETRY_BACKUP:
                _TRIED[row["id"]] = now
                with conn:
                    if queue(conn, row["id"], "backup", {"reason": "planifiée"}, "planification"):
                        started.add(row["id"])
        for dev_id in started:
            work_soon(dev_id)
    finally:
        conn.close()


_started = False


def start():
    """Au démarrage du portail : les tâches restées « en cours » sont closes (une copie ou une installation ne se
    reprend pas à l'aveugle), celles en attente repartent ; puis relevés et sauvegardes planifiées, en fond."""
    global _started
    from . import ha
    try:
        for name in os.listdir(netcli.KEY_DIR):
            if name.startswith(("session-", "known-")):
                netcli.remove(os.path.join(netcli.KEY_DIR, name))
    except OSError:
        pass
    if _started or ha.skip_background():
        return
    _started = True
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE net_jobs SET state='failed', finished_at=?, output=output || ? WHERE state='running'",
                         (_stamp(), "\n" + "Interrompue par un redémarrage de loco : vérifiez l'état de l'équipement."))
        waiting = [r[0] for r in conn.execute("SELECT DISTINCT device_id FROM net_jobs WHERE state='queued'")]
    except Exception:            # base d'avant la migration
        waiting = []
    finally:
        conn.close()
    for dev_id in waiting:
        work_soon(dev_id)

    def loop():
        from . import features
        time.sleep(20)
        while True:
            try:
                if features.enabled("network") and not ha.skip_background() and lease("network"):
                    _tick()
            except Exception:
                pass
            time.sleep(TICK)
    threading.Thread(target=loop, name="loco-reseau", daemon=True).start()


# --------------------------------------------------------------------------- vues

def state_of(row, info, running, offer):
    """(couleur, libellé) de l'état d'un équipement, pour Mon parc et sa page."""
    if running:
        job = running[0]
        return "orange", str(ACTIONS[job["action"]]).lower() + " · " + (_("en cours") if job["state"] == "running" else _("en attente"))
    if not linked(row):
        return "grey", _("liaison à confirmer")
    if row["error"]:
        return "red", _("injoignable") if re.search(r"répond pas|injoignable|refuse la connexion", row["error"]) else _("liaison en échec")
    if info is None:
        return "grey", _("relevé en cours")
    if offer:
        return "orange", _("firmware %(v)s disponible", v=offer[0]["version"])
    if row["backup_error"]:
        return "orange", _("sauvegarde en échec")
    return "green", _("en ligne")


def resources(info, checked):
    from . import monitoring
    if not info or (info.get("cpu") is None and info.get("mem") is None):
        return None
    m = {"cpu": info.get("cpu"), "mem": info.get("mem"), "disk": None, "disk_mount": "", "only": ("cpu", "mem"),
         "stale": time.time() - (checked or 0) > 2 * POLL_EVERY,
         "age_text": time.strftime("%H:%M", time.localtime(checked)) if checked else "—"}
    m["levels"] = {k: monitoring.level(k, m[k]) for k in ("cpu", "mem", "disk")}
    return m


def view(row):
    info = info_of(row)
    jobs = db().execute("SELECT * FROM net_jobs WHERE device_id=? ORDER BY id DESC LIMIT 12", (row["id"],)).fetchall()
    running = [j for j in reversed(jobs) if j["state"] in ("queued", "running")]
    busy = {j["action"] for j in running}
    offer = offers(row, info)
    color, label = state_of(row, info, running, offer)
    family = fam(row)
    backups = db().execute("SELECT * FROM net_backups WHERE device_id=? ORDER BY id DESC LIMIT 60", (row["id"],)).fetchall()
    return {"row": row, "info": info, "family": family, "jobs": jobs, "running": running, "busy": busy, "offers": offer,
            "color": color, "label": label, "backups": backups, "linked": linked(row),
            "fingerprint": homeassistant.fingerprint(row["host_key"].split()[-1]) if row["host_key"] else "",
            "cert": pretty(row["api_cert"]) if row["api_cert"] else "",
            "checked": time.strftime("%H:%M", time.localtime(row["checked"])) if row["checked"] else "",
            "kind": netfamilies.KINDS[family.kind]}


def pretty(fingerprint):
    return ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))


def signature(v):
    r = v["row"]
    return hashlib.sha256(json.dumps([r["checked"], r["error"], r["host_key"], r["api_cert"], r["last_backup"], r["backup_error"],
                                      [(j["id"], j["state"], len(j["output"] or "")) for j in v["jobs"]],
                                      [b["id"] for b in v["backups"][:5]]]).encode()).hexdigest()[:16]


def fleet_rows():
    """Équipements montrés comme des lignes de Mon parc (fonctionnalité active, compte qui peut les consulter)."""
    from . import features
    if not features.enabled("network") or not can("network.view"):
        return []
    out, now = [], time.time()
    for row in db().execute("SELECT * FROM net_devices ORDER BY name COLLATE NOCASE, id").fetchall():
        if linked(row) and now - (row["checked"] or 0) > POLL_EVERY:
            refresh_soon(row["id"])
        info = info_of(row)
        running = active(db(), row["id"])
        offer = offers(row, info)
        color, label = state_of(row, info, running, offer)
        family = fam(row)
        out.append({"id": row["id"], "name": row["name"], "family": family.label, "kind": family.kind, "address": row["host"],
                    "model": (info or {}).get("model") or "", "version": (info or {}).get("version") or "",
                    "hostname": (info or {}).get("hostname") or "", "busy": bool(running), "offer": bool(offer),
                    "online": linked(row) and not row["error"] and info is not None, "color": color, "label": label,
                    "mon": resources(info, row["checked"])})
    return out


def attention():
    """Pour « À traiter » de la vue d'ensemble : équipement injoignable, sauvegarde en échec, firmware disponible."""
    rows = []
    for row in db().execute("SELECT * FROM net_devices ORDER BY name COLLATE NOCASE, id").fetchall():
        if not linked(row):
            continue
        url = url_for("network_device", dev_id=row["id"])
        if row["error"]:
            rows.append({"tone": "warn", "url": url, "text": _("« %(name)s » : %(e)s", name=row["name"], e=row["error"][:160])})
        elif row["backup_error"]:
            rows.append({"tone": "warn", "url": url, "text": _("« %(name)s » : la sauvegarde de la configuration échoue.", name=row["name"])})
        offer = offers(row, info_of(row))
        if offer:
            rows.append({"tone": "info", "url": url, "text": _("« %(name)s » : firmware %(v)s disponible dans la bibliothèque.", name=row["name"], v=offer[0]["version"])})
    return rows


def backups_summary():
    """Pour la page Sauvegardes : chaque équipement, sa dernière configuration gardée et la planification."""
    out = []
    for row in db().execute("SELECT * FROM net_devices ORDER BY name COLLATE NOCASE, id").fetchall():
        count = db().execute("SELECT COUNT(*) FROM net_backups WHERE device_id=?", (row["id"],)).fetchone()[0]
        out.append({"row": row, "count": count, "family": fam(row).label,
                    "every": every_label(row["backup_every"])})
    return out


def every_label(seconds):
    return {0: _("à la demande"), 6 * 3600: _("toutes les 6 heures"), 86400: _("chaque jour"),
            7 * 86400: _("chaque semaine")}.get(seconds or 0, _("toutes les %(n)s heures", n=(seconds or 0) // 3600))


# --------------------------------------------------------------------------- pages

def _device(dev_id):
    row = db().execute("SELECT * FROM net_devices WHERE id=?", (dev_id,)).fetchone()
    if row is None or row["family"] not in FAMILIES:
        abort(404)
    return row


@app.route("/network")
@login_required
def network():
    rows = fleet_rows()
    firmware = db().execute("SELECT * FROM net_firmware ORDER BY family, id DESC").fetchall()
    used = {}
    for row in db().execute("SELECT * FROM net_devices").fetchall():
        for fw in offers(row, info_of(row)):
            used[fw["id"]] = used.get(fw["id"], 0) + 1
    sig = hashlib.sha256(json.dumps([[r["id"], r["color"], r["label"], r["version"]] for r in rows] + [f["id"] for f in firmware]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": sig}
    return render_template("network.html", rows=rows, firmware=firmware, used=used, families=FAMILIES, signature=sig,
                           ssh=bool(homeassistant.ssh_binary()), every=EVERY, keep=KEEP, every_label=every_label)


def _fields(form, row=None):
    """Champs d'un équipement, vérifiés ; ValueError avec le message à montrer."""
    name = form.get("name", "").strip()
    family = form.get("family", row["family"] if row else "")
    host = form.get("host", "").strip()
    username = form.get("username", "").strip()
    if not NAME_RE.match(name):
        raise ValueError(_("Nom invalide (64 caractères au plus)."))
    if family not in FAMILIES:
        raise ValueError(_("Famille d'équipement inconnue."))
    if not homeassistant.HOST_RE.match(host) or host.startswith("-"):
        raise ValueError(_("Adresse invalide : un nom ou une adresse IP, sans port."))
    if not USER_RE.match(username):
        raise ValueError(_("Compte invalide : lettres, chiffres et . _ - @ \\ seulement."))
    try:
        port = int(form.get("port", "22") or 22)
        api_port = int(form.get("api_port", "443") or 443)
        every = int(form.get("backup_every", str(row["backup_every"] if row else 86400)))
        keep = int(form.get("backup_keep", str(row["backup_keep"] if row else 30)))
    except ValueError:
        raise ValueError(_("Port ou réglage invalide.")) from None
    if not (1 <= port <= 65535 and 1 <= api_port <= 65535):
        raise ValueError(_("Port invalide."))
    if every not in EVERY or keep not in KEEP:
        raise ValueError(_("Réglage de sauvegarde invalide."))
    secret_value = form.get("password", "")
    enable_value = form.get("enable", "")
    for value in (secret_value, enable_value):
        if len(value) > 256 or any(ord(c) < 32 for c in value):
            raise ValueError(_("Mot de passe invalide (256 caractères au plus, sans caractère de contrôle)."))
    secret_sealed = row["secret"] if row is not None and secret_value in ("", MASK) else seal(secret_value)
    enable_sealed = row["enable_secret"] if row is not None and enable_value in ("", MASK) else seal(enable_value)
    if form.get("clear_enable") == "1":
        enable_sealed = ""
    if not secret_sealed:
        raise ValueError(_("Le mot de passe du compte est requis."))
    return {"name": name, "family": family, "host": host, "port": port, "username": username, "secret": secret_sealed,
            "enable_secret": enable_sealed, "api_port": api_port, "backup_every": every, "backup_keep": keep}


def _probe(row):
    """Relève clé d'hôte (et certificat pour Stormshield) et la garde en attente de confirmation. Rend l'erreur ou ""."""
    family, pending, errors = fam(row), {"dev_id": row["id"], "at": time.time(), "nonce": secrets.token_hex(8)}, []
    try:
        kind, key = netcli.probe_host_key(row["host"], row["port"], row["username"])
        pending.update(type=kind, key=key, same=row["host_key"].split()[-1:] == [key])
    except netcli.LinkError as e:
        if not family.api:
            return str(e)
        errors.append(str(e))
    if family.api:
        try:
            print_, details = sns.probe_certificate(row["host"], row["api_port"])
            pending.update(cert=print_, cert_details=details, cert_same=hmac.compare_digest(print_, row["api_cert"] or ""))
        except netcli.LinkError as e:
            return str(e)
    session["network_pending"] = pending
    return ""


def _pending(dev_id):
    data = session.get("network_pending")
    if not isinstance(data, dict) or data.get("dev_id") != dev_id or time.time() - data.get("at", 0) > 900:
        return None
    return dict(data, fingerprint=homeassistant.fingerprint(data["key"]) if data.get("key") else "",
                cert_pretty=pretty(data["cert"]) if data.get("cert") else "")


@app.route("/network/add", methods=["POST"])
@login_required
def network_add():
    try:
        fields = _fields(request.form)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("network"))
    conn = db()
    if conn.execute("SELECT COUNT(*) FROM net_devices").fetchone()[0] >= 500:
        flash(_("Cinq cents équipements au plus."), "error")
        return redirect(url_for("network"))
    cur = conn.execute("INSERT INTO net_devices (name, family, host, port, username, secret, enable_secret, api_port, backup_every, backup_keep, "
                       "created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (fields["name"], fields["family"], fields["host"], fields["port"], fields["username"], fields["secret"],
                        fields["enable_secret"], fields["api_port"], fields["backup_every"], fields["backup_keep"], _stamp(), g.user["username"]))
    audit("network.ajout", f"{fields['name']} ({FAMILIES[fields['family']].label}, {fields['username']}@{fields['host']})", commit=False)
    conn.commit()
    row = _device(cur.lastrowid)
    error = _probe(row)
    if error:
        flash(_("Équipement ajouté, mais %(e)s", e=error), "error")
    return redirect(url_for("network_device", dev_id=row["id"]) + "#liaison")


@app.route("/network/<int:dev_id>")
@login_required
def network_device(dev_id):
    row = _device(dev_id)
    if linked(row) and time.time() - (row["checked"] or 0) > STALE:
        refresh_soon(dev_id)
    v = view(row)
    sig = signature(v)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": sig}
    return render_template("network_device.html", v=v, signature=sig, actions=ACTIONS, pending=_pending(dev_id), mask=MASK,
                           ssh=bool(homeassistant.ssh_binary()), terminal=homeassistant.terminal_ready(), families=FAMILIES,
                           every=EVERY, keep=KEEP, every_label=every_label)


@app.route("/network/<int:dev_id>/link", methods=["POST"])
@login_required
def network_link(dev_id):
    error = _probe(_device(dev_id))
    if error:
        flash(error, "error")
    return redirect(url_for("network_device", dev_id=dev_id) + "#liaison")


@app.route("/network/<int:dev_id>/confirm", methods=["POST"])
@login_required
def network_confirm(dev_id):
    """L'administrateur a comparé les empreintes : loco ne parlera qu'à un équipement qui présente cette clé-là."""
    row = _device(dev_id)
    data = _pending(dev_id)
    session.pop("network_pending", None)
    if request.form.get("cancel") == "1" or data is None:
        return redirect(url_for("network_device", dev_id=dev_id))
    if not secrets.compare_digest(request.form.get("nonce", ""), data["nonce"]):
        abort(400)
    host_key = f"{data['type']} {data['key']}" if data.get("key") else row["host_key"]
    cert = data.get("cert") or row["api_cert"]
    conn = db()
    conn.execute("UPDATE net_devices SET host_key=?, api_cert=?, error='' WHERE id=?", (host_key, cert, dev_id))
    audit("network.liaison", f"{row['name']} ({row['username']}@{row['host']})"
          + (f", clé d'hôte {data['fingerprint']}" if data.get("key") else "") + (f", certificat {data['cert_pretty']}" if data.get("cert") else ""),
          commit=False)
    conn.commit()
    error = refresh(dev_id)
    flash(_("Liaison confirmée : l'équipement répond.") if not error else _("Empreinte enregistrée, mais la liaison échoue : %(e)s", e=error),
          "ok" if not error else "error")
    return redirect(url_for("network_device", dev_id=dev_id))


@app.route("/network/<int:dev_id>/settings", methods=["POST"])
@login_required
def network_settings(dev_id):
    row = _device(dev_id)
    try:
        fields = _fields(dict(request.form, family=row["family"]), row)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("network_device", dev_id=dev_id) + "#reglages")
    moved = fields["host"] != row["host"] or fields["port"] != row["port"] or fields["api_port"] != row["api_port"]
    conn = db()
    conn.execute("UPDATE net_devices SET name=?, host=?, port=?, username=?, secret=?, enable_secret=?, api_port=?, backup_every=?, backup_keep=?, "
                 "error='' WHERE id=?", (fields["name"], fields["host"], fields["port"], fields["username"], fields["secret"],
                                        fields["enable_secret"], fields["api_port"], fields["backup_every"], fields["backup_keep"], dev_id))
    if moved:                                   # autre adresse : clé et certificat à confirmer de nouveau
        conn.execute("UPDATE net_devices SET host_key='', api_cert='' WHERE id=?", (dev_id,))
    changed = [k for k in ("name", "host", "port", "username", "api_port", "backup_every", "backup_keep") if fields[k] != row[k]]
    if fields["secret"] != row["secret"]:
        changed.append("mot de passe")
    if fields["enable_secret"] != row["enable_secret"]:
        changed.append("mot de passe enable")
    audit("network.reglages", f"{row['name']} : {', '.join(changed) or 'rien'}", commit=False)
    conn.commit()
    prune(conn, _device(dev_id))
    if moved:
        error = _probe(_device(dev_id))
        if error:
            flash(error, "error")
        return redirect(url_for("network_device", dev_id=dev_id) + "#liaison")
    flash(_("Réglages enregistrés."), "ok")
    if linked(row):
        refresh_soon(dev_id)
    return redirect(url_for("network_device", dev_id=dev_id))


@app.route("/network/<int:dev_id>/delete", methods=["POST"])
@login_required
def network_delete(dev_id):
    row = _device(dev_id)
    conn = db()
    if active(conn, dev_id):
        flash(_("Une tâche est en cours sur cet équipement : attendez sa fin."), "error")
        return redirect(url_for("network_device", dev_id=dev_id))
    for b in conn.execute("SELECT file FROM net_backups WHERE device_id=?", (dev_id,)).fetchall():
        netcli.remove(os.path.join(CONFIG_DIR, b["file"]))
    try:
        os.rmdir(os.path.join(CONFIG_DIR, str(dev_id)))
    except OSError:
        pass
    close_sessions(conn, dev_id, "Équipement retiré.")
    conn.execute("DELETE FROM net_devices WHERE id=?", (dev_id,))
    audit("network.retrait", row["name"], commit=False)
    conn.commit()
    flash(_("Équipement retiré de loco, avec ses sauvegardes de configuration."), "ok")
    return redirect(url_for("network"))


@app.route("/network/<int:dev_id>/refresh", methods=["POST"])
@login_required
def network_refresh(dev_id):
    row = _device(dev_id)
    error = refresh(dev_id, db()) if linked(row) else ""
    if error:
        flash(error, "error")
    return redirect(url_for("network_device", dev_id=dev_id))


def _launch(row, action, params=None):
    conn = db()
    added = queue(conn, row["id"], action, params, g.user["username"])
    if added:
        audit("network." + action, row["name"] + (f" ({params.get('label')})" if params and params.get("label") else ""), commit=False)
    conn.commit()
    if added:
        work_soon(row["id"])
    return added


@app.route("/network/<int:dev_id>/backup", methods=["POST"])
@login_required
def network_backup(dev_id):
    row = _device(dev_id)
    if not linked(row):
        abort(409)
    added = _launch(row, "backup", {"reason": "manuelle"})
    flash(_("Sauvegarde de la configuration lancée.") if added else _("Une sauvegarde est déjà en cours."), "ok" if added else "error")
    return redirect(url_for("network_device", dev_id=dev_id))


@app.route("/network/<int:dev_id>/reboot", methods=["POST"])
@login_required
def network_reboot(dev_id):
    row = _device(dev_id)
    if not linked(row):
        abort(409)
    added = _launch(row, "reboot")
    flash(_("Redémarrage lancé : loco suit le retour de l'équipement.") if added else _("Ce redémarrage est déjà en cours."), "ok" if added else "error")
    return redirect(url_for("network_device", dev_id=dev_id))


@app.route("/network/<int:dev_id>/firmware", methods=["POST"])
@login_required
def network_firmware(dev_id):
    row = _device(dev_id)
    family = fam(row)
    fw = db().execute("SELECT * FROM net_firmware WHERE id=? AND family=?", (request.form.get("firmware_id", type=int), row["family"])).fetchone()
    if not linked(row) or fw is None or not family.firmware:
        abort(409)
    reboot = request.form.get("reboot") == "1"
    if reboot:
        require("network.restart")
    added = _launch(row, "firmware", {"firmware_id": fw["id"], "reboot": reboot, "label": fw["version"]})
    flash(_("Installation du firmware %(v)s lancée.", v=fw["version"]) if added else _("Une installation est déjà en cours."), "ok" if added else "error")
    return redirect(url_for("network_device", dev_id=dev_id))


def _backup_row(dev_id, backup_id):
    b = db().execute("SELECT * FROM net_backups WHERE id=? AND device_id=?", (backup_id, dev_id)).fetchone()
    if b is None:
        abort(404)
    return b


@app.route("/network/<int:dev_id>/config/<int:backup_id>")
@login_required
def network_config(dev_id, backup_id):
    """Une configuration gardée, et ce qui a changé par rapport à une autre (la précédente par défaut)."""
    row, b = _device(dev_id), _backup_row(dev_id, backup_id)
    content = read_backup(b)
    if content is None:
        abort(410)
    others = db().execute("SELECT * FROM net_backups WHERE device_id=? AND binary=0 ORDER BY id DESC LIMIT 60", (dev_id,)).fetchall()
    against = request.args.get("contre", type=int)
    base = None
    if not b["binary"]:
        if against:
            base = next((o for o in others if o["id"] == against), None)
        else:
            base = db().execute("SELECT * FROM net_backups WHERE device_id=? AND id<? AND binary=0 ORDER BY id DESC LIMIT 1", (dev_id, b["id"])).fetchone()
    diff, text = None, ""
    if not b["binary"]:
        text = content.decode("utf-8", "replace")
        if base is not None:
            old = read_backup(base)
            diff = diff_lines(old.decode("utf-8", "replace"), text) if old is not None else None
    audit("network.consultation", f"{row['name']}, configuration du {time.strftime('%Y-%m-%d %H:%M', time.localtime(b['taken_at']))}")
    return render_template("network_config.html", row=row, b=b, base=base, others=others, diff=diff, text=text,
                           view=request.args.get("vue", "diff" if diff is not None else "texte"))


@app.route("/network/<int:dev_id>/config/<int:backup_id>/download")
@login_required
def network_config_download(dev_id, backup_id):
    row, b = _device(dev_id), _backup_row(dev_id, backup_id)
    content = read_backup(b)
    if content is None:
        abort(410)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", row["name"]).strip("-") or "equipement"
    name = f"{stem}-{time.strftime('%Y%m%d-%H%M', time.localtime(b['taken_at']))}.{'na' if b['binary'] else 'cfg'}"
    audit("network.telechargement", f"{row['name']}, {name}")
    return Response(content, mimetype="application/octet-stream" if b["binary"] else "text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.route("/network/firmware/upload", methods=["POST"])
@login_required
def network_firmware_upload():
    upload = request.files.get("file")
    family = request.form.get("family", "")
    if family not in FAMILIES or upload is None or not upload.filename:
        flash(_("Choisissez la famille et le fichier du firmware."), "error")
        return redirect(url_for("network") + "#firmwares")
    filename = os.path.basename(upload.filename.replace("\\", "/"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", filename):
        flash(_("Nom de fichier invalide : lettres, chiffres et . _ - + seulement."), "error")
        return redirect(url_for("network") + "#firmwares")
    version = request.form.get("version", "").strip() or netfamilies.firmware_version(filename)
    models = request.form.get("models", "").strip()
    if not VERSION_RE.match(version):
        flash(_("Indiquez la version du firmware (elle n'a pas pu être lue dans le nom du fichier)."), "error")
        return redirect(url_for("network") + "#firmwares")
    if not MODELS_RE.match(models):
        flash(_("Modèles concernés invalides : des noms ou des motifs (C9300-*, X440G2*), séparés par des virgules."), "error")
        return redirect(url_for("network") + "#firmwares")
    os.makedirs(FIRMWARE_DIR, exist_ok=True)
    tmp = os.path.join(FIRMWARE_DIR, ".envoi-" + secrets.token_hex(8))
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    try:
        with open(tmp, "wb") as f:
            for chunk in iter(lambda: upload.stream.read(1 << 20), b""):
                f.write(chunk)
                sha.update(chunk)
                md5.update(chunk)
                size += len(chunk)
        if size < 1024:
            raise ValueError(_("Fichier trop petit pour être un firmware."))
        digest = sha.hexdigest()
        if db().execute("SELECT 1 FROM net_firmware WHERE sha256=?", (digest,)).fetchone():
            raise ValueError(_("Ce fichier est déjà dans la bibliothèque."))
        stored = digest[:16] + "-" + filename
        os.replace(tmp, os.path.join(FIRMWARE_DIR, stored))
    except (OSError, ValueError) as e:
        netcli.remove(tmp)
        flash(str(e) if isinstance(e, ValueError) else _("Enregistrement impossible : %(e)s", e=e), "error")
        return redirect(url_for("network") + "#firmwares")
    conn = db()
    conn.execute("INSERT INTO net_firmware (family, version, models, filename, size, sha256, md5, file, uploaded_at, uploaded_by) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", (family, version, models, filename, size, digest, md5.hexdigest(), stored, _stamp(), g.user["username"]))
    audit("network.firmware-depot", f"{FAMILIES[family].label} {version} ({filename}, SHA-256 {digest})", commit=False)
    conn.commit()
    flash(_("Firmware %(v)s ajouté à la bibliothèque. Comparez son empreinte SHA-256 à celle publiée par le constructeur : %(h)s", v=version, h=digest), "ok")
    return redirect(url_for("network") + "#firmwares")


@app.route("/network/firmware/<int:fw_id>/delete", methods=["POST"])
@login_required
def network_firmware_delete(fw_id):
    conn = db()
    fw = conn.execute("SELECT * FROM net_firmware WHERE id=?", (fw_id,)).fetchone()
    if fw is None:
        abort(404)
    busy = [j for j in conn.execute("SELECT params FROM net_jobs WHERE action='firmware' AND state IN ('queued','running')").fetchall()
            if json.loads(j["params"] or "{}").get("firmware_id") == fw_id]
    if busy:
        flash(_("Ce firmware est en cours d'installation : attendez la fin."), "error")
        return redirect(url_for("network") + "#firmwares")
    netcli.remove(firmware_path(fw))
    conn.execute("DELETE FROM net_firmware WHERE id=?", (fw_id,))
    audit("network.firmware-retrait", f"{FAMILIES[fw['family']].label if fw['family'] in FAMILIES else fw['family']} {fw['version']} ({fw['filename']})", commit=False)
    conn.commit()
    flash(_("Firmware retiré de la bibliothèque."), "ok")
    return redirect(url_for("network") + "#firmwares")


def close_sessions(conn, dev_id, reason):
    from . import remote
    remote.close_server_sessions(conn, f"net:{dev_id}", reason)


@app.route("/network/<int:dev_id>/shell", methods=["POST"])
@login_required
def network_shell(dev_id):
    """Terminal de l'équipement : une session distante tenue par loco (ssh dans un pseudo-terminal), mot de passe remis
    par loco — l'élévation des droits (enable) se tape dans le terminal."""
    from . import remote
    row = _device(dev_id)
    if not homeassistant.ssh_binary() or not row["host_key"] or not homeassistant.terminal_ready():
        abort(409, _("La liaison SSH avec cet équipement n'est pas prête."))
    password = _password(row)
    return remote.open_server_session(
        f"net:{dev_id}", row["name"], "network.terminal", netcli.KEY_DIR,
        f"{homeassistant._pattern(row['host'], row['port'])} {row['host_key']}\n",
        lambda known: netcli.base_argv(row["host"], row["port"], row["username"], known) + ["-tt", "--", row["host"]],
        netcli.environment(password, term="xterm-256color"))
