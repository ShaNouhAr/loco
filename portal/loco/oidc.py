"""Connexion OpenID Connect (Authentik, Keycloak, Zitadel…) : flux « code » avec PKCE et nonce, jeton d'identité
vérifié (signature, émetteur, audience, expiration) et identités liées comme pour les annuaires LDAP et RADIUS.

Aucun jeton n'est conservé : le jeton d'identité sert une fois à ouvrir la session loco, puis est oublié."""
import base64
import datetime
import hashlib
import hmac
import http.client
import json
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import abort, g, make_response, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import app, config
from .auth import _complete_login, audit
from .db import db
from .i18n import L_, _
from .util import safe_next

LIMIT = 1024 * 1024                      # réponse JSON du fournisseur
LEEWAY = 120                             # écart d'horloge toléré (s)
CACHE_TTL = 300                          # découverte et clés publiques
FLOW_COOKIE, FLOW_PATH, FLOW_TTL = "loco_sso", "/login/sso/", 600
SCOPES = "openid profile email"
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA",
              "HS256", "HS384", "HS512")


class OIDCError(Exception):
    """Configuration ou dialogue avec le fournisseur en échec ; le message ne contient jamais de secret."""


# --------------------------------------------------------------------------- HTTP vers le fournisseur

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OIDCError(_("Redirection inattendue du fournisseur : vérifiez l’adresse de l’émetteur."))


def https_url(value):
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment
            or any(ord(c) <= 32 for c in value) or len(value) > 2048):
        raise OIDCError(_("Adresse HTTPS attendue : %(url)s", url=value[:200]))
    return value


def _tls(cfg):
    """(contexte TLS, empreinte épinglée ou None). Sans PEM : autorités du système. Certificat auto-signé de serveur
    (celui d'Authentik par défaut, au SAN « * » inutilisable) : épinglé à l'identique. Autorité : chaîne et nom vérifiés."""
    pem = cfg.get("ca_pem") or ""
    if not pem:
        return ssl.create_default_context(), None
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        certs = x509.load_pem_x509_certificates(pem.encode())
    except ValueError:
        raise OIDCError(_("Autorité (PEM) illisible."))
    if len(certs) == 1 and certs[0].issuer == certs[0].subject:
        try:
            authority = certs[0].extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            authority = False
        if not authority:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE                 # authentifié par l'empreinte exacte, après la poignée de main
            return context, hashlib.sha256(certs[0].public_bytes(serialization.Encoding.DER)).digest()
    try:
        context = ssl.create_default_context(cadata=pem)
    except (ssl.SSLError, ValueError):
        raise OIDCError(_("Autorité (PEM) illisible."))
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT              # autorité interne choisie par l'administrateur
    return context, None


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    """HTTPS authentifié par l'empreinte SHA-256 exacte du certificat du serveur."""
    def __init__(self, context, pin):
        super().__init__(context=context)
        self.pin = pin

    def https_open(self, req):
        pin = self.pin

        class Connection(http.client.HTTPSConnection):
            def connect(self):
                super().connect()
                der = self.sock.getpeercert(binary_form=True)
                if not der or not hmac.compare_digest(hashlib.sha256(der).digest(), pin):
                    self.close()
                    raise OIDCError(_("Le certificat présenté par le fournisseur n’est pas celui collé dans loco."))
        return self.do_open(Connection, req, context=self._context)


TOKEN_ERRORS = {
    "invalid_client": L_("Identifiant ou secret client refusé par le fournisseur."),
    "invalid_grant": L_("Code refusé par le fournisseur (expiré, déjà utilisé, ou URL de redirection différente)."),
    "unauthorized_client": L_("Ce client n’est pas autorisé à utiliser le flux « code » chez le fournisseur."),
}


