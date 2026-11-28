"""Apparence du portail : celle de chaque compte (thème, couleur d'accent, transparence des panneaux, fond d'écran)
dans le cadre fixé pour toute l'instance (logo, couleur et fond de l'entreprise, apparence par défaut, thèmes
proposés, couleur et fond imposés ou au choix).

L'apparence d'un compte est stockée en JSON dans users.appearance (suit le compte sur n'importe quel navigateur) ; le
cadre de l'instance dans settings (appearance_policy). Les fonds téléversés sont des fichiers servis par une route
(CSP img-src 'self'). Le serveur rend thème/accent/transparence/fond en attributs data-* sur <html> ; theme.js
applique la transparence et la couleur de l'entreprise avant l'affichage (sans flash).
"""
import glob
import hashlib
import json
import os
import re
import time

from flask import abort, flash, g, has_request_context, jsonify, redirect, render_template, request, send_file, url_for

from . import app, config
from .auth import audit, login_required
from .db import db
from .i18n import L_, N_, _

# Thèmes (clés data-theme, définies en CSS) et couleurs d'accent (clés data-accent).
THEMES = ("oled", "dim", "light", "midnight", "sepia", "sapin")
ACCENTS = ("neutral", "blue", "green", "violet", "orange", "pink", "teal", "amber")
# Fonds dégradés fournis (rendus en CSS, aucun fichier) : clé « g:<nom> ».
GRADIENTS = ("aurora", "ocean", "forest", "dusk", "rose", "slate")
# Fonds photo fournis (fichiers portal/static/wallpapers/<nom>.jpg + <nom>-thumb.jpg) : clé « w:<nom> ».
WALLPAPERS = ("montagne", "foret", "desert", "dunes", "ocean", "aurore", "nuit", "hiver", "brume")
ALPHA_MIN, ALPHA_MAX = 0, 100           # opacité des panneaux, en %

# Presets prêts à l'emploi : (clé, libellé, thème, accent, transparence, fond).
PRESETS = (                              # libellés marqués N_ : le panneau Apparence les traduit à l'affichage (_(label))
    ("oled", N_("OLED"), "oled", "neutral", 100, ""),
    ("clair", N_("Clair"), "light", "neutral", 100, ""),
    ("brume", N_("Brume"), "dim", "neutral", 88, "w:brume"),
    ("ocean", N_("Océan"), "dim", "blue", 82, "g:ocean"),
    ("alpes", N_("Alpes"), "midnight", "blue", 90, "w:montagne"),
    ("foret", N_("Forêt"), "sapin", "green", 86, "w:foret"),
    ("boreale", N_("Boréale"), "oled", "teal", 80, "w:aurore"),
    ("desert", N_("Désert"), "sepia", "amber", 84, "w:dunes"),
)

BG_DIR = os.path.join(config.STATE, "appearance")
MAX_BG = 8 * 1024 * 1024                 # 8 Mo
# Signatures des formats d'image acceptés -> extension servie.
_SIGS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"))

# Logo global de l'instance (un seul fichier, partagé par tout le monde) : BG_DIR/logo.<ext>.
MAX_LOGO = 2 * 1024 * 1024               # 2 Mo
_LOGO_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}


# Libellés (page d'administration de l'instance).
THEME_LABELS = {"oled": L_("OLED"), "dim": L_("Sombre doux"), "light": L_("Clair"), "midnight": L_("Minuit"), "sepia": L_("Sépia"), "sapin": L_("Sapin")}
ACCENT_LABELS = {"neutral": L_("Neutre"), "blue": L_("Bleu"), "green": L_("Vert"), "violet": L_("Violet"), "orange": L_("Orange"),
                 "pink": L_("Rose"), "teal": L_("Sarcelle"), "amber": L_("Ambre")}
GRADIENT_LABELS = {"aurora": L_("Aurore"), "ocean": L_("Océan"), "forest": L_("Forêt"), "dusk": L_("Crépuscule"),
                   "rose": L_("Rose"), "slate": L_("Ardoise")}
