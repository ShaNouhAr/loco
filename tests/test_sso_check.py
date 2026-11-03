"""« Vérifier la configuration » : chaque panne reproduite contre un vrai serveur TLS local, avec sa cause et sa correction,
sans secret dans le résultat, sans compte ni cookie créés."""
import datetime
import hashlib
import html
import http.server
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from test_oidc import CLIENT, DIRECTORY, ISSUER, OidcTest, SECRET
from loco import oidc, sso_check
from loco.db import transaction

CALLBACK = f"https://loco.lab.local:8443/login/sso/{DIRECTORY}/callback"
NOW = datetime.datetime.now(datetime.timezone.utc)


def issue(name, signer=None, ca=False, sans=(), days=30, start=-1):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    parent_key = signer[1] if signer else key
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(signer[0].subject if signer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(NOW + datetime.timedelta(days=start)).not_valid_after(NOW + datetime.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(not ca, False, False, False, False, ca, ca, False, False), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(parent_key.public_key()), critical=False))
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]), critical=False)
    return builder.sign(parent_key, hashes.SHA256()), key


def pem(*certs):
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in certs)


ROOT = issue("Racine Lab", ca=True)
INTERMEDIATE = issue("Intermediaire Lab", ROOT, ca=True)
LEAF = issue("sso.lab.local", INTERMEDIATE, sans=("localhost",))
OTHER_ROOT = issue("Autre Racine", ca=True)


