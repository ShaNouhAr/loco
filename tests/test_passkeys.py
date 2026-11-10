"""Clés d'accès (passkeys, WebAuthn) : enregistrement dans Mon compte, connexion sans mot de passe, refus de toute
réponse qui ne vient pas du bon site, du bon appareil ou de la bonne personne.

Un authentificateur logiciel joue le rôle du téléphone ou de la clé de sécurité : il fabrique de vraies réponses
WebAuthn (clientDataJSON, données de l'authentificateur, attestation CBOR, signature), que loco vérifie pour de bon."""
import base64
import hashlib
import json
import os
import re
import struct
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

import test_portal as fixture
from loco import auth, cli, config, passkeys
from loco.db import transaction

ORIGIN = "https://repo.test:8443"           # adresse du portail du montage (LOCO_ADMIN_URL)
PW = "Cle-Password-2026"


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor(value):
    """Encodeur CBOR minimal, pour fabriquer les réponses de l'authentificateur."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError(n)
    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(value)


class Authenticator:
    """Appareil de la personne : une clé par site, la personne vérifiée (empreinte, code) à chaque usage."""

    def __init__(self, alg=-7, rp="repo.test", origin=ORIGIN, counter=0, synced=False):
        self.alg, self.rp, self.origin, self.counter, self.synced = alg, rp, origin, counter, synced
        self.credential = os.urandom(32)
        self.flags = 0x05                               # personne présente et vérifiée
        self.handle = ""
        if alg == -7:
            self.key = ec.generate_private_key(ec.SECP256R1())
        elif alg == -8:
            self.key = ed25519.Ed25519PrivateKey.generate()
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def cose(self):
        public = self.key.public_key()
        if self.alg == -7:
            n = public.public_numbers()
            return {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        if self.alg == -8:
            return {1: 1, 3: -8, -1: 6, -2: public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)}
        n = public.public_numbers()
        return {1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")}

    def sign(self, data):
        if self.alg == -7:
            return self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == -8:
            return self.key.sign(data)
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    def client_data(self, kind, challenge, **extra):
        return json.dumps(dict({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}, **extra)).encode()

    def auth_data(self, attested=False):
        flags = self.flags | (0x18 if self.synced else 0) | (0x40 if attested else 0)
        data = hashlib.sha256(self.rp.encode()).digest() + bytes([flags]) + struct.pack(">I", self.counter)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential)) + self.credential + cbor(self.cose())
        return data

    def create(self, options, **client):
        """Réponse à navigator.credentials.create : nouvelle clé pour ce site."""
        self.handle = options["user"]["id"]
        attestation = cbor({"fmt": "none", "attStmt": {}, "authData": self.auth_data(attested=True)})
        return {"id": b64(self.credential), "response": {
            "clientDataJSON": b64(self.client_data("webauthn.create", options["challenge"], **client)),
            "attestationObject": b64(attestation), "transports": ["internal", "hybrid"]}}

    def get(self, options, **client):
        """Réponse à navigator.credentials.get : la personne se connecte avec cette clé."""
        if self.counter:
            self.counter += 1
        client_data = self.client_data("webauthn.get", options["challenge"], **client)
        data = self.auth_data()
        return {"id": b64(self.credential), "response": {
            "clientDataJSON": b64(client_data), "authenticatorData": b64(data),
            "signature": b64(self.sign(data + hashlib.sha256(client_data).digest())), "userHandle": self.handle}}


class Site:
    """Navigateur ouvert sur l'adresse du portail (là où une clé d'accès est possible), ou ailleurs."""

    def __init__(self, base=ORIGIN):
        self.c, self.base = fixture.loco.app.test_client(), base

    def get(self, path):
        return self.c.get(path, base_url=self.base)

    def csrf(self, page="/login"):
        found = re.search(r'name="csrf" value="([^"]+)"', self.get(page).get_data(as_text=True))
        return found.group(1) if found else ""

    def post(self, path, data=None, page="/login"):
        return self.c.post(path, data=dict(data or {}, csrf=self.csrf(page)), base_url=self.base)

    def login(self, username="alice", password=PW):
        return self.post("/login", {"username": username, "password": password})

    def add_key(self, device, name="Téléphone", password=PW):
        """Ajoute la clé de l'appareil au compte connecté ; renvoie (options, réponse de l'ajout)."""
        options = self.post("/account/passkeys/options", {"password": password}, page="/account")
        if options.status_code != 200:
            return options, None
        return options, self.post("/account/passkeys/add", {"name": name, "response": json.dumps(device.create(options.get_json()))},
                                  page="/account")

    def passkey_login(self, device, alter=None, **client):
        options = self.post("/login/passkey/options")
        if options.status_code != 200:
            return options
        answer = device.get(options.get_json(), **client)
        if alter:
            alter(answer)
        return self.post("/login/passkey", {"response": json.dumps(answer)})

    def signed_in(self):
        return self.get("/account").status_code == 200


class PasskeyTest(unittest.TestCase):
    def setUp(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username IN ('alice', 'bob')")
            conn.execute("DELETE FROM passkeys")
            conn.execute("DELETE FROM passkey_challenges")
            conn.execute("DELETE FROM audit")
            auth.create_user(conn, "alice", PW, "admin", must_change=False)
        fixture.reset_failures()
        self.site = Site()
        self.assertEqual(self.site.login().status_code, 302)
        self.device = Authenticator()

    def tearDown(self):
        with transaction() as conn:                                       # base partagée par toute la suite : rien n'y reste
            conn.execute("DELETE FROM users WHERE username IN ('alice', 'bob')")
            conn.execute("DELETE FROM passkeys")
            conn.execute("DELETE FROM passkey_challenges")
        fixture.reset_failures()

    def keys(self, username="alice"):
        with transaction() as conn:
            return conn.execute("SELECT p.* FROM passkeys p JOIN users u ON u.id=p.user_id WHERE u.username=? ORDER BY p.id",
                                (username,)).fetchall()

    def audit(self, action):
        with transaction() as conn:
            return [r[0] for r in conn.execute("SELECT detail FROM audit WHERE action=? ORDER BY id", (action,))]

    def enrolled(self, device=None, name="Téléphone"):
        """Compte alice avec une clé d'accès, et un navigateur neuf (déconnecté)."""
        device = device or self.device
        _options, added = self.site.add_key(device, name)
        self.assertEqual(added.status_code, 200, added.get_data(as_text=True))
        return Site()


