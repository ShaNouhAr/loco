"""Configuration automatique d'Authentik : le jeton ne part qu'après le certificat, n'est gardé nulle part, aucun doublon, changements
montrés avant d'être appliqués, aucune URL de retour supprimée sans confirmation. Faux Authentik reproduisant l'API relevée sur 2026.8.5."""
import html
import json
import re
import unittest
from unittest import mock
import urllib.parse

from test_oidc import OidcTest
from test_sso_check import INTERMEDIATE, LEAF, Provider, ROOT, issue, pem
from loco import authentik_setup as setup, oidc
from loco.db import transaction

BASE, TOKEN = "https://sso.lab.local:9443", "jeton-api-tres-secret-0123456789"
CLIENT_SECRET = "secret-client-genere-par-authentik"
RSA = {"pk": "key-rsa", "name": setup.DEFAULT_KEY, "key_type": "rsa"}


class FakeAuthentik:
    """API v3 en mémoire. Comme le vrai : 403 pour un jeton inconnu, liste d'applications filtrée, lecture directe fiable."""
    def __init__(self):
        self.providers, self.applications, self.calls = {}, {}, []
        self.keys, self.next_pk = [RSA], 1

    def __call__(self, cfg, url, data=None, headers=None, method=None):
        method = method or ("POST" if data is not None else "GET")
        path = url.split("/api/v3/", 1)[-1] if "/api/v3/" in url else url
        self.calls.append((method, path, dict(headers or {}), data))
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": cfg["issuer"]}
        if (headers or {}).get("Authorization") != "Bearer " + TOKEN:
            raise oidc.OIDCError("HTTP 403", status=403, body={"detail": "Token invalid/expired"})
        body = json.loads(data) if data else None
        route, _, query = path.partition("?")
        args = dict(urllib.parse.parse_qsl(query))
        if route == "core/users/me/":
            return {"user": {"username": "akadmin"}}
        if route.startswith("core/applications/") and route != "core/applications/":
            slug = route.split("/")[2]
            if slug not in self.applications:
                raise oidc.OIDCError("HTTP 404", status=404)
            if method == "PATCH":
                self.applications[slug].update(body)
            return self.applications[slug]
        if route == "core/applications/":
            if body["slug"] in self.applications:
                raise oidc.OIDCError("HTTP 400", status=400, body={"slug": ["application with this slug already exists."]})
            self.applications[body["slug"]] = dict(body, pk="app")
            return self.applications[body["slug"]]
        if route == "providers/oauth2/" and method == "GET":
            return {"results": [p for p in self.providers.values() if p["name"] == args.get("name")]}
        if route == "providers/oauth2/":
            pk = self.next_pk
            self.next_pk += 1
            self.providers[pk] = dict(body, pk=pk, client_id=f"client-{pk}", client_secret=CLIENT_SECRET)
            return self.providers[pk]
        if route.startswith("providers/oauth2/"):
            pk = int(route.split("/")[2])
            if pk not in self.providers:
                raise oidc.OIDCError("HTTP 404", status=404)
            if method == "PATCH":
                self.providers[pk].update(body)
            return self.providers[pk]
        if route == "flows/instances/":
            return {"results": [{"pk": "flow-" + args["slug"]}]}
        if route == "crypto/certificatekeypairs/":
            return {"results": self.keys}
        if route == "propertymappings/provider/scope/":
            return {"results": [{"pk": "scope-" + m[-5:], "managed": m} for m in setup.WANTED_SCOPES]}
        raise AssertionError(path)

    def existing(self, **provider):
        self.providers[1] = {"pk": 1, "name": "loco (loco)", "client_id": "client-1", "client_secret": CLIENT_SECRET, "client_type": "confidential",
                             "grant_types": ["authorization_code"], "signing_key": "key-rsa", "issuer_mode": "per_provider",
                             "property_mappings": ["scope-" + m[-5:] for m in setup.WANTED_SCOPES], "redirect_uris": [], **provider}
        self.applications["loco"] = {"pk": "app", "slug": "loco", "name": "loco", "provider": 1}
        self.next_pk = 2


