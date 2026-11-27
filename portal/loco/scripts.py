"""Bibliothèque de scripts versionnés, exécutés par les agents ou par SSH sur Proxmox."""
import hashlib
import json
import time

from flask import abort, flash, g, render_template, request

from . import app, fleet, groups, scope
from .i18n import L_, _, n_
from .auth import audit, can, lock_access, login_required
from .db import db, transaction
from .util import back, counted

PLATFORMS = {"windows": L_("Windows · PowerShell"), "linux": L_("Linux · shell"), "python": L_("Python · tous systèmes")}
MAX_CONTENT = 64 * 1024
MAX_TARGETS = 500
ACTIVE = ("queued", "sent", "running")
STATES = {"queued": L_("En attente du poste"), "sent": L_("Transmis à l’agent"), "running": L_("En cours"), "success": L_("Réussi"),
          "failed": L_("Échec"), "cancelled": L_("Annulé")}


def _script(script_id):
    row = db().execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def runs_on(platform, os_family):
    """Un script Python tourne sur tout système ; PowerShell seulement sous Windows, shell seulement ailleurs."""
    return platform == "python" or (os_family == "windows") == (platform == "windows")


def _compatible(dev, platform):
    return dev["state"] == "active" and runs_on(platform, dev["os_family"])


def _targets(run_id):
    rows = db().execute("SELECT t.*, c.state AS command_state, COALESCE(c.output, t.last_output) AS output, "
                        "COALESCE(c.exit_code, t.ssh_exit_code) AS exit_code, "
                        "c.finished_at, c.sent_at, c.run_after FROM script_run_targets t LEFT JOIN device_commands c ON c.id=t.command_id "
                        "WHERE t.run_id=? ORDER BY t.hostname COLLATE NOCASE", (run_id,)).fetchall()
    return [{**dict(r), "state": r["command_state"] or r["last_state"] or "cancelled"} for r in rows]


def _run_in_scope(conn, run_id):
    """Exécution dont tous les postes sont dans le parc du compte courant (sinon il ne la voit pas)."""
    return conn.execute("SELECT 1 FROM script_runs WHERE id=?" + _run_filter(), (run_id,)).fetchone() is not None


def _can_proxmox():
    return scope.whole() and can("proxmox.shell")


def _run_filter(alias="script_runs"):
    conditions = []
    if not scope.whole():
        conditions.append("(t.device_id IS NOT NULL AND NOT COALESCE(1" + scope.sql("t.device_id") + ", 0))")
    if not _can_proxmox():
        conditions.append("t.ssh_identity!=''")
    if not conditions:
        return ""
    return (f" AND NOT EXISTS (SELECT 1 FROM script_run_targets t WHERE t.run_id={alias}.id AND ("
            + " OR ".join(conditions) + "))")


def _summary(targets):
    counts = {"success": 0, "active": 0, "failed": 0, "other": 0}
    for t in targets:
        key = "active" if t["state"] in ACTIVE else t["state"] if t["state"] in ("success", "failed") else "other"
        counts[key] += 1
    counts["total"] = len(targets)
    return counts


HISTORY_LIMIT = 100
HISTORY_FILTERS = {"": L_("Toutes"), "en-cours": L_("En cours"), "echecs": L_("Avec échecs"), "reussies": L_("Réussies")}


def _run_state(p):
    return "active" if p["active"] else "failed" if p["failed"] else "success" if p["success"] == p["total"] and p["total"] else "other"