class TestCbor(unittest.TestCase):
    def test_what_webauthn_uses_is_read(self):
        value = {"fmt": "none", "attStmt": {}, "authData": b"\x01\x02", 1: -7, -3: [True, False], "n": 70000}
        decoded, end = passkeys.cbor(cbor(value))
        self.assertEqual(decoded, value)
        self.assertEqual(end, len(cbor(value)))

    def test_everything_else_is_refused(self):
        for bad in (b"", b"\x5f\x41\x00\xff",              # chaîne de longueur indéfinie
                    b"\xc0\x00",                           # étiquette
                    b"\xfb" + bytes(8),                    # flottant
                    b"\x58\x10\x00",                       # octets tronqués
                    b"\xa2\x01\x00\x01\x00",               # clé en double
                    b"\xa1\x80\x00",                       # clé qui n'est ni entier ni texte
                    b"\x81" * 12 + b"\x00",                # imbrication excessive
                    b"\x98\xff" + b"\x00" * 255):          # tableau démesuré
            with self.subTest(bad=bad.hex()), self.assertRaises(passkeys.Refused):
                passkeys.cbor(bad)


class TestWhereItWorks(PasskeyTest):
    def test_only_on_the_portal_address_and_never_on_an_ip_address(self):
        page = self.site.get("/account").get_data(as_text=True)
        self.assertIn("data-passkey-add", page)                           # sur l'adresse du portail : ajout proposé
        elsewhere = Site("https://192.168.43.2:8443")
        elsewhere.login()
        page = elsewhere.get("/account").get_data(as_text=True)
        self.assertNotIn("data-passkey-add", page)
        self.assertIn("ouvrez loco par l'adresse du portail : https://repo.test:8443", page)
        self.assertEqual(elsewhere.post("/account/passkeys/options", {"password": PW}, page="/account").status_code, 409)
        with mock.patch.object(config, "portal_url", return_value="https://192.168.43.2:8443"):
            self.assertEqual(passkeys.relying_party(), "")
            page = elsewhere.get("/account").get_data(as_text=True)
            self.assertIn("une adresse IP ne convient pas", page)
            self.assertNotIn("data-passkey-add", page)
        with mock.patch.object(config, "portal_url", return_value="http://loco.exemple:8080"):
            self.assertEqual(passkeys.relying_party(), "")                # jamais sans HTTPS

    def test_the_login_button_appears_once_an_account_has_a_key(self):
        fresh = Site()
        self.assertNotIn("data-passkey-login", fresh.get("/login").get_data(as_text=True))
        self.enrolled()
        self.assertIn("data-passkey-login", fresh.get("/login").get_data(as_text=True))
        self.assertIn("passkeys.js", fresh.get("/login").get_data(as_text=True))
        self.assertNotIn("data-passkey-login", Site("https://192.168.43.2:8443").get("/login").get_data(as_text=True))
        self.assertEqual(Site("https://192.168.43.2:8443").post("/login/passkey/options").status_code, 404)


