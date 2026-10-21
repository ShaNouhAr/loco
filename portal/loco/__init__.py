"""loco : portail du dépôt local (paquets Linux, Windows Update, logiciels Windows)."""

import datetime
import hmac
import json
import logging
import os
import secrets
import sys

from flask import Flask, abort, g, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config
from .i18n import _

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_secrets():
    try:
        with open(config.SECRETS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


SECRETS = _load_secrets()

app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"), static_folder=os.path.join(ROOT, "static"))
# nginx est le seul client de gunicorn : on lui fait confiance pour l'adresse du poste et le protocole.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.secret_key = SECRETS.get("secret_key") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_NAME="loco_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("LOCO_SECURE_COOKIE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=config.SESSION_IDLE_HOURS),  # inactivité
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_CONTENT_LENGTH=None,
)

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(message)s")
app.logger.setLevel(logging.INFO)

STATIC_FILES = ("style.css", "app.js", "theme.js", "remote.js", "remote-sizing.js", "permissions.js", "files.js", "logo.svg")
ASSET_V = str(int(max(os.path.getmtime(os.path.join(app.static_folder, f)) for f in STATIC_FILES)))

# Politique de contenu stricte : aucun script ni style en ligne.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")

# Points d'entrée publics (postes clients, supervision) : ni session, ni jeton CSRF.
PUBLIC_ENDPOINTS = {"linux_setup", "linux_repository_source", "windows_install_script", "flatpak_setup", "flatpak_repo_file", "rpm_setup",
                    "healthz", "metrics", "static",
                    # Agent des postes (loco-client) : authentifié par sa clé d'inscription puis son jeton.
                    "agent_enroll", "agent_checkin", "agent_result", "agent_linux_installer", "agent_windows_installer",
                    "agent_file", "agent_deb", "agent_rpm", "remote_agent"}


@app.before_request
def csrf_protect():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        token = request.form.get("csrf", "")
        if not hmac.compare_digest(token, session["csrf"]):
            abort(400, _('Jeton CSRF invalide, rechargez la page.'))


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.endpoint not in ("static",):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.context_processor
def inject_globals():
    user = g.get("user")
    return {
        "csrf": session.get("csrf", ""),
        "https_url": config.HTTPS_URL,
        "http_url": config.HTTP_URL,
        "client_url": config.HTTPS_URL,
        "host": config.HOST,
        "acng_port": config.ACNG_PORT,
        "asset_v": ASSET_V,
        "version": config.VERSION,
        "app_states": {"ready": _("Prêt"), "pending": _("En attente"), "downloading": _("Téléchargement en cours"), "error": _("Erreur")},
        "current_user": user,
        "can": auth.can,
        "can_any": auth.can_any,
        "roles": auth.permissions.role_labels(_db.db()) if user else {},
        "flatpak_enabled": config.FLATPAK_ENABLED,
        "rpm_enabled": config.RPM_ENABLED,
        "container": config.CONTAINER,
        "windows": config.WINDOWS,
        "alpine": config.ALPINE,
        "apt_nginx": config.APT_NGINX,
        "appliance": config.APPLIANCE,
        "server_updates": config.SERVER_UPDATES,
        "lang": i18n.locale(),
        "langs": i18n.LANGS,
        "js_i18n": i18n.js_catalog(),
    }


from . import util, i18n  # noqa: E402

app.jinja_env.filters["human"] = util.human
app.jinja_env.filters["date_fr"] = util.date_fr
# Traductions : {{ _("Texte") }}, {{ _("%(n)s postes", n=x) }}, {{ ngettext("…", "…", n) }} (valeurs échappées).
app.jinja_env.add_extension("jinja2.ext.i18n")
app.jinja_env.install_gettext_callables(i18n.gettext, i18n.ngettext, newstyle=True)
# Valeur enregistrée (libellé de profil intégré…) : {{ valeur|tr }} traduit sans mise en forme.
app.jinja_env.filters["tr"] = i18n.gettext

from . import db as _db  # noqa: E402

app.teardown_appcontext(_db.close_db)

# Les modules déclarent leurs routes sur `app` (structure « grande application » de Flask).
from . import auth, dashboard, linux, wupdate, winapps, flatpak, rpmrepo, clients, system, appliance, fleet, winget, remote, directories, groups, deployments, features, scripts, enrollment, schedules  # noqa: E402

ROUTE_MODULES = (auth, dashboard, linux, wupdate, winapps, flatpak, rpmrepo, clients, system, appliance, fleet, winget, remote,
                 directories, groups, deployments, features, scripts, enrollment, schedules)


def startup():
    """Migrations, reprise du compte historique, nettoyage des fichiers temporaires."""
    _db.migrate()
    with _db.transaction() as conn:
        auth.bootstrap_admin(conn, SECRETS)
        auth.purge_audit(conn)      # rotation du journal (aussi au plus une fois par heure ensuite)
    util.cleanup_temp_files()
