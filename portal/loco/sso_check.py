"""« Vérifier la configuration » d'un fournisseur OpenID Connect, depuis ce serveur : chaque étape du dialogue, avec son état
et sa correction, sans connexion d'un utilisateur ni création de compte. Aucun secret n'apparaît dans le résultat."""
import concurrent.futures
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from . import app, config
from .i18n import L_, _, n_
from .oidc import (ALGORITHMS, LIMIT, OIDCError, _PinnedHTTPS, _candidates, _client_auth, _json, _public_key, _s256, _tls,
                   authorize_url, callback_url, https_url, metadata, public_keys)

OK, FAIL, UNSURE, SKIPPED = "ok", "fail", "unsure", "skipped"
NET_TIMEOUT = 5
STEPS = (("dns", L_("Résolution du nom")), ("tcp", L_("Connexion au serveur")), ("tls", L_("Certificat")),
         ("discovery", L_("Découverte OpenID Connect")), ("keys", L_("Clés de signature")),
         ("client", L_("Identifiant et secret client")), ("authorize", L_("Demande de connexion")))
BLOCKING = ("dns", "tcp", "tls", "discovery")       # les suivantes ne pourraient rien conclure
PASTE_FIELD = L_("« Certificat ou autorité du fournisseur » (Modifier, options avancées)")


def _done(state, detail, fix="", copy=""):
    return {"state": state, "detail": detail, "fix": fix, "value": copy}


def _proxy_for(url):
    """Proxy par lequel partent les appels vers ce fournisseur, sans ses identifiants ; vide s'ils partent en direct."""
    parts = urllib.parse.urlsplit(url)
    proxy = urllib.request.getproxies().get("https")
    if not proxy or urllib.request.proxy_bypass(parts.hostname):
        return ""
    return urllib.parse.urlsplit(proxy).hostname or "proxy"


# --------------------------------------------------------------------------- réseau

def _resolvers():
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as f:
            return re.findall(r"^\s*nameserver\s+(\S+)", f.read(), re.M)
    except OSError:
        return []


def _dns_fix(host):
    from .system import internal_hosts_hint
    servers = _resolvers()
    hint = internal_hosts_hint()
    if hint:
        return hint
    if servers:
        return _("Déclarez %(host)s dans le DNS utilisé par ce serveur (%(servers)s), ou utilisez dans l’émetteur un nom qu’il résout.",
                 host=host, servers=", ".join(servers[:3]))
    return _("Déclarez %(host)s dans le DNS utilisé par ce serveur, ou utilisez dans l’émetteur un nom qu’il résout.", host=host)


def _step_dns(host, port):
    try:
        ipaddress.ip_address(host)
        return _done(OK, _("%(host)s est une adresse IP : rien à résoudre.", host=host))
    except ValueError:
        pass
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        found = pool.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM).result(NET_TIMEOUT)
    except socket.gaierror as e:
        return dict(_done(FAIL, _("Ce serveur ne résout pas %(host)s : %(e)s.", host=host, e=e.strerror or e), _dns_fix(host)), host=host)
    except concurrent.futures.TimeoutError:
        return dict(_done(FAIL, _("Le DNS de ce serveur ne répond pas pour %(host)s.", host=host), _dns_fix(host)), host=host)
    finally:
        pool.shutdown(wait=False)
    return _done(OK, _("%(host)s : %(addresses)s.", host=host, addresses=", ".join(dict.fromkeys(i[4][0] for i in found))))


def _step_tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=NET_TIMEOUT) as sock:
            return _done(OK, _("Port %(port)s joint sur %(address)s.", port=port, address=sock.getpeername()[0]))
    except ConnectionRefusedError:
        return _done(FAIL, _("Connexion refusée sur le port %(port)s de %(host)s.", port=port, host=host),
                     _("Vérifiez le port indiqué dans l’émetteur et que le fournisseur écoute bien sur celui-ci."))
    except TimeoutError:
        return _done(FAIL, _("Pas de réponse du port %(port)s de %(host)s en %(seconds)s s.", port=port, host=host, seconds=NET_TIMEOUT),
                     _("Un pare-feu bloque sans doute ce port : ouvrez-le depuis ce serveur vers le fournisseur."))
    except OSError as e:
        return _done(FAIL, _("Connexion impossible à %(host)s : %(e)s.", host=host, e=e.strerror or e),
                     _("Vérifiez la route et le pare-feu entre ce serveur et le fournisseur."))


