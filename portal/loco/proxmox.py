"""Proxmox VE (fonctionnalité facultative) : sauvegarde des machines virtuelles et des conteneurs, à la demande ou planifiée.

loco se relie à Proxmox par son API, avec un jeton d'API créé pour lui (identifiant « utilisateur@royaume!nom » et
secret) : le secret est scellé dans la base, ne passe jamais en argument d'une commande ni dans le journal, et n'est
jamais réaffiché. Le certificat de Proxmox n'est jamais accepté d'office : son empreinte SHA-256 est montrée, un
administrateur la compare à celle qu'affiche Proxmox (nœud › Système › Certificats) et la confirme ; le jeton n'est
ensuite envoyé qu'à un serveur qui présente ce certificat-là, vérifié avant le moindre octet de la requête.

Proxmox écrit lui-même les sauvegardes (vzdump) sur l'un de ses stockages : disque local, NAS déclaré dans Proxmox (NFS,
SMB) ou Proxmox Backup Server. loco lance une tâche par machine, la suit par son identifiant (UPID) — y compris après un
redémarrage de loco, la tâche continuant sur Proxmox — et applique la conservation d'une planification aux seules
sauvegardes de cette planification, repérées par leur note : celles faites autrement ne sont jamais supprimées.
"""
import datetime
import hashlib
import hmac
import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.parse

from flask import abort, flash, g, request

from . import app, config
from .i18n import L_, _, n_
from .auth import audit, login_required
from .db import connect, db
from .util import back, sealer

DEFAULT_PORT = 8006
HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,45})$")     # nom, IPv4 ou IPv6
TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}@[A-Za-z0-9._-]{1,64}![A-Za-z][A-Za-z0-9._-]{0,63}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")
STORAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,62}[A-Za-z0-9]$")
NODE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$")
UPID_RE = re.compile(r"^UPID:[A-Za-z0-9.-]{1,63}:[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}:[a-z0-9]{1,32}:[^:\s]{0,64}:[^:\s]{1,140}:$")
MODES = {"snapshot": L_("Instantané, sans arrêt"), "suspend": L_("Suspension"), "stop": L_("Arrêt puis redémarrage")}
# Droits que demande la sauvegarde : voir les machines et les stockages, sauvegarder, écrire et supprimer ses sauvegardes.
PRIVILEGES = ("VM.Audit", "VM.Backup", "Datastore.Audit", "Datastore.AllocateSpace", "Sys.Audit")
ROLE = "LocoBackup"
MAX_HOSTS = 8
STALE = 120                     # relevé plus ancien : refait en arrière-plan tant que la page est affichée
OVERVIEW_STALE = 1800
OFFER_TTL = 900                 # empreinte relevée, en attente de confirmation : oubliée au-delà
LIMIT = 8 * 1024 ** 2           # réponse de l'API lue au plus
POLL, LOST, TASK_MAX = 5, 900, 24 * 3600        # suivi d'une tâche : pas, coupure tolérée, durée au plus
MAX_GUESTS, MAX_BACKUPS = 500, 12               # gardés dans le relevé : machines, sauvegardes par machine
CHANGED = L_("Proxmox ne présente plus le certificat confirmé : vérifiez qu'il s'agit bien de lui (certificat renouvelé), puis confirmez le nouveau.")

_GUARD = threading.Lock()
_WORKERS = {}
_REFRESH = set()


class ProxmoxError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- secret du jeton

_seal, _unseal = sealer(b"loco-proxmox-v1")        # secret du jeton scellé avec la clé du serveur


# --------------------------------------------------------------------------- liaison HTTPS épinglée

def _context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE            # authentifié par l'empreinte confirmée, vérifiée avant tout envoi
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def pretty(fingerprint):
    """Empreinte écrite comme Proxmox l'affiche : octets en hexadécimal majuscule séparés par « : »."""
    return ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))


