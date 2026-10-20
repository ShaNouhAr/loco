"""Planifications : mises à jour et exécutions de scripts, une fois ou récurrentes, à l'heure du serveur.

Une planification due devient des commandes d'agent au premier contact d'un poste (ou à l'ouverture de la page des
planifications), puis passe à son occurrence suivante : les occurrences manquées (serveur ou postes éteints) ne
s'accumulent pas. Les cibles (tout le parc, groupes, appareils) sont résolues à chaque occurrence. Un script modifié
depuis la planification la met en pause : on n'exécute jamais un contenu qui n'a pas été relu au moment de planifier."""
import calendar
import datetime
import json
import re
import time

from flask import abort, flash, g, redirect, render_template, request, url_for

from . import app, fleet, groups
from .auth import audit, login_required, require
from .db import db
from .util import safe_next

RECURRENCES = {"once": "Une fois", "daily": "Chaque jour", "weekly": "Chaque semaine", "monthly": "Chaque mois"}
ACTIONS = {"update": "Mise à jour", "run-script": "Script"}
DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
          "décembre")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FMT = "%Y-%m-%d %H:%M:%S"
MAX_TARGETS = 500
LAST_DAY = 31                    # « dernier jour du mois »


# --------------------------------------------------------------------------- calcul des occurrences

def _at(s):
    hours, minutes = map(int, s["at_time"].split(":"))
    return datetime.time(hours, minutes)


def next_occurrence(s, after):
    """Première occurrence strictement après « after », ou None (planification ponctuelle passée)."""
    at, start = _at(s), datetime.date.fromisoformat(s["start_date"])
    if s["recurrence"] == "once":
        when = datetime.datetime.combine(start, at)
        return when if when > after else None
    day = max(start, after.date())
    if s["recurrence"] == "monthly":
        year, month = day.year, day.month
        for _ in range(25):
            last = calendar.monthrange(year, month)[1]
            when = datetime.datetime.combine(datetime.date(year, month, min(int(s["monthday"]), last)), at)
            if when > after and when.date() >= start:
                return when
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return None
    days = {int(d) for d in s["weekdays"].split(",") if d != ""} if s["recurrence"] == "weekly" else set(range(7))
    for offset in range(8):
        d = day + datetime.timedelta(days=offset)
        when = datetime.datetime.combine(d, at)
        if d.weekday() in days and when > after:
            return when
    return None


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " et " + items[-1]


def when_text(when):
    """« mardi 21 octobre à 02:00 » (année seulement si elle diffère de l'année en cours)."""
    if isinstance(when, str):
        when = datetime.datetime.strptime(when, FMT)
    text = f"{DAYS[when.weekday()]} {'1er' if when.day == 1 else when.day} {MONTHS[when.month - 1]}"
    if when.year != datetime.date.today().year:
        text += f" {when.year}"
    return f"{text} à {when:%H:%M}"


def describe(s):
    """Récurrence en toutes lettres : « Chaque mardi et jeudi à 02:00 »."""
    at = s["at_time"]
    if s["recurrence"] == "once":
        return "Le " + when_text(datetime.datetime.combine(datetime.date.fromisoformat(s["start_date"]), _at(s)))
    if s["recurrence"] == "daily":
        return f"Chaque jour à {at}"
    if s["recurrence"] == "weekly":
        days = sorted(int(d) for d in s["weekdays"].split(",") if d != "")
        if days == list(range(7)):
            return f"Chaque jour à {at}"
        if days == list(range(5)):
            return f"Du lundi au vendredi à {at}"
        if days == [5, 6]:
            return f"Le week-end à {at}"
        return f"Chaque {_join([DAYS[d] for d in days])} à {at}"
    day = int(s["monthday"])
    if day == LAST_DAY:
        return f"Le dernier jour de chaque mois à {at}"
    return f"Le {'1er' if day == 1 else day} de chaque mois à {at}"