class TestRegistration(PasskeyTest):
    def test_the_password_is_asked_again_and_the_options_demand_a_verified_resident_key(self):
        refused, _added = self.site.add_key(self.device, password="mauvais")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.keys(), [])
        options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        self.assertEqual(options["rp"], {"id": "repo.test", "name": "loco"})
        self.assertEqual(options["user"]["name"], "alice")
        self.assertEqual(len(unb64(options["user"]["id"])), 32)            # identifiant opaque, pas le nom du compte
        self.assertEqual(options["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(options["authenticatorSelection"]["userVerification"], "required")
        self.assertEqual(options["attestation"], "none")
        self.assertEqual([p["alg"] for p in options["pubKeyCredParams"]], [-7, -8, -257])

    def test_a_key_is_stored_with_its_public_key_only_and_listed(self):
        _options, added = self.site.add_key(self.device, "  Téléphone   de   test  ")
        self.assertEqual(added.get_json()["redirect"], "/account#securite")
        key = self.keys()[0]
        self.assertEqual((key["name"], key["credential_id"], key["alg"], key["sign_count"], key["transports"]),
                         ("Téléphone de test", b64(self.device.credential), -7, 0, "internal,hybrid"))
        stored = serialization.load_der_public_key(base64.b64decode(key["public_key"]))
        self.assertEqual(stored.public_numbers(), self.device.key.public_key().public_numbers())
        self.assertEqual(self.audit("compte.cle.ajout"), ["Téléphone de test"])
        page = self.site.get("/account").get_data(as_text=True)
        self.assertIn("Téléphone de test", page)
        self.assertIn("connexion sans mot de passe", page)

    def test_the_same_key_twice_and_too_many_keys_are_refused(self):
        self.site.add_key(self.device)
        options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        self.assertEqual(options["excludeCredentials"], [{"type": "public-key", "id": b64(self.device.credential)}])
        again = self.site.post("/account/passkeys/add", {"response": json.dumps(self.device.create(options))}, page="/account")
        self.assertEqual(again.status_code, 409)
        with mock.patch.object(passkeys, "MAX_KEYS", 1):
            full, _added = self.site.add_key(Authenticator())
        self.assertEqual(full.status_code, 409)
        self.assertEqual(len(self.keys()), 1)

    def test_a_registration_for_another_site_or_without_verification_is_refused(self):
        for label, device, client in (("autre origine", Authenticator(origin="https://faux.test:8443"), {}),
                                      ("autre site", Authenticator(rp="faux.test"), {}),
                                      ("cadre d'un autre site", Authenticator(), {"crossOrigin": True}),
                                      ("mauvais type", Authenticator(), {"type": "webauthn.get"})):
            with self.subTest(label):
                options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
                r = self.site.post("/account/passkeys/add", {"response": json.dumps(device.create(options, **client))}, page="/account")
                self.assertEqual(r.status_code, 400)
        unverified = Authenticator()
        unverified.flags = 0x01                                           # présente, mais pas vérifiée (ni empreinte ni code)
        options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": json.dumps(unverified.create(options))},
                                        page="/account").status_code, 400)
        self.assertEqual(self.keys(), [])
        self.assertEqual(len(self.audit("compte.cle.echec")), 5)

    def test_a_challenge_serves_once_and_only_for_its_purpose(self):
        options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        answer = json.dumps(self.device.create(options))
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": answer}, page="/account").status_code, 200)
        with transaction() as conn:
            conn.execute("DELETE FROM passkeys")
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": answer}, page="/account").status_code, 400)   # rejeu
        fresh = Site()
        login = fresh.post("/login/passkey/options").get_json()            # défi de connexion : inutilisable pour enregistrer
        other = Authenticator()
        other.handle = "x"
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": json.dumps(other.create(
            dict(options, challenge=login["challenge"])))}, page="/account").status_code, 400)

    def test_a_challenge_expires(self):
        options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        with transaction() as conn:
            conn.execute("UPDATE passkey_challenges SET created = created - ?", (passkeys.CHALLENGE_SECONDS + 5,))
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": json.dumps(self.device.create(options))},
                                        page="/account").status_code, 400)

    def test_pending_challenges_stay_bounded_and_a_request_without_csrf_is_refused(self):
        with mock.patch.object(passkeys, "MAX_CHALLENGES", 3):
            for _i in range(6):
                self.assertEqual(Site().post("/login/passkey/options").status_code, 200)
            options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM passkey_challenges").fetchone()[0], 3)
        self.assertEqual(self.site.post("/account/passkeys/add", {"response": json.dumps(self.device.create(options))},
                                        page="/account").status_code, 200)             # le plus récent est toujours là
        fresh = Site()
        self.assertEqual(fresh.c.post("/login/passkey/options", base_url=ORIGIN).status_code, 400)
        self.assertEqual(fresh.c.post("/login/passkey", data={"response": "{}"}, base_url=ORIGIN).status_code, 400)
        self.assertEqual(Site().c.post("/account/passkeys/options", data={"password": PW}, base_url=ORIGIN).status_code, 400)

    def test_accounts_that_cannot_have_a_key(self):
        with transaction() as conn:
            auth.create_user(conn, "bob", PW, "admin", must_change=True)
        bob = Site()
        bob.login("bob")
        self.assertEqual(bob.post("/account/passkeys/options", {"password": PW}, page="/account").status_code, 302)   # mot de passe provisoire : tout ramène à Mon compte
        self.assertNotIn("data-passkey-add", bob.get("/account").get_data(as_text=True))
        from flask import g
        from werkzeug.exceptions import Forbidden
        with fixture.loco.app.test_request_context("/account", base_url=ORIGIN):
            g.user = {"auth_source": "directory", "must_change": 0}            # compte d'annuaire : son mot de passe vit ailleurs
            with self.assertRaises(Forbidden):
                passkeys._own()
        self.assertEqual(self.keys("bob"), [])

    def test_all_three_algorithms_register_and_sign_in(self):
        for alg in (-7, -8, -257):
            with self.subTest(alg=alg):
                device = Authenticator(alg=alg)
                _options, added = self.site.add_key(device, f"alg {alg}")
                self.assertEqual(added.status_code, 200)
                fresh = Site()
                self.assertEqual(fresh.passkey_login(device).status_code, 200)
                self.assertTrue(fresh.signed_in())


