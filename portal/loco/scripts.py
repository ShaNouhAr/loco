"""Scripts : bibliothèque PowerShell (Windows) et shell (Linux), exécutés en SYSTEM/root par l'agent, résultats par poste."""
import hashlib
import json

from flask import abort, flash, g, render_template, request

from . import app, fleet, groups
from .auth import audit, lock_access, login_required
from .db import db
from .util import back

PLATFORMS = {"windows": "Windows · PowerShell", "linux": "Linux · shell"}
MAX_CONTENT = 64 * 1024
MAX_TARGETS = 500
ACTIVE = ("queued", "sent", "running")
STATES = {"queued": "En attente du poste", "sent": "Transmis à l’agent", "running": "En cours", "success": "Réussi",
          "failed": "Échec", "cancelled": "Annulé"}


def _script(script_id):
    row = db().execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _compatible(dev, platform):
    return dev["state"] == "active" and (dev["os_family"] == "windows") == (platform == "windows")


def _targets(run_id):
    rows = db().execute("SELECT t.*, c.state AS command_state, COALESCE(c.output, t.last_output) AS output, c.exit_code, "
                        "c.finished_at, c.sent_at FROM script_run_targets t LEFT JOIN device_commands c ON c.id=t.command_id "
                        "WHERE t.run_id=? ORDER BY t.hostname COLLATE NOCASE", (run_id,)).fetchall()
    return [{**dict(r), "state": r["command_state"] or r["last_state"] or "cancelled"} for r in rows]


def _summary(targets):
    counts = {"success": 0, "active": 0, "failed": 0, "other": 0}
    for t in targets:
        key = "active" if t["state"] in ACTIVE else t["state"] if t["state"] in ("success", "failed") else "other"
        counts[key] += 1
    counts["total"] = len(targets)
    return counts


@app.route("/parc/scripts")
@login_required
def scripts():
    rows = db().execute("SELECT s.*, (SELECT MAX(created_at) FROM script_runs r WHERE r.script_id=s.id) AS last_run, "
                        "(SELECT COUNT(*) FROM script_runs r WHERE r.script_id=s.id) AS runs FROM scripts s "
                        "ORDER BY s.name COLLATE NOCASE").fetchall()
    recent = db().execute("SELECT * FROM script_runs ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("scripts.html", scripts=rows, platforms=PLATFORMS,
                           recent=[{"run": r, "summary": _summary(_targets(r["id"]))} for r in recent])


@app.route("/parc/scripts/nouveau", defaults={"script_id": 0})
@app.route("/parc/scripts/<int:script_id>")
@login_required
def script_editor(script_id):
    script = _script(script_id) if script_id else None
    runs = db().execute("SELECT * FROM script_runs WHERE script_id=? ORDER BY id DESC LIMIT 15", (script_id,)).fetchall() if script else []
    return render_template("script_editor.html", script=script, platforms=PLATFORMS, max_content=MAX_CONTENT,
                           runs=[{"run": r, "summary": _summary(_targets(r["id"]))} for r in runs])


@app.route("/parc/scripts/enregistrer", defaults={"script_id": 0}, methods=["POST"])
@app.route("/parc/scripts/<int:script_id>/enregistrer", methods=["POST"])
@login_required
def script_save(script_id):
    conn = lock_access("scripts.edit")
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    platform = request.form.get("platform", "")
    content = request.form.get("content", "").replace("\r\n", "\n")
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name) or len(description) > 300 or platform not in PLATFORMS:
        abort(400, "Nom (1 à 80 caractères), description (300 au plus) et système attendus.")
    if not content.strip() or len(content.encode()) > MAX_CONTENT:
        abort(400, "Contenu du script vide ou trop long (64 Kio au plus).")
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
    flash("Script enregistré.", "ok")
    return back("script_editor", script_id=script_id)


@app.route("/parc/scripts/<int:script_id>/supprimer", methods=["POST"])
@login_required
def script_delete(script_id):
    conn = lock_access("scripts.edit")
    script = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if script is None:
        abort(404)
    conn.execute("DELETE FROM scripts WHERE id=?", (script_id,))
    audit("scripts.suppression", script["name"], commit=False)
    conn.commit()
    flash("Script supprimé. Les résultats de ses exécutions restent consultables.", "ok")
    return back("scripts")


