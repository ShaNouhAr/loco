"""Sauvegardes : la page qui les réunit — loco, Home Assistant, Proxmox — avec la destination externe (NAS, S3, SFTP).

- loco se sauvegarde chaque nuit (Système › Sauvegardes) ; la plus récente part vers la destination externe si on le
  veut (offsite.py).
- Home Assistant : sa sauvegarde la plus récente (créée juste avant si on le demande) est téléchargée par la liaison SSH,
  chiffrée avec la phrase de passe de la destination s'il y en a une, puis déposée sur la destination ; seules les
  dernières copies de chaque instance y sont gardées, en nombre choisi.
- Proxmox : ses machines sont sauvegardées par Proxmox lui-même sur l'un de ses stockages (proxmox.py).

Les planifications (une fois, chaque jour, chaque semaine, chaque mois, à l'heure du serveur) sont tenues ici : un fil de
fond les change en tâches à l'heure dite. Une occurrence manquée (serveur éteint) n'est pas rattrapée.
"""
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import threading
import time

from flask import abort, flash, g, render_template, request

from . import app, config, features, homeassistant, offsite, proxmox, schedules
from .i18n import _, n_
from .auth import audit, can, login_required, require
from .db import db, lease, transaction
from .util import back

SPOOL = os.path.join(config.STATE, "backup-spool")
KEEP, KEEP_MAX = 3, 60
MARGIN = 256 * 1024 ** 2         # place laissée libre sur le disque de loco pendant une copie
COPY_RE = re.compile(r"homeassistant-(\d+)-(\d{8}-\d{6})-([a-z0-9_]{1,64})\.tar(?:\.enc)?")
TICK = 30                        # secondes entre deux passages du fil des planifications


# --------------------------------------------------------------------------- copie des sauvegardes de Home Assistant

def copy_state(row):
    try:
        data = json.loads(row["copy_state"]) if row["copy_state"] else {}
    except (ValueError, IndexError, KeyError):
        data = {}
    data = data if isinstance(data, dict) else {}
    keep = data.get("keep")
    data["keep"] = keep if isinstance(keep, int) and 1 <= keep <= KEEP_MAX else KEEP
    return data


