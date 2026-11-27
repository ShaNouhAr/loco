"""Groupes explicites : appartenances multiples, sans action implicite sur les agents."""
import sqlite3
import zipfile

from flask import abort, flash, g, render_template, request

from . import app, fleet, scope, update_blocks
from .i18n import _
from .auth import audit, can, lock_access, login_required, require
from .db import db
from .util import back, counted

HOST_TYPES = {"proxmox": ("Proxmox", "proxmox_system_host"), "homeassistant": ("Home Assistant", "homeassistant_host")}


def host_choices(kind, host_id):
    if not can("fleet.view") or not scope.whole():
        return []
    return db().execute(
        f"SELECT g.id, g.name, m.host_id IS NOT NULL AS selected FROM device_groups g "
        f"LEFT JOIN {kind}_group_members m ON m.group_id=g.id AND m.host_id=? "
        "ORDER BY g.name COLLATE NOCASE", (host_id,)
    ).fetchall()


def _group_hosts(group_id):
    if not scope.whole():
        return []
    from . import homeassistant, proxmox_system

    items = []
    for kind, hosts, icon in (
        ("proxmox", proxmox_system.fleet_rows(include_pending=True), "server"),
        ("homeassistant", homeassistant.fleet_rows(), "ha"),
    ):
        if not hosts:
            continue
        selected = {r[0] for r in db().execute(
            f"SELECT host_id FROM {kind}_group_members WHERE group_id=?", (group_id,))}
        label, endpoint = HOST_TYPES[kind]
        for host in hosts:
            system = " ".join(filter(None, ("Proxmox VE", host["version"]))) if kind == "proxmox" else host["system"] or label
            items.append({"row": host, "kind": kind, "icon": icon, "endpoint": endpoint,
                          "system": system,
                          "name": host["name"], "selected": host["id"] in selected,
                          "color": host["color"], "label": host["label"]})
    return items


def identifiers(values, limit=500):
    if len(values) > limit:
        abort(400, _('Sélection trop volumineuse.'))
    try:
        selected = list(dict.fromkeys(int(value) for value in values))
    except (ValueError, TypeError):
        abort(400, _('Identifiant de sélection invalide.'))
    if any(value <= 0 for value in selected):
        abort(400)
    return selected


def listing(kind, *, include_hosts=False):
    """Groupes et nombre de membres ; pour un profil limité à une partie du parc, les groupes qu'il a reçus ou qui
    contiennent de ses postes, comptés sur ses seuls postes."""
    table, members, column, entities = (("software_groups", "software_group_members", "app_id", "win_apps") if kind == "software"
                                       else ("device_groups", "device_group_members", "device_id", "devices"))
    where = "" if kind == "software" else " AND e.state!='removed'" + scope.sql("e.id")
    host_counts = ""
    if include_hosts and kind == "device" and scope.whole():
        host_counts = "".join(f" + (SELECT COUNT(*) FROM {host_kind}_group_members WHERE group_id=g.id)"
                              for host_kind in HOST_TYPES if can(host_kind + ".view"))
    rows = db().execute(f"SELECT g.*,(SELECT COUNT(*) FROM {members} m JOIN {entities} e ON e.id=m.{column} "
                        f"WHERE m.group_id=g.id{where}){host_counts} AS members FROM {table} g ORDER BY g.name COLLATE NOCASE").fetchall()
    if kind == "software" or scope.whole():
        return rows
    _whole, declared, _devices = scope.declared(db(), g.user["role"])
    return [r for r in rows if r["members"] or r["id"] in declared]


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


def _device_state(dev, apps):
    color, reason = fleet.status(dev, apps)
    return {"color": color, "label": fleet.status_label(dev, color), "reason": reason}