class TestSignIn(PasskeyTest):
    def test_a_key_signs_in_without_password_nor_second_factor(self):
        with transaction() as conn:
            conn.execute("UPDATE users SET mfa_enabled=1, totp_secret='x' WHERE username='alice'")    # 2FA activée sur le compte
        fresh = self.enrolled()
        self.assertFalse(fresh.signed_in())
        r = fresh.passkey_login(self.device)
        self.assertEqual((r.status_code, r.get_json()), (200, {"redirect": "/"}))
        self.assertTrue(fresh.signed_in())
        self.assertEqual(self.audit("connexion.cle"), ["Téléphone"])
        self.assertTrue(self.keys()[0]["last_used"])

    def test_the_destination_asked_is_followed_but_never_another_site(self):
        fresh = self.enrolled()
        options = fresh.post("/login/passkey/options").get_json()
        r = fresh.post("/login/passkey", {"response": json.dumps(self.device.get(options)), "next": "/fleet"})
        self.assertEqual(r.get_json(), {"redirect": "/fleet"})
        other = Site()
        options = other.post("/login/passkey/options").get_json()
        r = other.post("/login/passkey", {"response": json.dumps(self.device.get(options)), "next": "https://faux.test/"})
        self.assertEqual(r.get_json(), {"redirect": "/"})

    def test_anything_that_is_not_this_key_on_this_site_is_refused(self):
        def tamper(field):
            def alter(answer):
                raw = bytearray(unb64(answer["response"][field]))
                raw[-1] ^= 1
                answer["response"][field] = b64(bytes(raw))
            return alter

        def no_verification(answer):
            raw = bytearray(unb64(answer["response"]["authenticatorData"]))
            raw[32] = 0x01
            answer["response"]["authenticatorData"] = b64(bytes(raw))

        def other_account(answer):
            answer["response"]["userHandle"] = b64(os.urandom(32))

        def unknown(answer):
            answer["id"] = b64(os.urandom(32))

        self.enrolled()
        cases = (("signature altérée", self.device, tamper("signature"), {}),
                 ("données altérées", self.device, tamper("authenticatorData"), {}),
                 ("personne non vérifiée", self.device, no_verification, {}),
                 ("autre compte", self.device, other_account, {}),
                 ("clé inconnue", self.device, unknown, {}),
                 ("cadre d'un autre site", self.device, None, {"crossOrigin": True}),
                 ("réponse d'enregistrement", self.device, None, {"type": "webauthn.create"}),
                 ("autre origine", None, None, {"origin": "https://faux.test:8443"}),
                 ("même nom, autre port", None, None, {"origin": "https://repo.test"}))
        for label, device, alter, client in cases:
            with self.subTest(label):
                fixture.reset_failures()
                fresh = Site()
                r = fresh.passkey_login(device or self.device, alter, **client)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.get_json(), {"error": "Cette clé d'accès n'a pas été acceptée."})    # jamais le motif
                self.assertFalse(fresh.signed_in())
        self.assertEqual(len(self.audit("connexion.echec")), len(cases))
        phishing = Authenticator(rp="faux.test")
        phishing.key, phishing.credential, phishing.handle = self.device.key, self.device.credential, self.device.handle
        fixture.reset_failures()
        self.assertEqual(Site().passkey_login(phishing).status_code, 401)                               # clé d'un autre site

    def test_malformed_answers_are_refused_without_an_error_page(self):
        self.enrolled()
        good = b64(self.device.credential)
        for label, answer in (("pas du JSON", "{"), ("une liste", "[]"), ("sans réponse", '{"id": "x"}'),
                              ("réponse vide", json.dumps({"id": good, "response": {}})),
                              ("mauvais types", json.dumps({"id": good, "response": {"clientDataJSON": 5, "authenticatorData": [], "signature": {},
                                                                                     "userHandle": ["x"]}})),
                              ("identifiant non textuel", json.dumps({"id": {"a": 1}, "response": {"clientDataJSON": "e30"}})),
                              ("base64 illisible", json.dumps({"id": good, "response": {"clientDataJSON": "@@@", "authenticatorData": "@", "signature": "@"}})),
                              ("données tronquées", json.dumps({"id": good, "response": {"clientDataJSON": "e30", "authenticatorData": "AAAA", "signature": "AA"}}))):
            with self.subTest(label):
                fixture.reset_failures()
                fresh = Site()
                fresh.post("/login/passkey/options")
                self.assertIn(fresh.post("/login/passkey", {"response": answer}).status_code, (400, 401))
                self.assertFalse(fresh.signed_in())
        for label, answer in (("pas du JSON", "nope"), ("réponse vide", '{"response": {}}'),
                              ("attestation qui n'est pas du CBOR", json.dumps({"response": {"clientDataJSON": "e30", "attestationObject": "_w"}})),
                              ("transports farfelus", None)):
            with self.subTest(label):
                options = self.site.post("/account/passkeys/options", {"password": PW}, page="/account").get_json()
                if answer is None:
                    made = Authenticator().create(options)
                    made["response"]["transports"] = 7
                    self.assertEqual(self.site.post("/account/passkeys/add", {"response": json.dumps(made)}, page="/account").status_code, 200)
                    self.assertEqual(self.keys()[-1]["transports"], "")
                else:
                    self.assertEqual(self.site.post("/account/passkeys/add", {"response": answer}, page="/account").status_code, 400)

    def test_a_response_cannot_be_replayed(self):
        fresh = self.enrolled()
        options = fresh.post("/login/passkey/options").get_json()
        answer = json.dumps(self.device.get(options))
        self.assertEqual(fresh.post("/login/passkey", {"response": answer}).status_code, 200)
        thief = Site()
        self.assertEqual(thief.post("/login/passkey", {"response": answer}).status_code, 401)           # sans défi
        thief.post("/login/passkey/options")
        self.assertEqual(thief.post("/login/passkey", {"response": answer}).status_code, 401)           # autre défi
        self.assertFalse(thief.signed_in())

    def test_a_counter_going_backwards_reveals_a_copied_key(self):
        device = Authenticator(counter=41)
        fresh = self.enrolled(device)
        self.assertEqual(fresh.passkey_login(device).status_code, 200)
        self.assertEqual(self.keys()[0]["sign_count"], 42)
        device.counter = 10                                               # copie restée en arrière
        self.assertEqual(Site().passkey_login(device).status_code, 401)
        self.assertIn("copiée", self.audit("connexion.echec")[-1])

    def test_a_disabled_or_directory_account_no_longer_signs_in(self):
        self.enrolled()
        for change in ("disabled=1", "pending=1", "auth_source='directory'"):
            with self.subTest(change):
                fixture.reset_failures()
                with transaction() as conn:
                    conn.execute(f"UPDATE users SET disabled=0, pending=0, auth_source='local', {change} WHERE username='alice'")
                self.assertEqual(Site().passkey_login(self.device).status_code, 401)

    def test_a_temporary_password_still_has_to_be_changed(self):
        fresh = self.enrolled()
        with transaction() as conn:
            conn.execute("UPDATE users SET must_change=1 WHERE username='alice'")
        self.assertEqual(fresh.passkey_login(self.device).get_json(), {"redirect": "/account"})

    def test_repeated_failures_are_throttled(self):
        self.enrolled()

        def broken(answer):
            answer["response"]["signature"] = b64(b"faux")

        codes = [Site().passkey_login(self.device, broken).status_code for _ in range(config.LOGIN_MAX_PER_USER + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(Site().passkey_login(self.device).status_code, 429)                           # même la bonne clé attend
        self.assertTrue(self.audit("connexion.bloquee"))


class TestRemoval(PasskeyTest):
    def test_the_owner_renames_and_removes_only_their_own_keys(self):
        self.enrolled()
        key = self.keys()[0]
        self.site.post(f"/account/passkeys/{key['id']}/rename", {"name": "Clé de bureau"}, page="/account")
        self.assertEqual(self.keys()[0]["name"], "Clé de bureau")
        with transaction() as conn:
            auth.create_user(conn, "bob", PW, "admin", must_change=False)
        bob = Site()
        bob.login("bob")
        self.assertEqual(bob.post(f"/account/passkeys/{key['id']}/delete", page="/account").status_code, 404)
        self.assertEqual(len(self.keys()), 1)
        self.site.post(f"/account/passkeys/{key['id']}/delete", page="/account")
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.audit("compte.cle.retrait"), ["Clé de bureau"])
        fixture.reset_failures()
        self.assertEqual(Site().passkey_login(self.device).status_code, 401)

    def test_an_administrator_removes_the_keys_of_an_account(self):
        self.enrolled()
        with transaction() as conn:
            auth.create_user(conn, "bob", PW, "admin", must_change=False)
            alice = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        bob = Site()
        bob.login("bob")
        self.assertIn("Retirer les clés d'accès", bob.get("/accounts").get_data(as_text=True))
        bob.post(f"/accounts/{alice}/passkeys-reset", page="/accounts")
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.audit("comptes.cles.reinitialisation"), ["alice : 1"])
        self.assertNotIn("Retirer les clés d'accès", bob.get("/accounts").get_data(as_text=True))

    def test_resetting_a_password_removes_the_keys_too(self):
        self.enrolled()
        with transaction() as conn:
            auth.create_user(conn, "bob", PW, "admin", must_change=False)
            alice = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        bob = Site()
        bob.login("bob")
        bob.post(f"/accounts/{alice}/reset", page="/accounts")
        self.assertEqual(self.keys(), [])
        self.assertIn("clés d'accès retirées : 1", self.audit("comptes.reinitialisation")[0])

    def test_deleting_the_account_or_the_command_line_removes_them(self):
        self.enrolled()
        with fixture.loco.app.app_context(), mock.patch("builtins.print"):
            cli.main(["user", "passkeys-reset", "alice"])
        self.assertEqual(self.keys(), [])
        self.site.add_key(Authenticator())
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='alice'")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0], 0)

    def test_pending_challenges_are_not_kept_in_backups(self):
        import locosnapshot
        import sqlite3
        self.site.post("/account/passkeys/options", {"password": PW}, page="/account")
        with tempfile.TemporaryDirectory() as folder:
            copy = os.path.join(folder, "portal.db")
            locosnapshot.snapshot(config.DB_PATH, copy)
            conn = sqlite3.connect(copy)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM passkey_challenges").fetchone()[0], 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
