"""Configuration automatique d'Authentik depuis Annuaires et SSO.

Avec un jeton API temporaire : loco crée ou met à jour le fournisseur et l'application, sans doublon, puis enregistre le client.
Le jeton n'est jamais stocké, ni journalisé, ni passé en argument de commande : entre deux étapes il reste chez le navigateur, chiffré
et limité à dix minutes. Il n'est envoyé qu'une fois le certificat d'Authentik reconnu, ou son autorité confirmée par l'empreinte.
Sans jeton : un blueprint prêt à coller et les valeurs à saisir."""
import base64
import datetime
import hashlib
import hmac
import json
import re
import secrets
import socket
import ssl
import urllib.parse

from flask import abort, flash, make_response, redirect, render_template, request, url_for

from . import app
from .auth import audit, lock_access, login_required, require
from .db import db
from .i18n import _
from .oidc import OIDCError, SCOPES, _json, _tls, callback_url, https_url, metadata
from .sso_check import FAIL, _common_name, _explain_certificate, _served_chain, _sha256, _step_dns, _step_tcp, chain_gap

TOKEN_TTL = 600
AUTH_FLOW, INVALIDATION_FLOW = "default-provider-authorization-implicit-consent", "default-provider-invalidation-flow"
DEFAULT_KEY = "authentik Self-signed Certificate"
GRANTS = ["authorization_code"]
WANTED_SCOPES = tuple(f"goauthentik.io/providers/oauth2/scope-{s}" for s in SCOPES.split())
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class SetupError(Exception):
    """Refus à afficher tel quel ; le message ne contient jamais le jeton."""
    def __init__(self, message="", status=None):
        super().__init__(message)
        self.status = status


def base_url(value):
    """https://hôte[:port] d'Authentik, sans chemin."""
    value = str(value or "").strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(https_url(value))
        parts.port
    except (OIDCError, ValueError):
        raise SetupError(_("Adresse d’Authentik attendue : https://authentik.exemple.lan:9443, sans chemin."))
    if parts.path or parts.query or len(value) > 255:
        raise SetupError(_("Adresse d’Authentik attendue : https://authentik.exemple.lan:9443, sans chemin."))
    return value


def clean_slug(value):
    value = str(value or "").strip().lower()
    if not SLUG_RE.fullmatch(value):
        raise SetupError(_("Identifiant de l’application (slug) : minuscules, chiffres, tirets et soulignés."))
    return value


def clean_label(value):
    value = str(value or "").strip() or "Authentik"
    if len(value) > 80 or any(ord(c) < 32 for c in value):
        raise SetupError(_('Nom de 1 à 80 caractères requis.'))
    return value


def clean_token(value):
    value = str(value or "").strip()
    if not value or len(value) > 4096 or re.search(r"\s", value) or any(ord(c) < 33 for c in value):
        raise SetupError(_("Jeton API requis : copiez-le depuis Authentik, sans espace."))
    return value


def _cipher():
    from cryptography.fernet import Fernet
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return Fernet(base64.urlsafe_b64encode(hmac.new(key, b"loco-authentik-setup-v1", hashlib.sha256).digest()))


def seal_token(token):
    return _cipher().encrypt(token.encode()).decode()


def open_token(sealed):
    from cryptography.fernet import InvalidToken
    try:
        return clean_token(_cipher().decrypt(str(sealed or "").encode(), ttl=TOKEN_TTL).decode())
    except (InvalidToken, ValueError):
        raise SetupError(_("Le jeton a expiré après dix minutes sans action : saisissez-le de nouveau."))


# --------------------------------------------------------------------------- certificat d'Authentik

