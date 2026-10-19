"""Annuaires LDAP (LDAPS) et RADIUS : protocoles réels contre de faux serveurs, connexion, demande d'accès, approbation."""
import datetime
import hashlib
import hmac
import socket
import ssl
import struct
import tempfile
import threading
import unittest

import test_portal as fixture
from test_fleet import FleetTest
from loco import directory_protocols as proto
from loco import features
from loco.db import transaction

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def certificate():
    """Autorité de test et certificat du serveur 127.0.0.1 (PEM)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "annuaire-test-ca")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(key.public_key())
          .serial_number(1).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .sign(key, hashes.SHA256()))
    import ipaddress
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
              .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(2)
              .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
              .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .sign(key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM).decode()  # noqa: E731
    key_pem = server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode()
    return pem(ca), pem(server), key_pem


class FakeLdap(threading.Thread):
    """LDAPS minimal : liaisons, recherche d'un compte par uid, compte de service."""
    USERS = {"uid=alice,ou=people,dc=test": ("alice", "Mot-de-passe-Alice", "Alice Martin", "alice@test.local")}
    SERVICE = ("cn=svc,dc=test", "secret-service")

    def __init__(self):
        super().__init__(daemon=True)
        self.ca, cert, key = certificate()
        self.dir = tempfile.TemporaryDirectory()
        open(f"{self.dir.name}/c.pem", "w").write(cert + key)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(f"{self.dir.name}/c.pem")
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.start()

    def run(self):
        while True:
            try:
                raw, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(raw,), daemon=True).start()

    def serve(self, raw):
        try:
            conn = self.context.wrap_socket(raw, server_side=True)
            while True:
                tag, content = proto._read_message(conn)
                parts = proto.parse(content)
                msgid, (op, body) = parts[0], parts[1]
                ident = proto.integer(int.from_bytes(msgid[1], "big"))
                if op == 0x42:
                    return
                if op == 0x60:
                    fields = proto.parse(body)
                    dn, password = fields[1][1].decode(), fields[2][1].decode()
                    ok = (dn, password) == self.SERVICE or (dn in self.USERS and self.USERS[dn][1] == password)
                    result = proto.sequence(proto.integer(0 if ok else 49, tag=0x0a), proto.octets(""), proto.octets(""), tag=0x61)
                    conn.sendall(proto.sequence(ident, result))
                elif op == 0x63:
                    for dn, (uid, _pw, name, mail) in self.USERS.items():
                        if f"uid\x04\x05{uid}".encode() in body or uid.encode() in body:
                            attrs = proto.sequence(
                                proto.sequence(proto.octets("displayName"), proto.sequence(proto.octets(name), tag=0x31)),
                                proto.sequence(proto.octets("mail"), proto.sequence(proto.octets(mail), tag=0x31)))
                            conn.sendall(proto.sequence(ident, proto.sequence(proto.octets(dn), attrs, tag=0x64)))
                    done = proto.sequence(proto.integer(0, tag=0x0a), proto.octets(""), proto.octets(""), tag=0x65)
                    conn.sendall(proto.sequence(ident, done))
        except (OSError, proto.DirectoryError, ssl.SSLError, IndexError):
            return

    def stop(self):
        self.server.close()
        self.dir.cleanup()


class FakeRadius(threading.Thread):
    """Serveur RADIUS : décode le mot de passe PAP, répond signé (avec ou sans Message-Authenticator)."""
    def __init__(self, secret=b"secret-radius", with_mac=True, password="Mot-de-passe-Bob"):
        super().__init__(daemon=True)
        self.secret, self.with_mac, self.password = secret, with_mac, password
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.start()

    def run(self):
        while True:
            try:
                data, peer = self.sock.recvfrom(4096)
            except OSError:
                return
            ident, authenticator = data[1], data[4:20]
            attrs = {k: v for k, v, _o in proto._attributes(data[20:])}
            hidden, previous, clear = attrs[2], authenticator, b""
            for i in range(0, len(hidden), 16):
                key = hashlib.md5(self.secret + previous).digest()
                clear += bytes(a ^ b for a, b in zip(hidden[i:i + 16], key))
                previous = hidden[i:i + 16]
            ok = attrs[1] == b"bob" and clear.rstrip(b"\x00") == self.password.encode() and 80 in attrs
            code = 2 if ok else 3
            body = proto._attribute(18, b"Bienvenue" if ok else b"Refus")
            if self.with_mac:
                body += proto._attribute(80, b"\x00" * 16)
            header = struct.pack("!BBH", code, ident, 20 + len(body))
            if self.with_mac:
                mac = hmac.new(self.secret, header + authenticator + body, hashlib.md5).digest()
                body = body[:-16] + mac
            reply_auth = hashlib.md5(header + authenticator + body + self.secret).digest()
            self.sock.sendto(header + reply_auth + body, peer)

    def stop(self):
        self.sock.close()


