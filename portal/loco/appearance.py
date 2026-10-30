"""Apparence du portail : celle de chaque compte (thème, couleur d'accent, transparence des panneaux, fond d'écran)
dans le cadre fixé pour toute l'instance (logo, couleur et fond de l'entreprise, apparence par défaut, thèmes
proposés, couleur et fond imposés ou au choix).

L'apparence d'un compte est stockée en JSON dans users.appearance (suit le compte sur n'importe quel navigateur) ; le
cadre de l'instance dans settings (appearance_policy). Les fonds téléversés sont des fichiers servis par une route
(CSP img-src 'self'). Le serveur rend thème/accent/transparence/fond en attributs data-* sur <html> ; theme.js
applique la transparence et la couleur de l'entreprise avant l'affichage (sans flash).
"""
import glob
import json
import os
import re

from flask import abort, flash, g, has_request_context, jsonify, redirect, render_template, request, send_file, url_for

from . import app, config
from .auth import audit, login_required
from .db import db
from .i18n import L_, _

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
    ("alpes", "Alpes", "midnight", "blue", 90, "w:montagne"),
    ("boreale", "Boréale", "oled", "teal", 80, "w:aurore"),
    ("desert", "Désert", "sepia", "amber", 84, "w:dunes"),
)

BG_DIR = os.path.join(config.STATE, "appearance")
MAX_BG = 8 * 1024 * 1024                 # 8 Mo
# Signatures des formats d'image acceptés -> extension servie.
_SIGS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"))

# Logo global de l'instance (un seul fichier, partagé par tout le monde) : BG_DIR/logo.<ext>.
MAX_LOGO = 2 * 1024 * 1024               # 2 Mo
_LOGO_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}


# Libellés (page d'administration de l'instance).
THEME_LABELS = {"oled": L_("OLED"), "dim": L_("Sombre doux"), "light": L_("Clair"), "midnight": L_("Minuit"), "sepia": L_("Sépia")}
ACCENT_LABELS = {"neutral": L_("Neutre"), "blue": L_("Bleu"), "green": L_("Vert"), "violet": L_("Violet"), "orange": L_("Orange"),
                 "pink": L_("Rose"), "teal": L_("Sarcelle"), "amber": L_("Ambre")}
GRADIENT_LABELS = {"aurora": L_("Aurore"), "ocean": L_("Océan"), "forest": L_("Forêt"), "dusk": L_("Crépuscule"),
                   "rose": L_("Rose"), "slate": L_("Ardoise")}
WALLPAPER_LABELS = {"montagne": L_("Montagne"), "foret": L_("Forêt"), "desert": L_("Désert"), "dunes": L_("Dunes"),
                    "ocean": L_("Océan"), "aurore": L_("Aurore"), "nuit": L_("Nuit étoilée"), "hiver": L_("Hiver")}

# Défaut « attirant » à l'installation = preset « Alpes » : bleu nuit, accent bleu, photo montagne en fond, verre dépoli.
FACTORY = {"theme": "midnight", "accent": "blue", "alpha": 90, "bg": "w:montagne"}
POLICY_KEY = "appearance_policy"
BRAND_RE = re.compile(r"#[0-9a-fA-F]{6}")
# Thèmes clairs (les autres sont sombres) : la page d'administration rappelle d'en garder un de chaque.
LIGHT_THEMES = ("light", "sepia")


