"""Partages ouverts dans le navigateur : le gestionnaire de fichiers d'un partage du serveur loco.

Le portail ne touche jamais lui-même aux dossiers des partages : le service des fichiers (portal/locosamba.py, lancé
par l'agent root sous le compte loco-share) fait chaque opération et passe au portail, déjà ouverts, les fichiers à
lire ou à écrire. Qui ouvre quoi : le droit « Ouvrir les partages dans le navigateur » ; avec « Gérer les partages de
fichiers », tous les partages, en lecture et écriture ; sinon, ceux de l'utilisateur Samba lié au compte loco, avec ses
droits sur le réseau (lecture, ou lecture et écriture). Un partage plein ne peut plus que diminuer.
"""
import functools
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse

from flask import Response, abort, g, render_template, request
from werkzeug.http import http_date
from werkzeug.wsgi import wrap_file

import locosamba
from . import app
from .auth import audit, can, login_required
from .db import db
from .i18n import N_, _
from .util import human

UPLOAD_CHUNK = 64 << 20          # octets d'un fragment de téléversement, au plus
UPLOAD_IDLE = 3600               # téléversement resté sans nouvelles une heure : abandonné
UPLOADS_PER_USER = 24
CALL_TIMEOUT = 60
WALK_TIMEOUT = 45                # recherche, espace disque, taille d'un dossier (le service s'arrête avant)

ERRORS = {
    "not-found": (N_("Élément introuvable : il a peut-être été déplacé ou supprimé entre-temps."), 404),
    "exists": (N_("Un élément porte déjà ce nom."), 409),
    "bad-path": (N_("Chemin refusé."), 400),
    "bad-op": (N_("Demande refusée."), 400),
    "bad-query": (N_("Saisissez un texte à rechercher."), 400),
    "bad-name": (N_("Nom refusé : sans / \\ : * ? \" < > | ni point ou espace à la fin, et pas un nom réservé de Windows (CON, NUL…), pour que les postes Windows puissent l'ouvrir."), 400),
    "link": (N_("C'est un lien symbolique : loco ne le suit pas."), 400),
    "not-dir": (N_("Ce n'est pas un dossier."), 400),
    "is-dir": (N_("C'est un dossier."), 400),
    "full": (N_("Partage plein : supprimez des fichiers pour libérer de la place."), 507),
    "quota": (N_("Pas assez de place dans le partage : il reste %(free)s avant sa taille limite."), 507),
    "disk": (N_("Pas assez de place sur la partition du partage."), 507),
    "into-itself": (N_("Un dossier ne peut pas être copié ou déplacé dans lui-même."), 400),
    "denied": (N_("Accès refusé par le système de fichiers du serveur."), 403),
    "busy": (N_("Trop d'opérations en cours : réessayez dans un instant."), 503),
    "no-share": (N_("Ce partage n'est pas disponible pour le moment."), 503),
    "changed": (N_("Le fichier a été modifié entre-temps par quelqu'un d'autre : rouvrez-le avant d'enregistrer."), 409),
    "cancelled": (N_("Annulé."), 409),
    "size": (N_("Le fichier reçu n'a pas la taille annoncée : recommencez le téléversement."), 400),
    "read-only": (N_("Vous n'avez accès à ce partage qu'en lecture."), 403),
    "service": (N_("Le service des fichiers des partages ne répond pas (agent loco-agent)."), 503),
    "upload": (N_("Téléversement inconnu ou abandonné : recommencez-le."), 404),
    "io": (N_("Opération impossible : %(error)s."), 500),
}

# Ouverts dans le navigateur (aperçu) : images, son, vidéo et PDF seulement ; tout le reste se télécharge, sans jamais
# être interprété par le navigateur (une page HTML ou une image SVG d'un partage n'y exécute rien).
INLINE = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
          "avif": "image/avif", "bmp": "image/bmp", "ico": "image/x-icon",
          "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "ogv": "video/ogg", "mov": "video/quicktime",
          "mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "ogg": "audio/ogg", "oga": "audio/ogg",
          "opus": "audio/ogg", "wav": "audio/wav", "flac": "audio/flac", "pdf": "application/pdf"}
