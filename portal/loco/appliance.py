"""Mises à jour du serveur (système ; loco sur l'appliance Alpine) et réglages de l'appliance, exécutés par l'agent
root (locoadmin) : le portail dépose les demandes, l'agent les revalide et les applique."""

import ipaddress
import re
import socket
import subprocess

from flask import abort, flash, g, render_template, request

from . import app, config
from .i18n import L_, _
from .auth import audit, login_required, require
from .system import _alpine_release
from .util import back

if config.SERVER_UPDATES:
    import locoadmin

ACTION_LABELS = {"check": L_("Vérification des mises à jour"), "update-loco": L_("Mise à jour de loco"),
                 "update-system": L_("Mise à jour du système"), "update-security": L_("Correctifs de sécurité"),
                 "configure": L_("Réglages"), "reboot": L_("Redémarrage"), "set-repo": L_("Source des mises à jour"),
                 "set-time": L_("Heure du serveur"), "install-qemu-agent": L_("Installation de l'agent invité QEMU"),
                 "restore": L_("Restauration d'une sauvegarde"), "set-backup-keep": L_("Conservation des sauvegardes"),
                 "delete-backup": L_("Suppression d'une sauvegarde"), "set-shell": L_("Shell root du serveur")}


def job_detail(job):
    """Résumé lisible des paramètres d'une opération (au lieu de « clé=valeur »)."""
    p, action = job.get("params") or {}, job.get("action")
    if action == "set-shell":
        return _("activation (effective au redémarrage)") if p.get("enabled") == "1" else _("désactivation")
    if action == "set-backup-keep":
        return _("%(n)s sauvegardes conservées", n=p.get("keep", "?"))
    if action == "set-time":
        ntp = _("NTP %(s)s", s=p.get("ntp_server") or _("serveurs par défaut")) if p.get("ntp") else _("NTP désactivé")
        return " · ".join(x for x in (p.get("timezone", ""), ntp) if x)
    if action in ("restore", "delete-backup"):
        return p.get("archive") or p.get("name") or (_("fichier téléversé") if p.get("upload") else "")
    if action == "set-repo":
        return p.get("repo", "")
    return " ".join(f"{k}={v}" for k, v in p.items())
TAG_LABELS = {"noyau": "noyau", "loco": L_("service loco"), "socle": "socle"}


def _reboot_text(r):
    """Raison de redémarrage (code renvoyé par l'agent) en toutes lettres, dans la langue de la personne."""
    if isinstance(r, str):  # État conservé avant les messages structurés, jusqu'à la prochaine vérification.
        return r
    code = r.get("code")
    if code == "kernel-modules":
        return _("noyau mis à jour (les modules du noyau en cours ont été remplacés)")
    if code == "kernel-newer":
        return _("noyau %(installed)s installé, %(running)s en cours", installed=r.get("installed", ""), running=r.get("running", ""))
    if code == "requested":
        return _("redémarrage demandé par %(packages)s", packages=r["packages"]) if r.get("packages") \
            else _("redémarrage demandé par une mise à jour")
    if code == "stale-core":
        return _("d'anciennes bibliothèques restent chargées par %(units)s", units=r.get("units", ""))
    return ""


def _error_text(e):
    """Erreur d'état (code renvoyé par l'agent) dans la langue de la personne ; le détail reste tel quel."""
    if isinstance(e, str):
        return e
    if e.get("code") == "loco-release":
        return _("Version publiée de loco introuvable : %(detail)s", detail=e.get("detail", ""))
    if e.get("code") == "system-packages":
        return _("Paquets du système : %(detail)s.", detail=e.get("detail", ""))
    return e.get("detail", "")
SWITCHES = ("HTTP_ENABLED", "WU_ENABLED", "RPM_ENABLED", "FLATPAK_ENABLED")


def _require_appliance():
    if not config.APPLIANCE:
        abort(404)


def _require_agent():
    if not config.SERVER_UPDATES:
        abort(404)


def _agent_restart():
    return "rc-service loco-agent restart" if config.APPLIANCE else "systemctl restart loco-agent"


def _submit(action, params=None):
    running = locoadmin.running_job()
    if running:
        flash(_('Opération en cours : %(get)s. Réessayez à la fin.', get=ACTION_LABELS.get(running['action'], running['action'])), "error")
        return False
    if not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(_agent_restart)s.", _agent_restart=_agent_restart()), "error")
        return False
    try:
        locoadmin.request(action, params, user=_username())
    except ValueError as e:
        flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
        return False
    return True


def _username():
    return g.user["username"] if g.get("user") else ""


def _network():
    """État du réseau, en lecture seule (modifié sur la console avec setup-loco)."""
    info = {"hostname": socket.gethostname(), "addresses": [], "gateway": "", "dns": [], "mode": ""}
    try:
        out = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=5).stdout
        info["addresses"] = [f"{f[3]} ({f[1]})" for f in (line.split() for line in out.splitlines()) if len(f) > 3]
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        info["gateway"] = out.split()[2] if out.startswith("default via") else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    try:
        with open("/etc/resolv.conf") as f:
            info["dns"] = [line.split()[1] for line in f if line.startswith("nameserver") and len(line.split()) > 1]
    except OSError:
        pass
    try:
        with open("/etc/network/interfaces") as f:
            text = f.read()
        info["mode"] = "DHCP" if " dhcp" in text else ("adresse fixe" if " static" in text else "")
    except OSError:
        pass
    return info


