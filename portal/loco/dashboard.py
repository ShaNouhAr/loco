"""Vue d'ensemble."""

import datetime
import time

from flask import render_template

from . import app
from .i18n import L_, _
from .auth import login_required, can
from .db import db
from .linux import list_debs
from .util import cpu_usage, disk_usage, mem_info, net_counters, read_stats, service_active
from .wupdate import wu_chart, wu_stats

STATUS_LABELS = {"ready": L_("prêt"), "downloading": L_("téléchargement"), "error": L_("erreur"), "pending": L_("en attente")}


def dashboard_data():
    stats = read_stats()
    apps = db().execute("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM win_apps WHERE status='ready'").fetchone()
    debs = list_debs()
    wu = wu_stats()
    return {
        "services": {"acng": service_active("apt-cacher-ng"), "nginx": service_active("nginx"), "portal": True},
        "acng_size": stats.get("acng", 0),
        "wucache_size": stats.get("wucache", 0),
        "flatpak_cache": stats.get("flatpakcache", 0),
        "debs": len(debs),
        "debs_size": sum(d["size"] for d in debs),
        "apps": apps["n"],
        "apps_size": apps["s"],
        "wu": {k: wu[k] for k in ("hits", "misses", "hit_bytes", "miss_bytes", "ratio")},
        "disk": disk_usage(),
        "cpu": cpu_usage(),
        "mem": mem_info(),
        "stats_updated": stats.get("updated", ""),
    }


def recent_activity(limit=6):
    events = []
    for d in list_debs() if can("linux.view") else []:
        events.append({"when": datetime.datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d %H:%M"),
                       "kind": "linux", "title": d["package"], "package_id": None, "detail": f"{d['version']} · {d['arch']}"})
    for r in db().execute("SELECT name, version, kind, status, updated_at, winget_id FROM win_apps") if can("windows.view") else []:
        events.append({"when": r["updated_at"], "kind": "windows", "title": r["name"], "package_id": r["winget_id"],
                       "detail": " · ".join(str(x) for x in (r["version"], r["kind"], STATUS_LABELS.get(r["status"], "")) if x)})
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]


def chart_data(d):
    """Données des graphiques de la vue d'ensemble (tracés en SVG par le serveur : aucun script tiers)."""
    stats = read_stats()
    charts = {}
    charts["wu"] = wu_chart(14)
    disk = d["disk"]
    parts = [(_("Cache APT"), stats.get("acng", 0), "c1"), (_("Cache Windows Update"), stats.get("wucache", 0), "c2"),
             (_("Cache RPM"), stats.get("rpmcache", 0), "c3"), (_("Cache Flathub"), stats.get("flatpakcache", 0), "c4"),
             (_("Dépôts et logiciels locaux"), sum(stats.get(k, 0) for k in ("apt", "rpm", "flatpak", "win")), "c5")]
    parts = [p for p in parts if p[1] > 0]
    other = max(0, disk["used"] - sum(p[1] for p in parts))
    total = max(disk["total"], sum(p[1] for p in parts) + other + disk["free"]) or 1
    segments, x = [], 0.0
    for label, size, cls in parts + [(_("Système et autres"), other, "c6")] * bool(other) + [(_("Libre"), disk["free"], "free")]:
        width = 1000 * size / total
        segments.append({"label": label, "size": size, "cls": cls, "x": round(x, 2), "w": round(width, 2),
                         "pct": round(100 * size / total, 1)})
        x += width
    charts["disk"] = segments
    if can("fleet.view"):
        from .fleet import catalog, status
        from . import scope
        apps = catalog()
        counts = {"green": 0, "orange": 0, "red": 0, "grey": 0}
        for dev in db().execute("SELECT * FROM devices WHERE state != 'removed'" + scope.sql()):
            counts[status(dev, apps)[0]] += 1
        n = sum(counts.values())
        ring, offset = [], 0.0
        circumference = 301.59                       # 2π × 48 : rayon de l'anneau
        for key, label in (("green", _("à jour")), ("orange", _("mises à jour en attente")), ("red", _("à traiter")),
                           ("grey", _("état inconnu"))):
            length = circumference * counts[key] / n if n else 0
            ring.append({"key": key, "label": label, "count": counts[key], "len": round(length, 2),
                         "gap": round(circumference - length, 2), "offset": round(-offset, 2)})
            offset += length
        charts["fleet"] = {"total": n, "ring": ring}
    return charts


@app.route("/")
@login_required
def index():
    d = dashboard_data()
    return render_template("index.html", d=d, activity=recent_activity(), charts=chart_data(d))


@app.route("/api/status")
@login_required
def api_status():
    return dashboard_data()


@app.route("/api/network")
@login_required
def api_network():
    """Compteurs cumulés de l'interface réseau et instant du relevé : le débit se calcule entre deux appels."""
    counters = net_counters()
    if not counters:
        return {"available": False}
    return dict(counters, available=True, t=round(time.monotonic(), 3))
