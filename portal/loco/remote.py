"""Relais HTTPS des terminaux et flux RFB de l'agent ; aucune connexion au poste depuis le serveur.

Les octets sont acquittés, bornés et supprimés après lecture. Les sessions sont liées au compte ET à sa
connexion ; un changement de droits/mot de passe, une fermeture ou une perte de contact les révoque.
Les transactions courtes fonctionnent avec gunicorn comme avec waitress, sans serveur WebSocket séparé.
"""
import base64
import binascii
import hashlib
import secrets
import time

from flask import abort, g, redirect, render_template, request, session, url_for

from . import app, fleet, permissions, scope
from .i18n import _
from .auth import audit, can, login_required, require
from .db import db

PACKET = 256 * 1024
BUFFER = 2 * 1024 * 1024
BATCH = 1024 * 1024
OPEN = ("waiting", "active")
# Sessions d'un assistant IA (MCP) : gardées entre deux appels, sans navigateur pour signaler sa présence. Le bureau
# (écran déverrouillé sous GNOME) se ferme plus tôt que le terminal et les fichiers.
MCP_IDLE, MCP_DESKTOP_IDLE = 300, 120
SERVER_SESSIONS, KIND_SESSIONS, DEVICE_SESSIONS = 8, 2, 4    # serveur, poste et type, poste (l'agent Windows n'en sert pas plus)


def _owner():
    return hashlib.sha256(session["csrf"].encode()).hexdigest()


def _close(sid, reason, username="loco"):
    row = db().execute("SELECT s.state,s.kind,d.hostname FROM remote_sessions s JOIN devices d ON d.id=s.device_id "
                       "WHERE s.id=?", (sid,)).fetchone()
    if row is not None and row["state"] != "closed":
        db().execute("INSERT INTO audit(at,username,ip,action,detail) VALUES (?,?,?,?,?)",
                     (fleet._now(), username, request.remote_addr or "", "parc.session.fermeture",
                      f"{row['hostname']} : {row['kind']}, {sid} ({reason[:300]})"))
    db().execute("UPDATE remote_sessions SET state='closed', error=? WHERE id=?", (reason[:300], sid))
    db().execute("DELETE FROM remote_packets WHERE session_id=?", (sid,))


def _prune():
    now = time.time()
    conn = db()
    expired = conn.execute(
        "SELECT s.id FROM remote_sessions s JOIN users u ON u.id=s.user_id JOIN devices d ON d.id=s.device_id "
        "WHERE s.state IN ('waiting','active') AND (s.expires<? OR s.browser_seen<(CASE WHEN s.owner NOT LIKE 'mcp:%' THEN ? "
        "WHEN s.kind='desktop' THEN ? ELSE ? END) OR (s.agent_seen>0 AND s.agent_seen<?) OR (s.agent_seen=0 AND s.created<?) OR "
        "u.disabled=1 OR u.pw_version!=s.pw_version OR d.state!='active')",
        (now, now - 60, now - MCP_DESKTOP_IDLE, now - MCP_IDLE, now - 30, now - 30)).fetchall()
    for row in expired:
        _close(row["id"], "Session expirée ou accès révoqué.")
    fleets = {}
    for row in conn.execute("SELECT s.id,s.kind,s.terminal_fallback,s.device_id,u.role FROM remote_sessions s "
                            "JOIN users u ON u.id=s.user_id WHERE s.state IN ('waiting','active')").fetchall():
        grants = permissions.role_permissions(conn, row["role"])
        if ("fleet.view" not in grants or "remote." + row["kind"] not in grants
                or row["terminal_fallback"] and "remote.terminal" not in grants):
            _close(row["id"], "Droit d'accès distant révoqué.")
            continue
        if row["role"] not in fleets:
            fleets[row["role"]] = scope.resolve(conn, row["role"])
        if fleets[row["role"]] is not None and row["device_id"] not in fleets[row["role"]]:
            _close(row["id"], "Poste sorti du parc du profil.")
    conn.execute("DELETE FROM remote_sessions WHERE state='closed' AND browser_seen<?", (now - 120,))


def make_room(dev, kind, mine=None):
    """Libère la place d'une nouvelle session en fermant d'abord les sessions d'assistant IA inactives (aucun appel en
    cours : un appel relit la session toutes les 50 ms) : une personne passe avant l'assistant, qui rouvrira la sienne
    au besoin. mine (propriétaire « mcp:<accès> ») : l'assistant ne ferme que les siennes."""
    conn, now = db(), time.time()
    owners = ("(owner=? OR owner LIKE ?)", (mine, mine + ":%")) if mine else ("owner LIKE 'mcp:%'", ())
    limits = [("device_id=? AND kind=?", (dev["id"], kind), KIND_SESSIONS), ("1", (), SERVER_SESSIONS)]
    if dev["os_family"] == "windows":
        limits.append(("device_id=?", (dev["id"],), DEVICE_SESSIONS))
    for where, args, limit in limits:
        excess = conn.execute(f"SELECT COUNT(*) FROM remote_sessions WHERE state IN ('waiting','active') AND {where}",
                              args).fetchone()[0] - limit + 1
        if excess > 0:
            for row in conn.execute(f"SELECT id FROM remote_sessions WHERE state IN ('waiting','active') AND {where} AND "
                                    f"{owners[0]} AND browser_seen<? ORDER BY browser_seen LIMIT ?",
                                    (*args, *owners[1], now - 3, excess)).fetchall():
                _close(row["id"], "Session MCP inactive fermée pour laisser la place à une autre session.")