def parse(form, now=None):
    """Champs « Quand » d'un formulaire → colonnes de la planification (avec next_run). ValueError si invalide."""
    now = now or datetime.datetime.now()
    recurrence = form.get("recurrence", "once")
    if recurrence not in RECURRENCES:
        raise ValueError("Récurrence inconnue.")
    at = (form.get("at_time") or "").strip()[:5]
    if not TIME_RE.match(at):
        raise ValueError("Heure invalide.")
    try:
        start = datetime.date.fromisoformat((form.get("start_date") or "").strip())
    except ValueError:
        raise ValueError("Date invalide.") from None
    if start > now.date() + datetime.timedelta(days=366):
        raise ValueError("La planification ne peut pas commencer dans plus d'un an.")
    weekdays, monthday = "", 0
    if recurrence == "weekly":
        days = sorted({int(d) for d in form.getlist("weekdays") if d.isdigit() and int(d) < 7})
        if not days:
            raise ValueError("Choisissez au moins un jour de la semaine.")
        weekdays = ",".join(map(str, days))
    if recurrence == "monthly":
        try:
            monthday = int(form.get("monthday", ""))
        except ValueError:
            monthday = 0
        if not (1 <= monthday <= 28 or monthday == LAST_DAY):
            raise ValueError("Jour du mois invalide.")
    fields = {"recurrence": recurrence, "at_time": at, "start_date": start.isoformat(), "weekdays": weekdays,
              "monthday": monthday}
    upcoming = next_occurrence(fields, now)
    if upcoming is None:
        raise ValueError("L'instant planifié est déjà passé.")
    fields["next_run"] = upcoming.strftime(FMT)
    return fields


def default_date(at="02:00"):
    """Date proposée : aujourd'hui si l'heure n'est pas encore passée, sinon demain."""
    now = datetime.datetime.now()
    today = now.date()
    return (today if now.strftime("%H:%M") < at else today + datetime.timedelta(days=1)).isoformat()


def timezone():
    return time.strftime("%Z") or "heure locale"


# --------------------------------------------------------------------------- cibles et libellés

def _targets(s):
    try:
        return json.loads(s["targets"] or "{}")
    except (TypeError, ValueError):
        return {}


def _params(s):
    try:
        return json.loads(s["params"] or "{}")
    except (TypeError, ValueError):
        return {}


def resolve(conn, s):
    """Postes actifs visés par la planification, résolus maintenant (membres actuels des groupes)."""
    t = _targets(s)
    if t.get("all"):
        return conn.execute("SELECT * FROM devices WHERE state='active' ORDER BY id LIMIT ?", (MAX_TARGETS,)).fetchall()
    ids = {int(i) for i in t.get("devices", [])}
    for gid in t.get("groups", []):
        ids |= {r[0] for r in conn.execute("SELECT device_id FROM device_group_members WHERE group_id=?", (int(gid),))}
    rows = (conn.execute("SELECT * FROM devices WHERE id=?", (i,)).fetchone() for i in sorted(ids))
    return [r for r in rows if r is not None and r["state"] == "active"][:MAX_TARGETS]


def targets_text(conn, targets):
    if targets.get("all"):
        return "Tout le parc"
    parts = []
    for gid in targets.get("groups", []):
        row = conn.execute("SELECT name FROM device_groups WHERE id=?", (int(gid),)).fetchone()
        if row:
            parts.append(f"groupe {row['name']}")
    names = [r["hostname"] or r["uid"][:8] for r in
             (conn.execute("SELECT hostname, uid FROM devices WHERE id=? AND state!='removed'", (int(i),)).fetchone()
              for i in targets.get("devices", [])) if r]
    if len(names) <= 2:
        parts += names
    else:
        parts.append(f"{len(names)} appareils")
    if parts and parts[0].startswith("groupe "):
        parts[0] = "G" + parts[0][1:]                  # « Groupe Serveurs » ; un nom de poste garde sa casse
    return " + ".join(parts) or "Aucun appareil"


