"""Mises à jour bloquées : loco ne les installe plus, quel que soit le chemin (fiche du poste, tout le parc, vagues,
planifications, assistants IA).

Un blocage désigne un paquet (Linux) ou un KB ou un titre (Windows), avec « * » comme joker, sur tout le parc ou un
groupe de machines. Le serveur retire ces mises à jour de ses comptes et de ce qu'il demande ; l'agent les écarte aussi
de son côté, car son inventaire a pu changer : Debian et Ubuntu les retiennent (apt-mark hold), Windows les masque, DNF
les exclut à chaque mise à jour. Flatpak et logiciels du catalogue ne sont pas concernés."""
import functools
import re

from flask import flash, g, redirect, render_template, request, url_for

from . import app, fleet, scope
from .auth import audit, login_required
from .db import db
from .i18n import _

AGENT_VERSION = (2, 137)        # agents qui respectent les blocages
FAMILIES = ("linux", "windows")
MAX_RULES = 500
LINUX_RE = re.compile(r"[A-Za-z0-9*][A-Za-z0-9+._:*-]{0,127}")
KB_RE = re.compile(r"(?:KB)?([0-9]{6,8})", re.I)


def family_of(dev):
    return "windows" if dev["os_family"] == "windows" else "linux"


def clean_pattern(family, raw):
    """Motif vérifié (KB normalisé) ; ValueError avec le message à afficher."""
    pattern = str(raw or "").strip()
    if family == "windows":
        kb = KB_RE.fullmatch(pattern)
        if kb:
            pattern = "KB" + kb.group(1)
        elif not pattern or len(pattern) > 200 or not pattern.isprintable():
            raise ValueError(_("Indiquez un numéro KB ou le titre de la mise à jour."))
    elif not LINUX_RE.fullmatch(pattern):
        raise ValueError(_("Nom de paquet invalide : lettres, chiffres, « + . _ : - » et le joker « * »."))
    if len(pattern.replace("*", "")) < 3:
        raise ValueError(_("Motif trop large : gardez au moins trois caractères en plus du joker."))
    return pattern


@functools.lru_cache(maxsize=1024)
def _regex(family, pattern):
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")), re.I if family == "windows" else 0)


def _hit(family, pattern, update):
    regex, name = _regex(family, pattern), str(update.get("name") or "")
    if family == "windows":
        return any(regex.fullmatch(text) for text in [name, *str(update.get("version") or "").split()])
    return bool(regex.fullmatch(name) or regex.fullmatch(name.split(":")[0]))


def rule_of(found, family, update):
    """Premier blocage qui vise cette mise à jour, ou None."""
    return next((rule for rule in found if _hit(family, rule["pattern"], update)), None)


def rules(conn):
    return conn.execute("SELECT * FROM update_blocks ORDER BY id").fetchall()


def _applicable(all_rules, family, groups):
    return [r for r in all_rules if r["family"] == family and (r["group_id"] is None or r["group_id"] in groups)]


def for_device(conn, dev):
    """Blocages qui concernent ce poste."""
    all_rules = rules(conn)
    groups = set()
    if any(r["group_id"] for r in all_rules):
        groups = {r[0] for r in conn.execute("SELECT group_id FROM device_group_members WHERE device_id=?", (dev["id"],))}
    return _applicable(all_rules, family_of(dev), groups)


def _counts(found, family, inv):
    """(à installer, dont sécurité, bloquées) d'un inventaire : ce qui est bloqué ne compte plus comme en attente."""
    updates = [u for u in inv.get("updates") or [] if isinstance(u, dict)]
    open_updates = [u for u in updates if not rule_of(found, family, u)]
    flatpak = inv.get("flatpak") if isinstance(inv.get("flatpak"), int) else 0
    return len(open_updates) + flatpak, sum(bool(u.get("security")) for u in open_updates), len(updates) - len(open_updates)


def counts(conn, dev, inv):
    return _counts(for_device(conn, dev), family_of(dev), inv)


def annotate(conn, dev, updates):
    """Mises à jour d'un inventaire, avec « blocked » (le blocage) pour celles qui sont bloquées."""
    found, family = for_device(conn, dev), family_of(dev)
    out = []
    for update in updates:
        rule = rule_of(found, family, update) if isinstance(update, dict) else None
        out.append(dict(update, blocked={"id": rule["id"], "pattern": rule["pattern"], "reason": rule["reason"]}) if rule else update)
    return out


def recount(conn):
    """Après un changement de blocages ou de groupes : ce que chaque poste a d'effectivement à installer."""
    all_rules, members = rules(conn), {}
    for device_id, group_id in conn.execute("SELECT device_id, group_id FROM device_group_members"):
        members.setdefault(device_id, set()).add(group_id)
    for dev in conn.execute("SELECT id, os_family, inventory FROM devices WHERE state != 'removed'").fetchall():
        family = family_of(dev)
        total, security, blocked = _counts(_applicable(all_rules, family, members.get(dev["id"], set())), family, fleet.inventory(dev))
        conn.execute("UPDATE devices SET updates=?, security_updates=?, blocked_updates=? WHERE id=?", (total, security, blocked, dev["id"]))


