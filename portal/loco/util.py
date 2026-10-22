"""Fonctions utilitaires sans état."""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import redirect, request, url_for
from werkzeug.utils import secure_filename

from . import config
from .i18n import _


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


UNITS = {"fr": ("o", "Ko", "Mo", "Go", "To"), "en": ("B", "KB", "MB", "GB", "TB"),
         "es": ("B", "KB", "MB", "GB", "TB")}
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def human(n):
    from .i18n import locale
    lang = locale()
    n = float(n or 0)
    units = UNITS.get(lang, UNITS["fr"])
    for unit in units:
        if n < 1024 or unit == units[-1]:
            if unit == units[0]:
                return f"{n:.0f} {unit}"
            text = f"{n:.2f}" if n < 10 else f"{n:.1f}"
            return (text if lang == "en" else text.replace(".", ",")) + f" {unit}"
        n /= 1024
    return ""


def date_fr(value):
    """« 2026-10-12 20:57 » -> « 12/10 20:57 » (en anglais : « Oct 12 20:57 »)."""
    from .i18n import locale
    m = re.match(r"\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2})", value or "")
    if not m:
        return value or ""
    if locale() == "en":
        return f"{MONTHS_EN[int(m.group(1)) - 1]} {int(m.group(2))} {m.group(3)}"
    return f"{m.group(2)}/{m.group(1)} {m.group(3)}"


def explain(exc):
    """Message d'erreur lisible pour un téléchargement raté."""
    if isinstance(exc, urllib.error.HTTPError):
        return _("Le serveur a répondu HTTP %(code)s (%(reason)s).", code=exc.code, reason=exc.reason)
    if isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason)
        if "Name or service not known" in reason or "Temporary failure in name resolution" in reason:
            return _("Adresse introuvable : vérifiez le lien.")
        if "timed out" in reason:
            return _("Le serveur n'a pas répondu à temps.")
        if "CERTIFICATE" in reason.upper():
            return _("Certificat HTTPS invalide sur le site source.")
        return _("Connexion impossible : %(reason)s", reason=reason)
    if isinstance(exc, TimeoutError):
        return _("Le serveur n'a pas répondu à temps.")
    return str(exc)[:300] or exc.__class__.__name__


def slugify(text):
    text = text.lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"), ("à", "a"), ("â", "a"), ("ç", "c"),
                 ("ô", "o"), ("î", "i"), ("ï", "i"), ("ù", "u"), ("û", "u")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:63]


