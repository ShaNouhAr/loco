"""Connexions OIDC/OAuth : identités isolées, approbation obligatoire et aucun jeton conservé."""
import base64
import datetime
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from . import app, config, permissions
from .auth import audit, can_assign, lock_access, login_required
from .db import db

# Une instance peut avoir plusieurs fournisseurs du même type. L'émetteur exact reste configurable.
PRESETS = {
    "entra": ("Microsoft Entra ID / Microsoft 365", "https://login.microsoftonline.com/TENANT-UUID/v2.0"),
    "google": ("Google / Google Workspace", "https://accounts.google.com"),
    "github": ("GitHub", "https://github.com"),
    "gitlab": ("GitLab", "https://gitlab.com"),
    "keycloak": ("Keycloak", "https://sso.example.com/realms/REALM"),
    "authentik": ("Authentik", "https://sso.example.com/application/o/APPLICATION/"),
    "okta": ("Okta", "https://TENANT.okta.com/oauth2/default"),
    "auth0": ("Auth0", "https://TENANT.eu.auth0.com/"),
    "authelia": ("Authelia", "https://sso.example.com"),
    "zitadel": ("ZITADEL", "https://TENANT.zitadel.cloud"),
    "dex": ("Dex", "https://sso.example.com/dex"),
    "cognito": ("Amazon Cognito", "https://cognito-idp.REGION.amazonaws.com/USER_POOL_ID"),
    "adfs": ("AD FS", "https://adfs.example.com/adfs"),
    "oidc": ("OpenID Connect générique", "https://sso.example.com"),
}
COOKIE = "loco_sso_flow"
LIMIT = 1024 * 1024
ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


def _cipher():
    # Clé stable du serveur déjà incluse dans les sauvegardes ; jamais un secret dans le cookie Flask.
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-sso-v1", hashlib.sha256).digest()))


def _seal(value):
    return _cipher().encrypt(value.encode()).decode()


def _open(value):
    return _cipher().decrypt(value.encode()).decode()


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _https(value):
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment
            or any(ord(c) <= 32 for c in value)):
        raise ValueError("Une URL HTTPS sans identifiant ni fragment est requise.")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Redirection inattendue du fournisseur SSO.")


def _json(url, data=None, headers=None):
    req = urllib.request.Request(_https(url), data=data, headers={"Accept": "application/json",
                                  "User-Agent": config.USER_AGENT, **(headers or {})})
    with urllib.request.build_opener(_NoRedirect()).open(req, timeout=10) as response:
        payload = response.read(LIMIT + 1)
    if len(payload) > LIMIT:
        raise ValueError("Réponse SSO trop volumineuse.")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("Réponse SSO invalide.")
    return result


def _metadata(provider):
    if provider["preset"] == "github":
        return {"issuer": "https://github.com", "authorization_endpoint": "https://github.com/login/oauth/authorize",
                "token_endpoint": "https://github.com/login/oauth/access_token"}
    doc = _json(provider["issuer"].rstrip("/") + "/.well-known/openid-configuration")
    if doc.get("issuer") != provider["issuer"]:
        raise ValueError("L'émetteur du document de découverte ne correspond pas à la configuration.")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _https(doc[name])
    return doc


def _provider(pid, enabled=False):
    row = db().execute("SELECT * FROM sso_providers WHERE id=?", (pid,)).fetchone()
    if row is None or (enabled and not row["enabled"]):
        abort(404)
    return row


def _callback(pid):
    # Ne dépend pas d'un en-tête Host fourni par le navigateur.
    return config.HTTPS_URL.rstrip("/") + url_for("sso_callback", provider_id=pid)


@app.context_processor
def _login_providers():
    if request.endpoint == "login":
        return {"sso_providers": db().execute("SELECT id,label FROM sso_providers WHERE enabled=1 ORDER BY label").fetchall()}
    return {}


@app.route("/comptes/sso")
@login_required
def sso_settings():
    providers = db().execute("SELECT id,label,preset,issuer,client_id,auth_method,enabled,"
                             "(SELECT COUNT(*) FROM sso_identities i WHERE i.provider_id=p.id) AS accounts "
                             "FROM sso_providers p ORDER BY label").fetchall()
    return render_template("sso.html", providers=providers, presets=PRESETS, callback=_callback)


