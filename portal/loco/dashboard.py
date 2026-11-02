"""Vue d'ensemble."""

import datetime
import hashlib
import json
import time

from flask import g, render_template, url_for

from . import app, features
from .i18n import L_, _
from .auth import login_required, can
from .db import db
from .permissions import ENDPOINTS
from .linux import list_debs
from .util import cpu_usage, disk_usage, mem_info, net_counters, read_stats, service_active
from .wupdate import wu_chart, wu_stats

STATUS_LABELS = {"ready": L_("prêt"), "downloading": L_("téléchargement"), "error": L_("erreur"), "pending": L_("en attente")}
# Règle d'alerte -> droit pour la voir (None : qui ouvre la page voit déjà cet état), page où agir, onglet.
# « Postes hors contact » n'y figure pas : un poste éteint n'est pas un problème, le panneau Parc le compte.
ATTENTION = {"service_down": (None, "system", ""), "disk_high": (None, "system", ""),
             "cert_expiring": ("system.view", "system", "certificat"), "backup_overdue": ("system.view", "system", "sauvegardes"),
             "offsite_failed": ("system.view", "system", "sauvegardes"), "syslog_failed": ("syslog.configure", "syslog_settings", ""),
             "login_failures": ("audit.view", "journal", ""), "security_updates": ("fleet.view", "fleet", ""),
             "device_disk_high": ("fleet.view", "fleet", ""), "device_memory_high": ("fleet.view", "fleet", ""),
             "device_cpu_high": ("fleet.view", "fleet", "")}
STATE_TTL = 30                  # secondes entre deux calculs lors des actualisations en direct
_state_cache = {}


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


def recent_activity(limit=4):
    events = []
    for d in list_debs() if can("linux.view") else []:
        events.append({"when": datetime.datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d %H:%M"),
                       "kind": "linux", "title": d["package"], "package_id": None, "detail": f"{d['version']} · {d['arch']}",
                       # Un paquet APT n'a pas de page à lui : son dépôt, filtré sur ce paquet.
                       "url": url_for("linux", repository=d["repository"], q=d["package"])})
    for r in db().execute("SELECT slug, name, version, kind, status, updated_at, winget_id FROM win_apps") if can("windows.view") else []:
        events.append({"when": r["updated_at"], "kind": "windows", "title": r["name"], "package_id": r["winget_id"],
                       "url": url_for("software_detail", slug=r["slug"]),
                       "detail": " · ".join(str(x) for x in (r["version"], r["kind"], STATUS_LABELS.get(r["status"], "")) if x)})
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]


def _storage_link(feature, permission, endpoint):
    return url_for(endpoint) if feature not in features.disabled() and can(permission) else ""


def chart_data(d):
    """Données des graphiques de la vue d'ensemble (tracés en SVG par le serveur : aucun script tiers)."""
    stats = read_stats()
    charts = {}
    charts["wu"] = wu_chart(14)
    disk = d["disk"]
    local = []
    if d["debs"]:
        local.append(_("%(n)s paquet", n=d["debs"]) if d["debs"] == 1 else _("%(n)s paquets", n=d["debs"]))
    if d["apps"]:
        local.append(_("%(n)s logiciel Windows", n=d["apps"]) if d["apps"] == 1 else _("%(n)s logiciels Windows", n=d["apps"]))
    parts = [(_("Cache APT"), stats.get("acng", 0), _storage_link("apt", "linux.view", "linux"), ""),
             (_("Cache Windows Update"), stats.get("wucache", 0), _storage_link("wu", "wu.view", "windows_update"), ""),
             (_("Cache RPM"), stats.get("rpmcache", 0), _storage_link("rpm", "rpm.view", "rpm"), ""),
             (_("Cache Flathub"), stats.get("flatpakcache", 0), _storage_link("flatpak", "flatpak.view", "flatpak"), ""),
             (_("Dépôts et logiciels locaux"), sum(stats.get(k, 0) for k in ("apt", "rpm", "flatpak", "win")), "", " · ".join(local))]
    parts = [p for p in parts if p[1] > 0]
    other = max(0, disk["used"] - sum(p[1] for p in parts))
    total = max(disk["total"], sum(p[1] for p in parts) + other + disk["free"]) or 1
    if other:
        parts.append((_("Système et autres"), other, "", ""))
    charts["disk"] = sorted(({"label": label, "size": size, "url": url, "note": note, "pct": round(100 * size / total, 1)}
                             for label, size, url, note in parts), key=lambda part: -part["size"])
    return charts