WALLPAPER_LABELS = {"montagne": L_("Montagne"), "foret": L_("Forêt"), "desert": L_("Désert"), "dunes": L_("Dunes"),
                    "ocean": L_("Océan"), "aurore": L_("Aurore"), "nuit": L_("Nuit étoilée"), "hiver": L_("Hiver"), "brume": L_("Brume")}

# Défaut à l'installation = preset « Brume » : sombre et sobre, accent neutre, crêtes dans la brume en fond.
FACTORY = {"theme": "dim", "accent": "neutral", "alpha": 88, "bg": "w:brume"}
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


def _stored():
    row = db().execute("SELECT value FROM settings WHERE key=?", (POLICY_KEY,)).fetchone()
    return _loads(row["value"] if row else "")


def _store(data):
    db().execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (POLICY_KEY, json.dumps(data)))


def _login_clean(raw, base, brand, company):
    """Apparence de l'écran de connexion : une image personnelle n'y serait pas servie (réservée à son compte)."""
    look = _clean(raw, base, brand, company)
    if look["bg"] == "u":
        look["bg"] = ""
    return look


def policy():
    """Cadre de l'instance : {brand, themes, accent_free, background_free, default, login}, relu une fois par requête.
    login : apparence choisie pour l'écran de connexion, ou None s'il suit l'apparence par défaut."""
    if has_request_context() and "appearance_policy" in g:
        return g.appearance_policy
    data = _stored()
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
    # Hors du cadre des thèmes proposés : ceux-ci limitent le choix de chaque compte, pas ce que l'instance affiche.
    login = _loads(data.get("login"))
    out = {"brand": brand, "themes": themes, "accent_free": data.get("accent_free") is not False,
           "background_free": data.get("background_free") is not False, "default": default_look,
           "login": _login_clean(login, default_look, brand, company) if login else None}
    if has_request_context():
        g.appearance_policy = out
    return out


def default():
    """Apparence par défaut de l'instance : nouveaux comptes, « Réinitialiser »."""
    return dict(policy()["default"])


def login_look():
    """Apparence vue sans compte (écran de connexion) : celle choisie, sinon l'apparence par défaut."""
    pol = policy()
    return dict(pol["login"] or pol["default"])


def _valid_bg(bg):
    if not isinstance(bg, str):
        return False
    return (bg == "u"
            or (bg.startswith("g:") and bg[2:] in GRADIENTS)
            or (bg.startswith("w:") and bg[2:] in WALLPAPERS))


# Gardé dans le même JSON que l'apparence, sans en faire partie : photo de profil (et d'où elle vient), encadrés masqués.
KEPT_KEYS = ("avatar", "avatar_sso", "avatar_own", "dismissed")


def _with_kept(appear, stored):
    """Apparence à enregistrer, avec ce qui n'en fait pas partie repris tel quel (un changement de thème ne retire ni la
    photo de profil ni les encadrés masqués)."""
    raw = _loads(stored)
    return dict(appear, **{k: raw[k] for k in KEPT_KEYS if k in raw})


def parse(raw):
    """Apparence sûre à partir du JSON stocké (ou d'un dict de formulaire) ; valeurs inconnues ignorées. Le choix du
    logo (administrateurs, voir logo_for) n'est gardé que s'il en est un."""
    pol = policy()
    data = _loads(raw)
    out = _clean(data, pol["default"], pol["brand"], company_file() is not None)
    if data.get("logo") in LOGO_CHOICES[1:]:
        out["logo"] = data["logo"]
    return out


def free(user=None):
    """Les comptes au profil Administrateur règlent leur apparence comme ils l'entendent : le cadre de l'entreprise
    (thèmes proposés, couleur et fond imposés) ne s'applique pas à eux, et ils peuvent remplacer le logo pour eux seuls."""
    from .features import is_administrator
    return is_administrator(user)


