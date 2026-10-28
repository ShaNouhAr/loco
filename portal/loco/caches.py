"""Contenu des caches (Windows Update, APT, RPM, Flatpak) : visualisation et purge.

Les fichiers de cache appartiennent à nginx (ou à apt-cacher-ng) et sont illisibles pour le compte loco :
l'agent root (locoadmin) les parcourt, publie un index et applique les purges. Le portail lit l'index et
dépose les demandes.
"""
from flask import abort, jsonify, request

from . import app, appliance
from .auth import audit, can, login_required, require

# cache -> (droit de consultation de la page, droit de purge)
AREAS = {
    "wu": ("wu.view", "wu.configure"),
    "apt": ("linux.view", "linux.delete"),
    "rpm": ("rpm.view", "rpm.delete"),
    "flatpak": ("flatpak.view", "flatpak.delete"),
}


def _agent():
    """Module locoadmin si l'agent est présent (appliance et serveur Linux), sinon None."""
    return getattr(appliance, "locoadmin", None)


def available():
    """Vrai si la gestion des caches est possible sur ce serveur (agent root présent)."""
    return _agent() is not None


def visible_areas():
    """Caches que l'utilisateur peut consulter et dont le cache existe sur ce serveur."""
    agent = _agent()
    if agent is None:
        return {}
    caches = agent.cache_index().get("caches", {})
    out = {}
    for area, (view_perm, purge_perm) in AREAS.items():
        data = caches.get(area)
        if data and data.get("present") and can(view_perm):
            out[area] = {"can_purge": can(purge_perm)}
    return out


@app.route("/cache/state")
@login_required
def cache_state():
    agent = _agent()
    if agent is None:
        return jsonify({})
    wanted = [a for a in request.args.get("areas", "").split(",") if a in AREAS]
    rescan = request.args.get("scan") == "1"
    caches = agent.cache_index().get("caches", {})
    out, to_scan = {}, []
    for area in wanted:
        view_perm, purge_perm = AREAS[area]
        if not can(view_perm):
            continue
        data = caches.get(area) or {"present": False}
        out[area] = {"present": bool(data.get("present")), "bytes": data.get("bytes", 0),
                     "count": data.get("count", 0), "shown": data.get("shown", 0),
                     "scanned": data.get("scanned", ""), "entries": data.get("entries", []),
                     "can_purge": can(purge_perm)}
        if rescan and data.get("present"):
            to_scan.append(area)
    for area in to_scan:
        try:
            agent.cache_request("scan", area, user=_user())
        except ValueError:
            pass
    return jsonify(out)


@app.route("/cache/purge", methods=["POST"])
@login_required
def cache_purge():
    agent = _agent()
    area = request.form.get("area", "")
    if area not in AREAS or agent is None:
        abort(400)
    require(AREAS[area][1])
    everything = request.form.get("all") == "1"
    entry_id = request.form.get("id", "")
    try:
        agent.cache_request("purge", area, entry_id=entry_id, everything=everything, user=_user())
    except ValueError:
        abort(400)
    audit(f"cache.purge.{area}", "tout le cache" if everything else entry_id)
    return jsonify(ok=True)


def _user():
    from flask import g
    return g.user["username"] if g.get("user") else ""
