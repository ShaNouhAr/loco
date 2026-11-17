"""Installateurs MCP pour Claude Code et Codex : un fichier à ouvrir sur l'ordinateur, qui écrit la configuration MCP
de l'application (loco, avec un jeton créé pour lui) et, au besoin, fait reconnaître le certificat de loco.

Ni Claude ni Codex n'acceptent qu'un lien ajoute un serveur MCP : l'installateur écrit donc lui-même les fichiers que
ces applications lisent — `~/.claude.json` (Claude Code : commande `claude` et onglet Code de l'application de bureau)
et `~/.codex/config.toml` (Codex : application de bureau et commande `codex`). Le jeton est créé au téléchargement, avec
les outils et la durée choisis dans loco ; il ne passe ni par une conversation ni par une commande, et l'installateur
s'efface après usage.
"""
import datetime
import hashlib
import json
import re
import secrets

from flask import abort, current_app, g, render_template, request

from . import app, config, mcp
from .auth import audit, login_required, require
from .db import db
from .i18n import _
from .util import counted

APPS = {"claude": "Claude", "codex": "Codex"}
SYSTEMS = ("windows", "macos", "linux")
CLIENT_PREFIX = "installateur:"
URL_RE = re.compile(r"^https://[A-Za-z0-9.\[\]:-]+/mcp$")
PEM_RE = re.compile(r"^-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE-----\n?$")
FILES = {"windows": "ajouter-loco-a-{app}.cmd", "macos": "ajouter-loco-a-{app}.command", "linux": "ajouter-loco-a-{app}.sh"}


def detect_system():
    """Système du navigateur, d'après son User-Agent : l'installateur proposé d'office."""
    agent = request.headers.get("User-Agent", "")
    if "Windows" in agent:
        return "windows"
    if "Macintosh" in agent or "Mac OS X" in agent:
        return "macos"
    return "linux" if "Linux" in agent or "X11" in agent else "windows"


def app_label(client_id):
    """Libellé de l'origine d'un jeton créé par un installateur (« installateur:claude »), ou None."""
    if client_id.startswith(CLIENT_PREFIX) and client_id[len(CLIENT_PREFIX):] in APPS:
        return _("l'installateur %(app)s", app=APPS[client_id[len(CLIENT_PREFIX):]])
    return None


def ps(value):
    """Chaîne PowerShell entre apostrophes (rien n'y est interprété)."""
    return "'" + str(value).replace("'", "''") + "'"


def sh(value):
    """Chaîne shell entre apostrophes (rien n'y est interprété)."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _authority_pem():
    """(PEM, empreinte) de l'autorité de loco quand c'est elle qui a signé le certificat du portail ; sinon ("", "")."""
    found = mcp._authority()
    if not found:
        return "", ""
    import os
    try:
        with open(os.path.join(config.TLS_DIR, "ca.crt"), encoding="ascii") as f:
            text = f.read()
    except (OSError, ValueError):
        return "", ""
    block = re.search(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----", text)
    if not block:
        return "", ""
    pem = "\n".join(line.strip() for line in block.group(0).splitlines() if line.strip()) + "\n"
    return (pem, found[1]) if PEM_RE.match(pem) else ("", "")


def messages(label):
    """Textes de l'installateur, dans la langue de la page."""
    return {
        "title": _("Ajouter loco à %(app)s", app=label),
        "check": _("Vérification de la connexion à loco…"),
        "trust": _("Cet ordinateur ne reconnaît pas encore le certificat de loco. Son autorité a l'empreinte SHA-256 suivante :"),
        "ask": _("Faire confiance à l'autorité de loco sur cet ordinateur ? [O/n]"),
        "trusted": _("Autorité de loco ajoutée aux certificats de confiance."),
        "skipped": _("Autorité non ajoutée : l'application refusera la connexion tant que le certificat de loco ne sera pas reconnu."),
        "down": _("loco ne répond pas depuis cet ordinateur pour l'instant : la configuration est écrite quand même."),
        "backup": _("Copie de l'ancien fichier :"),
        "done_claude": _("loco est ajouté à Claude Code : application de bureau (onglet Code) et commande claude. Redémarrez Claude pour le voir."),
        "done_codex": _("loco est ajouté à Codex : application de bureau et commande codex. Redémarrez Codex pour le voir."),
        "failed": _("L'installation a échoué :"),
        "linux_ca": _("Pour Codex sous Linux, l'autorité doit aussi être reconnue par le système (droits administrateur demandés)."),
    }


def render_installer(system, target, endpoint, token, pem, fingerprint):
    """Texte de l'installateur pour un système et une application."""
    if system not in SYSTEMS or target not in APPS or not URL_RE.match(endpoint) or not mcp.TOKEN_RE.match(token):
        raise ValueError("installateur : paramètres invalides")
    if pem and not PEM_RE.match(pem):
        raise ValueError("installateur : autorité invalide")
    values = {"app": target, "label": APPS[target], "url": endpoint, "token": token, "pem": pem, "fingerprint": fingerprint,
              "m": messages(APPS[target]), "ps": ps, "sh": sh, "version": config.VERSION}
    if system == "windows":
        text = render_template("installers/windows.cmd.j2", **values)
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return render_template("installers/unix.sh.j2", **values, macos=system == "macos")


@app.route("/accounts/mcp/install", methods=["POST"])
@login_required
def mcp_installer():
    """Crée le jeton choisi et renvoie l'installateur qui le pose dans la configuration de l'application."""
    require("mcp.manage")
    target, system = request.form.get("app", ""), request.form.get("system", "")
    days = mcp.EXPIRIES.get(request.form.get("expiry", "90"))
    if target not in APPS or system not in SYSTEMS or days is None:
        abort(400)
    try:
        name, chosen = mcp._form_values()
    except ValueError as e:
        abort(400, str(e))
    secret = mcp.PREFIX + secrets.token_urlsafe(32)
    expires = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else ""
    pem, fingerprint = _authority_pem()
    body = render_installer(system, target, mcp._endpoint_url(), secret, pem, fingerprint)
    conn = db()
    conn.execute("INSERT INTO mcp_tokens (user_id, name, token_hash, hint, tools, created_at, expires_at, client_id) "
                 "VALUES (?,?,?,?,?,?,?,?)", (g.user["id"], name, hashlib.sha256(secret.encode()).hexdigest(),
                                              secret[:len(mcp.PREFIX) + 4], json.dumps(chosen), mcp._now(), expires,
                                              CLIENT_PREFIX + target))
    audit("mcp.acces.ajout", f"{name} : {counted(len(chosen), 'outil')} (installateur {APPS[target]}, {system})"
          + (f", expire le {expires[:10]}" if expires else ""), commit=False)
    conn.commit()
    response = current_app.response_class(body.encode("utf-8"), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f'attachment; filename="{FILES[system].format(app=target)}"'
    response.headers["Cache-Control"] = "no-store"
    return response
