"""Page Système, contrôle de santé et métriques Prometheus."""

import datetime
import os
import re
import subprocess
import sys

from flask import Response, abort, flash, g, redirect, render_template, request, url_for

from . import app, config
from .i18n import L_, _
from .auth import audit, login_required, require
from .db import db
from .linux import list_debs
from .util import back, disk_usage, read_stats, service_active

if config.SERVER_UPDATES:
    import locoadmin


def server_time():
    """Fuseau horaire et état de la synchronisation NTP du serveur, en lecture seule (appliqués par l'agent root)."""
    info = {"timezone": "", "local": datetime.datetime.now().strftime("%H:%M"), "ntp": False,
            "synchronized": False, "server": "", "backend": ""}
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "Timezone", "-p", "NTP", "-p", "NTPSynchronized"],
                             capture_output=True, text=True, timeout=5).stdout
        values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        info["timezone"] = values.get("Timezone", "")
        info["ntp"] = values.get("NTP") == "yes"
        info["synchronized"] = values.get("NTPSynchronized") == "yes"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if not info["timezone"]:
        try:
            info["timezone"] = os.readlink("/etc/localtime").split("zoneinfo/", 1)[-1]
        except OSError:
            pass
    for path in ("/etc/systemd/timesyncd.conf.d/loco.conf", "/etc/chrony/conf.d/loco.conf", "/etc/chrony.d/loco.conf"):
        try:
            with open(path) as f:
                match = re.search(r"(?:NTP=|server\s+)(\S+)", f.read())
            if match:
                info["server"] = match.group(1)
                break
        except OSError:
            pass
    return info


def timezones():
    """Fuseaux horaires proposés dans le menu (liste du système, repli sur zoneinfo)."""
    try:
        out = subprocess.run(["timedatectl", "list-timezones"], capture_output=True, text=True, timeout=5).stdout
        zones = out.split()
        if zones:
            return zones
    except (OSError, subprocess.SubprocessError):
        pass
    base, found = "/usr/share/zoneinfo", []
    for root, _dirs, files in os.walk(base):
        if any(skip in root for skip in ("/posix", "/right", "/SystemV", "/Etc")):
            continue
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), base)
            if "/" in rel:
                found.append(rel)
    return sorted(set(found) | {"UTC"})


def qemu_agent():
    """État de l'agent invité QEMU : virtualisation détectée, paquet installé, service actif."""
    virt = ""
    try:
        virt = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    installed = any(os.path.exists(p) for p in ("/usr/sbin/qemu-ga", "/usr/bin/qemu-ga"))
    return {"qemu": virt in ("qemu", "kvm"), "virt": virt, "installed": installed,
            "running": service_active("qemu-guest-agent")}
from .wupdate import wu_stats

UNITS = [
    ("loco-portal", L_("Interface et scripts clients")),
    ("nginx", L_("HTTPS, fichiers, cache Windows Update")),
    ("apt-cacher-ng", L_("Cache des paquets Linux")),
    ("loco-stats.timer", L_("Taille des caches, toutes les 15 min")),
    ("loco-nginx.path", L_("Recharge nginx quand la liste des domaines change")),
    ("loco-backup.timer", L_("Sauvegarde quotidienne")),
]
if config.CONTAINER:
    UNITS = UNITS[:3] + [("loco-cron", "Tâches périodiques : taille des caches, rechargement nginx, sauvegarde")]
if config.ALPINE:
    UNITS = [
        ("loco-portal", "Interface et scripts clients"),
        ("nginx", "HTTPS, fichiers, caches APT, RPM, Flathub et Windows Update"),
        ("loco-cron", "Tâches périodiques : taille des caches, rechargement nginx, sauvegarde"),
        ("loco-agent", "Mises à jour et configuration de l'appliance"),
    ]
if config.WINDOWS:
    UNITS = [
        ("loco-portal", "Interface et scripts clients (service Windows loco)"),
        ("nginx", "HTTPS, fichiers, caches APT, RPM, Flathub et Windows Update"),
        ("loco-tasks", "Tâches : taille des caches, rechargement nginx, sauvegarde quotidienne"),
    ]


