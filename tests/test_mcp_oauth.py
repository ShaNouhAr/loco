"""Connexion d'un client MCP par OAuth 2.1 : découverte (RFC 9728, RFC 8414), enregistrement d'un client public
(RFC 7591), page d'autorisation de loco, échange du code avec PKCE, jeton MCP limité aux outils choisis, révocation ;
liens qui ouvrent Claude et Codex avec la demande d'ajout pré-remplie."""
import base64
import datetime
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

import test_portal as fixture
from test_mcp import Client, McpTest
from loco import config, mcp, mcp_oauth
from loco.db import transaction

CALLBACK = "http://127.0.0.1:53682/callback"


def pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class OAuthTest(McpTest):
    def setUp(self):
        super().setUp()
        self.c = fixture.loco.app.test_client()
        with transaction() as conn:
            conn.execute("DELETE FROM mcp_oauth_clients")
            conn.execute("DELETE FROM mcp_oauth_codes")
            conn.execute("DELETE FROM audit")

    def register(self, uris=(CALLBACK,), name="Claude Code"):
        return self.c.post("/mcp/oauth/register", json={"redirect_uris": list(uris), "client_name": name,
                                                        "grant_types": ["authorization_code", "refresh_token"],
                                                        "response_types": ["code"], "token_endpoint_auth_method": "none"})

    def client_id(self, **kwargs):
        r = self.register(**kwargs)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["client_id"]

    @staticmethod
    def params(client_id, challenge, redirect=CALLBACK, **extra):
        values = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": "etat-123", "resource": mcp_oauth.resource(), "scope": "mcp"}
        values.update(extra)
        return {k: v for k, v in values.items() if v is not None}

    def consent(self, params, browser=None, **form):
        """Décision sur la page d'autorisation : (réponse, adresse de retour vers l'application ou None)."""
        page = "/mcp/oauth/authorize?" + urllib.parse.urlencode(params)
        data = dict(params, name="Claude Code", expiry="30", tools=["list_devices"], decision="allow")
        data.update(form)
        r = (browser or self.admin).post("/mcp/oauth/authorize", data, page=page)
        found = re.search(r'data-continue="([^"]+)"', r.get_data(as_text=True))
        return r, html.unescape(found.group(1)) if found else None

    def code(self, client_id, challenge, **form):
        _r, target = self.consent(self.params(client_id, challenge), **form)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)["code"][0]

    def exchange(self, client_id, code, verifier, **extra):
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": client_id,
                "code_verifier": verifier, "resource": mcp_oauth.resource()}
        data.update(extra)
        return self.c.post("/mcp/oauth/token", data={k: v for k, v in data.items() if v is not None})

    def actions(self):
        with transaction() as conn:
            return [r["action"] for r in conn.execute("SELECT action FROM audit ORDER BY id")]


class TestDiscovery(OAuthTest):
    def test_a_client_without_token_learns_where_to_log_in(self):
        r = Client(None).request("tools/list")
        self.assertEqual(r.status_code, 401)
        self.assertIn(f'resource_metadata="{mcp_oauth.issuer()}/.well-known/oauth-protected-resource/mcp"', r.headers["WWW-Authenticate"])
        self.assertNotIn("invalid_token", r.headers["WWW-Authenticate"])
        self.assertIn('error="invalid_token"', Client("loco_mcp_" + "A" * 43).request("tools/list").headers["WWW-Authenticate"])
        for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
            doc = self.c.get(path).get_json()
            self.assertEqual(doc["resource"], mcp_oauth.issuer() + "/mcp")
            self.assertEqual(doc["authorization_servers"], [mcp_oauth.issuer()])
        meta = self.c.get("/.well-known/oauth-authorization-server").get_json()
        self.assertEqual(meta["issuer"], mcp_oauth.issuer())
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(meta["token_endpoint_auth_methods_supported"], ["none"])
        self.assertTrue(meta["authorization_response_iss_parameter_supported"])
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
            self.assertTrue(meta[key].startswith(mcp_oauth.issuer() + "/mcp/oauth/"), key)

    def test_nothing_is_offered_while_the_mcp_server_is_off(self):
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('features_disabled', '[\"mcp\"]')")
        try:
            for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server"):
                self.assertEqual(self.c.get(path).status_code, 404, path)
            self.assertEqual(self.register().status_code, 404)
        finally:
            with transaction() as conn:
                conn.execute("DELETE FROM settings WHERE key='features_disabled'")


