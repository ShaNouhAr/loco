"""Postes réservés : un compte se réserve un appareil du parc et choisit un mot de passe ; les autres comptes doivent le
saisir pour ouvrir son terminal, son bureau à distance (console comprise) ou ses fichiers — et, sur Android, son
terminal adb, son écran et son déverrouillage —, et pour le redémarrer (redémarrage, mise à jour suivie d'un
redémarrage, mise à niveau du système). Tout le reste reste ouvert à leurs droits : inventaire, mises à jour sans
redémarrage, scripts, déploiements, retrait de l'agent…

Le mot de passe n'est gardé que haché. Saisi, il ouvre l'accès une demi-heure, à ce compte et dans ce navigateur ;
changé par le propriétaire, il referme les accès déjà ouverts. Les outils MCP et l'API d'un autre compte ne peuvent pas
le saisir : ils sont refusés ; une planification ou une vague ne redémarre le poste que pour son propriétaire. La
réservation disparaît avec l'appareil, ou avec le compte qui la tient.
"""
import json
import time

from flask import flash, g, has_request_context, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import app, fleet
from .auth import audit, login_required, password_problem
from .db import connect, db
from .i18n import _, n_

PROTECTED = ("terminal", "desktop", "files", "adb")
REBOOTING = ("reboot", "os-upgrade")      # la mise à niveau du système finit par un redémarrage
OPEN_FOR = 1800                 # accès ouvert par le mot de passe, en secondes
TRIES, TRIES_WINDOW = 5, 900    # essais manqués permis par compte et par appareil, sur un quart d'heure
SESSION_KEY = "reserved_open"


def get(dev_id, conn=None):
    """Réservation d'un appareil, avec le nom du compte qui la tient (None : libre)."""
    return (conn or db()).execute("SELECT r.*, u.username AS owner FROM device_reservations r JOIN users u ON u.id=r.user_id "
                                  "WHERE r.device_id=?", (dev_id,)).fetchone()


def owners(conn=None):
    """{appareil: compte} des appareils réservés (liste du parc)."""
    return {r[0]: r[1] for r in (conn or db()).execute(
        "SELECT r.device_id, u.username FROM device_reservations r JOIN users u ON u.id=r.user_id")}


def _opened(row):
    """Jusqu'à quand le compte connecté a ouvert, par le mot de passe, ce poste réservé (None : fermé)."""
    entry = (session.get(SESSION_KEY) or {}).get(str(row["device_id"]))
    if isinstance(entry, list) and len(entry) == 2 and entry[1] == row["changed_at"] and entry[0] > time.time():
        return entry[0]
    return None


def allowed(dev_id):
    """Le compte connecté peut-il ouvrir un accès protégé de cet appareil ? Libre, à lui, ou mot de passe saisi."""
    row = get(dev_id)
    return row is None or row["user_id"] == g.user["id"] or _opened(row) is not None


def reboots(action, params):
    """Action qui redémarre le poste : redémarrage, mise à niveau du système, mise à jour suivie d'un redémarrage."""
    return action in REBOOTING or (action == "update" and bool((params or {}).get("reboot")))


def may_reboot(dev, user=None):
    """Redémarrage permis : poste libre, ou à ce compte ; pour la personne connectée qui le demande à l'instant (user
    None), aussi une fois le mot de passe saisi. user = compte pour qui une planification ou une vague le demande, plus
    tard et hors de sa page : seul le propriétaire passe, quelle que soit la requête qui le déclenche."""
    row = get(dev["id"])
    if row is None:
        return True
    if user is None:
        if has_request_context() and g.get("user") is not None:
            return row["user_id"] == g.user["id"] or _opened(row) is not None
        return False
    return row["owner"] == user


def blocked_ids():
    """Appareils que le compte connecté ne peut pas redémarrer (réservés par d'autres, mot de passe non saisi)."""
    return {r["device_id"] for r in db().execute("SELECT device_id, user_id, changed_at FROM device_reservations")
            if r["user_id"] != g.user["id"] and _opened(r) is None}


def reboot_message(row):
    return _("Ce poste est réservé par %(owner)s : son redémarrage demande le mot de passe de la réservation, à saisir sur "
             "sa fiche.", owner=row["owner"])


def view(dev):
    """Ce que la fiche montre : qui tient le poste, depuis quand, et l'état de l'accès pour le compte connecté."""
    row = get(dev["id"])
    if row is None:
        return None
    mine = row["user_id"] == g.user["id"]
    until = None if mine else _opened(row)
    return {"owner": row["owner"], "mine": mine, "since": row["created_at"], "until": until,
            "until_text": time.strftime("%H:%M", time.localtime(until)) if until else "", "locked": not mine and until is None}


def message(row):
    return _("Ce poste est réservé par %(owner)s : son terminal, son bureau à distance, ses fichiers et son redémarrage "
             "demandent le mot de passe de la réservation.", owner=row["owner"])


