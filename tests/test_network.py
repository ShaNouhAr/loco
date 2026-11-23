"""Équipements réseau : lecture des sorties de chaque famille, conversation avec la ligne de commande (invite,
pagination, enable, identifiants demandés par l'équipement), API de Stormshield, puis le portail — ajout et empreintes
confirmées, relevé et Mon parc, sauvegardes gardées quand la configuration change et leurs différences, firmware
installé famille par famille (redémarrage et retour suivis), terminal, droits.

Les équipements sont simulés : netsim_cli joue la ligne de commande d'un switch (à la place de ssh, dans un tube),
netsim_sns l'API HTTPS d'un pare-feu Stormshield. Le reste est le vrai code de loco."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_portal as fixture
import netsim_data as data
import netsim_sns
from loco import adb, auth, features, homeassistant, netcli, netfamilies, network, permissions, sns
from loco.db import transaction

PASSWORD = "Reseau-Equipements-2026"
SIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "netsim_cli.py")
HOST_KEY = ("ssh-ed25519", base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(1, 33))).decode())


def simulate(family, env=None):
    """Remplace ssh par le simulateur de ligne de commande (dans un tube, comme ssh -tt sans terminal)."""
    env = dict(env or {})
    base = netcli.environment
    return [mock.patch.object(netcli, "QUIET", 0.06),
            mock.patch.object(netcli, "base_argv", lambda host, port, user, known, program="ssh": [sys.executable, SIM, family]),
            mock.patch.object(netcli, "environment", lambda secret, term="vt100": dict(base(secret, term), **env)),
            mock.patch.object(homeassistant, "ssh_binary", lambda: "/usr/bin/ssh")]


class TestParsing(unittest.TestCase):
    def test_a_cisco_ios_switch(self):
        info = netfamilies.parse("cisco-ios", data.FAMILY_OUTPUTS["cisco-ios"])
        self.assertEqual((info["os"], info["version"], info["model"], info["serial"], info["hostname"]),
                         ("IOS", "15.2(7)E9", "WS-C2960X-48FPD-L", "FOC2133X0AB", "SW-CORE"))
        self.assertEqual(info["uptime"], 12 * 7 * 86400 + 3 * 86400 + 4 * 3600 + 22 * 60)
        self.assertEqual((info["ports"], info["ports_up"]), (7, 3))                      # le port-channel n'est pas un port
        self.assertEqual((info["cpu"], info["mem"], info["temperature"]), (6, 40.0, 38))
        self.assertEqual((info["flash_free"], info["install_mode"]), (95002624, False))

    def test_a_catalyst_9000_in_install_mode_and_a_router(self):
        xe = netfamilies.parse("cisco-ios", {"show version": data.XE_VERSION})
        self.assertEqual((xe["os"], xe["version"], xe["model"], xe["serial"], xe["install_mode"]),
                         ("IOS XE", "17.09.04a", "C9300-48P", "FCW2233L0ZZ", True))
        self.assertEqual(xe["uptime"], 365 * 86400 + 2 * 7 * 86400 + 5 * 86400 + 3 * 3600 + 60)
        router = netfamilies.parse("cisco-ios", {"show version": data.CSR_VERSION, "show ip interface brief": data.CSR_BRIEF})
        self.assertEqual((router["model"], router["ports"], router["ports_up"]), ("CSR1000V", 4, 2))  # sans la boucle locale

    def test_a_cisco_small_business(self):
        info = netfamilies.parse("cisco-sb", data.FAMILY_OUTPUTS["cisco-sb"])
        self.assertEqual((info["version"], info["inactive"], info["model"], info["serial"], info["hostname"]),
                         ("3.5.0.22", "3.4.0.17", "CBS350-24P-4G", "PSZ24051ABC", "SW-ETAGE1"))
        self.assertEqual(info["uptime"], 12 * 86400 + 3 * 3600 + 27 * 60 + 44)
        self.assertEqual((info["ports"], info["ports_up"], info["cpu"]), (5, 3, 3))

    def test_extreme_exos_and_voss(self):
        exos = netfamilies.parse("exos", data.FAMILY_OUTPUTS["exos"])
        self.assertEqual((exos["version"], exos["model"], exos["serial"], exos["hostname"], exos["primary"], exos["secondary"]),
                         ("33.6.1.14", "X440G2-48p-10G4", "1849G-00123", "X440G2-BAT-A", "33.6.1.14", "32.7.2.19"))
        self.assertEqual((exos["ports"], exos["ports_up"], exos["cpu"], exos["mem"], exos["temperature"]), (5, 2, 4, 59.6, 41))
        self.assertEqual(exos["uptime"], 52 * 86400 + 2 * 3600 + 34 * 60 + 7)
        voss = netfamilies.parse("voss", data.FAMILY_OUTPUTS["voss"])
        self.assertEqual((voss["version"], voss["model"], voss["serial"], voss["hostname"], voss["primary"]),
                         ("8.10.1.0", "8284XSQ", "14JP12345678", "VSP-COEUR", "VOSS8.10.1.0.GA"))
        self.assertEqual((voss["ports"], voss["ports_up"], voss["cpu"], voss["mem"], voss["temperature"]), (4, 2, 9, 28, 43))

    def test_a_config_whose_timestamps_moved_is_unchanged(self):
        later = (data.IOS_RUNNING.replace("09:40:03 CET Mon Nov 23", "11:02:51 CET Tue Nov 24")
                 .replace("ntp clock-period 36028797", "ntp clock-period 36028801").replace("1832 bytes", "1840 bytes"))
        self.assertEqual(netfamilies.normalize_config("cisco-ios", data.IOS_RUNNING), netfamilies.normalize_config("cisco-ios", later))
        self.assertNotIn("Building configuration", netfamilies.normalize_config("cisco-ios", data.IOS_RUNNING))
        moved = data.VOSS_RUNNING.replace("10:47:20", "18:01:02")
        self.assertEqual(netfamilies.normalize_config("voss", data.VOSS_RUNNING), netfamilies.normalize_config("voss", moved))
        self.assertNotEqual(netfamilies.normalize_config("cisco-ios", data.IOS_RUNNING),
                            netfamilies.normalize_config("cisco-ios", data.IOS_RUNNING.replace("vlan 20", "vlan 21")))

    def test_versions_in_file_names_and_their_order(self):
        names = {"c2960x-universalk9-mz.152-7.E10.bin": "15.2(7)E10", "cat9k_iosxe.17.12.04.SPA.bin": "17.12.04",
                 "summitX-33.6.1.14-patch1-3.xos": "33.6.1.14", "VOSS8400.8.10.1.0.tgz": "8.10.1.0",
                 "image_cbs_ros_3.5.0.25_release_cisco_signed.bin": "3.5.0.25", "fwupd-4.8.2-SNS-amd64-M.maj": "4.8.2"}
        for name, version in names.items():
            self.assertEqual(netfamilies.firmware_version(name), version, name)
        key = netfamilies.version_key
        self.assertGreater(key("15.2(7)E10"), key("15.2(7)E9"))                          # 10 après 9, pas avant
        self.assertGreater(key("17.12.04"), key("17.09.04a"))
        self.assertTrue(netfamilies.same_version("17.9.4a", "17.09.04a"))
        self.assertEqual(netfamilies.uptime_seconds("52 day(s), 02:34:07"), 52 * 86400 + 2 * 3600 + 34 * 60 + 7)


class TestCli(unittest.TestCase):
    def session(self, family, env=None, secret="secret", enable="enable-secret"):
        for patch in simulate(family, env):
            patch.start()
            self.addCleanup(patch.stop)
        return netcli.Session("192.0.2.9", 22, "admin", secret, " ".join(HOST_KEY), netfamilies.FAMILIES[family], enable=enable, timeout=10)

    def test_enable_then_unpaged_output(self):
        """Compte de niveau 1 : loco passe en mode privilégié avec le mot de passe enable, coupe la pagination, et lit
        la configuration entière sans l'écho de la commande ni l'invite."""
        with self.session("cisco-ios", {"NETSIM_LEVEL": "1"}) as cli:
            self.assertTrue(cli.privileged)
            self.assertEqual(cli.base, "SW-CORE")
            text = cli.run("show running-config")
        self.assertTrue(text.startswith("Building configuration..."))
        self.assertTrue(text.rstrip().endswith("end"))
        self.assertNotIn("SW-CORE#", text)

    def test_a_refused_enable_is_said(self):
        with self.assertRaisesRegex(netcli.CliError, "enable"):
            with self.session("cisco-ios", {"NETSIM_LEVEL": "1"}, enable="faux"):
                pass

    def test_paging_left_on_is_followed(self):
        """Une pagination que la commande n'a pas coupée (« --More-- ») est suivie d'elle-même, sans rien perdre."""
        family = netfamilies.Family(**dict(netfamilies.FAMILIES["cisco-ios"].__dict__, prepare=()))
        for patch in simulate("cisco-ios"):
            patch.start()
            self.addCleanup(patch.stop)
        with netcli.Session("192.0.2.9", 22, "admin", "secret", " ".join(HOST_KEY), family, timeout=10) as cli:
            text = cli.run("show running-config")
        self.assertEqual(text.count("\n"), data.IOS_RUNNING.rstrip("\n").count("\n"))
        self.assertNotIn("More", text)

    def test_small_business_asks_for_credentials_itself(self):
        with self.session("cisco-sb", {"NETSIM_PASSWORD": "pw-sb"}, secret="pw-sb") as cli:
            self.assertEqual(cli.base, "SW-ETAGE1")
            self.assertIn("CBS350-24P-4G", cli.run("show inventory"))
        with self.assertRaisesRegex(netcli.LinkError, "refuse le compte"):
            with self.session("cisco-sb", {"NETSIM_PASSWORD": "pw-sb"}, secret="faux"):
                pass

    def test_exos_counter_prompt_and_voss(self):
        with self.session("exos") as cli:
            self.assertEqual(cli.base, "X440G2-BAT-A")
            cli.run("show switch")
            self.assertIn("Primary ver:", cli.run("show switch"))                          # l'invite a changé : .3, .4…
            with self.assertRaises(netcli.CliError):
                cli.run("show nimporte")
        with self.session("voss") as cli:
            self.assertTrue(cli.privileged)                                                # enable sans mot de passe
            self.assertEqual(cli.base, "VSP-COEUR:1")

    def test_clean_text(self):
        self.assertEqual(netcli.clean("ab\bc\x1b[7m --More-- \x1b[m\r\nnext\r\n"), "ac --More-- \nnext\n")


class TestStormshield(unittest.TestCase):
    def setUp(self):
        self.fw = netsim_sns.FakeSns(password="pw").start()
        self.addCleanup(self.fw.stop)

    def test_collect_backup_and_firmware(self):
        fingerprint, details = sns.probe_certificate("127.0.0.1", self.fw.port)
        self.assertEqual((fingerprint, details["issuer"]), (self.fw.fingerprint, "SNS-WebServer-default-authority"))
        with sns.Client("127.0.0.1", self.fw.port, fingerprint, "admin", "pw") as client:
            info = sns.collect(client)
            self.assertEqual((info["model"], info["version"], info["serial"], info["hostname"], info["cpu"]),
                             ("SN310", "4.3.29", "SN31XA19D0012345", "FW-SIEGE", 12))
            self.assertEqual(client.download("CONFIG BACKUP list=all"), self.fw.backup)       # taille et CRC vérifiés
            with self.assertRaisesRegex(sns.SnsError, "refuse"):
                client.command("SYSTEM UPDATE UPLOAD")                                        # sans MODIFY ON
            self.fw.backup = b"autre"
            self.fw.pending.clear()

    def test_refusals(self):
        with self.assertRaisesRegex(sns.SnsError, "refuse le compte"):
            with sns.Client("127.0.0.1", self.fw.port, self.fw.fingerprint, "admin", "faux"):
                pass
        with self.assertRaisesRegex(sns.SnsError, "certificat"):
            with sns.Client("127.0.0.1", self.fw.port, "AB" * 32, "admin", "pw"):
                pass
        self.assertEqual(sns.crc(b"123456789"), "%X" % (0xCBF43926 ^ 0xFFFFFFFF))


class NetworkPortalTest(unittest.TestCase):
    family, env = "cisco-ios", {}

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="loco-net-test-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.flash = os.path.join(self.work, "flash")
        os.makedirs(self.flash)
        self.state = os.path.join(self.work, "state.json")
        env = dict(self.env, NETSIM_STATE=self.state, NETSIM_FLASH=self.flash)
        self.start(simulate(self.family, env) + [
            mock.patch.object(netcli, "probe_host_key", lambda host, port, username="loco": HOST_KEY),
            mock.patch.object(netcli, "push_file", self.push),
            mock.patch.object(network, "FIRST", 0.05), mock.patch.object(network, "STEP", 0.05),
            mock.patch.object(network, "CONFIG_DIR", os.path.join(self.work, "configs")),
            mock.patch.object(network, "FIRMWARE_DIR", os.path.join(self.work, "firmware"))])
        self.pushed = []
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.EXPERIMENTAL_SETTING, '["network"]'))
            conn.execute("DELETE FROM net_devices")
            conn.execute("DELETE FROM net_firmware")
        self.addCleanup(self.reset)
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)

    def start(self, patches):
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def push(self, host, port, username, secret, host_key, local, remote, legacy=True, timeout=7200):
        self.pushed.append((remote, legacy, secret))
        shutil.copy(local, os.path.join(self.flash, remote.split(":")[-1].rsplit("/", 1)[-1]))
        return True, ""

    def reset(self):
        self.idle()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
            conn.execute("DELETE FROM net_devices")
            conn.execute("DELETE FROM net_firmware")
            conn.execute("DELETE FROM users WHERE username LIKE 'nd-%'")
            conn.execute("DELETE FROM access_roles WHERE id LIKE 'nd-%'")

    def idle(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end and (network._WORKERS or network._REFRESH):
            time.sleep(0.05)
        self.assertFalse(network._WORKERS, "tâches encore en cours")

    def add(self, name="SW-CORE", family=None, password="secret", **extra):
        form = {"name": name, "family": family or self.family, "host": "192.0.2.20", "port": "22", "username": "admin",
                "password": password, "enable": "enable-secret", "api_port": "443", "backup_every": "86400", "backup_keep": "30"}
        form.update(extra)
        r = self.admin.post("/network/add", form, page="/network")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        dev_id = int(re.search(r"/network/(\d+)", r.headers["Location"]).group(1))
        html = self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True)
        nonce = re.search(r'name="nonce" value="([0-9a-f]+)"', html).group(1)
        self.assertIn(homeassistant.fingerprint(HOST_KEY[1]), html)
        self.admin.post(f"/network/{dev_id}/confirm", {"nonce": nonce}, page=f"/network/{dev_id}")
        return dev_id

    def row(self, dev_id):
        with transaction() as conn:
            return conn.execute("SELECT * FROM net_devices WHERE id=?", (dev_id,)).fetchone()

    def jobs(self):
        with transaction() as conn:
            return conn.execute("SELECT * FROM net_jobs ORDER BY id").fetchall()

    def backups(self, dev_id):
        with transaction() as conn:
            return conn.execute("SELECT * FROM net_backups WHERE device_id=? ORDER BY id", (dev_id,)).fetchall()

    def upload(self, filename, content=None, family=None, **extra):
        payload = {"csrf": self.admin.token("/network"), "family": family or self.family,
                   "file": (io.BytesIO(content or os.urandom(4096)), filename)}
        payload.update(extra)
        return self.admin.c.post("/network/firmware/upload", content_type="multipart/form-data", data=payload)

    def member(self, name, grants):
        with transaction() as conn:
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("nd-" + name, "Nd " + name, permissions.role_key("Nd " + name), json.dumps(sorted(permissions.expand(grants)))))
            auth.create_user(conn, "nd-" + name, PASSWORD, "nd-" + name, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login("nd-" + name, PASSWORD).status_code, 302)
        return browser


class TestPortal(NetworkPortalTest):
    def test_added_confirmed_and_shown(self):
        dev_id = self.add()
        row = self.row(dev_id)
        self.assertEqual(row["host_key"], " ".join(HOST_KEY))
        self.assertNotIn("secret", row["secret"])                                          # scellé, pas en clair
        self.assertEqual(network.unseal(row["secret"]), "secret")
        info = json.loads(row["info"])
        self.assertEqual((info["model"], info["version"], info["ports_up"]), ("WS-C2960X-48FPD-L", "15.2(7)E9", 3))
        html = self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True)
        self.assertIn("FOC2133X0AB", html)
        self.assertNotIn("enable-secret", html)
        fleet = self.admin.c.get("/fleet").get_data(as_text=True)
        self.assertIn("nd-row", fleet)
        self.assertIn("WS-C2960X-48FPD-L · 15.2(7)E9", fleet)
        self.assertIn("en ligne", self.admin.c.get("/network").get_data(as_text=True))

    def test_backups_are_kept_when_the_config_changes(self):
        dev_id = self.add()
        self.admin.post(f"/network/{dev_id}/backup", page=f"/network/{dev_id}")
        self.idle()
        self.admin.post(f"/network/{dev_id}/backup", page=f"/network/{dev_id}")
        self.idle()
        self.assertEqual(len(self.backups(dev_id)), 1)                                     # inchangée : rien de plus
        self.assertIn("inchangée", self.jobs()[-1]["output"])
        changed = {"PATH": os.environ.get("PATH", ""), "NETSIM_STATE": self.state, "NETSIM_EXTRA": "vlan 30\n name INVITES"}
        with mock.patch.object(netcli, "environment", lambda secret, term="vt100": changed):         # modifiée sur le switch
            self.admin.post(f"/network/{dev_id}/backup", page=f"/network/{dev_id}")
            self.idle()
        kept = self.backups(dev_id)
        self.assertEqual((len(kept), kept[-1]["added"], kept[-1]["removed"]), (2, 2, 0))
        with open(os.path.join(network.CONFIG_DIR, kept[-1]["file"]), "rb") as f:
            self.assertNotIn(b"hostname SW-CORE", f.read())                                 # chiffrée sur le disque
        html = self.admin.c.get(f"/network/{dev_id}/config/{kept[-1]['id']}").get_data(as_text=True)
        self.assertIn('<span class="add">+vlan 30</span>', html)
        raw = self.admin.c.get(f"/network/{dev_id}/config/{kept[-1]['id']}/download")
        self.assertIn(b"name INVITES", raw.data)
        self.assertIn("attachment", raw.headers["Content-Disposition"])
        backups = self.admin.c.get("/backups").get_data(as_text=True)
        self.assertIn("Configurations des équipements réseau", backups)

    def test_a_wrong_password_is_reported(self):
        self.family = "cisco-sb"
        self.start(simulate("cisco-sb", {"NETSIM_PASSWORD": "le-bon"}))
        dev_id = self.add(family="cisco-sb", password="pas-le-bon")
        self.assertIn("refuse le compte", self.row(dev_id)["error"])
        self.assertIn("liaison en échec", self.admin.c.get("/fleet").get_data(as_text=True))

    def test_firmware_on_an_ios_switch(self):
        """Bibliothèque : la version lue dans le nom ; l'équipement plus ancien le signale ; installation avec la
        configuration sauvegardée avant, l'image vérifiée par le switch (MD5), le démarrage réglé dessus, le
        redémarrage et le retour suivis, la nouvelle version constatée."""
        dev_id = self.add()
        image = os.urandom(8192)
        self.assertEqual(self.upload("c2960x-universalk9-mz.152-7.E10.bin", image).status_code, 302)
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        self.assertEqual((fw["version"], fw["sha256"], fw["md5"]), ("15.2(7)E10", hashlib.sha256(image).hexdigest(), hashlib.md5(image).hexdigest()))
        self.assertIn("firmware 15.2(7)E10 disponible", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertIn('id="nd-firmware"', self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True))
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"], "reboot": "1"}, page=f"/network/{dev_id}")
        self.idle()
        job = self.jobs()[-1]
        self.assertEqual(job["state"], "success", job["output"])
        self.assertIn("Empreinte MD5 vérifiée", job["output"])
        self.assertIn("Firmware 15.2(7)E10 en service", job["output"])
        self.assertEqual(self.pushed[0][:2], ("flash:c2960x-universalk9-mz.152-7.E10.bin", True))
        self.assertEqual(self.backups(dev_id)[0]["reason"], "avant le firmware 15.2(7)E10")
        info = json.loads(self.row(dev_id)["info"])
        self.assertEqual(info["version"], "15.2(7)E10")
        self.assertLess(info["uptime"], 300)

    def test_an_image_of_another_platform_is_never_offered(self):
        """Une image de Catalyst 9300 n'est pas proposée à un 2960X, même plus récente ; un modèle indiqué au dépôt décide."""
        dev_id = self.add()
        self.upload("cat9k_iosxe.17.12.04.SPA.bin")
        self.assertNotIn("firmware 17.12.04 disponible", self.admin.c.get("/fleet").get_data(as_text=True))
        self.upload("c2960x-universalk9-mz.152-7.E10.bin")
        self.assertIn("firmware 15.2(7)E10 disponible", self.admin.c.get("/fleet").get_data(as_text=True))
        self.upload("autre-image-15.2.8.bin", version="15.2(8)E1", models="C9300-*")
        html = self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True)
        self.assertIn("Installer le firmware 15.2(7)E10", html)                            # pas 15.2(8)E1, réservé au C9300
        self.assertEqual(netfamilies.stem("flash:/c2960x-universalk9-mz.152-7.E9.bin"), "c2960x-universalk9")
        self.assertEqual(netfamilies.platform("cisco-ios", {"image": "flash:packages.conf", "sw_image": "CAT9K_IOSXE"}), "cat9k-iosxe")
        self.assertEqual(netfamilies.stem("flash://system/images/image_cbs_ros_3.5.0.22_release_cisco_signed.bin"), "image-cbs-ros")

    def test_rights(self):
        dev_id = self.add()
        network.take_backup(network.connect(), self.row(dev_id), "manuelle")
        b = self.backups(dev_id)[0]
        viewer = self.member("ro", ["network.view", "fleet.view"])
        html = viewer.c.get(f"/network/{dev_id}").get_data(as_text=True)
        self.assertNotIn("Sauvegarder la configuration", html)
        self.assertNotIn("/shell", html)
        self.assertNotIn('id="reglages"', html)
        self.assertEqual(viewer.c.get(f"/network/{dev_id}/config/{b['id']}").status_code, 403)
        for path in ("backup", "reboot", "shell", "delete", "settings"):
            self.assertEqual(viewer.c.post(f"/network/{dev_id}/{path}", data={"csrf": viewer.token(f"/network/{dev_id}")}).status_code, 403, path)
        self.assertEqual(viewer.c.post("/network/firmware/upload", data={"csrf": viewer.token("/network")}).status_code, 403)
        self.assertEqual(len(self.jobs()), 0)

    def test_terminal_hands_the_password_to_ssh_not_to_its_arguments(self):
        dev_id = self.add()
        started = {}
        with mock.patch.object(adb, "start_command", lambda sid, argv, env, name, after=None: started.update(argv=argv, env=env)), \
                mock.patch.object(homeassistant, "terminal_ready", lambda: True):
            r = self.admin.post(f"/network/{dev_id}/shell", page=f"/network/{dev_id}")
        self.assertEqual(r.status_code, 302)
        self.assertIn("-tt", started["argv"])
        self.assertNotIn("secret", " ".join(started["argv"]))
        self.assertEqual((started["env"]["LOCO_NET_PASS"], started["env"]["SSH_ASKPASS_REQUIRE"]), ("secret", "force"))
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT target FROM remote_sessions ORDER BY created DESC LIMIT 1").fetchone()[0], f"net:{dev_id}")

    def test_off_with_the_feature(self):
        self.add()
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.EXPERIMENTAL_SETTING,))
        self.assertNotIn("nd-row", self.admin.c.get("/fleet").get_data(as_text=True))
        self.assertEqual(self.admin.c.get("/network").status_code, 403)


