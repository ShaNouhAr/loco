"""Connexion d'un client MCP par OAuth 2.1 (spécification d'autorisation MCP) : Claude Code, Codex et les autres
clients MCP se connectent à loco sans jeton à copier.

Le client découvre loco (métadonnées de la ressource protégée, RFC 9728, puis du serveur d'autorisation, RFC 8414),
s'enregistre seul (RFC 7591, client public), puis ouvre le navigateur sur la page d'autorisation de loco : la personne,
connectée à loco, choisit les outils et la durée, et autorise. Le code remis à l'application (sur cet ordinateur :
adresse de retour en boucle locale) s'échange contre un jeton MCP ordinaire — mêmes outils cochés, mêmes droits du
compte, même liste, même révocation. PKCE (S256) est obligatoire, le paramètre `iss` (RFC 9207) accompagne le retour et
le jeton n'est valable que pour le serveur MCP de loco (RFC 8707).

Le jeton n'est jamais montré : il passe du serveur à l'application, et seule son empreinte est gardée.
"""
import base64
import datetime
import hashlib
import ipaddress
import json
import re
import secrets
import time
import urllib.parse

from flask import abort, current_app, g, render_template, request

from . import app, config, features, mcp, permissions
from .auth import audit
from .db import db
from .i18n import _
from .util import counted