@app.route("/fleet/scripts")
@login_required
def scripts():
    visible_runs = _run_filter("r")
    rows = db().execute("SELECT s.*, (SELECT MAX(created_at) FROM script_runs r WHERE r.script_id=s.id" + visible_runs
                        + ") AS last_run, (SELECT COUNT(*) FROM script_runs r WHERE r.script_id=s.id" + visible_runs
                        + ") AS runs FROM scripts s "
                        "ORDER BY s.name COLLATE NOCASE").fetchall()
    history = []
    visible = " WHERE 1" + _run_filter()
    for r in db().execute("SELECT * FROM script_runs" + visible + " ORDER BY id DESC LIMIT ?", (HISTORY_LIMIT,)).fetchall():
        summary = _summary(_targets(r["id"]))
        history.append({"run": r, "summary": summary, "state": _run_state(summary)})
    total_runs = db().execute("SELECT COUNT(*) FROM script_runs" + visible).fetchone()[0]
    counts = {"": len(history), "en-cours": sum(h["state"] == "active" for h in history),
              "echecs": sum(h["summary"]["failed"] > 0 for h in history), "reussies": sum(h["state"] == "success" for h in history)}
    wanted = request.args.get("historique", "")
    if wanted not in HISTORY_FILTERS:
        wanted = ""
    keep = {"": lambda h: True, "en-cours": lambda h: h["state"] == "active", "echecs": lambda h: h["summary"]["failed"] > 0,
            "reussies": lambda h: h["state"] == "success"}[wanted]
    shown = [h for h in history if keep(h)]
    waiting = db().execute("SELECT COUNT(*) FROM device_commands WHERE state='queued' AND id IN "
                           "(SELECT command_id FROM script_run_targets)" + scope.sql("device_id")).fetchone()[0]
    if _can_proxmox():
        waiting += db().execute("SELECT COUNT(*) FROM script_run_targets WHERE ssh_identity!='' AND last_state='queued'").fetchone()[0]
    return render_template("scripts.html", scripts=rows, platforms=PLATFORMS, history=shown, counts=counts,
                           filters=HISTORY_FILTERS, wanted=wanted, total_runs=total_runs, waiting=waiting,
                           running=sum(h["summary"]["active"] for h in history), limit=HISTORY_LIMIT)


@app.route("/fleet/scripts/runs/cancel-all", methods=["POST"])
@login_required
def script_runs_cancel_all():
    """Vider la file : aucun poste ne recevra plus les scripts encore en attente (déjà transmis : ils se terminent)."""
    conn = lock_access("scripts.run")
    cur = conn.execute("UPDATE device_commands SET state='cancelled', finished_at=?, output='Annulé avant transmission à l’agent.' "
                       "WHERE state='queued' AND id IN (SELECT command_id FROM script_run_targets)" + scope.sql("device_id"),
                       (fleet._now(),))
    count = cur.rowcount
    if _can_proxmox():
        count += conn.execute("UPDATE script_run_targets SET last_state='cancelled' "
                              "WHERE ssh_identity!='' AND last_state='queued'").rowcount
    audit("scripts.execution", f"file vidée, en attente annulés : {counted(count, 'poste')}", commit=False)
    conn.commit()
    flash((_('File vidée : %(rowcount)s envoi%(v)s en attente annulé%(v)s.', rowcount=count, v='s' if count != 1 else '') if count else _('Aucun envoi en attente.')), "ok")
    return back("scripts")


def _finished_runs(conn, run_ids=None):
    """Exécutions sans poste en attente ni en cours (les autres restent dans l'historique)."""
    rows = conn.execute("SELECT id FROM script_runs").fetchall() if run_ids is None else [{"id": i} for i in run_ids]
    return [r["id"] for r in rows if _run_in_scope(conn, r["id"]) and not _summary(_targets(r["id"]))["active"]]


def _delete_runs(conn, run_ids):
    for run_id in run_ids:
        conn.execute("DELETE FROM script_run_targets WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM script_runs WHERE id=?", (run_id,))
        conn.execute("UPDATE schedules SET last_ref=0 WHERE action='run-script' AND last_ref=?", (run_id,))   # plus de lien mort