def _pem(cert):
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def inspect(base, ca_pem=""):
    """Avant d'envoyer le jeton : le serveur est-il joignable, et par quelle autorité ? « status » : unreachable (résultat d'étape),
    trusted (reconnu, ca_pem éventuel à confirmer), confirm (autorité trouvée, à faire confirmer), refused (à corriger d'abord)."""
    parts = urllib.parse.urlsplit(base)
    host, port = parts.hostname, parts.port or 443
    for step in (_step_dns(host, port), _step_tcp(host, port)):
        if step["state"] == FAIL:
            return {"status": "unreachable", "step": step}
    from .system import _load_certs
    cfg = {"ca_pem": ca_pem}
    try:
        context, pin = _tls(cfg)
        with socket.create_connection((host, port), timeout=5) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except OIDCError as e:
        return {"status": "refused", "step": {"detail": str(e), "fix": ""}}
    except ssl.SSLCertVerificationError as e:
        if e.verify_code in (62, 64, 9, 10):                    # nom non couvert, certificat expiré : rien à confirmer
            return {"status": "refused", "step": _explain_certificate(e, cfg, host, port)}
        served = _served_chain(host, port)
        kind, cert = chain_gap(served, _load_certs(ca_pem.encode()) if ca_pem else []) if served else ("", None)
        if kind in ("self", "root"):
            return {"status": "confirm", "ca_pem": _pem(cert), "subject": _common_name(cert.subject), "sha": _sha256(cert), "self_signed": kind == "self"}
        return {"status": "refused", "step": _explain_certificate(e, cfg, host, port), "paste": True}
    except (ssl.SSLError, OSError) as e:
        return {"status": "refused", "step": {"detail": _("Pas de dialogue TLS avec %(host)s:%(port)s : %(e)s.", host=host, port=port, e=getattr(e, "reason", None) or e),
                                              "fix": _("Vérifiez que ce port sert bien du HTTPS : l’émetteur doit viser le port HTTPS du fournisseur.")}}
    if pin and not hmac.compare_digest(hashlib.sha256(der).digest(), pin):
        return {"status": "refused", "step": {"detail": _("Le certificat présenté n’est pas celui collé dans loco."), "fix": ""}, "paste": True}
    result = {"status": "trusted", "ca_pem": ca_pem}
    if ca_pem:
        certs = _load_certs(ca_pem.encode())
        result.update(subject=_common_name(certs[0].subject), sha=_sha256(certs[0]))
    return result


# --------------------------------------------------------------------------- API d'Authentik

class Api:
    def __init__(self, base, ca_pem, token):
        self.base, self.cfg, self.token = base, {"ca_pem": ca_pem, "issuer": base}, token

    def call(self, path, body=None, method=None):
        url = f"{self.base}/api/v3/{path}"
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        try:
            return _json(self.cfg, url, json.dumps(body).encode() if body is not None else None, headers, method=method)
        except OIDCError as e:
            status = e.info.get("status")
            if status in (401, 403):                         # Authentik répond 403 à un jeton inconnu ou expiré aussi
                raise SetupError(_("Jeton refusé par Authentik : vérifiez qu’il n’a pas expiré, qu’il est de type « API » et que son compte est administrateur."), status=status)
            detail = e.info.get("body")
            if status == 400 and isinstance(detail, dict):
                text = "; ".join(f"{k} : {v}" for k, v in list(detail.items())[:5])[:400]
                raise SetupError(_("Authentik a refusé la configuration : %(detail)s", detail=text.replace(self.token, "•••")))
            raise SetupError(str(e).replace(self.token, "•••"), status=status)

    def read_application(self, slug):
        """Lecture directe : la liste d'Authentik est filtrée selon les droits et un cache, et peut omettre une application existante."""
        try:
            return self.call(f"core/applications/{urllib.parse.quote(slug)}/")
        except SetupError as e:
            if e.status == 404:
                return None
            raise

    def one(self, path):
        found = self.call(path).get("results")
        return found[0] if found else None


