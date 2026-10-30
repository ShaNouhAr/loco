"""Annuaires LDAP et RADIUS (formulaire de loco) et fournisseurs OpenID Connect comme Authentik (bouton de connexion) :
nouvelles identités en attente d'approbation."""
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
from .i18n import L_, _
from .auth import audit, can_assign, lock_access, login_required, require
from .db import db
from .directory_protocols import DirectoryError, USERNAME_RE, ldap_authenticate, radius_authenticate

KINDS = {"ldap": L_("LDAP / Active Directory"), "radius": "RADIUS", "oidc": L_("OpenID Connect")}
# OpenID Connect : Authentik (émetteur par application) ou tout autre fournisseur (Keycloak, Zitadel, Authelia…).
OIDC_PRESETS = {"authentik": "Authentik", "oidc": L_("OpenID Connect")}
# Modèles LDAP : filtre de recherche et attributs habituels.
LDAP_PRESETS = {
    "ad": ("Active Directory", "(&(objectClass=user)(sAMAccountName={username}))", "displayName", "mail"),
    "openldap": ("OpenLDAP / 389 DS / FreeIPA", "(&(objectClass=person)(uid={username}))", "cn", "mail"),
}
SECRET_FIELDS = ("bind_password", "secret", "client_secret")


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
    if row["kind"] == "oidc":
        return False, {}                                        # connexion par le bouton du fournisseur, jamais par mot de passe
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
    enabled = enabled_directories(conn)
    active = {d["id"]: d for d in enabled if d["kind"] != "oidc"}
    if row is not None:
        link = conn.execute("SELECT * FROM directory_identities WHERE user_id=?", (row["id"],)).fetchone()
        sso = next((d for d in enabled if link is not None and d["id"] == link["directory_id"] and d["kind"] == "oidc"), None)
        if sso is not None:
            return None, _("Ce compte se connecte avec le bouton « Se connecter avec %(label)s ».", label=sso["label"])
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
        enabled = enabled_directories()
        return {"login_directories": [d["label"] for d in enabled if d["kind"] != "oidc"],
                "login_sso": [{"id": d["id"], "label": d["label"]} for d in enabled if d["kind"] == "oidc"]}
    return {}


# --------------------------------------------------------------------------- administration

DOMAIN_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{1,253}")


def _server_url(server, starttls):
    """« hôte » ou « hôte:port » -> URL LDAPS (636) ou LDAP + StartTLS (389)."""
    host, port = server, ""
    if server.count(":") == 1:                         # « dc1:3269 » (catalogue global en SSL, par exemple)
        host, port = server.split(":")
    if not HOST_RE.fullmatch(host) or (port and not (port.isdigit() and 1 <= int(port) <= 65535)):
        raise ValueError(_("Serveur : nom ou adresse attendu, avec un port facultatif."))
    host = f"[{host}]" if ":" in host else host        # adresse IPv6
    return f"{'ldap' if starttls else 'ldaps'}://{host}:{port or ('389' if starttls else '636')}"


def _ldap_form():
    """Champs LDAP saisis. Active Directory et FreeIPA : tout se déduit du domaine (et du serveur) ; les autres
    annuaires (OpenLDAP, 389 DS…) se décrivent champ par champ."""
    f = request.form
    starttls = f.get("starttls") == "1"
    preset = f.get("preset", "")
    if preset in ("ad", "freeipa"):
        domain = f.get("ad_domain", "").strip().lower().rstrip(".")
        if not DOMAIN_RE.fullmatch(domain):
            raise ValueError(_("Domaine attendu, par exemple entreprise.local."))
        server = f.get("ad_server", "").strip().lower()
        if preset == "freeipa" and not server:
            raise ValueError(_("Nom du serveur FreeIPA attendu, par exemple ipa.entreprise.local."))
        server = server or domain
        dc = ",".join(("DC=" if preset == "ad" else "dc=") + part for part in domain.split("."))
        common = {"preset": preset, "domain": domain, "server": server, "url": _server_url(server, starttls),
                  "starttls": starttls, "bind_dn": f.get("bind_dn", "").strip()[:500], "mail_attr": "mail",
                  "name_attr": "displayName", "ca_pem": f.get("ca_pem", "").strip()[:20000]}
        if preset == "ad":                             # liaison identifiant@domaine, recherche sAMAccountName
            return {**common, "base_dn": dc, "user_filter": "(&(objectClass=user)(sAMAccountName={username}))",
                    "user_template": "{username}@" + domain}
        users = "cn=users,cn=accounts," + dc           # FreeIPA : comptes sous cn=users,cn=accounts
        return {**common, "base_dn": users, "user_filter": "(&(objectClass=person)(uid={username}))",
                "user_template": "uid={username}," + users}
    return {"url": f.get("url", "").strip(), "starttls": starttls, "base_dn": f.get("base_dn", "").strip()[:500],
            "user_filter": f.get("user_filter", "").strip()[:500] or "(uid={username})",
            "bind_dn": f.get("bind_dn", "").strip()[:500],
            "user_template": f.get("user_template", "").strip()[:500] or "{username}",
            "name_attr": f.get("name_attr", "").strip()[:60] or "displayName",
            "mail_attr": f.get("mail_attr", "").strip()[:60] or "mail", "ca_pem": f.get("ca_pem", "").strip()[:20000]}


