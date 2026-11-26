"""Enrôlement : rattacher un poste à loco, par système. L'agent l'inscrit dans Mon parc (MSI, paquet .deb ou .rpm,
ou commande) ; les dépôts et caches lui servent ensuite paquets et mises à jour depuis le réseau local. Un serveur
Proxmox VE ou un Home Assistant, sans agent, se relie aussi d'ici (jeton d'API, clé SSH de loco)."""

from flask import render_template, request

from . import agentpkg, app, config, features
from .auth import can, login_required
from .db import db


def _proxmox_link():
    """Onglet Proxmox VE : liaisons en attente de confirmation du certificat, serveurs reliés sans SSH (pas encore dans
    Mon parc), et si la place reste, les étapes d'une nouvelle liaison."""
    if not (features.enabled("proxmox") and can("proxmox.manage")):
        return None
    from . import proxmox
    rows = db().execute("SELECT * FROM proxmox_hosts ORDER BY name COLLATE NOCASE, id").fetchall()
    return {"pending": [v for v in (proxmox.view(r) for r in rows if not r["fingerprint"]) if v["offered"]],
            "without_ssh": [r for r in rows if r["fingerprint"] and not r["host_key"]],
            "full": len(rows) >= proxmox.MAX_HOSTS, "role": proxmox.ROLE, "privileges": proxmox.PRIVILEGES}


def _homeassistant_link():
    """Onglet Home Assistant : clé d'hôte d'une nouvelle liaison à confirmer, ou les étapes pour en relier une."""
    if not (features.enabled("homeassistant") and can("homeassistant.manage")):
        return None
    from . import homeassistant
    pending = homeassistant._pending()
    count = db().execute("SELECT COUNT(*) FROM homeassistant_hosts").fetchone()[0]
    return {"pending": pending if pending and not pending.get("host_id") else None, "public_key": homeassistant.public_key(),
            "ssh": bool(homeassistant.ssh_binary()), "full": count >= homeassistant.MAX_HOSTS}


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
    apk, apk_state = fleet.agent_apk() if key and features.enabled("android") else (None, "")
    if key and features.enabled("android") and not apk:
        fleet.prefetch_apk()            # récupérer l'APK de cette version en arrière-plan : il est là au rechargement
    return render_template("enrollment.html", key=key, commands=fleet._commands(key) if key else {},
                           pve_link=_proxmox_link(), ha_link=_homeassistant_link(),
                           device_groups=device_groups, group=group, whole_fleet=scope.whole(), no_group=no_group,
                           ca_sha256=fleet._ca_fingerprint() if key else "", version=config.VERSION,
                           msi_sha256=msi_state if msi else "", msi_ready=bool(msi),
                           apk_sha256=apk_state if apk else "", apk_ready=bool(apk),
                           proxy_host=config.HOST, proxy_port=config.WU_PROXY_PORT, proxy_available=config.WU_PROXY,
                           packages={kind: {"name": name(config.VERSION), "full": name(config.VERSION, True),
                                            "repos": fleet.repos_available(kind)}
                                     for kind, name in (("deb", agentpkg.deb_name), ("rpm", agentpkg.rpm_name))})
