"""Adresse du portail (PORTAL_URL) séparée de celle des agents : réglage, validation par l'agent root, certificat, retour
du SSO par le nom, agents et enrôlement inchangés, redirections sans en-tête Host."""
import datetime
import html
import ipaddress
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.parse

from flask import g

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import locoadmin
import locoha
import test_portal as fixture
from test_fleet import FleetTest
from test_oidc import DIRECTORY, OidcTest
from loco import addresses, config, db, mcp, oidc, system

ROOT = Path(__file__).resolve().parents[1]
AGENTS, NAME = "https://192.168.100.48:8443", "https://loco.lab.local:8443"
CONF = {"HOST": "192.168.100.48", "HTTPS_PORT": "8443"}


def cert(*names, key=None):
    """Certificat auto-signé couvrant ces noms et adresses (PEM)."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    san = []
    for name in names:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            san.append(x509.DNSName(name))
    built = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(7)
             .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
             .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(key, hashes.SHA256()))
    return built.public_bytes(serialization.Encoding.PEM).decode()


class TempConf(unittest.TestCase):
    """loco.conf et dossier des certificats propres au test, pour le portail et pour l'agent root."""
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.etc, self.tls = tmp.name, os.path.join(tmp.name, "tls")
        os.mkdir(self.tls)
        self.conf = os.path.join(self.etc, "loco.conf")
        for patcher in (mock.patch.object(config, "ETC", self.etc), mock.patch.object(config, "HTTPS_URL", AGENTS),
                        mock.patch.object(config, "TLS_DIR", self.tls), mock.patch.object(locoadmin, "CONF", self.conf),
                        mock.patch.object(locoadmin, "TLS", self.tls), mock.patch.object(locoadmin, "ha_configured", return_value=False),
                        mock.patch.dict(os.environ, {"LOCO_PORTAL_URL": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write_conf()

    def write_conf(self, portal=""):
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(f'HOST=192.168.100.48\nHTTPS_PORT=8443\nPORTAL_URL="{portal}"\n')

    def serve(self, *names, file="server.crt"):
        with open(os.path.join(self.tls, file), "w", encoding="utf-8") as f:
            f.write(cert(*names))


class TestReading(TempConf):
    def test_by_default_the_portal_is_at_the_agents_address(self):
        self.assertEqual(config.portal_url(), AGENTS)

    def test_a_change_applies_without_restart(self):
        self.write_conf(NAME)
        self.assertEqual(config.portal_url(), NAME)
        self.write_conf("https://autre.lab.local:8443")
        self.assertEqual(config.portal_url(), "https://autre.lab.local:8443")
        self.write_conf()
        self.assertEqual(config.portal_url(), AGENTS)

    def test_a_tampered_value_is_ignored(self):
        for bad in ("http://loco.lab.local", "https://loco.lab.local/chemin", 'https://x.test";reboot;"', "https://x.test$(id)", "//evil.test"):
            self.write_conf(bad)
            self.assertEqual(config.portal_url(), AGENTS, bad)

    def test_docker_and_windows_read_the_environment(self):
        os.unlink(self.conf)
        with mock.patch.dict(os.environ, {"LOCO_PORTAL_URL": NAME + "/"}):
            self.assertEqual(config.portal_url(), NAME)

    def test_an_unreadable_file_never_breaks_a_page(self):
        os.utime(self.conf, (1, 1))         # le fichier a changé : il sera relu
        with mock.patch("builtins.open", side_effect=PermissionError):
            self.assertEqual(config.portal_url(), AGENTS)


class TestValidation(TempConf):
    def setUp(self):
        super().setUp()
        self.serve("192.168.100.48", "loco.lab.local", "*.corp.test", "10.0.0.9", "::1")

    def test_covered_addresses_are_accepted_and_normalized(self):
        check = lambda url: locoadmin.portal_params({"url": url}, CONF)["url"]  # noqa: E731
        self.assertEqual(check(" https://LOCO.lab.local:8443/ "), NAME)
        self.assertEqual(check("https://portail.corp.test:8443"), "https://portail.corp.test:8443")     # joker
        self.assertEqual(check("https://10.0.0.9:8443"), "https://10.0.0.9:8443")
        self.assertEqual(check("https://[::1]:8443"), "https://[::1]:8443")

    def test_the_agents_address_or_nothing_means_the_default(self):
        for url in ("", "https://192.168.100.48:8443"):
            self.assertEqual(locoadmin.portal_params({"url": url}, CONF)["url"], "")

    def test_values_that_could_escape_are_refused(self):
        for url in ('https://x.test";reboot;"', "https://x.test$(reboot)", "https://x.test`id`", "https://x.test:8443\nHOST=evil", "http://loco.lab.local:8443",
                    "https://loco.lab.local:8443/chemin", "https://loco.lab.local:8443?a=1", "https://admin@loco.lab.local:8443", "https://loco.lab.local:99999",
                    "//loco.lab.local:8443", "loco.lab.local", "https://-x.test:8443", "https://" + "a" * 260 + ".test:8443", "javascript:alert(1)"):
            with self.assertRaises(ValueError, msg=url):
                locoadmin.portal_params({"url": url}, CONF)

    def test_another_port_is_refused_with_the_right_one(self):
        with self.assertRaisesRegex(ValueError, "8443"):
            locoadmin.portal_params({"url": "https://loco.lab.local:9443"}, CONF)
        with self.assertRaisesRegex(ValueError, "8443"):
            locoadmin.portal_params({"url": "https://loco.lab.local"}, CONF)

    def test_port_443_is_implicit(self):
        self.assertEqual(locoadmin.portal_params({"url": "https://loco.lab.local:443"}, dict(CONF, HTTPS_PORT="443"))["url"], "https://loco.lab.local")
        self.assertEqual(locoadmin.portal_params({"url": "https://loco.lab.local"}, dict(CONF, HTTPS_PORT="443"))["url"], "https://loco.lab.local")

    def test_an_address_the_certificate_does_not_cover_is_refused(self):
        with self.assertRaisesRegex(ValueError, "absent.lab.local"):
            locoadmin.portal_params({"url": "https://absent.lab.local:8443"}, CONF)
        self.assertEqual(locoadmin.portal_params({"url": "https://absent.lab.local:8443", "extend": "1"}, CONF),
                         {"url": "https://absent.lab.local:8443", "extend": "1"})

    def test_a_name_already_covered_does_not_renew_the_certificate(self):
        self.assertEqual(locoadmin.portal_params({"url": NAME, "extend": "1"}, CONF)["extend"], "")

    def test_the_imported_certificate_also_covers(self):
        self.serve("autre.public.test", file="imported.crt")
        self.assertEqual(locoadmin.portal_params({"url": "https://autre.public.test:8443"}, CONF)["url"], "https://autre.public.test:8443")

    def test_loco_cannot_extend_a_certificate_it_does_not_issue(self):
        with mock.patch.object(locoadmin, "tls_mode", return_value="enterprise"), self.assertRaisesRegex(ValueError, "absent.lab.local"):
            locoadmin.portal_params({"url": "https://absent.lab.local:8443", "extend": "1"}, CONF)
        Path(self.tls, "custom").touch()
        with self.assertRaisesRegex(ValueError, "--tls-cert"):
            locoadmin.portal_params({"url": "https://absent.lab.local:8443", "extend": "1"}, CONF)

    def test_the_root_agent_validates_every_request_again(self):
        # Le portail dépose la demande, l'agent root la relit : une demande forgée ne passe pas.
        self.assertIn("set-portal-url", locoadmin.ACTIONS)
        self.assertNotIn("set-portal-url", locoadmin.APPLIANCE_ACTIONS)
        with mock.patch.object(locoadmin, "agent_running", return_value=True), self.assertRaises(ValueError):
            locoadmin.request("set-portal-url", {"url": 'https://x.test";reboot;"'})
        with self.assertRaises(ValueError):
            locoadmin._operation("set-portal-url", {"url": "https://x.test$(id):8443"}, print)
        source = (ROOT / "portal/locoadmin.py").read_text(encoding="utf-8")
        loop = source[source.index('elif req["action"] == "set-portal-url"'):][:160]
        self.assertIn("portal_params(req.get(\"params\"))", loop)


class TestApply(TempConf):
    def setUp(self):
        super().setUp()
        self.serve("192.168.100.48", "loco.lab.local")
        self.logs = []

    def test_saving_writes_the_setting_and_keeps_the_rest(self):
        self.assertTrue(locoadmin.set_portal_url({"url": NAME}, self.logs.append))
        conf = locoadmin.read_conf()
        self.assertEqual((conf["PORTAL_URL"], conf["HOST"], conf["HTTPS_PORT"]), (NAME, "192.168.100.48", "8443"))
        self.assertEqual(config.portal_url(), NAME)
        self.assertIn(NAME, self.logs[-1])

    def test_going_back_to_the_default(self):
        self.write_conf(NAME)
        self.assertTrue(locoadmin.set_portal_url({"url": ""}, self.logs.append))
        self.assertEqual(locoadmin.read_conf()["PORTAL_URL"], "")
        self.assertEqual(config.portal_url(), AGENTS)

    def test_extending_renews_the_certificate_with_the_new_name(self):
        with mock.patch.object(locoadmin, "renew_cert") as renew:
            locoadmin.set_portal_url({"url": "https://absent.lab.local:8443", "extend": "1"}, self.logs.append)
        renew.assert_called_once()
        self.assertEqual(locoadmin.read_conf()["PORTAL_URL"], "https://absent.lab.local:8443")

    def test_a_failed_renewal_restores_the_previous_address(self):
        self.write_conf(NAME)
        with mock.patch.object(locoadmin, "renew_cert", side_effect=OSError("boom")), self.assertRaises(OSError):
            locoadmin.set_portal_url({"url": "https://absent.lab.local:8443", "extend": "1"}, self.logs.append)
        self.assertEqual(locoadmin.read_conf()["PORTAL_URL"], NAME)

    def test_the_standby_server_refuses_it_loco_conf_comes_from_the_primary(self):
        ha = mock.Mock()
        ha.role.return_value = "standby"
        with mock.patch.object(locoadmin, "ha_configured", return_value=True), mock.patch.object(locoadmin, "_ha", return_value=ha), \
                self.assertRaises(ValueError):
            locoadmin.set_portal_url({"url": NAME}, self.logs.append)
        self.assertEqual(locoadmin.read_conf()["PORTAL_URL"], "")

    def test_the_certificate_covers_the_agents_and_the_portal_even_a_short_name(self):
        for portal, expected in ((NAME, "DNS:loco.lab.local"), ("https://loco:8443", "DNS:loco"), ("https://10.0.0.9:8443", "IP:10.0.0.9")):
            self.write_conf(portal)
            names = locoadmin._agent_names()
            self.assertEqual(names[0], "IP:192.168.100.48")
            self.assertIn(expected, names)

    def test_renewing_in_a_cluster_keeps_both_servers(self):
        ha = mock.Mock(wraps=locoha)
        ha.node.return_value = {"self": {"address": "192.168.100.51"}, "peer": {"address": "192.168.100.52"}}
        with mock.patch.object(locoadmin, "ha_configured", return_value=True), mock.patch.object(locoadmin, "_ha", return_value=ha), \
                mock.patch.object(locoadmin, "tls_mode", return_value="loco"), mock.patch.object(locoadmin, "_swap_tls"), \
                mock.patch.object(locoadmin, "_loco_cert", return_value=("k", "c", ["IP:192.168.100.48"])) as issue:
            locoadmin.renew_cert(log=self.logs.append)
        self.assertEqual(issue.call_args.kwargs["extra"], ["192.168.100.51", "192.168.100.52"])

    def test_removing_the_imported_certificate_that_covers_the_portal_is_refused(self):
        self.serve("192.168.100.48")
        self.serve("loco.lab.local", file="imported.crt")
        self.write_conf(NAME)
        with self.assertRaisesRegex(ValueError, "loco.lab.local"):
            locoadmin.remove_imported_cert(log=self.logs.append)
        self.assertTrue(os.path.exists(os.path.join(self.tls, "imported.crt")))

    def test_an_enterprise_certificate_without_the_portal_name_is_refused(self):
        self.write_conf(NAME)
        key = ec.generate_private_key(ec.SECP256R1())
        with tempfile.NamedTemporaryFile("wb", suffix=".key") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            f.flush()
            with self.assertRaisesRegex(ValueError, "loco.lab.local"):
                locoadmin._check_enterprise_pair(cert("192.168.100.48", key=key), f.name, self.logs.append)


class TestAddressCheck(TempConf):
    """off_portal : seulement une comparaison, y compris avec un en-tête forgé ou mal formé."""
    def off(self, base_url, **headers):
        with fixture.loco.app.test_request_context("/", base_url=base_url, headers=headers):
            return oidc.off_portal()

    def test_same_name_and_port(self):
        self.write_conf(NAME)
        self.assertFalse(self.off(NAME))
        self.assertFalse(self.off("https://LOCO.lab.local:8443"))
        self.assertTrue(self.off(AGENTS))
        self.assertTrue(self.off("https://loco.lab.local:9443"))

    def test_default_port(self):
        self.write_conf("https://loco.lab.local")
        self.assertFalse(self.off("https://loco.lab.local"))
        self.assertFalse(self.off("https://loco.lab.local:443"))
        self.assertTrue(self.off("http://loco.lab.local"))

    def test_malformed_headers_count_as_elsewhere(self):
        self.write_conf(NAME)
        for host in ("loco.lab.local:abc", "[::1", "a b", ""):
            with fixture.loco.app.test_request_context("/", headers={"Host": host}):
                self.assertTrue(oidc.off_portal(), host)

    def test_behind_a_relay_a_post_compares_what_the_browser_sees(self):
        self.write_conf(NAME)
        self.assertFalse(self.off("https://interne:8443", Origin=NAME))
        self.assertTrue(self.off(NAME, Origin=AGENTS))


class TestSignInByName(OidcTest):
    """Panne 1 : un flux commencé sur l'IP ou sur le nom revient sur le nom, le cookie n'est pas perdu."""
    on_portal = False

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conf = os.path.join(tmp.name, "loco.conf")
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(f'PORTAL_URL="{NAME}"\n')
        for patcher in (mock.patch.object(config, "ETC", tmp.name), mock.patch.object(config, "HTTPS_URL", AGENTS)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.add()
        self.path = f"/login/sso/{DIRECTORY}"
        self.browser = fixture.Browser().c

    def has_flow_cookie(self, response):
        return any(oidc.FLOW_COOKIE in c for c in response.headers.getlist("Set-Cookie"))

    def query(self, response):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(response.headers["Location"]).query))

    def test_from_the_ip_the_browser_goes_to_the_name_before_any_cookie(self):
        r = self.browser.get(self.path, query_string={"next": "/fleet?statut=red"}, base_url=AGENTS)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.has_flow_cookie(r))
        target = urllib.parse.urlsplit(r.headers["Location"])
        self.assertEqual((target.scheme, target.netloc, target.path), ("https", "loco.lab.local:8443", self.path))
        self.assertEqual(self.query(r)["next"], "/fleet?statut=red")

    def test_the_whole_sign_in_started_on_the_ip_ends_on_the_name(self):
        first = self.browser.get(self.path, base_url=AGENTS)
        second = self.browser.get(first.headers["Location"], base_url=NAME)
        self.assertEqual(second.status_code, 302)
        self.assertTrue(self.has_flow_cookie(second))
        query = self.query(second)
        self.assertEqual(query["redirect_uri"], f"{NAME}{self.path}/callback")
        self.assertTrue(query["state"] and query["nonce"] and query["code_challenge"])
        self.idp.nonce = query["nonce"]
        r = self.browser.get(f"{self.path}/callback", query_string={"code": "the-code", "state": query["state"]}, base_url=NAME)
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True)[:300])
        self.assertIn("demande d’accès a été créée", r.get_data(as_text=True))

    def test_a_forged_host_never_reaches_a_url(self):
        for host in ("evil.example", "evil.example:8443", "loco.lab.local.evil.example:8443"):
            r = self.browser.get(self.path, headers={"Host": host}, query_string={"next": "/fleet"})
            self.assertEqual(r.status_code, 302, host)
            self.assertTrue(r.headers["Location"].startswith(NAME + self.path + "?"), r.headers["Location"])
            self.assertNotIn("evil", r.headers["Location"])

    def test_a_malformed_host_is_no_error(self):
        r = self.browser.get(self.path, headers={"Host": "loco.lab.local:abc"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].startswith(NAME))

    def test_no_open_redirect_through_next(self):
        for evil in ("//evil.example/", "https://evil.example/", "/\\evil.example", "javascript:alert(1)"):
            r = self.browser.get(self.path, query_string={"next": evil}, base_url=AGENTS)
            self.assertEqual(self.query(r)["next"], "/", evil)

    def test_behind_a_relay_that_rewrites_host_the_flow_still_starts_and_does_not_loop(self):
        r = self.browser.get(self.path, query_string={"portail": "1"}, base_url="https://interne:8443")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.has_flow_cookie(r))
        self.assertEqual(self.query(r)["redirect_uri"], f"{NAME}{self.path}/callback")

    def test_a_disabled_or_unknown_provider_is_not_redirected(self):
        self.assertEqual(self.browser.get("/login/sso/ffffffffffffffff", base_url=AGENTS).status_code, 404)

    def test_coming_back_on_another_address_says_so_instead_of_expired(self):
        r = self.browser.get(f"{self.path}/callback", query_string={"code": "x", "state": "y"}, base_url=AGENTS)
        page = html.unescape(r.get_data(as_text=True))
        self.assertEqual(r.status_code, 400)
        self.assertIn(NAME, page)
        self.assertIn("URL de retour", page)
        self.assertNotIn("a expiré", page)

    def test_no_cookie_on_the_portal_address_is_still_an_expired_flow(self):
        r = self.browser.get(f"{self.path}/callback", query_string={"code": "x", "state": "y"}, base_url=NAME)
        self.assertEqual(r.status_code, 400)
        self.assertIn("a expiré", html.unescape(r.get_data(as_text=True)))

    def test_the_provider_test_must_start_from_the_portal_address(self):
        r = self.admin.post(f"/accounts/directories/{DIRECTORY}/test", {}, page="/accounts/directories")     # ici localhost
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.has_flow_cookie(r))
        self.assertIn(NAME, html.unescape(self.admin.c.get(r.headers["Location"]).get_data(as_text=True)))

    def test_the_return_url_shown_for_the_provider_follows_the_portal_address(self):
        page = self.admin.c.get("/accounts/directories").get_data(as_text=True)
        self.assertIn(f"{NAME}{self.path}/callback", page)
        self.assertNotIn(f"{AGENTS}{self.path}/callback", page)


