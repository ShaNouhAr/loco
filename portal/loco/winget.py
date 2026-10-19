"""Catalogue winget (Microsoft) : recherche de logiciels Windows et import dans le catalogue loco.

L'index public de winget (source.msix, base SQLite) est téléchargé une fois par jour. Le manifeste d'un logiciel
(YAML) est vérifié par l'empreinte SHA-256 inscrite dans l'index ; il donne l'installeur, son empreinte SHA-256
(vérifiée après téléchargement), son type, les options silencieuses et le nom affiché dans Windows (détection
des versions installées sur le parc).
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

from flask import flash, render_template, request, send_file

from . import app, config
from .auth import audit, login_required, can, lock_access, require
from .db import db
from .util import back, now, slugify

SOURCE = "https://cdn.winget.microsoft.com/cache/"
INDEX_DIR = os.path.join(config.DATA, "winget")   # cache régénérable : hors des sauvegardes (STATE)
INDEX = os.path.join(INDEX_DIR, "index.db")
MAX_AGE = 86400
MAX_INDEX = 200 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")
_LOCK = threading.Lock()

# Arguments silencieux par type d'installeur (mêmes choix que winget), si le manifeste n'en donne pas.
SILENT = {"msi": "/qn /norestart", "wix": "/qn /norestart", "nullsoft": "/S",
          "inno": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "burn": "/quiet /norestart"}
SUPPORTED = ("msi", "wix", "nullsoft", "inno", "burn", "exe")
MAX_ICON = 4 * 1024 * 1024
ICON_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "ico": "image/x-icon"}
# Complément provenant de l'éditeur lorsque WinGet ne fournit pas encore son logo.
PUBLISHER_ICONS = {
    "VideoLAN.VLC": "https://images.videolan.org/images/favicon.ico",
    "Audacity.Audacity": "https://www.audacityteam.org/favicon.ico",
    "BlenderFoundation.Blender": "https://www.blender.org/favicon.ico",
    "Git.Git": "https://git-scm.com/favicon.ico",
    "Google.Chrome": "https://www.google.com/chrome/static/images/favicons/favicon.ico",
    "Inkscape.Inkscape": "https://inkscape.org/favicon.ico",
    "KeePassXCTeam.KeePassXC": "https://keepassxc.org/favicon.ico",
    "SumatraPDF.SumatraPDF": "https://www.sumatrapdfreader.org/favicon.ico",
    "TheDocumentFoundation.LibreOffice": "https://www.libreoffice.org/favicon.ico",
}
_ICON_LOCKS = {}
_ICON_GUARD = threading.Lock()


# --------------------------------------------------------------------------- YAML (sous-ensemble des manifestes)

def _scalar(text):
    text = text.strip()
    if not text or text == "~" or text == "null":
        return "" if text != "~" and text != "null" else None
    if text[0] == '"' and text.endswith('"') and len(text) > 1:
        body = text[1:-1]
        return re.sub(r'\\(x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)', _unescape, body)
    if text[0] == "'" and text.endswith("'") and len(text) > 1:
        return text[1:-1].replace("''", "'")
    if text in ("[]", "{}"):
        return [] if text == "[]" else {}
    if text[0] == "[" and text.endswith("]"):
        return [_scalar(x) for x in text[1:-1].split(",") if x.strip()]
    return re.sub(r"\s+#.*$", "", text)


def _unescape(m):
    s = m.group(1)
    if s[0] in "xuU" and len(s) > 1:
        return chr(int(s[1:], 16))
    return {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "/": "/", " ": " "}.get(s, s)


def parse_yaml(text):
    """Mappings, séquences, scalaires (simples, entre guillemets, blocs | et >) : la forme des manifestes winget."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.strip() and not raw.lstrip().startswith("#") and raw.strip() != "---":
            lines.append(raw.rstrip())
    pos = [0]

    def indent(line):
        return len(line) - len(line.lstrip(" "))

    def block_scalar(min_indent, style):
        out = []
        while pos[0] < len(lines) and (indent(lines[pos[0]]) >= min_indent):
            out.append(lines[pos[0]][min_indent:])
            pos[0] += 1
        return ("\n" if style.startswith("|") else " ").join(out)

    def value_after(rest, level):
        rest = rest.strip()
        if rest.startswith(("|", ">")):
            nxt = lines[pos[0]] if pos[0] < len(lines) else ""
            return block_scalar(max(indent(nxt), level + 1), rest)
        if rest:
            # Scalaire sur plusieurs lignes (texte ou guillemets) : lignes suivantes plus indentées, jointes.
            while pos[0] < len(lines) and indent(lines[pos[0]]) > level:
                rest += " " + lines[pos[0]].strip()
                pos[0] += 1
            return _scalar(rest)
        if pos[0] < len(lines) and (indent(lines[pos[0]]) > level or
                                    (indent(lines[pos[0]]) == level and lines[pos[0]].lstrip().startswith("- "))):
            return node(indent(lines[pos[0]]))
        return ""

    def node(level):
        if lines[pos[0]].lstrip().startswith("-"):
            seq = []
            while pos[0] < len(lines) and indent(lines[pos[0]]) == level and lines[pos[0]].lstrip().startswith("-"):
                item = lines[pos[0]].lstrip()[1:]
                if not item.strip():
                    pos[0] += 1
                    seq.append(node(indent(lines[pos[0]])) if pos[0] < len(lines) and indent(lines[pos[0]]) > level else "")
                    continue
                inner = level + 1 + (len(item) - len(item.lstrip(" ")))
                if re.match(r"^\s*[^\s\"'][^:]*:(\s|$)", item) or re.match(r"^\s*\"[^\"]*\":(\s|$)", item):
                    lines[pos[0]] = " " * inner + item.lstrip(" ")   # « - clé: valeur » : mapping en ligne
                    seq.append(node(inner))
                else:
                    pos[0] += 1
                    seq.append(_scalar(item))
            return seq
        mapping = {}
        while pos[0] < len(lines) and indent(lines[pos[0]]) == level and not lines[pos[0]].lstrip().startswith("- "):
            line = lines[pos[0]].strip()
            m = re.match(r'^("(?:[^"\\]|\\.)*"|[^:]+?):(?:\s+(.*))?$', line) or re.match(r"^(.+?):$", line)
            if not m:
                raise ValueError(f"YAML non pris en charge : {line[:60]}")
            key = _scalar(m.group(1))
            rest = (m.group(2) if m.re.groups >= 2 else "") or ""
            pos[0] += 1
            mapping[key] = value_after(rest, level)
        return mapping

    if not lines:
        return {}
    result = node(indent(lines[0]))
    if pos[0] != len(lines):
        raise ValueError(f"YAML non pris en charge : {lines[pos[0]].strip()[:60]}")
    return result