OIDC_SUFFIX = "/.well-known/openid-configuration"


def _oidc_form(old):
    """Émetteur, client et options d'un fournisseur OpenID Connect. Authentik : l'émetteur d'une application est
    https://authentik…/application/o/<slug>/ (affiché dans Authentik sur la page du fournisseur)."""
    from .oidc import OIDCError, https_url
    f = request.form
    preset = f.get("preset", "oidc")
    if preset not in OIDC_PRESETS:
        raise ValueError(_("Fournisseur inconnu."))
    issuer = f.get("issuer", "").strip()
    if issuer.endswith(OIDC_SUFFIX):                            # URL de configuration collée au lieu de l'émetteur
        issuer = issuer[:-len(OIDC_SUFFIX)]
    try:
        https_url(issuer)
    except OIDCError:
        raise ValueError(_("Émetteur : adresse https://… attendue."))
    parts = urllib.parse.urlsplit(issuer)
    if parts.query or len(issuer) > 500:
        raise ValueError(_("Émetteur : adresse https://… attendue."))
    if preset == "authentik" and not re.fullmatch(r"/application/o/[A-Za-z0-9_-]+/?", parts.path):
        raise ValueError(_("Pour Authentik, indiquez l’émetteur de l’application, par exemple "
                           "https://authentik.entreprise.fr/application/o/loco/ (page du fournisseur dans Authentik)."))
    if old and old.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise ValueError(_("L’émetteur ne se change pas : les comptes liés appartiennent à ce fournisseur. "
                           "Ajoutez un nouveau fournisseur à la place."))
    client_id = f.get("client_id", "").strip()
    if not client_id or len(client_id) > 255 or any(ord(c) < 33 for c in client_id):
        raise ValueError(_("Identifiant client (Client ID) requis."))
    scopes = " ".join(f.get("scopes", "").split()) or "openid profile email"
    if "openid" not in scopes.split() or not re.fullmatch(r"[A-Za-z0-9_:./ -]{1,200}", scopes):
        raise ValueError(_("Portées : liste séparée par des espaces, avec openid."))
    claim = f.get("username_claim", "").strip() or "preferred_username"
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,60}", claim):
        raise ValueError(_("Revendication du nom d’utilisateur invalide."))
    ca_pem = f.get("ca_pem", "").strip()[:20000]
    if ca_pem and "BEGIN CERTIFICATE" not in ca_pem:
        raise ValueError(_('Autorité : certificat PEM attendu.'))
    secret = f.get("client_secret", "")
    if len(secret) > 1024:
        raise ValueError(_("Secret client trop long."))
    cfg = {"preset": preset, "issuer": issuer, "client_id": client_id, "scopes": scopes, "username_claim": claim,
           "ca_pem": ca_pem, "client_secret": _seal(secret) if secret else (old or {}).get("client_secret", "")}
    if not cfg["client_secret"]:
        raise ValueError(_("Secret client (Client Secret) requis."))
    return cfg