def _certificate(der):
    """Nom, émetteur et fin de validité du certificat, pour aider à le reconnaître (l'empreinte seule fait foi)."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        cert = x509.load_der_x509_certificate(der)

        def common(name):
            found = name.get_attributes_for_oid(NameOID.COMMON_NAME)
            return str(found[0].value)[:120] if found else ""
        end = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        return {"subject": common(cert.subject), "issuer": common(cert.issuer), "until": end.strftime("%Y-%m-%d")}
    except Exception:
        return {"subject": "", "issuer": "", "until": ""}


def probe_certificate(host, port):
    """Certificat que présente l'adresse : (empreinte SHA-256 en hexadécimal majuscule, détails). Seule la poignée de main
    TLS a lieu : rien d'autre n'est envoyé, et surtout pas le jeton."""
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with _context().wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        raise ProxmoxError(_("Proxmox ne répond pas en HTTPS à %(host)s:%(port)s (%(e)s).", host=host, port=port,
                             e=(getattr(e, "strerror", None) or str(e))[:120])) from e
    if not der:
        raise ProxmoxError(_("Aucun certificat présenté par %(host)s:%(port)s.", host=host, port=port))
    return hashlib.sha256(der).hexdigest().upper(), _certificate(der)


def _reason(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()[:240]


def request_api(row, method, path, params=None, timeout=20):
    """Appel de l'API de Proxmox avec le jeton de l'instance : renvoie le champ « data » de la réponse."""
    if not row["fingerprint"]:
        raise ProxmoxError(_("Le certificat de Proxmox n'a pas encore été confirmé."))
    secret = _unseal(row["secret"])
    if not secret:
        raise ProxmoxError(_("Le secret du jeton est illisible (clé du serveur changée) : reliez de nouveau ce Proxmox."))
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = "/api2/json" + path + ("?" + query if query and method in ("GET", "DELETE") else "")
    body = query.encode() if query and method not in ("GET", "DELETE") else None
    headers = {"Authorization": f"PVEAPIToken={row['token_id']}={secret}", "Accept": "application/json",
               "User-Agent": config.USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPSConnection(row["host"], row["port"], context=_context(), timeout=timeout)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        if not der or not hmac.compare_digest(hashlib.sha256(der).hexdigest().upper(), row["fingerprint"]):
            raise ProxmoxError(_(CHANGED), status=-1)
        conn.request(method, url, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read(LIMIT + 1)
        status, reason = response.status, response.reason
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        raise ProxmoxError(_("Proxmox ne répond pas (%(e)s).", e=_reason(getattr(e, "strerror", None) or e)[:120] or type(e).__name__)) from e
    finally:
        conn.close()
    if len(payload) > LIMIT:
        raise ProxmoxError(_("Réponse de Proxmox trop grande."))
    try:
        data = json.loads(payload or b"{}")
    except ValueError:
        data = {}
    if status == 401:
        raise ProxmoxError(_("Proxmox refuse le jeton : vérifiez son identifiant et son secret, et qu'il n'a pas expiré."), status)
    if status == 403:
        raise ProxmoxError(_("Droit refusé au jeton par Proxmox : %(reason)s", reason=_reason(reason)), status)
    if status != 200:
        detail = data.get("errors") if isinstance(data, dict) else None
        extra = "; ".join(f"{k} : {v}" for k, v in detail.items())[:200] if isinstance(detail, dict) else ""
        raise ProxmoxError(_("Proxmox a répondu HTTP %(code)s : %(reason)s", code=status,
                             reason=_reason(reason) + (f" ({_reason(extra)})" if extra else "")), status)
    if not isinstance(data, dict):
        raise ProxmoxError(_("Réponse inattendue : ce n'est pas l'API de Proxmox."))
    return data.get("data")


# --------------------------------------------------------------------------- relevé

def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value, limit=120):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value if value is not None else "")).strip()[:limit]


def _collect(row):
    version = request_api(row, "GET", "/version") or {}
    guests = request_api(row, "GET", "/cluster/resources", {"type": "vm"}) or []
    storages = request_api(row, "GET", "/cluster/resources", {"type": "storage"}) or []
    try:
        granted = request_api(row, "GET", "/access/permissions") or {}
    except ProxmoxError:
        granted = None
    if not isinstance(version, dict) or not isinstance(guests, list) or not isinstance(storages, list):
        raise ProxmoxError(_("Réponse inattendue : ce n'est pas l'API de Proxmox."))
    found = {"version": _text(version.get("version"), 40), "guests": [], "storages": [], "nodes": [], "missing": []}
    if isinstance(granted, dict):
        privileges = set()
        for path, privs in granted.items():
            if isinstance(privs, dict):
                privileges.update(k for k, v in privs.items() if v)
        found["missing"] = [p for p in PRIVILEGES if p not in privileges]
    nodes, listed = set(), set()
    # Ressources des nœuds (processeur, mémoire, disque système), montrées dans Mon parc : un seul appel pour tous.
    try:
        listed_nodes = request_api(row, "GET", "/nodes") or []
    except ProxmoxError:
        listed_nodes = []
    found["node_status"] = {}
    for n in listed_nodes if isinstance(listed_nodes, list) else []:
        if not isinstance(n, dict) or not NODE_RE.match(str(n.get("node", ""))):
            continue
        cpu = n.get("cpu")
        found["node_status"][n["node"]] = {
            "online": n.get("status") == "online", "cpu": round(float(cpu) * 100, 1) if isinstance(cpu, (int, float)) else None,
            "cpus": _int(n.get("maxcpu")), "mem": _int(n.get("mem")), "mem_total": _int(n.get("maxmem")),
            "disk": _int(n.get("disk")), "disk_total": _int(n.get("maxdisk")), "uptime": _int(n.get("uptime"))}
        nodes.add(n["node"])
    for s in storages:
        if not isinstance(s, dict) or not STORAGE_RE.match(str(s.get("storage", ""))) or not NODE_RE.match(str(s.get("node", ""))):
            continue
        content = {c for c in str(s.get("content", "")).split(",") if c}
        maxdisk, disk = _int(s.get("maxdisk")), _int(s.get("disk"))
        found["storages"].append({"storage": s["storage"], "node": s["node"], "type": _text(s.get("plugintype"), 20),
                                  "shared": bool(_int(s.get("shared"))), "backup": "backup" in content,
                                  "available": s.get("status") == "available", "total": maxdisk, "free": max(0, maxdisk - disk)})
        nodes.add(s["node"])
    backups = {}
    for s in found["storages"]:
        if not s["backup"] or not s["available"] or (s["shared"] and s["storage"] in listed):
            continue
        listed.add(s["storage"])
        try:
            items = request_api(row, "GET", f"/nodes/{s['node']}/storage/{s['storage']}/content", {"content": "backup"}) or []
        except ProxmoxError:
            continue
        for item in items if isinstance(items, list) else []:
            vmid = _int(item.get("vmid") if isinstance(item, dict) else None, -1)
            if vmid < 0:
                continue
            backups.setdefault(vmid, []).append({"volid": _text(item.get("volid"), 200), "storage": s["storage"],
                                                 "node": s["node"], "ctime": _int(item.get("ctime")), "size": _int(item.get("size")),
                                                 "notes": _text(item.get("notes"), 120), "protected": bool(_int(item.get("protected")))})
    for v in guests[:MAX_GUESTS]:
        if not isinstance(v, dict) or v.get("type") not in ("qemu", "lxc") or not NODE_RE.match(str(v.get("node", ""))):
            continue
        vmid = _int(v.get("vmid"), -1)
        if not 100 <= vmid <= 999999999:
            continue
        mine = sorted(backups.get(vmid, []), key=lambda b: b["ctime"], reverse=True)
        found["guests"].append({"vmid": vmid, "name": _text(v.get("name"), 64) or str(vmid), "type": v["type"],
                                "node": v["node"], "status": _text(v.get("status"), 20), "template": bool(_int(v.get("template"))),
                                "backups": mine[:MAX_BACKUPS], "count": len(mine)})
        nodes.add(v["node"])
    found["guests"].sort(key=lambda v: v["vmid"])
    found["storages"].sort(key=lambda s: (not s["backup"], s["storage"], s["node"]))
    found["nodes"] = sorted(nodes)
    return found


def refresh(host_id, conn=None):
    """Refait le relevé d'une instance ; renvoie le message d'erreur, ou une chaîne vide."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
        if row is None or not row["fingerprint"]:
            return ""
        try:
            info, error = json.dumps(_collect(row)), ""
        except ProxmoxError as e:
            info, error = row["info"], str(e)
        with conn:
            conn.execute("UPDATE proxmox_hosts SET info=?, error=?, checked=? WHERE id=?", (info, error, time.time(), host_id))
        return error
    finally:
        if own:
            conn.close()