def _loads(raw):
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else {}
    except (ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _clean(data, base, brand, company):
    """Apparence sûre : valeurs inconnues ou indisponibles (couleur ou fond d'entreprise absents) remplacées par base."""
    out = dict(base)
    if data.get("theme") in THEMES:
        out["theme"] = data["theme"]
    if data.get("accent") in ACCENTS or (data.get("accent") == "brand" and brand):
        out["accent"] = data["accent"]
    try:
        out["alpha"] = max(ALPHA_MIN, min(ALPHA_MAX, int(data.get("alpha", out["alpha"]))))
    except (ValueError, TypeError):
        pass
    bg = data.get("bg")
    if bg == "" or _valid_bg(bg) or (bg == "c" and company):   # « » = aucun fond, choix explicite distinct de « absent »
        out["bg"] = bg
    return out


def policy():
    """Cadre de l'instance : {brand, themes, accent_free, background_free, default}, relu une fois par requête."""
    if has_request_context() and "appearance_policy" in g:
        return g.appearance_policy
    row = db().execute("SELECT value FROM settings WHERE key=?", (POLICY_KEY,)).fetchone()
    data = _loads(row["value"] if row else "")
    brand = str(data.get("brand") or "")
    brand = brand.lower() if BRAND_RE.fullmatch(brand) else ""
    themes = [t for t in THEMES if t in (data.get("themes") or THEMES)] or list(THEMES)
    company = company_file() is not None
    base = dict(FACTORY, theme=FACTORY["theme"] if FACTORY["theme"] in themes else themes[0])
    default_look = _clean(_loads(data.get("default")), base, brand, company)
    if default_look["bg"] == "u":
        default_look["bg"] = ""                       # le fond personnel d'un compte n'est le défaut de personne
    if default_look["theme"] not in themes:
        default_look["theme"] = themes[0]
    out = {"brand": brand, "themes": themes, "accent_free": data.get("accent_free") is not False,
           "background_free": data.get("background_free") is not False, "default": default_look}
    if has_request_context():
        g.appearance_policy = out
    return out


def default():
    """Apparence par défaut de l'instance : nouveaux comptes, écran de connexion, « Réinitialiser »."""
    return dict(policy()["default"])


def _valid_bg(bg):
    if not isinstance(bg, str):
        return False
    return (bg == "u"
            or (bg.startswith("g:") and bg[2:] in GRADIENTS)
            or (bg.startswith("w:") and bg[2:] in WALLPAPERS))


def parse(raw):
    """Apparence sûre à partir du JSON stocké (ou d'un dict de formulaire) ; valeurs inconnues ignorées."""
    pol = policy()
    return _clean(_loads(raw), pol["default"], pol["brand"], company_file() is not None)


def constrain(appear):
    """Apparence ramenée dans le cadre de l'instance : thème proposé, couleur et fond imposés le cas échéant."""
    pol = policy()
    out, d = dict(appear), pol["default"]
    if out["theme"] not in pol["themes"]:
        out["theme"] = d["theme"]
    if not pol["accent_free"]:
        out["accent"] = d["accent"]
    if not pol["background_free"]:
        out["bg"], out["alpha"] = d["bg"], d["alpha"]
    return out


def for_user(user):
    if not user or "appearance" not in user.keys():
        return default()
    return constrain(parse(user["appearance"]))


def brand_info():
    """Couleur de l'entreprise et texte lisible dessus (noir ou blanc selon sa luminance), ou None."""
    color = policy()["brand"]
    if not color:
        return None
    r, g_, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g_, b)]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return {"color": color, "fg": "#111111" if luminance > 0.45 else "#ffffff"}


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
    appear = constrain(parse(request.form))
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


# --- logo global de l'instance (admin « system.configure »), visible par tout le monde ---

def _detect_logo(blob):
    """Extension d'un logo téléversé : images raster (comme un fond) ou SVG."""
    ext = _detect(blob)
    if ext:
        return ext
    head = blob[:1024].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        return "svg"
    return None


def logo_file():
    found = glob.glob(os.path.join(BG_DIR, "logo.*"))
    return found[0] if found else None


def logo_info():
    """Logo personnalisé de l'instance : {'url': ..., 'ext': ...} (avec anti-cache) ou None si logo loco par défaut."""
    path = logo_file()
    if not path:
        return None
    ext = os.path.splitext(path)[1].lstrip(".")
    return {"url": f"/appearance/logo.img?v={int(os.path.getmtime(path))}", "ext": ext}