def recount_grouped(conn):
    """Après un changement de membres d'un groupe : utile seulement si un blocage vise un groupe."""
    if conn.execute("SELECT 1 FROM update_blocks WHERE group_id IS NOT NULL LIMIT 1").fetchone():
        recount(conn)


def patterns_for(conn, dev):
    return [r["pattern"] for r in for_device(conn, dev)]


def trim(conn, dev, only):
    """Sélection d'une commande sans ce qui a été bloqué depuis qu'elle est en attente."""
    found, family = for_device(conn, dev), family_of(dev)
    if not found:
        return only
    if family == "windows":
        barred = {u.get("id") for u in fleet.inventory(dev).get("updates") or [] if rule_of(found, family, u)}
        return dict(only, updates=[i for i in only.get("updates") or [] if i not in barred])
    return dict(only, packages=[n for n in only.get("packages") or [] if not rule_of(found, family, {"name": n})])


@app.route("/fleet/blocks")
@login_required
def update_blocks():
    conn = db()
    all_rules = rules(conn)
    group_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM device_groups")}
    members = {}
    for device_id, group_id in conn.execute("SELECT device_id, group_id FROM device_group_members"):
        members.setdefault(group_id, set()).add(device_id)
    devices = [(d, fleet.inventory(d)) for d in conn.execute("SELECT id, hostname, os_family, inventory FROM devices "
                                                             "WHERE state != 'removed'" + scope.sql()).fetchall()]
    rows = []
    for rule in all_rules:
        hit, security = [], False
        for dev, inv in devices:
            if family_of(dev) != rule["family"] or (rule["group_id"] is not None and dev["id"] not in members.get(rule["group_id"], ())):
                continue
            matched = [u for u in inv.get("updates") or [] if isinstance(u, dict) and _hit(rule["family"], rule["pattern"], u)]
            if matched:
                hit.append(dev["hostname"])
                security = security or any(u.get("security") for u in matched)
        rows.append({"rule": rule, "where": group_names.get(rule["group_id"], ""), "hosts": sorted(hit), "security": security})
    family = request.args.get("family") if request.args.get("family") in FAMILIES else "linux"
    return render_template("update_blocks.html", rows=rows, groups=group_names, family=family, pattern=request.args.get("pattern", "")[:200])


@app.route("/fleet/blocks", methods=["POST"])
@login_required
def update_block_add():
    conn, form = db(), request.form
    family = form.get("family")
    group_id = None
    try:
        if family not in FAMILIES:
            raise ValueError(_("Choisissez le système concerné."))
        pattern = clean_pattern(family, form.get("pattern"))
        if form.get("group"):
            group_id = form["group"]
            row = conn.execute("SELECT id FROM device_groups WHERE id=?", (group_id,)).fetchone()
            if row is None:
                raise ValueError(_("Ce groupe n'existe plus."))
            group_id = row["id"]
        if conn.execute("SELECT COUNT(*) FROM update_blocks").fetchone()[0] >= MAX_RULES:
            raise ValueError(_("Trop de blocages : levez-en d'abord."))
        if conn.execute("SELECT 1 FROM update_blocks WHERE family=? AND pattern=? AND COALESCE(group_id, 0)=?",
                        (family, pattern, group_id or 0)).fetchone():
            raise ValueError(_("Cette mise à jour est déjà bloquée ici."))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("update_blocks", family=family if family in FAMILIES else "linux", pattern=form.get("pattern", "")[:200]))
    reason = " ".join(form.get("reason", "").split())[:300]
    conn.execute("INSERT INTO update_blocks (family, pattern, reason, group_id, created_at, created_by) VALUES (?,?,?,?,?,?)",
                 (family, pattern, reason, group_id, fleet._now(), g.user["username"]))
    recount(conn)
    audit("parc.blocage", f"{family} : {pattern}{f', groupe {group_id}' if group_id else ''}{f' ({reason})' if reason else ''}", commit=False)
    conn.commit()
    flash(_("« %(pattern)s » est bloquée : loco ne l'installe plus. Les agents appliquent le blocage à leur prochain contact (moins de %(v)s min).",
            pattern=pattern, v=fleet.CHECKIN // 60), "ok")
    return redirect(url_for("update_blocks"))


@app.route("/fleet/blocks/<int:rule_id>/remove", methods=["POST"])
@login_required
def update_block_remove(rule_id):
    conn = db()
    rule = conn.execute("SELECT * FROM update_blocks WHERE id=?", (rule_id,)).fetchone()
    if rule is None:
        flash(_("Ce blocage n'existe plus."), "error")
        return redirect(url_for("update_blocks"))
    conn.execute("DELETE FROM update_blocks WHERE id=?", (rule_id,))
    recount(conn)
    audit("parc.deblocage", f"{rule['family']} : {rule['pattern']}", commit=False)
    conn.commit()
    flash(_("« %(pattern)s » n'est plus bloquée : elle peut de nouveau s'installer.", pattern=rule["pattern"]), "ok")
    return redirect(url_for("update_blocks"))


def group_blocks(conn, group_id):
    """Nombre de blocages portés par un groupe : le supprimer les lèverait sans qu'on l'ait demandé."""
    return conn.execute("SELECT COUNT(*) FROM update_blocks WHERE group_id=?", (group_id,)).fetchone()[0]

