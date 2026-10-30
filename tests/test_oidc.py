"""Connexion OpenID Connect (Authentik…) : faux fournisseur qui signe de vrais jetons (RS256, ES256, HS256), flux complet,
refus des jetons altérés, test sans création de compte, épinglage du certificat auto-signé d'Authentik."""
import base64
import datetime
import hashlib
import hmac
import html
import http.server
import json
import os
import re
import ssl
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

import test_portal as fixture
from test_fleet import FleetTest
from test_directories import certificate
from loco import oidc
from loco.db import transaction

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

ISSUER = "https://ak.test/application/o/loco/"
CLIENT, SECRET = "loco-client-id", "client-secret-value-0123456789"
DIRECTORY = "0123456789abcdef"


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def big(n):
    return b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class FakeIdP:
    """Points d'accès d'un fournisseur (découverte, clés, jeton, profil) servis à la place des appels HTTPS."""
    def __init__(self):
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.alg, self.nonce, self.mutate, self.userinfo, self.calls = "RS256", None, None, None, []

    def jwks(self):
        pub, point = self.rsa.public_key().public_numbers(), self.ec.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "rsa1", "use": "sig", "alg": "RS256", "n": big(pub.n), "e": big(pub.e)},
                         {"kty": "EC", "kid": "ec1", "use": "sig", "alg": "ES256", "crv": "P-256",
                          "x": b64u(point.x.to_bytes(32, "big")), "y": b64u(point.y.to_bytes(32, "big"))}]}

    def sign(self, claims, alg=None, kid=None, key=None, secret=SECRET):
        alg = alg or self.alg
        header = {"alg": alg, "typ": "JWT", **({"kid": kid or ("ec1" if alg == "ES256" else "rsa1")} if alg[:2] != "HS" else {})}
        data = (b64u(json.dumps(header).encode()) + "." + b64u(json.dumps(claims).encode())).encode()
        if alg == "RS256":
            sig = (key or self.rsa).sign(data, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            r, s = decode_dss_signature((key or self.ec).sign(data, ec.ECDSA(hashes.SHA256())))
            sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        elif alg == "HS256":
            sig = hmac.new(secret.encode(), data, hashlib.sha256).digest()
        else:
            sig = b""
        return data.decode() + "." + b64u(sig)

    def claims(self):
        now = int(time.time())
        return {"iss": ISSUER, "aud": CLIENT, "sub": "a1b2c3d4e5f6", "iat": now, "exp": now + 300, "nonce": self.nonce,
                "preferred_username": "Jean.Dupont", "name": "Jean Dupont", "email": "jean@entreprise.test",
                "groups": ["loco-admins", "staff"]}

    def __call__(self, cfg, url, data=None, headers=None):
        self.calls.append(url)
        base = "https://ak.test/application/o"
        if url == ISSUER.rstrip("/") + "/.well-known/openid-configuration":
            return {"issuer": ISSUER, "authorization_endpoint": base + "/authorize/", "token_endpoint": base + "/token/",
                    "userinfo_endpoint": base + "/userinfo/", "jwks_uri": ISSUER + "jwks/",
                    "code_challenge_methods_supported": ["plain", "S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]}
        if url == ISSUER + "jwks/":
            return self.jwks()
        if url == base + "/token/":
            form = urllib.parse.parse_qs(data.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
            assert headers["Authorization"] == "Basic " + base64.b64encode(f"{CLIENT}:{SECRET}".encode()).decode()
            self.verifier = form["code_verifier"][0]
            claims = self.claims()
            if self.mutate:
                token = self.mutate(self, claims)
            else:
                token = self.sign(claims)
            return {"id_token": token, "access_token": "access-token", "token_type": "Bearer"}
        if url == base + "/userinfo/":
            assert headers["Authorization"] == "Bearer access-token"
            return self.userinfo
        raise AssertionError(url)


class OidcTest(FleetTest):
    def setUp(self):
        super().setUp()
        oidc._cache.clear()
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE auth_source='directory'")
            conn.execute("DELETE FROM auth_directories")
        self.idp = FakeIdP()
        patcher = mock.patch.object(oidc, "_json", self.idp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, preset="authentik", issuer=ISSUER, enabled="1", **extra):
        return self.admin.post("/accounts/directories/add", {"kind": "oidc", "preset": preset, "issuer": issuer,
                               "client_id": CLIENT, "client_secret": SECRET, "enabled": enabled, "new_id": DIRECTORY,
                               "label": "", **extra}, page="/accounts/directories")

    def start(self, client):
        r = client.get(f"/login/sso/{DIRECTORY}")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["Location"]).query).items()}
        self.idp.nonce = query["nonce"]
        return query

    def callback(self, client, query, **extra):
        return client.get(f"/login/sso/{DIRECTORY}/callback", query_string={"code": "the-code", "state": query["state"], **extra})

    def user(self, username):
        with transaction() as conn:
            return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


class TestAuthentikSignIn(OidcTest):
    def test_request_then_approval_then_session(self):
        self.add()
        with transaction() as conn:
            row = conn.execute("SELECT * FROM auth_directories").fetchone()
        self.assertEqual((row["id"], row["kind"], row["label"]), (DIRECTORY, "oidc", "Authentik"))  # nom par défaut
        self.assertNotIn(SECRET, row["config"])                                 # secret chiffré
        browser = fixture.Browser()
        self.assertIn("Se connecter avec Authentik", browser.c.get("/login").get_data(as_text=True))
        query = self.start(browser.c)
        self.assertEqual(query["redirect_uri"], f"https://repo.test:8443/login/sso/{DIRECTORY}/callback")
        self.assertEqual((query["response_type"], query["client_id"], query["code_challenge_method"]), ("code", CLIENT, "S256"))
        self.assertEqual(query["scope"], "openid profile email")
        r = self.callback(browser.c, query)
        self.assertEqual(r.status_code, 202)
        self.assertIn("demande d’accès a été créée", r.get_data(as_text=True))
        user = self.user("jean.dupont")                                         # identifiant tiré de preferred_username
        self.assertTrue(user["pending"] and user["disabled"])
        expected = base64.urlsafe_b64encode(hashlib.sha256(self.idp.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], expected)                     # PKCE S256 cohérent
        with transaction() as conn:
            link = conn.execute("SELECT * FROM directory_identities WHERE user_id=?", (user["id"],)).fetchone()
        self.assertEqual((link["subject"], link["display_name"], link["email"]), ("a1b2c3d4e5f6", "Jean Dupont", "jean@entreprise.test"))
        self.admin.post(f"/accounts/{user['id']}/approve", {"role": "viewer"}, page="/accounts")
        accounts = html.unescape(self.admin.c.get("/accounts").get_data(as_text=True))
        self.assertIn("se connecter avec le bouton « Se connecter avec Authentik »", accounts)
        self.assertIn('title="Identité chez le fournisseur (sub) : a1b2c3d4e5f6"', accounts)   # sub opaque : en info-bulle
        self.assertNotIn("identifiant d'annuaire a1b2c3d4e5f6", accounts)
        r = self.callback(browser.c, self.start(browser.c))
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn('data-continue="/"', page)                                # enchaînement vers loco
        self.assertEqual(browser.c.get("/").status_code, 200)                   # session ouverte
        with browser.c.session_transaction() as s:
            self.assertEqual((s["uid"], s["directory"]), (user["id"], DIRECTORY))

    def test_password_form_never_signs_in_an_sso_account(self):
        self.add()
        browser = fixture.Browser()
        self.callback(browser.c, self.start(browser.c))
        user = self.user("jean.dupont")
        self.admin.post(f"/accounts/{user['id']}/approve", {"role": "viewer"}, page="/accounts")
        r = fixture.Browser().login("jean.dupont", "anything")
        self.assertEqual(r.status_code, 202)
        self.assertIn("Se connecter avec Authentik", r.get_data(as_text=True))

    def test_hs256_signed_with_the_client_secret_is_accepted(self):
        # Authentik sans clé de signature choisie : HMAC avec le secret client.
        self.add()
        self.idp.mutate = lambda idp, claims: idp.sign(claims, alg="HS256")
        browser = fixture.Browser()
        r = self.callback(browser.c, self.start(browser.c))
        self.assertEqual(r.status_code, 202)
        self.idp.mutate = lambda idp, claims: idp.sign(claims, alg="HS256", secret="another-secret")
        browser = fixture.Browser()
        r = self.callback(browser.c, self.start(browser.c))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Signature du jeton d’identité invalide", r.get_data(as_text=True))

    def test_es256_is_accepted(self):
        self.add()
        self.idp.mutate = lambda idp, claims: idp.sign(claims, alg="ES256")
        browser = fixture.Browser()
        self.assertEqual(self.callback(browser.c, self.start(browser.c)).status_code, 202)

    def test_userinfo_completes_missing_claims_and_must_match(self):
        self.add()
        def bare(idp, claims):
            for k in ("preferred_username", "email", "name"):
                claims.pop(k)
            return idp.sign(claims)
        self.idp.mutate = bare
        self.idp.userinfo = {"sub": "a1b2c3d4e5f6", "preferred_username": "marie", "email": "marie@entreprise.test"}
        browser = fixture.Browser()
        self.assertEqual(self.callback(browser.c, self.start(browser.c)).status_code, 202)
        self.assertIsNotNone(self.user("marie"))
        self.idp.userinfo = {"sub": "someone-else", "preferred_username": "x"}
        self.idp.mutate = lambda idp, claims: idp.sign({**{k: v for k, v in claims.items() if k != "preferred_username"},
                                                        "sub": "new-subject"})
        browser = fixture.Browser()
        r = self.callback(browser.c, self.start(browser.c))
        self.assertEqual(r.status_code, 400)


class TestRefusals(OidcTest):
    def refused(self, mutate, message=None):
        self.idp.mutate = mutate
        browser = fixture.Browser()
        r = self.callback(browser.c, self.start(browser.c))
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:400])
        if message:
            self.assertIn(message, r.get_data(as_text=True))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE auth_source='directory'").fetchone()[0], 0)

    def test_tampered_tokens_are_refused(self):
        self.add()
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        self.refused(lambda idp, c: idp.sign(c, key=other), "Signature du jeton d’identité invalide")
        self.refused(lambda idp, c: idp.sign({**c, "nonce": "autre"}), "nonce")
        self.refused(lambda idp, c: idp.sign({**c, "aud": "autre-client"}), "autre client")
        self.refused(lambda idp, c: idp.sign({**c, "aud": [CLIENT, "autre"]}), "autre client")      # sans azp
        self.refused(lambda idp, c: idp.sign({**c, "iss": "https://evil.test/"}), "Émetteur du jeton")
        self.refused(lambda idp, c: idp.sign({**c, "exp": now - 600}), "expiré")
        self.refused(lambda idp, c: idp.sign({**c, "iat": now + 3600}), "futur")
        self.refused(lambda idp, c: idp.sign(c, alg="none"), "Algorithme de signature refusé")
        self.refused(lambda idp, c: idp.sign(c, kid="inconnue"), "Clé de signature inconnue")
        self.refused(lambda idp, c: idp.sign({**c, "sub": ""}), "Identité absente")

    def test_state_and_browser_binding(self):
        self.add()
        browser = fixture.Browser()
        self.assertEqual(self.callback(browser.c, {**self.start(browser.c), "state": "falsifie"}).status_code, 400)
        self.assertEqual(self.callback(fixture.Browser().c, self.start(browser.c)).status_code, 400)   # autre navigateur
        r = self.callback(browser.c, self.start(browser.c), iss="https://evil.test/")                 # RFC 9207
        self.assertEqual(r.status_code, 400)
        self.assertIn("autre émetteur", r.get_data(as_text=True))
        r = browser.c.get(f"/login/sso/{DIRECTORY}/callback", query_string={"error": "access_denied", "state": self.start(browser.c)["state"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("refusé l’accès", r.get_data(as_text=True))

    def test_disabled_provider_neither_starts_nor_finishes(self):
        self.add()
        browser = fixture.Browser()
        query = self.start(browser.c)
        with transaction() as conn:
            conn.execute("UPDATE auth_directories SET enabled=0")
        self.assertEqual(self.callback(browser.c, query).status_code, 400)
        self.assertEqual(browser.c.get(f"/login/sso/{DIRECTORY}").status_code, 404)
        self.assertNotIn("Se connecter avec", browser.c.get("/login").get_data(as_text=True))


class TestAdministration(OidcTest):
    def test_test_shows_the_identity_and_creates_nothing_even_before_activation(self):
        self.add(enabled="0")
        r = self.admin.post(f"/accounts/directories/{DIRECTORY}/test", {}, page="/accounts/directories")
        # Pas de redirection depuis un POST (CSP form-action 'self') : page intermédiaire qui enchaîne vers Authentik.
        self.assertEqual(r.status_code, 200)
        target = html.unescape(re.search(r'data-continue="([^"]+)"', r.get_data(as_text=True)).group(1))
        self.assertTrue(target.startswith("https://ak.test/application/o/authorize/?"), target)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(target).query).items()}
        self.assertNotIn("prompt", query)                       # Authentik : prompt=login = deux connexions
        self.idp.nonce = query["nonce"]
        r = self.callback(self.admin.c, query)
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        for text in ("Connexion avec Authentik réussie", "jean.dupont", "jean@entreprise.test", "loco-admins, staff"):
            self.assertIn(text, page)
        self.assertIsNone(self.user("jean.dupont"))

    def test_provider_error_details_only_for_the_admin_test(self):
        self.add()
        r = self.admin.post(f"/accounts/directories/{DIRECTORY}/test", {}, page="/accounts/directories")
        target = html.unescape(re.search(r'data-continue="([^"]+)"', r.get_data(as_text=True)).group(1))
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)["state"][0]
        refusal = {"error": "invalid_request", "error_description": "The request is otherwise malformed<script>", "state": state}
        r = self.admin.c.get(f"/login/sso/{DIRECTORY}/callback", query_string=refusal)
        page = html.unescape(r.get_data(as_text=True))
        self.assertEqual(r.status_code, 400)
        for text in ("Test de connexion", "(invalid_request) : The request is otherwise malformedscript",
                     "« Authorization Code »", "Retour à Annuaires et SSO"):
            self.assertIn(text, page)
        browser = fixture.Browser()
        r = browser.c.get(f"/login/sso/{DIRECTORY}/callback", query_string={**refusal, "state": self.start(browser.c)["state"]})
        page = html.unescape(r.get_data(as_text=True))
        note = re.search(r'<p class="note err"[^>]*>(.*?)</p>', page).group(1)
        self.assertEqual(note, "Connexion avec Authentik impossible : Authentik a refusé la connexion (invalid_request).")
        self.assertNotIn("Authorization Code", page)
        self.assertNotIn("Annuaires et SSO", page)

    def test_form_validation_and_locked_issuer(self):
        self.add(issuer="https://ak.test/")                                     # émetteur Authentik incomplet
        with transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM auth_directories").fetchone())
        self.add(issuer=ISSUER + ".well-known/openid-configuration")            # URL de configuration collée : acceptée
        with transaction() as conn:
            cfg = json.loads(conn.execute("SELECT config FROM auth_directories").fetchone()[0])
        self.assertEqual(cfg["issuer"].rstrip("/"), ISSUER.rstrip("/"))
        page = self.admin.c.get("/accounts/directories").get_data(as_text=True)
        self.assertIn(f"https://repo.test:8443/login/sso/{DIRECTORY}/callback", page)          # à déclarer chez Authentik
        self.admin.post(f"/accounts/directories/{DIRECTORY}/edit", {"label": "Authentik", "preset": "authentik",
                        "issuer": "https://autre.test/application/o/loco/", "client_id": CLIENT, "enabled": "1"},
                        page="/accounts/directories")
        with transaction() as conn:
            cfg = json.loads(conn.execute("SELECT config FROM auth_directories").fetchone()[0])
        self.assertEqual(cfg["issuer"].rstrip("/"), ISSUER.rstrip("/"))        # émetteur inchangé
        sealed = cfg["client_secret"]
        self.admin.post(f"/accounts/directories/{DIRECTORY}/edit", {"label": "Authentik maison", "preset": "authentik",
                        "issuer": ISSUER, "client_id": CLIENT, "client_secret": "", "enabled": "1"}, page="/accounts/directories")
        with transaction() as conn:
            row = conn.execute("SELECT label, config FROM auth_directories").fetchone()
        self.assertEqual((row["label"], json.loads(row["config"])["client_secret"]), ("Authentik maison", sealed))  # secret conservé