def _json(cfg, url, data=None, headers=None):
    req = urllib.request.Request(https_url(url), data=data, headers={"Accept": "application/json",
                                 "User-Agent": config.USER_AGENT, **(headers or {})})
    context, pin = _tls(cfg)
    opener = urllib.request.build_opener(_NoRedirect(), _PinnedHTTPS(context, pin) if pin
                                         else urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(req, timeout=10) as response:
            payload = response.read(LIMIT + 1)
    except urllib.error.HTTPError as e:
        with e:
            try:
                body = json.loads(e.read(4096) or b"{}")
            except (ValueError, OSError):
                body = {}
        detail = body.get("error") if isinstance(body, dict) else None
        if isinstance(detail, str) and detail in TOKEN_ERRORS:
            raise OIDCError(str(TOKEN_ERRORS[detail]))
        raise OIDCError(_("Le fournisseur a répondu HTTP %(code)s.", code=e.code))
    except ssl.SSLCertVerificationError:
        raise OIDCError(_("Certificat du fournisseur non reconnu : collez son autorité (PEM) dans les options avancées."))
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise OIDCError(_("Certificat du fournisseur non reconnu : collez son autorité (PEM) dans les options avancées."))
        raise OIDCError(_("Fournisseur injoignable : %(e)s", e=str(reason)[:200]))
    if len(payload) > LIMIT:
        raise OIDCError(_("Réponse du fournisseur trop volumineuse."))
    try:
        result = json.loads(payload)
    except ValueError:
        raise OIDCError(_("Réponse du fournisseur illisible (JSON attendu)."))
    if not isinstance(result, dict):
        raise OIDCError(_("Réponse du fournisseur illisible (JSON attendu)."))
    return result


# --------------------------------------------------------------------------- découverte et clés publiques

_cache, _cache_lock = {}, threading.Lock()


def _cached(kind, cfg, fetch, fresh=False):
    key = (kind, hashlib.sha256((cfg["issuer"] + "\0" + cfg.get("ca_pem", "")).encode()).hexdigest())
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and hit[0] > time.monotonic():
            return hit[1]
    value = fetch()
    with _cache_lock:
        _cache[key] = (time.monotonic() + CACHE_TTL, value)
    return value


def metadata(cfg, fresh=False):
    """Document de découverte de l'émetteur, vérifié : même émetteur, points d'accès HTTPS, PKCE S256."""
    def fetch():
        issuer = cfg["issuer"]
        doc = _json(cfg, issuer.rstrip("/") + "/.well-known/openid-configuration")
        found = doc.get("issuer")
        if not isinstance(found, str) or found.rstrip("/") != issuer.rstrip("/"):
            raise OIDCError(_("L’émetteur annoncé par le fournisseur (%(found)s) ne correspond pas à celui configuré.",
                              found=str(found)[:200]))
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(doc.get(name), str):
                raise OIDCError(_("Document de découverte incomplet (%(name)s).", name=name))
            https_url(doc[name])
        if doc.get("userinfo_endpoint") is not None:
            https_url(str(doc["userinfo_endpoint"]))
        methods = doc.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OIDCError(_("Le fournisseur ne gère pas PKCE (S256)."))
        return doc
    return _cached("meta", cfg, fetch, fresh)


def public_keys(cfg, meta, fresh=False):
    def fetch():
        keys = _json(cfg, meta["jwks_uri"]).get("keys")
        if not isinstance(keys, list) or len(keys) > 100:
            raise OIDCError(_("Clés publiques du fournisseur illisibles."))
        return [k for k in keys if isinstance(k, dict)]
    return _cached("jwks", cfg, fetch, fresh)


# --------------------------------------------------------------------------- jeton d'identité (JWS compact)

def _b64(part):
    if not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", part):
        raise OIDCError(_("Jeton d’identité mal formé."))
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _uint(value):
    return int.from_bytes(_b64(value), "big")


def _hash(alg):
    from cryptography.hazmat.primitives import hashes
    return {"256": hashes.SHA256, "384": hashes.SHA384, "512": hashes.SHA512}[alg[2:]]()


CURVES = {"ES256": ("P-256", 32), "ES384": ("P-384", 48), "ES512": ("P-521", 66)}


def _public_key(jwk, alg):
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    if alg[:2] in ("RS", "PS"):
        key = rsa.RSAPublicNumbers(_uint(jwk["e"]), _uint(jwk["n"])).public_key()
        if key.key_size < 2048:
            raise OIDCError(_("Clé RSA du fournisseur trop courte."))
        return key
    if alg in CURVES:
        curve = {"P-256": ec.SECP256R1, "P-384": ec.SECP384R1, "P-521": ec.SECP521R1}[CURVES[alg][0]]
        return ec.EllipticCurvePublicNumbers(_uint(jwk["x"]), _uint(jwk["y"]), curve()).public_key()
    return ed25519.Ed25519PublicKey.from_public_bytes(_b64(jwk["x"]))


def _signature_ok(key, alg, data, signature):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    try:
        if alg.startswith("RS"):
            key.verify(signature, data, padding.PKCS1v15(), _hash(alg))
        elif alg.startswith("PS"):
            key.verify(signature, data, padding.PSS(mgf=padding.MGF1(_hash(alg)), salt_length=_hash(alg).digest_size),
                       _hash(alg))
        elif alg in CURVES:
            size = CURVES[alg][1]
            if len(signature) != 2 * size:
                return False
            der = encode_dss_signature(int.from_bytes(signature[:size], "big"), int.from_bytes(signature[size:], "big"))
            key.verify(der, data, ec.ECDSA(_hash(alg)))
        else:
            key.verify(signature, data)
    except InvalidSignature:
        return False
    return True


def _candidates(keys, alg, kid):
    kty = "RSA" if alg[:2] in ("RS", "PS") else "EC" if alg in CURVES else "OKP"
    return [k for k in keys if k.get("kty") == kty and k.get("use", "sig") == "sig"
            and "verify" in (k.get("key_ops") or ["verify"]) and k.get("alg", alg) == alg
            and (kid is None or k.get("kid") == kid) and (alg not in CURVES or k.get("crv") == CURVES[alg][0])
            and (alg != "EdDSA" or k.get("crv") == "Ed25519")]


def verify_id_token(token, cfg, meta, nonce):
    """Revendications du jeton d'identité après vérification complète, sinon OIDCError."""
    parts = token.split(".") if isinstance(token, str) and len(token) < 65536 else []
    if len(parts) != 3:
        raise OIDCError(_("Le fournisseur n’a pas renvoyé de jeton d’identité."))
    try:
        header, claims = json.loads(_b64(parts[0])), json.loads(_b64(parts[1]))
    except ValueError:
        raise OIDCError(_("Jeton d’identité mal formé."))
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise OIDCError(_("Jeton d’identité mal formé."))
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ALGORITHMS or header.get("crit") or (kid is not None and not isinstance(kid, str)):
        raise OIDCError(_("Algorithme de signature refusé : %(alg)s.", alg=str(alg)[:20]))
    data, signature = (parts[0] + "." + parts[1]).encode("ascii"), _b64(parts[2])
    if alg.startswith("HS"):
        # Authentik sans clé de signature : HMAC avec le secret client, connu seulement du fournisseur et de loco.
        secret = cfg.get("client_secret", "")
        expected = hmac.new(secret.encode(), data, {"256": hashlib.sha256, "384": hashlib.sha384,
                                                    "512": hashlib.sha512}[alg[2:]]).digest()
        valid = bool(secret) and hmac.compare_digest(expected, signature)
    else:
        keys = _candidates(public_keys(cfg, meta), alg, kid)
        if not keys:                                             # rotation des clés : relire une fois
            keys = _candidates(public_keys(cfg, meta, fresh=True), alg, kid)
        if not keys:
            raise OIDCError(_("Clé de signature inconnue du fournisseur."))
        valid = False
        for jwk in keys:
            try:
                valid = _signature_ok(_public_key(jwk, alg), alg, data, signature)
            except (KeyError, ValueError, TypeError):
                continue
            if valid:
                break
    if not valid:
        raise OIDCError(_("Signature du jeton d’identité invalide."))
    now, client = time.time(), cfg["client_id"]
    if claims.get("iss") != meta["issuer"]:
        raise OIDCError(_("Émetteur du jeton d’identité incorrect."))
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) else []
    if client not in audiences or (len(audiences) > 1 and claims.get("azp") != client) \
            or ("azp" in claims and claims["azp"] != client):
        raise OIDCError(_("Jeton d’identité destiné à un autre client."))
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if not number(claims.get("exp")) or claims["exp"] + LEEWAY < now:
        raise OIDCError(_("Jeton d’identité expiré : vérifiez l’heure de ce serveur."))
    if not number(claims.get("iat")) or claims["iat"] - LEEWAY > now:
        raise OIDCError(_("Jeton d’identité daté du futur : vérifiez l’heure de ce serveur."))
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
        raise OIDCError(_("Jeton d’identité rejoué ou d’une autre connexion (nonce)."))
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise OIDCError(_("Identité absente du jeton."))
    return claims


