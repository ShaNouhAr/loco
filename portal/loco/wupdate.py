"""Windows Update : statistiques du cache, domaines redirigés, script DNS."""

import datetime
import glob
import gzip
import json
import os
import re
import threading
import urllib.parse

from flask import Response, flash, render_template, request

from . import app, config
from .auth import audit, can, login_required
from .db import db
from .fleet import ago_text
from .util import back, read_stats, service_active

LOG_RE = re.compile(r'^(\S+) \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d+) (\d+) (\S+) "([^"]*)"$')
_CACHE = {}


# --------------------------------------------------------------------------- historique quotidien

MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_DAILY = {}                     # fichier -> (clé, totaux par jour) ; journal courant : lecture incrémentale
_DAILY_LOCK = threading.Lock()


def _day_totals(lines, totals):
    """Octets servis depuis le cache et depuis Internet, par jour (date du journal nginx, sans dépendre de la locale)."""
    for line in lines:
        m = LOG_RE.match(line)
        if not m:
            continue
        stamp, size, cache = m.group(2), int(m.group(6)), m.group(7)
        try:
            day = datetime.date(int(stamp[7:11]), MONTHS[stamp[3:6]], int(stamp[0:2])).isoformat()
        except (KeyError, ValueError):
            continue
        hit, miss = totals.get(day, (0, 0))
        if cache == "HIT":
            totals[day] = (hit + size, miss)
        elif cache in ("MISS", "EXPIRED", "UPDATING", "STALE", "BYPASS"):
            totals[day] = (hit, miss + size)


def _archived(path):
    """Journal archivé (.1, .2.gz, .AAAAMMJJ) : immuable, lu une seule fois."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime_ns)
    cached = _DAILY.get(path)
    if cached and cached[0] == key:
        return cached[1]
    totals = {}
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rb") as f:
            _day_totals((raw.decode("utf-8", "replace").rstrip("\n") for raw in f), totals)
    except (OSError, EOFError):
        return {}
    _DAILY[path] = (key, totals)
    return totals


def _current(path):
    """Journal en cours d'écriture : seules les lignes ajoutées depuis la dernière lecture sont analysées."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    cached = _DAILY.get(path)
    inode, offset, totals = cached[0] if cached else (None, 0, {})
    if inode != st.st_ino or st.st_size < offset:
        inode, offset, totals = st.st_ino, 0, {}
    totals = dict(totals)
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return totals
    end = data.rfind(b"\n") + 1                # ligne incomplète : relue au prochain passage
    _day_totals(data[:end].decode("utf-8", "replace").splitlines(), totals)
    _DAILY[path] = ((inode, offset + end, totals), None)
    return totals


def wu_daily(days=14):
    """[(jour ISO, octets servis depuis le cache, octets téléchargés sur Internet)] des `days` derniers jours."""
    merged = {}
    with _DAILY_LOCK:
        sources = [_current(config.WU_LOG)]
        archives = [p for p in glob.glob(glob.escape(config.WU_LOG) + ".*") if not p.endswith((".tmp", ".lock"))]
        sources += [_archived(p) for p in sorted(archives, key=os.path.getmtime, reverse=True)[:days + 2]]
    for totals in sources:
        for day, (hit, miss) in totals.items():
            h, m = merged.get(day, (0, 0))
            merged[day] = (h + hit, m + miss)
    today = datetime.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        out.append((day, *merged.get(day, (0, 0))))
    return out


def wu_chart(days=14):
    """Historique quotidien pour le graphique en barres (vue d'ensemble et page Windows Update)."""
    rows = wu_daily(days)
    hit, miss = sum(h for _, h, _ in rows), sum(m for _, _, m in rows)
    return {"days": [{"day": day, "label": day[8:10] + "/" + day[5:7], "hit": h, "miss": m} for day, h, m in rows],
            "max": max([h + m for _, h, m in rows] + [1]), "hit": hit, "miss": miss,
            "ratio": round(100 * hit / (hit + miss)) if hit + miss else 0}


def _stamp(nginx_date):
    """« 19/Oct/2026:10:22:33 +0200 » -> « 2026-10-19 10:22:33 » à l'heure locale du serveur, ou ''."""
    try:
        moment = datetime.datetime(int(nginx_date[7:11]), MONTHS[nginx_date[3:6]], int(nginx_date[0:2]),
                                   int(nginx_date[12:14]), int(nginx_date[15:17]), int(nginx_date[18:20]),
                                   tzinfo=datetime.datetime.strptime(nginx_date[21:26], "%z").tzinfo)
    except (KeyError, ValueError, IndexError):
        return ""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


SOURCES = {"HIT": "cache", "MISS": "Internet", "EXPIRED": "Internet", "UPDATING": "Internet", "STALE": "Internet",
           "BYPASS": "Internet"}


def read_wu_domains():
    try:
        with open(config.WU_DOMAINS_PATH) as f:
            domains = [d.strip() for d in f if d.strip() and not d.startswith("#")]
            return domains or config.DEFAULT_WU_DOMAINS
    except OSError:
        return config.DEFAULT_WU_DOMAINS


def wu_stats(max_bytes=20 * 1024 * 1024, recent=0):
    """Fin du journal du cache : compteurs HIT/MISS et dernières requêtes (mis en cache tant que le fichier ne change pas)."""
    try:
        st = os.stat(config.WU_LOG)
        key = (st.st_ino, st.st_size, st.st_mtime_ns, recent)
    except OSError:
        key = None
    if key and _CACHE.get("key") == key:
        return json.loads(_CACHE["value"])
    res = _parse_log(max_bytes, recent)
    if key:
        _CACHE.update(key=key, value=json.dumps(res))
    return res


