"""Enrôlement : rattacher un poste à loco, par système. L'agent l'inscrit dans Mon parc (MSI, paquet .deb ou .rpm,
ou commande) ; les dépôts et caches lui servent ensuite paquets et mises à jour depuis le réseau local."""

from flask import render_template, request

from . import agentpkg, app, config, features
from .auth import can, login_required


@app.route("/enrollment")
@login_required
def enrollment():
    from . import fleet, scope
    key = fleet.enroll_key() if can("fleet.enroll") and features.enabled("fleet") else ""
    device_groups = fleet.enrollment_groups() if key else []
    group, no_group = None, False
    if key and not scope.whole() and not device_groups:
        key, no_group = "", True                    # profil limité sans groupe attribué : rien à inscrire
    elif key:
        # Tout le parc : sans groupe par défaut ; profil limité : un de ses groupes, toujours.
        group = fleet.enrollment_group(request.args.get("groupe", ""))
    if group:
        key = fleet.group_key(group["id"])          # MSI, paquets et commandes : le poste rejoint ce groupe
    msi, msi_state = fleet.agent_msi() if key else (None, "")
    return render_template("enrollment.html", key=key, commands=fleet._commands(key) if key else {},
                           device_groups=device_groups, group=group, whole_fleet=scope.whole(), no_group=no_group,
                           ca_sha256=fleet._ca_fingerprint() if key else "", version=config.VERSION,
                           msi_sha256=msi_state if msi else "", msi_ready=bool(msi),
                           proxy_host=config.HOST, proxy_port=config.WU_PROXY_PORT, proxy_available=config.WU_PROXY,
                           packages={kind: {"name": name(config.VERSION), "full": name(config.VERSION, True),
                                            "repos": fleet.repos_available(kind)}
                                     for kind, name in (("deb", agentpkg.deb_name), ("rpm", agentpkg.rpm_name))})