def _overview(kind):
    """Par groupe : aperçu des membres (dans l'ordre) et état résumé (logiciels prêts, voyants des appareils)."""
    out = {}
    if kind == "software":
        rows = db().execute("SELECT m.group_id, a.name, a.slug, a.winget_id, a.status FROM software_group_members m "
                            "JOIN win_apps a ON a.id=m.app_id ORDER BY m.group_id, m.position, a.name COLLATE NOCASE")
        for r in rows:
            g = out.setdefault(r["group_id"], {"members": [], "states": {}})
            g["members"].append(r)
            g["states"][r["status"]] = g["states"].get(r["status"], 0) + 1
    else:
        apps = fleet.catalog()
        rows = db().execute("SELECT m.group_id, d.* FROM device_group_members m JOIN devices d ON d.id=m.device_id "
                            "WHERE d.state!='removed'" + scope.sql("d.id") + " ORDER BY m.group_id, d.hostname COLLATE NOCASE")
        for r in rows:
            g = out.setdefault(r["group_id"], {"members": [], "states": {}, "windows": 0, "wu_blocked": 0, "consent": 0, "active": 0})
            color = fleet.status(r, apps)[0]
            g["members"].append(r)
            g["states"][color] = g["states"].get(color, 0) + 1
            if r["state"] == "active":                     # accord avant le bureau distant : réglé sur les postes actifs
                g["active"] += 1
                g["consent"] += 1 if r["remote_consent"] else 0
            if r["os_family"] == "windows":
                g["windows"] += 1
                g["wu_blocked"] += 1 if r["block_windows_update"] else 0
        for kind, (label, _endpoint) in HOST_TYPES.items():
            if scope.whole() and can(kind + ".view"):
                for r in db().execute(f"SELECT m.group_id, h.name FROM {kind}_group_members m "
                                      f"JOIN {kind}_hosts h ON h.id=m.host_id ORDER BY h.name COLLATE NOCASE"):
                    g = out.setdefault(r["group_id"], {"members": [], "states": {}})
                    g.setdefault("hosts", []).append({"name": r["name"], "label": label})
    return out


def _editor(kind, group_id):
    software = kind == "software"
    group = _group(kind, group_id) if group_id else None
    if software:
        rows = db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE").fetchall()
        selected = {r[0] for r in db().execute("SELECT app_id FROM software_group_members WHERE group_id=?", (group_id,))}
        if not group_id:
            wanted = set(request.args.getlist("app"))
            selected = {r["id"] for r in rows if r["slug"] in wanted}
        items = [{"row": r, "selected": r["id"] in selected, "name": r["name"]} for r in rows]
    else:
        rows = db().execute("SELECT * FROM devices WHERE state!='removed'" + scope.sql()
                            + " ORDER BY hostname COLLATE NOCASE").fetchall()           # consultation limitée au parc
        selected = {r[0] for r in db().execute("SELECT device_id FROM device_group_members WHERE group_id=?", (group_id,))}
        apps = fleet.catalog()
        items = [{"row": r, "selected": r["id"] in selected, "name": r["hostname"] or r["uid"][:8],
                  **_device_state(r, apps)} for r in rows]
        items.extend(_group_hosts(group_id))
    items.sort(key=lambda i: (not i["selected"], i["name"].casefold()))
    return render_template("group_editor.html", kind=kind, group=group, items=items, selected=selected,
                           member_count=sum(i["selected"] for i in items), families=fleet.FAMILIES,
                           host_kinds=sorted({i["kind"] for i in items if "kind" in i}))


