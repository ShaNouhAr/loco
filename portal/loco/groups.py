"""Groupes explicites : appartenances multiples, sans action implicite sur les agents."""
import sqlite3
import zipfile

from flask import abort, flash, render_template, request

from . import app, fleet
from .auth import audit, lock_access, login_required, require
from .db import db
from .util import back


def identifiers(values, limit=500):
    if len(values) > limit:
        abort(400, "Sélection trop volumineuse.")
    try:
        selected = list(dict.fromkeys(int(value) for value in values))
    except (ValueError, TypeError):
        abort(400, "Identifiant de sélection invalide.")
    if any(value <= 0 for value in selected):
        abort(400)
    return selected


def listing(kind):
    table, members, column, entities = (("software_groups", "software_group_members", "app_id", "win_apps") if kind == "software"
                                       else ("device_groups", "device_group_members", "device_id", "devices"))
    where = "" if kind == "software" else " AND e.state!='removed'"
    return db().execute(f"SELECT g.*,(SELECT COUNT(*) FROM {members} m JOIN {entities} e ON e.id=m.{column} "
                        f"WHERE m.group_id=g.id{where}) AS members FROM {table} g ORDER BY g.name COLLATE NOCASE").fetchall()


def memberships(kind, entity_id):
    table, members, column = (("software_groups", "software_group_members", "app_id") if kind == "software"
                              else ("device_groups", "device_group_members", "device_id"))
    return db().execute(f"SELECT g.* FROM {table} g JOIN {members} m ON m.group_id=g.id WHERE m.{column}=? "
                        "ORDER BY g.name COLLATE NOCASE", (entity_id,)).fetchall()