def refresh_soon(host_id):
    with _GUARD:
        if host_id in _REFRESH:
            return
        _REFRESH.add(host_id)

    def run():
        try:
            refresh(host_id)
        except Exception:
            pass
        finally:
            with _GUARD:
                _REFRESH.discard(host_id)
    threading.Thread(target=run, name="loco-proxmox-releve", daemon=True).start()


def info(row):
    try:
        data = json.loads(row["info"]) if row["info"] else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("guests"), list) else None


def backup_storages(data, node=None):
    """Stockages où Proxmox peut écrire une sauvegarde (pour un nœud donné : les siens et les partagés)."""
    seen, out = set(), []
    for s in (data or {}).get("storages", []):
        if s["backup"] and s["available"] and (node is None or s["node"] == node or s["shared"]) and s["storage"] not in seen:
            seen.add(s["storage"])
            out.append(s)
    return out


# --------------------------------------------------------------------------- tâches de sauvegarde

def note(job):
    """Note posée sur la sauvegarde : c'est elle qui désigne les sauvegardes d'une planification à la conservation."""
    return (f"loco plan {job['plan_id']} " if job["plan_id"] else "loco ") + "{{guestname}}"


def active(conn, host_id):
    return conn.execute("SELECT * FROM proxmox_jobs WHERE host_id=? AND state IN ('queued','running') ORDER BY id",
                        (host_id,)).fetchall()