# --------------------------------------------------------------------------- index

def _fetch(url, limit, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("réponse trop volumineuse")
    return data


def refresh_index(force=False):
    """Index winget local, renouvelé chaque jour ; renvoie son chemin."""
    with _LOCK:
        if not force and os.path.isfile(INDEX) and time.time() - os.path.getmtime(INDEX) < MAX_AGE:
            return INDEX
        os.makedirs(INDEX_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=INDEX_DIR, prefix=".msix-")
        os.close(fd)
        try:
            req = urllib.request.Request(SOURCE + "source.msix", headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            with zipfile.ZipFile(tmp) as z:
                info = z.getinfo("Public/index.db")
                if info.file_size > MAX_INDEX:
                    raise ValueError("index winget trop volumineux")
                with z.open(info) as src, open(INDEX + ".new", "wb") as out:
                    shutil.copyfileobj(src, out)
            with sqlite3.connect(f"file:{INDEX}.new?mode=ro", uri=True) as conn:
                conn.execute("SELECT 1 FROM manifest LIMIT 1").fetchone()
            os.replace(INDEX + ".new", INDEX)
        finally:
            for path in (tmp, INDEX + ".new"):
                if os.path.exists(path):
                    os.unlink(path)
        return INDEX


def _connect():
    try:
        path = refresh_index()
    except (OSError, ValueError, zipfile.BadZipFile):
        if not os.path.isfile(INDEX):
            raise
        path = INDEX                       # consultation du dernier index valide hors ligne
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _version_key(v):
    from .fleet import version_key
    return version_key(v)


def _path(conn, part):
    parts = []
    while part:
        row = conn.execute("SELECT parent, pathpart FROM pathparts WHERE rowid=?", (part,)).fetchone()
        if row is None:
            break
        parts.append(row["pathpart"])
        part = row["parent"]
    return "/".join(reversed(parts))


QUERY = """
SELECT m.rowid AS rowid, i.id AS id, n.name AS name, v.version AS version, mo.moniker AS moniker,
       m.pathpart AS pathpart, lower(hex(m.hash)) AS hash
FROM manifest m JOIN ids i ON i.rowid = m.id JOIN names n ON n.rowid = m.name
JOIN versions v ON v.rowid = m.version JOIN monikers mo ON mo.rowid = m.moniker
"""


def search(query, limit=25):
    """Dernière version des logiciels dont le nom, l'identifiant ou le surnom contient la recherche."""
    query = query.strip()
    if len(query) < 2:
        return []
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with _connect() as conn:
        rows = conn.execute(QUERY + " WHERE n.name LIKE ? ESCAPE '\\' OR i.id LIKE ? ESCAPE '\\' "
                            "OR mo.moniker LIKE ? ESCAPE '\\'", (like, like, like)).fetchall()
    latest = {}
    for r in rows:
        if r["id"] not in latest or _version_key(r["version"]) > _version_key(latest[r["id"]]["version"]):
            latest[r["id"]] = r
    q = query.lower()

    def rank(r):
        exact = q in (r["id"].lower(), r["name"].lower(), (r["moniker"] or "").lower())
        return (not exact, not r["name"].lower().startswith(q), len(r["name"]), r["name"].lower())
    return [{"id": r["id"], "name": r["name"], "version": r["version"]} for r in sorted(latest.values(), key=rank)[:limit]]


def manifest(package_id):
    """Manifeste fusionné de la dernière version, vérifié par l'empreinte de l'index."""
    if not ID_RE.match(package_id):
        raise ValueError("identifiant winget invalide")
    with _connect() as conn:
        rows = conn.execute(QUERY + " WHERE i.id = ?", (package_id,)).fetchall()
        if not rows:
            raise ValueError(f"{package_id} absent du catalogue winget")
        row = max(rows, key=lambda r: _version_key(r["version"]))
        path = _path(conn, row["pathpart"])
    if not path.startswith("manifests/") or ".." in path.split("/") or re.search(r"[\x00-\x1f\\?#]", path):
        raise ValueError("chemin de manifeste inattendu")
    data = _fetch(SOURCE + urllib.parse.quote(path, safe="/+"), MAX_MANIFEST)
    if row["hash"] and hashlib.sha256(data).hexdigest() != row["hash"]:
        raise ValueError("empreinte du manifeste winget incorrecte")
    doc = parse_yaml(data.decode("utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("manifeste winget illisible")
    return doc


def choose_installer(doc):
    """Installeur retenu : x64 puis x86, MSI de préférence, portée machine, français puis anglais."""
    candidates = []
    for inst in doc.get("Installers") or []:
        if not isinstance(inst, dict):
            continue
        get = lambda k: inst.get(k) if inst.get(k) not in (None, "", [], {}) else doc.get(k)  # noqa: E731
        arch = (get("Architecture") or "").lower()
        kind = (get("InstallerType") or "").lower()
        scope = (get("Scope") or "").lower()
        url = get("InstallerUrl") or ""
        sha = (get("InstallerSha256") or "").lower()
        if arch not in ("x64", "x86", "neutral") or kind not in SUPPORTED or not url.startswith("https://") \
                or not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        switches = get("InstallerSwitches") or {}
        switches = switches if isinstance(switches, dict) else {}
        silent = switches.get("Silent") or SILENT.get(kind, "")
        if not silent:
            continue                        # exe sans option silencieuse connue : installation interactive
        if kind == "inno" and scope == "machine" and "/ALLUSERS" not in silent.upper():
            silent += " /ALLUSERS"
        if switches.get("Custom"):
            silent += " " + switches["Custom"]
        entries = get("AppsAndFeaturesEntries") or []
        entry = entries[0] if entries and isinstance(entries[0], dict) else {}
        locale = (get("InstallerLocale") or "").lower()
        score = (arch == "x64", kind in ("msi", "wix"), scope != "user", locale.startswith("fr"),
                 locale.startswith("en") or not locale)
        candidates.append((score, {"url": url, "sha256": sha, "kind": "msi" if kind in ("msi", "wix") else "exe",
                                   "args": silent.strip(), "display_name": entry.get("DisplayName") or "",
                                   "display_version": entry.get("DisplayVersion") or "", "type": kind}))
    if not candidates:
        raise ValueError("aucun installeur Windows x64/x86 installable en silence dans ce manifeste")
    return max(candidates, key=lambda c: c[0])[1]


def describe(package_id):
    doc = manifest(package_id)
    inst = choose_installer(doc)
    name = doc.get("PackageName") or package_id
    # Sans nom déclaré pour Windows : nom du paquet sans sa précision finale (« Mozilla Firefox (fr) »).
    detect = inst["display_name"] or re.sub(r"\s*\([^)]*\)\s*$", "", name) or name
    return {"id": package_id, "name": name, "publisher": doc.get("Publisher") or "",
            "version": inst["display_version"] or str(doc.get("PackageVersion") or ""), "detect": detect, **inst}


# --------------------------------------------------------------------------- logos

def _icon_valid(data, kind):
    return bool(data) and len(data) <= MAX_ICON and (
        (kind == "png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
        (kind == "jpeg" and data.startswith(b"\xff\xd8\xff")) or
        (kind == "ico" and data.startswith(b"\0\0\1\0")))


def package_icon(package_id):
    """Logo du manifeste vérifié, cache local par contenu ; aucun lien externe dans le navigateur."""
    if not ID_RE.fullmatch(package_id):
        return None
    directory = os.path.join(INDEX_DIR, "icons")
    record = os.path.join(directory, hashlib.sha256(package_id.encode()).hexdigest() + ".json")
    with _ICON_GUARD:
        lock = _ICON_LOCKS.setdefault(package_id, threading.Lock())
    with lock:
        cached, metadata = None, {}
        try:
            with open(record) as f:
                metadata = json.load(f)
            name = metadata.get("file", "")
            if re.fullmatch(r"[0-9a-f]{64}\.(png|jpeg|ico)", name):
                path = os.path.join(directory, name)
                with open(path, "rb") as f:
                    data = f.read(MAX_ICON + 1)
                kind = name.rsplit(".", 1)[1]
                if _icon_valid(data, kind) and hashlib.sha256(data).hexdigest() == name.split(".")[0]:
                    cached = (path, ICON_TYPES[kind])
            if time.time() - float(metadata.get("updated", 0)) < float(metadata.get("ttl", MAX_AGE)):
                if cached or (name == "" and metadata.get("publisher", "") == PUBLISHER_ICONS.get(package_id, "")):
                    return cached
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            doc = manifest(package_id)
            icons = [i for i in (doc.get("Icons") or []) if isinstance(i, dict)]
            icons.sort(key=lambda i: (i.get("IconTheme", "default") == "default",
                                     i.get("IconFileType") == "png", i.get("IconResolution") in ("64x64", "96x96")), reverse=True)
            name = ""
            for icon in icons:
                kind = icon.get("IconFileType")
                url = icon.get("IconUrl", "")
                # Les icônes enrichies par Microsoft ont une adresse immuable portant leur empreinte.
                match = re.fullmatch(r"https://cdn\.winget\.microsoft\.com/icons/([0-9a-fA-F]{64})\.(png|jpeg|ico)", url)
                if kind not in ICON_TYPES or not match or match[2] != kind:
                    continue
                expected = (icon.get("IconSha256") or match[1]).lower()
                if expected != match[1].lower():
                    continue
                data = _fetch(url, MAX_ICON, timeout=15)
                if not _icon_valid(data, kind) or hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("logo winget invalide ou empreinte incorrecte")
                name = expected + "." + kind
                break
            if not name and package_id in PUBLISHER_ICONS:
                data = _fetch(PUBLISHER_ICONS[package_id], MAX_ICON, timeout=15)
                if _icon_valid(data, "ico"):
                    name = hashlib.sha256(data).hexdigest() + ".ico"
            os.makedirs(directory, exist_ok=True)
            if name:
                fd, tmp = tempfile.mkstemp(dir=directory, prefix=".icon-")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    os.replace(tmp, os.path.join(directory, name))
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".meta-")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({"file": name, "updated": time.time(), "ttl": MAX_AGE if name else 3600,
                               "publisher": PUBLISHER_ICONS.get(package_id, "")}, f)
                os.replace(tmp, record)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return (os.path.join(directory, name), ICON_TYPES[name.rsplit(".", 1)[1]]) if name else None
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile):
            return cached


@app.route("/windows-apps/icons/<package_id>")
@login_required
def software_icon(package_id):
    icon = package_icon(package_id)
    if icon is None:
        return "", 204, {"Cache-Control": "private, max-age=900"}
    response = send_file(icon[0], mimetype=icon[1], conditional=True, max_age=MAX_AGE)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


# --------------------------------------------------------------------------- portail

# Sélections éditoriales : les versions et installeurs viennent toujours de l'index Microsoft.
CATEGORIES = {
    "selection": ("À découvrir", ("7zip.7zip", "Mozilla.Firefox", "Google.Chrome", "VideoLAN.VLC",
                                  "Notepad++.Notepad++", "TheDocumentFoundation.LibreOffice", "Microsoft.VisualStudioCode",
                                  "Git.Git", "SumatraPDF.SumatraPDF", "GIMP.GIMP.3", "Inkscape.Inkscape",
                                  "KeePassXCTeam.KeePassXC", "Audacity.Audacity", "BlenderFoundation.Blender")),
    "navigateurs": ("Navigateurs", ("Mozilla.Firefox", "Google.Chrome", "Microsoft.Edge", "Brave.Brave", "Vivaldi.Vivaldi")),
    "bureautique": ("Bureautique", ("TheDocumentFoundation.LibreOffice", "SumatraPDF.SumatraPDF", "PDFsam.PDFsam",
                                   "Mozilla.Thunderbird", "Notepad++.Notepad++")),
    "outils": ("Outils", ("7zip.7zip", "Microsoft.PowerToys", "WinSCP.WinSCP", "PuTTY.PuTTY", "WiresharkFoundation.Wireshark")),
    "creation": ("Création et médias", ("VideoLAN.VLC", "GIMP.GIMP.3", "Inkscape.Inkscape", "Audacity.Audacity",
                                        "BlenderFoundation.Blender", "OBSProject.OBSStudio")),
    "developpement": ("Développement", ("Microsoft.VisualStudioCode", "Git.Git", "Python.Python.3.13",
                                        "OpenJS.NodeJS.LTS", "Docker.DockerDesktop")),
    "securite": ("Sécurité", ("KeePassXCTeam.KeePassXC", "Bitwarden.Bitwarden", "Cryptomator.Cryptomator")),
    "all": ("Tout le catalogue", ()),
}


def browse(query="", category="all", page=1, size=24):
    """Pagination par logiciel (pas par manifeste/version) sur le véritable index winget."""
    category = category if category in CATEGORIES else "all"
    page, size = max(1, page), min(48, max(1, size))
    conditions, args = [], []
    if query.strip():
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(n.name LIKE ? ESCAPE '\\' OR i.id LIKE ? ESCAPE '\\' OR mo.moniker LIKE ? ESCAPE '\\')")
        args.extend((like, like, like))
    ids = CATEGORIES[category][1]
    if ids:
        conditions.append("i.id IN (" + ",".join("?" for _ in ids) + ")")
        args.extend(ids)
    joins = QUERY[QUERY.index("FROM manifest"):]
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(DISTINCT i.id) " + joins + where, args).fetchone()[0]
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        order, rank_args = "i.id COLLATE NOCASE", []
        if query.strip():
            exact = like[1:-1]
            order = ("MIN(CASE WHEN n.name LIKE ? ESCAPE '\\' OR i.id LIKE ? ESCAPE '\\' "
                     "OR mo.moniker LIKE ? ESCAPE '\\' THEN 0 WHEN n.name LIKE ? ESCAPE '\\' "
                     "THEN 1 ELSE 2 END), i.id COLLATE NOCASE")
            rank_args = [exact, exact, exact, exact + "%"]
        selected = [r[0] for r in conn.execute("SELECT i.id " + joins + where +
                    " GROUP BY i.id ORDER BY " + order + " LIMIT ? OFFSET ?", (*args, *rank_args, size, (page - 1) * size))]
        if not selected:
            return [], total, page, pages
        rows = conn.execute(QUERY + " WHERE i.id IN (" + ",".join("?" for _ in selected) + ")", selected).fetchall()
    latest = {}
    for row in rows:
        if row["id"] not in latest or _version_key(row["version"]) > _version_key(latest[row["id"]]["version"]):
            latest[row["id"]] = row
    return [{"id": latest[i]["id"], "name": latest[i]["name"], "version": latest[i]["version"]}
            for i in selected], total, page, pages


@app.route("/windows-apps/store")
@login_required
def software_store():
    query = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "all" if query else "selection")
    if category not in CATEGORIES:
        category = "all"
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    results, total, pages, error = [], 0, 1, ""
    try:
        results, total, page, pages = browse(query, category, page)
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        error = f"Le catalogue est momentanément indisponible : {e}"
    known = {r["winget_id"]: dict(r) for r in db().execute("SELECT slug, winget_id, status FROM win_apps WHERE winget_id != ''")}
    if not can("windows.view"):
        known = {}
    updated = time.strftime("%d/%m/%Y à %H:%M", time.localtime(os.path.getmtime(INDEX))) if os.path.isfile(INDEX) else ""
    return render_template("software_store.html", query=query, category=category, categories=CATEGORIES,
                           results=results, total=total, page=page, pages=pages, known=known, error=error, updated=updated)


@app.route("/windows-apps/store/<package_id>")
@login_required
def software_store_detail(package_id):
    doc, info, error = {}, None, ""
    try:
        doc = manifest(package_id)
        info = choose_installer(doc)
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        error = str(e)
    known = db().execute("SELECT * FROM win_apps WHERE winget_id=?", (package_id,)).fetchone()
    if not can("windows.view"):
        known = None
    homepage = doc.get("PackageUrl") or doc.get("PublisherUrl") or ""
    if not isinstance(homepage, str) or not homepage.startswith("https://"):
        homepage = ""
    from .groups import listing
    store_version = str(doc.get("PackageVersion") or "")
    newer = bool(known and store_version and _version_key(store_version) > _version_key(known["version"]))
    return render_template("software_store_detail.html", software_groups=listing("software") if can("windows.groups") else [], package_id=package_id, doc=doc, info=info,
                           error=error, known=known, homepage=homepage, newer=newer)


@app.route("/windows-apps/store/refresh", methods=["POST"])
@login_required
def software_store_refresh():
    try:
        refresh_index(force=True)
        flash("Catalogue Microsoft WinGet actualisé.", "ok")
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        flash(f"Actualisation impossible : {e}. Le dernier index valide reste disponible.", "error")
    return back("software_store")



@app.route("/windows-apps/winget")
@login_required
def winget_search():
    query = request.args.get("q", "")[:100]
    results, error = [], ""
    if query:
        try:
            results = search(query)
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
            error = f"Catalogue winget indisponible : {e}"
    known = {r["winget_id"] for r in db().execute("SELECT winget_id FROM win_apps WHERE winget_id != ''")}
    if not can("windows.view"):
        known = set()
    return render_template("winget.html", query=query, results=results, error=error, known=known)


def _filename(info):
    from werkzeug.utils import secure_filename
    name = secure_filename(urllib.parse.unquote(os.path.basename(info["url"].split("?")[0]))) or "installer"
    ext = ".msi" if info["kind"] == "msi" else ".exe"
    return name if name.lower().endswith(ext) else name + ext


def import_package(package_id):
    """Import commun au store, aux groupes et aux déploiements ; un seul import concurrent par identifiant."""
    from .winapps import start_fetch
    require("store.import")
    existing = db().execute("SELECT * FROM win_apps WHERE winget_id=? ORDER BY id LIMIT 1", (package_id,)).fetchone()
    if existing:
        return existing
    info = describe(package_id)
    conn = lock_access("store.import")
    existing = conn.execute("SELECT * FROM win_apps WHERE winget_id=? ORDER BY id LIMIT 1", (package_id,)).fetchone()
    if existing:
        conn.commit()
        return existing
    slug = slugify(info["name"]) or slugify(package_id)
    base, n = slug, 2
    while conn.execute("SELECT 1 FROM win_apps WHERE slug=?", (slug,)).fetchone():
        slug, n = f"{base[:60]}-{n}", n + 1
    cur = conn.execute("INSERT INTO win_apps (slug,name,version,kind,args,source_url,status,updated_at,detect,winget_id,"
                       "expected_sha256,source_filename) VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?)",
                       (slug, info["name"][:100], info["version"][:50], info["kind"], info["args"], info["url"], now(),
                        info["detect"][:200], package_id, info["sha256"], _filename(info)))
    row = conn.execute("SELECT * FROM win_apps WHERE id=?", (cur.lastrowid,)).fetchone()
    audit("windows.winget", f"{package_id} {info['version']} ({info['type']})", commit=False)
    conn.commit()
    start_fetch(row, expected_sha256=info["sha256"], filename=_filename(info))
    return db().execute("SELECT * FROM win_apps WHERE id=?", (row["id"],)).fetchone()


@app.route("/windows-apps/winget/add", methods=["POST"])
@login_required
def winget_add():
    try:
        row = import_package(request.form.get("id", ""))
    except (OSError, ValueError, sqlite3.Error) as e:
        flash(f"Import impossible : {e}", "error")
        return back("software_store", q=request.form.get("q", ""))
    flash(f"{row['name']} figure dans le catalogue local. Son installeur est vérifié avant tout déploiement.", "ok")
    if request.form.get("retour") == "store":          # ajout depuis la liste : on y reste pour en ajouter d'autres
        return back("software_store", **{k: request.form[k] for k in ("q", "category", "page") if request.form.get(k)})
    return back("software_detail", slug=row["slug"])


def check_updates():
    """Logiciels du catalogue issus de winget : (fiche, nouvelle description) quand une version plus récente existe."""
    out = []
    for row in db().execute("SELECT * FROM win_apps WHERE winget_id != '' ORDER BY name"):
        try:
            info = describe(row["winget_id"])
        except (OSError, ValueError, sqlite3.Error):
            continue
        if _version_key(info["version"]) > _version_key(row["version"]) or (
                info["sha256"] != (row["sha256"] or "").lower() and info["version"] != row["version"]):
            out.append((row, info))
    return out


@app.route("/windows-apps/winget/update", methods=["POST"])
@login_required
def winget_update():
    """Nouvelle version winget des logiciels du catalogue : nouvel installeur, puis mise à jour possible du parc."""
    from .winapps import start_fetch
    try:
        updates = check_updates()
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as e:
        flash(f"Catalogue winget indisponible : {e}", "error")
        return back("windows_apps")
    for row, info in updates:
        if row["status"] == "downloading":
            continue
        db().execute("UPDATE win_apps SET version=?, kind=?, args=?, source_url=?, detect=?, updated_at=?,expected_sha256=?,source_filename=? WHERE id=?",
                     (info["version"][:50], info["kind"], info["args"], info["url"], row["detect"] or info["detect"],
                      now(), info["sha256"], _filename(info), row["id"]))
        db().commit()
        start_fetch(db().execute("SELECT * FROM win_apps WHERE id=?", (row["id"],)).fetchone(),
                    expected_sha256=info["sha256"], filename=_filename(info))
        audit("windows.winget-maj", f"{row['winget_id']} {row['version']} -> {info['version']}")
    names = ", ".join(f"{r['name']} {i['version']}" for r, i in updates)
    flash(f"Nouvelles versions : {names}. Les postes concernés passent en orange dans Mon parc." if updates
          else "Les logiciels issus de winget sont à jour.", "ok")
    return back("windows_apps")