class TestAgentsStay(FleetTest):
    """Agents et enrôlement ne suivent pas : ils restent sur l'adresse d'origine, quoi que vaille PORTAL_URL."""
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "loco.conf"), "w", encoding="utf-8") as f:
            f.write(f'PORTAL_URL="{NAME}"\n')
        for patcher in (mock.patch.object(config, "ETC", tmp.name), mock.patch.object(config, "HTTPS_URL", AGENTS)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_announced_addresses_stay_on_the_agents_address(self):
        self.assertEqual(config.portal_url(), NAME)
        with db.transaction() as conn:
            found = addresses.servers(conn)
        self.assertIn(AGENTS, found)
        self.assertNotIn(NAME, found)

    def test_setup_scripts_keep_the_agents_address(self):
        script = self.admin.c.get("/linux/setup.sh").get_data(as_text=True)
        self.assertIn(AGENTS, script)
        self.assertNotIn("loco.lab.local", script)

    def test_every_use_of_the_address_is_on_the_right_side(self):
        # Revue des usages : ce qui sert aux navigateurs passe par portal_url(), le reste garde HTTPS_URL.
        def uses(name):
            return (ROOT / "portal/loco" / name).read_text(encoding="utf-8").count("config.HTTPS_URL")
        self.assertEqual(uses("oidc.py"), 0)
        self.assertEqual(uses("mcp.py"), 1)         # seulement pour reconnaître une origine déjà admise
        self.assertGreaterEqual(uses("fleet.py"), 5)
        self.assertGreaterEqual(uses("clients.py"), 2)
        self.assertGreaterEqual(uses("addresses.py"), 1)

    def test_assistants_are_given_the_portal_address(self):
        with fixture.loco.app.test_request_context("/mcp", base_url=NAME):
            self.assertEqual(mcp._endpoint_url(), f"{NAME}/mcp")
        for origin, accepted in ((NAME, True), (AGENTS, True), ("https://evil.example", False)):
            with fixture.loco.app.test_request_context("/mcp", base_url="https://127.0.0.1:8443", headers={"Origin": origin}):
                self.assertEqual(mcp._origin_ok(), accepted, origin)


class TestPanel(FleetTest):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conf = os.path.join(tmp.name, "loco.conf")
        with open(self.conf, "w", encoding="utf-8") as f:
            f.write(f'PORTAL_URL="{NAME}"\nHOST=192.168.100.48\n')
        for patcher in (mock.patch.object(config, "ETC", tmp.name), mock.patch.object(config, "HTTPS_URL", AGENTS),
                        mock.patch.object(locoadmin, "CONF", self.conf), mock.patch.object(locoadmin, "jobs", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def page(self, client=None, query=""):
        return html.unescape((client or self.admin.c).get("/system" + query).get_data(as_text=True))

    def test_the_panel_shows_both_addresses_and_the_form(self):
        page = self.page()
        self.assertIn('id="adresse-portail"', page)
        self.assertIn(f'value="{NAME}"', page)
        self.assertIn(AGENTS, page)
        self.assertIn("Ajouter ce nom au certificat de loco", page)

    def test_each_provider_return_url_is_listed(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM auth_directories")
        OidcTest.add(self, enabled="1")
        page = self.page()
        self.assertIn("URL de retour à déclarer", page)
        self.assertIn(f"{NAME}/login/sso/{DIRECTORY}/callback", page)
        self.assertIn("La connexion SSO ne marchera plus", page)            # confirmation avant de changer
        with db.transaction() as conn:
            conn.execute("DELETE FROM auth_directories")

    def test_without_provider_there_is_no_list_and_no_confirmation(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM auth_directories")
        page = self.page()
        self.assertNotIn("URL de retour à déclarer", page)
        self.assertNotIn("La connexion SSO ne marchera plus", page)

    def test_a_loco_certificate_can_be_extended_but_not_an_enterprise_one(self):
        with mock.patch.object(locoadmin, "tls_mode", return_value="enterprise"):
            self.assertNotIn("Ajouter ce nom au certificat de loco", self.page())

    def test_a_failed_change_is_reported_and_a_running_one_followed(self):
        failed = {"id": "j1", "action": "set-portal-url", "state": "failed"}
        with mock.patch.object(locoadmin, "jobs", return_value=[failed]):
            self.assertIn("Le dernier changement d'adresse a échoué", self.page())
        running = {"id": "j2", "action": "set-portal-url", "state": "running"}
        with mock.patch.object(locoadmin, "jobs", return_value=[running]):
            self.assertIn("Changement en cours", self.page())
        done = {"id": "j3", "action": "check", "state": "success"}
        with mock.patch.object(locoadmin, "jobs", return_value=[done]):
            self.assertIn("Changement en cours", self.page(query="?attente_portail=j3"))    # l'agent n'a pas encore pris la demande
            self.assertNotIn("Changement en cours", self.page())

    def test_the_change_goes_to_the_root_agent_and_is_audited(self):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request") as request:
            r = self.admin.c.post("/system/portal-url", data={"url": NAME, "extend": "1", "csrf": token})
        self.assertEqual(r.status_code, 302)
        self.assertIn("attente_portail=", r.headers["Location"])
        self.assertIn("#reseau", r.headers["Location"])
        self.assertEqual(request.call_args[0][:2], ("set-portal-url", {"url": NAME, "extend": "1"}))
        with db.transaction() as conn:
            row = conn.execute("SELECT detail FROM audit WHERE action='systeme.adresse-portail' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["detail"], NAME)

    def test_a_refused_address_is_explained_on_the_page(self):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request", side_effect=ValueError("le certificat de loco ne couvre pas x.test")):
            r = self.admin.c.post("/system/portal-url", data={"url": "https://x.test:8443", "csrf": token}, follow_redirects=True)
        self.assertIn("ne couvre pas x.test", html.unescape(r.get_data(as_text=True)))

    def test_a_refusal_is_in_the_language_of_the_person_not_of_the_server(self):
        for lang, expected in (("fr", "le certificat"), ("en", "certificate does not cover"), ("es", "el certificado")):
            with fixture.loco.app.test_request_context("/", headers={"Accept-Language": lang}), self.assertRaisesRegex(ValueError, expected):
                g.lang = lang
                with mock.patch.object(locoadmin, "_served_cert_covers", return_value=False), mock.patch.object(locoadmin, "tls_mode", return_value="loco"), \
                        mock.patch.object(locoadmin, "TLS", "/nonexistent"):
                    locoadmin.portal_params({"url": "https://x.test:8443"}, CONF)

    def test_outside_a_page_the_journal_stays_in_the_server_language(self):
        with mock.patch.object(locoadmin, "LANG", "fr"):
            self.assertEqual(locoadmin.t("oui", "yes", "sí"), "oui")
        with mock.patch.object(locoadmin, "LANG", "es"):
            self.assertEqual(locoadmin.t("oui", "yes", "sí"), "sí")

    def test_it_needs_the_right_to_configure_the_server(self):
        from loco.permissions import ENDPOINTS
        self.assertEqual(ENDPOINTS["system_portal_url"], "system.configure")

    def test_docker_and_windows_have_no_route_but_are_told_how(self):
        with mock.patch.object(config, "SERVER_UPDATES", False):
            token = self.admin.token("/account")
            self.assertEqual(self.admin.c.post("/system/portal-url", data={"url": NAME, "csrf": token}).status_code, 404)
            self.assertIsNone(system.portal_state())
        with mock.patch.object(config, "CONTAINER", True):
            self.assertIn("LOCO_PORTAL_URL", system.https_port_hint())
        with mock.patch.object(config, "CONTAINER", False), mock.patch.object(config, "WINDOWS", True):
            self.assertIn("-PortalUrl", system.https_port_hint())


class TestInstallers(unittest.TestCase):
    def block(self, start, end):
        source = (ROOT / "linux/install.sh").read_text(encoding="utf-8")
        i = source.index(start)
        return source[i:source.index(end, i)]

    def normalized(self, url, port):
        code = f'PORTAL_URL="{url}"; HTTPS_PORT={port}\n' + self.block("# Le port de l'adresse du portail", "\nfi\n") + '\nfi\necho "$PORTAL_URL"'
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=True).stdout.strip()

    def test_the_port_follows_the_interface_port(self):
        self.assertEqual(self.normalized("https://loco.lab.local:8443", 9443), "https://loco.lab.local:9443")
        self.assertEqual(self.normalized("https://loco.lab.local", 8443), "https://loco.lab.local:8443")
        self.assertEqual(self.normalized("https://loco.lab.local:8443", 443), "https://loco.lab.local")
        self.assertEqual(self.normalized("https://[fd00::1]:8443", 9443), "https://[fd00::1]:9443")
        self.assertEqual(self.normalized("", 9443), "")

    def test_the_setting_survives_updates(self):
        source = (ROOT / "linux/install.sh").read_text(encoding="utf-8")
        self.assertIn('PORTAL_URL=""', source.split('[ -f "$ETC/loco.conf" ] && . "$ETC/loco.conf"')[0])      # avant de relire loco.conf
        self.assertIn('PORTAL_URL="$PORTAL_URL"', source.split('cat > "$ETC/loco.conf"')[1])
        self.assertIn("--portal-url)", source)

    def test_install_refuses_what_could_be_sourced_as_shell(self):
        pattern = self.block('[[ "$PORTAL_URL" =~', " ||")
        for url, ok in ((NAME, True), ("", True), ("https://[fd00::1]:8443", True), ('https://x";reboot;"', False), ("https://x$(id)", False),
                        ("http://x", False), ("https://x/chemin", False)):
            p = subprocess.run(["bash", "-c", pattern], env={"PORTAL_URL": url, "PATH": os.environ["PATH"]}, capture_output=True)
            self.assertEqual(p.returncode == 0, ok, url)

    def test_the_agent_validation_and_the_shell_one_refuse_the_same_characters(self):
        for bad in ('"', "$", "`", ";", " ", "\n", "'", "\\", "(", "&", "|", "<", ">"):
            self.assertIsNone(locoadmin.PORTAL_URL_RE.fullmatch(f"https://x{bad}y.test:8443"), repr(bad))

    def test_the_command_exists_on_every_linux_server_and_is_removed(self):
        self.assertIn("/usr/local/sbin/loco-config", (ROOT / "linux/install.sh").read_text(encoding="utf-8"))
        self.assertIn("/usr/local/sbin/loco-config", (ROOT / "linux/remove.sh").read_text(encoding="utf-8"))

    def test_loco_config_portal_url_works_outside_the_appliance(self):
        with mock.patch.object(locoadmin, "ALPINE", False), mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(locoadmin, "_console", return_value=0) as console:
            self.assertEqual(locoadmin.main(["config", "portal-url", NAME, "--add-to-certificate"]), 0)
            console.assert_called_once_with("set-portal-url", {"url": NAME, "extend": "1"})
            console.reset_mock()
            self.assertEqual(locoadmin.main(["config", "portal-url", "--default"]), 0)
            console.assert_called_once_with("set-portal-url", {"url": "", "extend": ""})

    def test_docker_and_windows_expose_the_setting(self):
        self.assertIn("LOCO_PORTAL_URL", (ROOT / "linux/docker/docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn("LOCO_PORTAL_URL", (ROOT / "linux/docker/.env.example").read_text(encoding="utf-8"))
        self.assertIn('--portal-url "${LOCO_PORTAL_URL:-}"', (ROOT / "linux/docker/entrypoint.sh").read_text(encoding="utf-8"))
        self.assertIn("$PortalUrl", (ROOT / "windows/install.ps1").read_text(encoding="utf-8"))
        self.assertIn('"LOCO_PORTAL_URL": cfg.get("portal_url", "")', (ROOT / "portal/locowin.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