def queue(conn, row, vmids, storage, mode, keep=0, plan_id=None, user=""):
    """Une tâche par machine choisie, sauf celles déjà en attente ou en cours. Renvoie (ajoutées, refusées)."""
    data = info(row) or {}
    index = {v["vmid"]: v for v in data.get("guests", [])}
    busy = {j["vmid"] for j in active(conn, row["id"])}
    added, refused = 0, []
    for vmid in vmids:
        guest = index.get(vmid)
        if guest is None or vmid in busy:
            refused.append(str(vmid))
            continue
        if storage not in {s["storage"] for s in backup_storages(data, guest["node"])}:
            refused.append(str(vmid))
            continue
        conn.execute("INSERT INTO proxmox_jobs (host_id, plan_id, node, vmid, guest, storage, mode, keep, created_at, created_by) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (row["id"], plan_id, guest["node"], vmid, guest["name"], storage,
                                                      mode if mode in MODES else "snapshot", max(0, keep), _stamp(), user))
        busy.add(vmid)
        added += 1
    return added, refused


def work_soon(host_id):
    with _GUARD:
        if host_id in _WORKERS:
            return
        worker = threading.Thread(target=_work, args=(host_id,), name="loco-proxmox-taches", daemon=True)
        _WORKERS[host_id] = worker
    worker.start()


def _log(row, node, upid):
    try:
        lines = request_api(row, "GET", f"/nodes/{node}/tasks/{urllib.parse.quote(upid, safe='')}/log", {"limit": 400}) or []
    except ProxmoxError:
        return ""
    return "\n".join(_text(line.get("t"), 400) for line in lines if isinstance(line, dict))[-4000:]


def _follow(conn, row, job):
    """Suit la tâche Proxmox jusqu'à sa fin : (état, sortie). Une coupure passagère ne l'interrompt pas — la tâche
    continue sur Proxmox — tant qu'elle ne dure pas plus de LOST secondes."""
    path = f"/nodes/{job['node']}/tasks/{urllib.parse.quote(job['upid'], safe='')}/status"
    started, failing = time.time(), None
    while time.time() - started < TASK_MAX:
        try:
            status = request_api(row, "GET", path) or {}
            failing = None
        except ProxmoxError as e:
            if e.status in (-1, 401, 403, 404) or failing and time.time() - failing > LOST:
                return "failed", _("loco a perdu le suivi de la tâche (%(e)s) : voyez son résultat dans Proxmox.", e=str(e))
            failing = failing or time.time()
            time.sleep(POLL)
            continue
        if status.get("status") == "stopped":
            exit_status = _text(status.get("exitstatus"), 200)
            output = _log(row, job["node"], job["upid"])
            if exit_status == "OK" or exit_status.startswith("WARNINGS"):
                return "success", output
            return "failed", (output + "\n" + exit_status).strip()
        time.sleep(POLL)
    return "failed", _("La sauvegarde dure plus de 24 heures : loco cesse de la suivre ; voyez son résultat dans Proxmox.")


def _prune(row, job):
    """Conservation d'une planification : ses sauvegardes de la machine sur ce stockage au-delà des « keep » plus récentes
    sont supprimées. Les sauvegardes protégées, et celles qu'elle n'a pas faites, ne sont jamais touchées."""
    prefix = f"loco plan {job['plan_id']} "
    items = request_api(row, "GET", f"/nodes/{job['node']}/storage/{job['storage']}/content",
                        {"content": "backup", "vmid": job["vmid"]}) or []
    mine = sorted((i for i in items if isinstance(i, dict) and _int(i.get("vmid"), -1) == job["vmid"]
                   and str(i.get("notes") or "").startswith(prefix) and not _int(i.get("protected"))),
                  key=lambda i: _int(i.get("ctime")), reverse=True)
    removed = []
    for old in mine[job["keep"]:]:
        volid = str(old.get("volid", ""))
        request_api(row, "DELETE", f"/nodes/{job['node']}/storage/{job['storage']}/content/{urllib.parse.quote(volid, safe='')}")
        removed.append(volid)
    return removed


