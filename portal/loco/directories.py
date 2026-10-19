"""Annuaires LDAP et RADIUS : connexion avec le formulaire de loco, nouvelles identités en attente d'approbation."""
import base64
import datetime
import hashlib
import hmac
import json
import re
import secrets
import urllib.parse

from flask import abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import app, permissions
from .auth import audit, can_assign, lock_access, login_required, require
from .db import db
from .directory_protocols import DirectoryError, USERNAME_RE, ldap_authenticate, radius_authenticate

KINDS = {"ldap": "LDAP / Active Directory", "radius": "RADIUS"}
# Modèles LDAP : filtre de recherche et attributs habituels.
LDAP_PRESETS = {
    "ad": ("Active Directory", "(&(objectClass=user)(sAMAccountName={username}))", "displayName", "mail"),
    "openldap": ("OpenLDAP / 389 DS / FreeIPA", "(&(objectClass=person)(uid={username}))", "cn", "mail"),
}
SECRET_FIELDS = ("bind_password", "secret")


def _cipher():
    # Clé stable du serveur, déjà incluse dans les sauvegardes ; dérivée comme pour l'ancien SSO (secrets relisibles).
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-sso-v1", hashlib.sha256).digest()))


def _seal(value):
    return _cipher().encrypt(value.encode()).decode()


def _open(value):
    return _cipher().decrypt(value.encode()).decode() if value else ""


def config_of(row):
    """Configuration utilisable (secrets déchiffrés) d'un annuaire."""
    cfg = json.loads(row["config"])
    for field in SECRET_FIELDS:
        if cfg.get(field):
            cfg[field] = _open(cfg[field])
    return cfg