def constrain(appear):
    """Apparence ramenée dans le cadre de l'instance : thème proposé, couleur et fond imposés le cas échéant."""
    pol = policy()
    out, d = dict(appear), pol["default"]
    out.pop("logo", None)                              # réservé aux administrateurs, qui échappent au cadre
    if out["theme"] not in pol["themes"]:
        out["theme"] = d["theme"]
    if not pol["accent_free"]:
        out["accent"] = d["accent"]
    if not pol["background_free"]:
        out["bg"], out["alpha"] = d["bg"], d["alpha"]
    return out


def for_user(user):
    if not user:
        return login_look()
    if "appearance" not in user.keys():
        return default()
    look = parse(user["appearance"])
    return look if free(user) else constrain(look)


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


def _background_revision(path):
    try:
        stat = os.stat(path)
    except (FileNotFoundError, TypeError):
        return None
    return f"{os.path.basename(path)}-{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _background_url(endpoint, path):
    revision = _background_revision(path)
    return url_for(endpoint, v=revision) if revision else None


def background_urls(user):
    return {
        "u": _background_url("appearance_background", _bg_file(user["id"])) if user else None,
        "c": _background_url("appearance_company", company_file()),
    }


def _send_background(path, private=False):
    revision = _background_revision(path)
    requested = request.args.get("v")
    if revision is None or (requested is not None and requested != revision):
        abort(404)
    resp = send_file(path, max_age=0)
    resp.headers["Cache-Control"] = (
        ("private" if private else "public") + ", max-age=604800, immutable"
        if requested else ("private, no-cache" if private else "no-cache")
    )
    if private:
        resp.vary.add("Cookie")
    return resp


@app.route("/account/appearance", methods=["POST"])
@login_required
def account_appearance():
    appear = parse(request.form)
    appear.pop("logo", None)
    if not free():
        appear = constrain(appear)
    else:
        kept = parse(g.user["appearance"]).get("logo")      # le logo a sa propre route : son choix est gardé
        if kept:
            appear["logo"] = kept
    if appear["bg"] == "u" and not _bg_file(g.user["id"]):
        appear["bg"] = ""                                 # pas d'image téléversée : revenir à aucun fond
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(_with_kept(appear, g.user["appearance"])), g.user["id"]))
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
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(_with_kept(appear, g.user["appearance"])), g.user["id"]))
    db().commit()
    audit("compte.fond", f"{len(blob)} o")
    return jsonify({**appear, "background_url": _background_url("appearance_background", path)})


@app.route("/account/appearance/background/remove", methods=["POST"])
@login_required
def account_appearance_background_delete():
    for old in glob.glob(os.path.join(BG_DIR, f"{g.user['id']}.*")):
        os.unlink(old)
    appear = parse(g.user["appearance"])
    if appear["bg"] == "u":
        appear["bg"] = ""
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(_with_kept(appear, g.user["appearance"])), g.user["id"]))
    db().commit()
    return jsonify(appear)


@app.route("/appearance/background.img")
@login_required
def appearance_background():
    return _send_background(_bg_file(g.user["id"]), private=True)


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


# --- logo personnel d'un administrateur : celui de l'instance (par défaut), celui de loco, ou son image ---

LOGO_CHOICES = ("", "loco", "u")


def _user_logo_file(uid):
    found = glob.glob(os.path.join(BG_DIR, f"logo-u{int(uid)}.*"))
    return found[0] if found else None


def has_user_logo(user):
    return bool(user) and free(user) and _user_logo_file(user["id"]) is not None


def logo_for(user):
    """Logo montré à ce compte : celui de l'instance, sauf choix contraire d'un administrateur (logo d'origine de loco,
    ou image à lui, servie à lui seul). L'écran de connexion et les autres comptes gardent celui de l'instance."""
    if user and "appearance" in user.keys() and free(user):
        choice = parse(user["appearance"]).get("logo", "")
        if choice == "loco":
            return None
        path = _user_logo_file(user["id"]) if choice == "u" else None
        if path:
            ext = os.path.splitext(path)[1].lstrip(".")
            return {"url": f"/appearance/my-logo.img?v={int(os.path.getmtime(path))}", "ext": ext}
    return logo_info()


def _save_logo_choice(choice):
    appear = parse(g.user["appearance"])
    appear.pop("logo", None)
    if choice:
        appear["logo"] = choice
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(_with_kept(appear, g.user["appearance"])), g.user["id"]))
    db().commit()