@app.route("/appearance/logo.img")
def appearance_logo():
    path = logo_file()
    if not path:
        abort(404)
    ext = os.path.splitext(path)[1].lstrip(".")
    resp = send_file(path, mimetype=_LOGO_MIME.get(ext, "application/octet-stream"), max_age=3600)
    # Même ouvert directement, un SVG téléversé ne doit rien pouvoir exécuter.
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/appearance/logo", methods=["POST"])
def appearance_logo_set():
    upload = request.files.get("logo")
    if not upload:
        abort(400)
    blob = upload.read(MAX_LOGO + 1)
    if len(blob) > MAX_LOGO:
        return _done(error="trop-grand", text=_("Logo trop lourd (2 Mo maximum)."), status=413)
    ext = _detect_logo(blob)
    if not ext:
        return _done(error="format", text=_("Format non reconnu (PNG, JPEG, GIF, WebP ou SVG)."), status=415)
    os.makedirs(BG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(BG_DIR, "logo.*")):
        os.unlink(old)
    path = os.path.join(BG_DIR, f"logo.{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    os.chmod(path, 0o644)
    audit("apparence.logo", f"{ext}, {len(blob)} o")
    return _done(_("Logo enregistré."))


@app.route("/appearance/logo/remove", methods=["POST"])
def appearance_logo_delete():
    for old in glob.glob(os.path.join(BG_DIR, "logo.*")):
        os.unlink(old)
    audit("apparence.logo", "retiré")
    return _done(_("Logo loco rétabli."))


def _done(message="", error=None, text="", status=200):
    """Réponse d'un envoi : la page d'administration (formulaire avec « next ») revient sur elle-même avec un
    message ; les appels en JavaScript reçoivent du JSON (code d'erreur)."""
    if request.form.get("next") == "instance":
        flash(text if error else message, "error" if error else "ok")
        return redirect(url_for("instance_appearance"))
    return (jsonify(error=error), status) if error else jsonify(ok=True)


# --- fond de l'entreprise (un fichier pour toute l'instance), proposé à tous et servi à l'écran de connexion ---

def company_file():
    found = glob.glob(os.path.join(BG_DIR, "company.*"))
    return found[0] if found else None


@app.route("/appearance/company.img")
def appearance_company():
    path = company_file()
    if not path:
        abort(404)
    resp = send_file(path, max_age=0)
    resp.headers["Cache-Control"] = "no-cache"           # revalidé : un nouveau fond s'affiche aussitôt
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/appearance/company", methods=["POST"])
def appearance_company_set():
    upload = request.files.get("background")
    if not upload:
        abort(400)
    blob = upload.read(MAX_BG + 1)
    if len(blob) > MAX_BG:
        return _done(error="trop-grand", text=_("Image trop lourde (8 Mo maximum)."), status=413)
    ext = _detect(blob)
    if not ext:
        return _done(error="format", text=_("Format d'image non reconnu (PNG, JPEG, GIF ou WebP)."), status=415)
    os.makedirs(BG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(BG_DIR, "company.*")):
        os.unlink(old)
    path = os.path.join(BG_DIR, f"company.{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    os.chmod(path, 0o644)
    audit("apparence.fond-entreprise", f"{ext}, {len(blob)} o")
    return _done(_("Fond de l'entreprise enregistré : il est proposé dans les fonds et peut devenir le fond par défaut."))


@app.route("/appearance/company/remove", methods=["POST"])
def appearance_company_delete():
    for old in glob.glob(os.path.join(BG_DIR, "company.*")):
        os.unlink(old)
    audit("apparence.fond-entreprise", "retiré")
    return _done(_("Fond de l'entreprise retiré : les comptes qui l'utilisaient reprennent le fond par défaut."))


# --- page d'administration : apparence de l'instance (droit « Configurer le serveur ») ---

@app.route("/system/appearance")
@login_required
def instance_appearance():
    pol = policy()
    return render_template("instance_appearance.html", pol=pol, brand=brand_info(), company=company_file() is not None,
                           instance_logo=logo_info(), light_themes=LIGHT_THEMES, theme_labels=THEME_LABELS,
                           accent_labels=ACCENT_LABELS, gradient_labels=GRADIENT_LABELS, wallpaper_labels=WALLPAPER_LABELS)


@app.route("/system/appearance", methods=["POST"])
@login_required
def instance_appearance_save():
    form = request.form
    brand = form.get("brand", "").strip().lower() if form.get("brand_on") == "1" else ""
    if brand and not BRAND_RE.fullmatch(brand):
        flash(_("Couleur de l'entreprise invalide : indiquez-la sous la forme #RRVVBB."), "error")
        return redirect(url_for("instance_appearance"))
    themes = [t for t in THEMES if t in form.getlist("themes")]
    if not themes:
        flash(_("Gardez au moins un thème proposé."), "error")
        return redirect(url_for("instance_appearance"))
    company = company_file() is not None
    look = _clean({k: form.get("default_" + k) for k in ("theme", "accent", "alpha", "bg")}, FACTORY, brand, company)
    if look["bg"] == "u":
        look["bg"] = ""
    if look["theme"] not in themes:
        flash(_("Le thème par défaut doit faire partie des thèmes proposés."), "error")
        return redirect(url_for("instance_appearance"))
    data = {"brand": brand, "themes": themes, "accent_free": form.get("accent_free") == "1",
            "background_free": form.get("background_free") == "1", "default": look}
    db().execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (POLICY_KEY, json.dumps(data)))
    audit("apparence.instance", f"thèmes {', '.join(themes)} ; couleur {'au choix' if data['accent_free'] else 'imposée'} ; "
                                f"fond {'au choix' if data['background_free'] else 'imposé'} ; défaut {look['theme']}/"
                                f"{look['accent']}/{look['bg'] or 'aucun'}/{look['alpha']}" + (f" ; marque {brand}" if brand else ""),
          commit=False)
    db().commit()
    g.pop("appearance_policy", None)
    flash(_("Apparence de l'instance enregistrée : elle s'applique à tous les comptes dès leur prochaine page."), "ok")
    return redirect(url_for("instance_appearance"))


@app.route("/system/appearance/reset", methods=["POST"])
@login_required
def instance_appearance_reset():
    db().execute("DELETE FROM settings WHERE key=?", (POLICY_KEY,))
    audit("apparence.instance", "réglages d'origine", commit=False)
    db().commit()
    flash(_("Réglages d'origine rétablis : tous les thèmes, couleurs et fonds sont de nouveau au choix."), "ok")
    return redirect(url_for("instance_appearance"))
