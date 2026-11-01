"""Haute disponibilité : deux serveurs loco, une IP virtuelle, bascule automatique.

Un seul serveur (le principal) porte l'IP virtuelle et sert le portail, les dépôts et les agents ; l'autre (le
secours) réplique sa configuration et ses dépôts et prend l'IP si le principal tombe (keepalived / VRRP).

Ce module est la face portail : la page d'administration, les actions (déposées comme demandes à l'agent root
locoadmin), les points d'accès de réplication que le pair appelle, et le garde qui met le secours en lecture.
Toutes les opérations privilégiées (keepalived, certificat, bascule) sont faites par locoadmin ; le portail ne
connaît jamais le secret de la grappe, seulement l'empreinte des jetons présentés par le pair.
"""
import hmac
import os
import time

from flask import Response, abort, g, jsonify, redirect, render_template, request, send_file, url_for

from . import app, config
from .auth import audit, login_required, require
from .i18n import _
from .util import back

if config.SERVER_UPDATES and not config.CONTAINER:
    import locoadmin
    import locoha
else:                                                   # Docker, Windows : pas d'agent root, pas de haute disponibilité
    locoadmin = locoha = None

# Points d'accès appelés par le pair (authentifiés par l'en-tête X-Loco-HA, jamais par une session).
API_ENDPOINTS = {"ha_join_api", "ha_state_api", "ha_bundle_api", "ha_db_api", "ha_manifest_api", "ha_file_api", "ha_leave_api"}
# Sur le secours, pages tolérées en plus du compte et des points d'accès HA (le reste redirige vers la page HA).
STANDBY_OK = {"ha_page", "ha_enable", "ha_join", "ha_failover", "ha_disable", "healthz", "metrics", "static"}
_ROLE_CACHE = {"at": 0.0, "role": ""}


def available():
    """Vrai si la haute disponibilité est possible sur ce serveur (agent root, hors conteneur Docker)."""
    return locoha is not None


def configured():
    return available() and bool(locoha.node().get("vip"))


def role():
    """Rôle de ce serveur (« primary », « standby » ou «  »), relu au plus une fois par seconde."""
    if not available():
        return ""
    now = time.time()
    if now - _ROLE_CACHE["at"] > 1:
        _ROLE_CACHE.update(at=now, role=locoha.node().get("role", ""))
    return _ROLE_CACHE["role"]


def is_standby():
    return role() == "standby"


def in_lxc():
    """Conteneur LXC : VRRP peut demander une configuration côté hôte (le noyau est celui de l'hôte)."""
    return available() and locoadmin.in_container()


# --------------------------------------------------------------------------- garde du secours

@app.before_request
def standby_guard():
    """Sur le secours, le portail est passif : seules la page HA, le compte et la réplication répondent."""
    if not is_standby():
        return
    endpoint = request.endpoint
    if endpoint is None or endpoint in STANDBY_OK or endpoint in API_ENDPOINTS:
        return
    from .permissions import ACCOUNT_ENDPOINTS
    if endpoint in ACCOUNT_ENDPOINTS:
        return
    # Les agents et le serveur MCP visent l'IP virtuelle (le principal) : ici, invite à réessayer.
    if endpoint.startswith(("agent_", "remote_agent", "mcp_endpoint")):
        return Response(_("Serveur de secours : adressez-vous à l'IP virtuelle."), status=503, headers={"Retry-After": "30"})
    if request.method != "GET":
        abort(503, _("Serveur de secours : les modifications se font sur le principal (IP virtuelle)."))
    return redirect(url_for("ha_page"))


def skip_background():
    """Les fils de fond (bail, alertes, sauvegardes hors-site) ne tournent que sur le principal."""
    return is_standby()


# --------------------------------------------------------------------------- page et actions

def _status():
    """État publié par l'agent (rôle, IP portée, pair, fraîcheur de la réplication, événements)."""
    return locoha.read_json(locoha.status_path(), {}) or {}


