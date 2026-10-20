"""Page Système, contrôle de santé et métriques Prometheus."""

import datetime
import os
import subprocess
import sys

from flask import Response, render_template

from . import app, config
from .auth import login_required
from .db import db
from .linux import list_debs
from .util import disk_usage, read_stats, service_active
from .wupdate import wu_stats

UNITS = [
    ("loco-portal", "Interface et scripts clients"),
    ("nginx", "HTTPS, fichiers, cache Windows Update"),
    ("apt-cacher-ng", "Cache des paquets Linux"),
    ("loco-stats.timer", "Taille des caches, toutes les 15 min"),
    ("loco-nginx.path", "Recharge nginx quand la liste des domaines change"),
    ("loco-backup.timer", "Sauvegarde quotidienne"),
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
    text = "il y a moins d'une heure" if hours < 1 else f"il y a {int(hours)} h" if hours < 48 else f"il y a {int(hours // 24)} j"
    return {"text": text, "late": hours > 48}


@app.route("/systeme")
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
        paths={"Données": config.DATA, "État et base": config.STATE, "Configuration": config.ETC,
               "Certificats": config.TLS_DIR, "Sauvegardes": config.BACKUP_DIR},
    )


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