RAW_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; sandbox"
PDF_CSP = "default-src 'none'; frame-ancestors 'self'"

_lock = threading.Lock()
_uploads = {}                    # jeton -> téléversement en cours (fichier provisoire ouvert par le service)
_jobs = {}                       # tâche du service -> (compte, partage) qui l'a lancée


class Refused(Exception):
    def __init__(self, code, **values):
        super().__init__(code)
        self.code, self.values = code, values


def message(code, values=None):
    text, _status = ERRORS.get(code, ERRORS["io"])
    values = dict(values or {})
    if code == "quota":
        values["free"] = human(values.get("free") or 0)
    if code == "io":
        values["error"] = str(values.get("error") or code)[:200]
    try:
        return _(text, **values)
    except (KeyError, ValueError, TypeError):
        return _(ERRORS["io"][0], error=code)


def refusal(e):
    # Un téléchargement passe par un cadre caché de la page : son refus doit pouvoir s'y afficher pour être lu.
    return ({"error": message(e.code, e.values), "code": e.code}, ERRORS.get(e.code, ERRORS["io"])[1],
            {"X-Frame-Options": "SAMEORIGIN", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"})


def api(view):
    """Route du gestionnaire : un refus devient une réponse JSON (message traduit, code HTTP)."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Refused as e:
            return refusal(e)
    return wrapper


# --------------------------------------------------------------------------- droits

def share_mode(row):
    """Accès du compte connecté au partage : "write", "read" ou None."""
    if row is None or not can("samba.files"):
        return None
    if can("samba.manage"):
        return "write"
    found = db().execute("SELECT x.can_write FROM samba_access x JOIN samba_accounts a ON a.id=x.account_id "
                         "WHERE x.share_id=? AND a.user_id=? AND a.disabled=0", (row["id"], g.user["id"])).fetchone()
    return None if found is None else ("write" if found["can_write"] else "read")


def _published(st, name):
    found = (st.get("shares") or {}).get(name) if not st.get("stale") else None
    return found if isinstance(found, dict) and not found.get("error") else None


def openable(st=None):
    """Partages que le compte connecté peut ouvrir dans le navigateur : {numéro: accès}."""
    if not can("samba.files"):
        return {}
    from . import samba
    st = st if st is not None else samba.agent_state()
    found = {}
    for row in db().execute("SELECT * FROM samba_shares ORDER BY name COLLATE NOCASE"):
        mode = share_mode(row)
        if mode and _published(st, row["name"]):
            found[row["id"]] = mode
    return found


def _share(share_id, write=False):
    """(partage, accès, état publié) ; 404 sans accès (le partage n'existe pas pour ce compte)."""
    from . import samba
    row = db().execute("SELECT * FROM samba_shares WHERE id=?", (share_id,)).fetchone()
    mode = share_mode(row)
    if mode is None:
        abort(404)
    if write and mode != "write":
        raise Refused("read-only")
    published = _published(samba.agent_state(), row["name"])
    if published is None:
        raise Refused("no-share")
    return row, mode, published


def _call(row, op, timeout=CALL_TIMEOUT, **args):
    try:
        reply, fds = locosamba.files_call(dict(args, op=op, share=row["name"]), timeout=timeout)
    except (OSError, ValueError):
        raise Refused("service") from None
    if not reply.get("ok"):
        for fd in fds:
            os.close(fd)
        raise Refused(str(reply.get("error") or "io"), **(reply.get("values") if isinstance(reply.get("values"), dict) else {}))
    return reply, fds


def _path(value):
    return value if isinstance(value, str) and len(value) <= 4096 else ""


def _label(row, path):
    return f"{row['name']}/{path}".rstrip("/")


# --------------------------------------------------------------------------- page

@app.route("/shares/<int:share_id>/files")
@login_required
def share_files(share_id):
    try:
        row, mode, published = _share(share_id)
    except Refused as e:
        abort(503, message(e.code, e.values))
    limit, used = int(published.get("limit") or row["size_limit"] or 0), published.get("used")
    modes = openable()
    others = [{"id": r["id"], "name": r["name"], "mode": modes[r["id"]]}
              for r in db().execute("SELECT id, name FROM samba_shares ORDER BY name COLLATE NOCASE")
              if r["id"] in modes and r["id"] != row["id"]]
    pct = min(100, round(100 * used / limit)) if used is not None and limit else None
    return render_template("share_files.html", share=row, mode=mode, full=bool(published.get("full")), used=used,
                           limit=limit, pct=pct, others=others, start=_path(request.args.get("path", "")))


@app.route("/shares/<int:share_id>/files/list")
@login_required
@api
def share_files_list(share_id):
    row, mode, published = _share(share_id)
    reply, _fds = _call(row, "list", path=_path(request.args.get("path", "")))
    limit, used = int(published.get("limit") or 0), published.get("used")
    return {"path": reply["path"], "entries": reply["entries"], "truncated": reply["truncated"], "mode": mode,
            "full": bool(published.get("full")), "used": used, "limit": limit,
            "level": "err" if published.get("full") else "warn" if used is not None and limit and used >= 0.9 * limit else ""}


@app.route("/shares/<int:share_id>/files/search")
@login_required
@api
def share_files_search(share_id):
    row, _mode, _published = _share(share_id)
    reply, _fds = _call(row, "search", timeout=WALK_TIMEOUT, path=_path(request.args.get("path", "")),
                        q=str(request.args.get("q", ""))[:200])
    return {"path": reply["path"], "entries": reply["entries"], "partial": reply["partial"]}


@app.route("/shares/<int:share_id>/files/usage")
@login_required
@api
def share_files_usage(share_id):
    row, _mode, _published = _share(share_id)
    reply, _fds = _call(row, "usage", timeout=WALK_TIMEOUT, path=_path(request.args.get("path", "")))
    return {k: reply[k] for k in ("path", "total", "entries", "partial")}


@app.route("/shares/<int:share_id>/files/size")
@login_required
@api
def share_files_size(share_id):
    row, _mode, _published = _share(share_id)
    reply, _fds = _call(row, "size", timeout=WALK_TIMEOUT, path=_path(request.args.get("path", "")))
    return {k: reply[k] for k in ("path", "size", "files", "dirs", "partial")}


# --------------------------------------------------------------------------- lecture d'un fichier

def _disposition(kind, name):
    """En-tête Content-Disposition : nom ASCII de repli, et nom exact (RFC 5987)."""
    plain = unicodedata.normalize("NFKD", os.fsencode(name).decode("utf-8", "replace")).encode("ascii", "ignore").decode()
    plain = re.sub(r'[^\x20-\x7e]|["\\]', "_", plain).strip() or "fichier"
    return f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(os.fsencode(name), safe='')}"


@app.route("/shares/<int:share_id>/files/raw")
@login_required
@api
def share_files_raw(share_id):
    """Contenu d'un fichier : téléchargé (dl=1, ou tout type hors aperçu), ou ouvert pour l'aperçu (images, son,
    vidéo, PDF) ; morceaux demandés par le navigateur (Range) pour la lecture d'une vidéo."""
    row, _mode, _published = _share(share_id)
    reply, fds = _call(row, "open", path=_path(request.args.get("path", "")))
    handle = os.fdopen(fds[0], "rb")
    try:
        size, name = int(reply["size"]), str(reply["name"])
        mtime = int(reply.get("mtime") or 0)
        etag = f'"{size:x}-{mtime:x}"'
        inline = None if request.args.get("dl") == "1" else INLINE.get(name.rsplit(".", 1)[-1].lower() if "." in name else "")
        headers = {"ETag": etag, "Last-Modified": http_date(mtime / 1000), "Accept-Ranges": "bytes",
                   "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff",
                   "Content-Disposition": _disposition("inline" if inline else "attachment", name),
                   "Content-Security-Policy": PDF_CSP if inline == "application/pdf" else RAW_CSP,
                   "X-Frame-Options": "SAMEORIGIN", "X-Accel-Buffering": "no"}           # nginx : au rythme du navigateur
        if etag in [t.strip() for t in request.headers.get("If-None-Match", "").split(",")]:
            handle.close()
            return Response(status=304, headers=headers)
        start, end, status = 0, size, 200
        ranges = request.range
        if ranges is not None and ranges.units == "bytes" and len(ranges.ranges) == 1 \
                and (not request.headers.get("If-Range") or request.headers.get("If-Range") == etag):
            found = ranges.range_for_length(size)
            if found is None:
                handle.close()
                return Response(status=416, headers=dict(headers, **{"Content-Range": f"bytes */{size}"}))
            (start, end), status = found, 206
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
        handle.seek(start)
        headers["Content-Length"] = str(end - start)
        if status == 200:                                         # fichier entier : envoyé par sendfile (gunicorn)
            body = wrap_file(request.environ, handle, 1 << 20)
        else:
            body = _part(handle, end - start)
        return Response(body, status=status, headers=headers, mimetype=inline or "application/octet-stream",
                        direct_passthrough=True)
    except BaseException:
        handle.close()
        raise


def _part(handle, length):
    """Morceau demandé d'un fichier (Range), exactement ses octets."""
    try:
        while length > 0:
            chunk = handle.read(min(1 << 20, length))
            if not chunk:
                break
            length -= len(chunk)
            yield chunk
    finally:
        handle.close()


@app.route("/shares/<int:share_id>/files/zip", methods=["POST"])
@login_required
@api
def share_files_zip(share_id):
    """Archive ZIP d'une sélection ou d'un dossier, composée à la volée (sans compression : photos et vidéos le sont
    déjà) et envoyée au fil de l'eau."""
    row, _mode, _published = _share(share_id)
    path = _path(request.form.get("path", ""))
    names = [n for n in request.form.getlist("names") if isinstance(n, str)][:locosamba.FILES_ITEMS]
    reply, fds = _call(row, "zip", path=path, names=names)
    fd = fds[0]
    base = names[0] if len(names) == 1 else (path.rstrip("/").rsplit("/", 1)[-1] or row["name"])

    def stream():
        try:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                yield chunk
        finally:
            os.close(fd)
    return Response(stream(), mimetype="application/zip",
                    headers={"Content-Disposition": _disposition("attachment", base + ".zip"),
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- modifications

def _args():
    try:
        args = json.loads(request.form.get("args") or "{}")
    except ValueError:
        raise Refused("bad-op") from None
    if not isinstance(args, dict):
        raise Refused("bad-op")
    return args


def _names(paths):
    names = [str(p).rstrip("/").rsplit("/", 1)[-1] for p in paths[:5]]
    return ", ".join(names) + (f" (+{len(paths) - 5})" if len(paths) > 5 else "")


@app.route("/shares/<int:share_id>/files/op", methods=["POST"])
@login_required
@api
def share_files_op(share_id):
    """Nouveau dossier ou fichier, renommage, suppression, copie et déplacement (vers ce partage, depuis lui ou un autre
    que le compte peut ouvrir) ; les trois dernières sont des tâches suivies par /job."""
    op, args = request.form.get("op", ""), _args()
    if op == "conflicts":
        row, source = _transfer_shares(share_id, args, move=False, check=True)
        reply, _fds = _call(source, "conflicts", paths=args.get("paths"), to_share=row["name"], to=_path(args.get("to", "")))
        return {"conflicts": reply["conflicts"]}
    if op in ("copy", "move"):
        row, source = _transfer_shares(share_id, args, move=op == "move")
        paths = args.get("paths") if isinstance(args.get("paths"), list) else []
        reply, _fds = _call(source, op, paths=paths, to_share=row["name"], to=_path(args.get("to", "")),
                            decisions=args.get("decisions") if isinstance(args.get("decisions"), dict) else {})
        _own(reply["job"], share_id)
        to = _label(row, _path(args.get("to", "")))
        audit("partages.fichiers.copie" if op == "copy" else "partages.fichiers.deplacement",
              f"{source['name']}: {_names(paths)} -> {to}")
        return {"job": reply["job"]}
    row, _mode, _published = _share(share_id, write=True)
    if op == "delete":
        paths = args.get("paths") if isinstance(args.get("paths"), list) else []
        reply, _fds = _call(row, "delete", paths=paths)
        _own(reply["job"], share_id)
        audit("partages.fichiers.suppression", f"{row['name']}: {_names(paths)}")
        return {"job": reply["job"]}
    if op in ("mkdir", "newfile"):
        path = _path(args.get("path", ""))
        reply, _fds = _call(row, op, path=path, parents=bool(args.get("parents")))
        if not args.get("parents"):                               # dossiers d'un téléversement : tracés avec lui
            audit("partages.fichiers.dossier" if op == "mkdir" else "partages.fichiers.creation", _label(row, path))
        return {"path": reply["path"]}
    if op == "rename":
        path = _path(args.get("path", ""))
        reply, _fds = _call(row, "rename", path=path, name=args.get("name"))
        audit("partages.fichiers.renommage", f"{_label(row, path)} -> {reply['name']}")
        return {"path": reply["path"], "name": reply["name"]}
    raise Refused("bad-op")


def _transfer_shares(share_id, args, move, check=False):
    """(partage de destination, partage d'origine) d'une copie ou d'un déplacement, droits vérifiés : écriture à
    destination ; à l'origine (autre partage), lecture pour une copie, écriture pour un déplacement."""
    row, _mode, _published = _share(share_id, write=not check)
    source_id = args.get("from")
    if not isinstance(source_id, int) or source_id == share_id:
        return row, row
    source, _mode, _published = _share(source_id, write=move and not check)
    return row, source


def _own(job, share_id):
    with _lock:
        _jobs[job] = (g.user["id"], share_id)
        if len(_jobs) > 500:
            for key in list(_jobs)[:100]:
                del _jobs[key]


def _job(share_id, job):
    with _lock:
        owner = _jobs.get(job)
    if owner != (g.user["id"], share_id):
        raise Refused("not-found")
    return _share(share_id)[0]


@app.route("/shares/<int:share_id>/files/job/<job>")
@login_required
@api
def share_files_job(share_id, job):
    row = _job(share_id, job)
    reply, _fds = _call(row, "job", job=job)
    view = {k: reply.get(k) for k in ("state", "kind", "done", "total", "count", "items", "current")}
    view["errors"] = [{"name": e.get("name"), "error": message(e.get("error"))} for e in reply.get("errors") or []]
    return view


@app.route("/shares/<int:share_id>/files/job/<job>/cancel", methods=["POST"])
@login_required
@api
def share_files_job_cancel(share_id, job):
    row = _job(share_id, job)
    _call(row, "job_cancel", job=job)
    return {"ok": True}


# --------------------------------------------------------------------------- téléversements

def _forget(token, cancel=True):
    """Téléversement terminé ou abandonné : fichier provisoire refermé (et effacé par le service s'il est abandonné)."""
    with _lock:
        up = _uploads.pop(token, None)
    if up is None:
        return None
    try:
        os.close(up["fd"])
    except OSError:
        pass
    if cancel:
        try:
            locosamba.files_call({"op": "upload_cancel", "share": up["share"], "token": token}, timeout=10)
        except (OSError, ValueError):
            pass
    return up


def _tidy():
    now = time.time()
    with _lock:
        stale = [t for t, up in _uploads.items() if now - up["at"] > UPLOAD_IDLE]
    for token in stale:
        _forget(token)


def _upload(share_id, token):
    with _lock:
        up = _uploads.get(token)
    if up is None or up["user"] != g.user["id"] or up["share_id"] != share_id:
        raise Refused("upload")
    return up


@app.route("/shares/<int:share_id>/files/upload", methods=["POST"])
@login_required
@api
def share_files_upload(share_id):
    """Début d'un téléversement : place vérifiée par le service, fichier provisoire ouvert dans le partage."""
    _tidy()
    row, _mode, _published = _share(share_id, write=True)
    with _lock:
        if sum(1 for up in _uploads.values() if up["user"] == g.user["id"]) >= UPLOADS_PER_USER:
            raise Refused("busy")
    try:
        size = int(request.form.get("size", ""))
    except ValueError:
        raise Refused("size") from None
    folder, name = _path(request.form.get("dir", "")), request.form.get("name", "")
    reply, fds = _call(row, "upload", dir=folder, name=name, size=size)
    with _lock:
        _uploads[reply["token"]] = {"fd": fds[0], "share": row["name"], "share_id": share_id, "user": g.user["id"],
                                    "size": size, "written": 0, "at": time.time(), "dir": folder, "name": name,
                                    "lock": threading.Lock()}
    return {"token": reply["token"]}


@app.route("/shares/<int:share_id>/files/upload/<token>", methods=["POST"])
@login_required
@api
def share_files_upload_chunk(share_id, token):
    """Un fragment (corps brut de la requête) écrit à sa place ; le navigateur reprend où le serveur en est."""
    up = _upload(share_id, token)
    try:
        offset = int(request.args.get("offset", ""))
    except ValueError:
        raise Refused("size") from None
    length = request.content_length
    if length is None or length > UPLOAD_CHUNK:
        abort(413)
    with up["lock"]:
        if offset != up["written"]:
            return {"error": message("size"), "code": "offset", "written": up["written"]}, 409
        if offset + length > up["size"]:
            raise Refused("size")
        position, stream = offset, request.stream
        while position < offset + length:
            data = stream.read(min(1 << 20, offset + length - position))
            if not data:
                break
            view = memoryview(data)
            while view:
                written = os.pwrite(up["fd"], view, position)
                view, position = view[written:], position + written
        up["written"], up["at"] = position, time.time()
    return {"written": position}


@app.route("/shares/<int:share_id>/files/upload/<token>/done", methods=["POST"])
@login_required
@api
def share_files_upload_done(share_id, token):
    """Fin d'un téléversement : contenu mis en sûreté sur le disque, puis le fichier prend sa place dans le dossier."""
    up = _upload(share_id, token)
    row, _mode, _published = _share(share_id, write=True)
    with up["lock"]:
        if up["written"] != up["size"]:
            raise Refused("size")
        os.fsync(up["fd"])
    args = {"dir": up["dir"], "name": up["name"], "token": token, "size": up["size"],
            "conflict": request.form.get("conflict") or None}
    for key in ("mtime", "expect"):
        try:
            args[key] = int(request.form[key])
        except (KeyError, ValueError):
            pass
    try:
        reply, _fds = _call(row, "upload_done", **args)
    except Refused as e:
        if e.code in ("exists", "changed", "is-dir"):            # le navigateur peut redemander, avec un choix
            raise
        _forget(token)
        raise
    _forget(token, cancel=False)
    # Enregistré depuis l'éditeur (date attendue du fichier) : une modification, pas un nouveau fichier.
    audit("partages.fichiers.modification" if "expect" in args else "partages.fichiers.televersement",
          f"{_label(row, reply['path'])} ({human(up['size'])})")
    return {"name": reply["name"], "path": reply["path"], "mtime": reply.get("mtime")}


@app.route("/shares/<int:share_id>/files/upload/<token>/cancel", methods=["POST"])
@login_required
@api
def share_files_upload_cancel(share_id, token):
    _upload(share_id, token)
    _forget(token)
    return {"ok": True}

