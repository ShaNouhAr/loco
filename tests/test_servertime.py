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


class ChronyAlpine324Test(unittest.TestCase):
    """Alpine 3.24 : « confdir » en tête de chrony.conf puis « cmdport 0 » plus bas. Un fichier déposé dans confdir est
    lu AVANT cmdport 0, qui l'emporterait : loco doit s'inclure en fin de fichier et retirer l'ancien fichier."""

    def test_included_last_and_stale_confdir_file_removed(self):
        d = tempfile.mkdtemp()
        confd = os.path.join(d, "conf.d")
        os.makedirs(confd)
        conf = os.path.join(d, "chrony.conf")
        with open(conf, "w") as f:
            f.write(f"confdir {confd}\npool pool.ntp.org iburst\nmakestep 1.0 3\ncmdport 0\n")
        with open(os.path.join(confd, "loco.conf"), "w") as f:
            f.write("# Géré par loco : 2.68\ncmdport 323\n")
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            locoadmin._write_chrony(["10.0.0.5"], log=lambda *_: None)
        dropin = os.path.join(d, "chrony.loco.conf")
        text = pathlib.Path(conf).read_text()
        self.assertTrue(text.rstrip().endswith("include " + dropin))           # dernier mot sur cmdport 0
        self.assertIn("cmdport 323", pathlib.Path(dropin).read_text())
        self.assertFalse(os.path.exists(os.path.join(confd, "loco.conf")))   # reste de la 2.68 retiré

    def test_confdir_kept_when_nothing_overrides_it(self):
        d = tempfile.mkdtemp()
        conf = os.path.join(d, "chrony.conf")
        with open(conf, "w") as f:
            f.write("cmdport 0\nconfdir /etc/chrony/conf.d\n")                 # cmdport 0 AVANT confdir : sans effet
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            self.assertEqual(locoadmin._chrony_dropin(), ("/etc/chrony/conf.d/loco.conf", ""))


class NtpOnlyTest(unittest.TestCase):
    """« Utiliser uniquement ces serveurs » : sources de la distribution commentées (réversible), et état de chaque
    serveur saisi pour signaler qu'il ne répond pas (l'heure venant alors des serveurs de secours)."""

    def test_distribution_sources_commented_then_restored(self):
        d = tempfile.mkdtemp()
        conf = os.path.join(d, "chrony.conf")
        original = "confdir /etc/chrony/conf.d\npool pool.ntp.org iburst\nsourcedir /etc/chrony/sources.d\nmakestep 1.0 3\n"
        with open(conf, "w") as f:
            f.write(original)
        with mock.patch.object(locoadmin, "_chrony_conf", return_value=conf):
            locoadmin._chrony_only(True, log=lambda *_: None)
            text = pathlib.Path(conf).read_text()
            self.assertIn("#loco-ntp-only# pool pool.ntp.org iburst", text)
            self.assertIn("#loco-ntp-only# sourcedir", text)
            self.assertIn("\nmakestep 1.0 3", text)                         # le reste est intact
            with mock.patch.object(locoadmin, "_ntp_backend", return_value="chrony"):
                self.assertTrue(locoadmin._ntp_only_active())
            locoadmin._chrony_only(True, log=lambda *_: None)                 # idempotent
            self.assertEqual(pathlib.Path(conf).read_text().count("#loco-ntp-only#"), 2)
            locoadmin._chrony_only(False, log=lambda *_: None)
        self.assertEqual(pathlib.Path(conf).read_text(), original)

    def test_timesyncd_disables_the_fallback(self):
        files = {}
        with mock.patch.object(locoadmin, "_is_container", return_value=False), \
             mock.patch.object(locoadmin, "_ntp_backend", return_value="timesyncd"), \
             mock.patch.object(locoadmin, "write_time_status"), \
             mock.patch.object(locoadmin, "_run", return_value=0), \
             mock.patch.object(locoadmin, "_write_conf_file", side_effect=lambda p, c: files.__setitem__(p, c)):
            locoadmin.set_time({"ntp": "1", "ntp_server": "10.0.0.5", "ntp_only": "1"}, log=lambda *_: None)
        self.assertIn("FallbackNTP=\n", files[locoadmin.TIMESYNCD_DROPIN])

    def test_only_is_ignored_without_a_server(self):
        calls = []
        with mock.patch.object(locoadmin, "_is_container", return_value=False), \
             mock.patch.object(locoadmin, "_ntp_backend", return_value="chrony"), \
             mock.patch.object(locoadmin, "_write_chrony"), \
             mock.patch.object(locoadmin, "_chrony_only", side_effect=lambda on, log: calls.append(on)), \
             mock.patch.object(locoadmin, "write_time_status"), \
             mock.patch.object(locoadmin, "_run", return_value=0), \
             mock.patch.object(locoadmin.shutil, "which", return_value=None):
            locoadmin.set_time({"ntp": "1", "ntp_server": "", "ntp_only": "1"}, log=lambda *_: None)
        self.assertEqual(calls, [False])                                    # jamais sans aucune source

    def test_status_relates_each_configured_server_to_its_state(self):
        out = {("chronyc", "-n", "-c", "tracking"): "A29FC87B,162.159.200.123,4,1,0.0001,0,0,0,0,0,0,0,2,Normal\n",
               ("chronyc", "-n", "-c", "sources"): "^,?,192.0.2.1,0,6,0,-,0,0,0\n^,*,162.159.200.123,3,6,377,1,0,0,0\n",
               ("chronyc", "-N", "-c", "sources"): "^,?,192.0.2.1,0,6,0,-,0,0,0\n^,*,pool.ntp.org,3,6,377,1,0,0,0\n"}
        with mock.patch.object(locoadmin, "_is_container", return_value=False), \
             mock.patch.object(locoadmin, "_ntp_backend", return_value="chrony"), \
             mock.patch.object(locoadmin, "_configured_ntp", return_value=["192.0.2.1"]), \
             mock.patch.object(locoadmin, "_ntp_only_active", return_value=False), \
             mock.patch.object(locoadmin, "_quiet", side_effect=lambda cmd: (0, out.get(tuple(cmd), ""))):
            st = locoadmin.time_status()
        self.assertTrue(st["synchronized"])
        self.assertEqual(st["configured"], [{"name": "192.0.2.1", "state": "unreachable"}])
        self.assertEqual(st["sources"][1]["name"], "pool.ntp.org")


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
