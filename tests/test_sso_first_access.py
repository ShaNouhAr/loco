"""Certificats d'entreprise (erreurs distinctes, intermédiaire en complément, demande) et premier accès : liaison explicite d'un compte
local, profil proposé d'après un groupe, sans aucun rapprochement automatique par nom ou courriel."""
import datetime
import html
import io
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.parse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import locoadmin
import test_portal as fixture
from test_fleet import FleetTest
from test_oidc import DIRECTORY, OidcTest
from test_sso_check import INTERMEDIATE, OTHER_ROOT, ROOT, issue, pem
from loco import config, oidc, permissions, system
from loco.db import transaction
from loco.permissions import ENDPOINTS

HOST = "192.168.100.48"
KEY = ec.generate_private_key(ec.SECP256R1())


def leaf_for(*names, signer=INTERMEDIATE):
    """Certificat signé par « signer », pour KEY, couvrant ces noms et adresses."""
    now = datetime.datetime.now(datetime.timezone.utc)
    san = []
    for name in names:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            san.append(x509.DNSName(name))
    built = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loco")])).issuer_name(signer[0].subject)
             .public_key(KEY.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=30)).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(signer[1], hashes.SHA256()))
    return built


class Enterprise(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tls, self.conf = os.path.join(tmp.name, "tls"), os.path.join(tmp.name, "loco.conf")
        os.mkdir(self.tls)
        self.key = os.path.join(tmp.name, "k.pem")
        with open(self.key, "wb") as f:
            f.write(KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(f'HOST={HOST}\nHTTPS_PORT=8443\nPORTAL_URL="https://loco.lab.local:8443"\n')
        for patcher in (mock.patch.object(locoadmin, "TLS", self.tls), mock.patch.object(locoadmin, "CONF", self.conf),
                        mock.patch.object(locoadmin, "tls_path", lambda name: os.path.join(self.tls, locoadmin.TLS_FILES[name]))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.authority(ROOT[0])

    def authority(self, *certs):
        with open(os.path.join(self.tls, locoadmin.TLS_FILES["ENTERPRISE_CA"]), "w", encoding="utf-8") as f:
            f.write(pem(*certs))

    def check(self, *certs):
        return locoadmin._check_enterprise_pair(pem(*certs), self.key, lambda _text: None)

    def refused(self, *certs):
        with self.assertRaises(ValueError) as caught:
            self.check(*certs)
        return str(caught.exception)


class TestImportErrors(Enterprise):
    """Panne 6 : avant, tout donnait « pas signé par l'autorité »."""
    GOOD = (HOST, "loco.lab.local")

    def test_the_full_chain_is_accepted(self):
        chain = self.check(leaf_for(*self.GOOD), INTERMEDIATE[0])
        self.assertEqual(chain.count("BEGIN CERTIFICATE"), 2)

    def test_missing_intermediate_names_it(self):
        message = self.refused(leaf_for(*self.GOOD))
        self.assertIn("Intermediaire Lab", message)
        self.assertIn("Racine Lab", message)
        self.assertRegex(message, "(?i)intermédiaire manquant|missing intermediate|falta el intermedio")

    def test_wrong_authority_is_a_different_message(self):
        self.authority(OTHER_ROOT[0])
        message = self.refused(leaf_for(*self.GOOD), INTERMEDIATE[0])
        self.assertRegex(message, "(?i)mauvaise autorité|wrong authority|autoridad incorrecta")
        self.assertIn("Racine Lab", message)
        self.assertIn("Autre Racine", message)

    def test_same_name_but_another_key_is_a_wrong_authority(self):
        self.authority(issue("Racine Lab", ca=True)[0])
        message = self.refused(leaf_for(*self.GOOD), INTERMEDIATE[0])
        self.assertRegex(message, "(?i)mauvaise autorité|wrong authority|autoridad incorrecta")

    def test_a_removed_ip_is_a_missing_san_and_lists_what_is_there(self):
        message = self.refused(leaf_for("loco.lab.local"), INTERMEDIATE[0])
        self.assertIn(HOST, message)
        self.assertIn("loco.lab.local", message)
        self.assertIn("EJBCA", message)

    def test_a_missing_portal_name_is_a_missing_san(self):
        message = self.refused(leaf_for(HOST), INTERMEDIATE[0])
        self.assertIn("loco.lab.local", message)
        self.assertIn(HOST, message)

    def test_the_three_errors_are_all_different(self):
        missing = self.refused(leaf_for(*self.GOOD))
        san = self.refused(leaf_for("loco.lab.local"), INTERMEDIATE[0])
        self.authority(OTHER_ROOT[0])
        wrong = self.refused(leaf_for(*self.GOOD), INTERMEDIATE[0])
        self.assertEqual(len({missing.split(":")[0], san.split(":")[0], wrong.split(":")[0]}), 3)


class TestNames(Enterprise):
    def test_a_short_name_is_kept_panne_6(self):
        self.assertEqual(locoadmin.tls_params("ca-csr", {"names": "loco loco.lab.local LOCO"})["names"], "loco loco.lab.local")
        with mock.patch.object(locoadmin.socket, "gethostname", return_value="loco"), mock.patch.object(locoadmin.socket, "getfqdn", return_value="loco"):
            self.assertIn("DNS:loco", locoadmin._agent_names())

    def test_wrong_names_stay_refused(self):
        for bad in ("-x", "a b;c", "*.com", "*", "a..b", "x" * 64):
            with self.assertRaises(ValueError, msg=bad):
                locoadmin.tls_params("ca-csr", {"names": bad})
        self.assertEqual(locoadmin.tls_params("ca-csr", {"names": "*.lab.local"})["names"], "*.lab.local")

    def test_a_real_request_contains_the_fqdn_the_portal_and_the_ip(self):
        locoadmin.ca_csr({"names": "serveur.lab.local loco"}, log=lambda _t: None)
        out = subprocess.run(["openssl", "req", "-in", os.path.join(self.tls, locoadmin.TLS_FILES["REQUEST_CSR"]), "-noout", "-text"], capture_output=True, text=True).stdout
        for expected in (f"IP Address:{HOST}", "DNS:serveur.lab.local", "DNS:loco.lab.local", "DNS:loco"):
            self.assertIn(expected, out)


class TestCertificatePage(FleetTest):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(config, "HOST", HOST), mock.patch.object(config, "portal_url", return_value="https://loco.lab.local:8443"), mock.patch.object(locoadmin, "_agent_names", return_value=[f"IP:{HOST}", "DNS:loco.lab.local"]), mock.patch.object(locoadmin, "jobs", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def page(self):
        with mock.patch.object(system, "_tls_file", lambda name: pem(ROOT[0]).encode() if name == "enterprise-ca.pem" else b""):
            return html.unescape(self.admin.c.get("/system").get_data(as_text=True))

    def test_the_request_asks_the_fqdn_and_shows_the_names_and_warns_about_the_ip(self):
        page = self.page()
        self.assertIn('name="fqdn"', page)
        self.assertIn('value="loco.lab.local"', page)
        self.assertIn(f"{HOST}, loco.lab.local", page)
        self.assertIn(f"conserver l'IP {HOST}", page)

    def test_no_warning_when_the_agents_use_a_name(self):
        with mock.patch.object(config, "HOST", "loco.lab.local"):
            self.assertNotIn("conserver l'IP", self.page())

    def test_the_fqdn_and_extra_names_go_to_the_agent(self):
        token = self.admin.token("/account")
        with mock.patch.object(system, "_tls_routes_allowed"), mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), mock.patch.object(locoadmin, "request") as request:
            self.admin.c.post("/system/certificate/request", data={"fqdn": "loco.lab.local", "names": "loco", "csrf": token})
        self.assertEqual(request.call_args[0][1]["names"], "loco.lab.local loco")

    def upload(self, **files):
        token = self.admin.token("/account")
        data = {"csrf": token, **{name: (io.BytesIO(body.encode()), name + ".pem") for name, body in files.items()}}
        with mock.patch.object(system, "_tls_routes_allowed"), mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request") as request, mock.patch.object(system, "_spool_tls", return_value="x") as spool, mock.patch("os.path.exists", return_value=True):
            r = self.admin.c.post("/system/certificate/enterprise", data=data, follow_redirects=True, content_type="multipart/form-data")
        return html.unescape(r.get_data(as_text=True)), request, spool

    def test_the_leaf_alone_with_the_intermediate_as_a_complement(self):
        _page, request, spool = self.upload(certificate=pem(leaf_for(HOST, "loco.lab.local")), intermediate=pem(INTERMEDIATE[0]))
        request.assert_called_once()
        self.assertEqual(spool.call_args[0][2].count(b"BEGIN CERTIFICATE"), 2)

    def test_an_unreadable_intermediate_is_refused(self):
        page, request, _spool = self.upload(certificate=pem(leaf_for(HOST, "loco.lab.local")), intermediate="pas un certificat")
        self.assertIn("intermédiaire illisible", page)
        request.assert_not_called()

    def test_a_chain_problem_is_reported_on_the_page_not_only_in_the_log(self):
        with mock.patch.object(system, "_tls_file", lambda name: pem(ROOT[0]).encode() if name == "enterprise-ca.pem" else b""):
            page, request, _spool = self.upload(certificate=pem(leaf_for(HOST, "loco.lab.local")))
            self.assertIn("Intermédiaire manquant", page.replace("intermédiaire manquant", "Intermédiaire manquant"))
            self.assertIn("Intermediaire Lab", page)
            request.assert_not_called()
            other = leaf_for(HOST, "loco.lab.local", signer=OTHER_ROOT)
            page, request, _spool = self.upload(certificate=pem(other))
            self.assertIn("Autre Racine", page)
            request.assert_not_called()
            _page, request, _spool = self.upload(certificate=pem(leaf_for(HOST, "loco.lab.local")), intermediate=pem(INTERMEDIATE[0]))
            request.assert_called_once()

    def test_the_page_reports_a_missing_san_before_sending_anything(self):
        page, request, _spool = self.upload(certificate=pem(leaf_for("loco.lab.local")))
        self.assertIn("SAN absent", page)
        self.assertIn(HOST, page)
        self.assertIn("EJBCA", page)
        request.assert_not_called()


class LinkTest(OidcTest):
    def setUp(self):
        super().setUp()
        self.add()
        with transaction() as conn:
            self.admin_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
            conn.execute("UPDATE users SET mfa_enabled=0 WHERE id=?", (self.admin_id,))

    def start_link(self, browser=None):
        browser = browser or self.admin
        r = browser.post(f"/account/sso/{DIRECTORY}/link", {}, page="/account")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        url = html.unescape(re.search(r'data-continue="([^"]+)"', r.get_data(as_text=True)).group(1))
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.idp.nonce = query["nonce"]
        return query

    def finish_link(self, query, browser=None):
        return self.callback((browser or self.admin).c, query)

    def identity(self, user_id=None):
        with transaction() as conn:
            return conn.execute("SELECT * FROM directory_identities WHERE user_id=?", (user_id or self.admin_id,)).fetchone()

    def sso_login(self, browser=None):
        browser = browser or fixture.Browser()
        return browser, self.callback(browser.c, self.start(browser.c))


class TestLink(LinkTest):
    def test_linking_then_signing_in_opens_the_same_local_account(self):
        r = self.finish_link(self.start_link())
        self.assertEqual(r.status_code, 200)
        self.assertIn("est maintenant lié", html.unescape(r.get_data(as_text=True)))
        self.assertEqual(self.identity()["subject"], "a1b2c3d4e5f6")
        browser, r = self.sso_login()
        self.assertEqual(r.status_code, 200)
        with browser.c.session_transaction() as s:
            self.assertEqual(s["uid"], self.admin_id)
        with transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (self.admin_id,)).fetchone()
        self.assertEqual((user["auth_source"], user["disabled"], user["role"]), ("local", 0, "admin"))

    def test_the_password_still_works_after_linking(self):
        self.finish_link(self.start_link())
        self.assertEqual(fixture.Browser().login("admin", fixture.ADMIN_PW).status_code, 302)

    def test_nothing_is_ever_matched_by_name_or_email(self):
        with transaction() as conn:
            conn.execute("INSERT INTO users (username,password_hash,role,auth_source,created_at) VALUES ('jean.dupont','x','admin','local','2026-11-03 10:00')")
        _browser, r = self.sso_login()
        self.assertEqual(r.status_code, 202)                     # nouvelle demande, pas le compte du même nom
        with transaction() as conn:
            local = conn.execute("SELECT id FROM users WHERE username='jean.dupont'").fetchone()["id"]
            self.assertIsNone(conn.execute("SELECT 1 FROM directory_identities WHERE user_id=?", (local,)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM directory_identities WHERE user_id=?", (self.admin_id,)).fetchone())
            conn.execute("DELETE FROM users WHERE username='jean.dupont'")

    def test_a_waiting_request_for_this_identity_is_replaced(self):
        self.sso_login()
        with transaction() as conn:
            pending = conn.execute("SELECT user_id FROM directory_identities WHERE subject='a1b2c3d4e5f6'").fetchone()["user_id"]
        self.finish_link(self.start_link())
        self.assertEqual(self.identity()["subject"], "a1b2c3d4e5f6")
        with transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id=?", (pending,)).fetchone())

    def test_an_identity_of_another_active_account_is_not_taken(self):
        self.sso_login()
        with transaction() as conn:
            row = conn.execute("SELECT u.id FROM users u JOIN directory_identities i ON i.user_id=u.id").fetchone()
            conn.execute("UPDATE users SET pending=0,disabled=0,role='viewer' WHERE id=?", (row["id"],))
        r = self.finish_link(self.start_link())
        self.assertEqual(r.status_code, 409)
        self.assertIsNone(self.identity())
        self.assertEqual(self.identity(row["id"])["subject"], "a1b2c3d4e5f6")

    def test_a_flow_from_another_browser_links_nothing(self):
        query = self.start_link()
        r = self.callback(fixture.Browser().c, query)
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.identity())

    def test_a_wrong_state_links_nothing(self):
        query = dict(self.start_link(), state="autre")
        self.assertEqual(self.finish_link(query).status_code, 400)
        self.assertIsNone(self.identity())

    def test_a_password_changed_meanwhile_cancels_the_link(self):
        query = self.start_link()
        with transaction() as conn:
            conn.execute("UPDATE users SET pw_version=pw_version+1 WHERE id=?", (self.admin_id,))
        self.assertEqual(self.finish_link(query).status_code, 403)
        self.assertIsNone(self.identity())

    def test_a_tampered_flow_cannot_target_another_account(self):
        self.start_link()
        with mock.patch.object(oidc, "_open_flow", return_value=None):
            self.assertEqual(self.finish_link({"state": "x", "nonce": "y"}).status_code, 400)
        self.assertIsNone(self.identity())

    def test_rights_lost_meanwhile_cancel_the_link(self):
        query = self.start_link()
        with mock.patch("loco.permissions.role_permissions", return_value=frozenset()):
            self.assertEqual(self.finish_link(query).status_code, 403)
        self.assertIsNone(self.identity())

    def test_it_needs_the_right_and_a_local_account(self):
        self.assertEqual((ENDPOINTS["sso_link"], ENDPOINTS["sso_unlink"]), ("sso.manage", "sso.manage"))
        with transaction() as conn:
            conn.execute("UPDATE users SET auth_source='directory' WHERE id=?", (self.admin_id,))
        try:
            self.assertEqual(self.admin.post(f"/account/sso/{DIRECTORY}/link", {}, page="/accounts/directories").status_code, 400)
        finally:
            with transaction() as conn:
                conn.execute("UPDATE users SET auth_source='local' WHERE id=?", (self.admin_id,))

    def test_it_must_start_from_the_portal_address(self):
        with mock.patch.object(oidc, "off_portal", return_value=True):
            r = self.admin.post(f"/account/sso/{DIRECTORY}/link", {}, page="/account")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(any(oidc.FLOW_COOKIE in c for c in r.headers.getlist("Set-Cookie")))

    def test_unlinking(self):
        self.finish_link(self.start_link())
        self.assertIn("liée à", html.unescape(self.admin.c.get("/account").get_data(as_text=True)))
        self.admin.post("/account/sso/unlink", {}, page="/account")
        self.assertIsNone(self.identity())
        _browser, r = self.sso_login()
        self.assertEqual(r.status_code, 202)                     # de nouveau une simple demande

    def test_the_account_page_offers_it_to_admins_only(self):
        self.assertIn(f"/account/sso/{DIRECTORY}/link", self.admin.c.get("/account").get_data(as_text=True))
        with transaction() as conn:
            conn.execute("UPDATE auth_directories SET enabled=0")
        self.assertNotIn("/account/sso/", self.admin.c.get("/account").get_data(as_text=True))


class TestLinkedAccountStaysSafe(LinkTest):
    def setUp(self):
        super().setUp()
        self.finish_link(self.start_link())

    def tearDown(self):
        with transaction() as conn:
            conn.execute("UPDATE users SET mfa_enabled=0, disabled=0 WHERE id=?", (self.admin_id,))
        super().tearDown()

    def test_its_own_two_factor_is_still_required(self):
        with transaction() as conn:
            conn.execute("UPDATE users SET mfa_enabled=1 WHERE id=?", (self.admin_id,))
        browser, r = self.sso_login()
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-continue="/login/mfa"', r.get_data(as_text=True))
        with browser.c.session_transaction() as s:
            self.assertNotIn("uid", s)
            self.assertEqual(s["mfa_pending"]["uid"], self.admin_id)

    def test_deleting_the_provider_does_not_disable_the_local_account(self):
        self.admin.post(f"/accounts/directories/{DIRECTORY}/delete", {}, page="/accounts/directories")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id=?", (self.admin_id,)).fetchone()["disabled"], 0)
        self.assertEqual(self.admin.c.get("/account").status_code, 200)  # session toujours ouverte

    def test_editing_or_disabling_the_provider_keeps_the_session(self):
        with transaction() as conn:
            before = conn.execute("SELECT pw_version FROM users WHERE id=?", (self.admin_id,)).fetchone()["pw_version"]
        self.admin.post(f"/accounts/directories/{DIRECTORY}/toggle", {}, page="/accounts/directories")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT pw_version FROM users WHERE id=?", (self.admin_id,)).fetchone()["pw_version"], before)
        self.assertEqual(self.admin.c.get("/account").status_code, 200)

    def test_a_directory_account_is_still_cut_off_with_its_provider(self):
        with transaction() as conn:
            uid = conn.execute("INSERT INTO users (username,password_hash,role,auth_source,created_at) VALUES ('ldap.user','x','viewer','directory','2026-11-03')").lastrowid
            conn.execute("INSERT INTO directory_identities (directory_id,subject,user_id) VALUES (?,?,?)", (DIRECTORY, "autre-sub", uid))
        self.admin.post(f"/accounts/directories/{DIRECTORY}/delete", {}, page="/accounts/directories")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id=?", (uid,)).fetchone()["disabled"], 1)
            conn.execute("DELETE FROM users WHERE id=?", (uid,))


class TestGroups(OidcTest):
    FORM = {"preset": "authentik", "client_id": "loco-client-id", "enabled": "1", "label": ""}

    def request_access(self, mapping="loco-admins = admin\n"):
        self.add(group_roles=mapping)
        fixture_browser = fixture.Browser()
        return self.callback(fixture_browser.c, self.start(fixture_browser.c))

    def test_the_mapping_is_saved_by_key_or_by_label(self):
        with transaction() as conn:
            label = permissions.role_labels(conn)["admin"]
        self.add(group_roles=f"loco-admins = admin\n  chefs = {label} \n")
        with transaction() as conn:
            saved = json.loads(conn.execute("SELECT config FROM auth_directories").fetchone()["config"])["group_roles"]
        self.assertEqual(saved, {"loco-admins": "admin", "chefs": "admin"})

    def test_a_wrong_line_is_refused(self):
        for line in ("sans-egal", "groupe = profil-inconnu", "= admin", "groupe = pending"):
            r = self.add(group_roles=line)
            self.assertIn("/accounts/directories", r.headers["Location"])
            with transaction() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM auth_directories").fetchone(), line)

    def test_nothing_is_granted_before_approval(self):
        r = self.request_access()
        self.assertEqual(r.status_code, 202)
        user = self.user("jean.dupont")
        self.assertTrue(user["pending"] and user["disabled"])
        self.assertEqual(user["role"], "pending")

    def test_the_approval_page_preselects_and_explains(self):
        self.request_access()
        page = html.unescape(self.admin.c.get("/accounts").get_data(as_text=True))
        self.assertRegex(page, r'<option value="admin" selected>')
        self.assertIn("d’après le groupe loco-admins", page)

    def test_without_a_matching_group_the_default_stays(self):
        self.request_access("autre-groupe = admin\n")
        page = html.unescape(self.admin.c.get("/accounts").get_data(as_text=True))
        self.assertNotRegex(page, r'<option value="admin" selected>')
        self.assertNotIn("d’après le groupe", page)

    def test_groups_follow_the_last_sign_in(self):
        self.request_access()
        self.idp.claims = lambda original=self.idp.claims: {**original(), "groups": ["autre"]}
        browser = fixture.Browser()
        self.callback(browser.c, self.start(browser.c))
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT groups FROM directory_identities").fetchone()["groups"]), ["autre"])

    def test_an_approver_is_not_offered_a_profile_they_cannot_assign(self):
        self.request_access()
        with mock.patch("loco.auth.can_assign", side_effect=lambda role: role != "admin"):
            page = html.unescape(self.admin.c.get("/accounts").get_data(as_text=True))
        self.assertNotIn("d’après le groupe", page)

    def test_the_mapping_survives_the_authentik_setup(self):
        from loco import authentik_setup
        self.add(group_roles="loco-admins = admin\n")
        with fixture.loco.app.test_request_context("/"):
            fixture.loco.app.preprocess_request()
            from flask import g
            with transaction() as conn:
                g.user = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            with mock.patch.object(authentik_setup, "lock_access", side_effect=lambda _p: oidc.db()):
                authentik_setup.save_directory(DIRECTORY, "Authentik", "https://ak.test/application/o/loco/", "c", "s", "", 1)
        with transaction() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT config FROM auth_directories").fetchone()["config"])["group_roles"], {"loco-admins": "admin"})


if __name__ == "__main__":
    unittest.main()
