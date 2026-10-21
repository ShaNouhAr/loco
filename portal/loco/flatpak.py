"""Flatpak : cache Flathub (nginx) et dépôt Flatpak local signé (OSTree)."""

import os
import re
import subprocess
import tempfile
import threading

from flask import abort, flash, render_template, request

from . import app, config
from .i18n import _
from .auth import audit, login_required
from .util import back, read_stats

_lock = threading.Lock()


def _run(args, timeout=600):
    env = dict(os.environ, GNUPGHOME=config.GNUPGHOME)
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1] if (out.stderr or out.stdout) else "erreur")
    return out.stdout


def signing_key():
    """Empreinte de la clé GPG du dépôt (partagée avec le dépôt APT)."""
    out = _run([config.GPG, "--batch", "--with-colons", "--list-secret-keys"], timeout=30)
    for line in out.splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    raise RuntimeError("clé de signature introuvable")


def repo_ready():
    return os.path.isfile(os.path.join(config.FLATPAK_REPO, "config"))


def list_refs():
    if not repo_ready():
        return []
    refs = []
    for ref in sorted(_run(["ostree", "refs", "--repo", config.FLATPAK_REPO], timeout=30).split()):
        if not config.FLATPAK_REF_RE.match(ref):
            continue
        kind, ident, arch, branch = ref.split("/")
        date = ""
        try:
            show = _run(["ostree", "show", "--repo", config.FLATPAK_REPO, ref], timeout=30)
            m = re.search(r"^Date:\s+(\S+ \S+)", show, re.M)
            date = m.group(1)[:16] if m else ""
        except RuntimeError:
            pass
        refs.append({"ref": ref, "kind": kind, "id": ident, "arch": arch, "branch": branch, "date": date})
    return refs


def update_repo(prune=False):
    args = ["flatpak", "build-update-repo", f"--gpg-sign={signing_key()}", f"--gpg-homedir={config.GNUPGHOME}"]
    if prune:
        args.append("--prune")
    _run(args + [config.FLATPAK_REPO])


def import_bundle(path):
    """Importe un paquet .flatpak dans le dépôt local et le signe ; renvoie les références ajoutées."""
    with _lock:
        before = {r["ref"] for r in list_refs()}
        _run(["flatpak", "build-import-bundle", "--no-update-summary", f"--gpg-sign={signing_key()}",
              f"--gpg-homedir={config.GNUPGHOME}", config.FLATPAK_REPO, path])
        update_repo()
        return sorted({r["ref"] for r in list_refs()} - before) or ["(version mise à jour)"]


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


@app.route("/flatpak")
@login_required
def flatpak():
    if not config.FLATPAK_ENABLED:
        abort(404)
    refs, error = [], None
    if config.FLATPAK_LOCAL:
        try:
            refs = list_refs()
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            error = str(e)
    return render_template("flatpak.html", refs=refs, error=error, stats=read_stats(),
                           repo_ready=config.FLATPAK_LOCAL and repo_ready(), local=config.FLATPAK_LOCAL)


@app.route("/flatpak/upload", methods=["POST"])
@login_required
def flatpak_upload():
    if not config.FLATPAK_LOCAL:
        abort(404)
    os.makedirs(config.TMP_DIR, exist_ok=True)
    done = 0
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        fd, tmp = tempfile.mkstemp(dir=config.TMP_DIR, prefix=".up-", suffix=".flatpak")
        os.close(fd)
        try:
            f.save(tmp)
            added = import_bundle(tmp)
            done += 1
            audit("flatpak.ajout", ", ".join(added))
            flash(_('Ajouté : ') + ", ".join(added), "ok")
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            flash(_('%(filename)s : import impossible (%(e)s)', filename=f.filename, e=e), "error")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    if not done and not request.files.getlist("files"):
        flash(_('Aucun fichier fourni.'), "error")
    return back("flatpak")


@app.route("/flatpak/delete", methods=["POST"])
@login_required
def flatpak_delete():
    if not config.FLATPAK_LOCAL:
        abort(404)
    ref = request.form.get("ref", "")
    if not config.FLATPAK_REF_RE.match(ref):
        abort(400)
    try:
        with _lock:
            _run(["ostree", "refs", "--repo", config.FLATPAK_REPO, "--delete", ref])
            update_repo(prune=True)
        audit("flatpak.suppression", ref)
        flash(_('Supprimé : %(ref)s', ref=ref), "ok")
    except (RuntimeError, subprocess.SubprocessError) as e:
        flash(_('Suppression impossible : %(e)s', e=e), "error")
    return back("flatpak")