@app.route("/fleet/scripts/history/clear", methods=["POST"])
@login_required
def script_history_clear():
    conn = lock_access("scripts.edit")
    done = _finished_runs(conn)
    _delete_runs(conn, done)
    kept = sum(1 for r in conn.execute("SELECT id FROM script_runs").fetchall() if _run_in_scope(conn, r["id"]))
    audit("scripts.historique", f"historique vidé, supprimées : {counted(len(done), 'exécution')}", commit=False)
    conn.commit()
    flash(_('Historique vidé : %(len)s exécution%(v)s supprimée%(v)s', len=len(done), v='s' if len(done) != 1 else '') + (_(' ; %(kept)s encore en cours conservée%(v)s.', kept=kept, v='s' if kept != 1 else '') if kept else "."), "ok")
    return back("scripts")


@app.route("/fleet/scripts/runs/<int:run_id>/delete", methods=["POST"])
@login_required
def script_run_delete(run_id):
    conn = lock_access("scripts.edit")
    run = conn.execute("SELECT * FROM script_runs WHERE id=?", (run_id,)).fetchone()
    if run is None or not _run_in_scope(conn, run_id):
        abort(404)
    if not _finished_runs(conn, [run_id]):
        flash(_("Exécution en cours : annulez d'abord les postes en attente."), "error")
        return back("script_run", run_id=run_id)
    _delete_runs(conn, [run_id])
    audit("scripts.historique", f"exécution supprimée : {run['name']} v{run['version']} du {run['created_at']}", commit=False)
    conn.commit()
    flash(_("Exécution retirée de l'historique."), "ok")
    return back("scripts")


@app.route("/fleet/scripts/new", defaults={"script_id": 0})
@app.route("/fleet/scripts/<int:script_id>")
@login_required
def script_editor(script_id):
    script = _script(script_id) if script_id else None
    runs = db().execute("SELECT * FROM script_runs WHERE script_id=?" + _run_filter()
                        + " ORDER BY id DESC LIMIT 15", (script_id,)).fetchall() if script else []
    return render_template("script_editor.html", script=script, platforms=PLATFORMS, max_content=MAX_CONTENT,
                           proxmox_available=script and script["platform"] == "linux" and _can_proxmox(),
                           runs=[{"run": r, "summary": _summary(_targets(r["id"]))} for r in runs])


@app.route("/fleet/scripts/save", defaults={"script_id": 0}, methods=["POST"])
@app.route("/fleet/scripts/<int:script_id>/save", methods=["POST"])
@login_required
def script_save(script_id):
    conn = lock_access("scripts.edit")
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    platform = request.form.get("platform", "")
    content = request.form.get("content", "").replace("\r\n", "\n")
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name) or len(description) > 300 or platform not in PLATFORMS:
        abort(400, _('Nom (1 à 80 caractères), description (300 au plus) et système attendus.'))
    if not content.strip() or len(content.encode()) > MAX_CONTENT:
        abort(400, _('Contenu du script vide ou trop long (64 Kio au plus).'))
    now, user = fleet._now(), g.user["username"]
    if script_id:
        old = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
        if old is None:
            abort(404)
        version = old["version"] + (1 if (old["content"], old["platform"]) != (content, platform) else 0)
        conn.execute("UPDATE scripts SET name=?, description=?, platform=?, content=?, version=?, updated_at=?, updated_by=? "
                     "WHERE id=?", (name, description, platform, content, version, now, user, script_id))
        audit("scripts.modification", f"{name} (v{version})", commit=False)
    else:
        script_id = conn.execute("INSERT INTO scripts (name, description, platform, content, created_at, updated_at, updated_by) "
                                 "VALUES (?,?,?,?,?,?,?)", (name, description, platform, content, now, now, user)).lastrowid
        audit("scripts.ajout", f"{name} ({PLATFORMS[platform]})", commit=False)
    conn.commit()
    flash(_('Script enregistré.'), "ok")
    return back("script_editor", script_id=script_id)


@app.route("/fleet/scripts/<int:script_id>/delete", methods=["POST"])
@login_required
def script_delete(script_id):
    conn = lock_access("scripts.edit")
    script = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if script is None:
        abort(404)
    conn.execute("DELETE FROM scripts WHERE id=?", (script_id,))
    audit("scripts.suppression", script["name"], commit=False)
    conn.commit()
    flash(_('Script supprimé. Les résultats de ses exécutions restent consultables.'), "ok")
    return back("scripts")


