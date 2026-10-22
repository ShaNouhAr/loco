"""Logiciels Windows : catalogue d'installeurs."""

import os
import shutil
import sqlite3
import tempfile
import threading

from flask import abort, flash, render_template, request
from werkzeug.utils import secure_filename

from . import app, config
from .i18n import L_, _
from .auth import audit, login_required, can
from .db import db, transaction
from .util import CANCELLED, PROGRESS, Cancelled, back, download, explain, kind_for, now, sha256_of, slugify


def app_dir(slug):
    return os.path.join(config.WIN_DIR, slug)


def store_file(row_id, slug, tmp, filename):
    """Place le fichier téléchargé ou envoyé dans le catalogue et met à jour la fiche."""
    dest_dir = app_dir(slug)
    os.makedirs(dest_dir, exist_ok=True)
    digest = sha256_of(tmp)
    size = os.path.getsize(tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(dest_dir, filename))
    for old in os.listdir(dest_dir):
        if old != filename and not old.startswith("."):
            os.unlink(os.path.join(dest_dir, old))
    with transaction() as conn:
        conn.execute("UPDATE win_apps SET filename=?, size=?, sha256=?, status='ready', error='', updated_at=? "
                     "WHERE id=?", (filename, size, digest, now(), row_id))
        # Type « automatique » : déduit du vrai nom de fichier, avec ses arguments par défaut.
        row = conn.execute("SELECT kind, args FROM win_apps WHERE id=?", (row_id,)).fetchone()
        if row and row["kind"] == "auto":
            kind = kind_for(filename)
            conn.execute("UPDATE win_apps SET kind=?, args=? WHERE id=?",
                         (kind, row["args"] or config.DEFAULT_ARGS[kind], row_id))


def fetch_app(row_id, slug, url, expected_sha256="", filename=""):
    """Téléchargement en arrière-plan d'un logiciel depuis son lien (empreinte imposée : catalogue winget)."""
    tmp = None
    try:
        os.makedirs(app_dir(slug), exist_ok=True)
        try:
            tmp, name = download(url, app_dir(slug), progress_key=slug)
        except Cancelled:
            shutil.rmtree(app_dir(slug), ignore_errors=True)
            return
        filename = filename or name
        if expected_sha256 and sha256_of(tmp) != expected_sha256.lower():
            raise ValueError(_('Empreinte SHA-256 différente de celle du catalogue winget : fichier refusé.'))
        with transaction() as conn:
            still_there = conn.execute("SELECT 1 FROM win_apps WHERE id=?", (row_id,)).fetchone()
        if not still_there:  # supprimé pendant le téléchargement
            os.unlink(tmp)
            tmp = None
            shutil.rmtree(app_dir(slug), ignore_errors=True)
            return
        store_file(row_id, slug, tmp, filename)
        tmp = None
    except Exception as e:  # noqa: BLE001
        with transaction() as conn:
            conn.execute("UPDATE win_apps SET status='error', error=?, updated_at=? WHERE id=?",
                         (explain(e), now(), row_id))
    finally:
        PROGRESS.pop(slug, None)
        CANCELLED.discard(slug)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def start_fetch(row, expected_sha256="", filename=""):
    expected_sha256 = expected_sha256 or row["expected_sha256"]
    filename = filename or row["source_filename"]
    db().execute("UPDATE win_apps SET status='downloading', error='' WHERE id=?", (row["id"],))
    db().commit()
    threading.Thread(target=fetch_app, args=(row["id"], row["slug"], row["source_url"], expected_sha256, filename),
                     daemon=True).start()


def save_upload(row, upload, filename):
    """Enregistre un fichier envoyé ; renvoie un message d'erreur ou None."""
    slug = row["slug"]
    os.makedirs(app_dir(slug), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=app_dir(slug), prefix=".up-")
    os.close(fd)
    try:
        upload.save(tmp)
        store_file(row["id"], slug, tmp, filename or "installer.bin")
        return None
    except (OSError, sqlite3.Error) as e:
        db().execute("UPDATE win_apps SET status='error', error=? WHERE id=?", (f"Envoi impossible : {e}", row["id"]))
        db().commit()
        return f"Envoi impossible : {e}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_app(slug):
    row = db().execute("SELECT * FROM win_apps WHERE slug=?", (slug,)).fetchone()
    if row is None:
        abort(404)
    return row