def _save(kind, group_id):
    software = kind == "software"
    conn = lock_access("windows.groups" if software else "fleet.groups")
    if group_id:
        _group(kind, group_id)
    name, description = request.form.get("name", "").strip(), request.form.get("description", "").strip()
    if not name or len(name) > 80 or len(description) > 500 or any(ord(c) < 32 for c in name):
        abort(400, _('Indiquez un nom de groupe de 1 à 80 caractères.'))
    selected = identifiers(request.form.getlist("members"))
    table, member_table, column, entities = (("software_groups", "software_group_members", "app_id", "win_apps") if software
                                           else ("device_groups", "device_group_members", "device_id", "devices"))
    valid = {r[0] for r in conn.execute(f"SELECT id FROM {entities}" + ("" if software else " WHERE state!='removed'"))}
    if not set(selected) <= valid:
        abort(400, _('Un membre a été supprimé : rechargez la page.'))
    try:
        if group_id:
            conn.execute(f"UPDATE {table} SET name=?,description=?,updated_at=? WHERE id=?", (name, description, fleet._now(), group_id))
        else:
            group_id = conn.execute(f"INSERT INTO {table} (name,description,updated_at) VALUES (?,?,?)",
                                    (name, description, fleet._now())).lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        flash(_('Ce nom de groupe existe déjà.'), "error")
        return back("software_groups" if software else "fleet_groups")
    conn.execute(f"DELETE FROM {member_table} WHERE group_id=?", (group_id,))
    for position, entity_id in enumerate(selected):
        if software:
            conn.execute(f"INSERT INTO {member_table} (group_id,{column},position) VALUES (?,?,?)", (group_id, entity_id, position))
        else:
            conn.execute(f"INSERT INTO {member_table} (group_id,{column}) VALUES (?,?)", (group_id, entity_id))
    if not software:
        submitted = request.form.getlist("host_sections")
        for host_kind in HOST_TYPES:
            if host_kind not in submitted:
                continue
            require(host_kind + ".view")
            hosts = identifiers(request.form.getlist(host_kind))
            if not set(hosts) <= {r[0] for r in conn.execute(f"SELECT id FROM {host_kind}_hosts")}:
                abort(400, _('Un membre a été supprimé : rechargez la page.'))
            conn.execute(f"DELETE FROM {host_kind}_group_members WHERE group_id=?", (group_id,))
            conn.executemany(f"INSERT INTO {host_kind}_group_members VALUES (?,?)", [(group_id, hid) for hid in hosts])
        update_blocks.recount_grouped(conn)
    total = len(selected)
    if not software:
        total += sum(conn.execute(f"SELECT COUNT(*) FROM {host_kind}_group_members WHERE group_id=?", (group_id,)).fetchone()[0]
                     for host_kind in HOST_TYPES)
    audit("groupes.logiciels" if software else "groupes.machines", f"{name} : {counted(total, 'membre')}", commit=False)
    conn.commit()
    flash(_('Groupe enregistré. Les déploiements déjà confirmés conservent leurs cibles et leurs logiciels.'), "ok")
    # Retour à la liste, sur le groupe enregistré (surligné), plutôt que de rester dans l'éditeur.
    return back("software_groups" if software else "fleet_groups", _anchor=f"group-{group_id}")


def _rename(kind):
    """Renommer un groupe depuis la liste : seul le nom change (description, membres et déploiements conservés)."""
    software = kind == "software"
    listing_page = "software_groups" if software else "fleet_groups"
    conn = lock_access("windows.groups" if software else "fleet.groups")
    try:
        group_id = int(request.form.get("group_id", ""))
    except ValueError:
        abort(400)
    group = _group(kind, group_id)
    name = request.form.get("name", "").strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        abort(400, _('Indiquez un nom de groupe de 1 à 80 caractères.'))
    if name != group["name"]:
        try:
            conn.execute(f"UPDATE {'software_groups' if software else 'device_groups'} SET name=?,updated_at=? WHERE id=?",
                         (name, fleet._now(), group_id))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(_('Ce nom de groupe existe déjà.'), "error")
            return back(listing_page, _anchor=f"group-{group_id}")
        audit("groupes.renommage", f"{kind} : {group['name']} → {name}", commit=False)
        conn.commit()
        flash(_("Groupe renommé en « %(name)s ».", name=name), "ok")
    return back(listing_page, _anchor=f"group-{group_id}")


def _delete(kind, group_id):
    software = kind == "software"
    conn = lock_access("windows.groups" if software else "fleet.groups")
    group = _group(kind, group_id)
    table = "software_groups" if software else "device_groups"
    if not software and update_blocks.group_blocks(conn, group_id):
        conn.rollback()
        flash(_("Ce groupe porte des mises à jour bloquées : levez d'abord ces blocages."), "error")
        return back("fleet_groups")
    conn.execute(f"DELETE FROM {table} WHERE id=?", (group_id,))
    audit("groupes.suppression", f"{kind} : {group['name']}", commit=False)
    conn.commit()
    flash(_('Groupe supprimé. Les appareils, logiciels et déploiements sont conservés.'), "ok")
    return back("software_groups" if software else "fleet_groups")