def _relay_transaction():
    """Transaction du relais : paquets et état de session éphémères (exclus des sauvegardes). Sans fsync à chaque
    validation (WAL + synchronous=NORMAL), elle reste cohérente après un arrêt brutal et coûte deux fois moins par requête."""
    conn = db()
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("BEGIN IMMEDIATE")
    _prune()
    return conn


def _integer(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        abort(400)
    if not low <= number <= high:
        abort(400)
    return number


def _append(row, direction, seq, encoded):
    if not isinstance(encoded, str) or len(encoded) > (PACKET * 4 // 3 + 8):
        abort(413)
    if not encoded:
        return
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        abort(400)
    if len(data) > PACKET:
        abort(413)
    key = "input_seq" if direction == "in" else "output_seq"
    seq = _integer(seq, 1, 2**53 - 1)
    if seq <= row[key]:  # retransmission après une réponse perdue : ne jamais rejouer une saisie
        return
    if seq != row[key] + 1:
        abort(409)
    size = db().execute("SELECT COALESCE(SUM(length(data)),0) FROM remote_packets WHERE session_id=? "
                        "AND direction=?", (row["id"], direction)).fetchone()[0]
    if size + len(data) > BUFFER:
        abort(429)
    db().execute("INSERT INTO remote_packets VALUES (?,?,?,?)", (row["id"], direction, seq, data))
    db().execute(f"UPDATE remote_sessions SET {key}=? WHERE id=?", (seq, row["id"]))


def _read(row, direction, ack):
    key = "input_seq" if direction == "in" else "output_seq"
    ack = _integer(ack, 0, row[key])
    db().execute("DELETE FROM remote_packets WHERE session_id=? AND direction=? AND seq<=?",
                 (row["id"], direction, ack))
    packets, size = [], 0
    limit = BATCH if direction == "out" else PACKET
    for packet in db().execute("SELECT seq,data FROM remote_packets WHERE session_id=? AND direction=? "
                               "ORDER BY seq", (row["id"], direction)):
        if size and size + len(packet["data"]) > limit:
            break
        packets.append({"seq": packet["seq"], "data": base64.b64encode(packet["data"]).decode()})
        size += len(packet["data"])
    return packets


def _browser_session(sid):
    row = db().execute("SELECT s.*,d.hostname,d.os_family FROM remote_sessions s JOIN devices d ON d.id=s.device_id "
                       "WHERE s.id=? AND s.user_id=? AND s.owner=? AND s.pw_version=?",
                       (sid, g.user["id"], _owner(), g.user["pw_version"])).fetchone()
    if row is None:
        abort(404)
    require("remote." + row["kind"])
    return row


@app.route("/fleet/<int:dev_id>/session", methods=["POST"])
@login_required
def remote_open(dev_id):
    dev = fleet._get(dev_id)
    kind = request.form.get("kind")
    if kind not in ("terminal", "desktop", "files") or dev["state"] != "active":
        abort(400)
    require("remote." + kind)
    minimum = (2, 23) if kind == "files" else (2, 4)
    if fleet.version_key(dev["agent_version"]) < minimum:
        abort(409, _("L'agent doit être mis à jour vers la version %(v)s.0.", v='.'.join(map(str, minimum))))
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    _prune()
    make_room(dev, kind)
    if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE state IN ('waiting','active')").fetchone()[0] >= SERVER_SESSIONS:
        abort(409, _('Huit sessions sont déjà ouvertes.'))
    if conn.execute("SELECT COUNT(*) FROM remote_sessions WHERE device_id=? AND kind=? AND state IN ('waiting','active')",
                    (dev_id, kind)).fetchone()[0] >= KIND_SESSIONS:
        abort(409, _('Deux sessions de ce type sont déjà ouvertes sur ce poste.'))
    sid, now = secrets.token_hex(16), time.time()
    conn.execute("INSERT INTO remote_sessions (id,device_id,user_id,owner,pw_version,kind,created,browser_seen,expires,terminal_fallback) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, dev_id, g.user["id"], _owner(), g.user["pw_version"], kind,
                                              now, now, now + 3600,
                                              int(kind == "desktop" and dev["os_family"] != "windows" and can("remote.terminal"))))
    audit("parc.session.ouverture", f"{dev['hostname']} : {kind}, {sid}")
    if kind == "files":                       # onglet intégré à la fiche du poste : pas de page dédiée (audit a validé)
        return {"sid": sid, "io": url_for("remote_io", sid=sid)}
    return redirect(url_for("remote_session", sid=sid, **({"view": "console"} if request.form.get("view") == "console" else {})))


@app.route("/fleet/session/<sid>")
@login_required
def remote_session(sid):
    db().execute("BEGIN IMMEDIATE")
    _prune()
    row = _browser_session(sid)
    if row["browser_attached"] and row["state"] in OPEN:
        _close(sid, "Page rechargée : ouvrez une nouvelle session.", g.user["username"])
    else:
        db().execute("UPDATE remote_sessions SET browser_attached=1 WHERE id=?", (sid,))
    row = _browser_session(sid)
    db().commit()
    return render_template("remote_session.html", remote=row, detached=request.args.get("view") == "console")


@app.route("/fleet/session/<sid>/io", methods=["POST"])
@login_required
def remote_io(sid):
    if (request.content_length or 0) > 400000:
        abort(413)
    conn = _relay_transaction()
    row = _browser_session(sid)
    if request.form.get("close") == "1":
        _close(sid, "Session fermée par l'administrateur.", g.user["username"])
        conn.commit()
        return {"state": "closed", "packets": [], "ack": row["input_seq"]}
    if row["state"] in OPEN:
        _append(row, "in", request.form.get("seq", 0), request.form.get("data", ""))
        cols = _integer(request.form.get("cols", row["cols"]), 20, 500)
        rows = _integer(request.form.get("rows", row["rows"]), 5, 200)
        conn.execute("UPDATE remote_sessions SET browser_seen=?,cols=?,rows=? WHERE id=?", (time.time(), cols, rows, sid))
    packets = _read(row, "out", request.form.get("ack", 0))
    row = conn.execute("SELECT * FROM remote_sessions WHERE id=?", (sid,)).fetchone()
    conn.commit()
    response = {"state": row["state"], "error": row["error"], "packets": packets, "ack": row["input_seq"]}
    return response, 200, {"Cache-Control": "no-store"}


@app.route("/agent/api/remote", methods=["POST"])
def remote_agent():
    dev, data = fleet._device(), fleet._json()
    if dev["state"] != "active":
        abort(403)
    updates = data.get("sessions", [])
    if not isinstance(updates, list) or len(updates) > 8:
        abort(400)
    conn = _relay_transaction()
    requested, acks, finished = [], {}, []
    for update in updates:
        if not isinstance(update, dict) or not isinstance(update.get("id"), str):
            abort(400)
        sid = update["id"]
        row = conn.execute("SELECT * FROM remote_sessions WHERE id=? AND device_id=?", (sid, dev["id"])).fetchone()
        if row is None:
            continue
        requested.append(sid)
        if row["state"] not in OPEN:
            continue
        _append(row, "out", update.get("seq", 0), update.get("data", ""))
        acks[sid] = update.get("ack", 0)
        state = update.get("state", "active")
        if state not in ("active", "closed"):
            abort(400)
        error = str(update.get("error", ""))[:300]
        conn.execute("UPDATE remote_sessions SET state=?,error=?,agent_seen=? WHERE id=?", (state, error, time.time(), sid))
        if state == "closed":
            finished.append(f"{dev['hostname']} : {row['kind']}, {sid} ({error})")
    # Une session active absente de la mémoire de l'agent ne peut pas reprendre un flux PTY/RFB à mi-parcours.
    # Cela arrive après un redémarrage du service ou une mise à jour de l'agent.
    for row in conn.execute("SELECT id FROM remote_sessions WHERE device_id=? AND state='active'", (dev["id"],)).fetchall():
        if row["id"] not in requested:
            _close(row["id"], "Agent redémarré : ouvrez une nouvelle session.")
    output = []
    for row in conn.execute("SELECT * FROM remote_sessions WHERE device_id=?", (dev["id"],)).fetchall():
        if row["state"] not in OPEN and row["id"] not in requested:
            continue
        packets = _read(row, "in", acks.get(row["id"], 0)) if row["state"] in OPEN else []
        output.append({"id": row["id"], "kind": row["kind"], "state": row["state"], "packets": packets,
                       "terminal_fallback": bool(row["terminal_fallback"]),
                       "ack": row["output_seq"], "cols": row["cols"], "rows": row["rows"],
                       "expires_in": max(0, int(row["expires"] - time.time()))})
    from . import monitoring
    if isinstance(data.get("metrics"), dict):            # relevé en direct (agents 2.71 et plus)
        monitoring.record_live(conn, dev["id"], data["metrics"])
    live = monitoring.live_wanted(dev)
    fleet.record_contact(dev, conn)
    conn.commit()
    for detail in finished:
        audit("parc.session.fin", detail, username="agent")
    answer = {"sessions": output, "interval": 0.025 if output else (monitoring.LIVE_INTERVAL if live else 5)}
    if live:
        answer["live"] = monitoring.LIVE_INTERVAL
    return answer, 200, {"Cache-Control": "no-store"}