class SetupTest(OidcTest):
    def setUp(self):
        super().setUp()
        self.ak = FakeAuthentik()
        for patcher in (mock.patch.object(setup, "_json", self.ak), mock.patch.object(setup, "inspect", return_value={"status": "trusted", "ca_pem": ""}),
                        mock.patch.object(setup, "metadata", return_value={"issuer": BASE + "/application/o/loco/"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, **data):
        form = {"url": BASE, "slug": "loco", "label": "Authentik", **data}
        form["csrf"] = self.admin.token("/accounts/directories/authentik")
        return self.admin.c.post("/accounts/directories/authentik", data=form)

    def to_plan(self, token=TOKEN, **data):
        r = self.send(stage="plan", token=token, **data)
        return r, html.unescape(r.get_data(as_text=True))

    def field(self, page, name):
        return re.search(rf'name="{name}"[^>]*?value="([^"]*)"', page).group(1)

    def apply(self, page, **data):
        return self.send(stage="apply", sealed=self.field(page, "sealed"), signature=self.field(page, "signature"), new_id=self.field(page, "new_id"), enabled="1", **data)

    def directories(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM auth_directories").fetchall()


class TestCreation(SetupTest):
    def test_everything_asked_is_created_and_the_client_saved(self):
        _r, page = self.to_plan()
        self.assertIn("Créer le fournisseur", page)
        self.assertEqual(self.ak.providers, {})                  # rien de créé tant que ce n'est pas confirmé
        r = self.apply(page)
        self.assertEqual(r.status_code, 302)
        provider = self.ak.providers[1]
        self.assertEqual((provider["client_type"], provider["grant_types"]), ("confidential", ["authorization_code"]))
        self.assertEqual(provider["signing_key"], "key-rsa")
        self.assertEqual(len(provider["property_mappings"]), 3)
        self.assertEqual(provider["redirect_uris"][0]["matching_mode"], "strict")
        row = self.directories()[0]
        cfg = json.loads(row["config"])
        self.assertEqual((cfg["client_id"], cfg["issuer"], cfg["preset"]), ("client-1", BASE + "/application/o/loco/", "authentik"))
        self.assertTrue(provider["redirect_uris"][0]["url"].endswith(f"/login/sso/{row['id']}/callback"))
        self.assertIn(f"verifier={row['id']}", r.headers["Location"])
        self.assertNotIn(CLIENT_SECRET, row["config"])           # secret chiffré
        self.assertEqual(self.ak.applications["loco"]["provider"], 1)

    def test_a_new_provider_keeps_the_same_return_url_between_the_steps(self):
        # Trouvé en réel : un identifiant retiré à chaque étape changeait l'URL de retour et bloquait l'application.
        _r, page = self.to_plan()
        first = self.field(page, "new_id")
        self.assertEqual(self.apply(page).status_code, 302)
        self.assertEqual(self.directories()[0]["id"], first)

    def test_no_rsa_key_is_explained(self):
        self.ak.keys = [{"pk": "ec", "name": "ec", "key_type": "ecdsa"}]
        _r, page = self.to_plan()
        self.assertIn("Aucune clé RSA", page)


class TestNoDuplicate(SetupTest):
    def test_running_twice_creates_nothing_more(self):
        _r, page = self.to_plan()
        self.apply(page)
        _r, page = self.to_plan()
        self.assertIn("Rien à changer", page)
        self.assertEqual(self.apply(page).status_code, 302)
        self.assertEqual((len(self.ak.providers), len(self.ak.applications), len(self.directories())), (1, 1, 1))

    def test_an_existing_application_is_found_even_if_the_list_would_hide_it(self):
        # Relevé en réel : la liste core/applications/?slug= peut omettre une application existante.
        self.ak.existing()
        _r, page = self.to_plan()
        self.assertNotIn("Créer l’application", page)
        self.assertFalse([c for c in self.ak.calls if c[1].startswith("core/applications/?")])

    def test_a_provider_left_alone_by_an_interrupted_run_is_reused(self):
        self.ak.existing()
        del self.ak.applications["loco"]
        _r, page = self.to_plan()
        self.assertNotIn("Créer le fournisseur", page)
        self.assertIn("Créer l’application", page)
        self.apply(page)
        self.assertEqual(len(self.ak.providers), 1)
        self.assertEqual(self.ak.applications["loco"]["provider"], 1)

    def test_an_application_with_another_kind_of_provider_is_refused(self):
        self.ak.applications["loco"] = {"slug": "loco", "provider": 99}
        _r, page = self.to_plan()
        self.assertIn("autre type de fournisseur", page)


class TestChanges(SetupTest):
    def test_changes_are_shown_first_and_applied_only_on_confirmation(self):
        self.ak.existing(client_type="public", grant_types=["authorization_code", "refresh_token"], signing_key=None, issuer_mode="global", property_mappings=[])
        _r, page = self.to_plan()
        for text in ("public → confidentiel", "authorization_code, refresh_token → authorization_code seul", "Signature : clé", "Portées à ajouter", "URL de retour à ajouter", "Émetteur propre"):
            self.assertIn(text, page)
        self.assertEqual(self.ak.providers[1]["client_type"], "public")
        self.apply(page)
        provider = self.ak.providers[1]
        self.assertEqual((provider["client_type"], provider["grant_types"], provider["signing_key"], provider["issuer_mode"]),
                         ("confidential", ["authorization_code"], "key-rsa", "per_provider"))

    def test_client_id_and_secret_are_kept_not_rotated(self):
        self.ak.existing()
        _r, page = self.to_plan()
        self.apply(page)
        self.assertEqual(self.ak.providers[1]["client_id"], "client-1")
        self.assertEqual(self.ak.providers[1]["client_secret"], CLIENT_SECRET)
        self.assertEqual(json.loads(self.directories()[0]["config"])["client_id"], "client-1")

    def test_other_scopes_are_kept(self):
        self.ak.existing(property_mappings=["scope-groups"])
        _r, page = self.to_plan()
        self.apply(page)
        self.assertEqual(self.ak.providers[1]["property_mappings"][0], "scope-groups")
        self.assertEqual(len(self.ak.providers[1]["property_mappings"]), 4)

    def test_a_return_url_is_never_removed_without_confirmation(self):
        self.ak.existing(redirect_uris=[{"matching_mode": "strict", "url": "https://ancienne.lab/cb"}])
        _r, page = self.to_plan()
        self.assertIn("https://ancienne.lab/cb", page)
        self.apply(page)
        urls = [u["url"] for u in self.ak.providers[1]["redirect_uris"]]
        self.assertIn("https://ancienne.lab/cb", urls)
        self.assertEqual(len(urls), 2)

    def test_a_return_url_ticked_is_removed_and_others_stay(self):
        self.ak.existing(redirect_uris=[{"matching_mode": "strict", "url": "https://a.lab/cb"}, {"matching_mode": "regex", "url": "https://b.lab/.*"}])
        _r, page = self.to_plan()
        self.apply(page, remove_uri="https://a.lab/cb")
        urls = [u["url"] for u in self.ak.providers[1]["redirect_uris"]]
        self.assertNotIn("https://a.lab/cb", urls)
        self.assertIn("https://b.lab/.*", urls)

    def test_an_unknown_url_cannot_be_smuggled_in_the_removal(self):
        self.ak.existing(redirect_uris=[{"matching_mode": "strict", "url": "https://a.lab/cb"}])
        _r, page = self.to_plan()
        self.apply(page, remove_uri="https://autre.lab/cb")
        self.assertIn("https://a.lab/cb", [u["url"] for u in self.ak.providers[1]["redirect_uris"]])

    def test_a_loose_return_url_becomes_strict(self):
        _r, page = self.to_plan()
        callback = re.search(r"URL de retour stricte : (https://\S+?/callback)", page).group(1)
        ident = self.field(page, "new_id")
        self.ak.existing(redirect_uris=[{"matching_mode": "regex", "url": callback}])
        _r, page = self.to_plan(new_id=ident)
        self.assertIn("en mode strict", page)
        self.apply(page)
        self.assertEqual(self.ak.providers[1]["redirect_uris"], [{"matching_mode": "strict", "url": callback}])

    def test_if_authentik_changed_since_the_plan_nothing_is_applied_blindly(self):
        self.ak.existing()
        _r, page = self.to_plan()
        self.ak.providers[1]["client_type"] = "public"
        r = self.apply(page)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.ak.providers[1]["client_type"], "public")
        self.assertIn("public → confidentiel", html.unescape(r.get_data(as_text=True)))


class TestToken(SetupTest):
    def test_the_token_goes_only_in_the_authorization_header_to_this_address(self):
        _r, page = self.to_plan()
        self.apply(page)
        for _method, path, headers, data in self.ak.calls:
            self.assertNotIn(TOKEN, path)
            self.assertNotIn(TOKEN, (data or b"").decode())
            if headers.get("Authorization"):
                self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)

    def test_the_token_is_nowhere_in_pages_database_or_journal(self):
        r, page = self.to_plan()
        self.assertNotIn(TOKEN, page)
        self.assertNotIn(TOKEN, r.headers.get("Location", ""))
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        r = self.apply(page)
        self.assertNotIn(TOKEN, r.get_data(as_text=True))
        with transaction() as conn:
            dump = "\n".join(str(tuple(row)) for table in ("audit", "auth_directories", "settings") for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn(CLIENT_SECRET, dump)

    def test_the_journal_says_what_was_done(self):
        _r, page = self.to_plan()
        self.apply(page)
        with transaction() as conn:
            row = conn.execute("SELECT detail FROM audit WHERE action='sso.authentik' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("création", row["detail"])

    def test_the_sealed_token_expires_and_cannot_be_forged(self):
        sealed = setup.seal_token(TOKEN)
        self.assertNotIn(TOKEN, sealed)
        self.assertEqual(setup.open_token(sealed), TOKEN)
        for bad in ("", "abc", sealed[:-4] + "AAAA", TOKEN):
            with self.assertRaises(setup.SetupError):
                setup.open_token(bad)
        with mock.patch.object(setup, "TOKEN_TTL", -1), self.assertRaises(setup.SetupError):
            setup.open_token(sealed)

    def test_an_expired_token_asks_for_it_again(self):
        self.ak.existing()
        _r, page = self.to_plan()
        r = self.send(stage="apply", sealed="périmé", signature=self.field(page, "signature"))
        self.assertEqual(r.status_code, 400)
        self.assertIn('name="token"', r.get_data(as_text=True))
        self.assertIn("expiré", html.unescape(r.get_data(as_text=True)))

    def test_a_refused_token_is_explained_and_never_shown(self):
        r, page = self.to_plan(token="faux-jeton-bien-visible")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Jeton refusé", page)
        self.assertNotIn("faux-jeton-bien-visible", page)

    def test_an_api_error_never_repeats_the_token(self):
        def fail(cfg, url, data=None, headers=None, method=None):
            raise oidc.OIDCError(f"erreur avec {TOKEN}", status=400, body={"name": [f"valeur {TOKEN}"]})
        with mock.patch.object(setup, "_json", fail):
            _r, page = self.to_plan()
        self.assertNotIn(TOKEN, page)
        self.assertIn("•••", page)

    def test_bad_tokens_are_refused_before_any_call(self):
        for bad in ("", "a b", "a\nb"):
            self.ak.calls.clear()
            _r, page = self.to_plan(token=bad)
            self.assertEqual(self.ak.calls, [], repr(bad))
            self.assertIn("Jeton API requis", page)


class TestCertificateFirst(SetupTest):
    """Le jeton ne part jamais vers un serveur dont le certificat n'est pas reconnu ou confirmé."""
    def test_nothing_is_sent_while_the_certificate_is_not_trusted(self):
        for status in ({"status": "confirm", "ca_pem": pem(ROOT[0]), "subject": "Racine", "sha": "AA:BB", "self_signed": False},
                       {"status": "refused", "step": {"detail": "Racine manquante", "fix": "Collez"}, "paste": True},
                       {"status": "unreachable", "step": {"detail": "Ce serveur ne résout pas", "fix": "DNS"}}):
            with mock.patch.object(setup, "inspect", return_value=status):
                self.ak.calls.clear()
                _r, page = self.to_plan()
                self.assertEqual(self.ak.calls, [])
                self.assertNotIn('name="token"', page)

    def test_a_pasted_authority_needs_its_fingerprint_confirmed_before_the_token_is_sent(self):
        tls = {"status": "trusted", "ca_pem": pem(ROOT[0]), "subject": "Racine Lab", "sha": "AA:BB:CC"}
        with mock.patch.object(setup, "inspect", return_value=tls):
            r, page = self.to_plan(ca_pem=pem(ROOT[0]))
            self.assertEqual(r.status_code, 400)
            self.assertEqual(self.ak.calls, [])
            self.assertIn("Confirmez l’empreinte", page)
            self.assertIn("AA:BB:CC", page)
            _r, page = self.to_plan(ca_pem=pem(ROOT[0]), confirmed="DD:EE:FF")   # empreinte d'un autre certificat
            self.assertEqual(self.ak.calls, [])
            _r, page = self.to_plan(ca_pem=pem(ROOT[0]), confirmed="AA:BB:CC")
            self.assertTrue(self.ak.calls)
            self.assertIn("Ce qui sera fait", page)
            self.apply(page, ca_pem=pem(ROOT[0]), confirmed="AA:BB:CC")
        self.assertEqual(json.loads(self.directories()[0]["config"])["ca_pem"].strip(), pem(ROOT[0]).strip())

    def test_the_first_step_never_asks_for_the_token_before_the_check(self):
        with mock.patch.object(setup, "inspect", return_value={"status": "confirm", "ca_pem": pem(ROOT[0]), "subject": "Racine Lab", "sha": "AA:BB:CC", "self_signed": False}):
            page = html.unescape(self.send(stage="inspect").get_data(as_text=True))
        self.assertIn("AA:BB:CC", page)
        self.assertIn("Racine Lab", page)
        self.assertNotIn('name="token"', page)


class TestRealTls(unittest.TestCase):
    """inspect() contre un vrai serveur TLS local."""
    def setUp(self):
        oidc._cache.clear()

    def serve(self, certs, key):
        return f"https://localhost:{Provider(self, certs, key).port}"

    def test_a_root_sent_by_the_server_is_proposed_with_its_fingerprint(self):
        found = setup.inspect(self.serve((LEAF[0], INTERMEDIATE[0], ROOT[0]), LEAF[1]))
        self.assertEqual(found["status"], "confirm")
        self.assertEqual(found["subject"], "Racine Lab")
        self.assertEqual(found["ca_pem"].strip(), pem(ROOT[0]).strip())
        self.assertRegex(found["sha"], r"^[0-9A-F:]{95}$")

    def test_the_proposed_root_then_validates_the_server(self):
        base = self.serve((LEAF[0], INTERMEDIATE[0], ROOT[0]), LEAF[1])
        found = setup.inspect(base, setup.inspect(base)["ca_pem"])
        self.assertEqual(found["status"], "trusted")
        self.assertEqual(found["subject"], "Racine Lab")

    def test_a_self_signed_certificate_can_be_pinned(self):
        alone = issue("authentik default certificate", sans=("localhost",))
        base = self.serve((alone[0],), alone[1])
        found = setup.inspect(base)
        self.assertEqual((found["status"], found["self_signed"]), ("confirm", True))
        self.assertEqual(setup.inspect(base, found["ca_pem"])["status"], "trusted")

    def test_a_missing_root_must_be_pasted(self):
        found = setup.inspect(self.serve((LEAF[0], INTERMEDIATE[0]), LEAF[1]))
        self.assertEqual(found["status"], "refused")
        self.assertTrue(found["paste"])
        self.assertIn("Racine manquante", found["step"]["detail"])

    def test_a_name_not_covered_is_not_something_to_confirm(self):
        other = issue("sso", INTERMEDIATE, sans=("autre.lab.local",))
        found = setup.inspect(self.serve((other[0], INTERMEDIATE[0]), other[1]), pem(ROOT[0]))
        self.assertEqual(found["status"], "refused")
        self.assertNotIn("paste", found)
        self.assertIn("autre.lab.local", found["step"]["detail"])

    def test_a_closed_port(self):
        self.assertEqual(setup.inspect("https://127.0.0.1:9")["status"], "unreachable")


class TestBlueprint(SetupTest):
    def test_the_blueprint_has_everything_and_no_secret(self):
        page = html.unescape(self.send(stage="blueprint").get_data(as_text=True))
        yaml = re.search(r"<textarea readonly[^>]*>(.*?)</textarea>", page, re.S).group(1)
        for text in ("client_type: confidential", "grant_types: [authorization_code]", "matching_mode: strict", "authentik_providers_oauth2.oauth2provider",
                     'slug: "loco"', "scope-openid", "scope-profile", "scope-email", setup.DEFAULT_KEY, "issuer_mode: per_provider", "!KeyOf loco-provider"):
            self.assertIn(text, yaml)
        self.assertNotIn("client_secret", yaml)
        self.assertEqual(self.ak.calls, [])                      # aucun appel à Authentik dans ce mode

    def test_the_values_to_enter_are_given_and_the_form_saves_them(self):
        page = html.unescape(self.send(stage="blueprint").get_data(as_text=True))
        self.assertEqual(self.field(page, "issuer"), BASE + "/application/o/loco/")
        client_id, ident = self.field(page, "client_id"), self.field(page, "new_id")
        self.assertIn(client_id, page[page.index("<textarea readonly"):])
        self.assertIn(f"/login/sso/{ident}/callback", page[page.index("<textarea readonly"):])
        r = self.admin.post("/accounts/directories/add", {"kind": "oidc", "preset": "authentik", "issuer": BASE + "/application/o/loco/", "client_id": client_id,
                            "client_secret": CLIENT_SECRET, "new_id": ident, "label": "Authentik", "enabled": "1"}, page="/accounts/directories")
        self.assertIn(f"verifier={ident}", r.headers["Location"])
        self.assertEqual(self.directories()[0]["id"], ident)

    def test_values_cannot_break_out_of_the_yaml(self):
        text = setup.blueprint("loco", 'nom"\n  injecté: oui', "https://loco.lab/cb", "client")
        self.assertIn(r'name: "nom\"\n  injecté: oui"', text.replace("\\u00e9", "é"))


class TestInputs(SetupTest):
    def test_addresses_and_identifiers_are_checked(self):
        for url in ("http://sso.lab", "https://sso.lab/chemin", "https://u:p@sso.lab", "sso.lab", "https://sso.lab:99999", "https://sso.lab?x=1"):
            self.assertEqual(self.send(stage="inspect", url=url).status_code, 400, url)
        for slug in ("", "A B", "../admin", "a/b", "-x"):
            self.assertEqual(self.send(stage="inspect", slug=slug).status_code, 400, slug)
        self.assertEqual(self.send(stage="inconnue").status_code, 400)

    def test_it_needs_the_right_to_manage_sso(self):
        from loco.permissions import ENDPOINTS
        self.assertEqual(ENDPOINTS["directory_authentik"], "sso.manage")

    def test_the_page_is_reachable_from_directories(self):
        self.assertIn("/accounts/directories/authentik", self.admin.c.get("/accounts/directories").get_data(as_text=True))
        self.assertEqual(self.admin.c.get("/accounts/directories/authentik").status_code, 200)


if __name__ == "__main__":
    unittest.main()
