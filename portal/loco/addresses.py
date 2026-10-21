"""Adresses du serveur transmises aux agents, pour qu'ils suivent un changement d'adresse.

Avant de changer l'adresse du serveur (nouvelle IP, nom DNS), l'administrateur l'annonce : chaque agent la reçoit à
son contact suivant et la garde en réserve. Après le changement, un agent qui ne joint plus l'ancienne adresse essaie
celles qu'il connaît et adopte la première qui répond avec un certificat de la même autorité loco (épinglée) :
une adresse annoncée ne peut pas rediriger les postes vers un autre serveur.
"""

import json
import re
import urllib.parse

from . import config
from .i18n import _

SETTING = "announced_servers"
MAX_ANNOUNCED = 4
URL_RE = re.compile(r"^https://([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
                    r"|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?$")


def normalize(url):
    """« https://hôte[:port] » sans chemin ni barre finale ; ValueError sinon."""
    url = (url or "").strip().rstrip("/")
    if not URL_RE.match(url):
        raise ValueError(_('adresse attendue : https://hôte:port (IP ou nom DNS, sans chemin)'))
    port = urllib.parse.urlsplit(url).port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(_('port invalide'))
    return url


def announced(conn):
    """Adresses annoncées en plus de l'adresse actuelle du serveur."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    try:
        values = json.loads(row["value"]) if row else []
    except (TypeError, ValueError):
        values = []
    current = config.HTTPS_URL.rstrip("/")
    out = []
    for value in values if isinstance(values, list) else []:
        try:
            url = normalize(value)
        except ValueError:
            continue
        if url != current and url not in out:
            out.append(url)
    return out[:MAX_ANNOUNCED]


def servers(conn):
    """Liste transmise aux agents : adresse actuelle d'abord, puis les adresses annoncées."""
    return [config.HTTPS_URL.rstrip("/")] + announced(conn)


def _save(conn, values):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SETTING, json.dumps(values)))


def announce(conn, url):
    url = normalize(url)
    values = announced(conn)
    if url == config.HTTPS_URL.rstrip("/") or url in values:
        return url
    if len(values) >= MAX_ANNOUNCED:
        raise ValueError(_('%(MAX_ANNOUNCED)s adresses annoncées au plus : retirez-en une', MAX_ANNOUNCED=MAX_ANNOUNCED))
    _save(conn, values + [url])
    return url


def forget(conn, url):
    url = (url or "").strip().rstrip("/")
    values = announced(conn)
    if url not in values:
        return False
    _save(conn, [v for v in values if v != url])
    return True


def reported(value):
    """Adresses déclarées par un agent (liste JSON reçue) -> texte stocké, bornées et validées."""
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            url = normalize(item) if isinstance(item, str) else None
        except ValueError:
            url = None
        if url and url not in out:
            out.append(url)
    return " ".join(out[:8])


def coverage(conn):
    """[(adresse, postes qui la connaissent, postes actifs)] : avancement de l'annonce avant un changement."""
    devices = conn.execute("SELECT servers FROM devices WHERE state='active'").fetchall()
    total = len(devices)
    return [(url, sum(1 for d in devices if url in (d["servers"] or "").split()), total) for url in servers(conn)]