def _directory(directory_id):
    row = db().execute("SELECT * FROM auth_directories WHERE id=?", (directory_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def authenticate(row, username, password):
    """(accepté, {display_name, email}) ; DirectoryError si l'annuaire est injoignable ou mal configuré."""
    cfg = config_of(row)
    if row["kind"] == "ldap":
        return ldap_authenticate(cfg, username, password)
    return radius_authenticate(cfg, username, password)


def enabled_directories(conn=None):
    from . import features
    if "directories" in features.disabled(conn):
        return []
    return (conn or db()).execute("SELECT * FROM auth_directories WHERE enabled=1 ORDER BY position, label").fetchall()


def _free_username(conn, wanted, label):
    base = wanted.lower()
    for candidate in [base] + [f"{base}@{re.sub('[^a-z0-9]+', '-', label.lower()).strip('-') or 'annuaire'}"] + \
            [f"{base}-{n}" for n in range(2, 50)]:
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (candidate[:64],)).fetchone():
            return candidate[:64]
    return f"{base[:40]}-{secrets.token_hex(4)}"


def directory_login(username, password):
    """Connexion par annuaire depuis le formulaire : (compte ou None, message pour l'utilisateur ou None).

    Compte lié à un annuaire : vérifié par cet annuaire. Identifiant inconnu : essayé dans l'ordre des annuaires actifs ;
    une identité nouvelle devient une demande d'accès sans aucun droit. Jamais de rapprochement avec un compte local.
    """
    conn = db()
    if not USERNAME_RE.match(username) or not password:
        return None, None
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is not None and row["auth_source"] != "directory":
        return None, None                                       # compte local ou ancien SSO : pas d'annuaire
    active = {d["id"]: d for d in enabled_directories(conn)}
    if row is not None:
        link = conn.execute("SELECT * FROM directory_identities WHERE user_id=?", (row["id"],)).fetchone()
        if link is None or link["directory_id"] not in active:
            return None, None
        candidates = [(active[link["directory_id"]], link["subject"])]
    else:
        candidates = [(d, username) for d in active.values()]
    for directory, subject in candidates:
        try:
            accepted, info = authenticate(directory, subject, password)
        except (DirectoryError, ValueError, OSError) as e:
            audit("sso.echec", f"{directory['label']} injoignable ou mal configuré ({type(e).__name__})", username="")
            continue
        if not accepted:
            continue
        link = conn.execute("SELECT * FROM directory_identities WHERE directory_id=? AND subject=?",
                            (directory["id"], subject.lower())).fetchone()
        if link is None:
            name = _free_username(conn, subject, directory["label"])
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            uid = conn.execute("INSERT INTO users (username,password_hash,role,disabled,pending,auth_source,created_at) "
                               "VALUES (?,?,'pending',1,1,'directory',?)",
                               (name, generate_password_hash(secrets.token_urlsafe(48)), now)).lastrowid
            conn.execute("INSERT INTO directory_identities (directory_id, subject, user_id, display_name, email) VALUES (?,?,?,?,?)",
                         (directory["id"], subject.lower(), uid, info.get("display_name", ""), info.get("email", "")))
            audit("sso.demande", f"{name} via {directory['label']}", username=name, commit=False)
            conn.commit()
            return None, "Votre demande d'accès a été créée. Un administrateur doit l'approuver avant votre première connexion."
        conn.execute("UPDATE directory_identities SET display_name=?, email=? WHERE directory_id=? AND subject=?",
                     (info.get("display_name", "") or link["display_name"], info.get("email", "") or link["email"],
                      directory["id"], subject.lower()))
        user = conn.execute("SELECT * FROM users WHERE id=?", (link["user_id"],)).fetchone()
        if user["pending"]:
            return None, "Votre demande d'accès attend encore l'approbation d'un administrateur."
        if user["disabled"]:
            return None, None
        return user, directory["id"]
    return None, None


@app.context_processor
def _login_directories():
    if request.endpoint == "login":
        return {"login_directories": [d["label"] for d in enabled_directories()]}
    return {}


# --------------------------------------------------------------------------- administration

def _values(kind, existing=None):
    old = json.loads(existing["config"]) if existing else {}
    label = request.form.get("label", "").strip()
    if not label or len(label) > 80 or any(ord(c) < 32 for c in label):
        raise ValueError("Nom de 1 à 80 caractères requis.")
    if kind == "ldap":
        url = request.form.get("url", "").strip()
        parts = urllib.parse.urlsplit(url)
        starttls = request.form.get("starttls") == "1"
        if parts.scheme not in ("ldap", "ldaps") or not parts.hostname or parts.path not in ("", "/") or parts.query:
            raise ValueError("Adresse ldaps://hôte:636 ou ldap://hôte:389 attendue.")
        if parts.scheme == "ldap" and not starttls:
            raise ValueError("Sans ldaps://, activez StartTLS : le mot de passe ne doit jamais circuler en clair.")
        cfg = {"url": url, "starttls": starttls and parts.scheme == "ldap", "base_dn": request.form.get("base_dn", "").strip()[:500],
               "user_filter": request.form.get("user_filter", "").strip()[:500] or "(uid={username})",
               "bind_dn": request.form.get("bind_dn", "").strip()[:500],
               "user_template": request.form.get("user_template", "").strip()[:500] or "{username}",
               "name_attr": request.form.get("name_attr", "").strip()[:60] or "displayName",
               "mail_attr": request.form.get("mail_attr", "").strip()[:60] or "mail",
               "ca_pem": request.form.get("ca_pem", "").strip()[:20000]}
        from .directory_protocols import encode_filter
        try:
            encode_filter(cfg["user_filter"].replace("{username}", "x"))
        except ValueError as e:
            raise ValueError(f"Filtre de recherche : {e}.")
        if "{username}" not in cfg["user_filter"]:
            raise ValueError("Le filtre doit contenir {username}.")
        if cfg["bind_dn"] and not cfg["base_dn"]:
            raise ValueError("Avec un compte de service, la base de recherche est requise.")
        if not cfg["bind_dn"] and "{username}" not in cfg["user_template"]:
            raise ValueError("Sans compte de service, le modèle de liaison doit contenir {username}.")
        if cfg["ca_pem"] and "BEGIN CERTIFICATE" not in cfg["ca_pem"]:
            raise ValueError("Autorité : certificat PEM attendu.")
        password = request.form.get("bind_password", "")
        cfg["bind_password"] = _seal(password) if password else old.get("bind_password", "")
        if cfg["bind_dn"] and not cfg["bind_password"]:
            raise ValueError("Mot de passe du compte de service requis.")
    else:
        host = request.form.get("host", "").strip()
        try:
            port = int(request.form.get("port") or 1812)
            timeout = int(request.form.get("timeout") or 5)
            retries = int(request.form.get("retries") or 1)
        except ValueError:
            raise ValueError("Port, délai et nouvelles tentatives : nombres attendus.")
        if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host) or not 1 <= port <= 65535 or not 1 <= timeout <= 30 or not 0 <= retries <= 5:
            raise ValueError("Serveur, port (1-65535), délai (1-30 s) ou nouvelles tentatives (0-5) invalides.")
        secret = request.form.get("secret", "")
        cfg = {"host": host, "port": port, "timeout": timeout, "retries": retries,
               "nas_identifier": request.form.get("nas_identifier", "").strip()[:64] or "loco",
               "require_message_authenticator": request.form.get("require_message_authenticator") == "1",
               "secret": _seal(secret) if secret else old.get("secret", "")}
        if not cfg["secret"]:
            raise ValueError("Secret partagé requis.")
    return label, cfg


