"""Cache APT et dépôts locaux signés, isolés par distribution et version."""

import datetime
import os
import shutil
import subprocess
import tempfile
import threading

from flask import abort, flash, render_template, request
from werkzeug.utils import secure_filename

from . import app, config, debpkg, signing
from .i18n import _
from .auth import audit, login_required
from .db import transaction
from .util import back, download, explain, read_stats, service_active, sha256_of

_apt_lock = threading.RLock()
LEGACY = "legacy"
APT_FTPARCHIVE = shutil.which("apt-ftparchive") or "/usr/local/lib/loco/apt/bin/apt-ftparchive"
# Sans les outils Debian (serveur Windows), les index sont produits en Python (debpkg).
DEBIAN_TOOLS = os.path.exists(APT_FTPARCHIVE) and shutil.which("dpkg-deb") is not None


def repository_path(repository=LEGACY):
    if repository == LEGACY:
        return config.APT_DIR
    parts = repository.split("/")
    if len(parts) != 2 or parts[0] not in config.APT_DISTRIBUTIONS or not config.APT_SUITE_RE.fullmatch(parts[1]):
        raise ValueError(_('Dépôt invalide : distribution et nom de version attendus.'))
    return os.path.join(config.APT_REPOSITORIES_DIR, *parts)


def repositories():
    with transaction() as conn:
        rows = conn.execute("SELECT * FROM apt_repositories ORDER BY distribution, suite").fetchall()
    return [dict(r, key=f"{r['distribution']}/{r['suite']}",
                 label=f"{config.APT_DISTRIBUTIONS[r['distribution']]} {r['version']} ({r['suite']})".replace("  ", " "))
            for r in rows]


def require_repository(repository):
    path = repository_path(repository)
    if repository != LEGACY and not any(r["key"] == repository for r in repositories()):
        raise ValueError(_("Ce dépôt n'existe pas. Créez-le avant d'y ajouter des paquets."))
    return path


def repository_url(repository=LEGACY):
    repository_path(repository)
    return "/apt/" if repository == LEGACY else f"/apt/repositories/{repository}/"


def list_debs(repository=None):
    """Sans sélection : tous les dépôts, pour les métriques ; sinon, uniquement celui demandé."""
    keys = [LEGACY] + [r["key"] for r in repositories()] if repository is None else [repository]
    debs = []
    for key in keys:
        root = repository_path(key)
        try:
            with open(os.path.join(root, "Packages")) as f:
                stanzas = f.read().split("\n\n")
        except OSError:
            continue
        for stanza in stanzas:
            fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
            if "Filename" not in fields:
                continue
            name = os.path.basename(fields["Filename"])
            path = os.path.join(root, "pool", name)
            if not os.path.isfile(path):
                continue
            debs.append({"file": name, "package": fields.get("Package", ""), "version": fields.get("Version", ""),
                         "description": fields.get("Description", "")[:160],
                         "arch": fields.get("Architecture", ""), "size": os.path.getsize(path),
                         "mtime": os.path.getmtime(path), "repository": key, "url": repository_url(key) + "pool/" + name})
    return sorted(debs, key=lambda d: (d["repository"], d["package"], d["version"]))


def deb_fields(path):
    if not DEBIAN_TOOLS:
        return debpkg.deb_fields(path)
    out = subprocess.run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise ValueError(_("ce fichier n'est pas un paquet .deb valide"))
    fields = dict(line.split(": ", 1) for line in out.stdout.strip().splitlines() if ": " in line)
    if not all(k in fields for k in ("Package", "Version", "Architecture")):
        raise ValueError(_('champs Package/Version/Architecture manquants'))
    return fields