class TestProtocols(unittest.TestCase):
    def test_filters_are_parsed_and_usernames_escaped(self):
        self.assertEqual(proto.escape_filter("a*b(c)\\"), "a\\2ab\\28c\\29\\5c")
        encoded = proto.encode_filter("(&(objectClass=user)(sAMAccountName=" + proto.escape_filter("x*") + "))")
        self.assertEqual(encoded[0], 0xa0)
        self.assertIn(b"x*", encoded)                                   # joker échappé : valeur littérale
        for bad in ("(cn=a*)", "cn=a", "(cn~=a)", "(&)"):
            with self.assertRaises(ValueError):
                proto.encode_filter(bad)

    def test_plain_ldap_and_empty_passwords_are_refused(self):
        with self.assertRaises(ValueError):
            proto.LdapConnection("ldap://127.0.0.1:1")                 # ni ldaps ni StartTLS
        server = FakeLdap()
        self.addCleanup(server.stop)
        cfg = {"url": f"ldaps://127.0.0.1:{server.port}", "ca_pem": server.ca, "base_dn": "dc=test",
               "user_filter": "(uid={username})", "bind_dn": "cn=svc,dc=test", "bind_password": "secret-service"}
        self.assertEqual(proto.ldap_authenticate(cfg, "alice", ""), (False, {}))      # jamais de liaison anonyme
        ok, info = proto.ldap_authenticate(cfg, "alice", "Mot-de-passe-Alice")
        self.assertEqual((ok, info), (True, {"display_name": "Alice Martin", "email": "alice@test.local"}))
        self.assertFalse(proto.ldap_authenticate(cfg, "alice", "mauvais")[0])
        self.assertFalse(proto.ldap_authenticate(cfg, "inconnu", "Mot-de-passe-Alice")[0])
        with self.assertRaises(proto.DirectoryError):                  # autorité inconnue : TLS refusé
            proto.ldap_authenticate(dict(cfg, ca_pem=""), "alice", "Mot-de-passe-Alice")
        direct = {"url": cfg["url"], "ca_pem": server.ca, "user_template": "uid={username},ou=people,dc=test"}
        self.assertTrue(proto.ldap_authenticate(direct, "alice", "Mot-de-passe-Alice")[0])

    def test_radius_answers_are_authenticated(self):
        server = FakeRadius()
        self.addCleanup(server.stop)
        cfg = {"host": "127.0.0.1", "port": server.port, "secret": "secret-radius", "timeout": 2, "retries": 0}
        self.assertTrue(proto.radius_authenticate(cfg, "bob", "Mot-de-passe-Bob")[0])
        self.assertFalse(proto.radius_authenticate(cfg, "bob", "mauvais")[0])
        with self.assertRaises(proto.DirectoryError):                  # autre secret : réponse non authentique
            proto.radius_authenticate(dict(cfg, secret="autre"), "bob", "Mot-de-passe-Bob")
        old = FakeRadius(with_mac=False)
        self.addCleanup(old.stop)
        with self.assertRaises(proto.DirectoryError):                  # BlastRADIUS : Message-Authenticator exigé
            proto.radius_authenticate(dict(cfg, port=old.port), "bob", "Mot-de-passe-Bob")
        self.assertTrue(proto.radius_authenticate(dict(cfg, port=old.port, require_message_authenticator=False),
                                                  "bob", "Mot-de-passe-Bob")[0])


