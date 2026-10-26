"""Heure du serveur (agent root locoadmin) : synchronisation NTP par timesyncd (Debian) ou chrony,
avec gestion OpenRC sur l'appliance Alpine (pas de systemctl)."""
import sys
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
             mock.patch.object(locoadmin, "_run", side_effect=lambda cmd, log, **k: cmds.append(cmd) or 0), \
             mock.patch.object(locoadmin, "_write_conf_file", side_effect=lambda p, c: files.__setitem__(p, c)), \
             mock.patch.object(locoadmin.shutil, "which", side_effect=lambda n: which.get(n)), \
             mock.patch.object(locoadmin.os.path, "isdir", return_value=True), \
             mock.patch.object(locoadmin.os.path, "exists", return_value=False):
            locoadmin.set_time({"ntp": "1" if enabled else "", "ntp_server": server}, log=lambda *_: None)
        return cmds, files

    def test_debian_uses_timesyncd_and_systemctl(self):
        cmds, files = self._apply(backend="timesyncd", has_systemctl=True, has_timedatectl=True)
        self.assertEqual(files, {"/etc/systemd/timesyncd.conf.d/loco.conf": "# Géré par loco\n[Time]\nNTP=10.0.0.5\n"})
        self.assertIn(["timedatectl", "set-ntp", "true"], cmds)
        self.assertIn(["systemctl", "restart", "systemd-timesyncd"], cmds)

    def test_alpine_chrony_uses_rc_service_not_systemctl(self):
        cmds, files = self._apply(backend="chrony", has_systemctl=False, has_timedatectl=False)
        self.assertIn("server 10.0.0.5 iburst", next(iter(files.values())))
        self.assertIn(["rc-service", "chronyd", "restart"], cmds)       # OpenRC
        self.assertFalse(any(c[:1] == ["systemctl"] for c in cmds))     # jamais systemctl sur Alpine
        self.assertFalse(any(c[:1] == ["timedatectl"] for c in cmds))   # absent sur Alpine

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


if __name__ == "__main__":
    unittest.main()
