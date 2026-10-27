"""Page Système, contrôle de santé et métriques Prometheus."""

import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse

from flask import Response, abort, flash, g, render_template, request, send_file

from . import app, config
from .i18n import L_, _
from .auth import audit, login_required
from .db import db
from .linux import list_debs
from .util import back, disk_usage, read_stats, service_active

if config.SERVER_UPDATES:
    import locoadmin


def server_time():
    """Fuseau horaire et état NTP du serveur, en lecture seule. L'état fin (source en cours, strate, écart, liste des
    sources) est mesuré par l'agent root — le portail, non privilégié, ne peut pas interroger chrony sur l'appliance —
    puis déposé dans un fichier que l'on relit ici ; repli sur une lecture directe quand ce fichier manque."""
    info = {"timezone": "", "local": datetime.datetime.now().strftime("%H:%M"), "ntp": False, "synchronized": False,
            "source": "", "stratum": None, "offset_ms": None, "servers": [], "sources": [],
            "container": _virt()[1], "fresh": False}
    try:
        info["timezone"] = os.readlink("/etc/localtime").split("zoneinfo/", 1)[-1]
    except OSError:
        pass
    data = None
    if config.SERVER_UPDATES:
        try:
            with open(locoadmin.TIME_STATUS) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    if data and time.time() - data.get("at", 0) < 90:     # état frais mesuré par l'agent root
        info["fresh"], info["container"], info["ntp"] = True, data.get("container", info["container"]), bool(data.get("enabled"))
        for key in ("synchronized", "source", "stratum", "offset_ms", "servers", "sources"):
            info[key] = data.get(key, info[key])
    elif shutil.which("timedatectl"):                     # repli non privilégié (hôtes systemd)
        try:
            out = subprocess.run(["timedatectl", "show", "-p", "NTP", "-p", "NTPSynchronized"],
                                 capture_output=True, text=True, timeout=5).stdout
            values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            info["ntp"], info["synchronized"] = values.get("NTP") == "yes", values.get("NTPSynchronized") == "yes"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if not info["servers"]:                               # serveurs réglés par loco, pour pré-remplir le champ
        for path in ("/etc/systemd/timesyncd.conf.d/loco.conf", "/etc/chrony/conf.d/loco.conf",
                     "/etc/chrony.d/loco.conf", "/etc/chrony.loco.conf"):
            try:
                with open(path) as f:
                    for line in f:
                        m = re.match(r"\s*NTP=(.*)", line) or re.match(r"\s*server\s+(\S+)", line)
                        if m:
                            info["servers"] += m.group(1).split()
            except OSError:
                continue
            if info["servers"]:
                break
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


def _dmi(name):
    try:
        with open("/sys/class/dmi/id/" + name) as f:
            return f.read().strip()
    except OSError:
        return ""


def _virt():
    """(type de virtualisation, est un conteneur) : systemd-detect-virt si présent, sinon DMI — l'appliance Alpine
    n'a pas systemd, mais une VM QEMU/KVM se reconnaît à son fabricant DMI « QEMU »."""
    try:
        virt = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=3).stdout.strip()
        container = subprocess.run(["systemd-detect-virt", "--container"], capture_output=True, text=True,
                                   timeout=3).stdout.strip() not in ("none", "")
        return virt, container
    except (OSError, subprocess.SubprocessError):
        pass
    vendor, product = _dmi("sys_vendor"), _dmi("product_name")
    if "QEMU" in vendor or "QEMU" in product or "KVM" in vendor + product:
        return "qemu", False
    return "", False