def _values(existing=None):
    preset = request.form.get("preset", existing["preset"] if existing else "oidc")
    label = request.form.get("label", "").strip()
    issuer = request.form.get("issuer", "").strip()
    client_id = request.form.get("client_id", "").strip()
    secret = request.form.get("client_secret", "")
    method = request.form.get("auth_method", "client_secret_basic")
    if preset not in PRESETS or not label or len(label) > 80 or not client_id or len(client_id) > 512:
        raise ValueError("Nom, fournisseur et identifiant client valides requis.")
    _https(issuer)
    if urllib.parse.urlsplit(issuer).query or len(issuer) > 2048 or len(secret) > 4096:
        raise ValueError("Émetteur ou secret invalide.")
    if preset == "github" and issuer != "https://github.com":
        raise ValueError("Le modèle GitHub utilise https://github.com.")
    if method not in ("client_secret_basic", "client_secret_post"):
        raise ValueError("Méthode d'authentification inconnue.")
    if existing and (preset != existing["preset"] or issuer != existing["issuer"] or client_id != existing["client_id"]):
        raise ValueError("Pour changer l'identité d'un fournisseur, créez une nouvelle connexion SSO.")
    if not secret and not existing:
        raise ValueError("Le secret client est requis.")
    return (label, preset, issuer, client_id, _seal(secret) if secret else existing["client_secret"],
            method, int(request.form.get("enabled") == "1"))


@app.route("/comptes/sso/ajouter", methods=["POST"])
@login_required
def sso_add():
    conn = lock_access("sso.manage")
    try:
        values = _values()
    except ValueError as e:
        flash(str(e), "error")
    else:
        pid = secrets.token_hex(8)
        conn.execute("INSERT INTO sso_providers (id,label,preset,issuer,client_id,client_secret,auth_method,enabled) "
                     "VALUES (?,?,?,?,?,?,?,?)", (pid, *values))
        audit("sso.fournisseur.ajout", f"{values[0]} ({values[1]})", commit=False)
        flash("Fournisseur ajouté. Enregistrez son URL de retour chez le fournisseur.", "ok")
    conn.commit()
    return redirect(url_for("sso_settings"))


def _revoke_provider(pid):
    for row in db().execute("SELECT user_id FROM sso_identities WHERE provider_id=?", (pid,)).fetchall():
        permissions.revoke(db(), row[0])
    db().execute("DELETE FROM sso_flows WHERE provider_id=?", (pid,))


@app.route("/comptes/sso/<provider_id>/modifier", methods=["POST"])
@login_required
def sso_update(provider_id):
    conn = lock_access("sso.manage")
    row = _provider(provider_id)
    try:
        values = _values(row)
    except ValueError as e:
        flash(str(e), "error")
    else:
        _revoke_provider(provider_id)
        conn.execute("UPDATE sso_providers SET label=?,preset=?,issuer=?,client_id=?,client_secret=?,auth_method=?,enabled=?,"
                     "revision=revision+1 WHERE id=?", (*values, provider_id))
        audit("sso.fournisseur.modification", row["label"], commit=False)
        flash("Fournisseur enregistré ; ses sessions ont été fermées.", "ok")
    conn.commit()
    return redirect(url_for("sso_settings"))


@app.route("/comptes/sso/<provider_id>/supprimer", methods=["POST"])
@login_required
def sso_delete(provider_id):
    conn = lock_access("sso.manage")
    row = _provider(provider_id)
    _revoke_provider(provider_id)
    # Les comptes restent dans le journal et peuvent être supprimés depuis Comptes ; aucune réassociation automatique.
    conn.execute("UPDATE users SET disabled=1 WHERE id IN (SELECT user_id FROM sso_identities WHERE provider_id=?)", (provider_id,))
    conn.execute("DELETE FROM sso_providers WHERE id=?", (provider_id,))
    audit("sso.fournisseur.suppression", row["label"], commit=False)
    conn.commit()
    return redirect(url_for("sso_settings"))