def _run(conn, row, job):
    if not job["upid"]:
        params = {"vmid": job["vmid"], "storage": job["storage"], "mode": job["mode"], "compress": "zstd",
                  "notes-template": note(job)}
        upid = request_api(row, "POST", f"/nodes/{job['node']}/vzdump", params, timeout=60)
        if not isinstance(upid, str) or not UPID_RE.match(upid):
            raise ProxmoxError(_("Proxmox n'a pas lancé la sauvegarde (réponse inattendue)."))
        with conn:
            conn.execute("UPDATE proxmox_jobs SET upid=? WHERE id=?", (upid, job["id"]))
        job = conn.execute("SELECT * FROM proxmox_jobs WHERE id=?", (job["id"],)).fetchone()
    state, output = _follow(conn, row, job)
    if state == "success" and job["plan_id"] and job["keep"] > 0:
        try:
            removed = _prune(row, job)
            if removed:
                output = (output + "\n" + n_("Conservation : %(num)s sauvegarde plus ancienne supprimée.",
                                               "Conservation : %(num)s sauvegardes plus anciennes supprimées.", len(removed))).strip()
        except ProxmoxError as e:
            output = (output + "\n" + _("Conservation non appliquée : %(e)s", e=str(e))).strip()
    return state, output


def _work(host_id):
    """Exécute les sauvegardes d'une instance l'une après l'autre ; une tâche déjà lancée (UPID connu) est reprise."""
    conn = connect()
    try:
        while True:
            with conn:
                job = conn.execute("SELECT * FROM proxmox_jobs WHERE host_id=? AND state IN ('queued','running') "
                                   "ORDER BY state='running' DESC, id LIMIT 1", (host_id,)).fetchone()
                if job is None:
                    return
                if job["state"] == "queued":
                    conn.execute("UPDATE proxmox_jobs SET state='running', started_at=? WHERE id=?", (_stamp(), job["id"]))
            row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
            try:
                if row is None:
                    raise ProxmoxError(_("Instance retirée."))
                state, output = _run(conn, row, job)
            except ProxmoxError as e:
                state, output = "failed", str(e)
            except Exception as e:                  # jamais de tâche bloquée « en cours »
                state, output = "failed", f"{type(e).__name__}: {e}"[:300]
            with conn:
                conn.execute("UPDATE proxmox_jobs SET state=?, output=?, finished_at=? WHERE id=? AND state='running'",
                             (state, output.strip()[-4000:], _stamp(), job["id"]))
            refresh(host_id, conn)
    finally:
        conn.close()
        with _GUARD:
            _WORKERS.pop(host_id, None)
        check = connect()
        try:
            again = check.execute("SELECT 1 FROM proxmox_jobs WHERE host_id=? AND state IN ('queued','running')", (host_id,)).fetchone()
        finally:
            check.close()
        if again:
            work_soon(host_id)


def start():
    """Au démarrage du portail : les sauvegardes lancées continuent sur Proxmox, leur suivi reprend ; celles en attente
    partent. Une tâche « en cours » sans identifiant n'a pas atteint Proxmox : elle repart du début."""
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE proxmox_jobs SET state='queued' WHERE state='running' AND upid=''")
        waiting = [r[0] for r in conn.execute("SELECT DISTINCT host_id FROM proxmox_jobs WHERE state IN ('queued','running')")]
    except Exception:            # base d'avant la migration : rien à reprendre
        waiting = []
    finally:
        conn.close()
    for host_id in waiting:
        work_soon(host_id)


# --------------------------------------------------------------------------- pages (dans la page Sauvegardes)