def _values(kind, existing=None):
    old = json.loads(existing["config"]) if existing else {}
    label = request.form.get("label", "").strip()
    if not label and kind == "ldap" and request.form.get("preset") in ("ad", "freeipa"):
        label = "Active Directory" if request.form.get("preset") == "ad" else "FreeIPA"   # nom par défaut, modifiable
    if not label and kind == "oidc" and request.form.get("preset") == "authentik":
        label = "Authentik"
    if not label or len(label) > 80 or any(ord(c) < 32 for c in label):
        raise ValueError(_('Nom de 1 à 80 caractères requis.'))
    if kind == "oidc":
        return label, _oidc_form(old)
    if kind == "ldap":
        cfg = _ldap_form()
        url = cfg["url"]
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("ldap", "ldaps") or not parts.hostname or parts.path not in ("", "/") or parts.query:
            raise ValueError(_('Adresse ldaps://hôte:636 ou ldap://hôte:389 attendue.'))
        if parts.scheme == "ldap" and not cfg["starttls"]:
            raise ValueError(_('Sans ldaps://, activez StartTLS : le mot de passe ne doit jamais circuler en clair.'))
        cfg["starttls"] = cfg["starttls"] and parts.scheme == "ldap"
        from .directory_protocols import encode_filter
        try:
            encode_filter(cfg["user_filter"].replace("{username}", "x"))
        except ValueError as e:
            raise ValueError(_('Filtre de recherche : %(e)s.', e=e))
        if "{username}" not in cfg["user_filter"]:
            raise ValueError(_('Le filtre doit contenir {username}.'))
        if cfg["bind_dn"] and not cfg["base_dn"]:
            raise ValueError(_('Avec un compte de service, la base de recherche est requise.'))
        if not cfg["bind_dn"] and "{username}" not in cfg["user_template"]:
            raise ValueError(_('Sans compte de service, le modèle de liaison doit contenir {username}.'))
        if cfg["ca_pem"] and "BEGIN CERTIFICATE" not in cfg["ca_pem"]:
            raise ValueError(_('Autorité : certificat PEM attendu.'))
        password = request.form.get("bind_password", "")
        cfg["bind_password"] = _seal(password) if password else old.get("bind_password", "")
        if cfg["bind_dn"] and not cfg["bind_password"]:
            raise ValueError(_('Mot de passe du compte de service requis.'))
    else:
        host = request.form.get("host", "").strip()
        try:
            port = int(request.form.get("port") or 1812)
            timeout = int(request.form.get("timeout") or 5)
            retries = int(request.form.get("retries") or 1)
        except ValueError:
            raise ValueError(_('Port, délai et nouvelles tentatives : nombres attendus.'))
        if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host) or not 1 <= port <= 65535 or not 1 <= timeout <= 30 or not 0 <= retries <= 5:
            raise ValueError(_('Serveur, port (1-65535), délai (1-30 s) ou nouvelles tentatives (0-5) invalides.'))
        secret = request.form.get("secret", "")
        cfg = {"host": host, "port": port, "timeout": timeout, "retries": retries,
               "nas_identifier": request.form.get("nas_identifier", "").strip()[:64] or "loco",
               "require_message_authenticator": request.form.get("require_message_authenticator") == "1",
               "secret": _seal(secret) if secret else old.get("secret", "")}
        if not cfg["secret"]:
            raise ValueError(_('Secret partagé requis.'))
    return label, cfg


@app.route("/accounts/directories")
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
    summary = {"active": sum(1 for d in directories if d["row"]["enabled"]),
               "accounts": sum(d["row"]["accounts"] for d in directories),
               "pending": db().execute("SELECT COUNT(*) FROM users WHERE pending=1").fetchone()[0]}
    from .oidc import callback_url
    new_id = secrets.token_hex(8)                               # connu d'avance : URL de redirection à déclarer
    return render_template("directories.html", directories=directories, kinds=KINDS, presets=LDAP_PRESETS, legacy=legacy,
                           summary=summary, new_id=new_id, callback_url=callback_url)