@app.route("/sso/<provider_id>/connexion")
def sso_start(provider_id):
    if g.user is not None:
        return redirect(url_for("index"))
    provider = _provider(provider_id, enabled=True)
    try:
        meta = _metadata(provider)
    except (OSError, ValueError, KeyError):
        flash("Le fournisseur SSO ne répond pas ou sa configuration est invalide.", "error")
        return redirect(url_for("login"))
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    callback = _callback(provider_id)
    payload = _seal(json.dumps({"nonce": nonce, "verifier": verifier, "callback": callback}))
    conn = db()
    conn.execute("DELETE FROM sso_flows WHERE expires<?", (time.time(),))
    # Une seule transaction en cours par navigateur ; borne globale contre la saturation de la base.
    if conn.execute("SELECT COUNT(*) FROM sso_flows").fetchone()[0] >= 1000:
        abort(429)
    conn.execute("INSERT INTO sso_flows VALUES (?,?,?,?,?,?)",
                 (_hash(state), provider_id, provider["revision"], _hash(browser), payload, time.time() + 600))
    conn.commit()
    params = {"response_type": "code", "client_id": provider["client_id"], "redirect_uri": callback,
              "state": state, "code_challenge_method": "S256",
              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
              "scope": "read:user user:email" if provider["preset"] == "github" else "openid profile email"}
    if provider["preset"] != "github":
        params["nonce"] = nonce
    separator = "&" if "?" in meta["authorization_endpoint"] else "?"
    response = redirect(meta["authorization_endpoint"] + separator + urllib.parse.urlencode(params))
    # Cookie de corrélation Lax séparé : la session principale conserve SameSite=Strict.
    response.set_cookie(COOKIE, browser, max_age=600, secure=app.config["SESSION_COOKIE_SECURE"], httponly=True,
                        samesite="Lax", path="/sso/")
    return response


def _claims(provider, code, flow):
    from ._vendor import jwt
    meta = _metadata(provider)
    secret = _open(provider["client_secret"])
    params = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["callback"],
              "client_id": provider["client_id"], "code_verifier": flow["verifier"]}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if provider["auth_method"] == "client_secret_post" or provider["preset"] == "github":
        params["client_secret"] = secret
    else:
        credentials = urllib.parse.quote(provider["client_id"], safe="") + ":" + urllib.parse.quote(secret, safe="")
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    token = _json(meta["token_endpoint"], urllib.parse.urlencode(params).encode(), headers)
    if provider["preset"] == "github":
        bearer = token.get("access_token")
        if not isinstance(bearer, str) or not bearer or "\n" in bearer or "\r" in bearer:
            raise ValueError("Jeton GitHub absent.")
        user = _json("https://api.github.com/user", headers={"Authorization": "Bearer " + bearer,
                      "X-GitHub-Api-Version": "2022-11-28"})
        if type(user.get("id")) is not int or user["id"] <= 0:
            raise ValueError("Identité GitHub absente.")
        return {"sub": str(user["id"]), "name": user.get("name") or user.get("login", ""), "email": user.get("email", "")}
    encoded = token.get("id_token", "")
    header = jwt.get_unverified_header(encoded)
    algorithm = header.get("alg")
    if algorithm not in ALGORITHMS or header.get("crit"):
        raise ValueError("Algorithme de signature OIDC non autorisé.")
    keys = _json(meta["jwks_uri"]).get("keys")
    if not isinstance(keys, list) or len(keys) > 100:
        raise ValueError("Clés OIDC invalides.")
    matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header.get("kid")
               and key.get("use", "sig") == "sig" and "verify" in key.get("key_ops", ["verify"])
               and key.get("alg", algorithm) == algorithm]
    if len(matches) != 1:
        raise ValueError("Clé de signature OIDC ambiguë ou inconnue.")
    key = jwt.PyJWK.from_dict(matches[0], algorithm=algorithm).key
    claims = jwt.decode(encoded, key, algorithms=[algorithm], issuer=provider["issuer"], audience=provider["client_id"],
                        leeway=30, options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]})
    # Comparaison exacte indépendante des tolérances de la bibliothèque et des fournisseurs.
    if claims["iss"] != provider["issuer"] or not hmac.compare_digest(str(claims["nonce"]), flow["nonce"]):
        raise ValueError("Émetteur ou nonce incorrect.")
    if (isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != provider["client_id"]
            or "azp" in claims and claims["azp"] != provider["client_id"]):
        raise ValueError("Client OIDC incorrect.")
    return claims


def _identity(provider, claims):
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise ValueError("Identité SSO absente.")
    conn = db()
    row = conn.execute("SELECT u.* FROM users u JOIN sso_identities i ON i.user_id=u.id "
                       "WHERE i.provider_id=? AND i.subject=?", (provider["id"], subject)).fetchone()
    if row is None:
        # Ne jamais rapprocher un compte existant par son courriel ou son nom, même « vérifiés ».
        username = "sso-" + secrets.token_hex(12)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        cur = conn.execute("INSERT INTO users (username,password_hash,role,disabled,pending,auth_source,created_at) "
                           "VALUES (?,?,'pending',1,1,'sso',?)", (username, generate_password_hash(secrets.token_urlsafe(48)), now))
        uid = cur.lastrowid
        conn.execute("INSERT INTO sso_identities VALUES (?,?,?,?,?)", (provider["id"], subject, uid,
                     str(claims.get("name") or claims.get("preferred_username") or "")[:200], str(claims.get("email") or "")[:254]))
        audit("sso.demande", f"{username} via {provider['label']}", username=username, commit=False)
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return row


