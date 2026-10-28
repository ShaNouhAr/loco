"""Page Système, contrôle de santé et métriques Prometheus."""

import datetime
import glob
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
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
            "source": "", "stratum": None, "offset_ms": None, "servers": [], "sources": [], "only": False,
            "configured": [], "container": _virt()[1], "fresh": False}
    try:
        info["timezone"] = os.readlink("/etc/localtime").split("zoneinfo/", 1)[-1]
    except OSError:
        pass
    data, path = None, getattr(locoadmin, "TIME_STATUS", None) if config.SERVER_UPDATES else None
    if isinstance(path, str):                             # jamais un entier : open(1) fermerait stdout
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    if data and time.time() - data.get("at", 0) < 90:     # état frais mesuré par l'agent root
        info["fresh"], info["container"], info["ntp"] = True, data.get("container", info["container"]), bool(data.get("enabled"))
        for key in ("synchronized", "source", "stratum", "offset_ms", "servers", "sources", "only", "configured"):
            info[key] = data.get(key, info[key])
    elif shutil.which("timedatectl"):                     # repli non privilégié (hôtes systemd)
        try:
            out = subprocess.run(["timedatectl", "show", "-p", "NTP", "-p", "NTPSynchronized"],
                                 capture_output=True, text=True, timeout=5).stdout
            values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            info["ntp"], info["synchronized"] = values.get("NTP") == "yes", values.get("NTPSynchronized") == "yes"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    # Serveurs saisis dont aucun n'est utilisé : l'heure vient alors des serveurs de secours (ou de nulle part).
    info["unused"] = [c["name"] for c in info["configured"] if c["state"] != "used"] \
        if info["ntp"] and info["configured"] and not any(c["state"] == "used" for c in info["configured"]) else []
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


def _endpoint(url):
    """(hôte, port) d'une URL ; port par défaut selon le schéma. None si l'URL est locale ou illisible."""
    if re.match(r"(file|cdrom|copy|mirror\+file):|/", url):
        return None
    try:
        u = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
        host, port = (u.hostname or "").lower(), u.port or (80 if u.scheme == "http" else 443)
    except ValueError:
        return None
    if not host or host in ("localhost", config.HOST.lower()) or host.startswith("127."):
        return None
    return host, port