def _parse_log(max_bytes, recent):
    res = {"hit_bytes": 0, "miss_bytes": 0, "hits": 0, "misses": 0, "ok": False, "recent": [], "clients": 0,
           "last": ""}
    try:
        with open(config.WU_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
        res["ok"] = True
    except OSError:
        lines = []
    entries, clients, last = [], set(), ""
    for line in lines:
        # format : ip [date] "requête" statut octets cache "hôte"
        m = LOG_RE.match(line)
        if not m:
            continue
        ip, date, _method, path, status, size, cache, host = m.groups()
        size = int(size)
        clients.add(ip)
        last = date
        if cache == "HIT":
            res["hits"] += 1
            res["hit_bytes"] += size
        elif cache in ("MISS", "EXPIRED", "UPDATING", "STALE", "BYPASS"):
            res["misses"] += 1
            res["miss_bytes"] += size
        if recent:
            entries.append((ip, date, path, status, size, cache, host))
    res["clients"], res["last"] = len(clients), _stamp(last) if last else ""
    total = res["hit_bytes"] + res["miss_bytes"]
    res["ratio"] = round(100 * res["hit_bytes"] / total) if total else 0
    for ip, date, path, status, size, cache, host in reversed(entries[-recent:] if recent else []):
        name = urllib.parse.unquote(path.split("?")[0].rsplit("/", 1)[-1]) or "/"
        res["recent"].append({"client": ip, "time": date.split(":", 1)[-1].split(" ")[0], "host": host,
                              "file": name, "status": status, "size": size, "cache": cache,
                              "source": SOURCES.get(cache, "relayé")})
    return res


def write_wu_maps(domains=None):
    """Écrit la liste des domaines autorisés pour nginx (loco-nginx.path recharge nginx)."""
    domains = domains or read_wu_domains()
    os.makedirs(config.NGINX_MAP_DIR, exist_ok=True)
    for name, value in (("wu-stream.map", "$ssl_preread_server_name:443"), ("wu-http.map", "1")):
        body = "".join(f".{d} {value};\n" for d in domains if config.DOMAIN_RE.match(d))
        tmp = os.path.join(config.NGINX_MAP_DIR, f".{name}.tmp")
        with open(tmp, "w") as f:
            f.write("# Généré par le portail loco, ne pas modifier.\n" + body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(config.NGINX_MAP_DIR, name))


def _live():
    """Compteurs de la page (rafraîchis toutes les 5 s) : 14 derniers jours, dernières requêtes, postes connus."""
    wu = wu_stats(recent=15)
    wu["cache_size"] = read_stats().get("wucache", 0)
    chart = wu_chart(14)
    wu["period"] = {k: chart[k] for k in ("hit", "miss", "ratio")}
    # Seuls les postes Windows font des requêtes Windows Update. Derrière un NAT, plusieurs postes partagent une adresse :
    # on ne choisit pas au hasard, on les nomme tous (« lab-win11 ou lab-ws2025 »).
    names = {}
    if can("fleet.view"):
        for row in db().execute("SELECT hostname, ip, addresses FROM devices WHERE state != 'removed' AND hostname != '' "
                                "AND os_family = 'windows' ORDER BY hostname COLLATE NOCASE"):
            for ip in dict.fromkeys([row["ip"]] + row["addresses"].split()):
                names.setdefault(ip, []).append(row["hostname"])
    for r in wu["recent"]:
        found = names.get(r["client"], [])
        r["name"] = " ou ".join(found) if len(found) <= 2 else f"{len(found)} postes Windows"
        r["shared"] = len(found) > 1
    wu["last_text"] = ago_text(wu["last"]) if wu["last"] else ""
    return wu, chart


@app.route("/windows-update")
@login_required
def windows_update():
    from . import features
    wu, chart = _live()
    return render_template("windows_update.html", domains=read_wu_domains(), wu=wu, chart=chart,
                           nginx=service_active("nginx"), proxy_active=service_active("squid"),
                           proxy_on="wu_proxy" not in features.disabled(), proxy_available=config.WU_PROXY,
                           proxy_host=config.HOST, proxy_port=config.WU_PROXY_PORT)


@app.route("/api/wu")
@login_required
def api_wu():
    return _live()[0]


@app.route("/windows-update/domains", methods=["POST"])
@login_required
def windows_update_domains():
    domains, bad = [], []
    for d in re.split(r"[\s,]+", request.form.get("domains", "").lower()):
        d = d.strip().lstrip("*.").rstrip(".")
        if d:
            (domains if config.DOMAIN_RE.match(d) else bad).append(d)
    if bad:
        flash("Domaines ignorés (invalides) : " + ", ".join(bad), "error")
    if domains:
        domains = list(dict.fromkeys(domains))
        before = read_wu_domains()
        with open(config.WU_DOMAINS_PATH, "w") as f:
            f.write("\n".join(domains) + "\n")
        write_wu_maps(domains)
        if domains != before:
            audit("windows_update.domaines", ", ".join(domains))
        flash("Domaines enregistrés et appliqués au cache.", "ok")
    return back("windows_update")


@app.route("/windows-update/dns.ps1")
@login_required
def windows_update_dns():
    script = render_template("dns.ps1", domains=read_wu_domains(), host=config.HOST)
    return Response(script.replace("\n", "\r\n"), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=loco-dns.ps1"})
