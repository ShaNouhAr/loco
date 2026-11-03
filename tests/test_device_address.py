"""Adresse principale d'un poste : le pont Docker ne doit pas passer devant la carte réseau."""
import importlib.util
from pathlib import Path
from unittest import mock
import unittest

from test_fleet import FleetTest, Agent, LINUX, device
from loco import fleet

ROOT = Path(__file__).resolve().parents[1]
ROUTE = "default via 192.168.1.1 dev wlp2s0 proto dhcp metric 600\ndefault via 192.168.1.1 dev enp3s0 metric 700\n"
ADDR = """3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever
4: br-39a1fdaaf1fd    inet 172.19.0.1/16 brd 172.19.255.255 scope global br-39a1fdaaf1fd\\       valid_lft forever
5: wlp2s0    inet6 2a01:e0a::5/64 scope global dynamic\\       valid_lft 86000sec preferred_lft 86000sec
5: wlp2s0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic wlp2s0\\       valid_lft 86000sec
6: tailscale0    inet 100.64.0.7/32 scope global tailscale0\\       valid_lft forever
7: veth12ab@if6    inet 10.88.0.1/16 scope global veth12ab\\       valid_lft forever
"""


class TestMainAddress(unittest.TestCase):
    def test_the_address_loco_sees_comes_first_when_the_machine_declares_it(self):
        self.assertEqual(fleet.main_address({"addresses": "172.17.0.1 192.168.1.50", "ip": "192.168.1.50"}), "192.168.1.50")

    def test_behind_a_router_the_first_declared_address_is_kept(self):
        self.assertEqual(fleet.main_address({"addresses": "192.168.43.10 172.17.0.1", "ip": "192.168.42.43"}), "192.168.43.10")

    def test_without_declared_address_the_one_seen_is_used(self):
        self.assertEqual(fleet.main_address({"addresses": "", "ip": "192.168.1.50"}), "192.168.1.50")
        self.assertEqual(fleet.main_address({"addresses": None, "ip": ""}), "")


class TestPages(FleetTest):
    def test_list_details_and_search_agree(self):
        agent = Agent(dict(LINUX, addresses=["172.17.0.1", "192.168.43.90"]), "192.168.43.90")
        agent.enroll()
        dev = device(agent.uid)
        self.assertEqual(dev["addresses"], "172.17.0.1 192.168.43.90")
        fleet_page = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn('<span class="mono">192.168.43.90</span>', fleet_page)
        self.assertNotIn('<span class="mono">172.17.0.1</span>', fleet_page)
        details = self.admin.c.get(f"/fleet/{dev['id']}").get_data(as_text=True)
        self.assertIn('<span class="mono">192.168.43.90</span>', details)
        self.assertIn("172.17.0.1 192.168.43.90", details)       # toutes les adresses restent sur la fiche
        found = self.admin.c.get("/fleet/search.json").get_json()["devices"]
        self.assertIn("192.168.43.90", found[0]["sub"])
        self.assertNotIn("172.17.0.1", found[0]["sub"])

    def test_address_seen_through_a_router_stays_shown_as_such(self):
        agent = Agent(dict(LINUX, addresses=["192.168.43.91"]), "192.168.42.43")
        agent.enroll()
        page = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn('<span class="mono">192.168.43.91</span>', page)
        self.assertIn("via 192.168.42.43", page)


class TestLinuxAgent(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("loco_client_address", ROOT / "portal/agents/loco-client.py")
        self.agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.agent)

    def addresses(self, route=ROUTE, addr=ADDR):
        def run(args, **_kw):
            return mock.Mock(stdout=route if args[1] == "route" else addr)
        with mock.patch.object(self.agent.subprocess, "run", run):
            return self.agent.addresses()

    def test_default_route_first_then_real_interfaces_then_virtual_ones(self):
        self.assertEqual(self.addresses(), ["192.168.1.50", "2a01:e0a::5", "100.64.0.7", "172.17.0.1", "172.19.0.1", "10.88.0.1"])

    def test_without_default_route_virtual_interfaces_still_come_last(self):
        self.assertEqual(self.addresses(route="")[:2], ["192.168.1.50", "100.64.0.7"])
        self.assertEqual(self.addresses(route="garbled dev")[:1], ["192.168.1.50"])

    def test_missing_command_gives_no_address(self):
        with mock.patch.object(self.agent.subprocess, "run", side_effect=OSError):
            self.assertEqual(self.agent.addresses(), [])

    def test_list_stays_bounded(self):
        many = "".join(f"{i}: eth{i}    inet 10.0.{i}.1/24 scope global\\ \n" for i in range(30))
        self.assertEqual(len(self.addresses(route="", addr=many)), 16)


class TestWindowsAgent(unittest.TestCase):
    def test_default_route_card_comes_first(self):
        source = (ROOT / "windows/client/LocoClient.cs").read_text(encoding="utf-8")
        self.assertIn("props.GatewayAddresses.Any(", source)
        self.assertIn("found.OrderBy(f => f.Key)", source)