# --------------------------------------------------------------------------- échange du code

def _s256(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def callback_url(directory_id):
    # Toujours l'adresse d'administration configurée, jamais l'en-tête Host du navigateur.
    return config.HTTPS_URL.rstrip("/") + FLOW_PATH + directory_id + "/callback"


def identity(cfg, meta, code, flow):
    """Échange le code, vérifie le jeton d'identité ; complète par userinfo si nom d'utilisateur ou courriel manquent."""
    params = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["u"], "code_verifier": flow["v"]}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    methods = meta.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if "client_secret_basic" in methods:
        credentials = urllib.parse.quote(cfg["client_id"], safe="") + ":" + urllib.parse.quote(cfg["client_secret"], safe="")
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    elif "client_secret_post" in methods:
        params.update(client_id=cfg["client_id"], client_secret=cfg["client_secret"])
    else:
        raise OIDCError(_("Le fournisseur n’accepte ni client_secret_basic ni client_secret_post."))
    tokens = _json(cfg, meta["token_endpoint"], urllib.parse.urlencode(params).encode(), headers)
    claims = verify_id_token(tokens.get("id_token"), cfg, meta, flow["n"])
    access = tokens.get("access_token")
    wanted = cfg.get("username_claim") or "preferred_username"
    if (not claims.get(wanted) or not claims.get("email")) and meta.get("userinfo_endpoint") \
            and isinstance(access, str) and access and len(access) < 8192 and not re.search(r"\s", access):
        info = _json(cfg, meta["userinfo_endpoint"], headers={"Authorization": "Bearer " + access})
        if info.get("sub") != claims["sub"]:
            raise OIDCError(_("Profil du fournisseur incohérent avec le jeton d’identité."))
        claims = {**info, **{k: v for k, v in claims.items() if v not in (None, "")}}
    return claims


