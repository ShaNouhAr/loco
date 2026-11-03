"""Noms internes : validation par l'agent root, bloc dédié de /etc/hosts qui survit aux réécritures, page Système et proposition
par le diagnostic d'un fournisseur SSO. RESOLVERS et resolv.conf ne sont jamais touchés."""
import html
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

import locoadmin
import locoha
from test_fleet import FleetTest
from test_oidc import DIRECTORY, ISSUER, OidcTest
from loco import config, db, system
from loco.permissions import ENDPOINTS

ROOT = Path(__file__).resolve().parents[1]
BEGIN, END = locoadmin.HOSTS_BEGIN, locoadmin.HOSTS_END
BASE = "127.0.0.1\tlocalhost\n::1\t\tlocalhost\n192.168.42.226\tloco\n"


class TempHosts(unittest.TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hosts, self.source = os.path.join(tmp.name, "hosts"), os.path.join(tmp.name, "internal-hosts")
        with open(self.hosts, "w", encoding="utf-8") as f:
            f.write(BASE)
        for patcher in (mock.patch.object(locoadmin, "HOSTS_FILE", self.hosts), mock.patch.object(locoadmin, "INTERNAL_HOSTS", self.source),
                        mock.patch.object(locoadmin, "ha_configured", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.logs = []

    def read(self):
        with open(self.hosts, encoding="utf-8") as f:
            return f.read()

    def add(self, name="sso.lab.local", address="192.168.100.10"):
        return locoadmin.set_internal_hosts({"op": "add", "name": name, "address": address}, self.logs.append)


class TestValidation(unittest.TestCase):
    def test_names_and_addresses_are_normalized(self):
        self.assertEqual(locoadmin.hosts_params({"op": "add", "name": " SSO.Lab.Local. ", "address": " 192.168.100.10 "}),
                         {"op": "add", "name": "sso.lab.local", "address": "192.168.100.10"})
        self.assertEqual(locoadmin.hosts_params({"op": "add", "name": "sso", "address": "fd00::1"})["address"], "fd00::1")
        self.assertEqual(locoadmin.hosts_params({"op": "remove", "name": "sso.lab.local", "address": "ignorée"}), {"op": "remove", "name": "sso.lab.local"})

    def test_what_could_add_a_line_to_the_file_is_refused(self):
        for name in ("sso\n1.2.3.4 evil", "sso lab", "sso;reboot", "#sso", "", "-sso", "sso-", "a" * 64 + ".test", "x." * 130 + "test", "sso\tlab", "*.lab.local"):
            with self.assertRaises(ValueError, msg=repr(name)):
                locoadmin.hosts_params({"op": "add", "name": name, "address": "192.168.100.10"})

    def test_bad_addresses_are_refused(self):
        for address in ("", "sso", "1.2.3", "1.2.3.4\n5.6.7.8 evil", "1.2.3.4 evil", "0.0.0.0", "::", "127.0.0.1", "::1", "224.0.0.1"):
            with self.assertRaises(ValueError, msg=repr(address)):
                locoadmin.hosts_params({"op": "add", "name": "sso", "address": address})

    def test_names_of_this_server_are_reserved(self):
        for name in ("localhost", "foo.localhost", socket.gethostname()):
            with self.assertRaises(ValueError, msg=name):
                locoadmin.hosts_params({"op": "add", "name": name, "address": "192.168.100.10"})

    def test_unknown_operation(self):
        with self.assertRaises(ValueError):
            locoadmin.hosts_params({"op": "drop", "name": "sso"})

    def test_the_root_agent_validates_again(self):
        self.assertIn("set-internal-hosts", locoadmin.ACTIONS)
        self.assertNotIn("set-internal-hosts", locoadmin.APPLIANCE_ACTIONS)
        with mock.patch.object(locoadmin, "agent_running", return_value=True), self.assertRaises(ValueError):
            locoadmin.request("set-internal-hosts", {"op": "add", "name": "x\ny", "address": "1.2.3.4"})
        with self.assertRaises(ValueError):
            locoadmin._operation("set-internal-hosts", {"op": "add", "name": "x y", "address": "1.2.3.4"}, print)
        source = (ROOT / "portal/locoadmin.py").read_text(encoding="utf-8")
        self.assertIn('hosts_params(req.get("params"))', source[source.index('elif req["action"] == "set-internal-hosts"'):][:160])


class TestBlock(TempHosts):
    def test_the_block_is_added_and_the_rest_kept(self):
        self.add()
        self.assertEqual(self.read(), f"{BASE}{BEGIN}\n192.168.100.10\tsso.lab.local\n{END}\n")

    def test_changing_a_name_replaces_it_and_keeps_the_others(self):
        self.add()
        self.add("idp.lab.local", "192.168.100.11")
        self.add("sso.lab.local", "192.168.100.99")
        text = self.read()
        self.assertIn("192.168.100.99\tsso.lab.local", text)
        self.assertIn("192.168.100.11\tidp.lab.local", text)
        self.assertNotIn("192.168.100.10", text)
        self.assertEqual(text.count(BEGIN), 1)

    def test_removing_the_last_name_removes_the_block(self):
        self.add()
        locoadmin.set_internal_hosts({"op": "remove", "name": "sso.lab.local"}, self.logs.append)
        self.assertEqual(self.read(), BASE)

    def test_setup_loco_rewriting_the_file_does_not_lose_the_names(self):
        self.add()
        with open(self.hosts, "w", encoding="utf-8") as f:              # bin/setup-loco : printf … > /etc/hosts
            f.write("127.0.0.1\tlocalhost\n::1\t\tlocalhost\n10.0.0.2\tloco\n")
        self.assertTrue(locoadmin.apply_internal_hosts())
        self.assertIn("192.168.100.10\tsso.lab.local", self.read())
        self.assertIn("10.0.0.2\tloco", self.read())

    def test_setup_loco_renaming_the_server_does_not_damage_them(self):
        self.add()
        subprocess.run(["sed", "-i", r"s/\t[^\t]*$/\tnouveau/; 1,2s/\tnouveau$/\tlocalhost/", self.hosts], check=True)   # bin/setup-loco, name()
        self.assertTrue(locoadmin.apply_internal_hosts())
        self.assertIn("192.168.100.10\tsso.lab.local", self.read())
        self.assertIn("192.168.42.226\tnouveau", self.read())
        self.assertEqual(self.read().count(BEGIN), 1)

    def test_nothing_is_written_when_it_is_already_right(self):
        self.add()
        os.utime(self.hosts, (1, 1))
        self.assertFalse(locoadmin.apply_internal_hosts())
        self.assertEqual(os.stat(self.hosts).st_mtime, 1)

    def test_a_file_without_names_stays_identical_to_the_byte(self):
        with open(self.hosts, "w", encoding="utf-8") as f:
            f.write("127.0.0.1 localhost")                              # sans fin de ligne
        self.assertFalse(locoadmin.apply_internal_hosts())
        self.assertEqual(self.read(), "127.0.0.1 localhost")

    def test_a_broken_block_never_takes_the_end_of_the_file_with_it(self):
        with open(self.hosts, "w", encoding="utf-8") as f:
            f.write(f"{BASE}{BEGIN}\n10.0.0.5\tgarder.lab\n")
        locoadmin.apply_internal_hosts()
        self.assertIn("10.0.0.5\tgarder.lab", self.read())
        self.assertNotIn(BEGIN, self.read())

    def test_a_tampered_list_never_reaches_the_file(self):
        with open(self.source, "w", encoding="utf-8") as f:
            f.write("sso.lab.local 192.168.100.10\nmauvais;nom 1.2.3.4\nevil.lab pas-une-ip\ntrop de champs ici\n")
        locoadmin.apply_internal_hosts()
        self.assertEqual(self.read(), f"{BASE}{BEGIN}\n192.168.100.10\tsso.lab.local\n{END}\n")

    def test_a_mounted_file_is_written_in_place(self):
        self.add()
        with open(self.hosts, "w", encoding="utf-8") as f:
            f.write(BASE)
        with mock.patch.object(os, "replace", side_effect=OSError(16, "busy")):
            self.assertTrue(locoadmin.apply_internal_hosts())
        self.assertIn("sso.lab.local", self.read())
        self.assertFalse(os.path.exists(self.hosts + ".loco-tmp"))

    def test_the_file_stays_readable_by_everyone(self):
        self.add()
        self.assertEqual(stat.S_IMODE(os.stat(self.hosts).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(self.source).st_mode), 0o644)

    def test_the_stamp_sees_a_change_of_either_file(self):
        self.add()
        before = locoadmin._hosts_stamp()
        os.utime(self.hosts, (1, 1))
        self.assertNotEqual(locoadmin._hosts_stamp(), before)
        before = locoadmin._hosts_stamp()
        os.utime(self.source, (1, 1))
        self.assertNotEqual(locoadmin._hosts_stamp(), before)


class TestLimits(TempHosts):
    def test_too_many_names(self):
        with mock.patch.object(locoadmin, "MAX_INTERNAL_HOSTS", 2):
            self.add("a.lab")
            self.add("b.lab")
            self.add("a.lab", "192.168.100.20")                         # en changer un reste possible
            with self.assertRaises(ValueError):
                self.add("c.lab")
        self.assertNotIn("c.lab", self.read())

    def test_the_standby_server_refuses_the_list_comes_from_the_primary(self):
        ha = mock.Mock()
        ha.role.return_value = "standby"
        with mock.patch.object(locoadmin, "ha_configured", return_value=True), mock.patch.object(locoadmin, "_ha", return_value=ha), self.assertRaises(ValueError):
            self.add()
        self.assertEqual(self.read(), BASE)

    def test_the_list_is_replicated_in_high_availability(self):
        self.assertNotIn("internal-hosts", locoha.ETC_SKIP)
        self.assertIn('INTERNAL_HOSTS = os.path.join(ETC, "internal-hosts")', (ROOT / "portal/locoadmin.py").read_text(encoding="utf-8"))


class TestOtherFilesAreLeftAlone(unittest.TestCase):
    def test_resolvers_and_resolv_conf_are_never_touched(self):
        source = (ROOT / "portal/locoadmin.py").read_text(encoding="utf-8")
        code = source[source.index("def hosts_params"):source.index("def _valid_ip")]
        self.assertNotIn("resolv.conf", code)
        self.assertNotIn('"RESOLVERS', code)
        self.assertNotIn("_set_conf", code)


class TestInstallers(unittest.TestCase):
    def test_every_install_and_update_applies_the_block(self):
        self.assertIn("locoadmin.py\" hosts-apply", (ROOT / "linux/install.sh").read_text(encoding="utf-8"))
        with mock.patch.object(locoadmin, "apply_internal_hosts") as apply, mock.patch.object(os, "geteuid", return_value=0):
            self.assertEqual(locoadmin.main(["hosts-apply"]), 0)
        apply.assert_called_once()

    def test_removing_loco_removes_the_block_only(self):
        line = next(ln for ln in (ROOT / "linux/remove.sh").read_text(encoding="utf-8").splitlines() if "noms internes" in ln)
        with tempfile.NamedTemporaryFile("w", suffix="hosts", delete=False) as f:
            f.write(f"{BASE}{BEGIN}\n10.0.0.1\tsso\n{END}\n10.0.0.9\tgarde\n")
        self.addCleanup(os.unlink, f.name)
        subprocess.run(["bash", "-c", line.replace("/etc/hosts", f.name)], check=True)
        with open(f.name, encoding="utf-8") as result:
            self.assertEqual(result.read(), f"{BASE}10.0.0.9\tgarde\n")

    def test_the_marker_is_the_one_the_scripts_look_for(self):
        self.assertTrue(BEGIN.startswith("# BEGIN loco : noms internes"))
        self.assertEqual(END, "# END loco")


class TestPanel(FleetTest):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(locoadmin, "jobs", return_value=[]), mock.patch.object(locoadmin, "read_internal_hosts", return_value={"sso.lab.local": "192.168.100.10"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, **data):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request") as request, mock.patch.object(system, "_wait_for_job", return_value={"state": "success"}) as wait:
            r = self.admin.c.post("/system/internal-hosts", data=dict(data, csrf=token))
        self.request, self.wait = request, wait
        self.assertEqual(r.status_code, 302)
        return r

    def test_the_page_lists_the_names_and_says_what_it_does_not_touch(self):
        page = html.unescape(self.admin.c.get("/system").get_data(as_text=True))
        self.assertIn('id="noms-internes"', page)
        self.assertIn("sso.lab.local", page)
        self.assertIn("192.168.100.10", page)
        self.assertIn("Windows Update garde ses propres DNS", page)

    def test_adding_goes_to_the_root_agent_and_is_audited(self):
        r = self.send(name="idp.lab.local", address="192.168.100.11")
        self.assertEqual(self.request.call_args[0][:2], ("set-internal-hosts", {"op": "add", "name": "idp.lab.local", "address": "192.168.100.11"}))
        self.wait.assert_called_once()
        self.assertIn("#reseau", r.headers["Location"])
        with db.transaction() as conn:
            row = conn.execute("SELECT detail FROM audit WHERE action='systeme.noms-internes' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("idp.lab.local", row["detail"])

    def test_removing(self):
        self.send(op="remove", name="sso.lab.local")
        self.assertEqual(self.request.call_args[0][1]["op"], "remove")

    def test_a_refusal_is_explained(self):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request", side_effect=ValueError("adresse IP attendue")), mock.patch.object(system, "_wait_for_job") as wait:
            r = self.admin.c.post("/system/internal-hosts", data={"name": "x", "address": "y", "csrf": token}, follow_redirects=True)
        self.assertIn("Adresse IP attendue", html.unescape(r.get_data(as_text=True)))
        wait.assert_not_called()

    def test_waiting_ends_when_the_new_operation_is_finished(self):
        states = [[{"id": "a", "state": "success"}], [{"id": "b", "state": "running"}], [{"id": "b", "state": "success"}]]
        with mock.patch.object(locoadmin, "jobs", side_effect=states), mock.patch.object(system.time, "sleep"):
            self.assertEqual(system._wait_for_job("a")["id"], "b")
        with mock.patch.object(locoadmin, "jobs", return_value=[{"id": "a", "state": "success"}]), mock.patch.object(system.time, "sleep"), \
                mock.patch.object(system.time, "monotonic", side_effect=[0, 1, 99]):
            self.assertIsNone(system._wait_for_job("a"))

    def test_a_failed_operation_is_reported(self):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request"), mock.patch.object(system, "_wait_for_job", return_value={"state": "failed"}):
            r = self.admin.c.post("/system/internal-hosts", data={"name": "x", "address": "1.2.3.4", "csrf": token}, follow_redirects=True)
        self.assertIn("Le changement a échoué", html.unescape(r.get_data(as_text=True)))

    def test_it_needs_the_right_to_configure_the_server(self):
        self.assertEqual(ENDPOINTS["system_internal_hosts"], "system.configure")

    def test_docker_and_windows_have_no_route_but_are_told_how(self):
        with mock.patch.object(config, "SERVER_UPDATES", False):
            token = self.admin.token("/account")
            self.assertEqual(self.admin.c.post("/system/internal-hosts", data={"name": "x", "address": "1.2.3.4", "csrf": token}).status_code, 404)
            self.assertIsNone(system.internal_hosts_state())
        with mock.patch.object(config, "CONTAINER", True):
            self.assertIn("extra_hosts", system.internal_hosts_hint())
        with mock.patch.object(config, "CONTAINER", False), mock.patch.object(config, "WINDOWS", True):
            self.assertIn("drivers\\etc\\hosts", system.internal_hosts_hint())


class TestFromTheDiagnostic(OidcTest):
    """Panne 3 : le nom n'est pas résolu ; le diagnostic propose de l'ajouter, sans shell, puis repart."""
    def setUp(self):
        super().setUp()
        self.add()

    def check(self):
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(-2, "Name does not resolve")):
            return html.unescape(self.admin.post(f"/accounts/directories/{DIRECTORY}/check", {}, page="/accounts/directories").get_data(as_text=True))

    def test_a_dns_failure_offers_to_add_the_name_on_the_spot(self):
        page = self.check()
        self.assertIn("Name does not resolve", page)
        self.assertIn('action="/system/internal-hosts"', page)
        self.assertIn('name="name" value="ak.test"', page)
        self.assertIn(f'name="directory" value="{DIRECTORY}"', page)

    def test_without_the_right_there_is_only_the_explanation(self):
        with mock.patch("loco.directories.can", return_value=False):
            page = self.check()
        self.assertIn("Name does not resolve", page)
        self.assertNotIn("internal-hosts", page)

    def test_on_docker_the_correction_is_extra_hosts(self):
        with mock.patch.object(config, "CONTAINER", True), mock.patch.object(config, "SERVER_UPDATES", False):
            page = self.check()
        self.assertIn("extra_hosts", page)
        self.assertNotIn("internal-hosts", page)

    def test_other_failures_do_not_offer_it(self):
        with mock.patch("loco.sso_check._step_dns", return_value={"state": "ok", "detail": "x", "fix": "", "value": ""}), \
                mock.patch("loco.sso_check._step_tcp", return_value={"state": "fail", "detail": "fermé", "fix": "y", "value": ""}):
            page = self.admin.post(f"/accounts/directories/{DIRECTORY}/check", {}, page="/accounts/directories").get_data(as_text=True)
        self.assertNotIn("internal-hosts", page)

    def test_adding_comes_back_to_the_diagnostic_and_runs_it_again(self):
        token = self.admin.token("/account")
        with mock.patch.object(locoadmin, "jobs", return_value=[]), mock.patch.object(locoadmin, "running_job", return_value=None), mock.patch.object(locoadmin, "agent_running", return_value=True), \
                mock.patch.object(locoadmin, "request"), mock.patch("loco.system._wait_for_job", return_value={"state": "success"}):
            r = self.admin.c.post("/system/internal-hosts", data={"name": "ak.test", "address": "192.168.100.10", "directory": DIRECTORY, "csrf": token})
            self.assertIn(f"/accounts/directories?verifier={DIRECTORY}", r.headers["Location"])
            for bad in ("https://evil.example/", "//evil.example", "ffffffffffffffff", "../system"):
                r = self.admin.c.post("/system/internal-hosts", data={"name": "ak.test", "address": "192.168.100.10", "directory": bad, "csrf": token})
                self.assertNotIn("evil", r.headers["Location"])
                self.assertIn("/system", r.headers["Location"])
        self.assertIn(ISSUER, self.admin.c.get("/accounts/directories").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