# Filtres de la liste : clé -> (libellé, classe de la pastille).
ATTENTION = {"parc": (L_("Mises à jour à déployer"), "warn"), "arguments": (L_("Arguments manquants"), "warn"),
             "erreur": (L_("Erreurs"), "err"), "telechargement": (L_("En téléchargement"), "")}


def attention(row, used):
    """Points à traiter pour un logiciel du catalogue (clés de ATTENTION)."""
    keys = set()
    if used and used[1]:
        keys.add("parc")
    if row["status"] == "ready" and row["kind"] == "exe" and not row["args"]:
        keys.add("arguments")
    if row["status"] == "error":
        keys.add("erreur")
    if row["status"] in ("downloading", "pending"):
        keys.add("telechargement")
    return keys


def _back_to(slug):
    """Retour sur la fiche du logiciel quand le formulaire en vient, sinon sur la liste."""
    if request.form.get("retour") == "fiche":
        return back("software_detail", slug=slug)
    return back("windows_apps")


@app.route("/windows-apps")
@login_required
def windows_apps():
    from .fleet import fleet_usage
    rows = db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE").fetchall()
    usage = fleet_usage(rows) if can("fleet.view") else {}
    items = [{"a": r, "used": usage.get(r["slug"]), "flags": attention(r, usage.get(r["slug"]))} for r in rows]
    counts = {k: sum(k in i["flags"] for i in items) for k in ATTENTION}
    state = request.args.get("etat", "")
    if state not in ATTENTION:
        state = ""
    return render_template("windows_apps.html", items=[i for i in items if not state or state in i["flags"]],
                           total=len(items), total_size=sum(r["size"] for r in rows), counts=counts, state=state,
                           filters=ATTENTION, winget=any(r["winget_id"] for r in rows),
                           default_args=config.DEFAULT_ARGS, show_usage=can("fleet.view"))


@app.route("/api/apps")
@login_required
def api_apps():
    out = []
    for r in db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE"):
        item = {k: r[k] for k in ("slug", "name", "version", "kind", "size", "status", "error", "updated_at")}
        done, total = PROGRESS.get(r["slug"], (0, 0))
        item["progress"] = {"done": done, "total": total}
        out.append(item)
    return {"apps": out}


@app.route("/windows-apps/add", methods=["POST"])
@login_required
def windows_apps_add():
    name = request.form.get("name", "").strip()[:100]
    slug = slugify(request.form.get("slug", "").strip() or name)
    url = request.form.get("url", "").strip()
    upload = request.files.get("file")
    has_upload = bool(upload and upload.filename)
    if not name or not config.SLUG_RE.match(slug):
        flash(_('Nom invalide.'), "error")
        return back("windows_apps")
    if not url and not has_upload:
        flash(_('Indiquez un lien ou envoyez un fichier.'), "error")
        return back("windows_apps")
    if url and not url.startswith(("http://", "https://")):
        flash(_('Le lien doit commencer par http:// ou https://.'), "error")
        return back("windows_apps")
    if db().execute("SELECT 1 FROM win_apps WHERE slug=?", (slug,)).fetchone():
        flash(_("L'identifiant « %(slug)s » existe déjà.", slug=slug), "error")
        return back("windows_apps")

    kind = request.form.get("kind") or "auto"
    if kind not in config.DEFAULT_ARGS:
        kind = "auto"
    args = request.form.get("args", "").strip() or config.DEFAULT_ARGS.get(kind, "")
    cur = db().execute(
        "INSERT INTO win_apps (slug, name, version, kind, args, source_url, status, updated_at, detect) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (slug, name, request.form.get("version", "").strip()[:50], kind, args, url, "pending", now(),
         request.form.get("detect", "").strip()[:200]))
    db().commit()
    row = db().execute("SELECT * FROM win_apps WHERE id=?", (cur.lastrowid,)).fetchone()
    audit("windows.ajout", f"{slug} ({url or 'fichier envoyé'})")
    if has_upload:
        error = save_upload(row, upload, secure_filename(upload.filename))
        flash(error or _("%(name)s ajouté au catalogue.", name=name), "error" if error else "ok")
    else:
        start_fetch(row)
        flash(_('Téléchargement de %(name)s lancé.', name=name), "ok")
    return back("windows_apps")