@app.route("/parc/scripts/<int:script_id>/executer")
@login_required
def script_compose(script_id):
    script = _script(script_id)
    devices = db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE").fetchall()
    return render_template("script_compose.html", script=script, platforms=PLATFORMS,
                           devices=[d for d in devices if _compatible(d, script["platform"])],
                           incompatible=sum(1 for d in devices if not _compatible(d, script["platform"])),
                           device_groups=groups.listing("device"), selected={int(v) for v in request.args.getlist("device") if v.isdigit()})


@app.route("/parc/scripts/<int:script_id>/executer", methods=["POST"])
@login_required
def script_run_start(script_id):
    conn = lock_access("scripts.run")
    script = conn.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if script is None:
        abort(404)
    if request.form.get("version") != str(script["version"]):
        abort(409, "Le script a été modifié entre-temps : vérifiez-le avant de l'exécuter.")
    ids = set(groups.identifiers(request.form.getlist("device"), MAX_TARGETS))
    for gid in groups.identifiers(request.form.getlist("device_group")):
        ids |= {r[0] for r in conn.execute("SELECT device_id FROM device_group_members WHERE group_id=?", (gid,))}
    devices = [d for d in (conn.execute("SELECT * FROM devices WHERE id=?", (i,)).fetchone() for i in sorted(ids)) if d]
    targets = [d for d in devices if _compatible(d, script["platform"])]
    if not targets or len(targets) > MAX_TARGETS:
        abort(400, "Choisissez au moins un poste compatible avec ce script.")
    now, user = fleet._now(), g.user["username"]
    sha = hashlib.sha256(script["content"].encode()).hexdigest()
    run_id = conn.execute("INSERT INTO script_runs (script_id, name, version, platform, content, sha256, created_by, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?)", (script_id, script["name"], script["version"], script["platform"],
                                                        script["content"], sha, user, now)).lastrowid
    for dev in targets:
        command_id = conn.execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by) "
                                  "VALUES (?, 'run-script', ?, ?, ?)", (dev["id"], json.dumps({"run_id": run_id}), now, user)).lastrowid
        conn.execute("INSERT INTO script_run_targets (run_id, device_id, hostname, command_id) VALUES (?,?,?,?)",
                     (run_id, dev["id"], dev["hostname"] or dev["uid"][:8], command_id))
    audit("scripts.execution", f"{script['name']} v{script['version']} : {len(targets)} poste(s)", commit=False)
    conn.commit()
    skipped = len(devices) - len(targets)
    flash(f"Script envoyé à {len(targets)} poste(s) au prochain contact de leur agent"
          + (f" ; {skipped} poste(s) incompatible(s) ou inactif(s) ignoré(s)." if skipped else "."), "ok")
    return back("script_run", run_id=run_id)


@app.route("/parc/scripts/executions/<int:run_id>")
@login_required
def script_run(run_id):
    run = db().execute("SELECT * FROM script_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        abort(404)
    targets = _targets(run_id)
    signature = hashlib.sha256(json.dumps([(t["id"], t["state"], t["output"]) for t in targets]).encode()).hexdigest()[:16]
    if request.headers.get("X-Requested-With") == "fetch":
        return {"signature": signature}
    script = db().execute("SELECT * FROM scripts WHERE id=?", (run["script_id"],)).fetchone() if run["script_id"] else None
    return render_template("script_run.html", run=run, script=script, targets=targets, summary=_summary(targets),
                           platforms=PLATFORMS, states=STATES, signature=signature)


@app.route("/parc/scripts/executions/<int:run_id>/annuler", methods=["POST"])
@login_required
def script_run_cancel(run_id):
    conn = lock_access("scripts.run")
    conn.execute("UPDATE device_commands SET state='cancelled', finished_at=?, output='Annulé avant transmission à l’agent.' "
                 "WHERE state='queued' AND id IN (SELECT command_id FROM script_run_targets WHERE run_id=?)", (fleet._now(), run_id))
    audit("scripts.execution", f"annulation des postes en attente de l'exécution {run_id}", commit=False)
    conn.commit()
    flash("Postes en attente annulés. Un script déjà reçu par un agent peut se terminer.", "ok")
    return back("script_run", run_id=run_id)