class TestInstallMode(NetworkPortalTest):
    env = {"NETSIM_VARIANT": "xe"}

    def test_an_install_mode_switch_needs_the_reboot(self):
        dev_id = self.add(name="C9300-SIEGE")
        self.upload("cat9k_iosxe.17.12.04.SPA.bin")
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"]}, page=f"/network/{dev_id}")
        self.idle()
        self.assertEqual(self.jobs()[-1]["state"], "failed")
        self.assertIn("mode INSTALL", self.jobs()[-1]["output"])
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"], "reboot": "1"}, page=f"/network/{dev_id}")
        self.idle()
        self.assertEqual(self.jobs()[-1]["state"], "success", self.jobs()[-1]["output"])
        self.assertEqual(json.loads(self.row(dev_id)["info"])["version"], "17.12.04")


class TestExtreme(NetworkPortalTest):
    family = "exos"

    def test_exos_firmware(self):
        dev_id = self.add(name="X440G2-BAT-A")
        self.upload("summitX-33.6.2.5.xos")
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"], "reboot": "1"}, page=f"/network/{dev_id}")
        self.idle()
        job = self.jobs()[-1]
        self.assertEqual(job["state"], "success", job["output"])
        self.assertIn("partition secondary", job["output"])
        self.assertEqual(json.loads(self.row(dev_id)["info"])["version"], "33.6.2.5")