@app.route("/fleet/scripts/<int:script_id>/run")
@login_required
def script_compose(script_id):
    script = _script(script_id)
    proxmox_mode = request.args.get("target") == "proxmox"
    proxmox_available = script["platform"] == "linux" and _can_proxmox()
    if proxmox_mode and not proxmox_available:
        abort(403)
    devices = [] if proxmox_mode else db().execute("SELECT * FROM devices WHERE state!='removed'" + scope.sql()
                                                  + " ORDER BY hostname COLLATE NOCASE").fetchall()
    hosts = db().execute("SELECT id, name FROM proxmox_hosts WHERE host_key!='' ORDER BY name COLLATE NOCASE").fetchall() if proxmox_mode else []
    host_groups = db().execute(
        "SELECT g.id, g.name, COUNT(*) AS members FROM device_groups g "
        "JOIN proxmox_group_members m ON m.group_id=g.id JOIN proxmox_hosts h ON h.id=m.host_id "
        "WHERE h.host_key!='' GROUP BY g.id ORDER BY g.name COLLATE NOCASE"
    ).fetchall() if proxmox_mode else []
    return render_template("script_compose.html", script=script, platforms=PLATFORMS,
                           proxmox_mode=proxmox_mode, proxmox_available=proxmox_available, hosts=hosts, host_groups=host_groups,
                           devices=[d for d in devices if _compatible(d, script["platform"])],
                           incompatible=sum(1 for d in devices if not _compatible(d, script["platform"])),
                           device_groups=[] if proxmox_mode else groups.listing("device"),
                           selected={int(v) for v in request.args.getlist("device") if v.isdigit()})


def _snapshot(conn, script, user, now):
    sha = hashlib.sha256(script["content"].encode()).hexdigest()
    return conn.execute("INSERT INTO script_runs (script_id, name, version, platform, content, sha256, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (script["id"], script["name"], script["version"], script["platform"],
                                                      script["content"], sha, user, now)).lastrowid


def start_run(conn, script, targets, user, run_after=""):
    """Exécution figée (contenu et empreinte) envoyée aux postes cibles ; renvoie son identifiant."""
    now = fleet._now()
    run_id = _snapshot(conn, script, user, now)
    for dev in targets:
        command_id = conn.execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by, run_after) "
                                  "VALUES (?, 'run-script', ?, ?, ?, ?)",
                                  (dev["id"], json.dumps({"run_id": run_id}), now, user, run_after)).lastrowid
        conn.execute("INSERT INTO script_run_targets (run_id, device_id, hostname, command_id) VALUES (?,?,?,?)",
                     (run_id, dev["id"], dev["hostname"] or dev["uid"][:8], command_id))
    return run_id


def _ssh_identity(row):
    return hashlib.sha256(json.dumps([row["ssh_host"] or row["host"], row["ssh_port"], row["ssh_user"],
                                     row["host_key"]]).encode()).hexdigest()