# --------------------------------------------------------------------------- état du flux (cookie chiffré)

def _flow_cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-sso-flow-v1", hashlib.sha256).digest()))


def _open_flow(value):
    from cryptography.fernet import InvalidToken
    try:
        flow = json.loads(_flow_cipher().decrypt(value.encode(), ttl=FLOW_TTL)) if value else None
    except (InvalidToken, ValueError):
        return None
    return flow if isinstance(flow, dict) else None


def begin(directory, mode="login", next_url="/"):
    """Redirige vers le fournisseur ; l'état (PKCE, nonce, état) reste dans un cookie chiffré lié à ce navigateur."""
    from .directories import config_of
    cfg = config_of(directory)
    meta = metadata(cfg)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    flow = {"d": directory["id"], "r": directory["revision"], "s": state, "n": nonce, "v": verifier, "m": mode,
            "u": callback_url(directory["id"]), "next": safe_next(next_url)}
    params = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": flow["u"],
              "scope": cfg.get("scopes") or SCOPES, "state": state, "nonce": nonce,
              "code_challenge": _s256(verifier), "code_challenge_method": "S256"}
    # Pas de prompt=login pour le test : Authentik redemanderait alors les identifiants une 2e fois à un navigateur
    # qui vient de se connecter. Le test se fait donc avec le compte ouvert chez le fournisseur, s'il y en a un.
    endpoint = meta["authorization_endpoint"]
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(params)
    if mode == "test":
        # Le test part d'un formulaire (POST + CSRF) : la CSP « form-action 'self' » interdit au navigateur d'en suivre
        # une redirection vers un autre site. Il y va donc depuis une page intermédiaire.
        response = make_response(render_template("sso_status.html", message=_("Redirection vers %(label)s…",
                                 label=directory["label"]), kind="", next_url=url, heading=_("Test de connexion")))
    else:
        response = redirect(url)
    # SameSite=Lax : le retour du fournisseur est une navigation d'un autre site ; la session reste en Strict.
    response.set_cookie(FLOW_COOKIE, _flow_cipher().encrypt(json.dumps(flow).encode()).decode(), max_age=FLOW_TTL,
                        secure=app.config["SESSION_COOKIE_SECURE"], httponly=True, samesite="Lax", path=FLOW_PATH)
    return response