def _fleet_summary():
    from .fleet import ONLINE_SECONDS, catalog, status, updating_count
    from . import scope
    apps = catalog()
    counts = {"green": 0, "orange": 0, "red": 0, "grey": 0}
    reboot = offline = 0
    cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=ONLINE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    for dev in db().execute("SELECT * FROM devices WHERE state != 'removed'" + scope.sql()):
        counts[status(dev, apps)[0]] += 1
        reboot += bool(dev["reboot_required"])
        offline += bool(dev["last_seen"]) and dev["last_seen"] < cutoff
    ring = [{"key": key, "label": label, "count": counts[key]}
            for key, label in (("green", _("à jour")), ("orange", _("mises à jour en attente")), ("red", _("à traiter")),
                               ("grey", _("état inconnu")))]
    return {"total": sum(counts.values()), "ring": ring, "reboot": reboot, "offline": offline, "updating": updating_count()}


def _attention():
    """Ce qui demande une action, le plus grave d'abord, limité aux droits et au parc du compte."""
    from . import alerts, scope
    from .fleet import failed_updates_count
    conn = db()
    rows = []
    for alert in alerts.evaluate(conn, alerts.every_rule(conn), devices=scope.sql()):
        rule = ATTENTION.get(alert["key"])
        if not rule or (rule[0] and not can(rule[0])):
            continue
        url = url_for(rule[1], _anchor=rule[2] or None) if can(ENDPOINTS[rule[1]]) else ""
        rows.append({"tone": "err" if alert["severity"] == "critical" else "warn", "text": alert["body"], "url": url})
    if can("fleet.view") and "fleet" not in features.disabled():
        failed = failed_updates_count()
        if failed:
            text = _("%(n)s poste avec une mise à jour en échec depuis 24 h.", n=failed) if failed == 1 else _("%(n)s postes avec une mise à jour en échec depuis 24 h.", n=failed)
            rows.append({"tone": "warn", "text": text, "url": url_for("fleet_updates")})
    rows.sort(key=lambda row: row["tone"] != "err")
    return rows


def overview_state(fresh=False):
    """État général et parc. Coûteux sur un grand parc : relu au plus toutes les 30 s par les actualisations en direct."""
    now = time.monotonic()
    cached = _state_cache.get(g.user["id"])
    if not fresh and cached and now - cached[0] < STATE_TTL:
        return cached[1]
    fleet = _fleet_summary() if can("fleet.view") and "fleet" not in features.disabled() else None
    attention = _attention()
    checked = [_("services"), _("disque")]
    if can("system.view"):
        checked += [_("certificat"), _("sauvegardes")]
    if fleet:
        checked.append(_("postes"))
    # Le nombre de mises à jour en cours change souvent : il s'actualise en place, seul son passage à zéro recharge.
    signature = json.dumps([attention, fleet and [s["count"] for s in fleet["ring"]], fleet and [fleet["reboot"], fleet["offline"], bool(fleet["updating"])]])
    state = {"attention": attention, "fleet": fleet, "checked": ", ".join(checked),
             "signature": hashlib.sha256(signature.encode()).hexdigest()[:16]}
    if len(_state_cache) > 100:
        _state_cache.clear()
    _state_cache[g.user["id"]] = (now, state)
    return state


@app.route("/")
@login_required
def index():
    d = dashboard_data()
    return render_template("index.html", d=d, activity=recent_activity(), charts=chart_data(d), state=overview_state(fresh=True))


@app.route("/api/status")
@login_required
def api_status():
    state = overview_state()
    data = dict(dashboard_data(), signature=state["signature"])
    if state["fleet"]:
        from .fleet import updating_count
        data["updating"] = updating_count()
    return data


@app.route("/api/network")
@login_required
def api_network():
    """Compteurs cumulés de l'interface réseau et instant du relevé : le débit se calcule entre deux appels."""
    counters = net_counters()
    if not counters:
        return {"available": False}
    return dict(counters, available=True, t=round(time.monotonic(), 3))