class TestVoss(NetworkPortalTest):
    family = "voss"

    def test_voss_firmware_is_committed_after_the_reboot(self):
        dev_id = self.add(name="VSP-COEUR")
        self.upload("VOSS8400.8.10.2.0.tgz")
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"], "reboot": "1"}, page=f"/network/{dev_id}")
        self.idle()
        job = self.jobs()[-1]
        self.assertEqual(job["state"], "success", job["output"])
        self.assertEqual((self.pushed[0][0], self.pushed[0][1]), ("/intflash/VOSS8400.8.10.2.0.tgz", False))     # SFTP
        self.assertIn("Version validée (software commit)", job["output"])
        self.assertEqual(json.loads(self.row(dev_id)["info"])["version"], "8.10.2.0")


class TestSmallBusiness(NetworkPortalTest):
    family, env = "cisco-sb", {"NETSIM_PASSWORD": "secret"}

    def test_firmware_is_installed_on_the_switch(self):
        dev_id = self.add(name="SW-ETAGE1")
        self.upload("image_cbs_ros_3.5.0.25_release_cisco_signed.bin")
        html = self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True)
        self.assertIn("TFTP", html)
        self.assertNotIn('id="nd-firmware"', html)
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        r = self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"]}, page=f"/network/{dev_id}")
        self.assertEqual(r.status_code, 409)