def action_text(conn, action, params):
    if action == "update":
        return "Mise à jour" + (", redémarrage si nécessaire" if params.get("reboot") else "")
    script = conn.execute("SELECT name FROM scripts WHERE id=?", (params.get("script_id"),)).fetchone()
    return f"Script « {script['name'] if script else 'supprimé'} »"


def covers(conn, s, dev_id):
    t = _targets(s)
    if t.get("all") or dev_id in {int(i) for i in t.get("devices", [])}:
        return True
    return any(conn.execute("SELECT 1 FROM device_group_members WHERE group_id=? AND device_id=?",
                            (int(gid), dev_id)).fetchone() for gid in t.get("groups", []))


def covering(conn, dev_id):
    """Planifications actives qui concernent ce poste (fiche du poste)."""
    rows = conn.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run!='' ORDER BY next_run").fetchall()
    return [view(conn, s) for s in rows if covers(conn, s, dev_id)]


def view(conn, s):
    params = _params(s)
    state = "paused" if not s["enabled"] else ("done" if not s["next_run"] else "active")
    return {"id": s["id"], "name": s["name"], "action": s["action"], "label": action_text(conn, s["action"], params),
            "targets": targets_text(conn, _targets(s)), "when": describe(s), "recurrence": s["recurrence"],
            "next": when_text(s["next_run"]) if s["next_run"] else "", "next_raw": s["next_run"],
            "last": when_text(s["last_run"]) if s["last_run"] else "", "last_result": s["last_result"],
            "last_ref": s["last_ref"], "state": state, "created_by": s["created_by"]}


# --------------------------------------------------------------------------- droits, création, exécution

def _require(action, params, targets):
    if action == "update":
        require("fleet.update")
        if params.get("reboot"):
            require("fleet.reboot")
        if targets.get("all") or targets.get("groups") or len(targets.get("devices", [])) > 1:
            require("fleet.bulk_update")
    else:
        require("scripts.run")


def _check_targets(targets):
    if not (targets.get("all") or targets.get("groups") or targets.get("devices")):
        raise ValueError("Choisissez au moins un appareil ou un groupe.")


def create(conn, action, params, targets, form):
    """Nouvelle planification depuis un formulaire (champs « Quand ») ; renvoie son identifiant."""
    _require(action, params, targets)
    _check_targets(targets)
    fields = parse(form)
    now, user = fleet._now(), g.user["username"]
    sched_id = conn.execute(
        "INSERT INTO schedules (name, action, params, targets, recurrence, at_time, start_date, weekdays, monthday, "
        "next_run, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ((form.get("name") or "").strip()[:120], action, json.dumps(params), json.dumps(targets), fields["recurrence"],
         fields["at_time"], fields["start_date"], fields["weekdays"], fields["monthday"], fields["next_run"],
         user, now, now)).lastrowid
    audit("planifications.creation", f"{action_text(conn, action, params)} — {describe(fields)} — "
                                     f"{targets_text(conn, targets)}", commit=False)
    return sched_id