def _save_copy_state(conn, host_id, **changes):
    row = conn.execute("SELECT copy_state FROM homeassistant_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None:
        return
    data = copy_state(row)
    data.update(changes)
    with conn:
        conn.execute("UPDATE homeassistant_hosts SET copy_state=? WHERE id=?", (json.dumps(data), host_id))


def _local_backups(row):
    """Sauvegardes gardées dans Home Assistant même (pas celles d'un stockage réseau), relues à l'instant."""
    code, output = homeassistant.run(row, "ha backups --raw-json 2>&1")
    for line in output.splitlines():
        if line.startswith("{"):
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            data = answer.get("data") if isinstance(answer, dict) else None
            if isinstance(data, dict) and isinstance(data.get("backups"), list):
                found = []
                for b in data["backups"]:
                    if not isinstance(b, dict) or not homeassistant.SLUG_RE.match(str(b.get("slug", ""))):
                        continue
                    places = b.get("locations") if isinstance(b.get("locations"), list) else [b.get("location")]
                    if any(place in (None, ".local") for place in places):
                        found.append(b)
                return found
    raise homeassistant.LinkError(homeassistant._explain(output) if code else _("Réponse de Home Assistant illisible."))


def _stamp_of(backup):
    """Date de la sauvegarde (UTC chez Home Assistant) en heure du serveur, « AAAAMMJJ-HHMMSS » pour le nom de la copie."""
    try:
        when = datetime.datetime.fromisoformat(str(backup.get("date", "")).replace("Z", "+00:00")).astimezone()
    except ValueError:
        when = datetime.datetime.now()
    return when.strftime("%Y%m%d-%H%M%S")


def _remote_copies(cfg, host_id):
    """Copies de cette instance sur la destination, de la plus récente à la plus ancienne."""
    found = []
    for name in offsite._list(cfg, COPY_RE):
        m = COPY_RE.fullmatch(name)
        if m and int(m.group(1)) == host_id:
            found.append((m.group(2), name))
    return [name for _stamp, name in sorted(found, reverse=True)]


def copy_homeassistant(conn, row, job):
    """Tâche « copy » d'une instance : sauvegarde complète d'abord si demandé (job["backup"]), puis copie de la plus
    récente vers la destination externe et conservation. Renvoie (état, sortie)."""
    cfg = offsite.load(conn)
    if not offsite.configured(cfg):
        message = _("Aucune destination externe n'est choisie : réglez-la dans Sauvegardes › Destination.")
        _save_copy_state(conn, row["id"], error=message, failed=_now())
        return "failed", message
    lines, path, sent = [], "", ""
    try:
        if job["backup"]:
            code, output = homeassistant.run(row, homeassistant.command(dict(job, action="backup")) + " 2>&1",
                                             timeout=homeassistant.LONG)
            if code != 0:
                raise homeassistant.LinkError(homeassistant._explain(output) if code == 255 else
                                              (output.strip().splitlines() or [_("la sauvegarde a échoué")])[-1][:240])
            lines.append(_("Sauvegarde complète créée dans Home Assistant."))
        found = _local_backups(row)
        if not found:
            raise homeassistant.LinkError(_("Home Assistant ne garde aucune sauvegarde : lancez-en une, ou cochez « créer une sauvegarde avant »."))
        latest = max(found, key=lambda b: str(b.get("date", "")))
        state = copy_state(row)
        label = str(latest.get("name") or latest["slug"])[:80]
        if state.get("slug") == latest["slug"]:
            lines.append(_("La sauvegarde la plus récente (%(name)s) est déjà sur la destination.", name=label))
            _save_copy_state(conn, row["id"], error="", checked=_now())
            return "success", "\n".join(lines)
        size = latest.get("size_bytes") if isinstance(latest.get("size_bytes"), int) else 0
        passphrase = cfg["crypt"]["passphrase"]
        os.makedirs(SPOOL, mode=0o700, exist_ok=True)
        free = shutil.disk_usage(SPOOL).free
        if size and free < size * (2 if passphrase else 1) + MARGIN:
            raise homeassistant.LinkError(_("Pas assez de place sur le disque de loco pour copier cette sauvegarde (%(size)s Mo, %(free)s Mo libres).",
                                            size=size // 1048576, free=free // 1048576))
        path = os.path.join(SPOOL, f"homeassistant-{row['id']}-{latest['slug']}.tar")
        code, error = homeassistant.download(row, latest["slug"], path)
        if code != 0:
            raise homeassistant.LinkError(homeassistant._explain(error) if code == 255 else
                                          _("Téléchargement de la sauvegarde refusé par Home Assistant : %(e)s",
                                            e=(error.strip().splitlines() or ["code " + str(code)])[-1][:200]))
        got = os.path.getsize(path)
        if not got or size and got != size:
            raise homeassistant.LinkError(_("Sauvegarde reçue incomplète (%(got)s octets sur %(size)s).", got=got, size=size))
        name = f"homeassistant-{row['id']}-{_stamp_of(latest)}-{latest['slug']}.tar"
        if passphrase:
            sent = offsite._encrypt(path, passphrase)
            offsite._put(cfg, sent, name + ".enc")
            name += ".enc"
        else:
            offsite._put(cfg, path, name)
        lines.append(_("« %(name)s » copiée sur la destination : %(file)s.", name=label, file=name))
        kept = _remote_copies(cfg, row["id"])
        removed = kept[state["keep"]:]
        for old in removed:
            offsite._delete(cfg, old)
        if removed:
            lines.append(n_("Conservation : %(num)s copie plus ancienne supprimée.", "Conservation : %(num)s copies plus anciennes supprimées.", len(removed)))
        _save_copy_state(conn, row["id"], slug=latest["slug"], name=label, file=name, size=got, at=_now(), error="",
                         copies=min(len(kept), state["keep"]), encrypted=bool(passphrase))
        return "success", "\n".join(lines)
    except homeassistant.LinkError as e:
        message = str(e)
    except Exception as e:                       # destination injoignable, chiffrement, disque…
        message = f"{type(e).__name__}: {e}"[:300]
    finally:
        for leftover in (path, sent):
            if leftover:
                with contextlib.suppress(OSError):
                    os.unlink(leftover)
    _save_copy_state(conn, row["id"], error=message, failed=_now())
    return "failed", "\n".join(lines + [message])


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- planifications

def materialize(conn, now=None):
    """Planifications arrivées à échéance → tâches ; chacune passe à son occurrence suivante (une ponctuelle s'arrête)."""
    now = now or datetime.datetime.now()
    due = conn.execute("SELECT * FROM backup_plans WHERE enabled=1 AND next_run!='' AND next_run<=? ORDER BY id",
                       (now.strftime(schedules.FMT),)).fetchall()
    started = {"proxmox": set(), "homeassistant": set()}
    off = features.disabled(conn) if due else set()
    for plan in due:
        upcoming = schedules.next_occurrence(plan, now)
        with conn:
            conn.execute("UPDATE backup_plans SET last_run=?, next_run=?, enabled=? WHERE id=?",
                         (now.strftime(schedules.FMT), upcoming.strftime(schedules.FMT) if upcoming else "",
                          1 if upcoming else 0, plan["id"]))
        if plan["kind"] == "proxmox" and "proxmox" not in off:
            row = conn.execute("SELECT * FROM proxmox_hosts WHERE id=? AND fingerprint!=''", (plan["host_id"],)).fetchone()
            if row is None:
                continue
            data = proxmox.info(row) or {}
            vmids = ([v["vmid"] for v in data.get("guests", []) if not v["template"]] if plan["guests"] == "all"
                     else [int(v) for v in plan["guests"].split(",") if v.isdigit()])
            with conn:
                added, _refused = proxmox.queue(conn, row, vmids, plan["storage"], plan["mode"], plan["keep"], plan["id"], "planification")
            if added:
                started["proxmox"].add(row["id"])
        elif plan["kind"] == "homeassistant" and "homeassistant" not in off:
            with conn:
                if conn.execute("SELECT 1 FROM homeassistant_hosts WHERE id=? AND host_key!=''", (plan["host_id"],)).fetchone() and \
                        homeassistant.queue(conn, plan["host_id"], "copy", backup=bool(plan["fresh"]), user="planification"):
                    started["homeassistant"].add(plan["host_id"])
    for host_id in started["proxmox"]:
        proxmox.work_soon(host_id)
    for host_id in started["homeassistant"]:
        homeassistant.work_soon(host_id)


_started = False


def start():
    global _started
    if _started:
        return
    _started = True
    proxmox.start()

    def loop():
        from . import ha
        time.sleep(15)
        while True:
            try:
                if not ha.skip_background() and lease("backups"):   # un seul processus ; jamais le serveur de secours
                    with transaction() as conn:
                        materialize(conn)
            except Exception:
                pass
            time.sleep(TICK)

    threading.Thread(target=loop, name="loco-sauvegardes", daemon=True).start()


def plans(kind, host_id):
    rows = db().execute("SELECT * FROM backup_plans WHERE kind=? AND host_id=? ORDER BY id", (kind, host_id)).fetchall()
    out = []
    for p in rows:
        out.append({"row": p, "when": schedules.describe(p),
                    "next": schedules.when_text(p["next_run"]) if p["next_run"] else "",
                    "last": schedules.when_text(p["last_run"]) if p["last_run"] else "",
                    "guests": [] if p["guests"] in ("", "all") else p["guests"].split(",")})
    return out


def _plan_host(kind, host_id):
    table = {"proxmox": "proxmox_hosts", "homeassistant": "homeassistant_hosts"}[kind]
    row = db().execute(f"SELECT * FROM {table} WHERE id=?", (host_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _require_kind(kind):
    if kind not in ("proxmox", "homeassistant") or not features.enabled(kind):
        abort(404)
    require(kind + ".backup")


@app.route("/backups/plans", methods=["POST"])
@login_required
def backup_plan_save():
    """Nouvelle planification : machines Proxmox (stockage, mode, conservation) ou copie d'un Home Assistant."""
    form = request.form
    kind = form.get("kind", "")
    _require_kind(kind)
    row = _plan_host(kind, form.get("host_id", type=int) or 0)
    fields = {"guests": "", "storage": "", "mode": "", "fresh": 0, "keep": 0}
    try:
        when = schedules.parse(form)
        if kind == "proxmox":
            vmids = proxmox.selected_guests(form)
            everything = form.get("scope") == "all"
            if not everything and not vmids:
                raise ValueError(_("Cochez au moins une machine, ou choisissez « toutes les machines »."))
            storage, mode = form.get("storage", ""), form.get("mode", "snapshot")
            if not proxmox.STORAGE_RE.match(storage) or mode not in proxmox.MODES:
                raise ValueError(_("Stockage ou mode de sauvegarde invalide."))
            keep = form.get("keep", "0").strip()
            if not keep.isdigit() or int(keep) > KEEP_MAX:
                raise ValueError(_("Nombre de sauvegardes conservées invalide (0 à %(n)s).", n=KEEP_MAX))
            fields.update(guests="all" if everything else ",".join(map(str, vmids)), storage=storage, mode=mode, keep=int(keep))
        else:
            fields["fresh"] = 1 if form.get("fresh") == "1" else 0
    except ValueError as e:
        flash(str(e), "error")
        return back("backups", _anchor=kind)
    conn = db()
    conn.execute("INSERT INTO backup_plans (kind, host_id, guests, storage, mode, fresh, keep, recurrence, at_time, start_date, "
                 "weekdays, monthday, next_run, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (kind, row["id"], fields["guests"], fields["storage"], fields["mode"], fields["fresh"], fields["keep"],
                  when["recurrence"], when["at_time"], when["start_date"], when["weekdays"], when["monthday"], when["next_run"],
                  g.user["username"], time.strftime(schedules.FMT)))
    audit("sauvegardes.planification", f"{row['name']} : {schedules.describe(when)}", commit=False)
    conn.commit()
    flash(_("Planification enregistrée : %(when)s.", when=schedules.describe(when)), "ok")
    return back("backups", _anchor=kind)


def _plan(plan_id):
    plan = db().execute("SELECT * FROM backup_plans WHERE id=?", (plan_id,)).fetchone()
    if plan is None:
        abort(404)
    _require_kind(plan["kind"])
    return plan


@app.route("/backups/plans/<int:plan_id>/toggle", methods=["POST"])
@login_required
def backup_plan_toggle(plan_id):
    plan = _plan(plan_id)
    conn = db()
    if plan["enabled"]:
        conn.execute("UPDATE backup_plans SET enabled=0 WHERE id=?", (plan_id,))
        flash(_("Planification en pause."), "ok")
    else:
        upcoming = schedules.next_occurrence(plan, datetime.datetime.now())
        if upcoming is None:
            flash(_("L'instant planifié est déjà passé."), "error")
            return back("backups", _anchor=plan["kind"])
        conn.execute("UPDATE backup_plans SET enabled=1, next_run=? WHERE id=?", (upcoming.strftime(schedules.FMT), plan_id))
        flash(_("Planification reprise."), "ok")
    audit("sauvegardes.planification.pause" if plan["enabled"] else "sauvegardes.planification.reprise", str(plan_id), commit=False)
    conn.commit()
    return back("backups", _anchor=plan["kind"])


@app.route("/backups/plans/<int:plan_id>/delete", methods=["POST"])
@login_required
def backup_plan_delete(plan_id):
    plan = _plan(plan_id)
    conn = db()
    conn.execute("DELETE FROM backup_plans WHERE id=?", (plan_id,))
    audit("sauvegardes.planification.suppression", str(plan_id), commit=False)
    conn.commit()
    flash(_("Planification supprimée. Les sauvegardes déjà faites restent où elles sont."), "ok")
    return back("backups", _anchor=plan["kind"])


@app.route("/backups/homeassistant/<int:host_id>/copy", methods=["POST"])
@login_required
def backups_homeassistant_copy(host_id):
    """Copie maintenant la sauvegarde la plus récente d'un Home Assistant vers la destination externe."""
    row = _plan_host("homeassistant", host_id)
    if not offsite.configured(offsite.load()):
        flash(_("Choisissez d'abord une destination externe."), "error")
        return back("backups", _anchor="destination")
    fresh = request.form.get("fresh") == "1"
    conn = db()
    added = homeassistant.queue(conn, host_id, "copy", backup=fresh, user=g.user["username"])
    if added:
        audit("homeassistant.copy", row["name"] + (", sauvegarde avant" if fresh else ""), commit=False)
    conn.commit()
    if added:
        homeassistant.work_soon(host_id)
    flash(_("Copie lancée.") if added else _("Une copie est déjà en cours."), "ok" if added else "error")
    return back("backups", _anchor="homeassistant")


@app.route("/backups/homeassistant/<int:host_id>/keep", methods=["POST"])
@login_required
def backups_homeassistant_keep(host_id):
    row = _plan_host("homeassistant", host_id)
    keep = request.form.get("keep", "").strip()
    if not keep.isdigit() or not 1 <= int(keep) <= KEEP_MAX:
        flash(_("Nombre de copies invalide (1 à %(n)s).", n=KEEP_MAX), "error")
    else:
        _save_copy_state(db(), host_id, keep=int(keep))
        audit("homeassistant.copies", f"{row['name']} : {keep}")
        flash(_("Copies conservées sur la destination : %(n)s. Les plus anciennes au-delà sont supprimées à la prochaine copie.", n=keep), "ok")
    return back("backups", _anchor="homeassistant")


# --------------------------------------------------------------------------- page

def _homeassistant_views():
    rows = db().execute("SELECT * FROM homeassistant_hosts ORDER BY name COLLATE NOCASE, id").fetchall()
    out = []
    for row in rows:
        data = homeassistant._info(row)
        local = [b for b in (data or {}).get("backups", []) if b.get("local", True)]
        jobs = db().execute("SELECT * FROM homeassistant_jobs WHERE host_id=? AND action='copy' ORDER BY id DESC LIMIT 6",
                            (row["id"],)).fetchall()
        out.append({"row": row, "local": local[:1], "count": len(local), "state": copy_state(row), "jobs": jobs,
                    "copying": any(j["state"] in ("queued", "running") for j in jobs), "plans": plans("homeassistant", row["id"])})
    return out


def _loco_view():
    if config.WINDOWS or not can("system.view"):
        return None
    from .system import _age, backup_keep, list_backups
    found = list_backups()
    return {"backups": found, "last": _age(found[0]["mtime"]) if found else None, "keep": backup_keep()}


@app.route("/backups")
@login_required
def backups():
    show_ha = features.enabled("homeassistant") and can("homeassistant.view")
    show_pve = features.enabled("proxmox") and can("proxmox.view")
    pve = proxmox.views() if show_pve else []
    for v in pve:
        v["plans"] = plans("proxmox", v["row"]["id"])
    hass = _homeassistant_views() if show_ha else []
    destination = offsite.panel() if can("system.view") else None
    signature = hashlib.sha256(json.dumps([proxmox.signature(pve), [[h["row"]["id"], h["row"]["copy_state"],
                                                                     [(j["id"], j["state"]) for j in h["jobs"]]] for h in hass],
                                           (destination or {}).get("state")], default=str).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    return render_template("backups.html", loco=_loco_view(), destination=destination, hass=hass, pve=pve,
                           show_ha=show_ha, show_pve=show_pve, signature=signature, modes=proxmox.MODES,
                           nas_types=proxmox.NAS_TYPES, privileges=proxmox.PRIVILEGES, role=proxmox.ROLE,
                           max_hosts=proxmox.MAX_HOSTS, keep_max=KEEP_MAX,
                           busy=any(v["running"] for v in pve) or any(h["copying"] for h in hass))