def _os_repositories():
    """Dépôts du système du serveur lui-même (mises à jour du système) : apk, APT ou dnf."""
    urls = []
    try:
        with open("/etc/apk/repositories") as f:
            urls += [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        pass
    apt = ["/etc/apt/sources.list"] + sorted(glob.glob("/etc/apt/sources.list.d/*.list"))
    for path in apt + sorted(glob.glob("/etc/apt/sources.list.d/*.sources")):
        try:
            with open(path) as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if path.endswith(".sources") and line.lower().startswith("uris:"):
                        urls += line.split(":", 1)[1].split()
                    elif re.match(r"deb(-src)?\s", line):
                        urls += [w for w in line.split() if "://" in w][:1]
        except OSError:
            pass
    for path in sorted(glob.glob("/etc/yum.repos.d/*.repo")):
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        for section in re.split(r"^\[", text, flags=re.M):
            if re.search(r"^enabled\s*=\s*0", section, re.M):
                continue
            urls += re.findall(r"^(?:baseurl|mirrorlist|metalink)\s*=\s*(\S+)", section, re.M)
    return urls


def _acng_hosts():
    """Miroirs réellement demandés par les postes au cache apt-cacher-ng (un dossier par miroir)."""
    remap = {"debrep": "deb.debian.org", "secdeb": "security.debian.org", "uburep": "archive.ubuntu.com"}
    try:
        names = os.listdir("/var/cache/apt-cacher-ng")
    except OSError:
        return []
    return sorted({remap.get(n, n) for n in names if n in remap or ("." in n and not n.startswith("_"))})


def firewall_egress(time_info=None):
    """Flux à autoriser en SORTIE du serveur loco vers Internet, selon la configuration et les fonctionnalités actives.

    Chaque ligne : service, hôtes de destination, ports, protocole. Les hôtes viennent de la configuration réelle
    (dépôts du système, source des mises à jour, catalogue, alertes, sauvegarde hors site) quand c'est possible."""
    from . import alerts, features, offsite
    from .wupdate import read_wu_domains
    off, rows = features.disabled(), []

    def add(service, endpoints, proto="TCP", note="", essential=False):
        endpoints = [e for e in endpoints if e]
        if not endpoints:
            return
        hosts = list(dict.fromkeys(h for h, _p in endpoints))
        ports = sorted({p for _h, p in endpoints}, key=lambda p: (not str(p).isdigit(), int(p) if str(p).isdigit() else 0))
        rows.append({"service": service, "hosts": hosts, "ports": ", ".join(str(p) for p in ports), "proto": proto,
                     "note": note, "essential": essential})

    # Résolution de noms : indispensable à tout le reste (souvent un résolveur interne).
    resolvers = []
    for path in ("/run/systemd/resolve/resolv.conf", "/etc/resolv.conf"):
        try:
            with open(path) as f:
                resolvers = [m for m in re.findall(r"^nameserver\s+(\S+)", f.read(), re.M) if not m.startswith("127.")]
        except OSError:
            continue
        if resolvers:
            break
    add(_("Résolution DNS"), [(r, 53) for r in resolvers], "UDP/TCP", _("souvent un résolveur interne"), True)

    # Heure : serveurs NTP réglés, ou ceux de la distribution.
    if time_info and not time_info.get("container"):
        servers = time_info.get("servers") or []
        add(_("Synchronisation de l'heure (NTP)"), [(h, 123) for h in servers or ["pool.ntp.org"]], "UDP",
            "" if servers else _("serveurs par défaut de la distribution (pool.ntp.org ou équivalent)"))

    # Mises à jour du système du serveur et de loco lui-même.
    add(_("Mises à jour du système du serveur"), [_endpoint(u) for u in _os_repositories()])
    if config.SERVER_UPDATES:
        src = locoadmin.source()
        if "://" in src:
            add(_("Mises à jour de loco (GitLab)"), [_endpoint(src)], essential=False)
        else:
            add(_("Mises à jour de loco (GitHub)"), [(h, 443) for h in ("api.github.com", "github.com",
                "release-assets.githubusercontent.com", "objects.githubusercontent.com")])

    # Caches et catalogues, selon les fonctionnalités actives.
    if "apt" not in off:
        seen = _acng_hosts() if not config.APT_NGINX else []
        hosts = seen or ["deb.debian.org", "security.debian.org", "archive.ubuntu.com", "security.ubuntu.com"]
        add(_("Cache de paquets Linux (miroirs des postes)"), [(h, 80) for h in hosts], note=(
            _("miroirs vus dans le cache ; ajoutez tout autre miroir configuré sur les postes") if seen else
            _("miroirs par défaut ; ajoutez ceux configurés sur les postes")))
    if "rpm" not in off:
        add(_("Cache de paquets RPM (Rocky, AlmaLinux, EPEL)"), [_endpoint(u) for _n, u in config.RPM_UPSTREAMS.values()])
    if "flatpak" not in off:
        add(_("Cache Flathub"), [_endpoint(config.FLATHUB_URL)])
    if "wu" not in off:
        add(_("Cache Windows Update"), [(d if d.startswith("*.") else "*." + d, p)
                                        for d in read_wu_domains() for p in (80, 443)],
            note=_("domaines et sous-domaines"))
    if "windows_apps" not in off:
        installers = [_endpoint(r[0]) for r in db().execute(
            "SELECT DISTINCT source_url FROM win_apps WHERE source_url != ''").fetchall()]
        if any(e and e[0] == "github.com" for e in installers):        # les fichiers GitHub sont servis ailleurs
            installers += [("release-assets.githubusercontent.com", 443), ("objects.githubusercontent.com", 443)]
        add(_("Catalogue de logiciels Windows (winget et éditeurs)"),
            [("cdn.winget.microsoft.com", 443)] + sorted({e for e in installers if e}),
            note=_("hôtes des installeurs déjà présents au catalogue ; un nouveau logiciel peut en ajouter"))

    # Notifications et sauvegarde hors site : seulement si elles sont configurées.
    if "alerts" not in off:
        try:
            cfg = alerts.load()
        except Exception:
            cfg = None
        if cfg and cfg["email"]["enabled"] and cfg["email"]["host"]:
            add(_("Alertes par e-mail (SMTP)"), [(cfg["email"]["host"].lower(), int(cfg["email"]["port"] or 587))])
        if cfg and cfg["webhook"]["enabled"] and cfg["webhook"]["url"]:
            add(_("Alertes par webhook"), [_endpoint(cfg["webhook"]["url"])])
    try:
        bk = offsite.load()
    except Exception:
        bk = None
    if bk and bk["type"] == "s3" and bk["s3"]["endpoint"]:
        scheme = "https://" if bk["s3"].get("use_ssl", True) else "http://"
        add(_("Sauvegarde hors site (S3)"), [_endpoint(scheme + bk["s3"]["endpoint"].split("://")[-1])])
    elif bk and bk["type"] == "sftp" and bk["sftp"]["host"]:
        add(_("Sauvegarde hors site (SFTP)"), [(bk["sftp"]["host"].lower(), int(bk["sftp"]["port"] or 22))])
    return rows


@app.route("/system")
@login_required
def system():
    services = [(unit, desc, service_active(unit)) for unit, desc in UNITS]
    if not (config.CONTAINER or config.ALPINE or config.WINDOWS) and os.path.exists("/etc/squid/squid.conf"):
        services.append(("squid", "Proxy cache Windows Update (sans DNS)", service_active("squid")))
    backups = list_backups()
    time_info = server_time() if config.SERVER_UPDATES and not config.WINDOWS else None
    return render_template(
        "system.html",
        services=services, stopped=[unit for unit, _, active in services if not active],
        firewall=firewall_ports(),
        egress=firewall_egress(time_info),
        last_backup=_age(backups[0]["mtime"]) if backups else None,
        cert=cert_info(os.path.join(config.TLS_DIR, "server.crt")),
        ca=cert_info(os.path.join(config.TLS_DIR, "ca.crt")),
        imported=cert_info(os.path.join(config.TLS_DIR, "imported.crt")) if config.SERVER_UPDATES and not config.WINDOWS else None,
        tls_custom=tls_custom(), tls_manage=config.SERVER_UPDATES and not config.WINDOWS,
        tls=tls_state() if config.SERVER_UPDATES and not config.WINDOWS and not tls_custom() else None,
        readiness=trust_readiness() if config.SERVER_UPDATES and not config.WINDOWS and not tls_custom() else None,
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
        time_info=time_info,
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


# --------------------------------------------------------------------------- certificat HTTPS (agent root)
TLS_SPOOL = os.path.join(config.STATE, "tls-uploads")
MAX_TLS_FILE = 64 * 1024


def tls_custom():
    """Certificat fourni à l'installation (--tls-cert) : pas d'autorité loco pour le renouveler."""
    return os.path.exists(os.path.join(config.TLS_DIR, "custom"))


def _agent_action(action, params, audit_action, audit_detail, done):
    """Transmet une action à l'agent root (le retour se fait sur l'onglet de la page Système concerné)."""
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    if locoadmin.running_job():
        flash(_("Une opération est déjà en cours sur le serveur. Réessayez à la fin."), "error")
    elif not locoadmin.agent_running():
        flash(_("L'agent d'administration ne répond pas : sur le serveur, %(cmd)s.", cmd="systemctl restart loco-agent"), "error")
    else:
        try:
            locoadmin.request(action, params, user=g.user["username"] if g.get("user") else "")
            audit(audit_action, audit_detail)
            flash(done, "ok")
            return True
        except ValueError as e:
            flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
    return False


def check_import(cert_pem, key_pem, host):
    """Vérification immédiate d'un certificat à importer : (noms DNS, date d'expiration) ou ValueError lisible.
    L'agent root refait les mêmes contrôles avant de l'installer."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        certs = x509.load_pem_x509_certificates(cert_pem)
    except (ValueError, AttributeError):
        try:
            certs = [x509.load_pem_x509_certificate(cert_pem)]
        except ValueError:
            raise ValueError(_("certificat illisible : fichier PEM attendu (.crt, .pem)")) from None
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        raise ValueError(_("clé privée illisible : fichier PEM non chiffré attendu (.key)")) from None
    leaf = certs[0]
    spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if leaf.public_key().public_bytes(*spki) != key.public_key().public_bytes(*spki):
        raise ValueError(_("la clé ne correspond pas au certificat"))
    end = getattr(leaf, "not_valid_after_utc", None) or leaf.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    if end <= datetime.datetime.now(datetime.timezone.utc):
        raise ValueError(_("certificat expiré"))
    try:
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if not names:
        raise ValueError(_("aucun nom DNS dans le certificat : il doit couvrir le nom par lequel vous ouvrez le portail"))
    covered = [n for n in names if n.lower() == host.lower() or (n.startswith("*.") and host.lower().endswith(n[1:].lower())
                                                                   and host.count(".") == n.count("."))]
    if covered:
        raise ValueError(_("il couvre %(host)s, l'adresse utilisée par les agents du parc : ils perdraient la connexion. "
                           "Importez un certificat pour un nom DNS distinct.", host=host))
    return names, end


@app.route("/system/certificate/renew", methods=["POST"])
@login_required
def system_cert_renew():
    if tls_custom():
        abort(409)
    _agent_action("renew-cert", {}, "systeme.certificat.renouvellement", "",
                  _("Renouvellement du certificat lancé : rechargez la page dans quelques secondes."))
    return back("system", _anchor="certificat")


@app.route("/system/certificate/import", methods=["POST"])
@login_required
def system_cert_import():
    if config.WINDOWS or not config.SERVER_UPDATES:
        abort(404)
    files = request.files.get("certificate"), request.files.get("key")
    if not all(f and f.filename for f in files):
        flash(_("Choisissez le certificat (.crt ou .pem) et sa clé privée (.key)."), "error")
        return back("system", _anchor="certificat")
    cert_pem, key_pem = (f.read(MAX_TLS_FILE + 1) for f in files)
    if max(len(cert_pem), len(key_pem)) > MAX_TLS_FILE:
        flash(_("Fichier trop volumineux pour un certificat."), "error")
        return back("system", _anchor="certificat")
    try:
        agents = {h for h in (config.HOST, urllib.parse.urlsplit(config.HTTPS_URL).hostname) if h}
        for host in sorted(agents):
            names, _end = check_import(cert_pem, key_pem, host)
    except ValueError as e:
        flash(_("Certificat refusé : %(reason)s.", reason=str(e)), "error")
        return back("system", _anchor="certificat")
    os.makedirs(TLS_SPOOL, mode=0o700, exist_ok=True)
    upload = secrets.token_hex(16)
    paths = [os.path.join(TLS_SPOOL, upload + ext) for ext in (".crt", ".key")]
    for path, data in zip(paths, (cert_pem, key_pem)):
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
    if not _agent_action("import-cert", {"upload": upload}, "systeme.certificat.import", ", ".join(names),
                         _("Import du certificat lancé pour %(names)s : rechargez la page dans quelques secondes.",
                           names=", ".join(names))):
        for path in paths:
            _discard(path)
    return back("system", _anchor="certificat")


@app.route("/system/certificate/imported/remove", methods=["POST"])
@login_required
def system_cert_remove():
    _agent_action("remove-imported-cert", {}, "systeme.certificat.retrait", "",
                  _("Retrait du certificat importé lancé."))
    return back("system", _anchor="certificat")


# --------------------------------------------------------------------------- autorité de certification au choix
# Le portail lit et vérifie les fichiers (PEM, DER, PKCS#7, PFX) pour des messages clairs, les dépose en PEM dans le
# spool ; l'agent root refait les contrôles (chaîne vérifiée par OpenSSL) avant d'y toucher.
PEM_CERT_RE = re.compile(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def _load_certs(data):
    """Certificats d'un fichier PEM (un ou plusieurs), DER (.cer) ou PKCS#7 (.p7b) ; liste vide si illisible."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs7
    certs = []
    for block in PEM_CERT_RE.findall(data or b""):
        try:
            certs.append(x509.load_pem_x509_certificate(block))
        except ValueError:
            return []
    if certs:
        return certs
    for loader in ((lambda d: [x509.load_der_x509_certificate(d)]), pkcs7.load_der_pkcs7_certificates,
                   pkcs7.load_pem_pkcs7_certificates):
        try:
            return list(loader(data))
        except (ValueError, TypeError):
            continue
    return []


def _is_ca(cert):
    from cryptography import x509
    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


def _cert_summary(cert):
    """Résumé d'un certificat chargé : nom, émetteur, échéance, empreinte."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID
    end = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"name": cn[0].value if cn else cert.subject.rfc4514_string(), "subject": cert.subject.rfc4514_string(),
            "root": cert.subject == cert.issuer, "end": end.strftime("%d/%m/%Y"),
            "days": (end - datetime.datetime.now(datetime.timezone.utc)).days,
            "sha256": ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))}


def _covers(cert, host):
    """Le certificat couvre-t-il l'adresse des agents (IP ou nom DNS, joker compris) ?"""
    from cryptography import x509
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    try:
        return ipaddress.ip_address(host) in san.get_values_for_type(x509.IPAddress)
    except ValueError:
        host = host.lower()
        return any(n.lower() == host or (n.startswith("*.") and host.endswith(n[1:].lower()) and host.count(".") == n.count("."))
                   for n in san.get_values_for_type(x509.DNSName))


def _tls_file(name):
    try:
        with open(os.path.join(config.TLS_DIR, name), "rb") as f:
            return f.read()
    except OSError:
        return b""


def tls_state():
    """Autorité en service, autorité d'entreprise importée, certificat préparé et demande en attente."""
    from cryptography import x509
    state = {"mode": "enterprise" if _tls_file("mode").strip() == b"enterprise" else "loco",
             "authority": [_cert_summary(c) for c in _load_certs(_tls_file("enterprise-ca.pem"))],
             "prepared": cert_info(os.path.join(config.TLS_DIR, "enterprise.crt")) if _tls_file("enterprise.crt") else None,
             "request": None, "host": config.HOST}
    csr = _tls_file("enterprise.csr")
    if csr:
        try:
            req = x509.load_pem_x509_csr(csr)
            san = req.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            state["request"] = {"pem": csr.decode().strip(),
                                "names": [str(n) for n in san.get_values_for_type(x509.IPAddress)] + san.get_values_for_type(x509.DNSName)}
        except (ValueError, x509.ExtensionNotFound):
            state["request"] = {"pem": csr.decode(errors="replace").strip(), "names": []}
    return state


def trust_readiness():
    """Postes qui font déjà confiance au faisceau d'autorités en vigueur (reçu à leur dernier contact)."""
    from . import fleet
    pem = fleet.trust_pem()
    if not pem:
        return None
    sha = hashlib.sha256(pem.encode()).hexdigest()
    rows = db().execute("SELECT id, hostname, uid, agent_version, last_seen, os_family, trust_sha256 FROM devices "
                        "WHERE state='active' ORDER BY hostname COLLATE NOCASE").fetchall()
    waiting = [r for r in rows if r["trust_sha256"] != sha]
    return {"ready": len(rows) - len(waiting), "total": len(rows), "waiting": waiting[:60], "more": max(0, len(waiting) - 60)}


def _tls_upload(field):
    f = request.files.get(field)
    if not (f and f.filename):
        return None
    data = f.read(MAX_TLS_FILE + 1)
    if len(data) > MAX_TLS_FILE:
        raise ValueError(_("fichier trop volumineux pour un certificat"))
    return data


def _spool_tls(upload, ext, data):
    os.makedirs(TLS_SPOOL, mode=0o700, exist_ok=True)
    path = os.path.join(TLS_SPOOL, upload + ext)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)
    return path


def _tls_routes_allowed():
    if config.WINDOWS or not config.SERVER_UPDATES or tls_custom():
        abort(404)


@app.route("/system/certificate/authority", methods=["POST"])
@login_required
def system_ca_import():
    """Chaîne de l'autorité d'entreprise (racine, intermédiaires) : ajoutée au faisceau que les agents épinglent."""
    _tls_routes_allowed()
    from cryptography.hazmat.primitives.serialization import Encoding
    try:
        data = _tls_upload("authority")
        if not data:
            raise ValueError(_("choisissez le certificat de l'autorité (.cer, .crt, .pem ou .p7b)"))
        certs = _load_certs(data)
        if not certs:
            raise ValueError(_("fichier illisible : certificat PEM, DER (.cer) ou chaîne PKCS#7 (.p7b) attendu"))
        if not all(_is_ca(c) for c in certs):
            raise ValueError(_("le fichier contient un certificat qui n'est pas une autorité : importez la racine (et les "
                               "autorités intermédiaires), pas le certificat d'un serveur"))
    except ValueError as e:
        flash(_("Autorité refusée : %(reason)s.", reason=str(e)), "error")
        return back("system", _anchor="certificat")
    names = ", ".join(_cert_summary(c)["name"] for c in certs)
    upload = secrets.token_hex(16)
    path = _spool_tls(upload, ".pem", b"".join(c.public_bytes(Encoding.PEM) for c in certs))
    if not _agent_action("import-ca", {"upload": upload}, "systeme.autorite.import", names,
                         _("Autorité %(names)s importée : elle est transmise à chaque poste à son prochain contact.",
                           names=names)):
        _discard(path)
    return back("system", _anchor="certificat")


@app.route("/system/certificate/authority/remove", methods=["POST"])
@login_required
def system_ca_remove():
    _tls_routes_allowed()
    _agent_action("remove-ca", {}, "systeme.autorite.retrait", "", _("Autorité d'entreprise retirée."))
    return back("system", _anchor="certificat")


@app.route("/system/certificate/request", methods=["POST"])
@login_required
def system_csr():
    """Demande de certificat (CSR) préparée par le serveur : la clé privée ne le quitte jamais."""
    _tls_routes_allowed()
    try:
        params = locoadmin.tls_params("ca-csr", {"names": request.form.get("names", "")})
    except ValueError:
        flash(_("Noms DNS invalides : séparez-les par des espaces, huit au plus."), "error")
        return back("system", _anchor="certificat")
    _agent_action("ca-csr", params, "systeme.autorite.demande", params["names"],
                  _("Demande de certificat en préparation : rechargez la page dans quelques secondes pour la télécharger."))
    return back("system", _anchor="certificat")


@app.route("/system/certificate/request.csr")
@login_required
def system_csr_download():
    _tls_routes_allowed()
    path = os.path.join(config.TLS_DIR, "enterprise.csr")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="application/pkcs10", as_attachment=True,
                     download_name=f"loco-{socket.gethostname()}.csr")