def materialize(conn):
    """Transforme les planifications dues en commandes d'agent et les fait passer à l'occurrence suivante."""
    from . import scripts
    now = datetime.datetime.now()
    due = conn.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run!='' AND next_run<=?",
                       (now.strftime(FMT),)).fetchall()
    for s in due:
        upcoming = None if s["recurrence"] == "once" else next_occurrence(s, now)    # ponctuelle : terminée
        claim = conn.execute("UPDATE schedules SET next_run=?, last_run=? WHERE id=? AND next_run=? AND enabled=1",
                             (upcoming.strftime(FMT) if upcoming else "", s["next_run"], s["id"], s["next_run"]))
        if claim.rowcount != 1:
            continue                                # déjà traitée par une requête concurrente
        params, devices, ref = _params(s), resolve(conn, s), None
        if s["action"] == "update":
            added = sum(1 for dev in devices
                        if fleet.queue(dev, "update", {"reboot": bool(params.get("reboot"))}, user=s["created_by"],
                                       run_after=s["next_run"]))
            result = (f"{added} poste(s)" if added else
                      "Déjà en attente sur les postes" if devices else "Aucun poste actif concerné")
        else:
            script = conn.execute("SELECT * FROM scripts WHERE id=?", (params.get("script_id"),)).fetchone()
            if script is None or script["version"] != params.get("version"):
                conn.execute("UPDATE schedules SET enabled=0, last_result=? WHERE id=?",
                             ("Script modifié ou supprimé depuis la planification : mise en pause, à revoir.", s["id"]))
                audit("planifications.pause", f"planification {s['id']} : script modifié ou supprimé",
                      username=s["created_by"], commit=False)
                continue
            targets = [d for d in devices if scripts._compatible(d, script["platform"])]
            if targets:
                ref = scripts.start_run(conn, script, targets, s["created_by"], s["next_run"])
            result = f"{len(targets)} poste(s)" if targets else "Aucun poste compatible actif"
        conn.execute("UPDATE schedules SET last_result=?, last_ref=? WHERE id=?", (result, ref, s["id"]))
        audit("planifications.execution", f"{action_text(conn, s['action'], params)} : {result}",
              username=s["created_by"], commit=False)


# --------------------------------------------------------------------------- pages