def _start_proxmox(conn, script):
    from . import homeassistant
    if not _can_proxmox():
        abort(403)
    if script["platform"] != "linux" or request.form.get("when") != "now" or not homeassistant.ssh_binary():
        abort(400)
    host_ids = set(groups.identifiers(request.form.getlist("proxmox_host"), MAX_TARGETS))
    group_ids = set(groups.identifiers(request.form.getlist("device_group")))
    for group_id in group_ids:
        groups._group("device", group_id)
    linked = conn.execute("SELECT * FROM proxmox_hosts WHERE host_key!=''").fetchall()
    if not host_ids <= {row["id"] for row in linked}:
        abort(409, _("La liaison SSH de Proxmox a changé. Rechargez la page."))
    selected = host_ids | {r["host_id"] for r in conn.execute("SELECT group_id, host_id FROM proxmox_group_members")
                           if r["group_id"] in group_ids}
    targets = [row for row in linked if row["id"] in selected]
    if not 1 <= len(targets) <= MAX_TARGETS:
        abort(400, _("Choisissez au moins un serveur Proxmox relié par SSH."))
    run_id = _snapshot(conn, script, g.user["username"], fleet._now())
    conn.executemany("INSERT INTO script_run_targets (run_id, proxmox_id, hostname, ssh_identity, last_state) "
                     "VALUES (?,?,?,?,'queued')",
                     [(run_id, row["id"], row["name"], _ssh_identity(row)) for row in targets])
    audit("scripts.execution", f"{script['name']} v{script['version']} : Proxmox ({len(targets)})", commit=False)
    conn.commit()
    return back("script_run", run_id=run_id)


def run_next_proxmox():
    """Réserve une cible avant de lancer SSH. Une exécution interrompue ne peut pas être rejouée automatiquement."""
    from . import features, proxmox_system
    now = time.time()
    ssh_timeout = proxmox_system.SCRIPT_TIMEOUT + 15
    stale_before = now - ssh_timeout - 45
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM script_run_targets WHERE ssh_identity!='' AND "
                        "(last_state='queued' OR (last_state='running' AND ssh_started<?)) LIMIT 1", (stale_before,)).fetchone() is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE script_run_targets SET last_state='failed', last_output=? "
                     "WHERE ssh_identity!='' AND last_state='running' AND ssh_started<?",
                     (_("Suivi SSH interrompu. Vérifiez le résultat sur le serveur."), stale_before))
        if {"scripts", "proxmox"} & features.disabled(conn):
            return False
        job = conn.execute("SELECT t.*, r.content FROM script_run_targets t JOIN script_runs r ON r.id=t.run_id "
                           "WHERE t.ssh_identity!='' AND t.last_state='queued' ORDER BY t.id LIMIT 1").fetchone()
        if job is None:
            return False
        conn.execute("UPDATE script_run_targets SET last_state='running', ssh_started=? WHERE id=?", (now, job["id"]))
        host = conn.execute("SELECT * FROM proxmox_hosts WHERE id=?", (job["proxmox_id"],)).fetchone()
    code, state = None, "failed"
    try:
        if host is None or not host["host_key"] or _ssh_identity(host) != job["ssh_identity"]:
            raise proxmox_system.LinkError(_("La liaison SSH de Proxmox a changé. Rechargez la page."))
        code, output = proxmox_system.run(host, job["content"], timeout=ssh_timeout, stdin_script=True)
        if code == 0:
            state = "success"
    except (proxmox_system.LinkError, OSError) as exc:
        output = str(exc)
    with transaction() as conn:
        conn.execute("UPDATE script_run_targets SET last_state=?, last_output=?, ssh_exit_code=? "
                     "WHERE id=? AND last_state='running' AND ssh_started=?",
                     (state, output[-12000:], code, job["id"], now))
    return True


def start_proxmox_worker():
    from threading import Thread
    from . import ha

    def work():
        while True:
            try:
                if not ha.skip_background() and run_next_proxmox():
                    continue
            except Exception:
                app.logger.exception("Proxmox script worker")
            time.sleep(3)

    Thread(target=work, name="proxmox-scripts", daemon=True).start()