@app.route("/account/appearance/logo", methods=["POST"])
@login_required
def account_appearance_logo():
    """Logo d'un administrateur, pour lui seul : choix (« » instance, « loco », « u » son image) ou envoi d'une image."""
    if not free():
        abort(403)
    upload = request.files.get("logo")
    if upload:
        blob = upload.read(MAX_LOGO + 1)
        if len(blob) > MAX_LOGO:
            return jsonify(error="trop-grand"), 413
        ext = _detect_logo(blob)
        if not ext:
            return jsonify(error="format"), 415
        os.makedirs(BG_DIR, exist_ok=True)
        for old in glob.glob(os.path.join(BG_DIR, f"logo-u{g.user['id']}.*")):
            os.unlink(old)
        path = os.path.join(BG_DIR, f"logo-u{g.user['id']}.{ext}")
        with open(path, "wb") as f:
            f.write(blob)
        os.chmod(path, 0o640)
        audit("compte.logo", f"{ext}, {len(blob)} o")
        choice = "u"
    else:
        choice = request.form.get("logo", "")
        if choice not in LOGO_CHOICES:
            abort(400)
        if choice == "u" and not _user_logo_file(g.user["id"]):
            return jsonify(error="absent"), 409
    _save_logo_choice(choice)
    return jsonify(logo=choice)


@app.route("/account/appearance/logo/remove", methods=["POST"])
@login_required
def account_appearance_logo_delete():
    for old in glob.glob(os.path.join(BG_DIR, f"logo-u{g.user['id']}.*")):
        os.unlink(old)
    choice = parse(g.user["appearance"]).get("logo", "")
    _save_logo_choice("" if choice == "u" else choice)
    return jsonify(logo="" if choice == "u" else choice)


@app.route("/appearance/my-logo.img")
@login_required
def appearance_my_logo():
    path = _user_logo_file(g.user["id"]) if free() else None
    if not path:
        abort(404)
    ext = os.path.splitext(path)[1].lstrip(".")
    resp = send_file(path, mimetype=_LOGO_MIME.get(ext, "application/octet-stream"), max_age=0)
    resp.headers["Cache-Control"] = "private, no-cache"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------------- photo de profil d'un compte

MAX_AVATAR = 2 * 1024 * 1024               # 2 Mo


def user_avatar(user):
    """Extension de la photo de profil de ce compte (png, jpg…), ou None s'il n'en a pas (son initiale est montrée)."""
    if not user:
        return None
    try:
        return _loads(user["appearance"]).get("avatar") or None
    except (KeyError, TypeError):
        return None


def _avatar_file(uid):
    found = glob.glob(os.path.join(BG_DIR, f"avatar-u{int(uid)}.*"))
    return found[0] if found else None