def _row(sched_id):
    row = db().execute("SELECT * FROM schedules WHERE id=?", (sched_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _back(default="schedules"):
    target = request.form.get("next") or request.args.get("next")
    return redirect(safe_next(target) if target else url_for(default))


@app.route("/parc/planifications")
@login_required
def schedules():
    conn = db()
    materialize(conn)
    conn.commit()
    rows = conn.execute("SELECT * FROM schedules ORDER BY enabled DESC, next_run='' , next_run, id DESC").fetchall()
    return render_template("schedules.html", items=[view(conn, s) for s in rows], tz=timezone())


@app.route("/parc/planifications/nouvelle", defaults={"sched_id": 0})
@app.route("/parc/planifications/<int:sched_id>")
@login_required
def schedule_editor(sched_id):
    conn = db()
    s = _row(sched_id) if sched_id else None
    if s is not None:
        params, targets = _params(s), _targets(s)
        action, plan = s["action"], dict(s)
    else:
        action = request.args.get("action") if request.args.get("action") in ACTIONS else "update"
        params = {"script_id": int(request.args["script"])} if request.args.get("script", "").isdigit() else {}
        targets = {"all": request.args.get("all") == "1",
                   "devices": [int(v) for v in request.args.getlist("device") if v.isdigit()],
                   "groups": [int(v) for v in request.args.getlist("group") if v.isdigit()]}
        plan = {"recurrence": "weekly", "at_time": "02:00", "start_date": default_date(), "weekdays": "",
                "monthday": 1}
    targets = {"all": bool(targets.get("all")), "devices": [int(i) for i in targets.get("devices", [])],
               "groups": [int(i) for i in targets.get("groups", [])]}
    devices = conn.execute("SELECT * FROM devices WHERE state='active' ORDER BY hostname COLLATE NOCASE").fetchall()
    return render_template("schedule_editor.html", s=s, action=action, params=params, targets=targets, plan=plan,
                           devices=devices, device_groups=groups.listing("device"), tz=timezone(),
                           scripts=conn.execute("SELECT id, name, platform, version FROM scripts "
                                                "ORDER BY name COLLATE NOCASE").fetchall(),
                           actions=ACTIONS, recurrences=RECURRENCES)


def _form_targets(form):
    if form.get("target") == "all":
        return {"all": True}
    return {"devices": groups.identifiers(form.getlist("device"), MAX_TARGETS),
            "groups": groups.identifiers(form.getlist("device_group"))}


def _form_params(conn, action, form):
    if action == "update":
        return {"reboot": form.get("reboot") == "1"}
    try:
        script = conn.execute("SELECT * FROM scripts WHERE id=?", (int(form.get("script_id", "")),)).fetchone()
    except ValueError:
        script = None
    if script is None:
        raise ValueError("Choisissez un script.")
    return {"script_id": script["id"], "version": script["version"]}


@app.route("/parc/planifications/enregistrer", defaults={"sched_id": 0}, methods=["POST"])
@app.route("/parc/planifications/<int:sched_id>/enregistrer", methods=["POST"])
@login_required
def schedule_save(sched_id):
    conn = db()
    action = request.form.get("action")
    if action not in ACTIONS:
        abort(400)
    try:
        params = _form_params(conn, action, request.form)
        targets = _form_targets(request.form)
        if sched_id:
            old = _row(sched_id)
            _require(old["action"], _params(old), _targets(old))
            _require(action, params, targets)
            _check_targets(targets)
            fields = parse(request.form)
            conn.execute("UPDATE schedules SET name=?, action=?, params=?, targets=?, recurrence=?, at_time=?, "
                         "start_date=?, weekdays=?, monthday=?, next_run=?, enabled=1, updated_at=? WHERE id=?",
                         ((request.form.get("name") or "").strip()[:120], action, json.dumps(params),
                          json.dumps(targets), fields["recurrence"], fields["at_time"], fields["start_date"],
                          fields["weekdays"], fields["monthday"], fields["next_run"], fleet._now(), sched_id))
            audit("planifications.modification", f"{action_text(conn, action, params)} — {describe(fields)}",
                  commit=False)
        else:
            sched_id = create(conn, action, params, targets, request.form)
    except ValueError as e:
        flash(str(e), "error")
        if request.form.get("next"):
            return _back()
        return redirect(url_for("schedule_editor", sched_id=sched_id))
    conn.commit()
    flash("Planification enregistrée : " + describe(_row(sched_id)) + ".", "ok")
    return _back()


@app.route("/parc/planifications/<int:sched_id>/pause", methods=["POST"])
@login_required
def schedule_toggle(sched_id):
    conn, s = db(), _row(sched_id)
    _require(s["action"], _params(s), _targets(s))
    if s["enabled"]:
        conn.execute("UPDATE schedules SET enabled=0, updated_at=? WHERE id=?", (fleet._now(), sched_id))
        audit("planifications.pause", describe(s), commit=False)
        flash("Planification mise en pause.", "ok")
    else:
        upcoming = next_occurrence(s, datetime.datetime.now())     # les occurrences manquées sont sautées
        conn.execute("UPDATE schedules SET enabled=1, next_run=?, last_result=CASE WHEN last_result LIKE 'Script modifié%' "
                     "THEN '' ELSE last_result END, updated_at=? WHERE id=?",
                     (upcoming.strftime(FMT) if upcoming else "", fleet._now(), sched_id))
        if s["action"] == "run-script":                            # reprise = contenu actuel relu et accepté
            script = conn.execute("SELECT version FROM scripts WHERE id=?", (_params(s).get("script_id"),)).fetchone()
            if script is None:
                abort(409, "Le script de cette planification a été supprimé.")
            conn.execute("UPDATE schedules SET params=? WHERE id=?",
                         (json.dumps(dict(_params(s), version=script["version"])), sched_id))
        audit("planifications.reprise", describe(s), commit=False)
        flash("Planification reprise." if upcoming else "Planification ponctuelle déjà passée : modifiez sa date.", "ok")
    conn.commit()
    return _back()


@app.route("/parc/planifications/<int:sched_id>/supprimer", methods=["POST"])
@login_required
def schedule_delete(sched_id):
    conn, s = db(), _row(sched_id)
    _require(s["action"], _params(s), _targets(s))
    conn.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
    audit("planifications.suppression", f"{action_text(conn, s['action'], _params(s))} — {describe(s)}", commit=False)
    conn.commit()
    flash("Planification supprimée.", "ok")
    return _back()


@app.context_processor
def _planner_globals():
    return {"recurrence_labels": RECURRENCES, "planner_days": DAYS, "planner_default_date": default_date,
            "planner_default_weekday": lambda: datetime.date.fromisoformat(default_date()).weekday(),
            "planner_tz": timezone}