@app.route("/sso/<provider_id>/retour")
def sso_callback(provider_id):
    # Consommation atomique avant l'appel réseau : un code / état ne peut servir qu'une fois.
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    state = request.args.get("state", "")[:512]
    flow = conn.execute("SELECT * FROM sso_flows WHERE state_hash=? AND provider_id=?", (_hash(state), provider_id)).fetchone()
    provider = _provider(provider_id, enabled=True)
    browser = request.cookies.get(COOKIE, "")
    valid = (flow is not None and browser and flow["expires"] > time.time()
             and flow["revision"] == provider["revision"] and hmac.compare_digest(flow["browser_hash"], _hash(browser)))
    if not valid:
        conn.rollback()
        return render_template("sso_status.html", message="Cette connexion SSO a expiré ou ne correspond pas à ce navigateur. Recommencez."), 400
    conn.execute("DELETE FROM sso_flows WHERE state_hash=?", (_hash(state),))
    conn.commit()
    try:
        if request.args.get("error") or not request.args.get("code") or len(request.args["code"]) > 4096:
            raise ValueError("Autorisation refusée.")
        if request.args.get("iss", provider["issuer"]) != provider["issuer"]:
            raise ValueError("Émetteur du retour incorrect.")
        values = json.loads(_open(flow["payload"]))
        claims = _claims(provider, request.args["code"], values)
        conn.execute("BEGIN IMMEDIATE")
        fresh = _provider(provider_id, enabled=True)
        if fresh["revision"] != flow["revision"]:
            raise ValueError("Configuration SSO modifiée.")
        row = _identity(fresh, claims)
        if row["pending"]:
            response = current_app.make_response((render_template("sso_status.html", message="Votre demande a été créée. Aucun accès n'est accordé avant l'approbation d'un administrateur."), 202))
        elif row["disabled"]:
            response = current_app.make_response((render_template("sso_status.html", message="Votre compte est désactivé. Contactez un administrateur."), 403))
        else:
            session.clear()
            session.permanent = True
            session.update(uid=row["id"], pwv=row["pw_version"], csrf=secrets.token_hex(16), sso_provider=provider_id)
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), row["id"]))
            audit("sso.connexion", provider["label"], username=row["username"], commit=False)
            response = current_app.make_response(render_template("sso_status.html", message="Connexion réussie. Ouverture de loco…", complete=True))
        conn.commit()
    except Exception as e:
        # Les exceptions HTTP/JWT peuvent contenir un jeton/secret : ne pas en journaliser le texte.
        conn.rollback()
        audit("sso.echec", f"{provider['label']} ({type(e).__name__})", username="")
        response = current_app.make_response((render_template("sso_status.html", message="Connexion SSO refusée. Réessayez ou contactez l'administrateur."), 400))
    response.delete_cookie(COOKIE, path="/sso/", secure=app.config["SESSION_COOKIE_SECURE"], httponly=True, samesite="Lax")
    return response


@app.route("/comptes/<int:user_id>/approuver", methods=["POST"])
@login_required
def users_approve(user_id):
    conn = lock_access("users.approve")
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    role = request.form.get("role", "")
    if not row["pending"] or row["auth_source"] != "sso" or role == "pending" or role not in permissions.role_labels(conn):
        abort(400)
    if not can_assign(role):
        abort(403)
    if not conn.execute("SELECT 1 FROM sso_identities i JOIN sso_providers p ON p.id=i.provider_id "
                        "WHERE i.user_id=? AND p.enabled=1", (user_id,)).fetchone():
        abort(400, "Le fournisseur SSO doit être actif avant l'approbation.")
    conn.execute("UPDATE users SET role=?,disabled=0,pending=0 WHERE id=?", (role, user_id))
    permissions.revoke(conn, user_id)
    audit("sso.approbation", f"{row['username']} : {permissions.role_labels(conn)[role]}", commit=False)
    conn.commit()
    flash("Compte approuvé. L'utilisateur peut maintenant se reconnecter par SSO.", "ok")
    return redirect(url_for("users"))