@app.route("/windows-apps/groups")
@login_required
def software_groups():
    return render_template("groups.html", kind="software", groups=listing("software"), overview=_overview("software"))


@app.route("/windows-apps/groups/new", defaults={"group_id": 0})
@app.route("/windows-apps/groups/<int:group_id>")
@login_required
def software_group_editor(group_id):
    return _editor("software", group_id)


@app.route("/windows-apps/groups/save", defaults={"group_id": 0}, methods=["POST"])
@app.route("/windows-apps/groups/<int:group_id>/save", methods=["POST"])
@login_required
def software_group_save(group_id):
    return _save("software", group_id)


@app.route("/windows-apps/groups/rename", methods=["POST"])
@login_required
def software_group_rename():
    return _rename("software")


@app.route("/windows-apps/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def software_group_delete(group_id):
    return _delete("software", group_id)


@app.route("/fleet/groups")
@login_required
def fleet_groups():
    from . import auth, features
    enrollable = ({r["id"] for r in fleet.enrollment_groups()} if auth.can("fleet.enroll") and features.enabled("fleet")
                  else set())
    return render_template("groups.html", kind="device", groups=listing("device"), overview=_overview("device"),
                           enrollable=enrollable)


@app.route("/fleet/groups/new", defaults={"group_id": 0})
@app.route("/fleet/groups/<int:group_id>")
@login_required
def fleet_group_editor(group_id):
    return _editor("device", group_id)


@app.route("/fleet/groups/save", defaults={"group_id": 0}, methods=["POST"])
@app.route("/fleet/groups/<int:group_id>/save", methods=["POST"])
@login_required
def fleet_group_save(group_id):
    return _save("device", group_id)


@app.route("/fleet/groups/rename", methods=["POST"])
@login_required
def fleet_group_rename():
    return _rename("device")


@app.route("/fleet/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def fleet_group_delete(group_id):
    return _delete("device", group_id)


@app.route("/fleet/<int:dev_id>/groups", methods=["POST"])
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
    update_blocks.recount_grouped(conn)
    audit("groupes.appartenance", f"{dev['hostname']} : {counted(len(selected), 'groupe')}", commit=False)
    conn.commit()
    return back("fleet_device", dev_id=dev_id)


@app.route("/fleet/groups/hosts/<kind>/<int:host_id>", methods=["POST"])
@login_required
def fleet_host_group_membership(kind, host_id):
    if kind not in HOST_TYPES:
        abort(404)
    conn = lock_access("fleet.groups")
    require(kind + ".view")
    row = conn.execute(f"SELECT name FROM {kind}_hosts WHERE id=?", (host_id,)).fetchone()
    if row is None:
        abort(404)
    selected = identifiers(request.form.getlist("groups"))
    if not set(selected) <= {r[0] for r in conn.execute("SELECT id FROM device_groups")}:
        abort(400)
    conn.execute(f"DELETE FROM {kind}_group_members WHERE host_id=?", (host_id,))
    conn.executemany(f"INSERT INTO {kind}_group_members VALUES (?,?)", [(gid, host_id) for gid in selected])
    audit("groupes.appartenance", f"{HOST_TYPES[kind][0]} · {row['name']} : {counted(len(selected), 'groupe')}", commit=False)
    conn.commit()
    return back(HOST_TYPES[kind][1], host_id=host_id, _anchor="host-groups")


@app.route("/windows-apps/<slug>/group", methods=["POST"])
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


@app.route("/windows-apps/store/<package_id>/group", methods=["POST"])
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
            flash(_('Import impossible : %(e)s', e=e), "error")
            return back("software_store_detail", package_id=package_id)
    conn = lock_access("windows.groups")
    group = _group("software", group_id)
    conn.execute("INSERT OR IGNORE INTO software_group_members(group_id,app_id,position) VALUES (?,?,"
                 "(SELECT COALESCE(MAX(position),-1)+1 FROM software_group_members WHERE group_id=?))", (group_id, row["id"], group_id))
    audit("groupes.ajout-logiciel", f"{row['name']} → {group['name']}", commit=False)
    conn.commit()
    return back("software_group_editor", group_id=group_id)
