"""Mises à jour bloquées : ni comptées, ni demandées, ni installées, quel que soit le chemin."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_fleet import Agent, LINUX, WINDOWS
import test_portal as fixture
from test_portal import ADMIN_PW, Browser
from loco import auth, config, fleet
from loco.db import transaction

ROOT = Path(__file__).resolve().parent.parent
OPENSSL = {"name": "openssl", "version": "3.0.1", "security": True}
CURL = {"name": "curl", "version": "8.0", "security": False}
KERNEL = {"name": "linux-image-6.1.0-20-amd64", "version": "6.1.76", "security": False}
BAD_KB = {"name": "Cumul de sécurité 2026-10", "version": "KB5034441", "security": True, "id": "aaaaaaaa-1111"}
GOOD_KB = {"name": "Mise à jour .NET", "version": "KB5099999", "security": False, "id": "bbbbbbbb-2222"}
DRIVER = {"name": "Intel - Display - 31.0.101", "version": "", "security": False, "driver": True, "id": "cccccccc-3333"}


def device(uid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone()


class BlocksTest(unittest.TestCase):
    number = 0

    def setUp(self):
        with transaction() as conn:
            for table in ("update_blocks", "device_commands", "device_group_members", "devices", "device_groups"):
                conn.execute(f"DELETE FROM {table}")
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def agent(self, ident, updates=(), version=None):
        BlocksTest.number += 1
        name = f"poste-{BlocksTest.number}"
        agent = Agent(dict(ident, hostname=name, agent_version=version or config.VERSION), f"192.168.43.{BlocksTest.number}")
        agent.enroll()
        agent.checkin({"updates": list(updates), "reboot_required": False, "flatpak": 0})
        return agent

    def block(self, family, pattern, group="", reason="", browser=None):
        return (browser or self.admin).post("/fleet/blocks", {"family": family, "pattern": pattern, "group": group, "reason": reason}, page="/fleet/blocks")

    def rule_id(self, pattern):
        with transaction() as conn:
            return conn.execute("SELECT id FROM update_blocks WHERE pattern=?", (pattern,)).fetchone()["id"]

    def commands(self, agent):
        return agent.checkin().get_json()["commands"]

    def group(self, name, *agents):
        with transaction() as conn:
            group_id = conn.execute("INSERT INTO device_groups (name, description, updated_at) VALUES (?, '', '')", (name,)).lastrowid
            for agent in agents:
                conn.execute("INSERT INTO device_group_members VALUES (?, ?)", (group_id, device(agent.uid)["id"]))
        return group_id


class TestCounts(BlocksTest):
    def test_blocked_update_stops_counting_and_comes_back_when_lifted(self):
        agent = self.agent(LINUX, [OPENSSL, CURL])
        self.assertEqual((device(agent.uid)["updates"], device(agent.uid)["security_updates"]), (2, 1))
        self.assertEqual(self.block("linux", "openssl", reason="casse nginx").status_code, 302)
        dev = device(agent.uid)
        self.assertEqual((dev["updates"], dev["security_updates"], dev["blocked_updates"]), (1, 0, 1))
        self.admin.post(f"/fleet/blocks/{self.rule_id('openssl')}/remove", page="/fleet/blocks")
        dev = device(agent.uid)
        self.assertEqual((dev["updates"], dev["security_updates"], dev["blocked_updates"]), (2, 1, 0))

    def test_next_inventory_keeps_the_blocked_update_out_of_the_counts(self):
        self.block("linux", "openssl")
        agent = self.agent(LINUX, [OPENSSL, CURL])
        self.assertEqual((device(agent.uid)["updates"], device(agent.uid)["blocked_updates"]), (1, 1))

    def test_device_with_only_blocked_updates_is_up_to_date_and_says_so(self):
        agent = self.agent(LINUX, [OPENSSL])
        self.block("linux", "openssl")
        dev = device(agent.uid)
        self.assertFalse(fleet.needs_update(dev))
        with fixture.loco.app.test_request_context():
            color, reason = fleet.status(dev)
        self.assertEqual(color, "green")
        self.assertIn("1 mise à jour bloquée", reason)

    def test_star_is_the_only_wildcard(self):
        agent = self.agent(LINUX, [KERNEL, {"name": "linuxXimage", "version": "1"}, {"name": "libfoo:i386", "version": "1"}])
        self.block("linux", "linux-image-*")
        self.assertEqual(device(agent.uid)["blocked_updates"], 1)      # le point d'un nom n'est pas un joker
        self.block("linux", "libfoo")
        self.assertEqual(device(agent.uid)["blocked_updates"], 2)      # répond aussi avec l'architecture

    def test_windows_is_matched_on_kb_or_title_in_any_case(self):
        agent = self.agent(WINDOWS, [BAD_KB, GOOD_KB, DRIVER])
        self.block("windows", "kb5034441")
        self.assertEqual(device(agent.uid)["blocked_updates"], 1)
        self.block("windows", "intel - display*")
        self.assertEqual(device(agent.uid)["blocked_updates"], 2)
        self.assertEqual(device(agent.uid)["updates"], 1)

    def test_a_block_only_concerns_its_system(self):
        linux = self.agent(LINUX, [dict(OPENSSL, name="KB5034441")])
        self.block("windows", "KB5034441")
        self.assertEqual(device(linux.uid)["blocked_updates"], 0)

    def test_a_group_block_follows_the_members_of_the_group(self):
        inside, outside = self.agent(LINUX, [OPENSSL]), self.agent(LINUX, [OPENSSL])
        group = self.group("production", inside)
        self.block("linux", "openssl", group=str(group))
        self.assertEqual((device(inside.uid)["blocked_updates"], device(outside.uid)["blocked_updates"]), (1, 0))
        self.assertEqual(outside.checkin().get_json()["update_blocks"], {"patterns": []})
        self.assertEqual(inside.checkin().get_json()["update_blocks"], {"patterns": ["openssl"]})
        # Sorti du groupe depuis sa fiche : le poste n'est plus concerné.
        self.admin.post(f"/fleet/{device(inside.uid)['id']}/groups", {"groups": []}, page="/fleet/blocks")
        self.assertEqual(device(inside.uid)["blocked_updates"], 0)
        # Entré dans le groupe depuis son éditeur.
        self.admin.post(f"/fleet/groups/{group}/save", {"name": "production", "description": "", "members": [str(device(outside.uid)["id"])]}, page="/fleet/blocks")
        self.assertEqual(device(outside.uid)["blocked_updates"], 1)

    def test_a_group_with_blocks_cannot_be_deleted(self):
        agent = self.agent(LINUX, [OPENSSL])
        group = self.group("production", agent)
        self.block("linux", "openssl", group=str(group))
        self.admin.post(f"/fleet/groups/{group}/delete", page="/fleet/blocks")
        with transaction() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM device_groups WHERE id=?", (group,)).fetchone())
        self.assertEqual(device(agent.uid)["blocked_updates"], 1)
        self.admin.post(f"/fleet/blocks/{self.rule_id('openssl')}/remove", page="/fleet/blocks")
        self.admin.post(f"/fleet/groups/{group}/delete", page="/fleet/blocks")
        with transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM device_groups WHERE id=?", (group,)).fetchone())


class TestCommands(BlocksTest):
    def queue(self, agent, **form):
        return self.admin.post(f"/fleet/{device(agent.uid)['id']}/action", dict({"action": "update"}, **form), page="/fleet")

    def test_full_update_carries_the_blocks_to_the_agent(self):
        agent = self.agent(LINUX, [OPENSSL, CURL])
        self.block("linux", "openssl")
        self.queue(agent)
        command = self.commands(agent)[0]
        self.assertEqual(command["blocks"], ["openssl"])
        self.assertNotIn("only", command)

    def test_block_set_after_the_request_still_applies(self):
        agent = self.agent(LINUX, [OPENSSL, CURL])
        self.queue(agent, scope="selection", only=["openssl", "curl"])
        self.block("linux", "openssl")
        command = self.commands(agent)[0]
        self.assertEqual(command["only"]["packages"], ["curl"])
        self.assertEqual(command["blocks"], ["openssl"])

    def test_windows_selection_drops_the_blocked_identifier_set_after_the_request(self):
        agent = self.agent(WINDOWS, [BAD_KB, GOOD_KB])
        self.queue(agent, scope="selection", only=[BAD_KB["id"], GOOD_KB["id"]])
        self.block("windows", "KB5034441")
        self.assertEqual(self.commands(agent)[0]["only"]["updates"], [GOOD_KB["id"]])

    def test_selecting_a_blocked_update_is_refused(self):
        agent = self.agent(LINUX, [OPENSSL])
        self.block("linux", "openssl")
        self.assertEqual(self.queue(agent, scope="selection", only=["openssl"]).status_code, 400)
        self.assertEqual(self.commands(agent), [])

    def test_mcp_selection_cannot_reach_a_blocked_update(self):
        agent = self.agent(WINDOWS, [BAD_KB])
        self.block("windows", "KB5034441")
        with fixture.loco.app.test_request_context():
            with self.assertRaises(ValueError):
                fleet.select_updates(device(agent.uid), ["KB5034441"])

    def test_old_agent_waits_instead_of_installing_a_blocked_update(self):
        agent = self.agent(LINUX, [OPENSSL], version="2.136.0")
        self.block("linux", "openssl")
        self.queue(agent)
        self.assertEqual(self.commands(agent), [])                     # il ne saurait pas l'écarter
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM device_commands").fetchone()["state"], "queued")
        agent.ident["agent_version"] = config.VERSION                  # mis à jour seul, il reçoit la commande
        self.assertEqual(self.commands(agent)[0]["blocks"], ["openssl"])

    def test_old_agent_without_any_block_is_not_held_back(self):
        agent = self.agent(LINUX, [OPENSSL], version="2.136.0")
        self.queue(agent)
        self.assertEqual(len(self.commands(agent)), 1)

    def test_old_agent_is_only_held_back_by_blocks_of_its_system(self):
        agent = self.agent(LINUX, [OPENSSL], version="2.136.0")
        self.block("windows", "KB5034441")
        self.queue(agent)
        self.assertEqual(len(self.commands(agent)), 1)

    def test_every_answer_tells_the_agent_so_that_a_lifted_block_is_released(self):
        agent = self.agent(LINUX)
        self.assertEqual(agent.checkin().get_json()["update_blocks"], {"patterns": []})
        self.block("linux", "openssl")
        self.assertEqual(agent.checkin().get_json()["update_blocks"], {"patterns": ["openssl"]})
        self.admin.post(f"/fleet/blocks/{self.rule_id('openssl')}/remove", page="/fleet/blocks")
        self.assertEqual(agent.checkin().get_json()["update_blocks"], {"patterns": []})


class TestPages(BlocksTest):
    def viewer(self, role="viewer"):
        name = f"compte-{role}"
        with transaction() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                auth.create_user(conn, name, "Viewer-Pass-123456", role, must_change=False)
        browser = Browser()
        browser.login(name, "Viewer-Pass-123456")
        return browser

    def test_validation(self):
        for family, pattern in (("linux", "*"), ("linux", "a*"), ("linux", "foo bar"), ("linux", "foo;rm"), ("linux", ""),
                                ("windows", ""), ("windows", "*"), ("windows", "ab"), ("solaris", "openssl")):
            self.block(family, pattern)
            with transaction() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM update_blocks").fetchone(), (family, pattern))

    def test_kb_numbers_are_normalised_and_duplicates_refused(self):
        self.block("windows", "5034441")
        self.block("windows", "kb5034441")
        with transaction() as conn:
            self.assertEqual([r["pattern"] for r in conn.execute("SELECT pattern FROM update_blocks")], ["KB5034441"])

    def test_the_page_shows_who_is_waiting_and_prefills_from_the_device(self):
        agent = self.agent(LINUX, [OPENSSL, CURL])
        page = self.admin.c.get(f"/fleet/{device(agent.uid)['id']}").get_data(as_text=True)
        self.assertIn('/fleet/blocks?family=linux&amp;pattern=openssl#bloquer', page)
        self.block("linux", "openssl", reason="casse nginx")
        page = self.admin.c.get(f"/fleet/{device(agent.uid)['id']}").get_data(as_text=True)
        self.assertIn("casse nginx", page)
        self.assertNotIn('name="only" value="openssl"', page)           # plus cochable
        self.assertIn('name="only" value="curl"', page)
        listing = self.admin.c.get("/fleet/blocks").get_data(as_text=True)
        self.assertIn("1 poste", listing)
        self.assertIn("casse nginx", listing)
        prefill = self.admin.c.get("/fleet/blocks?family=windows&pattern=KB5034441").get_data(as_text=True)
        self.assertIn('value="KB5034441"', prefill)
        self.assertIn('value="windows" checked', prefill)

    def test_a_read_only_account_sees_blocks_but_cannot_change_them(self):
        self.block("linux", "openssl")
        viewer = self.viewer()
        listing = viewer.c.get("/fleet/blocks").get_data(as_text=True)
        self.assertIn("openssl", listing)
        self.assertNotIn('action="/fleet/blocks"', listing)
        self.assertEqual(viewer.post("/fleet/blocks", {"family": "linux", "pattern": "curl"}, page="/fleet/blocks").status_code, 403)
        self.assertEqual(viewer.post(f"/fleet/blocks/{self.rule_id('openssl')}/remove", page="/fleet/blocks").status_code, 403)

    def test_changes_are_journaled(self):
        self.block("linux", "openssl", reason="casse nginx")
        self.admin.post(f"/fleet/blocks/{self.rule_id('openssl')}/remove", page="/fleet/blocks")
        with transaction() as conn:
            rows = [(r["action"], r["detail"]) for r in conn.execute("SELECT action, detail FROM audit WHERE action LIKE 'parc.%blocage' ORDER BY id DESC LIMIT 2")]
        self.assertEqual(rows, [("parc.deblocage", "linux : openssl"), ("parc.blocage", "linux : openssl (casse nginx)")])


class TestLinuxAgent(unittest.TestCase):
    """Agent réel, commandes du système simulées."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_blocks", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)
        self.held, self.installed, self.calls, self.mark_fails = set(), ["openssl", "curl", "libc6:i386"], [], False
        self.agent.run = self.fake_run

    def fake_run(self, command, **kwargs):
        self.calls.append(command)
        if command[:2] == ["apt-mark", "showhold"]:
            return 0, "\n".join(sorted(self.held))
        if command[0] == "dpkg-query":
            return 0, "\n".join(self.installed)
        if command[:2] == ["apt-mark", "hold"]:
            if self.mark_fails:
                return 100, "E: dpkg verrouillé"
            self.held.update(command[2:])
            return 0, ""
        if command[:2] == ["apt-mark", "unhold"]:
            self.held.difference_update(command[2:])
            return 0, ""
        raise AssertionError(command)

    def test_holds_what_is_blocked_and_leaves_local_holds_alone(self):
        self.held = {"zfs", "openssl-local"}
        state = {}
        self.assertEqual(self.agent.sync_holds(["openssl", "lib*"], state), "")
        self.assertEqual(self.held, {"zfs", "openssl-local", "openssl", "libc6:i386"})
        self.assertEqual(state["holds"], ["libc6:i386", "openssl"])
        self.assertEqual(self.agent.sync_holds([], state), "")         # blocages levés : seuls les siens sont relâchés
        self.assertEqual(self.held, {"zfs", "openssl-local"})
        self.assertEqual(state["holds"], [])

    def test_a_manual_hold_matching_a_block_is_not_taken_over(self):
        self.held = {"openssl"}
        state = {}
        self.agent.sync_holds(["openssl"], state)
        self.assertEqual(state["holds"], [])
        self.agent.sync_holds([], state)
        self.assertEqual(self.held, {"openssl"})

    def test_new_package_seen_in_the_repositories_is_held_too(self):
        state = {}
        self.agent.sync_holds(["linux-image-*"], state, ["linux-image-6.1.0-20-amd64", "curl"])
        self.assertEqual(self.held, {"linux-image-6.1.0-20-amd64"})

    def test_nothing_runs_without_any_block(self):
        self.assertEqual(self.agent.sync_holds([], {}), "")
        self.assertEqual(self.calls, [])

    def test_failed_hold_is_reported(self):
        self.mark_fails = True
        state = {}
        self.assertIn("dpkg verrouillé", self.agent.sync_holds(["openssl"], state))
        self.assertNotIn("holds", state)

    def test_changed_list_asks_for_a_new_inventory_but_an_old_server_changes_nothing(self):
        self.agent.family = lambda: "debian"
        state = {"blocks": ["openssl"], "holds": ["openssl"]}
        self.agent.apply_blocks({}, state)
        self.assertNotIn("rescan", state)
        self.assertEqual(state["holds"], ["openssl"])
        self.agent.apply_blocks({"update_blocks": {"patterns": ["openssl"]}}, state)
        self.assertNotIn("rescan", state)
        self.agent.apply_blocks({"update_blocks": {"patterns": ["curl", "bad name;"]}}, state)
        self.assertTrue(state["rescan"])
        self.assertEqual(state["blocks"], ["curl"])

    def test_debian_update_refuses_to_run_if_the_hold_failed(self):
        self.agent.family = lambda: "debian"
        self.agent.apt_update = lambda: (0, "")
        self.agent.apt_simulation = lambda *a: []
        self.agent.shutil = mock.Mock(which=lambda name: None)
        self.mark_fails = True
        with mock.patch.object(self.agent, "run_stream", side_effect=AssertionError("apt-get lancé")):
            code, out = self.agent.update({"blocks": ["openssl"]}, state={})
        self.assertEqual(code, 1)
        self.assertIn("dpkg verrouillé", out)

    def test_debian_selection_never_keeps_a_blocked_package(self):
        self.agent.family = lambda: "debian"
        self.agent.apt_update = lambda: (0, "")
        self.agent.apt_simulation = lambda *a: []
        seen = []
        self.agent.run_stream = lambda command, *a, **k: (seen.append(command), (0, ""))[1]
        original = self.fake_run

        def run(command, **kwargs):
            if command[:2] == ["apt-mark", "showauto"]:
                return 0, ""
            return original(command, **kwargs)
        self.agent.run = run
        code, _ = self.agent.update({"only": {"packages": ["openssl", "curl"]}, "blocks": ["openssl"]}, state={})
        self.assertEqual(code, 0)
        self.assertIn("curl", seen[0])
        self.assertNotIn("openssl", seen[0])

    def test_dnf_excludes_the_blocked_packages(self):
        self.agent.family = lambda: "rhel"
        self.agent.shutil = mock.Mock(which=lambda name: "/usr/bin/" + name if name == "dnf" else None)
        seen = []
        self.agent.run_stream = lambda command, *a, **k: (seen.append(command), (0, ""))[1]
        self.agent.update({"blocks": ["kernel*"]})
        self.assertEqual(seen[0][:3], ["dnf", "-y", "upgrade"])
        self.assertIn("--exclude=kernel*", seen[0])
        self.assertIn("--setopt=best=False", seen[0])                  # sinon dnf refuse tout si une autre mise à jour dépend d'un exclu

    def test_inventory_reports_what_is_held_back_for_the_portal(self):
        self.agent.family = lambda: "debian"
        self.agent.apt_update = lambda: (0, "")
        for name in ("flatpak_updates", "reboot_required", "installed_packages", "installed_flatpaks", "system_info"):
            setattr(self.agent, name, lambda *a: 0)
        outputs = {(): [{"name": "curl", "version": "8", "security": False}],
                   ("--ignore-hold",): [{"name": "curl", "version": "8", "security": False}, {"name": "openssl", "version": "3", "security": True},
                                        {"name": "zfs", "version": "2", "security": False}]}
        self.agent.apt_simulation = lambda *options: outputs[options]
        inv = self.agent.scan({"blocks": ["openssl"], "holds": ["openssl"]})
        self.assertEqual([u["name"] for u in inv["updates"]], ["curl", "openssl"])      # zfs, retenu à la main, reste caché

    def test_removing_the_agent_releases_its_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.agent.STATE = str(Path(tmp, "state", "state.json"))
            Path(self.agent.STATE).parent.mkdir()
            Path(self.agent.STATE).write_text(json.dumps({"holds": ["openssl"]}))
            self.held = {"openssl", "zfs"}
            self.agent.ETC, self.agent.PROGRAM = str(Path(tmp, "etc")), str(Path(tmp, "bin", "loco"))
            self.agent.UNITS = ()
            with mock.patch.object(self.agent.subprocess, "run"):
                self.agent.remove_local()
        self.assertEqual(self.held, {"zfs"})


class TestWindowsAgentSource(unittest.TestCase):
    """Le service redémarre et inventorie avant son premier contact : les motifs doivent survivre, sur disque."""

    def test_blocked_updates_are_skipped_even_when_selected(self):
        source = (ROOT / "windows/client/LocoClient.cs").read_text(encoding="utf-8")
        only = source.index('if (only != null && !only.Contains(Convert.ToString(u.Identity.UpdateID))) continue;')
        self.assertIn("if (IsBlocked(u))", source[only:only + 200])
        self.assertIn('File.Exists(BlocksPath)', source)
        self.assertIn("ReleaseHiddenUpdates()", source[source.index("static void Uninstall()"):])


if __name__ == "__main__":
    unittest.main()
