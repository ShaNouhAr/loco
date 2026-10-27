"""Supervision des postes : relevé envoyé par l'agent à chaque contact (processeur, mémoire, swap, disques, réseau,
charge, durée depuis le démarrage), dernier état et historique de 7 jours pour les courbes de la fiche du poste."""

import datetime
import json
import time

from .i18n import _
from .util import human

RETENTION = 7 * 86400
RANGES = {"24h": 86400, "7j": 7 * 86400}
BUCKETS = 240                       # points par courbe, quelle que soit la période
STALE = 600                         # relevé plus vieux : poste hors contact, valeurs grisées
LEVELS = {"cpu": (85, 95), "mem": (85, 95), "disk": (85, 95), "swap": (50, 80)}
SERIES = ("cpu", "mem", "net_rx", "net_tx")


def _num(value, low=0.0, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    if value < low:
        return None
    return min(float(value), high) if high is not None else float(value)


def sanitize(raw):
    """Relevé borné et nettoyé (l'agent n'est jamais cru sur parole)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, high in (("cpu", 100.0), ("net_rx", 1e11), ("net_tx", 1e11), ("uptime", 1e10)):
        v = _num(raw.get(key), 0, high)
        if v is not None:
            out[key] = round(v, 1) if key == "cpu" else int(v)
    cores = _num(raw.get("cores"), 0, 4096)
    if cores:
        out["cores"] = int(cores)
    for prefix in ("mem", "swap"):
        total, used = _num(raw.get(prefix + "_total"), 0, 1e15), _num(raw.get(prefix + "_used"), 0, 1e15)
        if total is not None and used is not None:
            out[prefix + "_total"], out[prefix + "_used"] = int(total), int(min(used, total))
    load = raw.get("load")
    if isinstance(load, list) and len(load) == 3 and all(_num(x, 0, 1e5) is not None for x in load):
        out["load"] = [round(float(x), 2) for x in load]
    disks = []
    for d in raw.get("disks") or [] if isinstance(raw.get("disks"), list) else []:
        if not isinstance(d, dict) or len(disks) >= 16:
            continue
        total, used = _num(d.get("total"), 1, 1e16), _num(d.get("used"), 0, 1e16)
        mount = str(d.get("mount") or "")[:120]
        if total and used is not None and mount and all(ord(c) >= 32 for c in mount):
            disks.append({"mount": mount, "fs": str(d.get("fs") or "")[:20], "total": int(total), "used": int(min(used, total))})
    out["disks"] = disks
    return out


def _pct(used, total):
    return round(100 * used / total, 1) if total else None


def level(kind, pct):
    if pct is None:
        return ""
    warn, crit = LEVELS[kind]
    return "err" if pct >= crit else "warn" if pct >= warn else "ok"


def summary(m):
    """Valeurs lisibles d'un relevé : pourcentages, disque le plus rempli, niveaux (ok / warn / err)."""
    disks = sorted(({**d, "pct": _pct(d["used"], d["total"])} for d in m.get("disks", [])), key=lambda d: d["mount"])
    fullest = max(disks, key=lambda d: d["pct"], default=None)
    s = {"cpu": m.get("cpu"), "mem": _pct(m.get("mem_used", 0), m.get("mem_total", 0)),
         "swap": _pct(m.get("swap_used", 0), m.get("swap_total", 0)), "disk": fullest["pct"] if fullest else None,
         "disk_mount": fullest["mount"] if fullest else "", "disks": disks, "raw": m}
    s["levels"] = {k: level(k, s[k]) for k in LEVELS}
    for d in disks:
        d["level"] = level("disk", d["pct"])
    return s


def record(conn, dev_id, raw):
    """Garde le dernier relevé (fiche, liste du parc) et l'ajoute à l'historique ; purge au-delà de 7 jours."""
    m = sanitize(raw)
    if not m:
        return
    now = int(time.time())
    s = summary(m)
    conn.execute("UPDATE devices SET metrics=? WHERE id=?", (json.dumps({"at": now, **m}), dev_id))
    conn.execute("INSERT INTO device_metrics (device_id, at, cpu, mem, swap, disk, net_rx, net_tx) VALUES (?,?,?,?,?,?,?,?)",
                 (dev_id, now, s["cpu"], s["mem"], s["swap"], s["disk"], m.get("net_rx"), m.get("net_tx")))
    conn.execute("DELETE FROM device_metrics WHERE device_id=? AND at<?", (dev_id, now - RETENTION))


def duration(seconds):
    seconds = int(seconds)
    days, hours, minutes = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
    if days:
        return _("%(d)s j %(h)s h", d=days, h=hours)
    if hours:
        return _("%(h)s h %(m)s min", h=hours, m=minutes)
    return _("%(m)s min", m=max(minutes, 1))


def latest(dev):
    """Dernier relevé d'un poste (ligne de la table devices), ou None ; « stale » s'il date de plus de 10 min."""
    try:
        m = json.loads(dev["metrics"] or "{}")
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    if not m.get("at"):
        return None
    s = summary(m)
    s["age"] = max(0, int(time.time() - m["at"]))
    s["uptime_text"] = duration(m["uptime"]) if m.get("uptime") else ""
    s["age_text"] = duration(s["age"]) if s["age"] >= 60 else _("à l'instant")
    s["stale"] = s["age"] > STALE
    return s


def _buckets(conn, dev_id, span):
    now = int(time.time())
    width = span / BUCKETS
    sums = [dict.fromkeys(SERIES, 0.0) for _ in range(BUCKETS)]
    counts = [dict.fromkeys(SERIES, 0) for _ in range(BUCKETS)]
    rows = conn.execute("SELECT at, cpu, mem, net_rx, net_tx FROM device_metrics WHERE device_id=? AND at>=? ORDER BY at",
                        (dev_id, now - span)).fetchall()
    for row in rows:
        i = min(BUCKETS - 1, int((row[0] - (now - span)) / width))
        for key, value in zip(SERIES, row[1:]):
            if value is not None:
                sums[i][key] += value
                counts[i][key] += 1
    points = []
    for i in range(BUCKETS):
        at = now - span + (i + 0.5) * width
        points.append({"at": at, **{k: (sums[i][k] / counts[i][k] if counts[i][k] else None) for k in SERIES}})
    return points, bool(rows)


# Géométrie commune des courbes (unités SVG) : marge gauche pour l'axe, droite pour les étiquettes directes.
W, H, LEFT, RIGHT, TOP, BOTTOM = 1200, 230, 64, 120, 12, 26


def nice(value):
    """Maximum d'axe « rond » (1, 2 ou 5 × 10ⁿ) au-dessus de la valeur : graduations lisibles."""
    if value <= 0:
        return 1
    magnitude = 10 ** len(str(int(value))) / 10 if value >= 1 else 1
    for step in (1, 2, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def _label(at, span):
    moment = datetime.datetime.fromtimestamp(at)
    return moment.strftime("%H:%M") if span <= 86400 else moment.strftime("%d/%m")


def line_chart(points, span, series, ymax=None, fmt=lambda v: f"{v:.0f} %"):
    """Courbes prêtes à tracer : segments (interrompus quand le poste ne répondait pas), grille, étiquettes de temps,
    valeur finale de chaque série (étiquette directe) et zones de survol avec infobulle."""
    values = [p[k] for p in points for k, _l in series if p[k] is not None]
    if not values:
        return None
    top = ymax or nice(max(values) * 1.1)
    plot_w, plot_h = W - LEFT - RIGHT, H - TOP - BOTTOM
    step = plot_w / len(points)

    def y(v):
        return TOP + plot_h * (1 - min(v, top) / top)

    out = {"w": W, "h": H, "left": LEFT, "right": W - RIGHT, "top": TOP, "bottom": TOP + plot_h, "series": [],
           "grid": [{"y": round(y(top * f), 1), "label": fmt(top * f)} for f in (0, 0.5, 1)], "ticks": [], "cells": []}
    for n, (key, label) in enumerate(series, start=1):
        segments, current, last = [], [], None
        for i, p in enumerate(points):
            if p[key] is None:
                if current:
                    segments.append(current)
                current = []
                continue
            current.append(f"{LEFT + step * (i + 0.5):.1f},{y(p[key]):.1f}")
            last = (LEFT + step * (i + 0.5), y(p[key]), p[key])
        if current:
            segments.append(current)
        out["series"].append({"key": key, "slot": n, "label": label,
                              "segments": [" ".join(s) for s in segments if len(s) > 1],
                              "dots": [s[0] for s in segments if len(s) == 1],
                              "end": {"x": W - RIGHT + 8, "y": round(last[1] + 4, 1), "text": fmt(last[2])} if last else None})
    ends = sorted((s["end"] for s in out["series"] if s["end"]), key=lambda e: e["y"])
    for a, b in zip(ends, ends[1:]):                       # étiquettes directes : jamais superposées
        if b["y"] - a["y"] < 13:
            b["y"] = round(a["y"] + 13, 1)
    # Graduations sur des heures rondes : toutes les 4 h sur 24 h, chaque jour à minuit sur 7 jours.
    start = points[0]["at"] - span / len(points) / 2
    every = 4 * 3600 if span <= 86400 else 86400
    first = datetime.datetime.fromtimestamp(start).replace(minute=0, second=0, microsecond=0)
    if every == 86400:
        first = first.replace(hour=0)
    else:
        first = first.replace(hour=first.hour - first.hour % 4)
    moment = first                                      # avance en dates locales : juste au changement d'heure
    while moment.timestamp() < start + span:
        tick = moment.timestamp()
        if tick >= start:
            out["ticks"].append({"x": round(LEFT + plot_w * (tick - start) / span, 1), "label": _label(tick, span)})
        moment = (moment + datetime.timedelta(seconds=every + 7200)).replace(minute=0)
        moment = moment.replace(hour=0) if every == 86400 else moment.replace(hour=moment.hour - moment.hour % 4)
    for i, p in enumerate(points):
        parts = [f"{label} {fmt(p[k])}" for k, label in series if p[k] is not None]
        out["cells"].append({"x": round(LEFT + step * i, 1), "w": round(step, 2), "cx": round(LEFT + step * (i + 0.5), 1),
                             "title": _label(p["at"], 0) + " · " + (", ".join(parts) if parts else _("pas de relevé"))})
    return out


def rate(v):
    return human(v) + "/s"


def view(conn, dev, period="24h"):
    """Tout ce qu'affiche l'onglet Supervision de la fiche d'un poste."""
    period = period if period in RANGES else "24h"
    span = RANGES[period]
    now = latest(dev)
    points, has = _buckets(conn, dev["id"], span)
    return {"now": now, "period": period, "periods": list(RANGES), "has_history": has,
            "usage": line_chart(points, span, (("cpu", _("Processeur")), ("mem", _("Mémoire"))), ymax=100) if has else None,
            "network": line_chart(points, span, (("net_rx", _("Réception")), ("net_tx", _("Émission"))), fmt=rate) if has else None}


def recent_averages(conn, minutes=15):
    """Moyenne processeur et mémoire de chaque poste sur les dernières minutes (alertes : pas sur un simple pic)."""
    since = int(time.time()) - minutes * 60
    return {r[0]: {"cpu": r[1], "mem": r[2], "n": r[3]} for r in conn.execute(
        "SELECT device_id, AVG(cpu), AVG(mem), COUNT(*) FROM device_metrics WHERE at>=? GROUP BY device_id", (since,))}


# --------------------------------------------------------------------------- direct
# Tant qu'une fiche est regardée (onglet Supervision visible), la page le signale toutes les 2 s ; l'agent, qui contacte
# déjà le serveur par son canal distant, envoie alors un relevé léger à chaque échange. Rien de plus le reste du temps.
LIVE_INTERVAL = 2
LIVE_WATCH = 20                     # secondes de direct après le dernier signe de vie de la page
LIVE_KEEP = 600


def watch(conn, dev_id):
    conn.execute("UPDATE devices SET live_until=? WHERE id=?", (time.time() + LIVE_WATCH, dev_id))


def live_wanted(dev):
    try:
        return (dev["live_until"] or 0) > time.time()
    except (IndexError, KeyError):
        return False


def record_live(conn, dev_id, raw):
    m = sanitize(raw)
    if not m:
        return
    now = time.time()
    conn.execute("INSERT INTO device_live (device_id, at, cpu, mem, net_rx, net_tx) VALUES (?,?,?,?,?,?)",
                 (dev_id, now, m.get("cpu"), _pct(m.get("mem_used", 0), m.get("mem_total", 0)), m.get("net_rx"), m.get("net_tx")))
    conn.execute("DELETE FROM device_live WHERE device_id=? AND at<?", (dev_id, now - LIVE_KEEP))


def live_points(conn, dev_id, since=0.0, window=180):
    """Relevés en direct postérieurs à « since » (au plus les 3 dernières minutes)."""
    since = max(float(since or 0), time.time() - window)
    return [{"at": r[0], "cpu": r[1], "mem": r[2], "net_rx": r[3], "net_tx": r[4]} for r in conn.execute(
        "SELECT at, cpu, mem, net_rx, net_tx FROM device_live WHERE device_id=? AND at>? ORDER BY at LIMIT 200", (dev_id, since))]
