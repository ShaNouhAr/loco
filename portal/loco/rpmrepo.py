"""Famille Red Hat : dépôts RPM locaux signés (el8, el9…) et cache des miroirs Rocky Linux, AlmaLinux, EPEL."""

import datetime
import gzip
import os
import re
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET

from flask import abort, flash, request
from werkzeug.utils import secure_filename

from . import app, config, rpmpkg, signing
from .i18n import _
from .auth import audit, login_required
from .db import transaction
from .util import back, download, explain, sha256_of

_lock = threading.RLock()
# Sans rpm ni createrepo_c (serveur Windows), lecture des paquets et métadonnées en Python (rpmpkg).
RPM_TOOLS = shutil.which("createrepo_c") is not None and shutil.which("rpm") is not None
_NS = {"repo": "http://linux.duke.edu/metadata/repo", "common": "http://linux.duke.edu/metadata/common"}


def repository_path(name):
    if not config.RPM_REPOSITORY_RE.fullmatch(name or ""):
        raise ValueError(_('Dépôt invalide : el suivi de la version majeure (el9, el10…).'))
    return os.path.join(config.RPM_DIR, name)


def repositories():
    with transaction() as conn:
        rows = conn.execute("SELECT * FROM rpm_repositories").fetchall()
    names = sorted((r["name"] for r in rows), key=lambda n: int(n[2:]))
    return [{"name": n, "major": n[2:], "label": f"Famille Red Hat {n[2:]} ({n})"} for n in names]


def require_repository(name):
    path = repository_path(name)
    if not any(r["name"] == name for r in repositories()):
        raise ValueError(_("Ce dépôt n'existe pas. Créez-le avant d'y ajouter des paquets."))
    return path


def list_rpms(name=None):
    """Paquets lus dans les métadonnées publiées (primary.xml) ; sans nom : tous les dépôts."""
    names = [r["name"] for r in repositories()] if name is None else [name]
    out = []
    for repo in names:
        root = repository_path(repo)
        try:
            index = ET.parse(os.path.join(root, "repodata", "repomd.xml"))
        except (OSError, ET.ParseError):
            continue
        primary = index.find("repo:data[@type='primary']/repo:location", _NS)
        if primary is None:
            continue
        try:
            with gzip.open(os.path.join(root, primary.get("href"))) as f:
                tree = ET.parse(f)
        except (OSError, ET.ParseError):
            continue
        for pkg in tree.getroot().findall("common:package", _NS):
            ver = pkg.find("common:version", _NS)
            location = pkg.find("common:location", _NS).get("href")
            out.append({
                "repository": repo,
                "name": pkg.findtext("common:name", "", _NS),
                "summary": pkg.findtext("common:summary", "", _NS)[:160],
                "version": ver.get("ver"), "release": ver.get("rel"), "epoch": ver.get("epoch", "0"),
                "arch": pkg.findtext("common:arch", "", _NS),
                "size": int(pkg.find("common:size", _NS).get("package", "0")),
                "file": os.path.basename(location),
                "mtime": _mtime(os.path.join(root, location)),
                "url": f"/rpm/{repo}/{location}",
            })
    return sorted(out, key=lambda p: (p["repository"], p["name"], p["version"], p["release"]))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def rpm_fields(path):
    """Champs d'un paquet binaire ; les signatures éventuelles ne sont pas vérifiées ici (clés tierces)."""
    if not RPM_TOOLS:
        return rpmpkg.rpm_fields(path)
    out = subprocess.run(["rpm", "-qp", "--nosignature", "--nodigest", "--qf",
                          "%{NAME}\\n%{VERSION}\\n%{RELEASE}\\n%{ARCH}\\n%{SOURCERPM}\\n", path],
                         capture_output=True, text=True, timeout=30)
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) < 5:
        raise ValueError(_("ce fichier n'est pas un paquet .rpm valide"))
    name, version, release, arch, source = lines[:5]
    if source == "(none)" or arch in ("src", "nosrc"):
        raise ValueError(_('les paquets source (.src.rpm) ne sont pas publiés'))
    return {"name": name, "version": version, "release": release, "arch": arch}


