"""Points d'entrée des postes clients (publics) : script Linux et scripts d'installation Windows."""

import base64
import os
import urllib.parse

from flask import Response, abort, render_template, request

from . import app, config
from .db import db


def client_base():
    """Adresse par laquelle le poste a joint le serveur (HTTP ou HTTPS) : les liens du script la reprennent."""
    return request.host_url.rstrip("/")


@app.route("/linux/setup.sh")
def linux_setup():
    script = render_template("setup.sh", base=client_base(), https_url=config.HTTPS_URL,
                             host=config.HOST, acng_port=config.ACNG_PORT)
    return Response(script, mimetype="text/x-shellscript")


@app.route("/linux/proxmox.sh")
def linux_proxmox():
    script = render_template("proxmox.sh", base=client_base(), https_url=config.HTTPS_URL,
                             host=config.HOST, acng_port=config.ACNG_PORT)
    return Response(script, mimetype="text/x-shellscript")


@app.route("/linux/repositories/<distribution>/<suite>.sources")
def linux_repository_source(distribution, suite):
    from .linux import require_repository, repository_url
    repository = f"{distribution}/{suite}"
    try:
        root = require_repository(repository)
    except ValueError:
        abort(404)
    if not os.path.isfile(os.path.join(root, "InRelease")):
        abort(503)
    source = (f"Types: deb\nURIs: {client_base()}{repository_url(repository)}\nSuites: ./\n"
              "Signed-By: /etc/apt/keyrings/loco.gpg\n")
    return Response(source, mimetype="text/plain")


@app.route("/win/i/<slugs>")
def windows_install_script(slugs):
    """Script PowerShell : irm 'https://hôte:port/win/i/app1,app2' | iex"""
    wanted = [s for s in slugs.lower().split(",") if s]
    if not wanted or len(wanted) > 100 or not all(config.SLUG_RE.match(s) for s in wanted):
        abort(404)
    marks = ",".join("?" * len(wanted))
    rows = {r["slug"]: r for r in db().execute(
        f"SELECT * FROM win_apps WHERE status='ready' AND slug IN ({marks})", wanted)}
    missing = [s for s in wanted if s not in rows]
    apps = [rows[s] for s in wanted if s in rows]
    script = render_template("install.ps1", apps=apps, missing=missing, base=client_base(),
                             q=lambda s: "'" + (s or "").replace("'", "''") + "'",
                             quote=urllib.parse.quote)
    return Response(script.replace("\n", "\r\n"), mimetype="text/plain")


@app.route("/flatpak/setup.sh")
def flatpak_setup():
    if not config.FLATPAK_ENABLED:
        abort(404)
    script = render_template("flatpak-setup.sh", base=client_base(), flathub_url=config.FLATHUB_URL,
                             local=config.FLATPAK_LOCAL)
    return Response(script, mimetype="text/x-shellscript")


@app.route("/flatpak/loco.flatpakrepo")
@app.route("/flatpak/local-repo.flatpakrepo")
def flatpak_repo_file():
    """Fichier .flatpakrepo du dépôt local : adresse (selon le protocole utilisé) et clé publique GPG."""
    if not config.FLATPAK_LOCAL:
        abort(404)
    try:
        with open(os.path.join(config.APT_DIR, "loco.gpg"), "rb") as f:
            key = base64.b64encode(f.read()).decode()
    except OSError:
        abort(503)
    body = ("[Flatpak Repo]\n"
            f"Title=loco ({config.HOST})\n"
            f"Url={client_base()}/flatpak/repo/\n"
            f"Homepage={config.HTTPS_URL}/\n"
            "Comment=Dépôt Flatpak local de l'entreprise\n"
            f"GPGKey={key}\n")
    return Response(body, mimetype="application/vnd.flatpak.repo")


@app.route("/rpm/setup.sh")
def rpm_setup():
    if not config.RPM_ENABLED:
        abort(404)
    script = render_template("rpm-setup.sh", base=client_base(), https_url=config.HTTPS_URL)
    return Response(script, mimetype="text/x-shellscript")