class Provider:
    """Vrai serveur HTTPS local qui joue le fournisseur ; authentik=True répond comme Authentik 2026.8.5."""
    def __init__(self, test, certs=None, key=None):
        self.hits, self.token_calls = [], []
        self.doc, self.jwks = {}, {"keys": [{"kty": "EC", "kid": "k", "use": "sig", "crv": "P-256", "x": "AAAA", "y": "AAAA"}]}
        self.token = (400, {"error": "invalid_grant"})
        self.authorize = (302, {"Location": "/if/flow/default-authentication-flow/?next=x"}, b"")
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, body=b"", headers=None, kind="application/json"):
                self.send_response(code)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                provider.hits.append(path)
                if path.endswith("/.well-known/openid-configuration"):
                    if provider.doc is None:
                        return self.send(404)
                    return self.send(200, json.dumps(provider.document()).encode())
                if path.endswith("/jwks/"):
                    return self.send(200, json.dumps(provider.jwks).encode())
                if path.endswith("/authorize/"):
                    provider.authorize_query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                    code, headers, body = provider.authorize
                    return self.send(code, body, headers, "text/html")
                return self.send(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                provider.token_calls.append((dict(self.headers), dict(urllib.parse.parse_qsl(body.decode()))))
                code, data = provider.token
                self.send(code, json.dumps(data).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *args: None           # poignées de main refusées exprès par le client
        self.port = self.server.server_address[1]
        if certs:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            with tempfile.TemporaryDirectory() as tmp:
                chain, private = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
                with open(chain, "w") as f:
                    f.write(pem(*certs))
                with open(private, "wb") as f:
                    f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                context.load_cert_chain(chain, private)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True, do_handshake_on_connect=False)
        self.issuer = f"https://localhost:{self.port}/application/o/loco/"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def document(self):
        base = f"https://localhost:{self.port}/application/o/"
        return {"issuer": self.issuer, "authorization_endpoint": base + "authorize/", "token_endpoint": base + "token/", "jwks_uri": base + "loco/jwks/",
                "code_challenge_methods_supported": ["S256"], "id_token_signing_alg_values_supported": ["ES256"], **self.doc}


class CheckTest(unittest.TestCase):
    def setUp(self):
        oidc._cache.clear()
        for patcher in (mock.patch.object(sso_check, "callback_url", return_value=CALLBACK), mock.patch.dict(os.environ, {"https_proxy": "", "HTTPS_PROXY": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self, certs=None, key=None):
        certs = (LEAF[0], INTERMEDIATE[0]) if certs is None else certs
        return Provider(self, certs, key or LEAF[1])

    def run_check(self, provider, ca=None, preset="authentik", issuer=None, **extra):
        ca = pem(ROOT[0]) if ca is None else ca
        cfg = {"preset": preset, "issuer": issuer or provider.issuer, "client_id": CLIENT, "client_secret": SECRET, "ca_pem": ca, **extra}
        with mock.patch("loco.directories.config_of", return_value=cfg):
            steps = sso_check.run({"id": DIRECTORY})
        self.assertNotIn(SECRET, json.dumps(steps))
        return {s["key"]: s for s in steps}

    def assertStates(self, steps, **expected):
        self.assertEqual({k: steps[k]["state"] for k in expected}, expected, json.dumps(steps, ensure_ascii=False, indent=1))


class TestEverythingFine(CheckTest):
    def test_every_step_passes_with_the_full_chain(self):
        steps = self.run_check(self.serve())
        self.assertStates(steps, dns="ok", tcp="ok", tls="ok", discovery="ok", client="ok", authorize="ok")
        self.assertEqual([k for k, _t in sso_check.STEPS], list(steps))

    def test_the_real_parameters_are_sent_and_redirects_are_not_followed(self):
        provider = self.serve()
        self.run_check(provider, scopes="openid profile email")
        query = provider.authorize_query
        self.assertEqual((query["response_type"], query["client_id"], query["redirect_uri"], query["scope"], query["code_challenge_method"]),
                         ("code", CLIENT, CALLBACK, "openid profile email", "S256"))
        self.assertTrue(query["state"] and query["nonce"] and query["code_challenge"])
        self.assertNotIn("/if/flow/default-authentication-flow/", provider.hits)

    def test_the_secret_only_goes_in_the_authorization_header(self):
        provider = self.serve()
        self.run_check(provider)
        headers, form = provider.token_calls[0]
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        self.assertNotIn(SECRET, json.dumps(form))
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["redirect_uri"], CALLBACK)

    def test_discovery_is_read_again_not_from_the_cache(self):
        provider = self.serve()
        self.run_check(provider)
        provider.doc = {"issuer": "https://ailleurs.test/"}
        self.assertStates(self.run_check(provider), discovery="fail")


class TestNetwork(CheckTest):
    """Panne 3 : l'émetteur n'est pas résolu par ce serveur (1.1.1.1 ne connaît pas *.lab.local)."""
    def test_name_not_resolved_gives_the_cause_and_the_dns_in_use(self):
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(-2, "Name does not resolve")), \
                mock.patch.object(sso_check, "_resolvers", return_value=["1.1.1.1"]):
            steps = self.run_check(None, issuer="https://sso.lab.local:9443/application/o/loco/")
        self.assertStates(steps, dns="fail", tcp="skipped", tls="skipped", discovery="skipped", keys="skipped", client="skipped", authorize="skipped")
        self.assertIn("Name does not resolve", steps["dns"]["detail"])
        self.assertIn("sso.lab.local", steps["dns"]["detail"])
        self.assertIn("1.1.1.1", steps["dns"]["fix"])

    def test_closed_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        steps = self.run_check(None, issuer=f"https://127.0.0.1:{port}/application/o/loco/")
        self.assertStates(steps, dns="ok", tcp="fail", tls="skipped")
        self.assertIn(str(port), steps["tcp"]["detail"])

    def test_no_answer_points_to_the_firewall(self):
        with mock.patch.object(socket, "create_connection", side_effect=TimeoutError):
            steps = self.run_check(None, issuer="https://127.0.0.1:9443/application/o/loco/")
        self.assertStates(steps, tcp="fail")
        self.assertIn("pare-feu", steps["tcp"]["fix"])

    def test_behind_a_proxy_direct_steps_are_not_failures_and_its_password_is_hidden(self):
        with mock.patch.object(sso_check, "_proxy_for", return_value="proxy.lab"):
            with mock.patch.object(oidc, "metadata", side_effect=oidc.OIDCError("x")), mock.patch.object(sso_check, "metadata", side_effect=oidc.OIDCError("x")):
                steps = self.run_check(None, issuer="https://sso.lab.local/application/o/loco/")
        self.assertStates(steps, dns="skipped", tcp="skipped", tls="skipped", discovery="fail")
        with mock.patch.dict(os.environ, {"https_proxy": "http://user:motdepasse@proxy.lab:3128", "no_proxy": ""}):
            self.assertEqual(sso_check._proxy_for("https://sso.lab.local/"), "proxy.lab")


class TestCertificate(CheckTest):
    """Chaîne et nom, sur un vrai serveur TLS : l'intermédiaire ou la racine manquants sont nommés."""
    def test_missing_intermediate_is_named(self):
        steps = self.run_check(self.serve((LEAF[0],)))
        self.assertStates(steps, tls="fail", discovery="skipped")
        self.assertIn("Intermédiaire manquant", steps["tls"]["detail"])
        self.assertIn("Intermediaire Lab", steps["tls"]["detail"])
        self.assertIn("Racine Lab", steps["tls"]["detail"])
        self.assertIn("chaîne complète", steps["tls"]["fix"])

    def test_missing_root_is_named(self):
        steps = self.run_check(self.serve(), ca="")
        self.assertStates(steps, tls="fail")
        self.assertIn("Racine manquante", steps["tls"]["detail"])
        self.assertIn("Racine Lab", steps["tls"]["fix"])

    def test_root_sent_but_not_approved_shows_its_fingerprint(self):
        steps = self.run_check(self.serve((LEAF[0], INTERMEDIATE[0], ROOT[0])), ca="")
        self.assertStates(steps, tls="fail")
        self.assertIn("Racine non approuvée", steps["tls"]["detail"])
        digest = hashlib.sha256(ROOT[0].public_bytes(serialization.Encoding.DER)).hexdigest().upper()
        self.assertIn(digest[:2] + ":" + digest[2:4], steps["tls"]["fix"])

    def test_wrong_authority_names_the_expected_one(self):
        steps = self.run_check(self.serve(), ca=pem(OTHER_ROOT[0]))
        self.assertStates(steps, tls="fail")
        self.assertIn("Racine Lab", steps["tls"]["detail"])

    def test_leaf_alone_and_nothing_pasted(self):
        steps = self.run_check(self.serve((LEAF[0],)), ca="")
        self.assertStates(steps, tls="fail")
        self.assertIn("Autorité manquante", steps["tls"]["detail"])
        self.assertIn("Intermediaire Lab", steps["tls"]["detail"])

    def test_name_not_covered_lists_what_is(self):
        other = issue("sso", INTERMEDIATE, sans=("autre.lab.local",))
        steps = self.run_check(self.serve((other[0], INTERMEDIATE[0]), other[1]))
        self.assertStates(steps, tls="fail")
        self.assertIn("autre.lab.local", steps["tls"]["detail"])
        self.assertIn("localhost", steps["tls"]["detail"])

    def test_expired(self):
        old = issue("sso.lab.local", INTERMEDIATE, sans=("localhost",), days=-2, start=-10)
        steps = self.run_check(self.serve((old[0], INTERMEDIATE[0]), old[1]))
        self.assertStates(steps, tls="fail")
        self.assertIn("a expiré", steps["tls"]["detail"])

    def test_self_signed_can_be_pinned(self):
        alone = issue("authentik default certificate", sans=("localhost",))
        provider = self.serve((alone[0],), alone[1])
        refused = self.run_check(provider, ca="")
        self.assertStates(refused, tls="fail")
        self.assertIn("auto-signé", refused["tls"]["detail"])
        self.assertStates(self.run_check(provider, ca=pem(alone[0])), tls="ok", discovery="ok", authorize="ok")

    def test_a_renewed_pinned_certificate_is_explained(self):
        alone, renewed = issue("authentik", sans=("localhost",)), issue("authentik", sans=("localhost",))
        steps = self.run_check(self.serve((renewed[0],), renewed[1]), ca=pem(alone[0]))
        self.assertStates(steps, tls="fail")
        self.assertIn("celui collé", steps["tls"]["detail"])

    def test_a_port_without_tls(self):
        provider = Provider(self)
        steps = self.run_check(provider)
        self.assertStates(steps, tcp="ok", tls="fail", discovery="skipped")
        self.assertIn("HTTPS", steps["tls"]["fix"])

    def test_unreadable_authority(self):
        steps = self.run_check(self.serve(), ca="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n")
        self.assertStates(steps, tls="fail")

    def test_the_chain_walk(self):
        leaf, intermediate, root, other = LEAF[0], INTERMEDIATE[0], ROOT[0], OTHER_ROOT[0]
        self.assertEqual(sso_check.chain_gap([leaf], [root])[0], "signer")
        self.assertEqual(sso_check.chain_gap([leaf, intermediate], [other])[0], "top")
        self.assertEqual(sso_check.chain_gap([leaf, intermediate, root], [])[0], "root")
        self.assertEqual(sso_check.chain_gap([root], [])[0], "self")
        self.assertEqual(sso_check.chain_gap([leaf, intermediate], [root])[0], "")


class TestDiscovery(CheckTest):
    def test_another_issuer_shows_both_and_explains_host_and_port(self):
        provider = self.serve()
        provider.doc = {"issuer": "https://sso.lab.local:9443/application/o/loco/"}
        steps = self.run_check(provider)
        self.assertStates(steps, discovery="fail", keys="skipped", client="skipped", authorize="skipped")
        self.assertIn("https://sso.lab.local:9443/application/o/loco/", steps["discovery"]["detail"])
        self.assertIn("hôte et le port", steps["discovery"]["fix"])

    def test_unknown_application(self):
        provider = self.serve()
        provider.doc = None
        steps = self.run_check(provider)
        self.assertStates(steps, discovery="fail")
        self.assertIn("slug", steps["discovery"]["fix"])

    def test_no_pkce_is_a_failure_and_silence_is_inconclusive(self):
        provider = self.serve()
        provider.doc = {"code_challenge_methods_supported": ["plain"]}
        steps = self.run_check(provider)
        self.assertStates(steps, discovery="fail")
        self.assertIn("PKCE", steps["discovery"]["fix"])
        provider.doc = {"code_challenge_methods_supported": None}
        self.assertStates(self.run_check(provider), discovery="unsure", client="ok")


class TestKeys(CheckTest):
    def test_no_key_without_hmac_is_a_failure_but_does_not_stop_the_rest(self):
        provider = self.serve()
        provider.jwks = {"keys": []}
        steps = self.run_check(provider)
        self.assertStates(steps, keys="fail", client="ok", authorize="ok")
        self.assertIn("clé de signature", steps["keys"]["fix"])

    def test_no_key_with_hmac_works_and_recommends_rs256(self):
        provider = self.serve()
        provider.jwks = {"keys": []}
        provider.doc = {"id_token_signing_alg_values_supported": ["HS256"]}
        steps = self.run_check(provider)
        self.assertStates(steps, keys="ok")
        self.assertIn("RS256", steps["keys"]["detail"])

    def test_keys_are_listed(self):
        provider = self.serve()
        provider.jwks = {"keys": [{"kty": "EC", "crv": "P-256", "x": "AAAA", "y": "AAAA", "use": "sig"}]}
        with mock.patch.object(sso_check, "_public_key"):
            self.assertIn("ES256", self.run_check(provider)["keys"]["detail"])

    def test_unusable_keys(self):
        provider = self.serve()
        provider.jwks = {"keys": [{"kty": "EC", "crv": "P-256", "use": "sig"}]}
        self.assertStates(self.run_check(provider), keys="fail")


class TestClient(CheckTest):
    def test_wrong_secret(self):
        provider = self.serve()
        provider.token = (400, {"error": "invalid_client", "error_description": "Client authentication failed"})
        steps = self.run_check(provider)
        self.assertStates(steps, client="fail", authorize="ok")
        self.assertIn("Client Secret", steps["client"]["fix"])

    def test_flow_not_allowed(self):
        provider = self.serve()
        provider.token = (400, {"error": "unsupported_grant_type"})
        self.assertStates(self.run_check(provider), client="fail")

    def test_unknown_answers_are_inconclusive(self):
        provider = self.serve()
        for token in ((401, {}), (500, {}), (400, {"error": "invalid_request"})):
            provider.token = token
            self.assertStates(self.run_check(provider), client="unsure")

    def test_tokens_returned_for_a_fake_code_are_not_a_success(self):
        provider = self.serve()
        provider.token = (200, {"access_token": "jeton-secret", "id_token": "x"})
        steps = self.run_check(provider)
        self.assertStates(steps, client="unsure")
        self.assertNotIn("jeton-secret", json.dumps(steps))


class TestAuthorize(CheckTest):
    """Réponses relevées sur Authentik 2026.8.5, ailleurs une réponse inconnue n'est jamais un échec."""
    def test_authentik_undeclared_return_url_gives_the_url_to_declare(self):
        provider = self.serve()
        provider.authorize = (400, {}, b"<html><p>Redirect URI Error</p></html>")
        steps = self.run_check(provider)
        self.assertStates(steps, authorize="fail")
        self.assertEqual(steps["authorize"]["value"], CALLBACK)
        self.assertIn("Déclarez", steps["authorize"]["fix"])

    def test_an_undeclared_url_does_not_accuse_the_secret(self):
        # Relevé sur Authentik 2026.8.5 : URL de retour non déclarée = invalid_client au jeton, même avec le bon secret.
        provider = self.serve()
        provider.authorize = (400, {}, b"Redirect URI Error")
        provider.token = (400, {"error": "invalid_client"})
        steps = self.run_check(provider)
        self.assertStates(steps, client="unsure", authorize="fail")
        self.assertIn("URL de retour", steps["client"]["fix"])

    def test_authentik_code_flow_not_allowed(self):
        provider = self.serve()
        provider.authorize = (302, {"Location": CALLBACK + "?error=invalid_request&error_description=The%20request%20is%20otherwise%20malformed&state=S"}, b"")
        steps = self.run_check(provider)
        self.assertStates(steps, authorize="fail")
        self.assertIn("Authorization Code", steps["authorize"]["fix"])

    def test_authentik_unknown_client(self):
        provider = self.serve()
        provider.authorize = (400, {}, b"Client ID Error")
        steps = self.run_check(provider)
        self.assertStates(steps, authorize="fail")
        self.assertIn("Client ID", steps["authorize"]["fix"])

    def test_authentik_unknown_answer_is_inconclusive(self):
        provider = self.serve()
        provider.authorize = (500, {}, b"erreur")
        self.assertStates(self.run_check(provider), authorize="unsure")

    def test_other_providers_are_never_failed_on_an_unknown_answer(self):
        provider = self.serve()
        for answer in ((400, {}, b"Redirect URI Error"), (200, {}, b"<form>"), (302, {"Location": "/login"}, b""), (500, {}, b""),
                       (302, {"Location": CALLBACK + "?error=invalid_request"}, b""), (302, {"Location": CALLBACK + "?error=server_error&error_description=x"}, b"")):
            provider.authorize = answer
            self.assertStates(self.run_check(provider, preset="oidc"), authorize="unsure")
        provider.authorize = (400, {}, b"")
        self.assertEqual(self.run_check(provider, preset="oidc")["authorize"]["value"], CALLBACK)

    def test_standard_refusals_are_failures_everywhere(self):
        provider = self.serve()
        for error in ("unauthorized_client", "unsupported_response_type", "invalid_scope"):
            provider.authorize = (302, {"Location": f"{CALLBACK}?error={error}"}, b"")
            self.assertStates(self.run_check(provider, preset="oidc"), authorize="fail")

    def test_what_comes_back_is_never_shown_raw(self):
        provider = self.serve()
        provider.authorize = (302, {"Location": CALLBACK + "?error=server_error&error_description=" + urllib.parse.quote(f"<script>{SECRET}</script>") + "&state=etat-secret"}, b"")
        steps = self.run_check(provider, preset="oidc")
        self.assertNotIn("<script>", json.dumps(steps))
        self.assertNotIn("etat-secret", json.dumps(steps))

    def test_an_unexpected_exception_never_breaks_the_page(self):
        with mock.patch.object(sso_check, "_step_authorize", side_effect=RuntimeError(SECRET)), mock.patch.object(oidc.app.logger, "exception"):
            steps = self.run_check(self.serve())
        self.assertStates(steps, authorize="unsure")


class TestRoute(OidcTest):
    STEPS = [{"key": "dns", "title": "Résolution du nom", "state": "fail", "detail": f"<b>{ISSUER}</b>", "fix": "Déclarez ce nom", "value": "https://x/cb"},
             {"key": "tcp", "title": "Connexion", "state": "skipped", "detail": "Non effectuée", "fix": "", "value": ""}]

    def check(self, steps=None, status=200):
        with mock.patch("loco.sso_check.run", return_value=steps or self.STEPS) as run:
            r = self.admin.post(f"/accounts/directories/{DIRECTORY}/check", {}, page="/accounts/directories")
        self.assertEqual(r.status_code, status)
        self.last_run = run
        return r

    def test_the_result_lists_each_step_escaped_with_its_correction(self):
        self.add()
        page = self.check().get_data(as_text=True)
        self.assertIn("Résolution du nom", page)
        self.assertIn("Déclarez ce nom", page)
        self.assertIn("&lt;b&gt;", page)
        self.assertNotIn("<b>" + ISSUER, page)
        self.assertIn('value="https://x/cb"', page)
        self.assertIn("1 problème à corriger", page)

    def test_it_creates_no_account_and_sets_no_flow_cookie_and_is_audited_without_secret(self):
        self.add()
        with transaction() as conn:
            before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        r = self.check()
        self.assertFalse(any(oidc.FLOW_COOKIE in c for c in r.headers.getlist("Set-Cookie")))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], before)
            row = conn.execute("SELECT detail FROM audit WHERE action='sso.verification' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("1 échec", row["detail"])
        self.assertIn("dns", row["detail"])
        self.assertNotIn(SECRET, row["detail"])

    def test_it_also_works_on_a_provider_not_yet_enabled(self):
        self.add(enabled="0")
        self.check()
        self.last_run.assert_called_once()

    def test_it_runs_on_the_real_provider_row_with_the_secret_only_in_memory(self):
        self.add()
        with mock.patch("loco.sso_check._step_dns", return_value=sso_check._done("fail", "x")):
            page = self.admin.post(f"/accounts/directories/{DIRECTORY}/check", {}, page="/accounts/directories").get_data(as_text=True)
        self.assertNotIn(SECRET, page)
        self.assertIn("Non effectuée", page)

    def test_saving_a_provider_starts_it(self):
        r = self.add()
        self.assertIn(f"verifier={DIRECTORY}", r.headers["Location"])
        page = self.admin.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertRegex(page, r'data-sso-check="dir-check-' + DIRECTORY + r'"[^>]*data-auto="1"')
        r = self.admin.post(f"/accounts/directories/{DIRECTORY}/edit", {"preset": "authentik", "issuer": ISSUER, "client_id": CLIENT, "label": "", "enabled": "1"},
                            page="/accounts/directories")
        self.assertIn(f"verifier={DIRECTORY}", r.headers["Location"])

    def test_without_saving_nothing_starts_alone(self):
        self.add()
        self.assertNotIn('data-auto="1"', self.admin.c.get("/accounts/directories").get_data(as_text=True))

    def test_only_openid_providers_have_it(self):
        self.admin.post("/accounts/directories/add", {"kind": "radius", "host": "10.0.0.1", "secret": "s3cret", "label": "NPS", "enabled": "1"}, page="/accounts/directories")
        with transaction() as conn:
            radius = conn.execute("SELECT id FROM auth_directories WHERE kind='radius'").fetchone()["id"]
        r = self.admin.post(f"/accounts/directories/{radius}/check", {}, page="/accounts/directories")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(f"dir-check-{radius}", self.admin.c.get("/accounts/directories").get_data(as_text=True))

    def test_it_needs_the_right_to_manage_sso(self):
        from loco.permissions import ENDPOINTS
        self.assertEqual(ENDPOINTS["directory_check"], "sso.manage")

    def test_a_failed_sign_in_points_the_administrator_to_it(self):
        self.add()
        with mock.patch.object(oidc, "metadata", side_effect=oidc.OIDCError("injoignable")):
            page = html.unescape(self.admin.__class__().c.get(f"/login/sso/{DIRECTORY}").get_data(as_text=True))
        self.assertIn("Vérifier la configuration", page)
        self.assertNotIn("injoignable", page)                    # le détail reste pour l'administrateur


if __name__ == "__main__":
    unittest.main()
