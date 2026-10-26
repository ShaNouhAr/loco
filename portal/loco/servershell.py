"""Shell root du serveur loco dans le navigateur (Système).

Désactivé par défaut ; activé, il ne devient disponible qu'après un redémarrage du serveur. Réservé au compte
« admin » de l'installation. Le terminal (remote.js) parle au broker de l'agent root par un socket Unix.
"""
import secrets
import time

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import app, config
from .i18n import _
from .auth import audit, login_required
from .features import is_super_admin
from .util import back

if config.SERVER_UPDATES and not config.WINDOWS:
    import locoadmin
else:
    locoadmin = None

MAX_SESSIONS = 4


def available():
    return locoadmin is not None


def status():
    return locoadmin.shell_status() if available() else "off"


def _guard():
    if not available():
        abort(404)
    if not is_super_admin():
        abort(403, _("Réservé au compte « admin » de l'installation."))


def _owned(sid):
    if sid not in session.get("server_shells", []):
        abort(404)


def _request(enabled):
    if locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
        return False
    if not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
        return False
    locoadmin.request("set-shell", {"enabled": "1" if enabled else ""}, user=g.user["username"])
    return True


@app.context_processor
def _shell_globals():
    """État pour la navigation (menu Administration, icône de la barre) : seulement pour le compte admin."""
    try:
        state = status() if available() and g.get("user") is not None and is_super_admin() else None
    except OSError:
        state = None
    return {"server_shell": state}


@app.route("/system/shell")
@login_required
def system_shell_home():
    """Shell actif : le terminal directement. Sinon : la mise en service (activer, redémarrer)."""
    _guard()
    state, host = status(), request.host.split(":")[0]
    if state != "active":
        session.pop("shell_disabling", None)
    elif time.time() - session.get("shell_disabling", 0) < 30:
        state = "disabling"                   # désactivation demandée, pas encore appliquée : ne pas rouvrir de shell
    if state != "active":
        return render_template("server_shell_home.html", state=state, host=host)
    # Rien ne démarre ici (simple affichage) : le shell s'ouvre au premier échange du terminal, protégé par le CSRF.
    sid = secrets.token_hex(16)
    session["server_shells"] = (session.get("server_shells", []) + [sid])[-MAX_SESSIONS:]
    return render_template("server_shell.html", sid=sid, host=host)


@app.route("/system/shell/enable", methods=["POST"])
@login_required
def system_shell_enable():
    _guard()
    if not check_password_hash(g.user["password_hash"], request.form.get("password", "")):
        audit("systeme.shell.refus", "mot de passe incorrect")
        flash(_("Mot de passe incorrect : shell root non activé."), "error")
        return back("system_shell_home")
    if _request(True):
        audit("systeme.shell.activation", "effective au prochain redémarrage")
        flash(_("Shell root activé : redémarrez le serveur pour le rendre disponible."), "ok")
    return back("system_shell_home")


@app.route("/system/shell/disable", methods=["POST"])
@login_required
def system_shell_disable():
    _guard()
    if _request(False):
        audit("systeme.shell.desactivation", "")
        for _i in range(24):                  # l'agent root applique la demande en 2 à 3 s : l'attendre, sans plus
            if status() != "active":
                break
            time.sleep(0.25)
        if status() == "active":
            session["shell_disabling"] = time.time()
        flash(_("Shell root désactivé. Les shells ouverts sont fermés."), "ok")
    return back("system_shell_home")


@app.route("/system/shell/open", methods=["POST"])
@app.route("/system/shell/<sid>")
@login_required
def system_shell_open(sid=None):
    """Anciennes adresses (avant 2.67) : la page Shell root ouvre désormais le terminal elle-même."""
    _guard()
    return redirect(url_for("system_shell_home"))


@app.route("/system/shell/<sid>/io", methods=["POST"])
@login_required
def system_shell_io(sid):
    _guard()
    _owned(sid)
    if (request.content_length or 0) > 400000:
        abort(413)
    if request.form.get("close") == "1":
        req = {"op": "close", "sid": sid}
        session["server_shells"] = [s for s in session.get("server_shells", []) if s != sid]
        audit("systeme.shell.fermeture", sid)
    else:
        # Tant qu'aucune sortie n'est reçue (ack 0), le broker peut démarrer le shell de cette session.
        req = {"op": "io", "sid": sid, "seq": request.form.get("seq", 0), "data": request.form.get("data", ""),
               "ack": request.form.get("ack", 0), "cols": request.form.get("cols", 100), "rows": request.form.get("rows", 30),
               "create": str(request.form.get("ack", "0")) == "0"}
    try:
        reply = locoadmin.shell_call(req)
    except (OSError, ValueError):
        reply = {"state": "closed", "error": _("Broker du shell injoignable (service loco-agent)."), "packets": [], "ack": 0}
    if reply.get("created"):
        audit("systeme.shell.ouverture", sid)
    return ({"state": reply.get("state", "closed"), "error": reply.get("error", ""), "packets": reply.get("packets", []),
             "ack": reply.get("ack", 0)}, 200, {"Cache-Control": "no-store"})