SCOPE = "mcp"
CODE_TTL = 120                          # secondes entre l'autorisation et l'échange du code
MAX_CLIENTS = 500
MAX_REDIRECTS = 5
CLIENT_PREFIX = "loco_client_"
CLIENT_RE = re.compile(r"^loco_client_[A-Za-z0-9_-]{22}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
STATE_MAX = 512
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+-]*(\.[a-z0-9+-]+)+$")     # schéma propre à une application : nom de domaine inversé
MAX_REGISTRATION = 16 * 1024
# Schémas qui ne désignent pas une application de l'ordinateur (ou qu'un navigateur interprète lui-même).
REFUSED_SCHEMES = {"http", "https", "javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ws", "wss", "filesystem"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def issuer():
    """Identité de loco comme serveur d'autorisation : l'adresse du portail, jamais l'en-tête Host."""
    return config.portal_url().rstrip("/")


def resource():
    return issuer() + "/mcp"


def by_ip():
    """loco est joint par une adresse IP : Codex refuse alors de s'y connecter par OAuth (il n'accepte un serveur
    d'autorisation d'adresse privée que par un nom), Claude Code l'accepte."""
    host = (urllib.parse.urlsplit(issuer()).hostname or "").strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def metadata_url():
    return issuer() + "/.well-known/oauth-protected-resource/mcp"


def challenge_header(invalid):
    """En-tête WWW-Authenticate d'un refus du serveur MCP : où trouver de quoi se connecter (RFC 9728, § 5.1)."""
    value = f'Bearer resource_metadata="{metadata_url()}", scope="{SCOPE}"'
    return value + ', error="invalid_token"' if invalid else value


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _json(payload, status=200, headers=None):
    response = current_app.response_class(json.dumps(payload, ensure_ascii=False), status=status, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _oauth_error(error, description, status=400):
    return _json({"error": error, "error_description": description}, status)


def _enabled():
    if not features.enabled("mcp"):
        abort(404)


# --------------------------------------------------------------------------- découverte

@app.route("/.well-known/oauth-protected-resource")
@app.route("/.well-known/oauth-protected-resource/mcp")
def oauth_protected_resource():
    _enabled()
    return _json({"resource": resource(), "authorization_servers": [issuer()], "scopes_supported": [SCOPE],
                  "bearer_methods_supported": ["header"], "resource_name": "loco"})


@app.route("/.well-known/oauth-authorization-server")
def oauth_authorization_server():
    _enabled()
    base = issuer()
    return _json({"issuer": base, "authorization_endpoint": base + "/mcp/oauth/authorize",
                  "token_endpoint": base + "/mcp/oauth/token", "registration_endpoint": base + "/mcp/oauth/register",
                  "revocation_endpoint": base + "/mcp/oauth/revoke", "scopes_supported": [SCOPE],
                  "response_types_supported": ["code"], "response_modes_supported": ["query"],
                  "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"],
                  "token_endpoint_auth_methods_supported": ["none"], "revocation_endpoint_auth_methods_supported": ["none"],
                  "authorization_response_iss_parameter_supported": True})


# --------------------------------------------------------------------------- enregistrement du client

def redirect_allowed(uri):
    """Adresse de retour acceptée : une application de cet ordinateur — boucle locale en http (RFC 8252, § 7.3), ou
    schéma propre à une application (§ 7.1). Jamais un site : le code ne doit pas quitter l'ordinateur."""
    if not isinstance(uri, str) or len(uri) > 400 or re.search(r"[\x00-\x20\x7f#]", uri):
        return False
    parts = urllib.parse.urlsplit(uri)
    scheme = parts.scheme.lower()
    if scheme == "http":
        try:
            port = parts.port
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        return host in LOOPBACK and not parts.username and not parts.password and (port is None or 0 < port < 65536)
    # RFC 8252, § 7.1 : nom de domaine inversé (com.exemple.app) ; jamais « web+… », qu'un site web peut s'attribuer.
    return len(scheme) <= 60 and bool(SCHEME_RE.match(scheme)) and scheme not in REFUSED_SCHEMES and not scheme.startswith("web+")


def redirect_matches(registered, given):
    """Adresse de retour d'une demande : l'une de celles enregistrées ; en boucle locale, le port peut changer."""
    if given in registered:
        return True
    parts = urllib.parse.urlsplit(given)
    if parts.scheme.lower() != "http":
        return False
    for uri in registered:
        known = urllib.parse.urlsplit(uri)
        if (known.scheme.lower() == "http" and (known.hostname or "").lower() == (parts.hostname or "").lower()
                and known.path == parts.path and known.query == parts.query):
            return True
    return False


def _purge(conn):
    """Codes expirés, et clients enregistrés jamais utilisés depuis un jour ou inutilisés depuis 90 jours sans jeton."""
    conn.execute("DELETE FROM mcp_oauth_codes WHERE expires_at < ?", (time.time(),))
    day = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    quarter = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM mcp_oauth_clients WHERE (used_at = '' AND created_at < ?) OR (used_at != '' AND used_at < ? "
                 "AND id NOT IN (SELECT client_id FROM mcp_tokens))", (day, quarter))


@app.route("/mcp/oauth/register", methods=["POST"])
def mcp_oauth_register():
    """Enregistrement d'un client public (RFC 7591). Ouvert, comme le prévoit la spécification MCP : enregistrer un
    client ne donne aucun accès, l'autorisation se décide dans loco par une personne connectée."""
    _enabled()
    if (request.content_length or 0) > MAX_REGISTRATION:
        return _oauth_error("invalid_client_metadata", "Registration request too large.", 413)
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict):
        return _oauth_error("invalid_client_metadata", "The body must be a JSON object.")
    uris = data.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        return _oauth_error("invalid_redirect_uri", "redirect_uris is required.")
    kept = list(dict.fromkeys(u for u in uris if redirect_allowed(u)))[:MAX_REDIRECTS]
    if not kept:
        return _oauth_error("invalid_redirect_uri", "Only loopback (http://127.0.0.1, http://localhost, http://[::1]) or "
                            "private-use scheme redirect URIs are accepted: the code must stay on this computer.")
    grant_types = data.get("grant_types") or ["authorization_code"]
    response_types = data.get("response_types") or ["code"]
    if (not isinstance(grant_types, list) or "authorization_code" not in grant_types
            or not isinstance(response_types, list) or "code" not in response_types):
        return _oauth_error("invalid_client_metadata", "Only the authorization_code grant with the code response type is supported.")
    name = mcp.CONTROL_RE.sub(" ", str(data.get("client_name") or "")).strip()[:80] or _("Client MCP")
    conn = db()
    _purge(conn)
    if conn.execute("SELECT COUNT(*) FROM mcp_oauth_clients").fetchone()[0] >= MAX_CLIENTS:
        conn.commit()
        return _oauth_error("temporarily_unavailable", "Too many registered clients.", 503)
    client_id = CLIENT_PREFIX + secrets.token_urlsafe(16)
    issued = int(time.time())
    conn.execute("INSERT INTO mcp_oauth_clients (id, name, redirect_uris, created_at) VALUES (?,?,?,?)",
                 (client_id, name, json.dumps(kept), _now()))
    conn.commit()
    return _json({"client_id": client_id, "client_id_issued_at": issued, "client_name": name, "redirect_uris": kept,
                  "grant_types": ["authorization_code"], "response_types": ["code"],
                  "token_endpoint_auth_method": "none", "scope": SCOPE}, 201)


