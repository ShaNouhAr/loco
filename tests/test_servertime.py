"""Heure du serveur (agent root locoadmin) : synchronisation NTP par timesyncd (Debian) ou chrony, plusieurs
serveurs possibles, « include » pour les distributions sans confdir (Rocky/Alma), réactivation du port de commande
chrony sur l'appliance Alpine (cmdport 0) et persistance OpenRC."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, "portal")
import locoadmin


class SetNtpTest(unittest.TestCase):
    def _apply(self, *, backend, has_systemctl, has_timedatectl, container=False, enabled=True, server="10.0.0.5"):
        """Lance set_time() en simulant une plateforme ; renvoie (commandes lancées, fichiers écrits)."""
        cmds, files = [], {}
        which = {"systemctl": "/usr/bin/systemctl" if has_systemctl else None,
                 "timedatectl": "/usr/bin/timedatectl" if has_timedatectl else None,
                 "chronyd": "/usr/sbin/chronyd"}
        with mock.patch.object(locoadmin, "_is_container", return_value=container), \
             mock.patch.object(locoadmin, "_ntp_backend", return_value=backend), \
             mock.patch.object(locoadmin, "_chrony_conf", return_value=""), \
             mock.patch.object(locoadmin, "write_time_status"), \
             mock.patch.object(locoadmin, "_run", side_effect=lambda cmd, log, **k: cmds.append(cmd) or 0), \
             mock.patch.object(locoadmin, "_write_conf_file", side_effect=lambda p, c: files.__setitem__(p, c)), \
             mock.patch.object(locoadmin.shutil, "which", side_effect=lambda n: which.get(n)), \
             mock.patch.object(locoadmin.os.path, "exists", return_value=False):
            locoadmin.set_time({"ntp": "1" if enabled else "", "ntp_server": server}, log=lambda *_: None)
        return cmds, files

    def test_debian_uses_timesyncd_and_systemctl(self):
        cmds, files = self._apply(backend="timesyncd", has_systemctl=True, has_timedatectl=True)
        self.assertEqual(files, {"/etc/systemd/timesyncd.conf.d/loco.conf": "# Géré par loco\n[Time]\nNTP=10.0.0.5\n"})
        self.assertIn(["timedatectl", "set-ntp", "true"], cmds)
        self.assertIn(["systemctl", "restart", "systemd-timesyncd"], cmds)

    def test_timesyncd_lists_every_server(self):
        _, files = self._apply(backend="timesyncd", has_systemctl=True, has_timedatectl=True,
                               server="a.pool.ntp.org, b.pool.ntp.org; 10.0.0.5")
        self.assertEqual(files["/etc/systemd/timesyncd.conf.d/loco.conf"],
                         "# Géré par loco\n[Time]\nNTP=a.pool.ntp.org b.pool.ntp.org 10.0.0.5\n")

    def test_chrony_writes_every_server_with_prefer(self):
        _, files = self._apply(backend="chrony", has_systemctl=True, has_timedatectl=True,
                               server="a.pool.ntp.org b.pool.ntp.org 10.0.0.5")
        conf = next(iter(files.values()))
        self.assertIn("server a.pool.ntp.org iburst prefer", conf)      # les serveurs saisis l'emportent (prefer)
        self.assertIn("server 10.0.0.5 iburst prefer", conf)
        self.assertEqual(conf.count("server "), 3)                       # tous les serveurs, dédupliqués

    def test_alpine_chrony_uses_rc_service_not_systemctl(self):
        cmds, files = self._apply(backend="chrony", has_systemctl=False, has_timedatectl=False)
        self.assertIn("server 10.0.0.5 iburst prefer", next(iter(files.values())))
        self.assertIn(["rc-service", "chronyd", "restart"], cmds)       # OpenRC
        self.assertFalse(any(c[:1] == ["systemctl"] for c in cmds))     # jamais systemctl sur Alpine
        self.assertFalse(any(c[:1] == ["timedatectl"] for c in cmds))   # absent sur Alpine

    def test_openrc_persists_the_choice_across_reboots(self):
        cmds, _ = self._apply(backend="chrony", has_systemctl=False, has_timedatectl=False)
        self.assertIn(["rc-update", "add", "chronyd", "default"], cmds)  # démarré au boot
        cmds, _ = self._apply(backend="chrony", has_systemctl=False, has_timedatectl=False, enabled=False)
        self.assertIn(["rc-update", "del", "chronyd", "default"], cmds)  # retiré du boot

    def test_systemd_chrony_uses_systemctl(self):
        cmds, _ = self._apply(backend="chrony", has_systemctl=True, has_timedatectl=True)
        self.assertTrue(any(c[:2] == ["systemctl", "restart"] for c in cmds))
        self.assertFalse(any(c[:1] == ["rc-service"] for c in cmds))

    def test_disable_stops_the_service(self):
        cmds, _ = self._apply(backend="chrony", has_systemctl=False, has_timedatectl=False, enabled=False)
        self.assertIn(["rc-service", "chronyd", "stop"], cmds)

    def test_container_skips_ntp(self):
        cmds, files = self._apply(backend="timesyncd", has_systemctl=True, has_timedatectl=True, container=True)
        self.assertEqual(cmds, [])
        self.assertEqual(files, {})

    def test_invalid_server_is_rejected(self):
        with self.assertRaises(ValueError):
            locoadmin.set_time({"ntp": "1", "ntp_server": "bad server!"}, log=lambda *_: None)

    def test_rejects_more_than_four_servers(self):
        with self.assertRaises(ValueError):
            locoadmin.ntp_servers("a b c d e")

    def test_parses_ipv6_and_dedupes(self):
        self.assertEqual(locoadmin.ntp_servers("[2001:db8::1] 2001:db8::1 Pool.NTP.org"),
                         ["2001:db8::1", "pool.ntp.org"])


class ChronyIncludeTest(unittest.TestCase):
    """Rocky/AlmaLinux : chrony.conf n'a pas de confdir, loco y ajoute un « include » une seule fois ; avec confdir,
    le fichier de loco est déposé dans le dossier et aucun include n'est nécessaire."""

    def test_adds_include_once_and_writes_servers(self):
        d = tempfile.mkdtemp()
        conf = os.path.join(d, "chrony.conf")
        with open(conf, "w") as f:
            f.write("pool 2.rocky.pool.ntp.org iburst\ndriftfile /var/lib/chrony/drift\n")
        with mock.patch.object(locoadmin, "_is_container", return_value=False), \
             mock.patch.object(locoadmin, "_ntp_backend", return_value="chrony"), \
             mock.patch.object(locoadmin, "_chrony_conf", return_value=conf), \
             mock.patch.object(locoadmin, "write_time_status"), \
             mock.patch.object(locoadmin, "_run", return_value=0), \
             mock.patch.object(locoadmin.shutil, "which", return_value="/usr/bin/x"), \
             mock.patch.object(locoadmin.os.path, "exists", return_value=False):
            locoadmin.set_time({"ntp": "1", "ntp_server": "10.0.0.9"}, log=lambda *_: None)
            dropin = os.path.join(d, "chrony.loco.conf")
            self.assertIn("include " + dropin, pathlib.Path(conf).read_text())
            self.assertIn("server 10.0.0.9 iburst prefer", pathlib.Path(dropin).read_text())
            locoadmin.set_time({"ntp": "1", "ntp_server": "10.0.0.9"}, log=lambda *_: None)
            self.assertEqual(pathlib.Path(conf).read_text().count("include " + dropin), 1)  # idempotent

    def test_confdir_is_used_when_present(self):
        d = tempfile.mkdtemp()
        confd = os.path.join(d, "conf.d")
        os.makedirs(confd)
        conf = os.path.join(d, "chrony.conf")
        with open(conf, "w") as f:
            f.write("confdir " + confd + "\n")
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            dropin, needs_include = locoadmin._chrony_dropin()
        self.assertEqual(dropin, os.path.join(confd, "loco.conf"))
        self.assertEqual(needs_include, "")                                          # déjà lu via confdir