def guard(dev, kind):
    """Réservation qui ferme cet accès au compte connecté (None si l'accès est permis)."""
    if kind not in PROTECTED:
        return None
    row = get(dev["id"])
    if row is None or row["user_id"] == g.user["id"] or _opened(row) is not None:
        return None
    return row


def refuse(dev, row, kind):
    """Réponse à une ouverture refusée : JSON pour la page qui l'a demandée en arrière-plan (fichiers, écran adb), sinon
    la page du mot de passe, qui rejoue la même ouverture une fois l'accès ouvert."""
    audit("parc.reservation.refus", f"{dev['hostname']} (réservé par {row['owner']}) : {kind} sans mot de passe")
    if request.headers.get("X-Requested-With") == "fetch" or kind == "files":
        return {"error": message(row), "code": "reserved", "owner": row["owner"],
                "unlock": url_for("fleet_reservation_unlock", dev_id=dev["id"])}, 403
    keep = {k: request.form.get(k, "") for k in ("kind", "view", "consent") if request.form.get(k)}
    return render_template("reservation_unlock.html", dev=dev, owner=row["owner"], keep=keep, error=""), 403


def stream_allowed(dev_id, user_id, since):
    """Pour un flux déjà ouvert (écran adb), hors de la requête : vrai tant que le poste n'a pas été réservé, ou son mot
    de passe changé, par un autre compte depuis son ouverture."""
    conn = connect()
    try:
        row = conn.execute("SELECT user_id, changed_at FROM device_reservations WHERE device_id=?", (dev_id,)).fetchone()
    finally:
        conn.close()
    return row is None or row["user_id"] == user_id or row["changed_at"] <= since


def close_others(conn, dev, user_id, reason):
    """Ferme les sessions protégées ouvertes sur l'appareil par d'autres comptes (personnes et accès MCP)."""
    from . import remote
    sids = [r[0] for r in conn.execute(
        "SELECT id FROM remote_sessions WHERE device_id=? AND user_id!=? AND state IN ('waiting','active') AND kind IN "
        "(" + ",".join("?" * len(PROTECTED)) + ")", (dev["id"], user_id, *PROTECTED))]
    for sid in sids:
        remote._close(sid, reason, g.user["username"])
    return len(sids)


def hold_restarts(conn, dev, username):
    """Nouvelle réservation : les redémarrages demandés par d'autres comptes, pas encore transmis au poste, sont
    annulés ; une mise à jour reste, sans redémarrage. Renvoie leur nombre."""
    held = 0
    for c in conn.execute("SELECT id, action, params FROM device_commands WHERE device_id=? AND state='queued' AND created_by!=?",
                          (dev["id"], username)).fetchall():
        params = json.loads(c["params"] or "{}")
        if not reboots(c["action"], params):
            continue
        if c["action"] == "update":
            conn.execute("UPDATE device_commands SET params=? WHERE id=?", (json.dumps(dict(params, reboot=False), sort_keys=True), c["id"]))
        else:
            conn.execute("UPDATE device_commands SET state='cancelled', finished_at=?, output=? WHERE id=?",
                         (time.strftime("%Y-%m-%d %H:%M:%S"), "Poste réservé avant transmission à l'agent : redémarrage annulé.", c["id"]))
        held += 1
    return held


def _back(dev_id):
    return redirect(url_for("fleet_device", dev_id=dev_id))


# --------------------------------------------------------------------------- routes

@app.route("/fleet/<int:dev_id>/reservation", methods=["POST"])
@login_required
def fleet_reserve(dev_id):
    """Se réserver le poste (ou changer le mot de passe de sa réservation)."""
    dev = fleet._get(dev_id)
    row = get(dev_id)
    if row is not None and row["user_id"] != g.user["id"]:
        flash(_("Ce poste est déjà réservé par %(owner)s.", owner=row["owner"]), "error")
        return _back(dev_id)
    password, confirm = request.form.get("password", ""), request.form.get("confirm", "")
    problem = password_problem(password, g.user["username"])
    if problem:
        flash(problem, "error")
        return _back(dev_id)
    if password != confirm:
        flash(_("Les deux mots de passe ne correspondent pas."), "error")
        return _back(dev_id)
    conn = db()
    now = time.time()
    conn.execute("INSERT INTO device_reservations (device_id, user_id, password_hash, created_at, changed_at) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(device_id) DO UPDATE SET password_hash=excluded.password_hash, changed_at=excluded.changed_at",
                 (dev_id, g.user["id"], generate_password_hash(password), time.strftime("%Y-%m-%d %H:%M:%S"), now))
    closed = close_others(conn, dev, g.user["id"], _("Poste réservé : saisissez le mot de passe de la réservation."))
    held = 0 if row else hold_restarts(conn, dev, g.user["username"])
    audit("parc.reservation", f"{dev['hostname']} : {'mot de passe changé' if row else 'réservé'}"
          + (f", {closed} session(s) d'autres comptes fermée(s)" if closed else "")
          + (f", {held} redémarrage(s) d'autres comptes annulé(s)" if held else ""))
    flash(_("Mot de passe de la réservation changé.") if row else
          _("Poste réservé : les autres comptes devront saisir votre mot de passe pour son terminal, son bureau à distance, "
            "ses fichiers et son redémarrage."), "ok")
    if held:
        flash(n_("%(num)s redémarrage demandé par un autre compte, pas encore parti vers le poste, est annulé (une mise à jour reste, sans redémarrer).",
                 "%(num)s redémarrages demandés par d'autres comptes, pas encore partis vers le poste, sont annulés (une mise à jour reste, sans redémarrer).",
                 held), "ok")
    return _back(dev_id)