@app.route("/system/certificate/enterprise", methods=["POST"])
@login_required
def system_enterprise_import():
    """Certificat émis par l'autorité d'entreprise : pour la demande en attente, avec sa clé, ou en fichier PFX."""
    _tls_routes_allowed()
    from cryptography.hazmat.primitives import serialization
    try:
        pfx, crt, key = _tls_upload("pfx"), _tls_upload("certificate"), _tls_upload("key")
        key_pem = None
        if pfx:
            from cryptography.hazmat.primitives.serialization import pkcs12
            try:
                k, leaf, extra = pkcs12.load_key_and_certificates(pfx, request.form.get("password", "").encode() or None)
            except (ValueError, TypeError):
                raise ValueError(_("fichier PFX illisible ou mot de passe incorrect")) from None
            if not (k and leaf):
                raise ValueError(_("le fichier PFX doit contenir le certificat et sa clé privée"))
            certs = [leaf] + list(extra or [])
            key_pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
        elif crt:
            certs = _load_certs(crt)
            if not certs:
                raise ValueError(_("certificat illisible : PEM, DER (.cer) ou PKCS#7 (.p7b) attendu"))
            if key:
                try:
                    k = serialization.load_pem_private_key(key, password=None)
                except (ValueError, TypeError):
                    raise ValueError(_("clé privée illisible : fichier PEM non chiffré attendu (.key)")) from None
                key_pem = key
        else:
            raise ValueError(_("choisissez le certificat émis (.cer, .crt, .pem, .p7b) ou un fichier PFX"))
        leaf = next((c for c in certs if not _is_ca(c)), None)
        if leaf is None:
            raise ValueError(_("aucun certificat de serveur dans le fichier (uniquement des autorités)"))
        if _cert_summary(leaf)["days"] < 0:
            raise ValueError(_("certificat expiré"))
        if config.HOST and not _covers(leaf, config.HOST):
            raise ValueError(_("il ne couvre pas %(host)s, l'adresse utilisée par les agents du parc", host=config.HOST))
        if key_pem is not None:
            spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            if leaf.public_key().public_bytes(*spki) != k.public_key().public_bytes(*spki):
                raise ValueError(_("la clé ne correspond pas au certificat"))
        elif not os.path.exists(os.path.join(config.TLS_DIR, "enterprise.csr")):
            raise ValueError(_("aucune demande en attente : joignez la clé privée ou un fichier PFX"))
    except ValueError as e:
        flash(_("Certificat refusé : %(reason)s.", reason=str(e)), "error")
        return back("system", _anchor="certificat")
    upload = secrets.token_hex(16)
    chain = [leaf] + [c for c in certs if c is not leaf]
    paths = [_spool_tls(upload, ".crt", b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain))]
    if key_pem is not None:
        paths.append(_spool_tls(upload, ".key", key_pem))
    name = _cert_summary(leaf)["name"]
    if not _agent_action("import-enterprise-cert", {"upload": upload, "key": "1" if key_pem is not None else ""},
                         "systeme.autorite.certificat", name,
                         _("Certificat %(name)s importé : vérification de sa chaîne en cours, rechargez la page dans "
                           "quelques secondes.", name=name)):
        for path in paths:
            _discard(path)
    return back("system", _anchor="certificat")


@app.route("/system/certificate/mode", methods=["POST"])
@login_required
def system_tls_mode():
    """Bascule le certificat du portail sur l'autorité choisie (retour à loco toujours possible)."""
    _tls_routes_allowed()
    mode = request.form.get("mode", "")
    if mode not in ("loco", "enterprise"):
        abort(400)
    _agent_action("set-tls-mode", {"mode": mode}, "systeme.autorite.bascule", mode,
                  _("Bascule vers l'autorité d'entreprise lancée : rechargez la page dans quelques secondes.")
                  if mode == "enterprise" else _("Retour à l'autorité loco lancé : rechargez la page dans quelques secondes."))
    return back("system", _anchor="certificat")


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
              "ntp_server": request.form.get("ntp_server", "").strip(),
              "ntp_only": "1" if request.form.get("ntp_only") == "1" else ""}
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