def qemu_agent():
    """État de l'agent invité QEMU : virtualisation détectée, conteneur, paquet installé, service actif."""
    virt, container = _virt()
    installed = any(os.path.exists(p) for p in ("/usr/sbin/qemu-ga", "/usr/bin/qemu-ga"))
    return {"qemu": virt in ("qemu", "kvm"), "virt": virt, "container": container, "installed": installed,
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


def _url_port(url, default):
    try:
        return urllib.parse.urlsplit(url).port or default
    except ValueError:
        return default


def firewall_ports():
    """Ports à ouvrir en entrée sur le serveur loco, selon la configuration et les fonctionnalités actives.

    Le port interne de l'application (waitress/gunicorn) n'écoute qu'en local : il n'est pas listé."""
    from . import features
    off = features.disabled()
    https = _url_port(config.HTTPS_URL, 8443)
    rows = [{"port": str(https), "proto": "TCP", "service": _("Interface web, agents du parc et dépôts (APT, RPM, Flatpak, fichiers)"),
             "audience": _("Administration + postes"), "scheme": "HTTPS", "on": True, "always": True}]
    if config.HTTP_URL:
        rows.append({"port": str(_url_port(config.HTTP_URL, 8088)), "proto": "TCP",
                     "service": _("Amorçage HTTP des postes (déploiement de l'autorité et des dépôts)"),
                     "audience": _("Postes"), "scheme": "HTTP", "on": True, "always": False})
    if "apt" not in off:
        rows.append({"port": str(config.ACNG_PORT), "proto": "TCP",
                     "service": _("Cache de paquets Linux (apt-cacher-ng)"), "audience": _("Postes Linux"),
                     "scheme": "HTTP", "on": True, "always": False})
    if "wu" not in off:
        rows.append({"port": "80, 443", "proto": "TCP", "service": _("Cache Windows Update (redirection DNS des postes)"),
                     "audience": _("Postes Windows"), "scheme": "HTTP/TLS", "on": True, "always": False})
    if "wu_proxy" not in off:
        rows.append({"port": str(config.WU_PROXY_PORT), "proto": "TCP",
                     "service": _("Proxy Windows Update (Squid, sans redirection DNS)"), "audience": _("Postes Windows"),
                     "scheme": "HTTP", "on": True, "always": False})
    return rows


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
        firewall=firewall_ports(),
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
        backup_keep=backup_keep(),
        offsite=offsite_panel(),
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
    return back("system", _anchor="reglages")


BACKUP_NAME_RE = re.compile(r"(?:loco|local-repo)-[A-Za-z0-9._-]+-\d{8}-\d{6}\.tar\.gz")


@app.route("/system/backups/<name>")
@login_required
def system_backup_download(name):
    """Télécharge une sauvegarde (archive .tar.gz) du serveur. Réservé à la configuration du serveur."""
    if config.WINDOWS:
        abort(404)
    if not BACKUP_NAME_RE.fullmatch(name) or name not in {b["name"] for b in list_backups()}:
        abort(404)
    path = os.path.join(config.BACKUP_DIR, name)
    if not os.path.isfile(path):
        abort(404)
    try:
        audit("systeme.sauvegarde.telechargement", name)
        return send_file(path, mimetype="application/gzip", as_attachment=True, download_name=name)
    except PermissionError:
        abort(503)


@app.route("/system/backups/<name>/delete", methods=["POST"])
@login_required
def system_backup_delete(name):
    """Supprime une sauvegarde via l'agent root (le dossier des sauvegardes n'est pas accessible en écriture au portail)."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    if not BACKUP_NAME_RE.fullmatch(name) or name not in {b["name"] for b in list_backups()}:
        flash(_("Sauvegarde introuvable."), "error")
    elif locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request("delete-backup", {"name": name}, user=g.user["username"] if g.get("user") else "")
            audit("systeme.sauvegarde.suppression", name)
            flash(_("Sauvegarde supprimée : %(name)s.", name=name), "ok")
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system", _anchor="sauvegardes")


@app.route("/system/restore", methods=["POST"])
@login_required
def system_restore():
    """Restaure une sauvegarde via l'agent root : base, comptes, clés et dépôts, puis le serveur redémarre."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    name = request.form.get("archive", "").strip()
    if not BACKUP_NAME_RE.fullmatch(name) or name not in {b["name"] for b in list_backups()}:
        flash(_("Sauvegarde introuvable."), "error")
    elif locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request("restore", {"archive": name}, user=g.user["username"] if g.get("user") else "")
            audit("systeme.restauration", name)
            flash(_("Restauration lancée : le serveur va redémarrer. Reconnectez-vous dans un instant."), "ok")
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system", _anchor="sauvegardes")


UPLOAD_SPOOL = os.path.join(config.STATE, "restore-uploads")
MAX_UPLOAD = 4 * 1024 ** 3                        # 4 Gio : une sauvegarde (hors caches) reste bien en dessous


def offsite_panel():
    from . import offsite
    return offsite.panel() if config.SERVER_UPDATES and not config.WINDOWS else None


def backup_keep():
    """Nombre de sauvegardes conservées (BACKUP_KEEP de loco.conf), relu à chaque affichage. 3 par défaut."""
    try:
        with open(os.path.join(config.ETC, "loco.conf"), encoding="utf-8") as f:
            match = re.search(r'^BACKUP_KEEP="?(\d+)"?', f.read(), re.M)
        return int(match.group(1)) if match else 3
    except (OSError, ValueError):
        return 3


@app.route("/system/restore/upload", methods=["POST"])
@login_required
def system_restore_upload():
    """Téléverse une archive .tar.gz, la vérifie (sauvegarde loco valide), puis lance la restauration via l'agent."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    try:
        request.max_content_length = MAX_UPLOAD   # borne la taille lue (Werkzeug lève 413 au-delà)
    except AttributeError:
        pass
    if locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
        return back("system", _anchor="sauvegardes")
    if not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
        return back("system", _anchor="sauvegardes")
    upload = request.files.get("archive")
    if upload is None or not upload.filename:
        flash(_("Choisissez un fichier de sauvegarde (.tar.gz)."), "error")
        return back("system", _anchor="sauvegardes")
    os.makedirs(UPLOAD_SPOOL, exist_ok=True)
    name = secrets.token_hex(16) + ".tar.gz"
    path = os.path.join(UPLOAD_SPOOL, name)
    from .backup import verify
    try:
        upload.save(path)
        verify(path, data=config.DATA, etc=config.ETC, state=config.STATE)
    except ValueError as e:
        _discard(path)
        flash(_("Archive refusée : %(reason)s", reason=str(e)), "error")
        return back("system", _anchor="sauvegardes")
    except OSError:
        _discard(path)
        flash(_("Le téléversement a échoué."), "error")
        return back("system", _anchor="sauvegardes")
    try:
        locoadmin.request("restore", {"upload": name}, user=g.user["username"] if g.get("user") else "")
        audit("systeme.restauration", _("fichier téléversé"))
        flash(_("Sauvegarde vérifiée. Restauration lancée : le serveur va redémarrer. Reconnectez-vous dans un instant."), "ok")
    except ValueError as e:
        _discard(path)
        flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system", _anchor="sauvegardes")


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


@app.route("/system/backup-retention", methods=["POST"])
@login_required
def system_backup_retention():
    """Nombre de sauvegardes quotidiennes conservées (BACKUP_KEEP), appliqué par l'agent root."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    keep = request.form.get("keep", "").strip()
    if not keep.isdigit() or not 1 <= int(keep) <= 60:
        flash(_("Nombre de sauvegardes invalide (1 à 60)."), "error")
    elif locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request("set-backup-keep", {"keep": int(keep)}, user=g.user["username"] if g.get("user") else "")
            audit("systeme.sauvegarde.retention", keep)
            flash(_("Conservation des sauvegardes : %(n)s. Les plus anciennes au-delà sont supprimées.", n=keep), "ok")
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return back("system", _anchor="sauvegardes")


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
    return back("system", _anchor="reglages")


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