class TestRegistration(OAuthTest):
    def test_only_an_application_of_this_computer_can_receive_the_code(self):
        for uri in (CALLBACK, "http://localhost:8080/callback", "http://[::1]:9000/cb", "http://127.0.0.1/callback",
                    "com.example.app:/oauth"):
            self.assertEqual(self.register(uris=(uri,)).status_code, 201, uri)
        for uri in ("https://attaquant.example/callback", "http://192.168.1.20:8080/callback", "javascript:alert(1)",
                    "http://user:pw@127.0.0.1:1/cb", "data:text/html,x", "file:///etc/passwd", "http://127.0.0.1:1/cb#frag", "",
                    "web+loco.evil:x", "mailto:x@example.org", "monapp:/cb"):
            r = self.register(uris=(uri,))
            self.assertEqual((r.status_code, r.get_json()["error"]), (400, "invalid_redirect_uri"), uri)
        mixed = self.register(uris=("https://vscode.dev/redirect", "http://127.0.0.1:33418")).get_json()
        self.assertEqual(mixed["redirect_uris"], ["http://127.0.0.1:33418"])               # les autres sont écartées
        self.assertEqual(mixed["token_endpoint_auth_method"], "none")
        self.assertRegex(mixed["client_id"], mcp_oauth.CLIENT_RE)

    def test_malformed_registrations_are_refused(self):
        r = self.c.post("/mcp/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "x" * 20000})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.c.post("/mcp/oauth/register", data="pas du json", content_type="application/json").status_code, 400)
        self.assertEqual(self.c.post("/mcp/oauth/register", json={"client_name": "x"}).status_code, 400)
        r = self.c.post("/mcp/oauth/register", json={"redirect_uris": [CALLBACK], "grant_types": ["client_credentials"]})
        self.assertEqual(r.get_json()["error"], "invalid_client_metadata")

    def test_registration_needs_neither_session_nor_csrf_and_names_are_cleaned(self):
        r = self.register(name="Claude\x00 Code\n" + "x" * 200)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(r.get_json()["client_name"]), 80)
        self.assertNotIn("\x00", r.get_json()["client_name"])