def _cert_info_python(path):
    """Résumé d'un certificat X.509 avec la bibliothèque cryptography (serveur Windows, sans openssl)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    try:
        with open(path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
    except (OSError, ValueError):
        return None

    def when(d):
        return {"text": d.strftime("%d/%m/%Y"),
                "days": (d - datetime.datetime.now(datetime.timezone.utc)).days}
    info = {"subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
            "start": when(cert.not_valid_before_utc), "end": when(cert.not_valid_after_utc),
            "sha256": ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256())), "san": []}
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        info["san"] = [f"DNS:{n}" for n in san.get_values_for_type(x509.DNSName)] + \
                      [f"IP:{n}" for n in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass
    return info


def cert_info(path):
    """Résumé d'un certificat X.509 (via openssl)."""
    if config.WINDOWS:
        return _cert_info_python(path)
    try:
        out = subprocess.run(["openssl", "x509", "-in", path, "-noout", "-subject", "-issuer", "-startdate",
                              "-enddate", "-fingerprint", "-sha256", "-ext", "subjectAltName"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    info = {"san": []}
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("subject="):
            info["subject"] = line.split("=", 1)[1].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line.split("=", 1)[1].strip()
        elif line.startswith("notBefore="):
            info["start"] = _cert_date(line.split("=", 1)[1])
        elif line.startswith("notAfter="):
            info["end"] = _cert_date(line.split("=", 1)[1])
        elif "Fingerprint=" in line:
            info["sha256"] = line.split("=", 1)[1]
        elif line.startswith(("DNS:", "IP Address:")):
            info["san"] = [x.strip().replace("IP Address:", "IP:") for x in line.split(",")]
    return info


def _cert_date(text):
    try:
        d = datetime.datetime.strptime(text.strip(), "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return {"text": text.strip(), "days": None}
    return {"text": d.strftime("%d/%m/%Y"), "days": (d - datetime.datetime.utcnow()).days}


def package_version(name):
    if name == "python3-flask":
        from importlib.metadata import version
        return version("Flask")
    if config.WINDOWS:
        from .util import windows_status
        return windows_status().get("versions", {}).get(name, "")
    if config.ALPINE:
        try:
            out = subprocess.run(["apk", "info", "-e", "-v", name], capture_output=True, text=True, timeout=5)
            return out.stdout.strip()[len(name) + 1:] if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Version}", name], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", name], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
        if name == "apt-cacher-ng":
            with open("/usr/local/lib/loco/acng/version") as f:
                return f.read().strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _alpine_release():
    try:
        with open("/etc/alpine-release") as f:
            return f.read().strip()
    except OSError:
        return ""


def list_backups():
    try:
        names = sorted((n for n in os.listdir(config.BACKUP_DIR) if n.endswith(".tar.gz")), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        st = os.stat(os.path.join(config.BACKUP_DIR, n))
        out.append({"name": n, "size": st.st_size,
                    "when": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime": st.st_mtime})
    return out


def _age(mtime):
    """Ancienneté d'une sauvegarde en clair, et retard si la sauvegarde quotidienne a manqué (plus de 2 jours)."""
    hours = max(0, (datetime.datetime.now().timestamp() - mtime) / 3600)
    text = (_("il y a moins d'une heure") if hours < 1 else _("il y a %(n)s h", n=int(hours)) if hours < 48
            else _("il y a %(n)s j", n=int(hours // 24)))
    return {"text": text, "late": hours > 48}


@app.route("/system")
@login_required
def system():
    services = [(unit, desc, service_active(unit)) for unit, desc in UNITS]
    if not (config.CONTAINER or config.ALPINE or config.WINDOWS) and os.path.exists("/etc/squid/squid.conf"):
        services.append(("squid", "Proxy cache Windows Update (sans DNS)", service_active("squid")))
    backups = list_backups()
    return render_template(
        "system.html",
        services=services, stopped=[unit for unit, _, active in services if not active],
        last_backup=_age(backups[0]["mtime"]) if backups else None,
        cert=cert_info(os.path.join(config.TLS_DIR, "server.crt")),
        ca=cert_info(os.path.join(config.TLS_DIR, "ca.crt")),
        versions=({"loco": config.VERSION, "nginx": package_version("nginx"),
                   "waitress": package_version("waitress"), "cryptography (signatures, TLS)": package_version("cryptography"),
                   "Python": sys.version.split()[0],
                   "Flask": package_version("python3-flask"), "Windows": package_version("windows")}
                  if config.WINDOWS else
                  {"loco": config.VERSION, "nginx": package_version("nginx"), "Python": sys.version.split()[0],
                   "Flask": package_version("py3-flask"), "Alpine Linux": _alpine_release()}
                  if config.ALPINE else
                  {"loco": config.VERSION, "nginx": package_version("nginx"),
                   "apt-cacher-ng": package_version("apt-cacher-ng"), "Python": sys.version.split()[0],
                   "Flask": package_version("python3-flask")}),
        disk=disk_usage(),
        stats=read_stats(),
        backups=backups,
        backup_dir=config.BACKUP_DIR,
        paths={_("Données"): config.DATA, _("État et base"): config.STATE, _("Configuration"): config.ETC,
               _("Certificats"): config.TLS_DIR, _("Sauvegardes"): config.BACKUP_DIR},
        time_info=server_time() if config.SERVER_UPDATES and not config.WINDOWS else None,
        timezones=timezones() if config.SERVER_UPDATES and not config.WINDOWS else [],
        qemu=qemu_agent() if config.SERVER_UPDATES and not config.WINDOWS else None,
    )


@app.route("/system/qemu-agent", methods=["POST"])
@login_required
def system_qemu_agent():
    """Installe l'agent invité QEMU (qemu-guest-agent) via l'agent root, utile quand loco tourne dans une VM."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    if locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request("install-qemu-agent", user=g.user["username"] if g.get("user") else "")
            audit("systeme.qemu-agent", _("Agent invité QEMU"))
            flash(_("Installation de l'agent invité QEMU lancée."), "ok")
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system")


@app.route("/system/time", methods=["POST"])
@login_required
def system_time():
    """Fuseau horaire et synchronisation NTP du serveur, appliqués par l'agent root (locoadmin)."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    params = {"timezone": request.form.get("timezone", "").strip(),
              "ntp": "1" if request.form.get("ntp") == "1" else "",
              "ntp_server": request.form.get("ntp_server", "").strip()}
    if locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request("set-time", params, user=g.user["username"] if g.get("user") else "")
            audit("systeme.heure", ", ".join(x for x in (params["timezone"],
                  (_("NTP %(s)s", s=params["ntp_server"] or _("serveurs par défaut")) if params["ntp"] else _("NTP désactivé"))) if x))
            flash(_("Réglages de l'heure en cours d'application."), "ok")
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system")


@app.route("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return "ok"


@app.route("/metrics")
def metrics():
    """Format texte Prometheus. Accès limité par nginx aux réseaux d'administration."""
    stats = read_stats()
    wu = wu_stats()
    disk = disk_usage()
    lines = []

    def metric(name, help_text, kind, samples):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            label = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
            lines.append(f"{name}{label} {value}")

    metric("loco_info", "Version de loco.", "gauge", [({"version": config.VERSION}, 1)])
    metric("loco_service_up", "Service actif (1) ou non (0).", "gauge",
           [({"unit": u}, int(service_active(u))) for u, _ in UNITS])
    metric("loco_cache_bytes", "Taille des caches sur disque.", "gauge",
           [({"cache": "apt"}, stats.get("acng", 0)), ({"cache": "windows_update"}, stats.get("wucache", 0)),
            ({"cache": "flathub"}, stats.get("flatpakcache", 0)), ({"cache": "rpm"}, stats.get("rpmcache", 0))])
    metric("loco_wu_recent_bytes", "Octets Windows Update récents servis depuis le cache ou Internet.", "gauge",
           [({"source": "cache"}, wu["hit_bytes"]), ({"source": "internet"}, wu["miss_bytes"])])
    metric("loco_deb_packages", "Paquets du dépôt APT local.", "gauge", [({}, len(list_debs()))])
    if config.RPM_ENABLED:
        from .rpmrepo import list_rpms
        metric("loco_rpm_packages", "Paquets des dépôts RPM locaux.", "gauge", [({}, len(list_rpms()))])
    from .fleet import catalog, status
    apps = catalog()
    colors = {"green": 0, "orange": 0, "red": 0, "grey": 0}
    for dev in db().execute("SELECT * FROM devices WHERE state != 'removed'"):
        colors[status(dev, apps)[0]] += 1
    metric("loco_devices", "Postes du parc par voyant (green à jour, orange en attente, red à traiter).", "gauge",
           [({"status": c}, n) for c, n in colors.items()])
    rows = db().execute("SELECT status, COUNT(*) AS n FROM win_apps GROUP BY status").fetchall()
    metric("loco_windows_apps", "Logiciels du catalogue Windows par état.", "gauge",
           [({"status": r["status"]}, r["n"]) for r in rows] or [({"status": "ready"}, 0)])
    metric("loco_disk_bytes", "Disque des données.", "gauge",
           [({"kind": "total"}, disk["total"]), ({"kind": "free"}, disk["free"])])
    cert = cert_info(os.path.join(config.TLS_DIR, "server.crt"))
    if cert and cert.get("end", {}).get("days") is not None:
        metric("loco_tls_cert_expiry_days", "Jours avant expiration du certificat HTTPS.", "gauge",
               [({}, cert["end"]["days"])])
    backups = list_backups()
    metric("loco_backup_last_timestamp_seconds", "Date de la dernière sauvegarde (0 si aucune).", "gauge",
           [({}, int(backups[0]["mtime"]) if backups else 0)])
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4")