# --------------------------------------------------------------------------- autorisation (page de loco)

class Refusal(Exception):
    """Demande d'autorisation invalide. `redirect_uri` : adresse déjà vérifiée où renvoyer l'erreur à l'application."""
    def __init__(self, error, message, redirect_uri=""):
        super().__init__(message)
        self.error, self.message, self.redirect_uri = error, message, redirect_uri


def _same_resource(value):
    return not value or value.rstrip("/").lower() == resource().lower()


def _request(values):
    """Paramètres d'une demande d'autorisation, vérifiés : (client, paramètres). Refusal sinon."""
    client = db().execute("SELECT * FROM mcp_oauth_clients WHERE id=?", (values.get("client_id", ""),)).fetchone()
    if client is None:
        raise Refusal("invalid_client", _("Application inconnue de loco : relancez la connexion depuis l'application."))
    redirect_uri = values.get("redirect_uri", "")
    try:
        registered = json.loads(client["redirect_uris"])
    except ValueError:
        registered = []
    if not redirect_uri and len(registered) == 1:
        redirect_uri = registered[0]
    if not redirect_allowed(redirect_uri) or not redirect_matches(registered, redirect_uri):
        raise Refusal("invalid_request", _("Adresse de retour non enregistrée pour cette application."))
    params = {"client_id": client["id"], "redirect_uri": redirect_uri, "state": values.get("state", "")[:STATE_MAX],
              "code_challenge": values.get("code_challenge", ""), "code_challenge_method": values.get("code_challenge_method", ""),
              "resource": values.get("resource", ""), "scope": values.get("scope", "")}
    if values.get("response_type") != "code":
        raise Refusal("unsupported_response_type", _("Type de réponse non pris en charge."), redirect_uri)
    if params["code_challenge_method"] != "S256" or not CHALLENGE_RE.match(params["code_challenge"]):
        raise Refusal("invalid_request", _("Demande sans PKCE (S256) : refusée."), redirect_uri)
    if not _same_resource(params["resource"]):
        raise Refusal("invalid_target", _("Cette demande vise un autre serveur que celui de loco."), redirect_uri)
    return client, params


def _back_to_app(redirect_uri, values):
    """Adresse de retour vers l'application, complétée (code ou erreur, state, iss)."""
    query = urllib.parse.urlencode({k: v for k, v in values.items() if v})
    return redirect_uri + ("&" if "?" in redirect_uri else "?") + query


def _finish(redirect_uri, values, message, kind="ok"):
    """Retour vers l'application par une page intermédiaire : la CSP « form-action 'self' » interdit au navigateur de
    suivre une redirection vers une autre origine après l'envoi d'un formulaire."""
    target = _back_to_app(redirect_uri, dict(values, iss=issuer()))
    return render_template("mcp_oauth_done.html", message=message, kind=kind, target=target)


def _target(uri):
    parts = urllib.parse.urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else f"{parts.scheme}:"


def _page(client, params, error=None, status=200):
    own = permissions.role_permissions(db(), g.user["role"])
    tools = mcp._catalog_view(own)
    # Cochés d'office : la consultation, sans l'accès distant (lire les fichiers d'un poste, voir son écran).
    checked = [t["name"] for grp in tools if grp["key"] != "remote" for t in grp["tools"] if t["available"] and t["read_only"]]
    name = client["name"].lower()
    brand = "claude" if "claude" in name else "codex" if "codex" in name else ""
    return render_template("mcp_authorize.html", client=client, params=params, tools=tools, checked=checked, brand=brand,
                           target=_target(params["redirect_uri"]), error=error), status