def _host(host_id):
    row = db().execute("SELECT * FROM proxmox_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _age(stamp):
    if not stamp:
        return ""
    return datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


def view(row):
    """Une instance telle que la page la montre : relevé, machines et leur dernière sauvegarde, tâches, planifications."""
    data = info(row)
    jobs = db().execute("SELECT * FROM proxmox_jobs WHERE host_id=? ORDER BY id DESC LIMIT 15", (row["id"],)).fetchall()
    running = {j["vmid"]: j["state"] for j in jobs if j["state"] in ("queued", "running")}
    guests = []
    for v in (data or {}).get("guests", []):
        last = v["backups"][0] if v["backups"] else None
        guests.append(dict(v, job=running.get(v["vmid"], ""),
                           last=dict(last, when=_age(last["ctime"])) if last else None,
                           storages=[s["storage"] for s in backup_storages(data, v["node"])]))
    offered = row["offered"] if row["offered"] and time.time() - row["offered_at"] < OFFER_TTL else ""
    try:
        offered_details = json.loads(offered) if offered else None
    except ValueError:
        offered_details = None
    return {"row": row, "info": data, "guests": guests, "jobs": jobs, "running": bool(running),
            "storages": backup_storages(data), "fingerprint": pretty(row["fingerprint"]) if row["fingerprint"] else "",
            "offered": dict(offered_details, pretty=pretty(offered_details["fingerprint"])) if offered_details else None,
            "checked": time.strftime("%H:%M", time.localtime(row["checked"])) if row["checked"] else ""}


def views():
    rows = db().execute("SELECT * FROM proxmox_hosts ORDER BY name COLLATE NOCASE, id").fetchall()
    for row in rows:
        if row["fingerprint"] and (time.time() - (row["checked"] or 0) > STALE or (info(row) is None and not row["error"])):
            refresh_soon(row["id"])
    return [view(row) for row in rows]


def signature(found):
    return [[v["row"]["id"], v["row"]["checked"], v["row"]["error"], v["row"]["fingerprint"], v["row"]["offered"],
             [(j["id"], j["state"]) for j in v["jobs"]]] for v in found]


def attention():
    """Pour « À traiter » de la vue d'ensemble : liaison en échec, dernière sauvegarde d'une machine en échec."""
    rows, url = [], "/backups#proxmox"
    for row in db().execute("SELECT * FROM proxmox_hosts WHERE fingerprint!='' ORDER BY name COLLATE NOCASE, id").fetchall():
        if time.time() - (row["checked"] or 0) > OVERVIEW_STALE:
            refresh_soon(row["id"])
        if row["error"]:
            rows.append({"tone": "warn", "text": _("Proxmox « %(name)s » : la liaison échoue.", name=row["name"]), "url": url})
            continue
        failed = db().execute("SELECT COUNT(*) FROM (SELECT vmid, state FROM proxmox_jobs WHERE host_id=? AND id IN "
                              "(SELECT MAX(id) FROM proxmox_jobs WHERE host_id=? AND state IN ('success','failed') GROUP BY vmid)) "
                              "WHERE state='failed'", (row["id"], row["id"])).fetchone()[0]
        if failed:
            rows.append({"tone": "err", "url": url, "text": _("Proxmox « %(name)s » : %(n)s sauvegarde en échec.", name=row["name"], n=failed)
                         if failed == 1 else _("Proxmox « %(name)s » : %(n)s sauvegardes en échec.", name=row["name"], n=failed)})
    return rows


def _address(text):
    """« 192.168.1.10 », « pve.lan:8006 », « https://[fd00::1]:8006/ » → (hôte, port)."""
    text = text.strip()
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        host, port = parts.hostname or "", parts.port or DEFAULT_PORT
        clean = parts.scheme == "https" and parts.path in ("", "/") and not parts.query and not parts.username
    except ValueError:
        host, port, clean = "", 0, False
    if not clean or not HOST_RE.match(host) or host.startswith("-") or not 1 <= port <= 65535:
        raise ValueError(_("Adresse de Proxmox invalide : un nom ou une adresse IP, avec le port s'il n'est pas 8006."))
    return host, port


def _offer(host, port):
    fingerprint, details = probe_certificate(host, port)
    return json.dumps(dict(details, fingerprint=fingerprint)), fingerprint


@app.route("/backups/proxmox/link", methods=["POST"])
@login_required
def proxmox_link():
    """Première étape : relever le certificat que présente l'adresse, à confirmer avant tout envoi du jeton."""
    conn = db()
    if conn.execute("SELECT COUNT(*) FROM proxmox_hosts").fetchone()[0] >= MAX_HOSTS:
        abort(409, _("Nombre maximal d'instances atteint."))
    form = request.form
    token_id, secret = form.get("token_id", "").strip(), form.get("secret", "").strip()
    try:
        host, port = _address(form.get("address", ""))
        if not TOKEN_RE.match(token_id):
            raise ValueError(_("Identifiant du jeton invalide : il s'écrit utilisateur@royaume!nom, par exemple loco@pve!loco."))
        if not SECRET_RE.match(secret):
            raise ValueError(_("Secret du jeton invalide : collez la valeur affichée par Proxmox à la création du jeton."))
        offered, _fingerprint = _offer(host, port)
    except (ValueError, ProxmoxError) as e:
        flash(str(e), "error")
        return back("backups", _anchor="proxmox")
    # L'instance n'existe pour rien d'autre qu'une confirmation tant que l'empreinte n'est pas confirmée (vide = aucun
    # appel) ; une demande laissée sans suite est oubliée au bout d'un quart d'heure.
    conn.execute("DELETE FROM proxmox_hosts WHERE fingerprint='' AND offered_at<?", (time.time() - OFFER_TTL,))
    conn.execute("INSERT INTO proxmox_hosts (name, host, port, token_id, secret, offered, offered_at, created_at, created_by) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (form.get("name", "").strip()[:60] or host, host, port, token_id, _seal(secret),
                                                offered, time.time(), _stamp(), g.user["username"]))
    conn.commit()
    return back("backups", _anchor="proxmox")


