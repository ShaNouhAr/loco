"""Code de déverrouillage d'un appareil Android retenu par un utilisateur, pour lui seul.

Le bouton « Déverrouiller » de la session d'écran peut retenir le schéma, le code PIN ou le mot de passe de l'appareil.
Scellé avec la clé du serveur, et lié à l'utilisateur et à l'appareil (une ligne recopiée pour un autre ne s'ouvre pas),
il n'est rendu qu'à cet utilisateur, depuis l'une de ses sessions d'écran sur cet appareil, au moment de déverrouiller :
le navigateur le saisit aussitôt sur l'appareil, sans l'afficher. Le journal note qu'il a été retenu, utilisé ou
oublié, jamais le code.
"""
import json
import re
import time

from flask import abort, g, request

from . import app
from .auth import audit, login_required
from .db import db
from .i18n import _, L_
from .util import date_fr, sealer

METHODS = ("pattern", "pin", "password")
LABELS = {"pattern": L_("Schéma retenu pour vous seul, le %(date)s"),
          "pin": L_("Code PIN retenu pour vous seul, le %(date)s"),
          "password": L_("Mot de passe retenu pour vous seul, le %(date)s")}
_seal, _unseal = sealer(b"loco-unlock-v1")          # scellé avec la clé du serveur


def valid(method, code):
    """Schéma : 2 à 9 points distincts (1 à 9) ; code PIN : 3 à 16 chiffres ; mot de passe : 1 à 64 caractères."""
    if not isinstance(code, str):
        return False
    if method == "pattern":
        return bool(re.fullmatch(r"[1-9]{2,9}", code)) and len(set(code)) == len(code)
    if method == "pin":
        return bool(re.fullmatch(r"[0-9]{3,16}", code))
    if method == "password":
        return 1 <= len(code) <= 64 and all(ord(c) >= 32 for c in code)
    return False


def saved(user_id, device_id, conn=None):
    """Ce que la page peut savoir du code retenu par cet utilisateur pour cet appareil — sa nature et sa date, jamais
    le code —, ou None."""
    row = (conn or db()).execute("SELECT method, saved_at FROM unlock_codes WHERE user_id=? AND device_id=?",
                                 (user_id, device_id)).fetchone()
    if row is None or row["method"] not in METHODS:
        return None
    return {"method": row["method"], "saved_at": row["saved_at"], "label": _(LABELS[row["method"]], date=date_fr(row["saved_at"]))}


def _open(row, user_id, device_id):
    """Le code d'une ligne, s'il s'ouvre avec la clé du serveur et appartient bien à cet utilisateur et cet appareil."""
    try:
        data = json.loads(_unseal(row["secret"]) or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("u") != user_id or data.get("d") != device_id or data.get("m") != row["method"]:
        return None
    code = data.get("c")
    return code if valid(row["method"], code) else None


@app.route("/fleet/session/<sid>/unlock-code", methods=["POST"])
@login_required
def remote_unlock_code(sid):
    """Code retenu pour l'appareil de cette session d'écran (Android), pour l'utilisateur de la session seulement :
    action=save (method, code), use (le rend, pour le saisir aussitôt sur l'appareil) ou forget."""
    from .remote import _browser_session
    row = _browser_session(sid)                           # session de cet utilisateur, droit remote.desktop
    if row["kind"] != "desktop" or row["os_family"] != "android" or not row["device_id"]:
        abort(404)
    user_id, device_id, action = g.user["id"], row["device_id"], request.form.get("action", "")
    conn = db()
    if action == "save":
        method, code = request.form.get("method", ""), request.form.get("code", "")
        if method not in METHODS or not valid(method, code):
            return {"error": _("Code invalide.")}, 400
        secret = _seal(json.dumps({"u": user_id, "d": device_id, "m": method, "c": code}))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO unlock_codes (user_id, device_id, method, secret, saved_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id, device_id) DO UPDATE SET method=excluded.method, secret=excluded.secret, "
                     "saved_at=excluded.saved_at", (user_id, device_id, method, secret, time.strftime("%Y-%m-%d %H:%M")))
        audit("parc.deverrouillage.retenu", f"{row['hostname']} ({method})", commit=False)
        conn.commit()
        return {"ok": True, **saved(user_id, device_id)}
    if action == "use":
        found = conn.execute("SELECT * FROM unlock_codes WHERE user_id=? AND device_id=?", (user_id, device_id)).fetchone()
        code = _open(found, user_id, device_id) if found else None
        if code is None:
            return {"error": _("Aucun code retenu lisible pour cet appareil : saisissez-le de nouveau.")}, 404
        audit("parc.deverrouillage.utilise", f"{row['hostname']} ({found['method']})")
        return {"method": found["method"], "code": code}, 200, {"Cache-Control": "no-store"}
    if action == "forget":
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM unlock_codes WHERE user_id=? AND device_id=?", (user_id, device_id))
        audit("parc.deverrouillage.oubli", row["hostname"], commit=False)
        conn.commit()
        return {"ok": True}
    abort(400)