class TestStormshieldPortal(NetworkPortalTest):
    family = "stormshield"

    def setUp(self):
        super().setUp()
        self.fw = netsim_sns.FakeSns(password="secret").start()
        self.addCleanup(self.fw.stop)
        self.start([mock.patch.object(netcli, "probe_host_key", mock.Mock(side_effect=netcli.LinkError("Connection refused")))])

    def test_a_firewall_through_its_api(self):
        r = self.admin.post("/network/add", {"name": "FW-SIEGE", "family": "stormshield", "host": "127.0.0.1", "port": "22",
                                             "username": "admin", "password": "secret", "api_port": str(self.fw.port),
                                             "backup_every": "86400", "backup_keep": "30"}, page="/network")
        dev_id = int(re.search(r"/network/(\d+)", r.headers["Location"]).group(1))
        html = self.admin.c.get(f"/network/{dev_id}").get_data(as_text=True)
        self.assertIn(network.pretty(self.fw.fingerprint), html)
        self.assertIn("SSH ne répond pas", html)                                            # pas de terminal, le reste suit
        nonce = re.search(r'name="nonce" value="([0-9a-f]+)"', html).group(1)
        self.admin.post(f"/network/{dev_id}/confirm", {"nonce": nonce}, page=f"/network/{dev_id}")
        row = self.row(dev_id)
        self.assertEqual((row["api_cert"], row["host_key"], row["error"]), (self.fw.fingerprint, "", ""))
        self.assertEqual(json.loads(row["info"])["model"], "SN310")
        self.admin.post(f"/network/{dev_id}/backup", page=f"/network/{dev_id}")
        self.idle()
        b = self.backups(dev_id)[0]
        self.assertEqual((b["binary"], b["size"]), (1, len(self.fw.backup)))
        self.assertEqual(self.admin.c.get(f"/network/{dev_id}/config/{b['id']}/download").data, self.fw.backup)
        self.upload("fwupd-4.8.2-SNS-amd64-M.maj")
        with transaction() as conn:
            fw = conn.execute("SELECT * FROM net_firmware").fetchone()
        self.admin.post(f"/network/{dev_id}/firmware", {"firmware_id": fw["id"], "reboot": "1"}, page=f"/network/{dev_id}")
        self.idle()
        job = self.jobs()[-1]
        self.assertEqual(job["state"], "success", job["output"])
        self.assertEqual(self.fw.version, "4.8.2")
        self.assertIn("MODIFY ON", self.fw.commands)


if __name__ == "__main__":
    unittest.main()