@app.route("/fleet/<int:dev_id>/reservation/release", methods=["POST"])
@login_required
def fleet_reserve_release(dev_id):
    """Libérer le poste : seul le compte qui le tient le peut."""
    dev = fleet._get(dev_id)
    row = get(dev_id)
    if row is None or row["user_id"] != g.user["id"]:
        flash(_("Seul %(owner)s peut libérer ce poste.", owner=row["owner"]) if row else _("Ce poste n'est pas réservé."), "error")
        return _back(dev_id)
    db().execute("DELETE FROM device_reservations WHERE device_id=?", (dev_id,))
    audit("parc.reservation.liberation", dev["hostname"])
    flash(_("Poste libéré : ses accès et son redémarrage ne demandent plus de mot de passe."), "ok")
    return _back(dev_id)


@app.route("/fleet/<int:dev_id>/reservation/unlock", methods=["POST"])
@login_required
def fleet_reservation_unlock(dev_id):
    """Saisie du mot de passe d'un poste réservé : ouvre l'accès une demi-heure. Venue de la page du mot de passe, la même
    ouverture (terminal, bureau…) est rejouée aussitôt."""
    dev = fleet._get(dev_id)
    row = get(dev_id)
    fetch = request.headers.get("X-Requested-With") == "fetch"
    replay = request.form.get("kind") in PROTECTED

    def answer(error=None, until=None):
        if error is None:
            if replay:
                return redirect(url_for("remote_open", dev_id=dev_id), code=307)     # même formulaire, même jeton
            if fetch:
                return {"ok": True, "until": until, "until_text": time.strftime("%H:%M", time.localtime(until)) if until else ""}
            flash(_("Accès ouvert pour une demi-heure."), "ok")
            return _back(dev_id)
        if fetch:
            return {"error": error}, 403
        if replay:
            keep = {k: request.form.get(k, "") for k in ("kind", "view", "consent") if request.form.get(k)}
            return render_template("reservation_unlock.html", dev=dev, owner=row["owner"], keep=keep, error=error), 403
        flash(error, "error")
        return _back(dev_id)

    if row is None or row["user_id"] == g.user["id"]:
        return answer()
    conn = db()
    now = time.time()
    conn.execute("DELETE FROM device_unlock_failures WHERE at<?", (now - TRIES_WINDOW,))
    # Essai compté avant d'être vérifié (validé tout de suite) : des envois en parallèle ne dépassent pas la limite.
    conn.execute("INSERT INTO device_unlock_failures (at, user_id, device_id) VALUES (?,?,?)", (now, g.user["id"], dev_id))
    conn.commit()
    tried = conn.execute("SELECT COUNT(*) FROM device_unlock_failures WHERE user_id=? AND device_id=? AND at>=?",
                         (g.user["id"], dev_id, now - TRIES_WINDOW)).fetchone()[0]
    if tried > TRIES:
        audit("parc.reservation.refus", f"{dev['hostname']} (réservé par {row['owner']}) : trop d'essais")
        return answer(_("Trop d'essais manqués : réessayez dans un quart d'heure."))
    if not check_password_hash(row["password_hash"], request.form.get("password", "")):
        audit("parc.reservation.refus", f"{dev['hostname']} (réservé par {row['owner']}) : mot de passe incorrect")
        return answer(_("Mot de passe incorrect."))
    until = time.time() + OPEN_FOR
    opened = {k: v for k, v in (session.get(SESSION_KEY) or {}).items() if isinstance(v, list) and len(v) == 2 and v[0] > time.time()}
    opened[str(dev_id)] = [until, row["changed_at"]]
    session[SESSION_KEY] = opened
    conn.execute("DELETE FROM device_unlock_failures WHERE user_id=? AND device_id=?", (g.user["id"], dev_id))
    audit("parc.reservation.ouverture", f"{dev['hostname']} (réservé par {row['owner']}) : accès ouvert une demi-heure")
    return answer(until=until)


@app.route("/fleet/<int:dev_id>/reservation/lock", methods=["POST"])
@login_required
def fleet_reservation_lock(dev_id):
    """Refermer tout de suite l'accès ouvert par le mot de passe."""
    fleet._get(dev_id)
    opened = dict(session.get(SESSION_KEY) or {})
    if opened.pop(str(dev_id), None) is not None:
        session[SESSION_KEY] = opened
    flash(_("Accès refermé."), "ok")
    return _back(dev_id)