@app.route("/fleet/scripts/<int:script_id>/run", methods=["POST"])
@login_required
def script_run_start(script_id):
    from . import schedules
    conn = lock_access("scripts.run")
    script = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if script is None:
        abort(404)
    if request.form.get("version") != str(script["version"]):
        abort(409, _("Le script a été modifié entre-temps : vérifiez-le avant de l'exécuter."))
    if request.form.get("target") == "proxmox":
        return _start_proxmox(conn, script)
    device_ids = groups.identifiers(request.form.getlist("device"), MAX_TARGETS)
    group_ids = groups.identifiers(request.form.getlist("device_group"))
    ids = set(device_ids)
    for gid in group_ids:
        ids |= {r[0] for r in conn.execute("SELECT device_id FROM device_group_members WHERE group_id=?", (gid,))}
    if not all(scope.allows(i) for i in device_ids):
        abort(404)
    devices = [d for d in (conn.execute("SELECT * FROM devices WHERE id=?", (i,)).fetchone() for i in sorted(ids))
               if d and scope.allows(d["id"])]                # membres des groupes : seulement ceux du parc
    targets = [d for d in devices if _compatible(d, script["platform"])]
    if not targets or len(targets) > MAX_TARGETS:
        abort(400, _('Choisissez au moins un poste compatible avec ce script.'))
    if request.form.get("when") == "schedule":
        # Planifié : les cibles restent dynamiques (membres actuels des groupes à chaque occurrence).
        try:
            sched_id = schedules.create(conn, "run-script", {"script_id": script["id"], "version": script["version"]},
                                        {"devices": device_ids, "groups": group_ids}, request.form)
        except ValueError as e:
            abort(400, str(e))
        conn.commit()
        flash(_('Planification créée : %(script)s.', script=script['name']), "ok")
        return back("schedules", _anchor=f"planif-{sched_id}")
    run_id = start_run(conn, script, targets, g.user["username"])
    audit("scripts.execution", f"{script['name']} v{script['version']} : {counted(len(targets), 'poste')}", commit=False)
    conn.commit()
    skipped = len(devices) - len(targets)
    flash(n_('Script envoyé à %(num)s poste au prochain contact de son agent', 'Script envoyé à %(num)s postes au prochain contact de leur agent', len(targets)) + (n_(' ; %(num)s poste incompatible ou inactif ignoré.', ' ; %(num)s postes incompatibles ou inactifs ignorés.', skipped) if skipped else "."), "ok")
    return back("script_run", run_id=run_id)


@app.route("/fleet/scripts/runs/<int:run_id>")
@login_required
def script_run(run_id):
    run = db().execute("SELECT * FROM script_runs WHERE id=?", (run_id,)).fetchone()
    if run is None or not _run_in_scope(db(), run_id):
        abort(404)
    targets = _targets(run_id)
    signature = hashlib.sha256(json.dumps([(t["id"], t["state"], t["output"]) for t in targets]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    script = db().execute("SELECT * FROM scripts WHERE id=?", (run["script_id"],)).fetchone() if run["script_id"] else None
    return render_template("script_run.html", run=run, script=script, targets=targets, summary=_summary(targets),
                           proxmox_run=any(t["ssh_identity"] for t in targets), platforms=PLATFORMS, states=STATES,
                           signature=signature)


@app.route("/fleet/scripts/runs/<int:run_id>/cancel", methods=["POST"])
@login_required
def script_run_cancel(run_id):
    conn = lock_access("scripts.run")
    if not _run_in_scope(conn, run_id):
        abort(404)
    conn.execute("UPDATE device_commands SET state='cancelled', finished_at=?, output='Annulé avant transmission à l’agent.' "
                 "WHERE state='queued' AND id IN (SELECT command_id FROM script_run_targets WHERE run_id=?)", (fleet._now(), run_id))
    proxmox_run = conn.execute("SELECT 1 FROM script_run_targets WHERE run_id=? AND ssh_identity!='' LIMIT 1",
                               (run_id,)).fetchone() is not None
    if proxmox_run:
        conn.execute("UPDATE script_run_targets SET last_state='cancelled' WHERE run_id=? "
                     "AND ssh_identity!='' AND last_state='queued'", (run_id,))
    audit("scripts.execution", f"annulation des postes en attente de l'exécution {run_id}", commit=False)
    conn.commit()
    flash(_("Envois en attente annulés.") if proxmox_run else
          _('Postes en attente annulés. Un script déjà reçu par un agent peut se terminer.'), "ok")
    return back("script_run", run_id=run_id)