def rebuild_apt(repository=LEGACY):
    """Métadonnées produites à part ; les signatures sont publiées après les index."""
    with _apt_lock:
        root = repository_path(repository)
        os.makedirs(os.path.join(root, "pool"), exist_ok=True)
        env = dict(os.environ, GNUPGHOME=config.GNUPGHOME)
        suite = "local" if repository == LEGACY else repository.split("/")[1]
        origin = "loco"
        label = "loco" if repository == LEGACY else f"loco {repository}"
        # APT refuse un changement d'Origin/Label sans confirmation sur chaque poste.
        # Les dépôts déjà publiés gardent ces deux identifiants ; les nouveaux portent loco.
        try:
            with open(os.path.join(root, "Release"), encoding="utf-8") as previous:
                fields = debpkg.parse_stanza(previous.read())
            if fields.get("Origin") == "local-repo":
                origin, label = "local-repo", fields.get("Label", "local-repo")
        except OSError:
            pass
        if DEBIAN_TOOLS:
            packages = subprocess.run([APT_FTPARCHIVE, "packages", "pool"], cwd=root,
                                      capture_output=True, check=True, timeout=120).stdout
        else:
            packages = debpkg.packages_index(root)
        with tempfile.TemporaryDirectory(prefix=".metadata-", dir=root) as stage:
            with open(os.path.join(stage, "Packages"), "wb") as f:
                f.write(packages)
            if DEBIAN_TOOLS:
                subprocess.run(["gzip", "-9nk", "Packages"], cwd=stage, check=True, timeout=30)
                release = subprocess.run(
                    [APT_FTPARCHIVE, "-o", "APT::FTPArchive::Release::Origin=" + origin,
                     "-o", "APT::FTPArchive::Release::Label=" + label,
                     "-o", "APT::FTPArchive::Release::Suite=" + ("stable" if repository == LEGACY else suite),
                     "-o", f"APT::FTPArchive::Release::Codename={suite}", "release", "."],
                    cwd=stage, capture_output=True, check=True, timeout=60).stdout
            else:
                debpkg.gzip_file(os.path.join(stage, "Packages"))
                release = debpkg.release_file(stage, origin, label,
                                              "stable" if repository == LEGACY else suite, suite)
            with open(os.path.join(stage, "Release"), "wb") as f:
                f.write(release)
            if config.WINDOWS:
                signing.sign_release(stage)
            else:
                # Démarrer l'agent gpg à part (son démarrage à froid pendant la signature peut bloquer sur un vieux
                # gpg, p. ex. Rocky 8), avec sa propre limite de temps, avant de signer les index.
                subprocess.run(["gpgconf", "--launch", "gpg-agent"], env=env, check=False,
                               capture_output=True, timeout=30)
                for args in (["--clearsign", "-o", "InRelease"], ["-abs", "-o", "Release.gpg"]):
                    subprocess.run([config.GPG, "--batch", "--yes"] + args + ["Release"], cwd=stage,
                                   env=env, check=True, capture_output=True, timeout=120)
            for name in ("Packages", "Packages.gz", "Release", "Release.gpg", "InRelease"):
                os.chmod(os.path.join(stage, name), 0o644)
                os.replace(os.path.join(stage, name), os.path.join(root, name))


def create_repository(distribution, suite, version=""):
    key = f"{distribution}/{suite}"
    root = repository_path(key)
    if len(version) > 32 or any(not (c.isalnum() or c in ". -") for c in version):
        raise ValueError(_('Numéro de version invalide (32 caractères maximum).'))
    with _apt_lock:
        if any(r["key"] == key for r in repositories()):
            raise ValueError(_('Ce dépôt existe déjà.'))
        existed = os.path.exists(root)
        try:
            rebuild_apt(key)
        except Exception:
            if not existed:
                shutil.rmtree(root, ignore_errors=True)
            raise
        with transaction() as conn:
            conn.execute("INSERT INTO apt_repositories (distribution, suite, version, created_at) VALUES (?,?,?,?)",
                         (distribution, suite, version, datetime.datetime.now().isoformat(timespec="seconds")))
    return key


