"""Réveil à distance : détection des cartes par les agents, proposé seulement pour un poste en Ethernet sur un réseau joignable,
envoi direct ou par un poste allumé, suivi du résultat, droits et parc du compte."""
import ctypes
import datetime
import errno
import hashlib
import html
import importlib.util
import ipaddress
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, LINUX, device
from loco import config, db as loco_db, mcp, wake
from loco.db import transaction
from loco.permissions import ACTION_PERMISSIONS, DEFAULTS, ENDPOINTS, PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]
MAC, OTHER_MAC = "bc:24:11:1c:a4:02", "bc:24:11:1c:a4:03"
NET, ADDRESS = "192.168.50.0/24", "192.168.50.20/24"


def card(kind="ethernet", up=True, wol="on", networks=(ADDRESS,), mac=MAC, name="eth0", usb=False):
    return {"name": name, "mac": mac, "kind": kind, "up": up, "usb": usb, "wol": wol, "networks": list(networks)}


def stamp(minutes=0):
    return (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


class Agents(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("loco_client_wake", ROOT / "portal/agents/loco-client.py")
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        return agent


class TestDetection(Agents):
    """Vrai format de /sys/class/net, et l'ioctl simulé comme le noyau écrit sa réponse."""
    def setUp(self):
        self.agent = self.load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.net = Path(tmp.name, "net")
        self.net.mkdir()
        self.agent.SYS_NET = str(self.net)
        self.usb = Path(tmp.name, "bus", "usb")
        self.usb.mkdir(parents=True)

    def nic(self, name, device=True, carrier="1", mac="aa:bb:cc:dd:ee:01", kind="1", wireless=False, usb=False, master=None):
        base = self.net / name
        base.mkdir()
        (base / "address").write_text(mac + "\n")
        (base / "type").write_text(kind + "\n")
        if carrier is not None:
            (base / "carrier").write_text(carrier + "\n")
        if device:
            (base / "device").mkdir()
            if usb:
                (base / "device" / "subsystem").symlink_to(self.usb)
        if wireless:
            (base / "wireless").mkdir()
        if master:
            (base / "master").symlink_to(self.net / master)

    def cards(self, networks=None):
        with mock.patch.object(self.agent, "wol_state", return_value="on"):
            return {c["name"]: c for c in self.agent.network_cards(networks or {})}

    def test_ethernet_wifi_and_usb_are_told_apart(self):
        self.nic("eth0")
        self.nic("wlan0", wireless=True, mac="aa:bb:cc:dd:ee:02")
        self.nic("enx0c3796c2aff4", usb=True, mac="0c:37:96:c2:af:f4")
        found = self.cards({"eth0": [ADDRESS]})
        self.assertEqual((found["eth0"]["kind"], found["eth0"]["usb"], found["eth0"]["networks"]), ("ethernet", False, [ADDRESS]))
        self.assertEqual((found["wlan0"]["kind"], found["wlan0"]["wol"]), ("wifi", ""))
        self.assertEqual((found["enx0c3796c2aff4"]["kind"], found["enx0c3796c2aff4"]["usb"]), ("ethernet", True))

    def test_virtual_interfaces_are_not_cards(self):
        self.nic("docker0")
        self.nic("veth12ab")
        self.nic("br-abc")
        self.nic("eth0", device=False)                       # conteneur : une veth nommée eth0, sans matériel
        self.nic("wwan0", kind="519")
        self.assertEqual(self.cards(), {})

    def test_an_unplugged_card_is_listed_as_such_with_its_mac(self):
        self.nic("eth0", carrier="0")
        self.nic("eth1", carrier=None, mac="aa:bb:cc:dd:ee:03")       # interface arrêtée : carrier illisible
        found = self.cards()
        self.assertFalse(found["eth0"]["up"])
        self.assertFalse(found["eth1"]["up"])
        self.assertEqual(found["eth1"]["mac"], "aa:bb:cc:dd:ee:03")

    def test_a_card_in_a_bridge_takes_the_network_of_the_bridge(self):
        self.nic("br0", device=False)
        self.nic("enp3s0", master="br0")
        self.assertEqual(self.cards({"br0": [ADDRESS]})["enp3s0"]["networks"], [ADDRESS])

    def kernel(self, supported=0, enabled=0, error=None):
        def ioctl(_fd, request, data):
            self.assertEqual(request, 0x8946)
            if error:
                raise OSError(error, "x")
            name, pointer = struct.unpack_from("16sP", data)
            self.assertEqual(name.rstrip(b"\0"), b"eth0")
            self.assertEqual(struct.unpack("I", ctypes.string_at(pointer, 4))[0], 5)     # ETHTOOL_GWOL
            ctypes.memmove(pointer, struct.pack("III", 5, supported, enabled), 12)
        return mock.patch.object(self.agent.fcntl, "ioctl", ioctl)

    def test_wake_state_as_the_kernel_reports_it(self):
        magic = 0x20
        for supported, enabled, expected in ((magic | 1, magic, "on"), (magic, 0, "off"), (1, 1, "none"), (0, 0, "none")):
            with self.kernel(supported, enabled):
                self.assertEqual(self.agent.wol_state("eth0"), expected)
        with self.kernel(error=errno.EOPNOTSUPP):
            self.assertEqual(self.agent.wol_state("eth0"), "none")
        with self.kernel(error=errno.EPERM):
            self.assertEqual(self.agent.wol_state("eth0"), "")

    def test_identity_announces_cards_and_real_networks_only(self):
        self.nic("eth0")
        nets = {"eth0": [ADDRESS], "docker0": ["172.17.0.1/16"], "tun0": ["10.8.0.2/24"], "wg0": ["10.9.0.1/24"]}
        with mock.patch.object(self.agent, "local_networks", return_value=nets), mock.patch.object(self.agent, "wol_state", return_value="off"):
            found = self.agent.identity()
        self.assertEqual(found["networks"], [ADDRESS])
        self.assertEqual(found["cards"][0]["wol"], "off")

    def test_local_networks_reads_ip(self):
        out = ("2: eth0    inet 192.168.50.20/24 brd 192.168.50.255 scope global eth0\\       valid_lft forever\n"
               "3: vlan7@eth0    inet 10.7.0.2/16 scope global vlan7\\       valid_lft forever\n")
        with mock.patch.object(self.agent.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(self.agent.local_networks(), {"eth0": [ADDRESS], "vlan7": ["10.7.0.2/16"]})


class TestRelayOnAgent(Agents):
    def setUp(self):
        self.agent = self.load()
        self.sent = []
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.sendto.side_effect = lambda packet, target: self.sent.append((packet, target))
        for patcher in (mock.patch.object(self.agent.socket, "socket", return_value=sock), mock.patch.object(self.agent, "local_networks", return_value={"eth0": [ADDRESS]})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sock = sock

    def test_the_magic_packet_is_exact_and_sent_on_its_network(self):
        code, _out = self.agent.relay_wake({"broadcast": "192.168.50.255", "macs": [MAC]})
        self.assertEqual(code, 0)
        self.sock.bind.assert_called_once_with(("192.168.50.20", 0))
        self.assertEqual({t for _p, t in self.sent}, {("192.168.50.255", 9), ("255.255.255.255", 9)})
        self.assertEqual({p for p, _t in self.sent}, {b"\xff" * 6 + bytes.fromhex(MAC.replace(":", "")) * 16})

    def test_it_refuses_a_network_that_is_not_its_own(self):
        code, _out = self.agent.relay_wake({"broadcast": "10.0.0.255", "macs": [MAC]})
        self.assertEqual(code, 1)
        self.assertEqual(self.sent, [])

    def test_bad_addresses_are_dropped(self):
        for order in ({"broadcast": "192.168.50.255", "macs": ["pas-une-mac", 12, "AA:BB:CC:DD:EE:FF"]}, {"broadcast": "192.168.50.255"}, {}, None, "x"):
            self.assertEqual(self.agent.relay_wake(order)[0], 1)
        self.assertEqual(self.sent, [])


class TestCleanInfo(unittest.TestCase):
    def clean(self, **data):
        return json.loads(wake.clean_info(data))

    def test_an_older_agent_announces_nothing(self):
        self.assertIsNone(wake.clean_info({"hostname": "x"}))
        self.assertIsNone(wake.clean_info({"cards": "pas une liste"}))

    def test_valid_cards_are_kept(self):
        found = self.clean(cards=[card()], networks=[ADDRESS])
        self.assertEqual(found["cards"][0]["mac"], MAC)
        self.assertEqual(found["networks"], [ADDRESS])

    def test_a_bad_mac_never_reaches_a_packet(self):
        for mac in ("", "zz:24:11:1c:a4:02", "bc:24:11:1c:a4", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", 12, "bc:24:11:1c:a4:02\n"):
            self.assertEqual(self.clean(cards=[card(mac=mac)])["cards"], [], repr(mac))
        self.assertEqual(self.clean(cards=[card(mac="BC-24-11-1C-A4-02")])["cards"][0]["mac"], MAC)

    def test_only_ethernet_and_wifi(self):
        self.assertEqual(self.clean(cards=[card(kind="bluetooth"), "x", 3])["cards"], [])

    def test_only_real_ipv4_networks(self):
        bad = ["fd00::1/64", "127.0.0.1/8", "169.254.1.2/16", "224.0.0.1/24", "0.0.0.0/8", "10.0.0.1/32", "10.0.0.1/31", "1.2.3.4/4", "pas-une-ip", 5]
        self.assertEqual(self.clean(cards=[], networks=bad + [ADDRESS])["networks"], [ADDRESS])

    def test_a_bad_wake_state_is_unknown(self):
        self.assertEqual(self.clean(cards=[card(wol="peut-être")])["cards"][0]["wol"], "")

    def test_sizes_are_bounded(self):
        found = self.clean(cards=[card(name="x" * 500)] * 100, networks=[f"10.0.{i}.1/24" for i in range(100)])
        self.assertEqual((len(found["cards"]), len(found["cards"][0]["name"]), len(found["networks"])), (wake.MAX_CARDS, 60, wake.MAX_NETWORKS))


class TestServerNetworks(unittest.TestCase):
    def setUp(self):
        wake._server.update(until=0.0, networks={})
        self.addCleanup(wake._server.update, until=0.0, networks={})

    def with_addresses(self, found):
        with mock.patch.object(wake, "_linux_addresses", return_value=found) as read:
            return wake.server_networks(), read

    def test_real_networks_only(self):
        found, _read = self.with_addresses([("lo", "127.0.0.1/8"), ("eth0", "192.168.50.1/24"), ("docker0", "172.17.0.1/16"), ("tun0", "10.8.0.1/24"), ("wg0", "10.9.0.1/24"), ("vmbr0", "192.168.60.1/24")])
        self.assertEqual(found, {NET: "192.168.50.1", "192.168.60.0/24": "192.168.60.1"})

    def test_read_at_most_once_a_minute(self):
        self.with_addresses([("eth0", "192.168.50.1/24")])
        _found, read = self.with_addresses([])
        read.assert_not_called()

    def test_a_read_failure_leaves_only_relays(self):
        with mock.patch.object(wake, "_linux_addresses", side_effect=OSError):
            self.assertEqual(wake.server_networks(), {})

    @unittest.skipUnless(hasattr(socket, "AF_PACKET"), "Linux")
    def test_the_real_read_works_and_needs_no_netlink(self):
        # Trouvé en réel : RestrictAddressFamilies du service interdit le netlink, donc « ip ». Ici, seulement une socket AF_INET.
        real = socket.socket
        def only_inet(family=socket.AF_INET, *args, **kwargs):
            self.assertEqual(family, socket.AF_INET)
            return real(family, *args, **kwargs)
        with mock.patch.object(wake.socket, "socket", only_inet), mock.patch.object(wake.subprocess, "run", side_effect=AssertionError("pas de commande")):
            found = wake._linux_addresses()
        self.assertIn(("lo", "127.0.0.1/8"), found)
        for name, address in found:
            self.assertTrue(name)
            self.assertEqual(ipaddress.ip_interface(address).version, 4)

    def test_windows_server(self):
        out = mock.Mock(stdout="Ethernet|192.168.50.1/24\r\nvEthernet (WSL)|172.20.0.1/20\r\nLoopback Pseudo-Interface 1|127.0.0.1/8\r\n")
        with mock.patch.object(config, "WINDOWS", True), mock.patch.object(wake.subprocess, "run", return_value=out) as run:
            self.assertEqual(wake.server_networks(), {NET: "192.168.50.1"})
        self.assertEqual(run.call_args[0][0][0], "powershell")


class TestPacket(unittest.TestCase):
    def test_magic_packet_is_six_ff_then_sixteen_times_the_mac(self):
        packet = wake.magic_packet(MAC)
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:], bytes.fromhex("bc24111ca402") * 16)

    def test_it_goes_out_of_the_servers_address_on_this_network(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        with mock.patch.object(wake.socket, "socket", return_value=sock):
            wake.send_packet(MAC, NET, "192.168.50.1")
        sock.bind.assert_called_once_with(("192.168.50.1", 0))
        self.assertEqual({c[0][1] for c in sock.sendto.call_args_list}, {("192.168.50.255", 9), ("255.255.255.255", 9)})


class WakeTest(FleetTest):
    def setUp(self):
        super().setUp()
        self.packets = []
        for patcher in (mock.patch.object(wake, "server_networks", lambda: dict(self.server)),
                        mock.patch.object(wake, "send_packet", lambda mac, network, source: self.packets.append((mac, network, source)))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = {NET: "192.168.50.1"}

    def agent(self, name, cards=(), networks=(ADDRESS,), version="2.134.0", ip="192.168.50.20", announce=True):
        ident = dict(LINUX, hostname=name, agent_version=version)
        if announce:
            ident.update(cards=list(cards), networks=list(networks))
        agent = Agent(ident, ip, uid=hashlib.sha256(name.encode()).hexdigest()[:32])
        self.assertEqual(agent.enroll().status_code, 200)
        return agent

    def off(self, agent, minutes=60):
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen=? WHERE uid=?", (stamp(minutes), agent.uid))

    def row(self, agent):
        return device(agent.uid)

    def fleet_page(self):
        return html.unescape(self.admin.c.get("/fleet").get_data(as_text=True))

    def device_page(self, agent):
        return html.unescape(self.admin.c.get(f"/fleet/{self.row(agent)['id']}").get_data(as_text=True))

    def offered(self, agent):
        page = self.fleet_page()
        action = f'/fleet/{self.row(agent)["id"]}/action'
        return any(action in part and 'value="wake"' in part for part in page.split("<form "))

    def press(self, agent):
        return self.admin.post(f"/fleet/{self.row(agent)['id']}/action", {"action": "wake"}, page="/account")

    def commands(self, agent, action):
        with transaction() as conn:
            return conn.execute("SELECT * FROM device_commands WHERE device_id=? AND action=? ORDER BY id", (self.row(agent)["id"], action)).fetchall()


class TestWhenItIsOffered(WakeTest):
    """La règle demandée : Ethernet seulement, et branchée sur un réseau où quelque chose peut émettre."""
    def test_a_switched_off_wired_machine_on_the_servers_network(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        self.assertTrue(self.offered(agent))
        self.assertIn("Réveiller", self.device_page(agent))

    def test_a_machine_that_is_on_has_nothing_to_wake(self):
        agent = self.agent("poste1", [card()])
        self.assertFalse(self.offered(agent))
        self.assertIn("Réveil à distance possible", self.device_page(agent))      # la fiche l'annonce d'avance

    def test_wifi_is_never_offered(self):
        agent = self.agent("portable", [card(kind="wifi", wol="")])
        self.off(agent)
        self.assertFalse(self.offered(agent))
        self.assertIn("Aucune carte Ethernet", self.device_page(agent))

    def test_on_wifi_with_the_ethernet_card_unplugged(self):
        agent = self.agent("portable", [card(kind="wifi", wol="", name="wlan0"), card(up=False, networks=(), mac=OTHER_MAC)])
        self.off(agent)
        self.assertFalse(self.offered(agent))
        self.assertIn("Carte Ethernet eth0 débranchée", self.device_page(agent))

    def test_plugged_in_but_with_no_address(self):
        agent = self.agent("poste1", [card(networks=())])
        self.off(agent)
        self.assertFalse(self.offered(agent))

    def test_ethernet_on_a_network_nothing_can_reach(self):
        self.server = {"10.0.0.0/24": "10.0.0.1"}
        agent = self.agent("poste1", [card()])
        self.off(agent)
        self.assertFalse(self.offered(agent))
        self.assertIn(f"sur le réseau {NET}", self.device_page(agent))

    def test_wifi_on_the_servers_network_but_ethernet_elsewhere(self):
        agent = self.agent("poste1", [card(kind="wifi", wol="", name="wlan0"), card(networks=("10.9.9.9/24",), mac=OTHER_MAC)], networks=(ADDRESS, "10.9.9.9/24"))
        self.off(agent)
        self.assertFalse(self.offered(agent))

    def test_a_second_ethernet_card_on_the_right_network_is_used(self):
        agent = self.agent("poste1", [card(networks=("10.9.9.9/24",)), card(mac=OTHER_MAC, name="eth1")])
        self.off(agent)
        self.assertTrue(self.offered(agent))
        self.press(agent)
        self.assertEqual(self.packets, [(OTHER_MAC, NET, "192.168.50.1")])

    def test_wake_disabled_or_not_supported_is_explained_not_offered(self):
        for state, text in (("off", "Réveil désactivé sur la carte eth0"), ("none", "La carte eth0 ne gère pas le réveil")):
            agent = self.agent(f"poste{state}", [card(wol=state)])
            self.off(agent)
            self.assertFalse(self.offered(agent), state)
            self.assertIn(text, self.device_page(agent))

    def test_unknown_wake_state_is_still_offered(self):
        agent = self.agent("poste1", [card(wol="")])
        self.off(agent)
        self.assertTrue(self.offered(agent))

    def test_an_usb_adapter_is_offered_with_a_warning(self):
        agent = self.agent("poste1", [card(usb=True, name="enx0c37")])
        self.off(agent)
        self.assertTrue(self.offered(agent))
        self.assertIn("Adaptateur USB", self.device_page(agent))

    def test_an_older_agent_is_not_offered(self):
        agent = self.agent("ancien", announce=False, version="2.133.0")
        self.off(agent)
        self.assertFalse(self.offered(agent))
        self.assertNotIn("Cartes réseau", self.device_page(agent))

    def test_a_later_checkin_of_an_older_agent_keeps_what_is_known(self):
        agent = self.agent("poste1", [card()])
        agent.ident.pop("cards")
        agent.ident.pop("networks")
        self.assertEqual(agent.checkin().status_code, 200)
        self.assertEqual(json.loads(self.row(agent)["net_info"])["cards"][0]["mac"], MAC)

    def test_the_card_panel_shows_what_was_detected(self):
        agent = self.agent("poste1", [card(), card(kind="wifi", wol="", name="wlan0", up=False, networks=(), mac=OTHER_MAC)])
        page = self.device_page(agent)
        for text in ("Cartes réseau", MAC, OTHER_MAC, "Ethernet", "Wi-Fi", "branchée", "débranchée", "activé", "pas en Wi-Fi", ADDRESS):
            self.assertIn(text, page)

    def test_cards_arrive_at_enrollment_and_follow_each_contact(self):
        agent = self.agent("poste1", [card(wol="off")])
        self.assertEqual(json.loads(self.row(agent)["net_info"])["cards"][0]["wol"], "off")
        agent.ident["cards"] = [card(wol="on")]
        agent.checkin()
        self.assertEqual(json.loads(self.row(agent)["net_info"])["cards"][0]["wol"], "on")


class TestSendingDirectly(WakeTest):
    def test_the_server_sends_at_once_and_remembers_it(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        r = self.press(agent)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.packets, [(MAC, NET, "192.168.50.1")])
        rows = self.commands(agent, "wake")
        self.assertEqual((len(rows), rows[0]["state"], rows[0]["created_by"]), (1, "running", "admin"))
        with transaction() as conn:
            self.assertIn("poste1", conn.execute("SELECT detail FROM audit WHERE action='parc.wake' ORDER BY id DESC LIMIT 1").fetchone()["detail"])

    def test_pressing_twice_keeps_one_line_and_sends_again(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        self.press(agent)
        self.press(agent)
        self.assertEqual(len(self.packets), 2)
        self.assertEqual(len(self.commands(agent, "wake")), 1)

    def test_it_is_never_given_to_the_agent_of_the_sleeping_machine(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        self.press(agent)
        answer = agent.checkin().get_json()
        self.assertEqual([c for c in answer["commands"] if c["action"].startswith("wake")], [])

    def test_a_machine_already_online_is_not_sent_anything(self):
        agent = self.agent("poste1", [card()])
        r = self.admin.post(f"/fleet/{self.row(agent)['id']}/action", {"action": "wake"}, page="/account")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.packets, [])
        self.assertEqual(self.commands(agent, "wake"), [])

    def test_the_relay_action_cannot_be_asked_for_directly(self):
        agent = self.agent("poste1", [card()])
        self.assertEqual(self.admin.post(f"/fleet/{self.row(agent)['id']}/action", {"action": "wake-relay"}, page="/account").status_code, 400)


class TestSendingThroughARelay(WakeTest):
    def setUp(self):
        super().setUp()
        self.server = {}
        self.target = self.agent("eteint", [card()])
        self.off(self.target)

    def test_a_powered_on_machine_of_the_network_relays(self):
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.assertTrue(self.offered(self.target))
        self.press(self.target)
        self.assertEqual(self.packets, [])
        order = [c for c in relay.checkin().get_json()["commands"] if c["action"] == "wake-relay"]
        self.assertEqual(order[0]["wake"], {"broadcast": "192.168.50.255", "macs": [MAC]})
        self.assertEqual(self.commands(self.target, "wake")[0]["state"], "running")

    def test_a_wifi_machine_can_relay_since_only_the_target_must_be_wired(self):
        self.agent("voisin", [card(kind="wifi", wol="", mac=OTHER_MAC, networks=("192.168.50.21/24",))], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.assertTrue(self.offered(self.target))

    def test_a_machine_on_another_network_cannot_relay(self):
        self.agent("ailleurs", [], networks=("10.0.0.5/24",), ip="10.0.0.5")
        self.assertFalse(self.offered(self.target))

    def test_a_switched_off_machine_cannot_relay(self):
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.off(relay)
        self.assertFalse(self.offered(self.target))

    def test_an_agent_too_old_to_relay_is_not_used(self):
        self.agent("ancien", [], networks=("192.168.50.21/24",), ip="192.168.50.21", version="2.133.0")
        self.assertFalse(self.offered(self.target))

    def test_two_relays_at_most(self):
        relays = [self.agent(f"voisin{i}", [], networks=(f"192.168.50.{30 + i}/24",), ip=f"192.168.50.{30 + i}") for i in range(4)]
        self.press(self.target)
        self.assertEqual(sum(len(self.commands(r, "wake-relay")) for r in relays), wake.RELAYS)

    def test_a_relay_back_too_late_wakes_nobody_by_surprise(self):
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.press(self.target)
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET created_at=? WHERE action='wake-relay'", (stamp(wake.RELAY_MINUTES + 1),))
        self.assertEqual(relay.checkin().get_json()["commands"], [])
        self.assertEqual(self.commands(relay, "wake-relay")[0]["state"], "cancelled")

    def test_the_server_falling_back_on_a_relay_when_it_cannot_send(self):
        self.server = {NET: "192.168.50.1"}
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        with mock.patch.object(wake, "send_packet", side_effect=OSError("réseau coupé")):
            self.press(self.target)
        self.assertEqual(len(self.commands(relay, "wake-relay")), 1)

    def test_rights_lost_before_delivery_cancel_the_relay(self):
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.press(self.target)
        with mock.patch("loco.permissions.role_permissions", return_value=frozenset()):
            self.assertEqual(relay.checkin().get_json()["commands"], [])
        self.assertEqual(self.commands(relay, "wake-relay")[0]["state"], "cancelled")


class TestResult(WakeTest):
    def setUp(self):
        super().setUp()
        self.target = self.agent("eteint", [card()])
        self.off(self.target)
        self.press(self.target)

    def test_its_return_closes_the_line(self):
        self.target.checkin()
        row = self.commands(self.target, "wake")[0]
        self.assertEqual((row["state"], row["output"]), ("success", "Poste revenu en ligne."))
        self.assertIn("Réveil", self.device_page(self.target))

    def test_no_return_after_ten_minutes_says_what_to_check(self):
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET sent_at=? WHERE action='wake'", (stamp(wake.BACK_MINUTES + 1),))
            wake.expire(conn)
        row = self.commands(self.target, "wake")[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("BIOS", row["output"])

    def test_within_ten_minutes_it_keeps_waiting(self):
        with transaction() as conn:
            wake.expire(conn)
        self.assertEqual(self.commands(self.target, "wake")[0]["state"], "running")

    def test_the_general_six_hour_expiry_does_not_touch_it(self):
        with transaction() as conn:
            conn.execute("UPDATE device_commands SET sent_at=? WHERE action='wake'", (stamp(wake.BACK_MINUTES - 1),))
            from loco import fleet
            fleet.expire_commands(conn)
        self.assertEqual(self.commands(self.target, "wake")[0]["state"], "running")

    def test_the_list_shows_it_is_on_its_way(self):
        self.assertIn("réveil en attente", self.fleet_page())


class TestSelection(WakeTest):
    def test_only_what_can_be_woken_is_and_the_rest_is_counted(self):
        good = self.agent("bon", [card()])
        wifi = self.agent("wifi", [card(kind="wifi", wol="", mac=OTHER_MAC)])
        online = self.agent("allume", [card(mac="bc:24:11:1c:a4:04")])
        self.off(good)
        self.off(wifi)
        token = self.admin.token("/account")
        r = self.admin.c.post("/fleet/wake-selected", data={"csrf": token, "ids": [self.row(a)["id"] for a in (good, wifi, online)]})
        self.assertEqual(r.get_json(), {"ok": True, "count": 1, "skipped": 2})
        self.assertEqual(self.packets, [(MAC, NET, "192.168.50.1")])

    def test_bad_requests(self):
        token = self.admin.token("/account")
        self.assertEqual(self.admin.c.post("/fleet/wake-selected", data={"csrf": token}).status_code, 400)
        self.assertEqual(self.admin.c.post("/fleet/wake-selected", data={"csrf": token, "ids": ["x"]}).status_code, 400)

    def test_many_targets_on_a_relay_are_split(self):
        self.server = {}
        with mock.patch.object(wake, "MAX_TARGETS", 2):
            relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
            targets = [self.agent(f"eteint{i}", [card(mac=f"bc:24:11:1c:a5:0{i}", networks=(f"192.168.50.{40 + i}/24",))], networks=(), ip=f"192.168.50.{40 + i}") for i in range(3)]
            for agent in targets:
                self.off(agent)
            token = self.admin.token("/account")
            self.admin.c.post("/fleet/wake-selected", data={"csrf": token, "ids": [self.row(a)["id"] for a in targets]})
        self.assertEqual([len(json.loads(c["params"])["macs"]) for c in self.commands(relay, "wake-relay")], [2, 1])


class TestRights(WakeTest):
    def login_with(self, role):
        name = f"compte-{role}"
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username=?", (name,))
            fixture.auth.create_user(conn, name, fixture.ADMIN_PW, role, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login(name, fixture.ADMIN_PW).status_code, 302)
        return browser

    def test_the_right_exists_and_is_given_to_operators_not_viewers(self):
        self.assertEqual(ENDPOINTS["fleet_wake_selected"], "fleet.wake")
        self.assertEqual((ACTION_PERMISSIONS["wake"], ACTION_PERMISSIONS["wake-relay"]), ("fleet.wake", "fleet.wake"))
        self.assertEqual(PERMISSIONS["fleet.wake"]["requires"], ("fleet.view",))
        self.assertIn("fleet.wake", DEFAULTS["operator"][2])
        self.assertNotIn("fleet.wake", DEFAULTS["viewer"][2])

    def test_a_viewer_neither_sees_nor_can_press_it(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        viewer = self.login_with("viewer")
        self.assertNotIn('value="wake"', viewer.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(viewer.post(f"/fleet/{self.row(agent)['id']}/action", {"action": "wake"}, page="/account").status_code, 403)
        self.assertEqual(self.packets, [])

    def test_a_limited_profile_cannot_use_a_relay_outside_its_fleet(self):
        self.server = {}
        target = self.agent("eteint", [card()])
        relay = self.agent("voisin", [], networks=("192.168.50.21/24",), ip="192.168.50.21")
        self.off(target)
        with fixture.loco.app.test_request_context("/"):
            with mock.patch("loco.scope.sql", return_value=f" AND id={self.row(target)['id']}"):
                self.assertEqual(wake.context(), {})
        self.assertTrue(self.row(relay))

    def test_a_device_outside_the_fleet_of_the_account_stays_out_of_reach(self):
        agent = self.agent("poste1", [card()])
        self.off(agent)
        token = self.admin.token("/account")
        with mock.patch("loco.scope.sql", return_value=" AND id=-1"):
            r = self.admin.c.post("/fleet/wake-selected", data={"csrf": token, "ids": [self.row(agent)["id"]]})
        self.assertEqual(r.get_json()["count"], 0)
        self.assertEqual(self.packets, [])


class TestAssistants(WakeTest):
    def test_the_tool_exists_with_its_right(self):
        tool = mcp.TOOLS["wake_device"]
        self.assertEqual(tuple(tool["needs"]), ("fleet.wake",))
        self.assertFalse(tool["destructive"])


class TestMigration(unittest.TestCase):
    def test_the_column_exists_with_an_empty_default(self):
        with transaction() as conn:
            self.assertIn("net_info", [r[1] for r in conn.execute("PRAGMA table_info(devices)")])
        self.assertTrue(loco_db.MIGRATIONS)

    def test_the_windows_agent_uses_the_values_read_on_a_real_windows(self):
        # Relevé : Unsupported=0, Disabled=1, Enabled=2, Inactive=3 (classe MSFT_NetAdapterPowerManagementSettingData).
        source = (ROOT / "windows/client/LocoClient.cs").read_text(encoding="utf-8")
        self.assertIn('state == 2 ? "on" : state == 0 ? "none" : "off"', source)
        self.assertIn('else if (action == "wake-relay")', source)


if __name__ == "__main__":
    unittest.main()