@app.route("/comptes/annuaires")
@login_required
def sso_settings():
    rows = db().execute("SELECT d.*, (SELECT COUNT(*) FROM directory_identities i WHERE i.directory_id=d.id) AS accounts "
                        "FROM auth_directories d ORDER BY position, label").fetchall()
    directories = []
    for r in rows:
        cfg = json.loads(r["config"])
        for field in SECRET_FIELDS:
            cfg[field] = bool(cfg.get(field))                        # jamais renvoyé au navigateur
        directories.append({"row": r, "cfg": cfg})
    legacy = db().execute("SELECT COUNT(*) FROM users WHERE auth_source='sso'").fetchone()[0]
    return render_template("directories.html", directories=directories, kinds=KINDS, presets=LDAP_PRESETS, legacy=legacy)


@app.route("/comptes/annuaires/ajouter", methods=["POST"])
@login_required
def directory_add():
    conn = lock_access("sso.manage")
    kind = request.form.get("kind", "")
    if kind not in KINDS:
        abort(400)
    try:
        label, cfg = _values(kind)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("sso_settings"))
    position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM auth_directories").fetchone()[0]
    conn.execute("INSERT INTO auth_directories (id, kind, label, config, enabled, position, created_at) VALUES (?,?,?,?,?,?,?)",
                 (secrets.token_hex(8), kind, label, json.dumps(cfg), int(request.form.get("enabled") == "1"), position,
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    audit("sso.fournisseur.ajout", f"{label} ({KINDS[kind]})", commit=False)
    conn.commit()
    flash("Annuaire ajouté. Testez-le avec un compte de l'annuaire avant de l'annoncer aux utilisateurs.", "ok")
    return redirect(url_for("sso_settings"))


def _revoke(directory_id):
    for row in db().execute("SELECT user_id FROM directory_identities WHERE directory_id=?", (directory_id,)).fetchall():
        permissions.revoke(db(), row[0])


@app.route("/comptes/annuaires/<directory_id>/modifier", methods=["POST"])
@login_required
def directory_update(directory_id):
    conn = lock_access("sso.manage")
    row = _directory(directory_id)
    try:
        label, cfg = _values(row["kind"], row)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("sso_settings"))
    _revoke(directory_id)
    conn.execute("UPDATE auth_directories SET label=?, config=?, enabled=?, revision=revision+1 WHERE id=?",
                 (label, json.dumps(cfg), int(request.form.get("enabled") == "1"), directory_id))
    audit("sso.fournisseur.modification", label, commit=False)
    conn.commit()
    flash("Annuaire enregistré ; les sessions de ses comptes ont été fermées.", "ok")
    return redirect(url_for("sso_settings"))


@app.route("/comptes/annuaires/<directory_id>/supprimer", methods=["POST"])
@login_required
def directory_delete(directory_id):
    conn = lock_access("sso.manage")
    row = _directory(directory_id)
    _revoke(directory_id)
    # Comptes conservés (journal, profils) mais désactivés : aucune réassociation automatique à un autre annuaire.
    conn.execute("UPDATE users SET disabled=1 WHERE id IN (SELECT user_id FROM directory_identities WHERE directory_id=?)", (directory_id,))
    conn.execute("DELETE FROM auth_directories WHERE id=?", (directory_id,))
    audit("sso.fournisseur.suppression", row["label"], commit=False)
    conn.commit()
    flash("Annuaire supprimé ; ses comptes sont désactivés.", "ok")
    return redirect(url_for("sso_settings"))


@app.route("/comptes/annuaires/<directory_id>/monter", methods=["POST"])
@login_required
def directory_move(directory_id):
    conn = lock_access("sso.manage")
    rows = conn.execute("SELECT id FROM auth_directories ORDER BY position, label").fetchall()
    order = [r[0] for r in rows]
    if directory_id not in order:
        abort(404)
    i = order.index(directory_id)
    if i:
        order[i - 1], order[i] = order[i], order[i - 1]
    for position, ident in enumerate(order):
        conn.execute("UPDATE auth_directories SET position=? WHERE id=?", (position, ident))
    conn.commit()
    return redirect(url_for("sso_settings"))


@app.route("/comptes/annuaires/<directory_id>/tester", methods=["POST"])
@login_required
def directory_test(directory_id):
    require("sso.manage")                   # pas de verrou de la base pendant l'échange réseau avec l'annuaire
    row = _directory(directory_id)
    username, password = request.form.get("username", "").strip().lower(), request.form.get("password", "")
    try:
        accepted, info = authenticate(row, username, password)
    except (DirectoryError, ValueError, OSError) as e:
        result = {"ok": False, "message": f"Annuaire injoignable ou mal configuré : {e}"}
    else:
        result = ({"ok": True, "message": "Identifiants acceptés" + (f" : {info['display_name']}" if info.get("display_name") else "")
                   + (f" <{info['email']}>" if info.get("email") else "") + "."} if accepted
                  else {"ok": False, "message": "Annuaire joint, mais identifiants refusés (ou compte introuvable)."})
    audit("sso.test", f"{row['label']} : {username or '—'} ({'accepté' if result['ok'] else 'refusé'})", commit=False)
    db().commit()
    flash(result["message"], "ok" if result["ok"] else "error")
    return redirect(url_for("sso_settings"))


@app.route("/comptes/<int:user_id>/approuver", methods=["POST"])
@login_required
def users_approve(user_id):
    conn = lock_access("users.approve")
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    role = request.form.get("role", "")
    if not row["pending"] or row["auth_source"] != "directory" or role == "pending" or role not in permissions.role_labels(conn):
        abort(400)
    if not can_assign(role):
        abort(403)
    if not conn.execute("SELECT 1 FROM directory_identities i JOIN auth_directories d ON d.id=i.directory_id "
                        "WHERE i.user_id=? AND d.enabled=1", (user_id,)).fetchone():
        abort(400, "L'annuaire doit être actif avant l'approbation.")
    conn.execute("UPDATE users SET role=?,disabled=0,pending=0 WHERE id=?", (role, user_id))
    permissions.revoke(conn, user_id)
    audit("sso.approbation", f"{row['username']} : {permissions.role_labels(conn)[role]}", commit=False)
    conn.commit()
    flash("Compte approuvé. L'utilisateur peut maintenant se connecter avec son identifiant d'annuaire.", "ok")
    return redirect(url_for("users"))