class TestDirectoryLogin(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM auth_directories")
            conn.execute("DELETE FROM users WHERE auth_source IN ('directory', 'sso')")
            conn.execute("DELETE FROM settings WHERE key=?", (features.SETTING,))
        self.addCleanup(self.cleanup)
        self.ldap = FakeLdap()
        self.addCleanup(self.ldap.stop)
        self.radius = FakeRadius()
        self.addCleanup(self.radius.stop)

    @staticmethod
    def cleanup():
        with transaction() as conn:
            conn.execute("DELETE FROM auth_directories")
            conn.execute("DELETE FROM users WHERE auth_source IN ('directory', 'sso')")

    def add_directories(self):
        r = self.admin.post("/comptes/annuaires/ajouter", {
            "kind": "ldap", "label": "Annuaire test", "url": f"ldaps://127.0.0.1:{self.ldap.port}", "ca_pem": self.ldap.ca,
            "base_dn": "dc=test", "user_filter": "(uid={username})", "bind_dn": "cn=svc,dc=test",
            "bind_password": "secret-service", "enabled": "1"}, page="/comptes/annuaires")
        self.assertEqual(r.status_code, 302)
        self.admin.post("/comptes/annuaires/ajouter", {"kind": "radius", "label": "RADIUS test", "host": "127.0.0.1",
                        "port": str(self.radius.port), "secret": "secret-radius", "timeout": "2", "retries": "0",
                        "require_message_authenticator": "1", "enabled": "1"}, page="/comptes/annuaires")
        with transaction() as conn:
            stored = conn.execute("SELECT config FROM auth_directories WHERE kind='ldap'").fetchone()[0]
        self.assertNotIn("secret-service", stored)                     # secret chiffré au repos

    def login(self, username, password):
        browser = fixture.Browser()
        return browser, browser.login(username, password)

    def test_first_login_creates_a_request_then_approval_grants_access(self):
        self.add_directories()
        page = self.admin.c.get("/comptes/annuaires").get_data(as_text=True)
        self.assertIn("Annuaire test", page)
        self.assertNotIn("secret-service", page)
        _b, r = self.login("alice", "Mot-de-passe-Alice")
        self.assertEqual(r.status_code, 202)
        self.assertIn("Votre demande d&#39;accès a été créée", r.get_data(as_text=True))
        _b, r = self.login("bob", "Mot-de-passe-Bob")                  # second annuaire, dans l'ordre
        self.assertEqual(r.status_code, 202)
        with transaction() as conn:
            alice = conn.execute("SELECT * FROM users WHERE username='alice'").fetchone()
            self.assertEqual((alice["auth_source"], alice["pending"], alice["role"]), ("directory", 1, "pending"))
        users = self.admin.c.get("/comptes").get_data(as_text=True)
        self.assertIn("Alice Martin", users)
        self.assertIn("alice@test.local", users)
        self.admin.post(f"/comptes/{alice['id']}/approuver", {"role": "viewer"}, page="/comptes")
        browser, r = self.login("alice", "Mot-de-passe-Alice")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(browser.c.get("/compte").status_code, 200)
        _b, r = self.login("alice", "mauvais")
        self.assertEqual(r.status_code, 401)
        # Annuaire désactivé : la session de ce compte tombe, les comptes locaux restent.
        self.admin.post("/administration/fonctionnalites", {k: "1" for k in features.FEATURES if k != "directories"},
                        page="/administration/fonctionnalites")
        self.assertEqual(browser.c.get("/compte").status_code, 302)
        self.assertEqual(self.admin.c.get("/compte").status_code, 200)

    def test_local_accounts_are_never_matched_with_a_directory(self):
        self.add_directories()
        _b, r = self.login("admin", "Mot-de-passe-Alice")              # « admin » local : jamais l'annuaire
        self.assertEqual(r.status_code, 401)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE auth_source='directory'").fetchone()[0], 0)

    def test_directory_test_button_and_legacy_oidc_accounts(self):
        self.add_directories()
        with transaction() as conn:
            did = conn.execute("SELECT id FROM auth_directories WHERE kind='ldap'").fetchone()[0]
            conn.execute("INSERT INTO users (username,password_hash,role,auth_source,created_at) VALUES "
                         "('ancien-oidc','x','viewer','sso','2026-01-01 00:00')")
        r = self.admin.post(f"/comptes/annuaires/{did}/tester", {"username": "alice", "password": "Mot-de-passe-Alice"},
                            page="/comptes/annuaires")
        self.assertEqual(r.status_code, 302)
        page = self.admin.c.get("/comptes/annuaires").get_data(as_text=True)
        self.assertIn("Identifiants acceptés : Alice Martin", page)
        self.assertIn("1 ancien compte OpenID Connect", page)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE username='alice'").fetchone()[0], 0)   # pas de demande
            legacy = conn.execute("SELECT id FROM users WHERE username='ancien-oidc'").fetchone()[0]
        self.admin.post(f"/comptes/{legacy}/reinitialiser", {}, page="/comptes")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT auth_source FROM users WHERE id=?", (legacy,)).fetchone()[0], "local")


if __name__ == "__main__":
    unittest.main()