def _pairing_code():
    """Code d'appairage à usage unique si un secours est attendu (lisible par le groupe loco)."""
    try:
        with open(locoha.join_code_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _rel(ts):
    """« il y a… » à partir d'un horodatage epoch (flottant) ; «  » si absent."""
    if not ts:
        return ""
    secs = int(time.time() - float(ts))
    if secs < 0:
        secs = 0
    if secs < 60:
        return _("à l'instant")
    if secs < 3600:
        return _("il y a %(n)s min", n=secs // 60)
    if secs < 86400:
        return _("il y a %(n)s h", n=secs // 3600)
    return _("il y a %(n)s j", n=secs // 86400)


@app.route("/system/ha")
@login_required
def ha_page():
    require("system.view")
    if not available():
        return render_template("ha.html", available=False, node={}, status={}, code="", lxc=False,
                               running=None, agent=False, rel={})
    node = locoha.node()
    status = _status()
    peer = status.get("peer") or {}
    repl = status.get("replication") or {}
    running = locoadmin.running_job()
    rel = {"updated": _rel(_iso_to_epoch(status.get("updated"))), "db_at": _rel(repl.get("db_at")),
           "pulled_at": _rel(repl.get("pulled_at")), "peer_seen": _rel(peer.get("seen"))}
    return render_template("ha.html", available=True, node=node, status=status, peer=peer, repl=repl,
                           code=_pairing_code() if node.get("role") == "primary" and not (node.get("peer") or {}).get("address") else "",
                           lxc=in_lxc(), running=running, agent=locoadmin.agent_running(), rel=rel,
                           version=config.VERSION)


def _iso_to_epoch(iso):
    try:
        return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def _submit(action, params=None):
    """Dépose une demande pour l'agent root, avec les mêmes garde-fous que l'appliance."""
    from flask import flash
    if not available():
        abort(404)
    running = locoadmin.running_job()
    if running:
        flash(_("Opération en cours. Réessayez à la fin."), "error")
        return False
    if not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas."), "error")
        return False
    try:
        locoadmin.request(action, params, user=g.user["username"])
    except ValueError as e:
        flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
        return False
    return True


@app.route("/system/ha/enable", methods=["POST"])
@login_required
def ha_enable():
    require("system.configure")
    if _submit("ha-enable", {"vip": request.form.get("vip", "").strip()}):
        audit("ha.enable", request.form.get("vip", "").strip())
    return back("ha_page")


@app.route("/system/ha/join", methods=["POST"])
@login_required
def ha_join():
    require("system.configure")
    if _submit("ha-join", {"primary": request.form.get("primary", "").strip(), "code": request.form.get("code", "").strip()}):
        audit("ha.join", request.form.get("primary", "").strip())
    return back("ha_page")


@app.route("/system/ha/failover", methods=["POST"])
@login_required
def ha_failover():
    require("system.configure")
    if _submit("ha-failover"):
        audit("ha.failover")
    return back("ha_page")


@app.route("/system/ha/disable", methods=["POST"])
@login_required
def ha_disable():
    require("system.configure")
    if _submit("ha-disable"):
        audit("ha.disable")
    return back("ha_page")


# --------------------------------------------------------------------------- réplication (appelée par le pair)

def _authed(verifier_file):
    """Jeton X-Loco-HA valide : son empreinte correspond au vérificateur local (secret ou code jamais stockés)."""
    token = request.headers.get("X-Loco-HA", "")
    if not token:
        return False
    try:
        with open(verifier_file, encoding="utf-8") as f:
            expected = f.read().strip()
    except OSError:
        return False
    return bool(expected) and hmac.compare_digest(locoha.verifier(token), expected)


def _require_configured():
    if not configured():
        abort(404)


def _serve(name, mimetype):
    path = locoha._out(name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype=mimetype, conditional=False)


@app.route("/ha/api/join", methods=["POST"])
def ha_join_api():
    """Appairage du secours : le code vaut une fois, renvoie le paquet scellé (secret, IP virtuelle, autorité)."""
    _require_configured()
    if not _authed(locoha.join_verifier_path()):
        abort(403)
    body = request.get_json(silent=True) or {}
    if body.get("version") != config.VERSION:
        return jsonify({"error": "version"}), 409
    package = locoha._out("join.bin")
    if not os.path.isfile(package):
        abort(409)
    with open(package, "rb") as f:
        blob = f.read()                                 # lu avant d'enrôler : la tâche ha-peer le supprime ensuite
    address = str(body.get("address", "")).strip()
    try:
        locoadmin.request("ha-peer", {"address": address, "name": str(body.get("name", ""))}, user="loco-ha")
    except ValueError:
        abort(400)
    audit("ha.peer", address, username="loco-ha")
    return Response(blob, mimetype="application/octet-stream")


@app.route("/ha/api/state")
def ha_state_api():
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    return _serve("state.json", "application/json")


@app.route("/ha/api/bundle")
def ha_bundle_api():
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    return _serve("bundle.bin", "application/octet-stream")


@app.route("/ha/api/db")
def ha_db_api():
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    return _serve("db.bin", "application/octet-stream")


@app.route("/ha/api/manifest")
def ha_manifest_api():
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    return _serve("manifest.json.gz", "application/gzip")


@app.route("/ha/api/file")
def ha_file_api():
    """Un fichier d'un dépôt répliqué (chemin relatif à DATA, validé contre les traversées)."""
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    rel = locoha.safe_rel(request.args.get("p", ""))
    if not rel:
        abort(400)
    path = os.path.join(config.DATA, rel)
    if not os.path.isfile(path) or os.path.islink(path):
        abort(404)
    return send_file(path, mimetype="application/octet-stream", conditional=False)


@app.route("/ha/api/leave", methods=["POST"])
def ha_leave_api():
    """Le principal dissout la grappe : ce secours quitte à son tour."""
    _require_configured()
    if not _authed(locoha.verifier_path()):
        abort(403)
    try:
        locoadmin.request("ha-disable", {}, user="loco-ha")
    except ValueError:
        abort(400)
    audit("ha.disable", "quitte la grappe (demandé par le principal)", username="loco-ha")
    return jsonify({"leaving": True})