class TestAuthorization(OAuthTest):
    def test_the_whole_flow_gives_a_token_limited_to_the_chosen_tools(self):
        client_id = self.client_id()
        verifier, challenge = pkce()
        params = self.params(client_id, challenge)
        page = self.admin.c.get("/mcp/oauth/authorize?" + urllib.parse.urlencode(params)).get_data(as_text=True)
        self.assertIn("Autoriser Claude Code", page)
        self.assertIn("http://127.0.0.1:53682", page)                                       # où part le jeton
        self.assertRegex(page, r'value="list_devices" role="switch" data-read checked')       # lecture seule d'office
        self.assertNotRegex(page, r'value="reboot_device" role="switch"[^>]*checked')
        self.assertNotRegex(page, r'value="read_file" role="switch"[^>]*checked')           # accès distant : jamais d'office
        r, target = self.consent(params)
        self.assertEqual(r.status_code, 200)
        back = urllib.parse.urlsplit(target)
        self.assertEqual(f"{back.scheme}://{back.netloc}{back.path}", CALLBACK)
        query = urllib.parse.parse_qs(back.query)
        self.assertEqual((query["state"], query["iss"]), (["etat-123"], [mcp_oauth.issuer()]))
        answer = self.exchange(client_id, query["code"][0], verifier)
        self.assertEqual(answer.status_code, 200, answer.get_data(as_text=True))
        self.assertEqual(answer.headers["Cache-Control"], "no-store")
        token = answer.get_json()
        self.assertEqual((token["token_type"], token["expires_in"]), ("Bearer", 30 * 86400))
        self.assertRegex(token["access_token"], mcp.TOKEN_RE)
        self.assertEqual(Client(token["access_token"]).names(), ["list_devices"])
        with transaction() as conn:
            row = conn.execute("SELECT * FROM mcp_tokens WHERE client_id=?", (client_id,)).fetchone()
        self.assertEqual((row["name"], row["expires_at"][:10]),
                         ("Claude Code", (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")))
        self.assertNotIn(token["access_token"], json.dumps(dict(row)))                       # seule l'empreinte est gardée
        self.assertIn("obtenu par Claude Code", self.admin.c.get("/accounts/mcp").get_data(as_text=True))
        self.assertEqual(self.actions()[-2:], ["mcp.oauth.autorisation", "mcp.acces.ajout"])

    def test_the_code_is_single_use_and_bound_to_its_client_verifier_and_address(self):
        client_id = self.client_id()
        other = self.client_id(name="Autre")
        verifier, challenge = pkce()
        code = self.code(client_id, challenge)
        self.assertEqual(self.exchange(client_id, code, pkce()[0]).get_json()["error"], "invalid_grant")
        self.assertEqual(self.exchange(client_id, code, verifier).get_json()["error"], "invalid_grant")   # brûlé par l'échec
        self.assertEqual(self.exchange(other, self.code(client_id, challenge), verifier).get_json()["error"], "invalid_grant")
        self.assertEqual(self.exchange(client_id, self.code(client_id, challenge), verifier,
                                       redirect_uri="http://127.0.0.1:53682/autre").get_json()["error"], "invalid_grant")
        code = self.code(client_id, challenge)
        self.assertEqual(self.exchange(client_id, code, verifier, resource="https://ailleurs.example/mcp").get_json()["error"],
                         "invalid_target")
        code = self.code(client_id, challenge)
        with transaction() as conn:
            conn.execute("UPDATE mcp_oauth_codes SET expires_at=0")
        self.assertEqual(self.exchange(client_id, code, verifier).get_json()["error"], "invalid_grant")
        r = self.c.post("/mcp/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": client_id})
        self.assertEqual(r.get_json()["error"], "unsupported_grant_type")
        code = self.code(client_id, challenge)
        self.assertEqual(self.exchange(client_id, code, verifier).status_code, 200)
        self.assertEqual(self.exchange(client_id, code, verifier).get_json()["error"], "invalid_grant")   # déjà échangé
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mcp_tokens WHERE client_id=?", (client_id,)).fetchone()[0], 1)

    def test_a_loopback_port_may_change_but_not_the_path(self):
        client_id = self.client_id()
        _verifier, challenge = pkce()
        _r, target = self.consent(self.params(client_id, challenge, redirect="http://127.0.0.1:61000/callback"))
        self.assertTrue(target.startswith("http://127.0.0.1:61000/callback?code="))
        params = self.params(client_id, challenge, redirect="http://127.0.0.1:61000/ailleurs")
        r = self.admin.c.get("/mcp/oauth/authorize?" + urllib.parse.urlencode(params))
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("data-continue", r.get_data(as_text=True))                          # rien n'est renvoyé à cette adresse

    def test_invalid_requests_go_back_to_the_application_only_when_its_address_is_sure(self):
        _verifier, challenge = pkce()
        r = self.admin.c.get("/mcp/oauth/authorize?" + urllib.parse.urlencode(self.params("loco_client_inconnu", challenge)))
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("data-continue", r.get_data(as_text=True))
        client_id = self.client_id()
        cases = ((dict(code_challenge_method="plain"), "invalid_request"), (dict(code_challenge=None), "invalid_request"),
                 (dict(response_type="token"), "unsupported_response_type"),
                 (dict(resource="https://ailleurs.example/mcp"), "invalid_target"))
        for extra, error in cases:
            params = self.params(client_id, challenge, **extra)
            page = self.admin.c.get("/mcp/oauth/authorize?" + urllib.parse.urlencode(params)).get_data(as_text=True)
            target = html.unescape(re.search(r'data-continue="([^"]+)"', page).group(1))
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
            self.assertEqual((query["error"], query["state"]), ([error], ["etat-123"]), extra)

    def test_refusing_tells_the_application_and_creates_nothing(self):
        client_id = self.client_id()
        _r, target = self.consent(self.params(client_id, pkce()[1]), decision="deny")
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)["error"], ["access_denied"])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mcp_oauth_codes").fetchone()[0], 0)
        self.assertIn("mcp.oauth.refus", self.actions())

    def test_a_consent_without_tool_shows_the_page_again(self):
        client_id = self.client_id()
        r, target = self.consent(self.params(client_id, pkce()[1]), tools=[])
        self.assertEqual((r.status_code, target), (400, None))
        self.assertIn("Activez au moins un outil.", r.get_data(as_text=True))

    def test_logging_in_comes_first_and_the_right_to_create_tokens_is_needed(self):
        client_id = self.client_id()
        url = "/mcp/oauth/authorize?" + urllib.parse.urlencode(self.params(client_id, pkce()[1]))
        r = fixture.loco.app.test_client().get(url)
        self.assertEqual(r.status_code, 302)
        location = urllib.parse.urlsplit(r.headers["Location"])                         # encodage selon la version de Werkzeug
        self.assertEqual(location.path, "/login")
        self.assertTrue(urllib.parse.parse_qs(location.query)["next"][0].startswith("/mcp/oauth/authorize?response_type=code&"))
        reader = self.operator()
        self.assertEqual(reader.c.get(url).status_code, 200)
        with transaction() as conn:
            conn.execute("UPDATE access_roles SET permissions=? WHERE id='ia'", (json.dumps(["fleet.view"]),))
        self.assertEqual(reader.c.get(url).status_code, 403)

    def test_the_token_follows_the_account_like_any_mcp_token(self):
        browser = self.operator()
        client_id = self.client_id()
        verifier, challenge = pkce()
        _r, target = self.consent(self.params(client_id, challenge), browser=browser,
                                  tools=["list_devices", "update_device", "reboot_device"])
        self.assertIsNone(target)                                       # outil hors des droits de marie : formulaire forgé refusé
        _r, target = self.consent(self.params(client_id, challenge), browser=browser, tools=["list_devices", "update_device"])
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)["code"][0]
        token = self.exchange(client_id, code, verifier).get_json()["access_token"]
        self.assertEqual(sorted(Client(token).names()), ["list_devices", "update_device"])
        with transaction() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE username='marie'")
        self.assertEqual(Client(token).request("tools/list").status_code, 401)


