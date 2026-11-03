"""Changement du port de l'interface : validation, ouverture à côté de l'ancien, page Système, suivi par les agents et la HA."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error
from unittest import mock

import locoadmin
import locoha
from test_fleet import FleetTest, Agent, LINUX, device
from loco import config, db, system

ROOT = Path(__file__).resolve().parents[1]
CONF = {"HTTPS_PORT": "8443", "PORTAL_PORT": "8088", "HTTP_ENABLED": "1", "WU_ENABLED": "1", "HTTPS_OLD_PORTS": ""}


class TestPortValidation(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(locoadmin, "ha_configured", return_value=False),
                        mock.patch.object(locoadmin, "_port_listening", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def refused(self, port, conf=None):
        with self.assertRaises(ValueError):
            locoadmin.https_port_params({"port": port}, dict(CONF, **(conf or {})))

    def test_a_free_port_is_accepted_and_normalized(self):
        self.assertEqual(locoadmin.https_port_params({"port": " 09443 "}, CONF), {"port": "9443"})

    def test_invalid_values_are_refused(self):
        for port in ("", "abc", "0", "65536", "-1", "9443; reboot", "94 43"):
            self.refused(port)

    def test_current_port_and_ports_browsers_block_are_refused(self):
        self.refused("8443")
        for port in ("22", "6000", "10080"):
            self.refused(port)

    def test_ports_loco_already_uses_are_refused_according_to_the_features_in_use(self):
        for port in ("8088", "8089", "3142", "80", "443", "3128"):
            self.refused(port)
        self.assertEqual(locoadmin.https_port_params({"port": "443"}, dict(CONF, WU_ENABLED="0")), {"port": "443"})
        self.assertEqual(locoadmin.https_port_params({"port": "8088"}, dict(CONF, HTTP_ENABLED="0")), {"port": "8088"})

    def test_a_port_held_by_another_service_is_refused_but_going_back_to_an_old_one_is_not(self):
        with mock.patch.object(locoadmin, "_port_listening", return_value=True):
            self.refused("9443")
            self.assertEqual(locoadmin.https_port_params({"port": "8443"}, dict(CONF, HTTPS_PORT="9443", HTTPS_OLD_PORTS="8443")),
                             {"port": "8443"})

    def test_closing_needs_an_old_port(self):
        with self.assertRaises(ValueError):
            locoadmin.https_port_params({"close": "1"}, CONF)
        self.assertEqual(locoadmin.https_port_params({"close": "1"}, dict(CONF, HTTPS_OLD_PORTS="8443")), {"close": "1"})

    def test_high_availability_blocks_the_change(self):
        with mock.patch.object(locoadmin, "ha_configured", return_value=True), self.assertRaises(ValueError):
            locoadmin.https_port_params({"port": "9443"}, CONF)
        with mock.patch.object(locoadmin, "ha_configured", return_value=True), self.assertRaises(ValueError):
            locoadmin.https_port_params({"close": "1"}, dict(CONF, HTTPS_OLD_PORTS="8443"))


class TestPortListening(unittest.TestCase):
    def test_listening_socket_is_seen_and_closed_one_is_not(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            self.assertFalse(locoadmin._port_listening(port))
            s.listen()
            self.assertTrue(locoadmin._port_listening(port))


class TestMove(unittest.TestCase):
    def moved(self, params, conf):
        logged = []
        with mock.patch.object(locoadmin, "read_conf", return_value=dict(CONF, **conf)), \
                mock.patch.object(locoadmin, "ha_configured", return_value=False), \
                mock.patch.object(locoadmin, "_port_listening", return_value=False), \
                mock.patch.object(locoadmin, "_reinstall", return_value=True) as reinstall:
            self.assertTrue(locoadmin.set_https_port(params, logged.append))
        return reinstall.call_args[0][0]

    def test_the_previous_port_stays_open(self):
        self.assertEqual(self.moved({"port": "9443"}, {}), ["--https-port", "9443", "--https-old-ports", "8443"])

    def test_a_second_move_keeps_every_port_machines_may_still_use(self):
        self.assertEqual(self.moved({"port": "10443"}, {"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}),
                         ["--https-port", "10443", "--https-old-ports", "8443 9443"])

    def test_going_back_to_the_old_port_leaves_the_other_one_open(self):
        self.assertEqual(self.moved({"port": "8443"}, {"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}),
                         ["--https-port", "8443", "--https-old-ports", "9443"])

    def test_closing_only_touches_the_old_ports(self):
        self.assertEqual(self.moved({"close": "1"}, {"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}), ["--https-old-ports", ""])

    def test_a_failed_install_restores_the_previous_configuration(self):
        with tempfile.TemporaryDirectory() as source:
            os.makedirs(os.path.join(source, "linux"))
            Path(source, "linux", "install.sh").write_text("")
            with mock.patch.object(locoadmin, "read_conf", return_value={"SOURCE_DIR": source}), \
                    mock.patch.object(locoadmin, "_snapshot") as snapshot, \
                    mock.patch.object(locoadmin, "_install", return_value=1), \
                    mock.patch.object(locoadmin, "_rollback") as rollback:
                snapshot.return_value.__enter__.return_value = "work"
                self.assertFalse(locoadmin._reinstall(["--https-port", "9443"], lambda _text: None))
            rollback.assert_called_once()

    def test_the_action_is_accepted_on_any_linux_server_and_revalidated(self):
        self.assertNotIn("set-https-port", locoadmin.APPLIANCE_ACTIONS)
        with mock.patch.object(locoadmin, "read_conf", return_value=CONF), \
                mock.patch.object(locoadmin, "ha_configured", return_value=False), self.assertRaises(ValueError):
            locoadmin.request("set-https-port", {"port": "22"})


class TestNginxTemplate(unittest.TestCase):
    def test_old_ports_are_filled_on_every_platform(self):
        self.assertIn("@HTTPS_OLD_LISTEN@", (ROOT / "conf/nginx-portal.conf").read_text())
        self.assertIn('"HTTPS_OLD_LISTEN": ""', (ROOT / "portal/locowin.py").read_text())
        self.assertIn("LRT_HTTPS_OLD_LISTEN", (ROOT / "linux/install.sh").read_text())


class TestSystemPage(FleetTest):
    def page(self, conf=None, port="8443", jobs=(), query=""):
        with mock.patch.object(config, "HTTPS_URL", f"https://localhost:{conf.get('HTTPS_PORT', '8443') if conf else '8443'}"), \
                mock.patch.object(locoadmin, "read_conf", return_value=dict(CONF, **(conf or {}))), \
                mock.patch.object(locoadmin, "jobs", return_value=list(jobs)):
            r = self.admin.c.get("/system" + query, base_url=f"https://localhost:{port}")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_change_form_is_offered_with_the_current_port(self):
        page = self.page()
        self.assertIn("Changer le port", page)
        self.assertIn('value="8443"', page)
        self.assertNotIn("Fermer l&#39;ancien port", page)

    def test_on_the_old_port_the_new_one_must_be_opened_before_closing(self):
        page = self.page({"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}, port="8443")
        self.assertIn('href="https://localhost:9443/system#reseau"', page)
        self.assertNotIn('name="close"', page)
        self.assertIn("Ancien port de l&#39;interface", page)

    def test_on_the_new_port_the_old_one_can_be_closed(self):
        page = self.page({"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}, port="9443")
        self.assertIn('name="close"', page)
        self.assertNotIn('href="https://localhost:9443/system#reseau"', page)

    def test_machines_that_do_not_know_the_new_port_are_counted(self):
        Agent(LINUX, "192.168.43.70").enroll()
        page = self.page({"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}, port="9443")
        self.assertIn("ne connaissent pas encore le nouveau port : 1", page)

    def test_sso_return_address_is_shown_during_the_transition(self):
        directory = {"id": "abc", "kind": "oidc", "label": "Authentik"}
        with mock.patch("loco.directories.enabled_directories", return_value=[directory]):
            page = self.page({"HTTPS_PORT": "9443", "HTTPS_OLD_PORTS": "8443"}, port="9443")
        self.assertIn("https://localhost:9443", page)
        self.assertIn("abc/callback", page)

    def test_a_running_change_reloads_the_page_and_a_failed_one_is_reported(self):
        running = {"id": "20260101-000000-abcdef", "action": "set-https-port", "state": "running"}
        page = self.page(jobs=[running])
        self.assertIn("data-operations=", page)
        self.assertIn(f'{running["id"]}:running', page)
        self.assertNotIn('name="port"', page)
        self.assertIn("a échoué", self.page(jobs=[dict(running, state="failed")]))

    def test_a_request_the_agent_has_not_taken_yet_already_counts_as_running(self):
        finished = {"id": "20260101-000000-abcdef", "action": "set-https-port", "state": "success"}
        waiting = self.page(jobs=[finished], query=f"?attente={finished['id']}")
        self.assertIn(f'data-signature="{finished["id"]}:success"', waiting)
        self.assertNotIn('name="port"', waiting)
        self.assertIn('name="port"', self.page(jobs=[dict(finished, id="20260101-000001-abcdef")], query=f"?attente={finished['id']}"))
        self.assertIn('data-signature=""', self.page(query="?attente=aucune"))

    def test_high_availability_replaces_the_form_by_an_explanation(self):
        with mock.patch.object(locoadmin, "ha_configured", return_value=True):
            page = self.page()
        self.assertNotIn('name="port"', page)
        self.assertIn("quittez-la pour le changer", page)


class TestRoutes(FleetTest):
    def submit(self, data, host):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), \
                mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "read_conf", return_value=dict(CONF, HTTPS_PORT="9443", HTTPS_OLD_PORTS="8443")), \
                mock.patch.object(locoadmin, "request") as request:
            r = self.admin.c.post("/system/https-port", data=dict(data, csrf=token), base_url=f"https://{host}")
        self.assertEqual(r.status_code, 302)
        self.assertIn("#reseau", r.headers["Location"])
        self.location = r.headers["Location"]
        return request

    def test_port_change_goes_to_the_root_agent_and_is_audited(self):
        with mock.patch.object(config, "HTTPS_URL", "https://localhost:8443"):
            request = self.submit({"port": "9443"}, "localhost:8443")
        request.assert_called_once()
        self.assertEqual(request.call_args[0][:2], ("set-https-port", {"port": "9443"}))
        self.assertIn("attente=", self.location)
        with db.transaction() as conn:
            row = conn.execute("SELECT detail FROM audit WHERE action='systeme.port' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["detail"], "9443")

    def test_a_port_change_restarts_the_count_of_machines_that_know_it(self):
        agent = Agent(LINUX, "192.168.43.71")
        agent.enroll()
        agent.ident["servers"] = ["https://localhost:8443", "https://localhost:9443"]
        agent.checkin()
        self.assertTrue(device(agent.uid)["servers"])
        with mock.patch.object(config, "HTTPS_URL", "https://localhost:8443"):
            self.submit({"port": "9443"}, "localhost:8443")
        self.assertEqual(device(agent.uid)["servers"], "")

    def test_closing_is_only_accepted_from_the_current_port(self):
        with mock.patch.object(config, "HTTPS_URL", "https://localhost:9443"):
            self.submit({"close": "1"}, "localhost:8443").assert_not_called()
            self.assertNotIn("attente=", self.location)
            request = self.submit({"close": "1"}, "localhost:9443")
        self.assertEqual(request.call_args[0][:2], ("set-https-port", {"close": "1"}))

    def test_docker_and_windows_get_an_explanation_and_no_route(self):
        with mock.patch.object(config, "SERVER_UPDATES", False):
            token = self.admin.token("/account")
            self.assertEqual(self.admin.c.post("/system/https-port", data={"port": "9443", "csrf": token}).status_code, 404)
            self.assertIsNone(system.https_port_state())
        with mock.patch.object(config, "CONTAINER", True):
            self.assertIn("LOCO_HTTPS_PORT", system.https_port_hint())
        with mock.patch.object(config, "CONTAINER", False), mock.patch.object(config, "WINDOWS", True):
            self.assertIn("-HttpsPort", system.https_port_hint())


class TestAgentFollowsSources(unittest.TestCase):
    OLD, NEW = "https://192.168.42.65:8443", "https://192.168.42.65:9443"

    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_port", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)
        self.agent.CONFIG = str(self.dir / "client.json")
        self.agent.log = lambda _text: None
        self.agent.SOURCE_FILES = (str(self.dir / "loco*.sources"), str(self.dir / "loco*.repo"))

    def test_https_sources_follow_and_nothing_else_is_touched(self):
        https = self.dir / "loco.sources"
        https.write_text(f"Types: deb\nURIs: {self.OLD}/apt/repositories/debian/trixie/\nSuites: ./\n")
        https.chmod(0o640)
        rpm = self.dir / "loco.repo"
        rpm.write_text(f"[loco]\nbaseurl={self.OLD}/rpm/repositories/el9/\n")
        http = self.dir / "loco-http.sources"
        http.write_text("URIs: http://192.168.42.65:8088/apt/\n")
        similar = self.dir / "loco-other.sources"
        similar.write_text(f"URIs: {self.OLD}0/apt/\n")
        foreign = self.dir / "debian.sources"
        foreign.write_text(f"URIs: {self.OLD}/apt/\n")
        with mock.patch.object(self.agent.shutil, "which", return_value=None):
            self.agent.follow_sources(self.OLD, self.NEW)
        self.assertIn(f"URIs: {self.NEW}/apt/repositories/debian/trixie/", https.read_text())
        self.assertEqual(https.stat().st_mode & 0o777, 0o640)
        self.assertIn(f"baseurl={self.NEW}/rpm/", rpm.read_text())
        self.assertEqual(http.read_text(), "URIs: http://192.168.42.65:8088/apt/\n")
        self.assertIn(self.OLD + "0/", similar.read_text())
        self.assertIn(self.OLD, foreign.read_text())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if "loco-new" in p.name), [])

    def test_flatpak_remotes_follow(self):
        remotes = f"loco\t{self.OLD}/flatpak/repo/\nflathub\thttps://dl.flathub.org/repo/\n"
        calls = []

        def run(args, **_kw):
            calls.append(args)
            return mock.Mock(stdout=remotes if args[1] == "remotes" else "")
        with mock.patch.object(self.agent.shutil, "which", return_value="/usr/bin/flatpak"), \
                mock.patch.object(self.agent.subprocess, "run", run):
            self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(calls[1:], [["flatpak", "remote-modify", "--system", "loco", f"--url={self.NEW}/flatpak/repo/"]])

    def test_sources_follow_when_the_agent_falls_back_to_the_new_port(self):
        source = self.dir / "loco.sources"
        source.write_text(f"URIs: {self.OLD}/apt/\n")
        conf = {"server": self.OLD, "uid": "u", "token": "t", "servers": [self.NEW]}

        def call(server, path, data, token=None, timeout=60):
            if server == self.OLD:
                raise urllib.error.URLError(OSError(111, "Connection refused"))
            return {"commands": [], "servers": [self.NEW]}
        with mock.patch.object(self.agent, "call", call), mock.patch.object(self.agent.shutil, "which", return_value=None):
            self.agent.checkin(conf, {})
        self.assertIn(self.NEW, source.read_text())
        self.assertEqual(json.loads(Path(self.agent.CONFIG).read_text())["server"], self.NEW)

    def test_sources_stay_on_the_old_port_while_it_answers(self):
        source = self.dir / "loco.sources"
        source.write_text(f"URIs: {self.OLD}/apt/\n")
        conf = {"server": self.OLD, "uid": "u", "token": "t", "servers": []}
        with mock.patch.object(self.agent, "call", return_value={"commands": [], "servers": [self.NEW]}):
            self.agent.checkin(conf, {})
        self.assertIn(self.OLD, source.read_text())
        saved = json.loads(Path(self.agent.CONFIG).read_text())
        self.assertEqual((saved["server"], saved["servers"]), (self.OLD, [self.NEW]))


class TestAgentFollowsNewAddress(unittest.TestCase):
    """Changement d'IP : proxy APT et adresses HTTP suivent aussi, sur leurs propres ports."""
    OLD, NEW = "https://192.168.42.226:8443", "https://192.168.42.227:8443"

    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_ip", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)
        self.agent.CONFIG = str(self.dir / "client.json")
        self.agent.log = lambda _text: None
        self.agent.SOURCE_FILES = (str(self.dir / "loco*.sources"), str(self.dir / "loco*.repo"), str(self.dir / "01loco-proxy"))
        self.which = mock.patch.object(self.agent.shutil, "which", return_value=None)
        self.which.start()
        self.addCleanup(self.which.stop)

    def write(self, name, text, mode=0o644):
        path = self.dir / name
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_apt_proxy_follows_including_its_exception(self):
        proxy = self.write("01loco-proxy", 'Acquire::http::Proxy "http://192.168.42.226:3142";\nAcquire::https::Proxy "DIRECT";\n'
                                           'Acquire::http::Proxy::192.168.42.226 "DIRECT";\n')
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(proxy.read_text(), 'Acquire::http::Proxy "http://192.168.42.227:3142";\nAcquire::https::Proxy "DIRECT";\n'
                                            'Acquire::http::Proxy::192.168.42.227 "DIRECT";\n')

    def test_http_sources_keep_their_port(self):
        source = self.write("loco.sources", "URIs: http://192.168.42.226:8088/apt/repositories/debian/trixie/\n")
        repo = self.write("loco-cache.repo", "[baseos]\nbaseurl=http://192.168.42.226:8088/rpmcache/rocky/9/\n")
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertIn("http://192.168.42.227:8088/apt/", source.read_text())
        self.assertIn("http://192.168.42.227:8088/rpmcache/", repo.read_text())

    def test_port_and_address_can_change_together(self):
        source = self.write("loco.sources", "URIs: https://192.168.42.226:8443/apt/\nURIs: http://192.168.42.226:8088/apt/\n")
        self.agent.follow_sources(self.OLD, "https://192.168.42.227:9443")
        self.assertEqual(source.read_text(), "URIs: https://192.168.42.227:9443/apt/\nURIs: http://192.168.42.227:8088/apt/\n")

    def test_other_hosts_are_left_alone(self):
        text = ("URIs: http://192.168.42.2260:8088/apt/\nURIs: http://192.168.42.226.exemple.lan:8088/apt/\n"
                "URIs: http://1192.168.42.226:8088/apt/\nURIs: http://deb.debian.org/debian/\n")
        source = self.write("loco.sources", text)
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(source.read_text(), text)

    def test_a_proxy_on_another_loco_is_not_touched(self):
        text = 'Acquire::http::Proxy "http://10.0.0.5:3142";\nAcquire::http::Proxy::10.0.0.5 "DIRECT";\n'
        proxy = self.write("01loco-proxy", text)
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(proxy.read_text(), text)

    def test_a_port_change_leaves_http_addresses_and_proxy_alone(self):
        proxy = self.write("01loco-proxy", 'Acquire::http::Proxy "http://192.168.42.226:3142";\n')
        source = self.write("loco.sources", "URIs: http://192.168.42.226:8088/apt/\n")
        self.agent.follow_sources(self.OLD, "https://192.168.42.226:9443")
        self.assertIn(":3142", proxy.read_text())
        self.assertIn("http://192.168.42.226:8088/apt/", source.read_text())

    def test_file_mode_is_kept_and_no_temporary_file_stays(self):
        proxy = self.write("01loco-proxy", 'Acquire::http::Proxy "http://192.168.42.226:3142";\n', 0o640)
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(proxy.stat().st_mode & 0o777, 0o640)
        self.assertEqual([p.name for p in self.dir.iterdir() if "loco-new" in p.name], [])

    def test_names_and_ipv6_addresses(self):
        self.assertEqual(self.agent._url_host("https://loco.lan"), "loco.lan")
        self.assertEqual(self.agent._url_host("https://loco.lan:8443"), "loco.lan")
        self.assertEqual(self.agent._url_host("https://[fd00::1]:8443"), "[fd00::1]")
        self.assertEqual(self.agent._url_host("https://[fd00::1]"), "[fd00::1]")
        moved = self.agent._moved('Acquire::http::Proxy "http://[fd00::1]:3142";', "https://[fd00::1]:8443", "https://loco.lan:8443")
        self.assertEqual(moved, 'Acquire::http::Proxy "http://loco.lan:3142";')

    def test_flatpak_http_remote_follows(self):
        self.which.stop()
        remotes = "loco\thttp://192.168.42.226:8088/flatpak/repo/\nflathub\thttps://dl.flathub.org/repo/\n"
        calls = []

        def run(args, **_kw):
            calls.append(args)
            return mock.Mock(stdout=remotes if args[1] == "remotes" else "")
        with mock.patch.object(self.agent.shutil, "which", return_value="/usr/bin/flatpak"), mock.patch.object(self.agent.subprocess, "run", run):
            self.agent.follow_sources(self.OLD, self.NEW)
        self.which.start()
        self.assertEqual(calls[1:], [["flatpak", "remote-modify", "--system", "loco", "--url=http://192.168.42.227:8088/flatpak/repo/"]])

    def test_https_sources_follow(self):
        source = self.write("loco.sources", f"URIs: {self.OLD}/apt/\n")
        self.agent.follow_sources(self.OLD, self.NEW)
        self.assertEqual(source.read_text(), f"URIs: {self.NEW}/apt/\n")

    def test_proxy_follows_when_the_agent_falls_back_to_the_new_address(self):
        proxy = self.write("01loco-proxy", 'Acquire::http::Proxy "http://192.168.42.226:3142";\n')
        conf = {"server": self.OLD, "uid": "u", "token": "t", "servers": [self.NEW]}

        def call(server, path, data, token=None, timeout=60):
            if server == self.OLD:
                raise urllib.error.URLError(OSError(113, "No route to host"))
            return {"commands": [], "servers": [self.NEW]}
        with mock.patch.object(self.agent, "call", call):
            self.agent.checkin(conf, {})
        self.assertIn("192.168.42.227:3142", proxy.read_text())

    def test_proxy_stays_on_the_old_address_while_it_answers(self):
        proxy = self.write("01loco-proxy", 'Acquire::http::Proxy "http://192.168.42.226:3142";\n')
        conf = {"server": self.OLD, "uid": "u", "token": "t", "servers": []}
        with mock.patch.object(self.agent, "call", return_value={"commands": [], "servers": [self.NEW]}):
            self.agent.checkin(conf, {})
        self.assertIn("192.168.42.226:3142", proxy.read_text())