@app.route("/mcp/oauth/authorize", methods=["GET", "POST"])
def mcp_oauth_authorize():
    """Page d'autorisation : la personne connectée à loco choisit les outils et la durée, puis autorise (ou refuse)."""
    _enabled()
    values = request.values if request.method == "GET" else request.form
    try:
        client, params = _request(values)
    except Refusal as e:
        if e.redirect_uri:
            return _finish(e.redirect_uri, {"error": e.error, "error_description": e.message, "state": values.get("state", "")[:STATE_MAX]},
                           e.message, "err")
        return render_template("mcp_oauth_done.html", message=e.message, kind="err", target=""), 400
    if request.method == "GET":
        return _page(client, params)
    if request.form.get("decision") != "allow":
        audit("mcp.oauth.refus", client["name"])
        return _finish(params["redirect_uri"], {"error": "access_denied", "error_description": "The user denied access.",
                                                 "state": params["state"]}, _("Accès refusé. Vous pouvez fermer cette page."), "warn")
    days = mcp.EXPIRIES.get(request.form.get("expiry", "90"))
    if days is None:
        abort(400)
    try:
        name, chosen = mcp._form_values()
    except ValueError as e:
        return _page(client, params, str(e), 400)
    code = secrets.token_urlsafe(32)
    conn = db()
    _purge(conn)
    conn.execute("INSERT INTO mcp_oauth_codes (code_hash, client_id, user_id, redirect_uri, challenge, resource, granted, expires_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (_hash(code), client["id"], g.user["id"], params["redirect_uri"], params["code_challenge"],
                                              params["resource"], json.dumps({"name": name, "tools": chosen, "days": days}),
                                              time.time() + CODE_TTL))
    conn.execute("UPDATE mcp_oauth_clients SET used_at=? WHERE id=?", (_now(), client["id"]))
    audit("mcp.oauth.autorisation", f"{name} ({client['name']}) : {counted(len(chosen), 'outil')}", commit=False)
    conn.commit()
    return _finish(params["redirect_uri"], {"code": code, "state": params["state"]},
                   _("Accès autorisé : retour à %(app)s. Vous pouvez fermer cette page.", app=client["name"]))


# --------------------------------------------------------------------------- jeton

@app.route("/mcp/oauth/token", methods=["POST"])
def mcp_oauth_token():
    """Échange du code contre un jeton MCP (client public : le vérificateur PKCE fait foi)."""
    _enabled()
    form = request.form
    if form.get("grant_type") != "authorization_code":
        return _oauth_error("unsupported_grant_type", "Only authorization_code is supported.")
    code, verifier = form.get("code", ""), form.get("code_verifier", "")
    if not code or not VERIFIER_RE.match(verifier):
        return _oauth_error("invalid_request", "code and a valid code_verifier are required.")
    conn = db()
    row = conn.execute("SELECT * FROM mcp_oauth_codes WHERE code_hash=?", (_hash(code),)).fetchone()
    if row is not None:
        # Un seul essai par code, même pour deux requêtes simultanées : seule celle qui le supprime le garde.
        if conn.execute("DELETE FROM mcp_oauth_codes WHERE code_hash=?", (row["code_hash"],)).rowcount != 1:
            row = None
        conn.commit()
    if row is None or row["expires_at"] < time.time():
        return _oauth_error("invalid_grant", "Unknown, used or expired authorization code.")
    client_id = form.get("client_id") or row["client_id"]
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if client_id != row["client_id"] or not secrets.compare_digest(computed, row["challenge"]):
        return _oauth_error("invalid_grant", "The code was not issued to this client, or the PKCE verifier does not match.")
    if form.get("redirect_uri", row["redirect_uri"]) != row["redirect_uri"]:
        return _oauth_error("invalid_grant", "redirect_uri differs from the authorization request.")
    if not _same_resource(form.get("resource", "")) or not _same_resource(row["resource"]):
        return _oauth_error("invalid_target", "This token can only be used with the loco MCP server.")
    user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    client = conn.execute("SELECT * FROM mcp_oauth_clients WHERE id=?", (row["client_id"],)).fetchone()
    if user is None or user["disabled"] or client is None:
        return _oauth_error("invalid_grant", "The account or the client is no longer valid.")
    granted = json.loads(row["granted"])
    tools = [n for n in granted.get("tools", []) if n in mcp.TOOLS]
    secret = mcp.PREFIX + secrets.token_urlsafe(32)
    days = granted.get("days") or 0
    expires = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else ""
    conn.execute("INSERT INTO mcp_tokens (user_id, name, token_hash, hint, tools, created_at, expires_at, client_id) "
                 "VALUES (?,?,?,?,?,?,?,?)", (user["id"], granted.get("name") or client["name"], _hash(secret),
                                              secret[:len(mcp.PREFIX) + 4], json.dumps(tools), _now(), expires, client["id"]))
    audit("mcp.acces.ajout", f"{granted.get('name') or client['name']} : {counted(len(tools), 'outil')} (OAuth, {client['name']})"
          + (f", expire le {expires[:10]}" if expires else ""), username=user["username"], commit=False)
    conn.commit()
    answer = {"access_token": secret, "token_type": "Bearer", "scope": SCOPE}
    if days:
        answer["expires_in"] = days * 86400
    return _json(answer)