class TestRevocation(OAuthTest):
    def test_the_application_can_revoke_its_own_token_only(self):
        client_id = self.client_id()
        verifier, challenge = pkce()
        token = self.exchange(client_id, self.code(client_id, challenge), verifier).get_json()["access_token"]
        from test_mcp import create
        static, _r = create(self.admin, "Jeton fixe", ["list_devices"])
        for value in (static, "n'importe quoi"):
            self.assertEqual(self.c.post("/mcp/oauth/revoke", data={"token": value}).status_code, 200)
        self.assertEqual(Client(static).request("tools/list").status_code, 200)              # pas un jeton d'application
        self.assertEqual(self.c.post("/mcp/oauth/revoke", data={"token": token, "client_id": client_id}).status_code, 200)
        self.assertEqual(Client(token).request("tools/list").status_code, 401)
        self.assertIn("mcp.acces.revocation", self.actions())


class TestLinksToTheApplications(OAuthTest):
    def links(self):
        page = html.unescape(self.admin.c.get("/accounts/mcp").get_data(as_text=True))
        claude = re.search(r'href="(claude://code/new\?q=[^"]+)"', page).group(1)
        codex = re.search(r'href="(codex://new\?prompt=[^"]+)"', page).group(1)
        return (urllib.parse.parse_qs(urllib.parse.urlsplit(claude).query)["q"][0],
                urllib.parse.parse_qs(urllib.parse.urlsplit(codex).query)["prompt"][0], page)

    def test_the_page_opens_claude_and_codex_with_the_request_prefilled_and_no_token(self):
        claude, codex, page = self.links()
        endpoint = mcp_oauth.resource()
        self.assertIn(f"claude mcp add --scope user --transport http loco {endpoint}", claude)
        self.assertIn("claude mcp login loco", claude)
        self.assertIn(f"codex mcp add loco --url {endpoint}", codex)
        self.assertIn("codex mcp login loco", codex)
        for text in (claude, codex):
            self.assertNotIn("loco_mcp_", text)
            self.assertNotIn("empreinte", text)                          # certificat d'une autre autorité (ou aucun ici)
        self.assertIn(f"claude mcp add --scope user --transport http loco {endpoint}\nclaude mcp login loco", page)

    def test_an_address_by_ip_warns_that_codex_needs_a_name(self):
        self.assertNotIn("Codex refuse", self.links()[2])
        with mock.patch.object(config, "portal_url", return_value="https://192.168.1.20:8443"):
            page = self.links()[2]
            self.assertTrue(mcp_oauth.by_ip())
        self.assertIn("Codex refuse de se connecter par loco quand loco est joint par une adresse IP", page)
        with mock.patch.object(config, "portal_url", return_value="https://[fd00::20]:8443"):
            self.assertTrue(mcp_oauth.by_ip())

    def test_the_authority_of_loco_is_given_with_its_fingerprint_when_it_signed_the_certificate(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        folder = tempfile.mkdtemp(prefix="loco-tls-")
        self.addCleanup(shutil.rmtree, folder, True)
        now = datetime.datetime.now(datetime.timezone.utc)

        def certificate(subject, issuer, key, signer, ca):
            builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                       .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=9))
                       .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
            return builder.sign(signer, hashes.SHA256())
        ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "loco CA essai")])
        ca = certificate(ca_name, ca_name, ca_key, ca_key, True)
        server = certificate(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "loco.essai")]), ca_name, server_key, ca_key, False)
        for name, cert in (("ca.crt", ca), ("server.crt", server)):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        with mock.patch.object(config, "TLS_DIR", folder):
            claude, codex, _page = self.links()
        fingerprint = ca.fingerprint(hashes.SHA256()).hex().upper()
        expected = ":".join(fingerprint[i:i + 2] for i in range(0, 64, 2))
        for text, variable in ((claude, "NODE_EXTRA_CA_CERTS"), (codex, "CODEX_CA_CERTIFICATE")):
            self.assertIn(expected, text)
            self.assertIn(variable, text)
        other = ec.generate_private_key(ec.SECP256R1())
        alien = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Autorité d'entreprise")])
        with open(os.path.join(folder, "server.crt"), "wb") as f:
            f.write(certificate(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "loco.essai")]), alien, server_key, other, False)
                    .public_bytes(serialization.Encoding.PEM))
        with mock.patch.object(config, "TLS_DIR", folder):
            claude, _codex, _page = self.links()
        self.assertNotIn(expected, claude)


if __name__ == "__main__":
    unittest.main()
