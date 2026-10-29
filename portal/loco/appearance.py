"""Apparence du portail par utilisateur : thème, couleur d'accent, transparence des panneaux et fond d'écran.

Stockée en JSON dans users.appearance (suit le compte sur n'importe quel navigateur). Le fond téléversé est un
fichier par utilisateur, servi par une route (CSP img-src 'self'). Le serveur rend thème/accent/transparence/fond
en attributs data-* sur <html> ; theme.js applique le fond et la transparence avant l'affichage (sans flash).
"""
import glob
import json
import os

from flask import abort, g, jsonify, request, send_file

from . import app, config
from .auth import audit, login_required
from .db import db

# Thèmes (clés data-theme, définies en CSS) et couleurs d'accent (clés data-accent).
THEMES = ("oled", "dim", "light", "midnight", "sepia")
ACCENTS = ("neutral", "blue", "green", "violet", "orange", "pink", "teal", "amber")
# Fonds dégradés fournis (rendus en CSS, aucun fichier) : clé « g:<nom> ».
GRADIENTS = ("aurora", "ocean", "forest", "dusk", "rose", "slate")
# Fonds photo fournis (fichiers portal/static/wallpapers/<nom>.jpg + <nom>-thumb.jpg) : clé « w:<nom> ».
WALLPAPERS = ("montagne", "foret", "desert", "dunes", "ocean", "aurore", "nuit", "hiver")
ALPHA_MIN, ALPHA_MAX = 55, 100          # opacité des panneaux, en %

# Presets prêts à l'emploi : (clé, libellé, thème, accent, transparence, fond).
PRESETS = (
    ("oled", "OLED", "oled", "neutral", 100, ""),
    ("clair", "Clair", "light", "neutral", 100, ""),
    ("ocean", "Océan", "dim", "blue", 82, "g:ocean"),
    ("alpes", "Alpes", "midnight", "blue", 80, "w:montagne"),
    ("boreale", "Boréale", "oled", "teal", 80, "w:aurore"),
    ("desert", "Désert", "sepia", "amber", 84, "w:dunes"),
)

BG_DIR = os.path.join(config.STATE, "appearance")
MAX_BG = 8 * 1024 * 1024                 # 8 Mo
# Signatures des formats d'image acceptés -> extension servie.
_SIGS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"))


def default():
    return {"theme": "oled", "accent": "neutral", "alpha": 100, "bg": ""}


def _valid_bg(bg):
    if not isinstance(bg, str):
        return False
    return (bg == "u"
            or (bg.startswith("g:") and bg[2:] in GRADIENTS)
            or (bg.startswith("w:") and bg[2:] in WALLPAPERS))


def parse(raw):
    """Apparence sûre à partir du JSON stocké (ou d'un dict de formulaire) ; valeurs inconnues ignorées."""
    data = raw if isinstance(raw, dict) else {}
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    out = default()
    if data.get("theme") in THEMES:
        out["theme"] = data["theme"]
    if data.get("accent") in ACCENTS:
        out["accent"] = data["accent"]
    try:
        out["alpha"] = max(ALPHA_MIN, min(ALPHA_MAX, int(data.get("alpha", 100))))
    except (ValueError, TypeError):
        pass
    if _valid_bg(data.get("bg")):
        out["bg"] = data["bg"]
    return out


def for_user(user):
    return parse(user["appearance"]) if user and "appearance" in user.keys() else default()


def _detect(blob):
    if blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return "webp"
    for sig, ext in _SIGS:
        if blob.startswith(sig):
            return ext
    return None


def _bg_file(uid):
    found = glob.glob(os.path.join(BG_DIR, f"{int(uid)}.*"))
    return found[0] if found else None


def has_background(user):
    return bool(user) and _bg_file(user["id"]) is not None


@app.route("/account/appearance", methods=["POST"])
@login_required
def account_appearance():
    appear = parse(request.form)
    if appear["bg"] == "u" and not _bg_file(g.user["id"]):
        appear["bg"] = ""                                 # pas d'image téléversée : revenir à aucun fond
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    return jsonify(appear)


@app.route("/account/appearance/background", methods=["POST"])
@login_required
def account_appearance_background():
    upload = request.files.get("background")
    if not upload:
        abort(400)
    blob = upload.read(MAX_BG + 1)
    if len(blob) > MAX_BG:
        return jsonify(error="trop-grand"), 413
    ext = _detect(blob)
    if not ext:
        return jsonify(error="format"), 415
    os.makedirs(BG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(BG_DIR, f"{g.user['id']}.*")):
        os.unlink(old)
    path = os.path.join(BG_DIR, f"{g.user['id']}.{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    os.chmod(path, 0o640)
    appear = parse(g.user["appearance"])
    appear["bg"] = "u"
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    audit("compte.fond", f"{len(blob)} o")
    return jsonify(appear)


@app.route("/account/appearance/background/remove", methods=["POST"])
@login_required
def account_appearance_background_delete():
    for old in glob.glob(os.path.join(BG_DIR, f"{g.user['id']}.*")):
        os.unlink(old)
    appear = parse(g.user["appearance"])
    if appear["bg"] == "u":
        appear["bg"] = ""
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    return jsonify(appear)


@app.route("/appearance/background.img")
@login_required
def appearance_background():
    path = _bg_file(g.user["id"])
    if not path:
        abort(404)
    resp = send_file(path, max_age=0)
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp
