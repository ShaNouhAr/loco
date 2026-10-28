"""Appliance Alpine et serveurs Linux : réglages, mise à jour vérifiée avec retour arrière, paquets du système
(correctifs de sécurité, noyau, services à relancer), demandes de l'agent, pages du portail."""

import contextlib
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
import locoadmin
locoadmin.LANG = "fr"  # journaux de l'agent en français (langue de référence) pour des assertions stables
from loco import appliance, config


class TempAgent(unittest.TestCase):
    """Dossiers de l'agent, sources et loco.conf dans un dossier temporaire."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="lr-appliance-")
        root = self.work.name
        self.conf = os.path.join(root, "loco.conf")
        with open(self.conf, "w") as f:
            f.write('# Généré\nALLOW="10.0.0.0/8 192.168.0.0/16"\nADMIN_ALLOW="192.168.43.0/24"\nHTTP_ENABLED=1\n'
                    'UPSTREAM_PROXY=""\nWU_ENABLED=1\nRPM_ENABLED=1\nFLATPAK_ENABLED=1\nAPT_CACHE_SIZE=20g\n'
                    'WU_CACHE_SIZE=\nRPM_CACHE_SIZE=\nFLATPAK_CACHE_SIZE=\nMAX_UPLOAD=8g\nBACKUP_KEEP=3\n'
                    'RESOLVERS="1.1.1.1 9.9.9.9"\n'
                    f"SOURCE_DIR={root}/src/loco-2.0.0\n")
        agent = os.path.join(root, "agent")
        sources = os.path.join(root, "src")
        os.makedirs(os.path.join(sources, "loco-2.0.0", "linux"))
        with open(os.path.join(sources, "loco-2.0.0", "linux", "install.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        for path in (os.path.join(agent, "requests"), os.path.join(agent, "jobs")):
            os.makedirs(path)
        values = {"CONF": self.conf, "AGENT_DIR": agent, "REQUESTS": os.path.join(agent, "requests"),
                  "JOBS": os.path.join(agent, "jobs"), "STATUS": os.path.join(agent, "status.json"),
                  "LOCK": os.path.join(agent, "lock"), "HEARTBEAT": os.path.join(agent, "alive"),
                  "SOURCES": sources, "STATE": os.path.join(root, "state"), "ALPINE": True,
                  "BOOT": os.path.join(root, "boot"), "MODULES": os.path.join(root, "modules"),
                  "REBOOT_FLAG": os.path.join(root, "reboot-required")}
        os.makedirs(values["BOOT"])
        os.makedirs(os.path.join(values["MODULES"], os.uname().release))
        for name, value in values.items():
            patcher = mock.patch.object(locoadmin, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sources = sources

    def tearDown(self):
        self.work.cleanup()


class TestSettings(TempAgent):
    def test_configuration_is_read_like_the_shell(self):
        conf = locoadmin.read_conf()
        self.assertEqual(conf["ALLOW"], "10.0.0.0/8 192.168.0.0/16")
        self.assertEqual(conf["UPSTREAM_PROXY"], "")
        self.assertEqual(conf["WU_CACHE_SIZE"], "")

    def test_only_real_changes_are_kept_and_normalised(self):
        clean = locoadmin.validate({"ALLOW": "10.0.0.0/8  192.168.0.0/16", "ADMIN_ALLOW": "192.168.43.7/24",
                                    "HTTP_ENABLED": "1", "WU_CACHE_SIZE": "50g"})
        self.assertEqual(clean, {"WU_CACHE_SIZE": "50g"})    # 192.168.43.7/24 : même réseau qu'actuellement

    def test_values_that_could_reach_the_shell_are_refused(self):
        for key, value in (("UPSTREAM_PROXY", "http://$(reboot):3128"), ("UPSTREAM_PROXY", "http://a`id`:1"),
                           ("UPSTREAM_PROXY", 'http://a" b'), ("ALLOW", "10.0.0.0/8; reboot"),
                           ("RESOLVERS", "1.1.1.1 $(id)"), ("RESOLVERS", "fe80::%$(id)"),
                           ("ALLOW", "fe80::%$(id)/64"), ("ADMIN_ALLOW", "fe80::%eth0/64"), ("MAX_UPLOAD", "8t"), ("BACKUP_KEEP", "0"),
                           ("HTTP_ENABLED", "yes"), ("DATA", "/tmp"), ("WU_CACHE_SIZE", "50 g")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                locoadmin.validate({key: value})
        self.assertEqual(locoadmin.validate({"UPSTREAM_PROXY": "http://user:p%40ss@proxy.lan:3128"}),
                         {"UPSTREAM_PROXY": "http://user:p%40ss@proxy.lan:3128"})

    def test_installer_options_follow_the_settings(self):
        args = []
        for key, value in {"HTTP_ENABLED": "0", "UPSTREAM_PROXY": "", "RPM_ENABLED": "1", "BACKUP_KEEP": "5"}.items():
            args += locoadmin.SETTINGS[key][2](value)
        self.assertEqual(args, ["--no-http", "--no-proxy", "--rpm", "--backup-keep", "5"])


def _archive(version, files=None, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (files or {f"loco-{version}/linux/install.sh": b"#!/bin/bash\n"}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            tar.addfile(extra)
    return buf.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestUpdateLoco(TempAgent):
    def release(self, archive, digest=None):
        digest = digest or hashlib.sha256(archive).hexdigest()
        latest = {"version": "2.1.0", "url": "", "published": "", "notes": "",
                  "archive": "https://example.test/loco-2.1.0.tar.gz", "sums": "https://example.test/SHA256SUMS"}
        files = {latest["archive"]: archive, latest["sums"]: f"{digest}  loco-2.1.0.tar.gz\n".encode()}
        return (mock.patch.object(locoadmin, "latest_release", return_value=latest),
                mock.patch.object(locoadmin, "_get", lambda url, timeout=30: FakeResponse(files[url])),
                mock.patch.object(locoadmin, "current_version", return_value="2.0.0"))

    def run_update(self, archive, digest=None, install_codes=(0,)):
        codes = list(install_codes)
        calls = []

        def install(source, args, log):
            calls.append(os.path.basename(source))
            return codes.pop(0)
        p1, p2, p3 = self.release(archive, digest)
        with p1, p2, p3, mock.patch.object(locoadmin, "_install", install), mock.patch.object(locoadmin, "_run", return_value=0):
            ok = locoadmin.update_loco(log=lambda _t: None)
        return ok, calls

    def test_verified_archive_is_installed(self):
        ok, calls = self.run_update(_archive("2.1.0"))
        self.assertTrue(ok)
        self.assertEqual(calls, ["loco-2.1.0"])
        self.assertTrue(os.path.isfile(os.path.join(self.sources, "loco-2.1.0", "linux", "install.sh")))

    def test_wrong_checksum_is_refused_before_anything_is_installed(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_update(_archive("2.1.0"), digest="0" * 64)
        self.assertFalse(os.path.exists(os.path.join(self.sources, "loco-2.1.0")))

    def test_failed_installation_rolls_back_to_the_previous_sources(self):
        with open(os.path.join(self.sources, "loco-2.0.0", "linux", "install.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        ok, calls = self.run_update(_archive("2.1.0"), install_codes=(1, 0))
        self.assertFalse(ok)
        self.assertEqual(calls, ["loco-2.1.0", "loco-2.0.0"])

    def test_failed_update_restores_configuration_and_database(self):
        import sqlite3
        os.makedirs(locoadmin.STATE)
        db = os.path.join(locoadmin.STATE, "portal.db")
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE original(value)")
            conn.execute("INSERT INTO original VALUES ('conservé')")
            conn.commit()
        with open(self.conf) as f:
            original = f.read()
        calls = []

        def install(source, args, log):
            calls.append(source)
            if len(calls) == 1:
                with open(self.conf, "w") as f:
                    f.write("SOURCE_DIR=/bad\n")
                with contextlib.closing(sqlite3.connect(db)) as conn:
                    conn.execute("DROP TABLE original")
                    conn.commit()
                return 1
            with open(self.conf) as f:
                self.assertEqual(f.read(), original)
            with contextlib.closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT value FROM original").fetchone()[0], "conservé")
            return 0

        p1, p2, p3 = self.release(_archive("2.1.0"))
        with p1, p2, p3, mock.patch.object(locoadmin, "_install", install), mock.patch.object(locoadmin, "_run", return_value=0):
            self.assertFalse(locoadmin.update_loco(log=lambda _: None))
        self.assertEqual(len(calls), 2)

    def test_archives_with_links_or_escaping_paths_are_refused(self):
        link = tarfile.TarInfo("loco-2.1.0/linux/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
        for archive in (_archive("2.1.0", extra=link),
                        _archive("2.1.0", files={"loco-2.1.0/../../etc/cron.d/x": b"x"}),
                        _archive("2.1.0", files={"autre/linux/install.sh": b"x"})):
            with self.subTest(), self.assertRaisesRegex(ValueError, "archive refusée"):
                self.run_update(archive)


class TestRequests(TempAgent):
    def test_portal_request_is_revalidated_and_bounded(self):
        locoadmin.request("configure", {"WU_CACHE_SIZE": "40g"}, user="admin")
        names = os.listdir(locoadmin.REQUESTS)
        self.assertEqual(len(names), 1)
        req = locoadmin._read_request(os.path.join(locoadmin.REQUESTS, names[0]))
        self.assertEqual(req, {"action": "configure", "params": {"WU_CACHE_SIZE": "40g"}, "user": "admin"})
        with self.assertRaises(ValueError):
            locoadmin.request("configure", {"WU_CACHE_SIZE": "$(id)"})
        with self.assertRaises(ValueError):
            locoadmin.request("shell", {})

    def test_links_and_special_files_are_not_followed(self):
        secret = os.path.join(self.work.name, "secret.json")
        with open(secret, "w") as f:
            json.dump({"action": "reboot"}, f)
        link = os.path.join(locoadmin.REQUESTS, "x.json")
        os.symlink(secret, link)
        self.assertIsNone(locoadmin._read_request(link))
        fifo = os.path.join(locoadmin.REQUESTS, "y.json")
        os.mkfifo(fifo)
        self.assertIsNone(locoadmin._read_request(fifo))
        big = os.path.join(locoadmin.REQUESTS, "z.json")
        with open(big, "w") as f:
            f.write(" " * (locoadmin.MAX_REQUEST + 1))
        self.assertIsNone(locoadmin._read_request(big))

    def test_job_log_rejects_paths(self):
        self.assertEqual(locoadmin.job_log("../status"), "")


APT_SIMULATION = """NOTE: This is only a simulation!
Inst libssl3t64 [3.5.1-1] (3.5.4-1~deb13u1 Debian-Security:13/stable-security [amd64])
Inst linux-image-6.12.57+deb13-amd64 (6.12.57-1 Debian-Security:13/stable-security [amd64])
Inst linux-image-amd64 [6.12.48-1] (6.12.57-1 Debian-Security:13/stable-security [amd64])
Inst squid [6.13-2] (6.13-2+deb13u1 Debian:13.2/stable [amd64])
Inst tzdata [2025b-4] (2025b-4+deb13u1 Debian:13.2/stable [all]) []
Conf libssl3t64 (3.5.4-1~deb13u1 Debian-Security:13/stable-security [amd64])
"""

DNF_CHECK = """
kernel.x86_64                     5.14.0-570.25.1.el9_6       baseos
openssl-libs.x86_64               1:3.2.2-6.el9_5.1           baseos
vim-minimal.x86_64                2:8.2.2637-22.el9_6         baseos
Obsoleting Packages
grub2-tools.x86_64                1:2.06-104.el9_6            baseos
    grub2-tools.x86_64            1:2.06-94.el9               @baseos
"""

DNF_SECURITY = """RLSA-2025:1234 Important/Sec.  kernel-5.14.0-570.25.1.el9_6.x86_64
RLSA-2025:1301 Moderate/Sec.   openssl-libs-1:3.2.2-6.el9_5.1.x86_64
RLSA-2025:1302 Low/Sec.        kernel-5.14.0-570.25.1.el9_6.x86_64
"""


def _completed(stdout="", code=0):
    return mock.Mock(returncode=code, stdout=stdout, stderr="")


class TestSystemPackages(TempAgent):
    """Paquets du système : listes des gestionnaires, sécurité, noyau, services à relancer."""

    def test_apt_security_updates_and_key_packages_come_first(self):
        with mock.patch.object(locoadmin, "package_manager", return_value="apt"), \
                mock.patch.object(locoadmin.subprocess, "run", side_effect=[_completed(), _completed(APT_SIMULATION)]) as run:
            packages = locoadmin.pending_packages()
        self.assertEqual(run.call_args_list[1].args[0][:2], ["apt-get", "-s"])
        names = [p["name"] for p in packages]
        self.assertEqual(names, ["libssl3t64", "linux-image-6.12.57+deb13-amd64", "linux-image-amd64", "squid", "tzdata"])
        by = {p["name"]: p for p in packages}
        self.assertTrue(by["libssl3t64"]["security"])
        self.assertEqual((by["libssl3t64"]["current"], by["libssl3t64"]["new"]), ("3.5.1-1", "3.5.4-1~deb13u1"))
        self.assertEqual(by["linux-image-6.12.57+deb13-amd64"]["current"], "")    # nouveau paquet du noyau
        self.assertEqual(by["linux-image-amd64"]["tags"], ["noyau"])
        self.assertEqual(by["squid"]["tags"], ["loco"])
        self.assertFalse(by["squid"]["security"])
        self.assertEqual(by["libssl3t64"]["tags"], ["socle"])
        self.assertEqual(by["tzdata"]["tags"], [])

    def test_apt_failure_is_reported(self):
        with mock.patch.object(locoadmin, "package_manager", return_value="apt"), \
                mock.patch.object(locoadmin.subprocess, "run", return_value=_completed(code=100)):
            with self.assertRaisesRegex(ValueError, "injoignables"):
                locoadmin.pending_packages()

    def test_dnf_updates_with_advisories(self):
        rpm = _completed("kernel 5.14.0-503.40.1.el9_5\nkernel 5.14.0-570.12.1.el9_6\nopenssl-libs 3.2.2-6.el9_5\n"
                         "vim-minimal 8.2.2637-21.el9\n")
        with mock.patch.object(locoadmin, "package_manager", return_value="dnf"), \
                mock.patch.object(locoadmin.subprocess, "run",
                                  side_effect=[_completed(DNF_CHECK, 100), _completed(DNF_SECURITY), rpm]):
            packages = locoadmin.pending_packages()
        by = {p["name"]: p for p in packages}
        self.assertEqual(sorted(by), ["kernel", "openssl-libs", "vim-minimal"])     # obsolètes ignorés
        self.assertEqual((by["kernel"]["security"], by["kernel"]["severity"]), (True, "Important"))
        self.assertEqual(by["kernel"]["current"], "5.14.0-570.12.1.el9_6")          # le plus récent installé
        self.assertEqual(by["openssl-libs"]["severity"], "Moderate")
        self.assertFalse(by["vim-minimal"]["security"])
        self.assertEqual([p["name"] for p in packages][:2], ["kernel", "openssl-libs"])

    def test_dnf_repository_error(self):
        with mock.patch.object(locoadmin, "package_manager", return_value="dnf"), \
                mock.patch.object(locoadmin.subprocess, "run", return_value=_completed(code=1)):
            with self.assertRaises(ValueError):
                locoadmin.pending_packages()

    def test_apk_upgradable_list(self):
        out = ("musl-1.2.5-r10 x86_64 {musl} (MIT) [upgradable from: musl-1.2.5-r9]\n"
               "linux-virt-6.12.40-r0 x86_64 {linux-virt} (GPL-2.0-only) [upgradable from: linux-virt-6.12.38-r0]\n")
        with mock.patch.object(locoadmin, "package_manager", return_value="apk"), \
                mock.patch.object(locoadmin.subprocess, "run", side_effect=[_completed(), _completed(out)]):
            packages = locoadmin.pending_packages()
        by = {p["name"]: p for p in packages}
        self.assertEqual((by["musl"]["current"], by["musl"]["new"]), ("1.2.5-r9", "1.2.5-r10"))
        self.assertEqual(by["linux-virt"]["tags"], ["noyau"])
        self.assertFalse(any(p["security"] for p in packages))

    def test_newer_kernel_or_flag_requires_a_reboot(self):
        running = os.uname().release
        with mock.patch.object(locoadmin, "in_container", return_value=False):
            self.assertEqual(locoadmin.reboot_reasons({}), [])
            newer = "99.0.0-1-amd64"
            open(os.path.join(locoadmin.BOOT, "vmlinuz-" + newer), "w").close()
            open(os.path.join(locoadmin.BOOT, "vmlinuz-0-rescue-abc"), "w").close()
            self.assertEqual(locoadmin.installed_kernels()[0], newer)
            self.assertIn({"code": "kernel-newer", "installed": newer, "running": running}, locoadmin.reboot_reasons({}))
        # Conteneur : le noyau est celui de l'hôte.
        with mock.patch.object(locoadmin, "in_container", return_value=True):
            self.assertEqual(locoadmin.reboot_reasons({}), [])
            with open(locoadmin.REBOOT_FLAG, "w"), open(locoadmin.REBOOT_FLAG + ".pkgs", "w") as f:
                f.write("libc6\nlibc6\ndbus\n")
            self.assertEqual(locoadmin.reboot_reasons({}), [{"code": "requested", "packages": "dbus, libc6"}])
            self.assertIn({"code": "stale-core", "units": "init"},
                          locoadmin.reboot_reasons({"init": ["systemd"], "nginx.service": ["nginx"]}))

    def test_replaced_libraries_are_detected(self):
        self.assertTrue(locoadmin._stale_mapping(
            "7f2a1c000000-7f2a1c022000 r--p 00000000 00:2f 1234  /usr/lib/x86_64-linux-gnu/libssl.so.3 (deleted)\n"))
        self.assertFalse(locoadmin._stale_mapping(
            "7f2a1c000000-7f2a1c022000 r--p 00000000 00:2f 1234  /usr/lib/x86_64-linux-gnu/libssl.so.3\n"))
        self.assertFalse(locoadmin._stale_mapping("7f2a1c000000-7f2a1c022000 rw-s 00000000 00:01 9  /memfd:x (deleted)\n"))
        self.assertIsInstance(locoadmin.stale_services(), dict)

    def test_security_only_upgrades_the_flagged_packages(self):
        pending = [{"name": "libssl3t64", "current": "3.5.1-1", "security": True},
                   {"name": "linux-image-6.12.57+deb13-amd64", "current": "", "security": True},
                   {"name": "squid", "current": "6.13-2", "security": False}]
        commands = []
        with mock.patch.object(locoadmin, "package_manager", return_value="apt"), \
                mock.patch.object(locoadmin, "_run", side_effect=lambda cmd, log, **kw: commands.append(cmd) or 0), \
                mock.patch.object(locoadmin, "_apt_pending", return_value=pending), \
                mock.patch.object(locoadmin, "stale_services", return_value={}), \
                mock.patch.object(locoadmin, "_wait_healthy", return_value=True):
            self.assertTrue(locoadmin.update_system(lambda _t: None, security_only=True))
        install = commands[1]
        self.assertEqual(install[-3:], ["install", "--only-upgrade", "libssl3t64"])
        self.assertIn("Dpkg::Options::=--force-confold", install)

    def test_full_upgrade_restarts_only_loco_services(self):
        commands = []
        stale = {"nginx.service": ["nginx"], "ssh.service": ["sshd"], "loco-agent.service": ["python3"], "init": ["systemd"]}
        log = []
        with mock.patch.object(locoadmin, "package_manager", return_value="dnf"), \
                mock.patch.object(locoadmin, "_run", side_effect=lambda cmd, log, **kw: commands.append(cmd) or 0), \
                mock.patch.object(locoadmin, "stale_services", return_value=stale), \
                mock.patch.object(locoadmin, "reboot_reasons", return_value=[]), \
                mock.patch.object(locoadmin, "_wait_healthy", return_value=True):
            self.assertTrue(locoadmin.update_system(log.append))
        self.assertEqual(commands[0], ["dnf", "-y", "--refresh", "upgrade"])
        self.assertIn(["systemctl", "restart", "nginx.service"], commands)
        self.assertIn(["systemctl", "restart", "--no-block", "loco-agent.service"], commands)
        self.assertIn(["systemctl", "daemon-reexec"], commands)
        self.assertFalse(any("ssh.service" in c for c in commands))
        self.assertTrue(any("ssh.service" in line for line in log))

    def test_alpine_has_no_security_only_mode(self):
        with mock.patch.object(locoadmin, "package_manager", return_value="apk"):
            with self.assertRaisesRegex(ValueError, "Alpine"):
                locoadmin.update_system(lambda _t: None, security_only=True)

    def test_configure_is_appliance_only_but_loco_update_is_not(self):
        with mock.patch.object(locoadmin, "ALPINE", False):
            with self.assertRaises(ValueError):                       # configure : appliance seulement
                locoadmin.request("configure", {"WU_CACHE_SIZE": "40g"})
            locoadmin.request("update-loco", user="admin")           # loco : tout serveur Linux
            locoadmin.request("set-repo", {"repo": "octo/loco"}, user="admin")
            locoadmin.request("update-security", user="admin")
        self.assertEqual(len(os.listdir(locoadmin.REQUESTS)), 3)


class TestJobProgress(unittest.TestCase):
    """Barre d'avancement : étape et pourcentage lus dans le journal de l'opération."""

    @staticmethod
    def progress(action, log, state="running"):
        with fixture.loco.app.test_request_context():
            return appliance.job_progress({"action": action, "state": state, "started": "2026-10-27 15:00:00"}, log)

    def test_loco_update_follows_download_fingerprint_and_installer_steps(self):
        self.assertEqual(self.progress("update-loco", "15:00:01 Téléchargement de https://x/loco.tar.gz\n")["pct"], 5)
        self.assertEqual(self.progress("update-loco", "15:00:01 Empreinte SHA-256 vérifiée : abc\n")["pct"], 15)
        log = ("15:00:02 Installation de loco 2.73.0 (de 2.72.0)\n15:00:03 \x1b[1;34m==>\x1b[0m Vérification des ports\n"
               "15:00:09 \x1b[1;34m==>\x1b[0m Installation des paquets\n")
        p = self.progress("update-loco", log)
        self.assertEqual((p["step"], p["index"], p["total"], p["pct"]), ("Installation des paquets", 2, 10, 37))
        self.assertIsNotNone(p["elapsed"])
        self.assertEqual(self.progress("update-loco", log, state="success")["pct"], 100)
        self.assertEqual(self.progress("update-loco", "15:00:01 loco 2.72.0 est à jour.\n")["pct"], 100)

    def test_system_updates_read_the_package_manager(self):
        apk = "(3/12) Upgrading musl (1.2.5-r9 -> 1.2.6-r1)\n(4/12) Upgrading busybox\n"
        self.assertEqual(self.progress("update-system", apk)["step"], "Paquet 4 sur 12")
        dnf = "  Upgrading        : openssl-libs-1:3.2.2-6.el9.x86_64        7/20 \n"
        self.assertEqual(self.progress("update-security", dnf)["pct"], round(5 + 90 * 7 / 20))
        apt = "3 upgraded, 1 newly installed, 0 to remove\nSetting up libssl3 (3.0.17)\nSetting up openssl (3.0.17)\n"
        self.assertEqual(self.progress("update-system", apt)["step"], "Paquet 2 sur 4")
        self.assertIsNone(self.progress("check", "")["pct"])                  # indéterminé : barre animée
        self.assertEqual(self.progress("update-loco", "", state="queued")["step"], "En attente de l'agent…")


class TestPages(TempAgent):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(config, "APPLIANCE", True), mock.patch.object(config, "SERVER_UPDATES", True),
                        mock.patch.object(appliance, "locoadmin", locoadmin, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        with open(locoadmin.HEARTBEAT, "w") as f:
            f.write("vivant")
        fixture.reset_failures()
        self.b = fixture.Browser()
        self.b.login("admin", fixture.ADMIN_PW)

    def test_pages_show_versions_and_settings(self):
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-15 09:00:00", "latest": {"version": "9.0.0", "url": "", "published": ""},
                       "system_updates": ["musl-1.2.6-r1"], "errors": []}, f)
        html = self.b.c.get("/system/updates").get_data(as_text=True)
        self.assertIn("Installer 9.0.0", html)
        self.assertIn("musl-1.2.6-r1", html)
        html = self.b.c.get("/system/configuration").get_data(as_text=True)
        self.assertIn('value="10.0.0.0/8 192.168.0.0/16"', html)

    def test_news_of_the_available_version_are_shown_before_installing(self):
        news = "## 9.1.0 — 2026-11-02\n\n- **Grande nouveauté.** avec `loco-update`.\n\n## 9.0.0 — 2026-11-01\n\n- **Autre.** b\n"
        shipped = os.path.join(self.work.name, "CHANGELOG.md")                # journal livré avec la version installée
        with open(shipped, "w", encoding="utf-8") as f:
            f.write(f"# Journal\n\n## {config.VERSION} — 2026-10-01\n\n- **Version actuelle.** x\n")
        patch = mock.patch.object(config, "CHANGELOG_PATH", shipped)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(appliance._CHANGELOG.update, mtime=None, entries=[])
        appliance._CHANGELOG["mtime"] = None
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-15 09:00:00", "latest": {"version": "9.1.0", "url": "", "published": "2026-11-02T10:00:00Z",
                                                                  "changelog": news}, "packages": [], "errors": []}, f)
        html = self.b.c.get("/system/updates").get_data(as_text=True)
        self.assertIn("Installer 9.1.0", html)
        self.assertIn("Au programme", html)                                   # aperçu dans la carte loco
        self.assertIn("Grande nouveauté.", html)
        self.assertIn('id="nouveautes"', html)
        self.assertIn('class="tl-item is-new"', html)
        self.assertIn("<code>loco-update</code>", html)                       # code en ligne rendu, échappé
        self.assertIn('class="tl-item is-current"', html)                     # la version installée, depuis le journal livré
        self.assertIn("Aucune mise à jour en attente.", html)                 # système à jour

    def test_security_fixes_are_listed_by_severity(self):
        pkgs = [{"name": n, "current": "1", "new": "2", "origin": "main", "security": True, "severity": s, "tags": []}
                for n, s in (("low-pkg", "Low"), ("crit-pkg", "Critical"), ("med-pkg", "Medium"), ("high-pkg", "High"))]
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-15 09:00:00", "security_supported": True, "packages": pkgs, "errors": []}, f)
        html = self.b.c.get("/system/updates").get_data(as_text=True)
        box = html[html.index("À corriger en priorité"):]
        self.assertLess(box.index("crit-pkg"), box.index("high-pkg"))
        self.assertLess(box.index("high-pkg"), box.index("med-pkg"))
        self.assertNotIn("low-pkg", box[:box.index("</ul>")])                  # trois au plus dans l'encart
        self.assertIn("4 correctifs de sécurité", html)

    def test_running_operation_shows_a_progress_bar(self):
        job = {"id": "20261027-150000-abcdef", "action": "update-loco", "state": "running", "started": "2026-10-27 15:00:00"}
        with mock.patch.object(locoadmin, "running_job", return_value=job), \
             mock.patch.object(locoadmin, "jobs", return_value=[job]), \
             mock.patch.object(locoadmin, "job_log", return_value="\x1b[1;34m==>\x1b[0m Copie du portail dans /opt/loco\n"):
            html = self.b.c.get("/system/updates").get_data(as_text=True)
            data = self.b.c.get("/system/updates/operation/" + job["id"]).get_json()
        self.assertIn("data-job-progress=", html)
        self.assertIn("Copie du portail dans /opt/loco", html)
        self.assertIn('aria-valuenow="30"', html)
        self.assertEqual(data["progress"]["step"], "Copie du portail dans /opt/loco")
        self.assertNotIn("data-job-progress", self.b.c.get("/system/updates").get_data(as_text=True))   # plus rien en cours

    def test_cached_status_from_before_structured_messages_remains_readable(self):
        reason = "redémarrage demandé par libc6"
        error = "Version publiée de loco introuvable : <miroir indisponible>"
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-21 06:00:00", "reboot_required": True,
                       "reboot_reasons": [reason], "errors": [error]}, f)
        response = self.b.c.get("/system/updates")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(reason, html)
        self.assertIn("&lt;miroir indisponible&gt;", html)
        self.assertNotIn("<miroir indisponible>", html)
        self.assertEqual(locoadmin._error_log(error), error)

    def test_settings_are_sent_to_the_agent(self):
        form = {k: v for k, v in locoadmin.read_conf().items() if k in locoadmin.SETTINGS}
        form.update(WU_CACHE_SIZE="60g", ADMIN_ALLOW="127.0.0.0/8 192.168.43.0/24")
        r = self.b.post("/system/configuration", form, page="/system/configuration")
        self.assertEqual(r.status_code, 302)
        names = os.listdir(locoadmin.REQUESTS)
        self.assertEqual(len(names), 1)
        req = locoadmin._read_request(os.path.join(locoadmin.REQUESTS, names[0]))
        self.assertEqual(req["params"], {"WU_CACHE_SIZE": "60g", "ADMIN_ALLOW": "127.0.0.0/8 192.168.43.0/24"})

    def test_admin_cannot_lock_themself_out(self):
        form = {k: v for k, v in locoadmin.read_conf().items() if k in locoadmin.SETTINGS}
        form.update(ADMIN_ALLOW="10.99.0.0/16", csrf=self.b.token("/system/configuration"))
        self.b.c.post("/system/configuration", data=form, headers={"X-Forwarded-For": "192.168.43.5"})
        self.assertEqual(os.listdir(locoadmin.REQUESTS), [])
        form["ADMIN_ALLOW"] = "192.168.43.0/24 10.99.0.0/16"
        self.b.c.post("/system/configuration", data=form, headers={"X-Forwarded-For": "192.168.43.5"})
        self.assertEqual(len(os.listdir(locoadmin.REQUESTS)), 1)

    def test_new_operations_require_admin_and_csrf(self):
        for path in ("/system/configuration", "/system/updates"):
            self.assertEqual(self.b.c.post(path, data={"action": "reboot"}).status_code, 400)
        with fixture.connect() as conn:
            conn.execute("DELETE FROM users WHERE username='appliance-reader'")
            fixture.auth.create_user(conn, "appliance-reader", fixture.ADMIN_PW, role="viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("appliance-reader", fixture.ADMIN_PW)
        for path in ("/system/configuration", "/system/updates", "/system/operations/state"):
            self.assertEqual(viewer.c.get(path).status_code, 403)
        self.assertEqual(os.listdir(locoadmin.REQUESTS), [])

    def test_debian_server_lists_security_updates(self):
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-20 06:00:00", "os": "Debian GNU/Linux 13 (trixie)", "container": False,
                       "kernel": {"running": "6.12.48+deb13-amd64", "installed": "6.12.57+deb13-amd64"},
                       "security_supported": True, "reboot_required": True,
                       "reboot_reasons": [{"code": "kernel-newer", "installed": "6.12.57+deb13-amd64", "running": "6.12.48+deb13-amd64"}],
                       "stale_services": {"ssh.service": ["sshd"]}, "security_count": 1,
                       "packages": [{"name": "libssl3t64", "current": "3.5.1-1", "new": "3.5.4-1~deb13u1",
                                     "origin": "Debian-Security:13/stable-security", "security": True, "severity": "",
                                     "tags": ["socle"]},
                                    {"name": "squid", "current": "6.13-2", "new": "6.13-2+deb13u1", "origin": "",
                                     "security": False, "severity": "", "tags": ["loco"]}],
                       "errors": []}, f)
        with mock.patch.object(config, "APPLIANCE", False):
            html = self.b.c.get("/system/updates").get_data(as_text=True)
            self.assertIn("Debian GNU/Linux 13 (trixie)", html)
            self.assertIn("Installer les correctifs de sécurité", html)
            self.assertIn('data-security="1"', html)
            self.assertIn("6.12.57+deb13-amd64", html)
            self.assertIn("ssh.service", html)
            self.assertNotIn("/system/configuration", html)           # réglages : appliance seulement
            self.assertIn("ShaNouhAr/loco", html)                      # dépôt GitHub par défaut, modifiable
            self.assertIn('value="set-repo"', html)
            with mock.patch.object(locoadmin, "ALPINE", False):
                r = self.b.post("/system/updates", {"action": "update-security"}, page="/system/updates")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self.b.c.get("/system/configuration").status_code, 404)
        names = os.listdir(locoadmin.REQUESTS)
        self.assertEqual(len(names), 1)
        self.assertEqual(locoadmin._read_request(os.path.join(locoadmin.REQUESTS, names[0]))["action"], "update-security")

    def test_viewers_and_other_runtimes_have_no_access(self):
        with mock.patch.object(config, "SERVER_UPDATES", False):
            self.assertEqual(self.b.c.get("/system/updates").status_code, 404)
        anonymous = fixture.Browser()
        self.assertEqual(anonymous.c.get("/system/configuration").status_code, 302)


class TestReleaseChangelog(TempAgent):
    """Nouveautés de la version disponible : rapatriées à la vérification, seules les versions plus récentes gardées."""

    TEXT = ("# Journal\n\n## 2.3.0 — 2026-11-02\n\n- **Trois.** avec `code`.\n\n## 2.2.0 — 2026-11-01\n\n- **Deux.** b\n\n"
            "## 2.1.0 — 2026-10-30\n\n- **Un.** a\n")
    STATE = {"os": "Alpine Linux 3.24.1", "package_manager": "apk", "container": False,
             "kernel": {"running": "6.12.57-0-lts", "installed": ""}, "security_supported": False,
             "reboot_required": False, "reboot_reasons": [], "stale_services": {}}

    def test_only_sections_newer_than_installed_are_kept(self):
        kept = locoadmin.newer_sections(self.TEXT, "2.1.0")
        self.assertIn("## 2.3.0", kept)
        self.assertIn("## 2.2.0", kept)
        self.assertNotIn("## 2.1.0", kept)
        self.assertLess(kept.index("2.3.0"), kept.index("2.2.0"))            # ordre du fichier conservé
        self.assertEqual(locoadmin.newer_sections(self.TEXT, "2.3.0"), "")
        self.assertEqual(locoadmin.newer_sections("## Notes\n\n- rien\n", "1.0"), "")   # titre qui n'est pas une version

    def check(self, changelog):
        latest = {"version": "2.3.0", "tag": "v2.3.0", "url": "", "published": "", "notes": "", "archive": "", "sums": ""}
        with mock.patch.object(locoadmin, "latest_release", return_value=latest), \
             mock.patch.object(locoadmin, "release_changelog", changelog), \
             mock.patch.object(locoadmin, "current_version", return_value="2.1.0"), \
             mock.patch.object(locoadmin, "system_state", return_value=dict(self.STATE)), \
             mock.patch.object(locoadmin, "pending_packages", return_value=[]):
            locoadmin.check(log=lambda _t: None)
        return locoadmin.read_status()

    def test_check_keeps_the_news_of_the_available_version(self):
        status = self.check(lambda release: self.TEXT)
        self.assertIn("## 2.3.0", status["latest"]["changelog"])
        self.assertNotIn("## 2.1.0", status["latest"]["changelog"])
        self.assertEqual(status["errors"], [])

    def test_unreachable_changelog_does_not_fail_the_check(self):
        def broken(release):
            raise OSError("introuvable")
        status = self.check(broken)
        self.assertEqual(status["latest"]["version"], "2.3.0")
        self.assertNotIn("changelog", status["latest"])
        self.assertEqual(status["errors"], [])

    def test_github_and_gitlab_requests(self):
        urls = []

        def fake_get(url, timeout=15, accept=""):
            urls.append((url, accept))
            return FakeResponse(b"## 2.3.0\n")
        with mock.patch.object(locoadmin, "_get", fake_get):
            with mock.patch.object(locoadmin, "source", return_value="ShaNouhAr/loco"):
                locoadmin.release_changelog({"version": "2.3.0", "tag": "v2.3.0"})
            with mock.patch.object(locoadmin, "source", return_value="https://gitlab.exemple/infra/loco"):
                locoadmin.release_changelog({"version": "2.3.0", "tag": "v2.3.0"})
        self.assertEqual(urls[0], ("https://api.github.com/repos/ShaNouhAr/loco/contents/CHANGELOG.md?ref=v2.3.0",
                                   "application/vnd.github.raw+json"))
        self.assertEqual(urls[1][0], "https://gitlab.exemple/api/v4/projects/infra%2Floco/repository/files/CHANGELOG.md/raw?ref=v2.3.0")


class TestChangelog(unittest.TestCase):
    """Journal des versions lu depuis CHANGELOG.md et exposé à la page de mise à jour."""

    def _use(self, text):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        self.addCleanup(appliance._CHANGELOG.update, mtime=None, entries=[])
        appliance._CHANGELOG["mtime"] = None                        # forcer la relecture
        patch = mock.patch.object(config, "CHANGELOG_PATH", path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_parses_versions_bold_and_subitems(self):
        self._use("# Journal\n\n## 2.80.0 — 2026-11-01\n\n- **Titre.** du corps.\n  - un sous-point.\n\n## 2.79.0 — 2026-10-28\n\n- autre chose.\n")
        cl = appliance.changelog()
        self.assertEqual([e["version"] for e in cl], ["2.80.0", "2.79.0"])
        self.assertEqual(cl[0]["date"], "2026-11-01")
        self.assertEqual(cl[0]["notes"][0]["parts"][0], {"text": "Titre.", "b": True})
        self.assertFalse(cl[0]["notes"][0]["sub"])
        self.assertTrue(cl[0]["notes"][1]["sub"])                   # « - » indenté de deux espaces

    def test_missing_file_gives_no_entries(self):
        self.addCleanup(appliance._CHANGELOG.update, mtime=None, entries=[])
        with mock.patch.object(config, "CHANGELOG_PATH", "/pas-de-fichier/CHANGELOG.md"):
            appliance._CHANGELOG["mtime"] = None
            self.assertEqual(appliance.changelog(), [])


if __name__ == "__main__":
    unittest.main()