def _enabled(directory_id):
    from .directories import enabled_directories
    return next((d for d in enabled_directories() if d["id"] == directory_id and d["kind"] == "oidc"), None)


def _any(directory_id):
    """Fournisseur même inactif : un test peut précéder l'activation."""
    row = db().execute("SELECT * FROM auth_directories WHERE id=? AND kind='oidc'", (directory_id,)).fetchone()
    return row


@app.route("/login/sso/<directory_id>")
def sso_start(directory_id):
    if g.user is not None:
        return redirect(url_for("index"))
    directory = _enabled(directory_id)
    if directory is None:
        abort(404)
    try:
        return begin(directory, "login", request.args.get("next", "/"))
    except OIDCError as e:
        audit("sso.echec", f"{directory['label']} : {e}", username="")
        return _status(_("%(label)s ne répond pas ou sa configuration est invalide. Réessayez plus tard ou utilisez "
                         "votre compte loco.", label=directory["label"]), 502)


def _status(message, code=200, kind="err", next_url=None, test=None, testing=False, hint=None):
    response = make_response(render_template("sso_status.html", message=message, kind=kind, next_url=next_url,
                                             test=test, testing=testing, hint=hint), code)
    response.delete_cookie(FLOW_COOKIE, path=FLOW_PATH, secure=app.config["SESSION_COOKIE_SECURE"], httponly=True,
                           samesite="Lax")
    return response


def _wanted_username(claims, cfg):
    for value in (claims.get(cfg.get("username_claim") or "preferred_username"), claims.get("preferred_username"),
                  str(claims.get("email") or "").split("@")[0]):
        if isinstance(value, str):
            name = re.sub(r"[^a-z0-9._@-]+", "-", value.strip().lower()).strip("-.@")[:64]
            if name and name[0].isalnum():
                return name
    return "sso-" + secrets.token_hex(4)


def _link(directory, cfg, claims):
    """(compte, None) si une identité liée existe ; (None, message) pour une nouvelle demande ou un compte en attente."""
    from .directories import _free_username
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    subject = claims["sub"]                                      # identifiant opaque : casse conservée
    display = str(claims.get("name") or claims.get("preferred_username") or "")[:200]
    email = str(claims.get("email") or "")[:254]
    link = conn.execute("SELECT * FROM directory_identities WHERE directory_id=? AND subject=?",
                        (directory["id"], subject)).fetchone()
    if link is None:
        # Jamais de rapprochement avec un compte existant par son nom ou son courriel : nouvelle demande d'accès.
        name = _free_username(conn, _wanted_username(claims, cfg), directory["label"])
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        uid = conn.execute("INSERT INTO users (username,password_hash,role,disabled,pending,auth_source,created_at) "
                           "VALUES (?,?,'pending',1,1,'directory',?)",
                           (name, generate_password_hash(secrets.token_urlsafe(48)), now)).lastrowid
        conn.execute("INSERT INTO directory_identities (directory_id, subject, user_id, display_name, email) VALUES (?,?,?,?,?)",
                     (directory["id"], subject, uid, display, email))
        audit("sso.demande", f"{name} via {directory['label']}", username=name, commit=False)
        conn.commit()
        return None, _("Votre demande d’accès a été créée. Un administrateur doit l’approuver avant votre première connexion.")
    conn.execute("UPDATE directory_identities SET display_name=?, email=? WHERE directory_id=? AND subject=?",
                 (display or link["display_name"], email or link["email"], directory["id"], subject))
    user = conn.execute("SELECT * FROM users WHERE id=?", (link["user_id"],)).fetchone()
    conn.commit()
    if user["pending"]:
        return None, _("Votre demande d’accès attend encore l’approbation d’un administrateur.")
    if user["disabled"]:
        return None, _("Votre compte loco est désactivé. Contactez un administrateur.")
    return user, None