def kind_for(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if ext in ("exe", "msi", "msu", "ps1") else "file"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Conteneur : processus lancés par supervisord, reconnus à leur ligne de commande.
CONTAINER_PROCESSES = {"nginx": "nginx: master", "apt-cacher-ng": "/usr/sbin/apt-cacher-ng",
                       "loco-cron": "loco-cron"}


def process_running(marker):
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if marker in cmdline:
            return True
    return False


def windows_status():
    """État publié toutes les quelques secondes par le service Windows (locowin.py)."""
    try:
        with open(os.path.join(config.STATE, "service-status.json")) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return {}
    return status if time.time() - status.get("updated", 0) < 60 else {}


def service_active(name):
    if config.WINDOWS:
        if name == "loco-portal":
            return True
        # Sous Windows, le cache APT est assuré par nginx (proxy sur le port 3142).
        name = "nginx" if name == "apt-cacher-ng" else name
        return bool(windows_status().get("services", {}).get(name))
    if config.CONTAINER or config.ALPINE:
        if name == "loco-portal":
            return True
        if config.ALPINE and name == "loco-agent":
            import locoadmin
            return locoadmin.agent_running()
        if config.ALPINE and name == "apt-cacher-ng":
            name = "nginx"     # Alpine : cache APT servi par nginx
        return name in CONTAINER_PROCESSES and process_running(CONTAINER_PROCESSES[name])
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def read_stats():
    try:
        with open(config.STATS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def disk_usage():
    d = shutil.disk_usage(config.DATA)
    return {"total": d.total, "used": d.total - d.free, "free": d.free,
            "pct": round(100 * (d.total - d.free) / d.total) if d.total else 0}


def mem_info():
    """Mémoire vive du serveur loco (/proc/meminfo), en octets. {} si indisponible (p. ex. serveur Windows)."""
    if config.WINDOWS:
        return {}
    try:
        values = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable", "MemFree"):
                    values[key] = int(rest.split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        used = max(0, total - available)
        return {"total": total, "used": used, "available": available,
                "pct": round(100 * used / total) if total else 0}
    except (OSError, ValueError, IndexError):
        return {}


def _cpu_times():
    with open("/proc/stat") as f:
        fields = [int(x) for x in f.readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)   # idle + iowait
    return sum(fields), idle


def cpu_usage(interval=0.12):
    """Charge processeur du serveur loco : pourcentage (court échantillon sur /proc/stat), charge sur 1 min, cœurs.
    {} si indisponible (p. ex. serveur Windows)."""
    if config.WINDOWS:
        return {}
    try:
        total1, idle1 = _cpu_times()
        time.sleep(interval)
        total2, idle2 = _cpu_times()
        delta_total, delta_idle = total2 - total1, idle2 - idle1
        pct = round(100 * (delta_total - delta_idle) / delta_total) if delta_total > 0 else 0
        return {"pct": max(0, min(100, pct)), "load1": round(os.getloadavg()[0], 2), "cores": os.cpu_count() or 1}
    except (OSError, ValueError, IndexError, ZeroDivisionError):
        return {}


PROC_NET_DEV, PROC_NET_ROUTE = "/proc/net/dev", "/proc/net/route"
VIRTUAL_IFACES = ("lo", "veth", "docker", "br-", "virbr", "vnet", "tap", "tun", "fwbr", "fwpr", "fwln")


def net_counters():
    """Octets reçus et émis depuis le démarrage par l'interface de la route par défaut (à défaut, par les interfaces
    physiques) : le navigateur en déduit le débit entre deux relevés. None si le système ne les fournit pas."""
    if config.WINDOWS:
        try:
            return _net_counters_windows()
        except (OSError, AttributeError, ValueError):
            return None
    rows = {}
    try:
        with open(PROC_NET_DEV) as f:
            for line in f.readlines()[2:]:
                name, _, data = line.partition(":")
                fields = data.split()
                if len(fields) >= 9:
                    rows[name.strip()] = (int(fields[0]), int(fields[8]))
    except (OSError, ValueError):
        return None
    default = set()
    try:
        with open(PROC_NET_ROUTE) as f:
            for line in f.readlines()[1:]:
                fields = line.split()
                if len(fields) > 2 and fields[1] == "00000000":          # destination 0.0.0.0 : route par défaut
                    default.add(fields[0])
    except OSError:
        pass
    chosen = sorted(default & rows.keys()) or sorted(n for n in rows if not n.startswith(VIRTUAL_IFACES))
    if not chosen:
        return None
    return {"iface": ", ".join(chosen), "rx": sum(rows[n][0] for n in chosen), "tx": sum(rows[n][1] for n in chosen)}


def _net_counters_windows():
    """Compteurs 64 bits de l'interface vers Internet (GetBestInterface puis GetIfEntry2, iphlpapi)."""
    import ctypes
    import socket

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_uint8 * 8)]

    class MIB_IF_ROW2(ctypes.Structure):                                   # netioapi.h, 1352 octets en x64
        _fields_ = [("luid", ctypes.c_uint64), ("index", ctypes.c_uint32), ("guid", GUID),
                    ("alias", ctypes.c_uint16 * 257), ("description", ctypes.c_uint16 * 257),
                    ("phys_len", ctypes.c_uint32), ("phys", ctypes.c_uint8 * 32), ("perm_phys", ctypes.c_uint8 * 32),
                    ("mtu", ctypes.c_uint32), ("type", ctypes.c_uint32), ("tunnel", ctypes.c_int32),
                    ("media", ctypes.c_int32), ("medium", ctypes.c_int32), ("access", ctypes.c_int32),
                    ("direction", ctypes.c_int32), ("flags", ctypes.c_uint8), ("oper", ctypes.c_int32),
                    ("admin", ctypes.c_int32), ("connect", ctypes.c_int32), ("network_guid", GUID),
                    ("connection", ctypes.c_int32), ("tx_speed", ctypes.c_uint64), ("rx_speed", ctypes.c_uint64),
                    ("in_octets", ctypes.c_uint64)] + [(f"in_{i}", ctypes.c_uint64) for i in range(8)] + [
                    ("out_octets", ctypes.c_uint64)] + [(f"out_{i}", ctypes.c_uint64) for i in range(8)]

    if ctypes.sizeof(MIB_IF_ROW2) != 1352:
        return None
    iphlpapi = ctypes.WinDLL("iphlpapi")
    index = ctypes.c_uint32()
    dest = int.from_bytes(socket.inet_aton("1.1.1.1"), "little")             # adresse IPv4 dans l'ordre réseau
    if iphlpapi.GetBestInterface(ctypes.c_uint32(dest), ctypes.byref(index)) != 0:
        return None
    row = MIB_IF_ROW2()
    row.index = index.value
    if iphlpapi.GetIfEntry2(ctypes.byref(row)) != 0:
        return None
    alias = "".join(map(chr, row.alias)).split("\0")[0]
    return {"iface": alias, "rx": row.in_octets, "tx": row.out_octets}


class Cancelled(Exception):
    """Téléchargement interrompu (logiciel supprimé entre-temps)."""


PROGRESS = {}      # clé -> [octets reçus, taille totale]
CANCELLED = set()  # clés dont le téléchargement doit s'arrêter


def download(url, dest_dir, progress_key=None):
    """Télécharge url dans dest_dir. Retourne (chemin temporaire, nom de fichier)."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(_('Le lien doit commencer par http:// ou https://.'))
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        name = ""
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", resp.headers.get("Content-Disposition", ""))
        if m:
            name = urllib.parse.unquote(m.group(1))
        if not name:
            name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(resp.geturl()).path))
        name = secure_filename(name) or "download.bin"
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".dl-")
        try:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    if progress_key in CANCELLED:
                        raise Cancelled()
                    out.write(chunk)
                    done += len(chunk)
                    if progress_key:
                        PROGRESS[progress_key] = [done, total]
        except BaseException:
            os.unlink(tmp)
            raise
    return tmp, name


def back(endpoint, **values):
    """Retour après un formulaire : redirection, ou JSON si la page a envoyé le formulaire elle-même."""
    url = url_for(endpoint, **values)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"redirect": url}
    return redirect(url)


def safe_next(target):
    """Chemin local uniquement (pas de //hôte, /\\hôte, schéma ni caractère de contrôle)."""
    target = target or "/"
    parsed = urllib.parse.urlparse(target.replace("\\", "/"))
    if (not target.startswith("/") or parsed.scheme or parsed.netloc or target.startswith("//")
            or re.search(r"[\x00-\x1f\x7f]", target)):
        return "/"
    return target


def cleanup_temp_files():
    """Supprime les fichiers temporaires laissés par un arrêt brutal (téléchargements, envois)."""
    for root in (config.WIN_DIR, config.TMP_DIR, config.POOL_DIR):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.startswith((".dl-", ".up-")):
                    try:
                        os.unlink(os.path.join(dirpath, name))
                    except OSError:
                        pass
