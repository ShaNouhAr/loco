"""Enrôlement : rattacher un poste à loco, par système. L'agent l'inscrit dans Mon parc (MSI, paquet .deb ou .rpm,
ou commande) ; les dépôts et caches lui servent ensuite paquets et mises à jour depuis le réseau local."""

from flask import render_template

from . import agentpkg, app, config, features
from .auth import can, login_required


@app.route("/enrolement")
@login_required
def enrollment():
    from . import fleet
    key = fleet.enroll_key() if can("fleet.enroll") and features.enabled("fleet") else ""
    msi, msi_state = fleet.agent_msi() if key else (None, "")
    return render_template("enrollment.html", key=key, commands=fleet._commands(key) if key else {},
                           ca_sha256=fleet._ca_fingerprint() if key else "", version=config.VERSION,
                           msi_sha256=msi_state if msi else "", msi_ready=bool(msi),
                           proxy_host=config.HOST, proxy_port=config.WU_PROXY_PORT,
                           packages={kind: {"name": name(config.VERSION), "full": name(config.VERSION, True),
                                            "repos": fleet.repos_available(kind)}
                                     for kind, name in (("deb", agentpkg.deb_name), ("rpm", agentpkg.rpm_name))})