@app.route("/backups/proxmox/<int:host_id>/confirm", methods=["POST"])
@login_required
def proxmox_confirm(host_id):
    """Seconde étape : l'administrateur a comparé l'empreinte. Le certificat est épinglé, le jeton sert dès lors."""
    row = _host(host_id)
    conn = db()
    if request.form.get("cancel") == "1":
        if row["fingerprint"]:
            conn.execute("UPDATE proxmox_hosts SET offered='', offered_at=0 WHERE id=?", (host_id,))
        else:
            conn.execute("DELETE FROM proxmox_hosts WHERE id=?", (host_id,))
        conn.commit()
        return back("backups", _anchor="proxmox")
    try:
        offered = json.loads(row["offered"]) if row["offered"] and time.time() - row["offered_at"] < OFFER_TTL else None
    except ValueError:
        offered = None
    if not offered or not hmac.compare_digest(request.form.get("fingerprint", ""), offered["fingerprint"]):
        flash(_("La demande a expiré : relevez de nouveau le certificat."), "error")
        return back("backups", _anchor="proxmox")
    first = not row["fingerprint"]
    conn.execute("UPDATE proxmox_hosts SET fingerprint=?, offered='', offered_at=0, error='' WHERE id=?", (offered["fingerprint"], host_id))
    audit("proxmox.liaison" if first else "proxmox.certificat",
          f"{row['name']} ({row['token_id']} sur {row['host']}:{row['port']}), certificat {pretty(offered['fingerprint'])}", commit=False)
    conn.commit()
    error = refresh(host_id)
    flash(_("Proxmox relié.") if not error else _("Instance enregistrée, mais la liaison échoue : %(e)s", e=error), "ok" if not error else "error")
    return back("backups", _anchor="proxmox")


@app.route("/backups/proxmox/<int:host_id>/certificate", methods=["POST"])
@login_required
def proxmox_certificate(host_id):
    """Certificat renouvelé : le relever de nouveau ; il sera confirmé comme à la liaison."""
    row = _host(host_id)
    try:
        offered, _fingerprint = _offer(row["host"], row["port"])
    except ProxmoxError as e:
        flash(str(e), "error")
        return back("backups", _anchor="proxmox")
    db().execute("UPDATE proxmox_hosts SET offered=?, offered_at=? WHERE id=?", (offered, time.time(), host_id))
    db().commit()
    return back("backups", _anchor="proxmox")


@app.route("/backups/proxmox/<int:host_id>/delete", methods=["POST"])
@login_required
def proxmox_delete(host_id):
    from . import proxmox_system
    row = _host(host_id)
    conn = db()
    proxmox_system.close_sessions(conn, host_id, "Serveur retiré.")
    conn.execute("DELETE FROM backup_plans WHERE kind='proxmox' AND host_id=?", (host_id,))
    conn.execute("DELETE FROM proxmox_hosts WHERE id=?", (host_id,))
    audit("proxmox.retrait", row["name"], commit=False)
    conn.commit()
    flash(_("%(name)s retiré de loco. Le jeton reste valable dans Proxmox tant que vous ne l'y supprimez pas.", name=row["name"]) if not row["host_key"]
          else _("%(name)s retiré de loco. Le jeton et la clé de loco restent valables dans Proxmox tant que vous ne les y supprimez pas.", name=row["name"]), "ok")
    return back("backups", _anchor="proxmox")


@app.route("/backups/proxmox/<int:host_id>/refresh", methods=["POST"])
@login_required
def proxmox_refresh(host_id):
    error = refresh(_host(host_id)["id"], db())
    if error:
        flash(error, "error")
    return back("backups", _anchor="proxmox")


def selected_guests(form):
    return sorted({int(v) for v in form.getlist("vmid") if v.isdigit() and 100 <= int(v) <= 999999999})


