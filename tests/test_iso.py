"""Validation réseau avant l'effacement du disque et la reconfiguration de l'appliance."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'linux/lib/appliance.sh'


class TestNetworkAnswers(unittest.TestCase):
    def accepts(self, function, *values):
        return subprocess.run(['sh', '-c', '. "$1"; shift; "$@"', 'iso-test', str(LIB), function, *values],
                              capture_output=True).returncode == 0

    def test_static_addresses_are_ipv4_with_a_prefix(self):
        self.assertTrue(self.accepts('appliance_cidr_ok', '192.168.43.10/24'))
        for value in ('192.168.43.10', '2001:db8::10/64', '192.168.999.10/24', '$(id)/24'):
            self.assertFalse(self.accepts('appliance_cidr_ok', value), value)

    def test_gateway_must_match_the_configured_network(self):
        self.assertTrue(self.accepts('appliance_gateway_ok', '192.168.43.10/24', '192.168.43.1'))
        for gateway in ('192.168.42.1', '2001:db8::1', 'not-an-address'):
            self.assertFalse(self.accepts('appliance_gateway_ok', '192.168.43.10/24', gateway), gateway)

    def test_scoped_addresses_cannot_reach_shell_configuration(self):
        self.assertFalse(self.accepts('appliance_networks', 'fe80::%$(id)/64'))
        self.assertFalse(self.accepts('appliance_ip_ok', 'fe80::%$(id)'))
        self.assertTrue(self.accepts('appliance_networks', '10.0.0.0/8 fd00::/8'))


class TestSSHAnswers(unittest.TestCase):
    def resolve(self, mode, key=''):
        return subprocess.run(['sh', '-c', '. "$1"; appliance_ssh_mode "$2" "$3"',
                               'ssh-test', str(LIB), mode, key], capture_output=True, text=True)

    def test_legacy_answers_keep_key_or_disabled_behavior(self):
        self.assertEqual(self.resolve('auto').stdout.strip(), 'off')
        self.assertEqual(self.resolve('', 'ssh-ed25519 AAAA').stdout.strip(), 'key')

    def test_password_mode_needs_no_key(self):
        result = self.resolve('password')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'password')

    def test_unknown_mode_is_rejected(self):
        self.assertNotEqual(self.resolve('invalid').returncode, 0)

    def test_invalid_key_does_not_modify_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run(['sh', '-c', '. "$1"; appliance_write_ssh "$2" key ""',
                                     'ssh-test', str(LIB), root], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(root).iterdir()), [])


class TestBootService(unittest.TestCase):
    def test_runtime_directories_survive_a_reboot(self):
        # /run est en mémoire : ce qu'install.sh y crée disparaît au premier redémarrage.
        pidfile = re.search(r'^pid_filename (\S+)', (ROOT / 'conf/squid-wu.conf').read_text(encoding='utf-8'), re.M)[1]
        initd = (ROOT / 'linux/conf/loco.initd').read_text(encoding='utf-8')
        self.assertRegex(initd, r'checkpath -d .*' + re.escape(str(Path(pidfile).parent)) + r'\n')