def _group(kind, group_id):
    table = "software_groups" if kind == "software" else "device_groups"
    row = db().execute(f"SELECT * FROM {table} WHERE id=?", (group_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _editor(kind, group_id):
    software = kind == "software"
    group = _group(kind, group_id) if group_id else None
    if software:
        rows = db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE").fetchall()
        selected = {r[0] for r in db().execute("SELECT app_id FROM software_group_members WHERE group_id=?", (group_id,))}
        if not group_id:
            wanted = set(request.args.getlist("app"))
            selected = {r["id"] for r in rows if r["slug"] in wanted}
    else:
        rows = db().execute("SELECT * FROM devices WHERE state!='removed' ORDER BY hostname COLLATE NOCASE").fetchall()
        selected = {r[0] for r in db().execute("SELECT device_id FROM device_group_members WHERE group_id=?", (group_id,))}
    return render_template("group_editor.html", kind=kind, group=group, rows=rows, selected=selected)


def _save(kind, group_id):
    software = kind == "software"
    conn = lock_access("windows.groups" if software else "fleet.groups")
    if group_id:
        _group(kind, group_id)
    name, description = request.form.get("name", "").strip(), request.form.get("description", "").strip()
    if not name or len(name) > 80 or len(description) > 500 or any(ord(c) < 32 for c in name):
        abort(400, "Indiquez un nom de groupe de 1 à 80 caractères.")
    selected = identifiers(request.form.getlist("members"))
    table, member_table, column, entities = (("software_groups", "software_group_members", "app_id", "win_apps") if software
                                           else ("device_groups", "device_group_members", "device_id", "devices"))
    valid = {r[0] for r in conn.execute(f"SELECT id FROM {entities}" + ("" if software else " WHERE state!='removed'"))}
    if not set(selected) <= valid:
        abort(400, "Un membre a été supprimé : rechargez la page.")
    try:
        if group_id:
            conn.execute(f"UPDATE {table} SET name=?,description=?,updated_at=? WHERE id=?", (name, description, fleet._now(), group_id))
        else:
            group_id = conn.execute(f"INSERT INTO {table} (name,description,updated_at) VALUES (?,?,?)",
                                    (name, description, fleet._now())).lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("Ce nom de groupe existe déjà.", "error")
        return back("software_groups" if software else "fleet_groups")
    conn.execute(f"DELETE FROM {member_table} WHERE group_id=?", (group_id,))
    for position, entity_id in enumerate(selected):
        if software:
            conn.execute(f"INSERT INTO {member_table} (group_id,{column},position) VALUES (?,?,?)", (group_id, entity_id, position))
        else:
            conn.execute(f"INSERT INTO {member_table} (group_id,{column}) VALUES (?,?)", (group_id, entity_id))
    audit("groupes.logiciels" if software else "groupes.machines", f"{name} : {len(selected)} membre(s)", commit=False)
    conn.commit()
    flash("Groupe enregistré. Les déploiements déjà confirmés conservent leurs cibles et leurs logiciels.", "ok")
    return back("software_group_editor" if software else "fleet_group_editor", group_id=group_id)


def _delete(kind, group_id):
    software = kind == "software"
    conn = lock_access("windows.groups" if software else "fleet.groups")
    group = _group(kind, group_id)
    table = "software_groups" if software else "device_groups"
    conn.execute(f"DELETE FROM {table} WHERE id=?", (group_id,))
    audit("groupes.suppression", f"{kind} : {group['name']}", commit=False)
    conn.commit()
    flash("Groupe supprimé. Les appareils, logiciels et déploiements sont conservés.", "ok")
    return back("software_groups" if software else "fleet_groups")


@app.route("/windows-apps/groupes")
@login_required
def software_groups():
    return render_template("groups.html", kind="software", groups=listing("software"))


@app.route("/windows-apps/groupes/nouveau", defaults={"group_id": 0})
@app.route("/windows-apps/groupes/<int:group_id>")
@login_required
def software_group_editor(group_id):
    return _editor("software", group_id)


@app.route("/windows-apps/groupes/enregistrer", defaults={"group_id": 0}, methods=["POST"])
@app.route("/windows-apps/groupes/<int:group_id>/enregistrer", methods=["POST"])
@login_required
def software_group_save(group_id):
    return _save("software", group_id)


@app.route("/windows-apps/groupes/<int:group_id>/supprimer", methods=["POST"])
@login_required
def software_group_delete(group_id):
    return _delete("software", group_id)


@app.route("/parc/groupes")
@login_required
def fleet_groups():
    return render_template("groups.html", kind="device", groups=listing("device"))


@app.route("/parc/groupes/nouveau", defaults={"group_id": 0})
@app.route("/parc/groupes/<int:group_id>")
@login_required
def fleet_group_editor(group_id):
    return _editor("device", group_id)


@app.route("/parc/groupes/enregistrer", defaults={"group_id": 0}, methods=["POST"])
@app.route("/parc/groupes/<int:group_id>/enregistrer", methods=["POST"])
@login_required
def fleet_group_save(group_id):
    return _save("device", group_id)


@app.route("/parc/groupes/<int:group_id>/supprimer", methods=["POST"])
@login_required
def fleet_group_delete(group_id):
    return _delete("device", group_id)


@app.route("/parc/<int:dev_id>/groupes", methods=["POST"])
@login_required
def fleet_group_membership(dev_id):
    conn = lock_access("fleet.groups")
    dev = fleet._get(dev_id)
    selected = identifiers(request.form.getlist("groups"))
    valid = {r[0] for r in conn.execute("SELECT id FROM device_groups")}
    if not set(selected) <= valid:
        abort(400)
    conn.execute("DELETE FROM device_group_members WHERE device_id=?", (dev_id,))
    conn.executemany("INSERT INTO device_group_members VALUES (?,?)", [(gid, dev_id) for gid in selected])
    audit("groupes.appartenance", f"{dev['hostname']} : {len(selected)} groupe(s)", commit=False)
    conn.commit()
    return back("fleet_device", dev_id=dev_id)


@app.route("/windows-apps/<slug>/groupe", methods=["POST"])
@login_required
def software_group_attach(slug):
    from .winapps import get_app
    conn = lock_access("windows.groups")
    row = get_app(slug)
    selected = identifiers([request.form.get("group_id", "")])
    group = _group("software", selected[0])
    conn.execute("INSERT OR IGNORE INTO software_group_members(group_id,app_id,position) VALUES (?,?,"
                 "(SELECT COALESCE(MAX(position),-1)+1 FROM software_group_members WHERE group_id=?))", (group["id"], row["id"], group["id"]))
    audit("groupes.ajout-logiciel", f"{row['name']} → {group['name']}", commit=False)
    conn.commit()
    return back("software_group_editor", group_id=group["id"])


@app.route("/windows-apps/store/<package_id>/groupe", methods=["POST"])
@login_required
def store_group_attach(package_id):
    from . import winget
    group_id = identifiers([request.form.get("group_id", "")])[0]
    _group("software", group_id)
    row = db().execute("SELECT * FROM win_apps WHERE winget_id=? ORDER BY id LIMIT 1", (package_id,)).fetchone()
    if row is None:
        require("store.import")
        try:
            row = winget.import_package(package_id)
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
            flash(f"Import impossible : {e}", "error")
            return back("software_store_detail", package_id=package_id)
    conn = lock_access("windows.groups")
    group = _group("software", group_id)
    conn.execute("INSERT OR IGNORE INTO software_group_members(group_id,app_id,position) VALUES (?,?,"
                 "(SELECT COALESCE(MAX(position),-1)+1 FROM software_group_members WHERE group_id=?))", (group_id, row["id"], group_id))
    audit("groupes.ajout-logiciel", f"{row['name']} → {group['name']}", commit=False)
    conn.commit()
    return back("software_group_editor", group_id=group_id)