@app.route("/backups/proxmox/<int:host_id>/backup", methods=["POST"])
@login_required
def proxmox_backup(host_id):
    """Sauvegarde maintenant des machines cochées, sur le stockage choisi."""
    row = _host(host_id)
    vmids, storage, mode = selected_guests(request.form), request.form.get("storage", ""), request.form.get("mode", "snapshot")
    if not vmids:
        flash(_("Cochez au moins une machine."), "error")
        return back("backups", _anchor="proxmox")
    if mode not in MODES or not STORAGE_RE.match(storage):
        abort(400)
    conn = db()
    added, refused = queue(conn, row, vmids, storage, mode, user=g.user["username"])
    if added:
        audit("proxmox.sauvegarde", f"{row['name']} : {', '.join(map(str, vmids))} vers {storage} ({mode})", commit=False)
    conn.commit()
    if added:
        work_soon(host_id)
    flash(_("Sauvegarde lancée.") if added == 1 else _("%(n)s sauvegardes lancées, l'une après l'autre.", n=added) if added
          else _("Rien à lancer : ces machines sont déjà en cours de sauvegarde, ou le stockage n'est pas joignable depuis leur nœud."),
          "ok" if added else "error")
    if added and refused:
        flash(_("Non lancées (déjà en cours, ou stockage absent de leur nœud) : %(list)s.", list=", ".join(refused)), "error")
    return back("backups", _anchor="proxmox")


@app.route("/backups/proxmox/jobs/<int:task_id>/cancel", methods=["POST"])
@login_required
def proxmox_cancel(task_id):
    """Annule une sauvegarde en attente, ou arrête celle en cours sur Proxmox."""
    conn = db()
    job = conn.execute("SELECT * FROM proxmox_jobs WHERE id=?", (task_id,)).fetchone()
    if job is None:
        abort(404)
    row = _host(job["host_id"])
    if job["state"] == "queued":
        conn.execute("UPDATE proxmox_jobs SET state='failed', output=?, finished_at=? WHERE id=? AND state='queued'",
                     ("Annulée avant son départ.", _stamp(), task_id))
    elif job["state"] == "running" and job["upid"]:
        try:
            request_api(row, "DELETE", f"/nodes/{job['node']}/tasks/{urllib.parse.quote(job['upid'], safe='')}")
        except ProxmoxError as e:
            flash(str(e), "error")
            return back("backups", _anchor="proxmox")
    audit("proxmox.annulation", f"{row['name']} : {job['vmid']}", commit=False)
    conn.commit()
    flash(_("Sauvegarde annulée."), "ok")
    return back("backups", _anchor="proxmox")


NAS_TYPES = {"nfs": "NFS", "cifs": "SMB / CIFS"}


@app.route("/backups/proxmox/<int:host_id>/storage", methods=["POST"])
@login_required
def proxmox_storage_add(host_id):
    """Déclare un NAS dans Proxmox comme stockage de sauvegardes (NFS ou SMB). Proxmox garde lui-même le mot de passe
    d'un partage SMB : loco le transmet dans le corps de la requête et ne le conserve pas."""
    row = _host(host_id)
    form = request.form
    kind, storage, server = form.get("type", ""), form.get("storage", "").strip(), form.get("server", "").strip()
    if kind not in NAS_TYPES:
        abort(400)
    params = {"storage": storage, "type": kind, "server": server, "content": "backup"}
    try:
        if not STORAGE_RE.match(storage):
            raise ValueError(_("Nom du stockage invalide : lettres, chiffres, « - », « _ » ou « . », en commençant par une lettre."))
        if not HOST_RE.match(server) or server.startswith("-"):
            raise ValueError(_("Adresse du NAS invalide : un nom ou une adresse IP."))
        if kind == "nfs":
            export = form.get("export", "").strip()
            if not re.fullmatch(r"/[^\s,;]{0,250}", export):
                raise ValueError(_("Dossier exporté invalide : un chemin qui commence par « / »."))
            params["export"] = export
        else:
            share, username = form.get("share", "").strip(), form.get("username", "").strip()
            if not re.fullmatch(r"[^\s/\\,;]{1,80}", share):
                raise ValueError(_("Nom du partage invalide."))
            if username and not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", username):
                raise ValueError(_("Nom d'utilisateur invalide."))
            params["share"] = share
            if username:
                params["username"] = username
                params["password"] = form.get("password", "")
            domain = form.get("domain", "").strip()
            if domain:
                if not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", domain):
                    raise ValueError(_("Domaine invalide."))
                params["domain"] = domain
        request_api(row, "POST", "/storage", params, timeout=60)
    except ValueError as e:
        flash(str(e), "error")
        return back("backups", _anchor="proxmox")
    except ProxmoxError as e:
        hint = _(" Ce réglage demande en plus le droit Datastore.Allocate sur /storage pour le jeton.") if e.status == 403 else ""
        flash(str(e) + hint, "error")
        return back("backups", _anchor="proxmox")
    audit("proxmox.stockage", f"{row['name']} : {storage} ({NAS_TYPES[kind]} {server})")
    refresh(host_id, db())
    flash(_("Stockage « %(name)s » déclaré dans Proxmox : il est proposé pour les sauvegardes.", name=storage), "ok")
    return back("backups", _anchor="proxmox")