# --------------------------------------------------------------------------- certificat

def _load_der(der):
    from cryptography import x509
    return x509.load_der_x509_certificate(der)


def _der(cert):
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.DER)


def _common_name(name):
    from cryptography.x509.oid import NameOID
    found = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(found[0].value) if found else name.rfc4514_string()


def _sha256(cert):
    digest = hashlib.sha256(_der(cert)).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _not_after(cert):
    return (getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after).strftime("%Y-%m-%d")


def _not_before(cert):
    return (getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before).strftime("%Y-%m-%d")


def _san(cert):
    from cryptography import x509
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return [*san.get_values_for_type(x509.DNSName), *(str(a) for a in san.get_values_for_type(x509.IPAddress))]


def _served_chain(host, port):
    """Certificats envoyés par le serveur, feuille d'abord, lus sans les vérifier pour expliquer un refus."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=NET_TIMEOUT) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            # Public depuis Python 3.13 (des octets DER) ; avant, sur l'objet interne (des objets Certificate).
            getter = getattr(tls, "get_unverified_chain", None) or getattr(tls._sslobj, "get_unverified_chain", None)
            items = getter() if getter else []
            ders = [c if isinstance(c, bytes) else c.public_bytes(ssl._ssl.ENCODING_DER) for c in items]
            ders = ders or [tls.getpeercert(binary_form=True)]
    except (OSError, AttributeError, ValueError):
        return []
    try:
        return [_load_der(d) for d in ders if d]
    except ValueError:
        return []


def chain_gap(served, pasted):
    """Où s'arrête la chaîne d'approbation. (« self », certificat) auto-signé non approuvé ; (« root », certificat) racine envoyée
    mais non approuvée ; (« signer », certificat) l'autorité qui a signé la feuille manque ; (« top », certificat) un
    intermédiaire est envoyé mais sa racine manque ; (« », None) rien à signaler."""
    pool, current, seen = served + pasted, served[0], []
    while current not in seen:
        seen.append(current)
        if current.issuer == current.subject:
            if current in pasted:
                return "", None
            return ("self" if current is served[0] else "root"), current
        following = next((c for c in pool if c.subject == current.issuer), None)
        if following is None:
            return ("signer" if current is served[0] else "top"), current
        current = following
    return "", None


def _explain_certificate(error, cfg, host, port):
    from .system import _load_certs
    served = _served_chain(host, port)
    pasted = _load_certs((cfg.get("ca_pem") or "").encode()) if cfg.get("ca_pem") else []
    code, field = error.verify_code, str(PASTE_FIELD)
    leaf = served[0] if served else None
    if code in (62, 64) and leaf is not None:                   # X509_V_ERR_HOSTNAME_MISMATCH, IP_ADDRESS_MISMATCH
        return _done(FAIL, _("Le certificat couvre %(names)s, mais pas %(host)s.", names=", ".join(_san(leaf)[:8]) or "—", host=host),
                     _("Refaites le certificat avec ce nom, ou utilisez dans l’émetteur un nom qu’il couvre."))
    if code == 10 and served:                                   # X509_V_ERR_CERT_HAS_EXPIRED
        return _done(FAIL, _("Le certificat %(cn)s a expiré le %(date)s.", cn=_common_name(leaf.subject), date=_not_after(leaf)),
                     _("Renouvelez-le chez le fournisseur."))
    if code == 9 and served:                                    # X509_V_ERR_CERT_NOT_YET_VALID
        return _done(FAIL, _("Le certificat %(cn)s n’est valable qu’à partir du %(date)s.", cn=_common_name(leaf.subject), date=_not_before(leaf)),
                     _("Vérifiez l’heure de ce serveur et celle du fournisseur."))
    kind, cert = chain_gap(served, pasted) if served else ("", None)
    if kind == "self":
        return _done(FAIL, _("Le certificat %(cn)s est auto-signé et ce serveur ne l’approuve pas.", cn=_common_name(cert.subject)),
                     _("Vérifiez son empreinte (%(sha)s), puis collez-le dans %(field)s : loco l’épinglera.", sha=_sha256(cert), field=field))
    if kind == "root":
        return _done(FAIL, _("Racine non approuvée : le fournisseur envoie %(cn)s, que ce serveur ne connaît pas.", cn=_common_name(cert.subject)),
                     _("Vérifiez son empreinte (%(sha)s), puis collez cette racine dans %(field)s.", sha=_sha256(cert), field=field))
    if kind == "top":
        return _done(FAIL, _("Racine manquante : l’intermédiaire %(cn)s est signé par %(issuer)s, que ce serveur ne connaît pas.",
                             cn=_common_name(cert.subject), issuer=_common_name(cert.issuer)),
                     _("Collez la racine %(issuer)s dans %(field)s.", issuer=_common_name(cert.issuer), field=field))
    if kind == "signer" and pasted:
        names = ", ".join(_common_name(c.subject) for c in pasted[:3])
        return _done(FAIL, _("Intermédiaire manquant ou mauvaise autorité : le certificat %(cn)s est signé par %(issuer)s, qui n’est pas l’autorité collée (%(names)s).",
                             cn=_common_name(cert.subject), issuer=_common_name(cert.issuer), names=names),
                     _("Si %(issuer)s est un intermédiaire, faites envoyer la chaîne complète par le fournisseur, ou collez-le avec sa racine. Sinon, collez la bonne autorité.",
                       issuer=_common_name(cert.issuer)))
    if kind == "signer":
        return _done(FAIL, _("Autorité manquante : le certificat %(cn)s est signé par %(issuer)s, que le fournisseur n’envoie pas et que ce serveur ne connaît pas.",
                             cn=_common_name(cert.subject), issuer=_common_name(cert.issuer)),
                     _("Collez cette autorité et sa racine dans %(field)s. Si %(issuer)s est un intermédiaire, faites aussi envoyer la chaîne complète par le fournisseur.",
                       field=field, issuer=_common_name(cert.issuer)))
    return _done(FAIL, _("Certificat refusé : %(e)s.", e=error.verify_message or error),
                 _("Collez l’autorité du fournisseur dans %(field)s.", field=field))


def _step_tls(cfg, host, port):
    try:
        context, pin = _tls(cfg)
    except OIDCError as e:
        return _done(FAIL, str(e), _("Recollez le certificat au format PEM dans %(field)s.", field=str(PASTE_FIELD)))
    try:
        with socket.create_connection((host, port), timeout=NET_TIMEOUT) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    except ssl.SSLCertVerificationError as e:
        return _explain_certificate(e, cfg, host, port)
    except ssl.SSLError as e:
        return _done(FAIL, _("Pas de dialogue TLS avec %(host)s:%(port)s : %(e)s.", host=host, port=port, e=e.reason or e),
                     _("Vérifiez que ce port sert bien du HTTPS : l’émetteur doit viser le port HTTPS du fournisseur."))
    except OSError as e:
        return _done(FAIL, _("Connexion TLS interrompue : %(e)s.", e=e.strerror or e), _("Vérifiez que ce port sert bien du HTTPS."))
    cert = _load_der(der)
    if pin:
        if not hmac.compare_digest(hashlib.sha256(der).digest(), pin):
            return _done(FAIL, _("Le certificat présenté n’est pas celui collé dans loco."),
                         _("S’il a été renouvelé, vérifiez son empreinte (%(sha)s), puis collez-le dans %(field)s.", sha=_sha256(cert), field=str(PASTE_FIELD)))
        return _done(OK, _("Certificat épinglé identique, valable jusqu’au %(date)s.", date=_not_after(cert)))
    return _done(OK, _("Certificat %(cn)s, chaîne et nom vérifiés, valable jusqu’au %(date)s.", cn=_common_name(cert.subject), date=_not_after(cert)))


# --------------------------------------------------------------------------- découverte et clés

def _step_discovery(cfg):
    """(résultat, document de découverte ou None)."""
    try:
        meta = metadata(cfg, fresh=True)
    except OIDCError as e:
        kind, status = e.info.get("kind"), e.info.get("status")
        if kind == "issuer":
            fix = _("L’émetteur configuré doit être identique à celui annoncé. Authentik le construit avec l’hôte et le port demandés : "
                    "indiquez l’adresse par laquelle ce serveur le joint, ou recréez le fournisseur avec l’émetteur annoncé.")
        elif kind == "pkce":
            fix = _("Activez PKCE (S256) chez le fournisseur : loco l’exige.")
        elif status == 404:
            fix = _("Aucune application à cette adresse : vérifiez l’émetteur. Pour Authentik : https://authentik…/application/o/<slug>/, avec le slug de l’application.")
        else:
            fix = _("Vérifiez l’émetteur : copiez-le depuis la page du fournisseur.")
        return _done(FAIL, str(e), fix), None
    methods = meta.get("code_challenge_methods_supported")
    if not isinstance(methods, list):
        return _done(UNSURE, _("Émetteur identique, mais le fournisseur n’indique pas s’il gère PKCE (S256), que loco exige."),
                     _("Faites un essai avec Tester : la connexion échouera si PKCE n’est pas géré.")), meta
    return _done(OK, _("Émetteur identique (%(issuer)s), PKCE S256 géré.", issuer=meta["issuer"])), meta


def _step_keys(cfg, meta):
    try:
        keys = public_keys(cfg, meta, fresh=True)
    except OIDCError as e:
        return _done(FAIL, str(e), _("Vérifiez que l’adresse jwks_uri du fournisseur répond depuis ce serveur."))
    advertised = meta.get("id_token_signing_alg_values_supported")
    advertised = [a for a in advertised if a in ALGORITHMS] if isinstance(advertised, list) else []
    if not keys:
        if any(a.startswith("HS") for a in advertised):
            return _done(OK, _("Aucune clé publique : le fournisseur signe avec le secret client (HS256). Une clé de signature RS256 reste préférable."))
        return _done(FAIL, _("Le fournisseur ne publie aucune clé de signature."), _("Choisissez une clé de signature dans le fournisseur."))
    usable = []
    for alg in advertised or [a for a in ALGORITHMS if not a.startswith("HS")]:
        for jwk in _candidates(keys, alg, None):
            try:
                _public_key(jwk, alg)
                usable.append(alg)
            except (OIDCError, KeyError, ValueError, TypeError):
                continue
    if not usable:
        return _done(FAIL, n_("La clé publiée n’est pas utilisable par loco.", "Aucune des %(num)s clés publiées n’est utilisable par loco.", len(keys)),
                     _("Choisissez une clé de signature RSA d’au moins 2048 bits, ou une clé elliptique."))
    return _done(OK, n_("%(num)s clé publiée, algorithmes : %(algs)s.", "%(num)s clés publiées, algorithmes : %(algs)s.", len(keys), algs=", ".join(dict.fromkeys(usable))))


# --------------------------------------------------------------------------- client et demande de connexion

def _step_client(cfg, meta, callback):
    """Un faux code : le fournisseur authentifie d'abord le client, puis refuse le code. Aucun jeton n'est obtenu."""
    params = {"grant_type": "authorization_code", "code": secrets.token_urlsafe(32), "redirect_uri": callback,
              "code_verifier": secrets.token_urlsafe(48)}
    try:
        headers = _client_auth(cfg, meta, params)
        _json(cfg, meta["token_endpoint"], urllib.parse.urlencode(params).encode(), headers)
    except OIDCError as e:
        error, status = e.info.get("oauth", ""), e.info.get("status")
        if error == "invalid_grant":
            return _done(OK, _("Client reconnu : le fournisseur a refusé le faux code envoyé pour ce contrôle, comme prévu."))
        if error == "invalid_client":
            return _done(FAIL, _("Identifiant ou secret client refusé par le fournisseur."),
                         _("Recopiez le Client ID et le Client Secret depuis la page du fournisseur (Modifier), et vérifiez que le client est de type « Confidentiel »."))
        if error in ("unauthorized_client", "unsupported_grant_type"):
            return _done(FAIL, _("Ce client n’a pas le droit d’utiliser le flux « code » (%(error)s).", error=error),
                         _("Autorisez le type « Authorization Code » dans le fournisseur."))
        if status:
            return _done(UNSURE, _("Réponse inattendue du fournisseur : HTTP %(status)s %(error)s.", status=status, error=error),
                         _("Faites un essai avec Tester pour confirmer l’identifiant et le secret."))
        return _done(FAIL, str(e), _("Vérifiez que l’adresse token_endpoint du fournisseur répond depuis ce serveur."))
    return _done(UNSURE, _("Le fournisseur a accepté le faux code, ce qui n’est pas normal."), _("Faites un essai avec Tester."))


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch_once(cfg, url):
    """(statut, Location absolue, début du corps), sans suivre les redirections ni garder de cookie."""
    request = urllib.request.Request(https_url(url), headers={"Accept": "text/html,application/json", "User-Agent": config.USER_AGENT})
    context, pin = _tls(cfg)
    opener = urllib.request.build_opener(_Stay(), _PinnedHTTPS(context, pin) if pin else urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, urllib.parse.urljoin(url, response.headers.get("Location", "")), response.read(LIMIT).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        with e:
            location = e.headers.get("Location", "")
            return e.code, urllib.parse.urljoin(url, location) if location else "", e.read(LIMIT).decode("utf-8", "replace")


def read_authorize(authentik, status, location, body, callback):
    """Sens de la réponse à la demande de connexion. Authentik 2026.8 : 302 vers /if/flow/… = accepté ; 400 « Redirect URI Error » ;
    302 vers le retour avec invalid_request = flux « code » non autorisé. Inconnu : non concluant, jamais un échec."""
    target = urllib.parse.urlsplit(location)
    declare = _("Déclarez cette URL de retour, en mode strict, chez le fournisseur :")
    if location and location.split("?")[0] == callback:
        query = dict(urllib.parse.parse_qsl(target.query))
        error = re.sub(r"[^a-z_]", "", query.get("error", ""))[:40]
        if error == "invalid_request" and authentik:
            return _done(FAIL, _("Authentik refuse le flux « code » pour ce client (invalid_request, « The request is otherwise malformed »)."),
                         _("Dans le fournisseur Authentik, autorisez le type « Authorization Code » (Paramètres avancés du flux, ou champ grant_types)."))
        if error in ("unauthorized_client", "unsupported_response_type"):
            return _done(FAIL, _("Le fournisseur n’autorise pas le flux « code » pour ce client (%(error)s).", error=error),
                         _("Autorisez le type « Authorization Code » dans le fournisseur."))
        if error == "invalid_scope":
            return _done(FAIL, _("Le fournisseur refuse les portées demandées (invalid_scope)."),
                         _("Corrigez les portées dans Modifier, options avancées, ou activez-les chez le fournisseur."))
        if error:
            detail = " ".join(re.sub(r"[^\w .,:;'’()/-]", "", query.get("error_description", "")).split())[:200]
            return _done(UNSURE, _("Le fournisseur renvoie l’erreur %(error)s : %(detail)s", error=error, detail=detail or "—"),
                         _("Faites un essai avec Tester pour voir si la connexion aboutit."))
    if authentik:
        if status in (301, 302, 303, 307) and target.path.startswith("/if/flow/"):
            return _done(OK, _("Authentik accepte la demande : URL de retour déclarée et flux « code » autorisé."))
        if status == 400 and "Redirect URI Error" in body:
            return dict(_done(FAIL, _("Authentik refuse cette URL de retour (« Redirect URI Error ») : elle n’est pas déclarée."), declare, callback),
                        cause="redirect")
        if status == 400 and "Client ID Error" in body:
            return _done(FAIL, _("Authentik ne connaît pas cet identifiant client (« Client ID Error »)."),
                         _("Recopiez le Client ID depuis la page du fournisseur (Modifier)."))
    elif status in (200, 301, 302, 303, 307):
        return _done(UNSURE, _("Le fournisseur n’a signalé aucune erreur (HTTP %(status)s), mais il peut vérifier la suite après la connexion.", status=status),
                     _("Faites un essai avec Tester pour confirmer. URL de retour à déclarer :"), callback)
    return _done(UNSURE, _("Réponse non reconnue du fournisseur : HTTP %(status)s.", status=status),
                 _("Faites un essai avec Tester pour voir si la connexion aboutit. URL de retour à déclarer :"), callback)


def _step_authorize(cfg, meta, callback):
    url = authorize_url(cfg, meta, callback, secrets.token_urlsafe(32), secrets.token_urlsafe(32), _s256(secrets.token_urlsafe(48)))
    try:
        status, location, body = _fetch_once(cfg, url)
    except (urllib.error.URLError, OSError, OIDCError) as e:
        return _done(FAIL, _("Adresse de connexion du fournisseur injoignable : %(e)s", e=str(getattr(e, "reason", e))[:200]),
                     _("Vérifiez que l’adresse authorization_endpoint répond depuis ce serveur."))
    return read_authorize(cfg.get("preset") == "authentik", status, location, body, callback)


# --------------------------------------------------------------------------- enchaînement

def run(directory):
    """Toutes les étapes, dans l'ordre. Une étape réseau en échec arrête les suivantes, qui sont marquées « non effectuée »."""
    from .directories import config_of
    cfg = config_of(directory)
    callback = callback_url(directory["id"])
    parts = urllib.parse.urlsplit(cfg["issuer"])
    host, port = parts.hostname, parts.port or 443
    proxy, meta, results = _proxy_for(cfg["issuer"]), None, {}
    actions = {"dns": lambda: _step_dns(host, port), "tcp": lambda: _step_tcp(host, port), "tls": lambda: _step_tls(cfg, host, port),
               "keys": lambda: _step_keys(cfg, meta), "client": lambda: _step_client(cfg, meta, callback),
               "authorize": lambda: _step_authorize(cfg, meta, callback)}
    blocked = False
    for key, _title in STEPS:
        if blocked:
            results[key] = _done(SKIPPED, _("Non effectuée : corrigez d’abord l’étape en échec."))
        elif proxy and key in ("dns", "tcp", "tls"):
            results[key] = _done(SKIPPED, _("Les appels passent par le proxy %(proxy)s : il s’en charge.", proxy=proxy))
        else:
            try:
                if key == "discovery":
                    results[key], meta = _step_discovery(cfg)
                else:
                    results[key] = actions[key]()
            except Exception as e:  # noqa: BLE001 - un diagnostic rend toujours un résultat ; le détail reste dans le journal du portail
                app.logger.exception("sso check %s", key)
                results[key] = _done(FAIL if key in BLOCKING else UNSURE, _("Étape interrompue (%(type)s).", type=type(e).__name__))
            blocked = key in BLOCKING and results[key]["state"] == FAIL
    if results["authorize"].get("cause") == "redirect" and results["client"]["state"] == FAIL:
        # Authentik répond aussi invalid_client quand l'URL de retour envoyée n'est pas déclarée : rien à conclure sur le secret.
        results["client"] = _done(UNSURE, _("Refusé, mais Authentik répond ainsi aussi quand l’URL de retour n’est pas déclarée : le secret n’est pas forcément en cause."),
                                  _("Déclarez d’abord l’URL de retour, puis relancez la vérification."))
    # Un fournisseur qui recopierait le secret dans un message d'erreur ne le ferait pas afficher pour autant.
    secret = cfg.get("client_secret") or ""
    for result in results.values():
        for field in ("detail", "fix"):
            if secret:
                result[field] = str(result[field]).replace(secret, "•••")
    return [dict(results[key], key=key, title=str(title)) for key, title in STEPS]


def tally(steps):
    return {state: sum(1 for s in steps if s["state"] == state) for state in (OK, FAIL, UNSURE, SKIPPED)}