@app.route("/mcp/oauth/revoke", methods=["POST"])
def mcp_oauth_revoke():
    """Révocation par l'application (RFC 7009) : « se déconnecter » retire le jeton de loco. Toujours 200."""
    _enabled()
    value = request.form.get("token", "")
    if mcp.TOKEN_RE.match(value):
        conn = db()
        row = conn.execute("SELECT t.*, u.username FROM mcp_tokens t JOIN users u ON u.id=t.user_id WHERE token_hash=? "
                           "AND client_id != ''", (_hash(value),)).fetchone()
        if row is not None and request.form.get("client_id", row["client_id"]) == row["client_id"]:
            from .mcp_remote import close_sessions
            close_sessions(row["id"], "Jeton MCP révoqué par son application.")
            conn.execute("DELETE FROM mcp_tokens WHERE id=?", (row["id"],))
            audit("mcp.acces.revocation", f"{row['name']} ({row['username']}), par son application", username=row["username"], commit=False)
            conn.commit()
    return _json({})


def client_names(conn, ids):
    """Nom des applications qui ont obtenu des jetons par OAuth : {client_id: nom}."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r["name"] for r in conn.execute(f"SELECT id, name FROM mcp_oauth_clients WHERE id IN ({marks})", ids)}


# --------------------------------------------------------------------------- liens vers les applications

def prompts(endpoint, ca_url, fingerprint):
    """Liens qui ouvrent Claude Code (application de bureau) ou Codex avec la demande d'ajout pré-remplie : le serveur
    est ajouté à la configuration locale de l'application, sans jeton ; la connexion se fait ensuite par loco."""
    trust = _("Si le certificat de loco n'est pas reconnu, son autorité est à %(ca)s (empreinte SHA-256 %(fp)s) : "
              "vérifie l'empreinte avant de l'utiliser.", ca=ca_url, fp=fingerprint) if fingerprint else ""
    claude = " ".join(p for p in (
        _("Ajoute le serveur MCP de loco à ma configuration locale de Claude Code, pour tous mes projets : "
          "« claude mcp add --scope user --transport http loco %(url)s ». Aucun jeton n'est nécessaire. Ensuite, lance "
          "« claude mcp login loco » : mon navigateur s'ouvrira sur loco pour que j'autorise l'accès.", url=endpoint),
        _("Si la commande claude n'est pas disponible, ajoute dans ~/.claude.json, sous « mcpServers », l'entrée "
          "« loco » de type « http » à l'adresse %(url)s, puis dis-moi de me connecter depuis le menu des serveurs MCP.", url=endpoint),
        trust and trust + " " + _("Pour Claude Code : variable NODE_EXTRA_CA_CERTS dans le bloc « env » de ~/.claude/settings.json."),
    ) if p)
    codex = " ".join(p for p in (
        _("Ajoute le serveur MCP de loco à ma configuration locale de Codex : « codex mcp add loco --url %(url)s » "
          "(ou, sans la commande codex, une section [mcp_servers.loco] avec url = \"%(url)s\" dans ~/.codex/config.toml). "
          "Aucun jeton n'est nécessaire. Ensuite, lance « codex mcp login loco » : mon navigateur s'ouvrira sur loco pour "
          "que j'autorise l'accès.", url=endpoint),
        trust and trust + " " + _("Pour Codex : variable CODEX_CA_CERTIFICATE pointant sur ce fichier."),
    ) if p)
    quote = urllib.parse.quote
    return {"claude": "claude://code/new?q=" + quote(claude, safe=""), "codex": "codex://new?prompt=" + quote(codex, safe=""),
            "claude_text": claude, "codex_text": codex, "by_ip": by_ip()}