@app.route("/account/avatar", methods=["POST"])
@login_required
def account_avatar():
    """Photo de profil du compte : image raster envoyée, gardée pour ce compte et montrée à la place de son initiale."""
    upload = request.files.get("avatar")
    if not upload or not upload.filename:
        flash(_("Choisissez une image."), "error")
        return redirect(url_for("account") + "#apparence")
    blob = upload.read(MAX_AVATAR + 1)
    if len(blob) > MAX_AVATAR:
        flash(_("Image trop lourde (2 Mo maximum)."), "error")
        return redirect(url_for("account") + "#apparence")
    ext = _detect(blob)                    # images raster seulement (pas de SVG pour une photo de profil)
    if not ext:
        flash(_("Format non reconnu : PNG, JPEG, GIF ou WebP."), "error")
        return redirect(url_for("account") + "#apparence")
    os.makedirs(BG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(BG_DIR, f"avatar-u{g.user['id']}.*")):
        os.unlink(old)
    path = os.path.join(BG_DIR, f"avatar-u{g.user['id']}.{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    os.chmod(path, 0o640)
    appear = _loads(g.user["appearance"])
    appear["avatar"] = ext
    appear["avatar_own"] = True                           # choisie ici : le SSO ne la remplace plus
    appear.pop("avatar_sso", None)
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    audit("compte.photo", f"{ext}, {len(blob)} o")
    flash(_("Photo de profil enregistrée."), "ok")
    return redirect(url_for("account") + "#apparence")


@app.route("/account/avatar/remove", methods=["POST"])
@login_required
def account_avatar_delete():
    for old in glob.glob(os.path.join(BG_DIR, f"avatar-u{g.user['id']}.*")):
        os.unlink(old)
    appear = _loads(g.user["appearance"])
    appear.pop("avatar", None)
    appear.pop("avatar_sso", None)
    appear["avatar_own"] = True                           # retirée ici : le SSO ne la remet pas
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    flash(_("Photo de profil retirée."), "ok")
    return redirect(url_for("account") + "#apparence")


SSO_REFRESH = 24 * 3600                    # photo du fournisseur SSO relue au plus une fois par jour (ou si elle change)


def avatar_source(user):
    """D'où vient la photo d'un compte : {"sso": libellé du fournisseur} si elle est reprise du SSO, {"own": True} si la
    personne l'a choisie (ou retirée) ici, {} sinon."""
    raw = _loads(user["appearance"]) if user else {}
    if raw.get("avatar_sso") and raw.get("avatar"):
        return {"sso": str(raw["avatar_sso"].get("n") or "")}
    return {"own": True} if raw.get("avatar_own") else {}


def take_sso_picture(conn, user, directory, value, fetch):
    """Photo de profil reprise du fournisseur SSO à la connexion (value : son claim « picture », adresse ou image en
    ligne), sauf si la personne a choisi la sienne ici. Relue si elle a changé ou une fois par jour ; retirée si le
    fournisseur n'en donne plus. fetch(value) rend les octets de l'image ou None. Vrai si la photo a changé."""
    appear = _loads(user["appearance"])
    if appear.get("avatar_own"):
        return False
    known = appear.get("avatar_sso") if isinstance(appear.get("avatar_sso"), dict) else {}
    value = value if isinstance(value, str) else ""

    def drop():                                           # plus de photo chez le fournisseur : celle reprise s'en va
        if not known:
            return False
        for old in glob.glob(os.path.join(BG_DIR, f"avatar-u{int(user['id'])}.*")):
            os.unlink(old)
        appear.pop("avatar", None)
        appear.pop("avatar_sso", None)
        conn.execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), user["id"]))
        return True
    if not value:
        return drop()
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    if (known.get("h") == digest and time.time() - float(known.get("t") or 0) < SSO_REFRESH
            and appear.get("avatar") and _avatar_file(user["id"])):
        return False
    blob = fetch(value)
    if blob is None:                                      # injoignable : la photo du moment est gardée
        return False
    ext = _detect(blob) if len(blob) <= MAX_AVATAR else None      # raster seulement : jamais de SVG
    if not ext:                                           # avatar à initiales (SVG) ou autre chose qu'une photo
        return drop()
    os.makedirs(BG_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(BG_DIR, f"avatar-u{int(user['id'])}.*")):
        os.unlink(old)
    path = os.path.join(BG_DIR, f"avatar-u{int(user['id'])}.{ext}")
    with open(path, "wb") as f:
        f.write(blob)
    os.chmod(path, 0o640)
    appear["avatar"] = ext
    appear["avatar_sso"] = {"d": directory["id"], "n": directory["label"], "h": digest, "t": int(time.time())}
    conn.execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), user["id"]))
    audit("compte.photo", f"reprise de {directory['label']}, {ext}, {len(blob)} o", username=user["username"], commit=False)
    return True


@app.route("/account/avatar/sso", methods=["POST"])
@login_required
def account_avatar_sso():
    """Revenir à la photo du fournisseur SSO : la photo choisie ici est retirée, celle du fournisseur reprise à la
    prochaine connexion par lui."""
    appear = _loads(g.user["appearance"])
    for old in glob.glob(os.path.join(BG_DIR, f"avatar-u{g.user['id']}.*")):
        os.unlink(old)
    for key in ("avatar", "avatar_own", "avatar_sso"):
        appear.pop(key, None)
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    flash(_("Votre photo sera reprise de votre fournisseur d’identité à votre prochaine connexion par lui."), "ok")
    return redirect(url_for("account") + "#apparence")