def add_deb(tmp_path, repository):
    root = require_repository(repository)
    fields = deb_fields(tmp_path)
    version = fields["Version"].split(":", 1)[-1]
    name = secure_filename(f"{fields['Package']}_{version}_{fields['Architecture']}.deb")
    with _apt_lock:
        target = os.path.join(root, "pool", name)
        if os.path.isfile(target):
            if sha256_of(target) != sha256_of(tmp_path):
                raise ValueError(_('Ce paquet et cette version existent avec un contenu différent. Publiez une nouvelle version du paquet.'))
            os.unlink(tmp_path)
            return name
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, target)
        try:
            rebuild_apt(repository)
        except Exception:
            os.unlink(target)
            raise
    return name


@app.route("/linux")
@login_required
def linux():
    repos = repositories()
    selected = request.args.get("repository", repos[0]["key"] if repos else LEGACY)
    try:
        require_repository(selected)
    except ValueError:
        abort(404)
    return packages_page("apt", selected)


def packages_page(family, selected):
    """Page « Paquets Linux » commune : dépôts APT (Debian, Ubuntu) et RPM (famille Red Hat) dans une même barre ;
    le contenu (cache, paquets, commande de branchement, ajout) suit la famille du dépôt affiché."""
    from . import rpmrepo
    from .auth import can
    apt_repos, rpm_repos, legacy_count = [], [], 0
    if can("linux.view"):
        apt_repos = repositories()
        for r in apt_repos:
            r["count"] = len(list_debs(r["key"]))
        legacy_count = len(list_debs(LEGACY))
    if config.RPM_ENABLED and can("rpm.view"):
        rpm_repos = rpmrepo.repositories()
        for r in rpm_repos:
            r["count"] = len(rpmrepo.list_rpms(r["name"]))
    if family == "apt":
        current = next((r for r in apt_repos if r["key"] == selected), None)
        label = current["label"] if current else "Dépôt historique"
        packages = [{"name": d["package"], "version": d["version"], "arch": d["arch"], "size": d["size"],
                     "desc": d["description"], "mtime": d["mtime"], "url": d["url"], "file": d["file"]}
                    for d in list_debs(selected)]
        devices = _devices_on(current)
    else:
        current = next((r for r in rpm_repos if r["name"] == selected), None)
        label = f"Red Hat {current['major']} ({current['name']})" if current else "aucun dépôt"
        packages = [{"name": p["name"], "version": (p["epoch"] + ":" if p["epoch"] != "0" else "") + f"{p['version']}-{p['release']}",
                     "arch": p["arch"], "size": p["size"], "desc": p["summary"], "mtime": p["mtime"], "url": p["url"],
                     "file": p["file"]} for p in (rpmrepo.list_rpms(selected) if current else [])]
        devices = rpmrepo._devices_on(current)
    for p in packages:
        p["added"] = datetime.datetime.fromtimestamp(p["mtime"]).strftime("%Y-%m-%d %H:%M") if p["mtime"] else ""
    linux_devices = None
    if _fleet_visible():
        with transaction() as conn:
            linux_devices = conn.execute("SELECT COUNT(*) FROM devices WHERE state='active' AND os_family='linux'").fetchone()[0]
    own = sum(r["count"] for r in apt_repos) + legacy_count + sum(r["count"] for r in rpm_repos)
    from . import caches
    return render_template("packages.html", family=family, selected=selected, current=current, label=label,
                           apt_repos=apt_repos, rpm_repos=rpm_repos, legacy=LEGACY, legacy_count=legacy_count,
                           distributions=config.APT_DISTRIBUTIONS, packages=packages, devices=devices,
                           linux_devices=linux_devices, own_packages=own, cache_manage=caches.available(),
                           stats=read_stats(), acng=service_active("apt-cacher-ng") if family == "apt" else None,
                           upstreams=config.RPM_UPSTREAMS)


def _fleet_visible():
    from . import features
    from .auth import can
    return features.enabled("fleet") and can("fleet.view")


def _devices_on(repo):
    """Postes Linux du parc sur cette distribution et version (d'après le système annoncé par l'agent)."""
    if not repo or not _fleet_visible():
        return None
    with transaction() as conn:
        names = [r["os_name"].lower() for r in conn.execute(
            "SELECT os_name FROM devices WHERE state='active' AND os_family='linux'")]
    suite, version, dist = repo["suite"].lower(), (repo["version"] or "").lower(), repo["distribution"].lower()
    return sum(1 for n in names if f"({suite})" in n or (dist in n and version and f" {version}" in n))