def read_state(api, slug):
    """Ce qui existe déjà chez Authentik, et les objets nécessaires pour le reste."""
    api.call("core/users/me/")
    state = {"application": api.read_application(slug), "provider": None}
    linked = state["application"] and state["application"].get("provider")
    if linked:
        try:
            state["provider"] = _provider(api, linked)
        except SetupError as e:
            if e.status != 404:
                raise
            raise SetupError(_("L’application %(slug)s utilise un autre type de fournisseur : choisissez un autre identifiant.", slug=slug))
    else:
        found = api.one(f"providers/oauth2/?name={urllib.parse.quote(provider_name(slug))}")
        state["provider"] = _provider(api, found["pk"]) if found else None
    flow, invalidation = (api.one(f"flows/instances/?slug={s}") for s in (AUTH_FLOW, INVALIDATION_FLOW))
    if not flow or not invalidation:
        raise SetupError(_("Flux par défaut introuvables dans Authentik (%(a)s, %(b)s).", a=AUTH_FLOW, b=INVALIDATION_FLOW))
    keys = [k for k in api.call("crypto/certificatekeypairs/?has_key=true&page_size=100").get("results", []) if k.get("key_type") == "rsa"]
    key = next((k for k in keys if k.get("name") == DEFAULT_KEY), keys[0] if keys else None)
    if key is None:
        raise SetupError(_("Aucune clé RSA dans Authentik pour signer les jetons en RS256 : créez-en une dans Système, Certificats."))
    mappings = api.call("propertymappings/provider/scope/?page_size=200").get("results", [])
    scopes = {m.get("managed"): m["pk"] for m in mappings if m.get("managed") in WANTED_SCOPES}
    if len(scopes) != len(WANTED_SCOPES):
        raise SetupError(_("Portées par défaut introuvables dans Authentik (openid, profile, email)."))
    state.update(flow=flow["pk"], invalidation=invalidation["pk"], key=key, scopes=list(scopes.values()), rsa=[k["pk"] for k in keys])
    return state


def _provider(api, pk):
    return api.call(f"providers/oauth2/{int(pk)}/")


def provider_name(slug):
    return f"loco ({slug})"


def _uris(callback, others=()):
    return [{"matching_mode": "strict", "url": callback}, *others]


def plan(state, callback, slug):
    """Ce qui sera fait : {create, changes (texte), body (pour l'API), others (autres URL de retour, à garder sauf confirmation)}."""
    provider, application = state["provider"], state["application"]
    if provider is None:
        return {"create": True, "others": [], "changes": [
            _("Créer le fournisseur %(name)s : client confidentiel, flux authorization_code seul, signature RS256 (%(key)s).", name=provider_name(slug), key=state["key"]["name"]),
            _("URL de retour stricte : %(url)s", url=callback), _("Portées : %(scopes)s", scopes=SCOPES),
            _("Créer l’application %(slug)s.", slug=slug) if application is None else _("Lier l’application %(slug)s à ce fournisseur.", slug=slug)]}
    changes, body = [], {}
    if provider.get("client_type") != "confidential":
        changes.append(_("Type de client : %(before)s → confidentiel.", before=provider.get("client_type")))
        body["client_type"] = "confidential"
    if sorted(provider.get("grant_types") or []) != GRANTS:
        changes.append(_("Flux autorisés : %(before)s → authorization_code seul.", before=", ".join(provider.get("grant_types") or []) or "—"))
        body["grant_types"] = GRANTS
    if provider.get("signing_key") not in state["rsa"]:
        changes.append(_("Signature : clé %(key)s (RS256).", key=state["key"]["name"]))
        body["signing_key"] = state["key"]["pk"]
    if provider.get("issuer_mode") != "per_provider":
        changes.append(_("Émetteur propre à cette application."))
        body["issuer_mode"] = "per_provider"
    current = provider.get("property_mappings") or []
    missing = [s for s in state["scopes"] if s not in current]
    if missing:
        changes.append(_("Portées à ajouter : %(scopes)s.", scopes=SCOPES))
        body["property_mappings"] = current + missing
    uris = provider.get("redirect_uris") or []
    ours = [u for u in uris if u.get("url") == callback]
    others = [u for u in uris if u.get("url") != callback]
    if not ours:
        changes.append(_("URL de retour à ajouter : %(url)s", url=callback))
    elif ours[0].get("matching_mode") != "strict":
        changes.append(_("URL de retour en mode strict : %(url)s", url=callback))
    if not ours or ours[0].get("matching_mode") != "strict":
        body["redirect_uris"] = _uris(callback, others)
    if application is None:
        changes.append(_("Créer l’application %(slug)s.", slug=slug))
    elif not application.get("provider"):
        changes.append(_("Lier l’application %(slug)s à ce fournisseur.", slug=slug))
    return {"create": False, "changes": changes, "body": body, "others": others}