def rebuild(name):
    """Régénère et signe les métadonnées hors ligne, puis les publie d'un coup."""
    with _lock:
        root = repository_path(name)
        os.makedirs(os.path.join(root, "Packages"), exist_ok=True)
        env = dict(os.environ, GNUPGHOME=config.GNUPGHOME)
        # Dossiers de travail hors du dépôt : createrepo_c indexe tout .rpm trouvé sous la racine.
        os.makedirs(config.TMP_DIR, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rpm-metadata-", dir=config.TMP_DIR) as stage:
            if RPM_TOOLS:
                subprocess.run(["createrepo_c", "--quiet", "--no-database", "--general-compress-type=gz",
                                "--checksum=sha256", "--unique-md-filenames", "--outputdir", stage, root],
                               check=True, capture_output=True, timeout=600)
            else:
                rpmpkg.createrepo(root, stage)
            new = os.path.join(stage, "repodata")
            if config.WINDOWS:
                signing.detach_sign_armored(os.path.join(new, "repomd.xml"), os.path.join(new, "repomd.xml.asc"))
            else:
                subprocess.run([config.GPG, "--batch", "--yes", "--armor", "--detach-sign", "-o", "repomd.xml.asc",
                                "repomd.xml"], cwd=new, env=env, check=True, capture_output=True, timeout=60)
            for entry in os.listdir(new):
                os.chmod(os.path.join(new, entry), 0o644)
            os.chmod(new, 0o755)
            current = os.path.join(root, "repodata")
            old = os.path.join(stage, "previous")
            if os.path.isdir(current):
                os.replace(current, old)
            os.replace(new, current)


def create_repository(name):
    root = repository_path(name)
    with _lock:
        if any(r["name"] == name for r in repositories()):
            raise ValueError(_('Ce dépôt existe déjà.'))
        existed = os.path.exists(root)
        try:
            rebuild(name)
        except Exception:
            if not existed:
                shutil.rmtree(root, ignore_errors=True)
            raise
        with transaction() as conn:
            conn.execute("INSERT INTO rpm_repositories (name, created_at) VALUES (?, ?)",
                         (name, datetime.datetime.now().isoformat(timespec="seconds")))
    return name


def add_rpm(tmp_path, name):
    root = require_repository(name)
    fields = rpm_fields(tmp_path)
    # Un paquet marqué pour une autre version (.el8 dans el9…) ne doit pas atterrir dans ce dépôt.
    tag = re.search(r"\.el(\d+)", fields["release"])
    if tag and f"el{tag.group(1)}" != name:
        raise ValueError(_('paquet construit pour el%(group)s, pas pour %(name)s', group=tag.group(1), name=name))
    filename = secure_filename(f"{fields['name']}-{fields['version']}-{fields['release']}.{fields['arch']}.rpm")
    with _lock:
        target = os.path.join(root, "Packages", filename)
        if os.path.isfile(target):
            if sha256_of(target) != sha256_of(tmp_path):
                raise ValueError(_('Ce paquet et cette version existent avec un contenu différent. Publiez une nouvelle version.'))
            os.unlink(tmp_path)
            return filename
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, target)
        try:
            rebuild(name)
        except Exception:
            os.unlink(target)
            raise
    return filename


@app.route("/rpm")
@login_required
def rpm():
    if not config.RPM_ENABLED:
        abort(404)
    repos = repositories()
    selected = request.args.get("repository", repos[-1]["name"] if repos else "")
    if repos and not any(r["name"] == selected for r in repos):
        abort(404)
    from .linux import packages_page
    return packages_page("rpm", selected)


RED_HAT_FAMILY = ("rocky", "almalinux", "red hat", "rhel", "centos", "oracle linux")


def _devices_on(repo):
    """Postes du parc de la famille Red Hat sur cette version majeure (d'après le système annoncé par l'agent)."""
    from . import features
    from .auth import can
    if not repo or not features.enabled("fleet") or not can("fleet.view"):
        return None
    with transaction() as conn:
        names = [r["os_name"].lower() for r in conn.execute(
            "SELECT os_name FROM devices WHERE state='active' AND os_family='linux'")]
    major = re.compile(rf"(?<![\d.]){re.escape(repo['major'])}(\.\d+)*(?![\d.])")
    return sum(1 for n in names if any(f in n for f in RED_HAT_FAMILY) and major.search(n))


@app.route("/rpm/repositories", methods=["POST"])
@login_required
def rpm_repository_create():
    if not config.RPM_ENABLED:
        abort(404)
    major = request.form.get("major", "").strip()
    try:
        name = create_repository(f"el{major}")
    except ValueError as e:
        flash(str(e), "error")
        return back("rpm")
    except subprocess.SubprocessError as e:
        flash(explain(e), "error")
        return back("rpm")
    audit("rpm.depot.ajout", name)
    flash(_('Dépôt créé : %(name)s', name=name), "ok")
    return back("rpm", repository=name)


@app.route("/rpm/upload", methods=["POST"])
@login_required
def rpm_upload():
    if not config.RPM_ENABLED:
        abort(404)
    name = request.form.get("repository", "")
    try:
        require_repository(name)
    except ValueError as e:
        abort(400, str(e))
    os.makedirs(config.TMP_DIR, exist_ok=True)
    added, errors = [], []
    url = request.form.get("url", "").strip()
    try:
        if url:
            tmp, _sha = download(url, config.TMP_DIR)
            try:
                added.append(add_rpm(tmp, name))
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
                added.append(add_rpm(tmp, name))
            except (ValueError, subprocess.SubprocessError) as e:
                errors.append(f"{f.filename} : {e}")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception as e:  # noqa: BLE001 - toute erreur est remontée à l'utilisateur
        errors.append(explain(e))
    for filename in added:
        audit("rpm.ajout", f"{name}: {filename}")
        flash(_('Paquet ajouté : %(filename)s', filename=filename), "ok")
    for err in errors:
        flash(err, "error")
    if not added and not errors:
        flash(_('Aucun fichier ni lien fourni.'), "error")
    return back("rpm", repository=name)


@app.route("/rpm/delete", methods=["POST"])
@login_required
def rpm_delete():
    if not config.RPM_ENABLED:
        abort(404)
    name = request.form.get("repository", "")
    try:
        root = require_repository(name)
    except ValueError as e:
        abort(400, str(e))
    filename = secure_filename(request.form.get("file", ""))
    with _lock:
        path = os.path.join(root, "Packages", filename)
        if filename.endswith(".rpm") and os.path.isfile(path):
            os.makedirs(config.TMP_DIR, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".rpm-removed-", dir=config.TMP_DIR) as stage:
                saved = os.path.join(stage, filename)
                os.replace(path, saved)
                try:
                    rebuild(name)
                except Exception:
                    os.replace(saved, path)
                    raise
            audit("rpm.suppression", f"{name}: {filename}")
            flash(_('Paquet supprimé de ce dépôt.'), "ok")
    return back("rpm", repository=name)