@app.route("/accounts/directories/add", methods=["POST"])
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
    ident = request.form.get("new_id", "")
    if not re.fullmatch(r"[0-9a-f]{16}", ident) or conn.execute("SELECT 1 FROM auth_directories WHERE id=?", (ident,)).fetchone():
        ident = secrets.token_hex(8)
    conn.execute("INSERT INTO auth_directories (id, kind, label, config, enabled, position, created_at) VALUES (?,?,?,?,?,?,?)",
                 (ident, kind, label, json.dumps(cfg), int(request.form.get("enabled") == "1"), position,
                  datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    audit("sso.fournisseur.ajout", f"{label} ({KINDS[kind]})", commit=False)
    conn.commit()
    flash(_("Fournisseur ajouté. Testez une connexion avant de l’annoncer aux utilisateurs.") if kind == "oidc"
          else _("Annuaire ajouté. Testez-le avec un compte de l'annuaire avant de l'annoncer aux utilisateurs."), "ok")
    return redirect(url_for("sso_settings"))


def _revoke(directory_id):
    for row in db().execute("SELECT user_id FROM directory_identities WHERE directory_id=?", (directory_id,)).fetchall():
        permissions.revoke(db(), row[0])


@app.route("/accounts/directories/<directory_id>/edit", methods=["POST"])
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
    flash(_('Annuaire enregistré ; les sessions de ses comptes ont été fermées.'), "ok")
    return redirect(url_for("sso_settings"))


@app.route("/accounts/directories/<directory_id>/delete", methods=["POST"])
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
    flash(_('Annuaire supprimé ; ses comptes sont désactivés.'), "ok")
    return redirect(url_for("sso_settings"))


@app.route("/accounts/directories/<directory_id>/toggle", methods=["POST"])
@login_required
def directory_toggle(directory_id):
    """Active ou désactive un annuaire d'un clic ; désactivé, ses comptes perdent leurs sessions."""
    conn = lock_access("sso.manage")
    row = _directory(directory_id)
    enabled = 0 if row["enabled"] else 1
    if not enabled:
        _revoke(directory_id)
    conn.execute("UPDATE auth_directories SET enabled=?, revision=revision+1 WHERE id=?", (enabled, directory_id))
    audit("sso.fournisseur.modification", f"{row['label']} : {'activé' if enabled else 'désactivé'}", commit=False)
    conn.commit()
    flash(_("%(label)s activé : ses utilisateurs peuvent se connecter.", label=row["label"]) if enabled
          else _("%(label)s désactivé ; les sessions de ses comptes ont été fermées.", label=row["label"]), "ok")
    return redirect(url_for("sso_settings"))


def _move(directory_id, step):
    """Ordre d'essai des annuaires : un rang plus tôt (-1) ou plus tard (+1)."""
    conn = lock_access("sso.manage")
    rows = conn.execute("SELECT id FROM auth_directories ORDER BY position, label").fetchall()
    order = [r[0] for r in rows]
    if directory_id not in order:
        abort(404)
    i = order.index(directory_id)
    j = i + step
    if 0 <= j < len(order):
        order[j], order[i] = order[i], order[j]
    for position, ident in enumerate(order):
        conn.execute("UPDATE auth_directories SET position=? WHERE id=?", (position, ident))
    conn.commit()
    return redirect(url_for("sso_settings"))


@app.route("/accounts/directories/<directory_id>/move-up", methods=["POST"])
@login_required
def directory_move(directory_id):
    return _move(directory_id, -1)


@app.route("/accounts/directories/<directory_id>/move-down", methods=["POST"])
@login_required
def directory_move_down(directory_id):
    return _move(directory_id, 1)


@app.route("/accounts/directories/<directory_id>/test", methods=["POST"])
@login_required
def directory_test(directory_id):
    require("sso.manage")                   # pas de verrou de la base pendant l'échange réseau avec l'annuaire
    row = _directory(directory_id)
    if row["kind"] == "oidc":
        # Vraie connexion chez le fournisseur ; au retour, l'identité reçue est affichée, aucun compte n'est créé.
        from .oidc import OIDCError, start_test
        try:
            return start_test(row)
        except OIDCError as e:
            audit("sso.test", f"{row['label']} : {e}", commit=False)
            db().commit()
            flash(_("Fournisseur injoignable ou mal configuré : %(e)s", e=e), "error")
            return redirect(url_for("sso_settings"))
    username, password = request.form.get("username", "").strip().lower(), request.form.get("password", "")
    try:
        accepted, info = authenticate(row, username, password)
    except (DirectoryError, ValueError, OSError) as e:
        result = {"ok": False, "message": _("Annuaire injoignable ou mal configuré : %(e)s", e=e)}
    else:
        who = (info.get("display_name") or "") + (f" <{info['email']}>" if info.get("email") else "")
        result = ({"ok": True, "message": _("Identifiants acceptés : %(who)s.", who=who.strip()) if who.strip()
                   else _("Identifiants acceptés.")} if accepted
                  else {"ok": False, "message": _("Annuaire joint, mais identifiants refusés (ou compte introuvable).")})
    audit("sso.test", f"{row['label']} : {username or '—'} ({'accepté' if result['ok'] else 'refusé'})", commit=False)
    db().commit()
    flash(result["message"], "ok" if result["ok"] else "error")
    return redirect(url_for("sso_settings"))


@app.route("/accounts/<int:user_id>/approve", methods=["POST"])
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
    directory = conn.execute("SELECT d.kind, d.label FROM directory_identities i JOIN auth_directories d "
                             "ON d.id=i.directory_id WHERE i.user_id=? AND d.enabled=1", (user_id,)).fetchone()
    if directory is None:
        abort(400, _("L'annuaire doit être actif avant l'approbation."))
    conn.execute("UPDATE users SET role=?,disabled=0,pending=0 WHERE id=?", (role, user_id))
    permissions.revoke(conn, user_id)
    audit("sso.approbation", f"{row['username']} : {permissions.role_labels(conn)[role]}", commit=False)
    conn.commit()
    if directory["kind"] == "oidc":
        flash(_("Compte approuvé. L’utilisateur peut maintenant se connecter avec le bouton « Se connecter avec %(label)s ».",
                label=directory["label"]), "ok")
    else:
        flash(_("Compte approuvé. L'utilisateur peut maintenant se connecter avec son identifiant d'annuaire."), "ok")
    return redirect(url_for("users"))