def signature(steps, others):
    """Empreinte de ce qui a été montré : si Authentik a changé entre-temps, rien n'est appliqué à l'aveugle."""
    return hashlib.sha256(json.dumps([[str(c) for c in steps], others], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_plan(api, state, wanted, callback, slug, label, remove):
    """Applique le plan montré, sans doublon, puis rend le fournisseur à jour (avec client_id et secret)."""
    provider = state["provider"]
    if provider is None:
        provider = api.call("providers/oauth2/", {
            "name": provider_name(slug), "authorization_flow": state["flow"], "invalidation_flow": state["invalidation"],
            "client_type": "confidential", "grant_types": GRANTS, "redirect_uris": _uris(callback), "signing_key": state["key"]["pk"],
            "property_mappings": state["scopes"], "issuer_mode": "per_provider"})
    else:
        body = dict(wanted["body"])
        kept = [u for u in wanted["others"] if u["url"] not in remove]
        if len(kept) != len(wanted["others"]):
            body["redirect_uris"] = _uris(callback, kept)
        if body:
            provider = api.call(f"providers/oauth2/{int(provider['pk'])}/", body, method="PATCH")
    if state["application"] is None:
        api.call("core/applications/", {"name": label, "slug": slug, "provider": provider["pk"]})
    elif not state["application"].get("provider"):
        api.call(f"core/applications/{slug}/", {"provider": provider["pk"]}, method="PATCH")
    return _provider(api, provider["pk"])


def save_directory(ident, label, issuer, client_id, client_secret, ca_pem, enabled):
    """Crée ou met à jour le fournisseur de loco, jamais en double pour un même émetteur."""
    from .directories import _revoke, _seal
    conn = lock_access("sso.manage")
    row = conn.execute("SELECT * FROM auth_directories WHERE id=?", (ident,)).fetchone()
    old = json.loads(row["config"]) if row else {}
    cfg = {"preset": "authentik", "issuer": issuer, "client_id": client_id, "scopes": old.get("scopes") or SCOPES,
           "username_claim": old.get("username_claim") or "preferred_username", "ca_pem": ca_pem, "client_secret": _seal(client_secret)}
    if row:
        _revoke(ident)
        conn.execute("UPDATE auth_directories SET config=?, enabled=?, revision=revision+1 WHERE id=?", (json.dumps(cfg), enabled, ident))
    else:
        position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM auth_directories").fetchone()[0]
        conn.execute("INSERT INTO auth_directories (id, kind, label, config, enabled, position, created_at) VALUES (?,?,?,?,?,?,?)",
                     (ident, "oidc", label, json.dumps(cfg), enabled, position, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    audit("sso.authentik", f"{label} : {'mise à jour' if row else 'création'} ({issuer})", commit=False)
    conn.commit()


def directory_for(issuer, proposed=""):
    """Identifiant du fournisseur de loco : celui qui a déjà cet émetteur, sinon celui proposé à l'étape précédente, pour que
    l'URL de retour montrée ne change pas d'une étape à l'autre."""
    known = db().execute("SELECT id, kind, config FROM auth_directories").fetchall()
    for row in known:
        if row["kind"] == "oidc" and json.loads(row["config"]).get("issuer", "").rstrip("/") == issuer.rstrip("/"):
            return row["id"]
    if re.fullmatch(r"[0-9a-f]{16}", str(proposed)) and proposed not in {row["id"] for row in known}:
        return proposed
    return secrets.token_hex(8)


# --------------------------------------------------------------------------- blueprint

def blueprint(slug, label, callback, client_id):
    """Blueprint Authentik prêt à coller. Le secret reste généré par Authentik : à recopier ensuite. Valeurs en JSON, valide en YAML."""
    name = provider_name(slug)
    return f"""version: 1
metadata:
  name: {json.dumps(name)}
entries:
  - model: authentik_providers_oauth2.oauth2provider
    id: loco-provider
    identifiers:
      name: {json.dumps(name)}
    attrs:
      authorization_flow: !Find [authentik_flows.flow, [slug, {AUTH_FLOW}]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, {INVALIDATION_FLOW}]]
      client_type: confidential
      client_id: {json.dumps(client_id)}
      grant_types: [authorization_code]
      issuer_mode: per_provider
      redirect_uris:
        - matching_mode: strict
          url: {json.dumps(callback)}
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, {json.dumps(DEFAULT_KEY)}]]
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {WANTED_SCOPES[0]}]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {WANTED_SCOPES[1]}]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {WANTED_SCOPES[2]}]]
  - model: authentik_core.application
    identifiers:
      slug: {json.dumps(slug)}
    attrs:
      name: {json.dumps(label)}
      provider: !KeyOf loco-provider
"""


# --------------------------------------------------------------------------- page

def _page(**values):
    response = make_response(render_template("authentik_setup.html", **values))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/accounts/directories/authentik", methods=["GET", "POST"])
@login_required
def directory_authentik():
    require("sso.manage")                   # pas de verrou de la base pendant les échanges réseau avec Authentik
    f = request.form
    values = {"url": f.get("url", ""), "slug": f.get("slug", "loco"), "label": f.get("label", "Authentik"), "ca_pem": f.get("ca_pem", "").strip()[:20000], "new_id": ""}
    if request.method == "GET":
        return _page(stage="start", **values)
    stage = f.get("stage", "start")
    if stage not in ("inspect", "blueprint", "plan", "apply"):
        abort(400)
    try:
        base, slug, label = base_url(values["url"]), clean_slug(values["slug"]), clean_label(values["label"])
    except SetupError as e:
        return _page(stage="start", error=str(e), **values), 400
    values.update(url=base, slug=slug, label=label)
    issuer = f"{base}/application/o/{slug}/"
    ident = directory_for(issuer, f.get("new_id", ""))
    values["new_id"] = ident
    callback = callback_url(ident)
    if values["ca_pem"] and "BEGIN CERTIFICATE" not in values["ca_pem"]:
        return _page(stage="start", error=_('Autorité : certificat PEM attendu.'), **values), 400
    if stage == "blueprint":
        client_id = "loco-" + secrets.token_hex(8)
        return _page(stage="blueprint", yaml=blueprint(slug, label, callback, client_id), issuer=issuer, client_id=client_id, callback=callback, **values)

    tls = inspect(base, values["ca_pem"])
    if stage == "inspect" or tls["status"] != "trusted":
        return _page(stage="token", tls=tls, callback=callback, **{**values, "ca_pem": tls.get("ca_pem", values["ca_pem"])})
    # Le jeton ne part qu'ici : certificat reconnu, et autorité collée ou trouvée confirmée par l'empreinte.
    if values["ca_pem"] and f.get("confirmed") != tls.get("sha"):
        return _page(stage="token", tls=tls, callback=callback, error=_("Confirmez l’empreinte de l’autorité avant de continuer."), **values), 400
    try:
        token = clean_token(f.get("token")) if stage == "plan" else open_token(f.get("sealed"))
    except SetupError as e:
        return _page(stage="token", tls=tls, callback=callback, error=str(e), **values), 400
    context = {"tls": tls, "callback": callback, **values}
    api = Api(base, values["ca_pem"], token)
    try:
        state = read_state(api, slug)
        wanted = plan(state, callback, slug)
    except SetupError as e:
        return _page(stage="token", error=str(e), **context), 400
    shown = signature(wanted["changes"], wanted["others"])
    if stage == "plan":
        return _page(stage="plan", changes=wanted["changes"], others=wanted["others"], create=wanted["create"], signature=shown, sealed=seal_token(token), confirmed=f.get("confirmed", ""), **context)
    if f.get("signature") != shown:
        return _page(stage="plan", error=_("Authentik a changé depuis votre dernière lecture : vérifiez de nouveau ce qui sera fait."), changes=wanted["changes"], others=wanted["others"],
                     create=wanted["create"], signature=shown, sealed=seal_token(token), confirmed=f.get("confirmed", ""), **context), 409
    remove = {u for u in f.getlist("remove_uri") if u in {o["url"] for o in wanted["others"]}}
    try:
        provider = apply_plan(api, state, wanted, callback, slug, label, remove)
        client_id, secret = provider.get("client_id"), provider.get("client_secret")
        if not client_id or not secret:
            raise SetupError(_("Authentik n’a pas renvoyé l’identifiant et le secret du client."))
        metadata({"issuer": issuer, "ca_pem": values["ca_pem"]}, fresh=True)
    except (SetupError, OIDCError) as e:
        return _page(stage="token", error=str(e).replace(token, "•••"), **context), 502
    save_directory(ident, label, issuer, client_id, secret, values["ca_pem"], 1 if f.get("enabled") == "1" else 0)
    flash(_("Authentik est configuré. Supprimez maintenant le jeton API dans Authentik : loco ne l’a pas gardé."), "ok")
    return redirect(url_for("sso_settings", verifier=ident))