def self_signed(common_name="authentik default certificate"):
    """Certificat auto-signé de serveur au SAN « * », comme celui d'Authentik par défaut."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "authentik"), x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(7)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("*")]), critical=False).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())


class _Handler(http.server.BaseHTTPRequestHandler):
    ERRORS = {"/error-client": b'{"error": "invalid_client", "error_description": "Client authentication failed"}',
              "/error-list": b'["invalid_client"]', "/error-odd": b'{"error": ["invalid_client"]}'}

    def do_GET(self):
        body = self.ERRORS.get(self.path, b'{"ok": true}')
        self.send_response(400 if self.path in self.ERRORS else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestProviderTls(unittest.TestCase):
    """Vrai HTTPS local : certificat épinglé (auto-signé, SAN « * ») ou autorité ; jamais de certificat inconnu."""
    def serve(self, cert_pem, key_pem):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "server.pem")
        with open(path, "w") as f:
            f.write(cert_pem + key_pem)
        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"https://127.0.0.1:{server.server_address[1]}/x"

    def test_pinned_self_signed_certificate(self):
        cert, key = self_signed()
        url = self.serve(cert, key)
        self.assertEqual(oidc._json({"issuer": ISSUER, "ca_pem": cert}, url), {"ok": True})
        with self.assertRaises(oidc.OIDCError):                                 # inconnu sans épinglage
            oidc._json({"issuer": ISSUER}, url)
        other, _ = self_signed("another")
        with self.assertRaisesRegex(oidc.OIDCError, "pas celui collé"):         # autre certificat épinglé
            oidc._json({"issuer": ISSUER, "ca_pem": other}, url)

    def test_old_cryptography_without_the_plural_pem_loader(self):
        """Debian 12 (cryptography 38) et Ubuntu 22.04 (3.4) n'ont pas x509.load_pem_x509_certificates (39+)."""
        from cryptography import x509
        cert, key = self_signed()
        url = self.serve(cert, key)
        with mock.patch.dict(vars(x509)):
            vars(x509).pop("load_pem_x509_certificates", None)
            self.assertEqual(oidc._json({"issuer": ISSUER, "ca_pem": cert}, url), {"ok": True})
            ca, server_cert, server_key = certificate()
            self.assertEqual(oidc._json({"issuer": ISSUER, "ca_pem": ca}, self.serve(server_cert, server_key)), {"ok": True})
            with self.assertRaisesRegex(oidc.OIDCError, "illisible"):
                oidc._json({"issuer": ISSUER, "ca_pem": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"}, url)

    def test_provider_errors_are_read_safely(self):
        cert, key = self_signed()
        base, cfg = self.serve(cert, key).rsplit("/", 1)[0], {"issuer": ISSUER, "ca_pem": cert}
        with self.assertRaisesRegex(oidc.OIDCError, "secret client refusé"):  # erreur OAuth2 telle qu'Authentik la renvoie
            oidc._json(cfg, base + "/error-client")
        for path in ("/error-list", "/error-odd"):                             # JSON inattendu : message générique
            with self.assertRaisesRegex(oidc.OIDCError, "HTTP 400"):
                oidc._json(cfg, base + path)

    def test_authority_checks_chain_and_name(self):
        ca, server_cert, server_key = certificate()                             # autorité + serveur 127.0.0.1
        url = self.serve(server_cert, server_key)
        self.assertEqual(oidc._json({"issuer": ISSUER, "ca_pem": ca}, url), {"ok": True})
        with self.assertRaises(oidc.OIDCError):
            oidc._json({"issuer": ISSUER, "ca_pem": certificate()[0]}, url)     # autre autorité


if __name__ == "__main__":
    unittest.main()