@app.route("/account/avatar/<int:uid>.img")
@login_required
def account_avatar_img(uid):
    """Photo de profil d'un compte (visible par tout utilisateur connecté : elle n'a rien de secret)."""
    path = _avatar_file(uid)
    if not path:
        abort(404)
    ext = os.path.splitext(path)[1].lstrip(".")
    resp = send_file(path, mimetype=_LOGO_MIME.get(ext, "application/octet-stream"), max_age=0)
    resp.headers["Cache-Control"] = "private, no-cache"
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------------- encadrés d'information masqués (« ne plus afficher »)

_NOTE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def dismissed_notes(user):
    """Clés des encadrés d'information que ce compte a choisi de ne plus afficher (empreintes calculées côté navigateur)."""
    if not user:
        return []
    try:
        data = _loads(user["appearance"]).get("dismissed")
    except (KeyError, TypeError):
        return []
    return [k for k in data if isinstance(k, str)][:300] if isinstance(data, list) else []


@app.route("/account/dismiss", methods=["POST"])
@login_required
def account_dismiss_note():
    """« Ne plus afficher » un encadré d'information : sa clé est gardée pour ce compte (et renvoyée aux pages suivantes)."""
    key = (request.form.get("key") or "").strip()
    if not _NOTE_KEY.match(key):
        abort(400)
    appear = _loads(g.user["appearance"])
    kept = [k for k in (appear.get("dismissed") or []) if isinstance(k, str) and k != key]
    appear["dismissed"] = (kept + [key])[-300:]
    db().execute("UPDATE users SET appearance=? WHERE id=?", (json.dumps(appear), g.user["id"]))
    db().commit()
    return jsonify(ok=True)


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
    resp = _send_background(company_file())
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

def _look_text(look):
    return f"{look['theme']}/{look['accent']}/{look['bg'] or 'aucun'}/{look['alpha']}"


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
    login = None
    if form.get("login_mode") == "custom":
        login = _login_clean({k: form.get("login_" + k) for k in ("theme", "accent", "alpha", "bg")}, look, brand, company)
        data["login"] = login
    _store(data)
    audit("apparence.instance", f"thèmes {', '.join(themes)} ; couleur {'au choix' if data['accent_free'] else 'imposée'} ; "
                                f"fond {'au choix' if data['background_free'] else 'imposé'} ; défaut {_look_text(look)} ; connexion "
                                + (_look_text(login) if login else "comme le défaut") + (f" ; marque {brand}" if brand else ""),
          commit=False)
    db().commit()
    g.pop("appearance_policy", None)
    flash(_("Apparence de l'instance enregistrée : elle s'applique à tous les comptes dès leur prochaine page."), "ok")
    return redirect(url_for("instance_appearance"))


@app.route("/system/appearance/login", methods=["POST"])
@login_required
def instance_appearance_login():
    """Fenêtre Apparence : l'apparence affichée devient celle de l'écran de connexion, le reste du cadre ne bouge pas."""
    if request.form.get("bg") == "u":
        return jsonify(error="personnel"), 400
    pol = policy()
    look = _login_clean(request.form, pol["default"], pol["brand"], company_file() is not None)
    _store(dict(_stored(), login=look))
    audit("apparence.connexion", _look_text(look), commit=False)
    db().commit()
    g.pop("appearance_policy", None)
    return jsonify(look)


@app.route("/system/appearance/reset", methods=["POST"])
@login_required
def instance_appearance_reset():
    db().execute("DELETE FROM settings WHERE key=?", (POLICY_KEY,))
    audit("apparence.instance", "réglages d'origine", commit=False)
    db().commit()
    flash(_("Réglages d'origine rétablis : tous les thèmes, couleurs et fonds sont de nouveau au choix."), "ok")
    return redirect(url_for("instance_appearance"))