@app.route("/windows-apps/<slug>/edit", methods=["POST"])
@login_required
def windows_apps_edit(slug):
    row = get_app(slug)
    kind = request.form.get("kind", row["kind"])
    if kind not in config.DEFAULT_ARGS and kind != "auto":
        kind = row["kind"]
    url = request.form.get("url", "").strip()
    if url and not url.startswith(("http://", "https://")):
        flash(_('Le lien doit commencer par http:// ou https://.'), "error")
        return _back_to(slug)
    upload = request.files.get("file")
    if url != row["source_url"] or upload and upload.filename:
        db().execute("UPDATE win_apps SET winget_id='',expected_sha256='',source_filename='' WHERE id=?", (row["id"],))
    db().execute("UPDATE win_apps SET name=?, version=?, kind=?, args=?, source_url=?, updated_at=?, detect=? WHERE id=?",
                 (request.form.get("name", row["name"]).strip()[:100] or row["name"],
                  request.form.get("version", "").strip()[:50], kind,
                  request.form.get("args", "").strip(), url, now(),
                  request.form.get("detect", row["detect"]).strip()[:200], row["id"]))
    db().commit()
    detail = slug
    upload = request.files.get("file")
    if upload and upload.filename:
        error = save_upload(row, upload, secure_filename(upload.filename))
        if error:
            flash(error, "error")
            return _back_to(slug)
        detail += " (nouveau fichier)"
    audit("windows.modification", detail)
    flash(_('%(row)s mis à jour.', row=row['name']), "ok")
    return _back_to(slug)


@app.route("/windows-apps/<slug>/refresh", methods=["POST"])
@login_required
def windows_apps_refresh(slug):
    row = get_app(slug)
    if not row["source_url"]:
        flash(_('Pas de lien source : renvoyez le fichier depuis « Modifier ».'), "error")
    elif row["status"] != "downloading":
        start_fetch(row)
        audit("windows.actualisation", slug)
        flash(_('Nouveau téléchargement de %(row)s lancé.', row=row['name']), "ok")
    return _back_to(slug)


@app.route("/windows-apps/<slug>/delete", methods=["POST"])
@login_required
def windows_apps_delete(slug):
    row = get_app(slug)
    if row["status"] == "downloading":
        CANCELLED.add(slug)
    shutil.rmtree(app_dir(slug), ignore_errors=True)
    db().execute("DELETE FROM win_apps WHERE id=?", (row["id"],))
    db().commit()
    audit("windows.suppression", slug)
    flash(_('%(row)s supprimé.', row=row['name']), "ok")
    return back("windows_apps")


@app.route("/windows-apps/software/<slug>")
@login_required
def software_detail(slug):
    from . import fleet, groups
    row = get_app(slug)
    installed = []
    if can("fleet.view"):
        for device in db().execute("SELECT * FROM devices WHERE os_family='windows' AND state!='removed' ORDER BY hostname COLLATE NOCASE"):
            _, version, outdated = fleet.catalog_state(fleet.inventory(device), [row])[0]
            if version:
                installed.append({"device": device, "version": version, "outdated": outdated})
    return render_template("software_detail.html", software=row, installed=installed, default_args=config.DEFAULT_ARGS,
                           software_groups=groups.listing("software") if can("windows.groups") else [],
                           memberships=groups.memberships("software", row["id"]))