@app.route("/linux/repositories", methods=["POST"])
@login_required
def linux_repository_create():
    try:
        key = create_repository(request.form.get("distribution", ""), request.form.get("suite", "").strip(),
                                request.form.get("version", "").strip())
    except ValueError as e:
        flash(str(e), "error")
        return back("linux")
    except subprocess.SubprocessError as e:
        flash(explain(e), "error")
        return back("linux")
    audit("linux.depot.ajout", key)
    flash(_('Dépôt créé : %(key)s', key=key), "ok")
    return back("linux", repository=key)


@app.route("/linux/upload", methods=["POST"])
@login_required
def linux_upload():
    repository = request.form.get("repository", "")
    try:
        require_repository(repository)
    except ValueError as e:
        abort(400, str(e))
    os.makedirs(config.TMP_DIR, exist_ok=True)
    added, errors = [], []
    url = request.form.get("url", "").strip()
    try:
        if url:
            tmp, _sha = download(url, config.TMP_DIR)
            try:
                added.append(add_deb(tmp, repository))
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        for f in request.files.getlist("files"):
            if not f.filename:
                continue
            fd, tmp = tempfile.mkstemp(dir=config.TMP_DIR, prefix=".up-")
            os.close(fd)
            try:
                f.save(tmp)
                added.append(add_deb(tmp, repository))
            except (ValueError, subprocess.SubprocessError) as e:
                errors.append(f"{f.filename} : {e}")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception as e:
        errors.append(explain(e))
    for name in added:
        audit("linux.ajout", f"{repository}: {name}")
        flash(_('Paquet ajouté : %(name)s', name=name), "ok")
    for err in errors:
        flash(err, "error")
    if not added and not errors:
        flash(_('Aucun fichier ni lien fourni.'), "error")
    return back("linux", repository=repository)


@app.route("/linux/delete", methods=["POST"])
@login_required
def linux_delete():
    repository = request.form.get("repository", "")
    try:
        root = require_repository(repository)
    except ValueError as e:
        abort(400, str(e))
    name = secure_filename(request.form.get("file", ""))
    with _apt_lock:
        path = os.path.join(root, "pool", name)
        if name.endswith(".deb") and os.path.isfile(path):
            with tempfile.TemporaryDirectory(prefix=".removed-", dir=root) as stage:
                saved = os.path.join(stage, name)
                os.replace(path, saved)
                try:
                    rebuild_apt(repository)
                except Exception:
                    os.replace(saved, path)
                    raise
            audit("linux.suppression", f"{repository}: {name}")
            flash(_('Paquet supprimé de ce dépôt.'), "ok")
    return back("linux", repository=repository)


@app.route("/linux/copy", methods=["POST"])
@login_required
def linux_copy():
    source, destination = request.form.get("repository", ""), request.form.get("destination", "")
    name = request.form.get("file", "")
    try:
        root = require_repository(source)
    except ValueError as e:
        abort(400, str(e))
    try:
        require_repository(destination)
        if source == destination or name != secure_filename(name) or not name.endswith(".deb"):
            raise ValueError(_('Source, destination ou fichier invalide.'))
        with _apt_lock:
            path = os.path.join(root, "pool", name)
            if not os.path.isfile(path):
                abort(404)
            os.makedirs(config.TMP_DIR, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=config.TMP_DIR) as stage:
                tmp = os.path.join(stage, name)
                shutil.copyfile(path, tmp)
                add_deb(tmp, destination)
    except ValueError as e:
        flash(str(e), "error")
        return back("linux", repository=source)
    except subprocess.SubprocessError as e:
        flash(explain(e), "error")
        return back("linux", repository=source)
    audit("linux.copie", f"{source} → {destination}: {name}")
    flash(_("Paquet copié. Le dépôt d'origine est conservé."), "ok")
    return back("linux", repository=destination)