@app.route("/login/sso/<directory_id>/callback")
def sso_callback(directory_id):
    flow = _open_flow(request.cookies.get(FLOW_COOKIE, ""))
    directory = None
    if flow is not None and flow.get("d") == directory_id:
        directory = _any(directory_id) if flow.get("m") == "test" else _enabled(directory_id)
    test = flow is not None and flow.get("m") == "test"
    if (flow is None or directory is None or flow.get("r") != directory["revision"]
            or not hmac.compare_digest(str(request.args.get("state", ""))[:512], str(flow.get("s", "")))):
        return _status(_("Cette connexion a expiré ou a été commencée dans un autre navigateur. Recommencez."), 400,
                       testing=test)
    label, hint, detail = directory["label"], None, ""
    from .directories import config_of
    cfg = config_of(directory)
    try:
        if request.args.get("error"):
            error = re.sub(r"[^a-z_]", "", request.args["error"])[:40]
            if error == "access_denied":
                raise OIDCError(_("%(label)s a refusé l’accès : votre compte n’est peut-être pas autorisé à utiliser "
                                  "loco chez le fournisseur.", label=label))
            # Détail du fournisseur (texte libre) : seulement pour l'administrateur qui teste, et le journal.
            detail = " ".join(re.sub(r"[^\w .,:;'’()/-]", "", str(request.args.get("error_description", ""))).split())[:200]
            if test:
                hint = _("Vérifiez chez le fournisseur : client « Confidentiel », URL de redirection identique à celle "
                         "affichée par loco, et type d’autorisation « Authorization Code » permis.")
            raise OIDCError(_("%(label)s a refusé la connexion (%(error)s) : %(detail)s", label=label, error=error,
                              detail=detail) if detail and test else _("%(label)s a refusé la connexion (%(error)s).",
                                                                       label=label, error=error))
        meta = metadata(cfg)
        if request.args.get("iss", meta["issuer"]) != meta["issuer"]:     # RFC 9207
            raise OIDCError(_("Retour d’un autre émetteur que celui configuré."))
        code = request.args.get("code", "")
        if not code or len(code) > 4096:
            raise OIDCError(_("Le fournisseur n’a pas renvoyé de code d’autorisation."))
        claims = identity(cfg, meta, code, flow)
    except OIDCError as e:
        audit("sso.test" if test else "sso.echec", f"{label} : {e}" + (f" ({detail})" if detail and not test else ""),
              username="")
        if test:
            return _status(str(e), 400, testing=True, hint=hint)
        return _status(_("Connexion avec %(label)s impossible : %(e)s", label=label, e=e), 400)
    if test:
        groups = claims.get("groups") if isinstance(claims.get("groups"), list) else []
        audit("sso.test", f"{label} : {claims.get('preferred_username') or claims['sub']} (accepté)", username="")
        return _status(_("Connexion avec %(label)s réussie : loco reçoit bien l’identité ci-dessous. Aucun compte n’a été créé.",
                         label=label), kind="ok", test={
            "sub": claims["sub"], "username": _wanted_username(claims, cfg),
            "name": str(claims.get("name") or ""), "email": str(claims.get("email") or ""),
            "groups": ", ".join(str(x) for x in groups[:20])})
    user, notice = _link(directory, cfg, claims)
    if user is None:
        return _status(notice, 202, kind="ok")
    _complete_login(user, request.remote_addr or "", directory_id)
    # Page intermédiaire : la navigation suivante part de loco, le cookie de session (SameSite=Strict) est donc envoyé.
    return _status(_("Connexion réussie. Ouverture de loco…"), kind="ok", next_url=flow.get("next") or "/")


def start_test(directory):
    """Test depuis Annuaires et SSO : vraie connexion chez le fournisseur, identité affichée, aucun compte créé."""
    return begin(directory, "test", "/")