class TestHighAvailabilityPort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(locoha, "ETC", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def url(self, **kw):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(locoha.urllib.request, "urlopen", return_value=response) as urlopen, \
                mock.patch.object(locoha, "_context"):
            locoha.call("192.168.1.11", "/ha/api/status", "token", **kw)
        return urlopen.call_args[0][0].full_url

    def test_health_and_peer_calls_use_the_configured_port(self):
        self.assertEqual(locoha.https_port(), "8443")
        Path(self.tmp.name, "loco.conf").write_text('HOST=192.168.1.10\nHTTPS_PORT=9443\nHTTPS_OLD_PORTS=""\n')
        self.assertEqual(locoha.https_port(), "9443")
        self.assertEqual(self.url(), "https://192.168.1.11:9443/ha/api/status")

    def test_a_standby_being_paired_uses_the_port_of_the_primary(self):
        self.assertEqual(self.url(port="9443"), "https://192.168.1.11:9443/ha/api/status")
        with mock.patch.object(locoha, "node", return_value={"peer_port": "10443"}):
            self.assertEqual(self.url(), "https://192.168.1.11:10443/ha/api/status")

    def test_primary_address_is_split_from_its_port(self):
        for value, expected in (("192.168.1.11", ("192.168.1.11", "")), ("192.168.1.11:9443", ("192.168.1.11", "9443")),
                                ("https://192.168.1.11:9443/", ("192.168.1.11", "9443")), ("fd00::1", ("fd00::1", "")),
                                ("[fd00::1]:9443", ("fd00::1", "9443")), ("https://[fd00::1]/", ("fd00::1", ""))):
            self.assertEqual(locoha._split_address(value), expected)


if __name__ == "__main__":
    unittest.main()