class ChronyCmdPortTest(unittest.TestCase):
    """Appliance Alpine : chrony.conf a « cmdport 0 », qui coupe l'accès de chronyc même pour root. loco doit
    réactiver le port de commande (localhost) pour pouvoir lire l'état de l'heure."""

    def _conf(self, text):
        d = tempfile.mkdtemp()
        conf = os.path.join(d, "chrony.conf")
        with open(conf, "w") as f:
            f.write(text)
        return d, conf

    def test_cmdport_is_reenabled_when_disabled(self):
        d, conf = self._conf("pool pool.ntp.org iburst\ncmdport 0\n")
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            locoadmin._write_chrony(["10.0.0.5"], log=lambda *_: None)
        dropin = pathlib.Path(os.path.join(d, "chrony.loco.conf")).read_text()
        self.assertIn("cmdport 323", dropin)                          # port de commande réactivé (localhost)
        self.assertIn("server 10.0.0.5 iburst prefer", dropin)
        self.assertIn("include " + os.path.join(d, "chrony.loco.conf"), pathlib.Path(conf).read_text())

    def test_cmdport_untouched_when_already_open(self):
        d, conf = self._conf("pool pool.ntp.org iburst\n")            # pas de cmdport 0
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            locoadmin._write_chrony(["10.0.0.5"], log=lambda *_: None)
        self.assertNotIn("cmdport", pathlib.Path(os.path.join(d, "chrony.loco.conf")).read_text())

    def test_self_heal_reloads_only_when_blocked_and_unreachable(self):
        d, conf = self._conf("cmdport 0\n")
        base = dict(_is_container=lambda: False, _ntp_backend=lambda: "chrony",
                    _chrony_conf=lambda: conf, _configured_ntp=lambda: [])
        cmds = []
        with mock.patch.multiple(locoadmin, **base), \
             mock.patch.object(locoadmin, "_quiet", return_value=(1, "")), \
             mock.patch.object(locoadmin, "_run", side_effect=lambda c, l, **k: cmds.append(c) or 0), \
             mock.patch.object(locoadmin.shutil, "which", return_value=None), \
             mock.patch.object(locoadmin.os.path, "exists", return_value=False):
            locoadmin._ensure_chrony_readable()                       # injoignable + cmdport 0 → réactive et recharge
        self.assertIn(["rc-service", "chronyd", "restart"], cmds)
        self.assertIn("cmdport 323", pathlib.Path(os.path.join(d, "chrony.loco.conf")).read_text())
        cmds = []
        with mock.patch.multiple(locoadmin, **base), \
             mock.patch.object(locoadmin, "_quiet", return_value=(0, "x")), \
             mock.patch.object(locoadmin, "_run", side_effect=lambda c, l, **k: cmds.append(c) or 0):
            locoadmin._ensure_chrony_readable()                       # déjà joignable → ne touche à rien
        self.assertEqual(cmds, [])


if __name__ == "__main__":
    unittest.main()