def _kernel_short(release):
    """« 6.12.48+deb13-amd64 » -> « 6.12.48+deb13 » (variante d'architecture retirée, version complète en info-bulle)."""
    return re.sub(r"[-.](amd64|arm64|x86_64|aarch64|pve|lts|virt|generic)$", "", release or "")


def _packages(status):
    """Paquets en attente (états antérieurs : seulement des noms), filtrables par l'interface."""
    packages = status.get("packages")
    if not isinstance(packages, list):
        packages = [{"name": n, "current": "", "new": "", "origin": "", "security": False, "severity": "", "tags": []}
                    for n in status.get("system_updates") or []]
    return [p for p in packages if isinstance(p, dict) and p.get("name")]


@app.route("/system/updates")
@login_required
def appliance_updates():
    _require_agent()
    status = locoadmin.read_status()
    latest = status.get("latest") or {}
    jobs = locoadmin.jobs(limit=8)
    selected = request.args.get("operation", "")
    job = next((j for j in jobs if j["id"] == selected), None) or (jobs[0] if jobs else None)
    packages = _packages(status)
    return render_template(
        "appliance_updates.html", status=status, latest=latest, jobs=jobs, job=job,
        signature=f"{jobs[0]['id']}:{jobs[0]['state']}" if jobs else "",
        job_log=locoadmin.job_log(job["id"]) if job else "", labels=ACTION_LABELS, tag_labels=TAG_LABELS, job_detail=job_detail,
        update_available=bool(latest) and locoadmin.version_key(latest["version"]) > locoadmin.version_key(config.VERSION),
        running=locoadmin.running_job(), agent=locoadmin.agent_running(), agent_restart=_agent_restart(),
        os_label=status.get("os") or (f"Alpine Linux {_alpine_release()}" if config.APPLIANCE else _("Système")),
        reboot_texts=[_reboot_text(r) for r in status.get("reboot_reasons") or []],
        repo=status.get("repo") or locoadmin.DEFAULT_REPO, default_repo=locoadmin.DEFAULT_REPO,
        errors=[_error_text(e) for e in status.get("errors") or []],
        packages=packages, security=[p for p in packages if p.get("security")],
        key_count=sum(1 for p in packages if p.get("tags")),
        security_supported=status.get("security_supported", False), kernel_short=_kernel_short,
    )


@app.route("/system/updates/operation/<job_id>")
@login_required
def appliance_job(job_id):
    _require_agent()
    job = next((j for j in locoadmin.jobs(limit=40) if j["id"] == job_id), None)
    if not job:
        abort(404)
    return {"state": job.get("state", ""), "log": locoadmin.job_log(job_id)}


@app.route("/system/operations/state")
@login_required
def appliance_operations_state():
    _require_agent()
    latest = locoadmin.jobs(limit=1)
    signature = f"{latest[0]['id']}:{latest[0]['state']}" if latest else ""
    return {"signature": signature}


@app.route("/system/updates", methods=["POST"])
@login_required
def appliance_updates_post():
    _require_agent()
    action = request.form.get("action", "")
    if action not in ("check", "update-system", "update-security", "reboot", "update-loco", "set-repo"):
        abort(400)
    require("system.view" if action == "check" else "system.reboot" if action == "reboot" else "system.update")
    params = {"repo": request.form.get("repo", "").strip()} if action == "set-repo" else None
    if _submit(action, params):
        audit(f"systeme.{action}", ACTION_LABELS[action])
        flash((_('Redémarrage demandé.') if action == "reboot" else (_('Installation des correctifs de sécurité lancée.') if action == "update-security" else _('%(ACTION_LABELS)s lancée.', ACTION_LABELS=ACTION_LABELS[action]))), "ok")
    return back("appliance_updates")


@app.route("/system/configuration")
@login_required
def appliance_settings():
    _require_appliance()
    conf = locoadmin.read_conf()
    return render_template("appliance_settings.html", conf=conf, settings=locoadmin.SETTINGS, switches=SWITCHES,
                           network=_network(), running=locoadmin.running_job(), agent=locoadmin.agent_running(),
                           client_ip=request.remote_addr or "")


@app.route("/system/configuration", methods=["POST"])
@login_required
def appliance_settings_post():
    _require_appliance()
    changes = {}
    for key in locoadmin.SETTINGS:
        if key in SWITCHES:
            changes[key] = "1" if request.form.get(key) == "1" else "0"
        elif key in request.form:
            changes[key] = request.form.get(key, "")
    try:
        clean = locoadmin.validate(changes)
    except ValueError as e:
        flash(str(e) + ".", "error")
        return back("appliance_settings")
    if not clean:
        flash(_('Aucun changement.'), "ok")
        return back("appliance_settings")
    # Ne pas se couper soi-même l'accès à l'interface.
    if "ADMIN_ALLOW" in clean and not _allowed(request.remote_addr, clean["ADMIN_ALLOW"]):
        flash(_("Les réseaux de l'interface doivent inclure votre adresse (%(remote_addr)s).", remote_addr=request.remote_addr), "error")
        return back("appliance_settings")
    if _submit("configure", clean):
        audit("systeme.configuration", ", ".join(f"{k}={v}" for k, v in clean.items()))
        flash(_("Réglages en cours d'application : les services redémarrent quelques secondes."), "ok")
        return back("appliance_updates")
    return back("appliance_settings")


def _allowed(ip, networks):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    addr = getattr(addr, "ipv4_mapped", None) or addr
    if addr.is_loopback:
        return True
    return any(addr in ipaddress.ip_network(n, strict=False) for n in networks.split())

