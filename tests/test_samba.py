"""Partages de fichiers (Samba) : partitions proposées, description revalidée par l'agent root, configuration écrite,
mot de passe remis à smbpasswd par l'entrée standard seulement, partage plein passé en lecture seule, taille vue par
les postes (dfree) ; puis le portail — fonctionnalité facultative, droits, partages créés, modifiés et retirés,
mot de passe de partage choisi dans le compte, comptes supprimés retirés de Samba, resynchronisation de fond.

L'agent root est simulé : ses commandes (useradd, smbpasswd, testparm, du…) sont interceptées, ses dossiers d'échange
sont des dossiers temporaires. Le reste est le vrai code de loco."""
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

import test_portal as fixture
import locosamba
from loco import features, samba
from loco.db import transaction

GIB = 1 << 30
SECRET = "Partage-Secret-2026"
MOUNTINFO = """\
22 1 8:3 / / rw,relatime shared:1 - ext4 /dev/sda3 rw
23 22 8:1 / /boot rw,relatime shared:2 - ext4 /dev/sda1 rw
24 22 8:17 / /mnt/data rw,noatime shared:3 - xfs /dev/sdb1 rw
25 22 8:33 / /media/usb rw,relatime shared:4 - vfat /dev/sdc1 rw
26 22 8:49 / /srv/archive ro,relatime shared:5 - ext4 /dev/sdd1 ro
27 22 8:17 /films /mnt/films rw,noatime shared:3 - xfs /dev/sdb1 rw
28 22 0:31 / /var/lib/docker/overlay2/x/merged rw - overlay overlay rw
29 22 0:32 / /mnt/mes\\040disques rw,relatime shared:6 - btrfs /dev/sde1 rw
"""


class FakeStatvfs:
    f_frsize, f_blocks, f_bfree, f_bavail = 4096, 1000 * 262144, 600 * 262144, 550 * 262144    # 1000 Gio, 550 libres


class TestPartitions(unittest.TestCase):
    def test_only_writable_unix_filesystems_outside_system_folders(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(MOUNTINFO)
        self.addCleanup(os.unlink, f.name)
        with mock.patch.object(locosamba.os, "statvfs", lambda path: FakeStatvfs()):
            parts = locosamba.partitions(f.name)
        self.assertEqual([p["mount"] for p in parts], ["/", "/mnt/data", "/mnt/mes disques"])
        root = parts[0]
        self.assertEqual((root["device"], root["fstype"], root["base"]), ("/dev/sda3", "ext4", "/srv/partages"))
        self.assertEqual((root["size"], root["free"], root["used"]), (1000 * GIB, 550 * GIB, 400 * GIB))
        self.assertEqual(parts[1]["base"], "/mnt/data/partages")


class TestDescription(unittest.TestCase):
    PARTS = [{"mount": "/"}, {"mount": "/mnt/data"}]

    def check(self, shares, users=None):
        users = users if users is not None else [{"unix": "lsmb1", "login": "admin"}, {"unix": "lsmb2", "login": "marie"}]
        return locosamba.check_spec({"users": users, "shares": shares}, self.PARTS)

    def share(self, **values):
        base = {"name": "Documents", "mount": "/", "folder": "documents", "limit": 10 * GIB, "read": [], "write": ["lsmb1"]}
        base.update(values)
        return base

    def test_a_valid_share(self):
        spec = self.check([self.share(read=["lsmb2"], comment="Équipe %h\nsecrète\\")])
        share = spec["shares"][0]
        self.assertEqual(spec["users"], {"lsmb1": "admin", "lsmb2": "marie"})
        self.assertEqual((share["path"], share["read"], share["write"], share["error"]),
                         ("/srv/partages/documents", ["lsmb2"], ["lsmb1"], ""))
        self.assertEqual(share["comment"], "Équipe hsecrète")          # ni « % » (substitutions de Samba), ni contrôle

    def test_what_the_agent_refuses(self):
        spec = self.check([self.share(name="homes"), self.share(name="IPC$"), self.share(name="a b"),
                           self.share(name="Docs", folder="../etc"), self.share(name="Petit", limit=GIB // 2),
                           self.share(name="Ok", folder="ok"), self.share(name="OK", folder="ok2"),
                           self.share(name="Ailleurs", folder="ailleurs", mount="/mnt/absent")])
        self.assertEqual([s["name"] for s in spec["shares"]], ["Ok", "Ailleurs"])          # « OK » : même nom, casse ignorée
        self.assertIn("plus montée", spec["shares"][1]["error"])

    def test_accounts_are_only_loco_ones_and_writers_win(self):
        spec = self.check([self.share(read=["lsmb1", "root", "lsmb9"], write=["lsmb1", "lsmb2"])],
                          users=[{"unix": "lsmb1", "login": "admin"}, {"unix": "lsmb2", "login": "marie"},
                                 {"unix": "root", "login": "root"}, {"unix": "lsmb3", "login": "ADMIN"},
                                 {"unix": "lsmb4", "login": "x\ny"}])
        self.assertEqual(spec["users"], {"lsmb1": "admin", "lsmb2": "marie"})
        self.assertEqual((spec["shares"][0]["read"], spec["shares"][0]["write"]), ([], ["lsmb1", "lsmb2"]))


class TestConfiguration(unittest.TestCase):
    def spec(self):
        return locosamba.check_spec({"users": [{"unix": "lsmb1", "login": "admin"}, {"unix": "lsmb2", "login": "marie"}],
                                     "shares": [{"name": "Documents", "mount": "/", "folder": "documents", "limit": 10 * GIB,
                                                 "read": ["lsmb2"], "write": ["lsmb1"]},
                                                {"name": "Vide", "mount": "/", "folder": "vide", "limit": GIB},
                                                {"name": "Perdu", "mount": "/absent", "folder": "perdu", "limit": GIB}]},
                                    [{"mount": "/"}])

    def test_server_and_shares(self):
        text = locosamba.render(self.spec(), networks=["192.168.42.0/24"])
        self.assertTrue(text.startswith(locosamba.MARK + "\n"))
        for line in ("map to guest = never", "restrict anonymous = 2", "server min protocol = SMB2_10", "disable netbios = yes",
                     "load printers = no", "hosts allow = 127.0.0.1 ::1 192.168.42.0/24", "username map = /etc/samba/loco-users.map"):
            self.assertIn("   " + line + "\n", text)
        docs = text.split("[Documents]")[1].split("\n\n")[0]
        for line in ("path = /srv/partages/documents", "valid users = lsmb2 lsmb1", "read only = yes", "write list = lsmb1",
                     "force user = loco-share", "access based share enum = yes"):
            self.assertIn(line, docs)
        self.assertIn("available = no", text.split("[Vide]")[1])                   # sans compte : personne
        self.assertNotIn("[Perdu]", text)                                          # partition démontée : pas servi
        self.assertNotIn("guest ok = yes", text)
        self.assertIn(f"   dfree command = {locosamba.DFREE}\n", text)              # un programme seul, sans arguments

    def test_a_full_share_is_read_only_for_everyone(self):
        text = locosamba.render(self.spec(), full={"Documents"})
        self.assertNotIn("write list", text.split("[Documents]")[1].split("\n\n")[0])

    def test_a_disabled_account_is_left_out_of_every_share(self):
        spec = locosamba.check_spec({"users": [{"unix": "lsmb1", "login": "admin"}, {"unix": "lsmb2", "login": "scanner", "disabled": True}],
                                     "shares": [{"name": "Docs", "mount": "/", "folder": "docs", "limit": GIB,
                                                 "read": ["lsmb2"], "write": ["lsmb1"]}]}, [{"mount": "/"}])
        self.assertEqual((spec["users"], spec["disabled"]), ({"lsmb1": "admin", "lsmb2": "scanner"}, ["lsmb2"]))
        text = locosamba.render(spec)
        self.assertIn("valid users = lsmb1\n", text)
        self.assertIn("lsmb2 = scanner", locosamba.render_map(spec))

    def test_user_map(self):
        self.assertEqual(locosamba.render_map(self.spec()), locosamba.MARK + "\nlsmb1 = admin\nlsmb2 = marie\n")


class TestAccounts(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patch = mock.patch.object(locosamba, "_run", self.fake_run)
        patch.start()
        self.addCleanup(patch.stop)
        for name, value in (("_exists", lambda name: False), ("_nologin", lambda: "/sbin/nologin")):
            patch = mock.patch.object(locosamba, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(locosamba.grp, "getgrnam", lambda name: None)
        patch.start()
        self.addCleanup(patch.stop)

    def fake_run(self, cmd, stdin=None, timeout=120, env=None):
        self.calls.append((cmd, stdin))
        return 0, ""

    def test_the_password_goes_through_standard_input_only(self):
        self.assertTrue(locosamba.set_password("lsmb12", SECRET))
        argv = [c for c, _stdin in self.calls]
        self.assertIn(["smbpasswd", "-s", "-a", "lsmb12"], argv)
        self.assertIn(["smbpasswd", "-e", "lsmb12"], argv)
        self.assertTrue(any(c[0] == "useradd" and c[-1] == "lsmb12" and "--no-create-home" in c for c in argv))
        self.assertFalse(any(c[0] == "useradd" and ":" in c[c.index("--comment") + 1] for c in argv))   # GECOS sans « : »
        self.assertFalse(any(SECRET in " ".join(c) for c in argv))                        # jamais en argument
        self.assertIn((["smbpasswd", "-s", "-a", "lsmb12"], f"{SECRET}\n{SECRET}\n"), self.calls)

    def test_what_is_refused(self):
        self.assertFalse(locosamba.set_password("root", SECRET))
        self.assertFalse(locosamba.set_password("lsmb12", "deux\nlignes"))
        self.assertEqual(self.calls, [])

    def test_only_loco_accounts_are_removed(self):
        entries = [mock.Mock(pw_name="lsmb3", pw_gecos="loco compte de partage"), mock.Mock(pw_name="lsmb4", pw_gecos="Autre"),
                   mock.Mock(pw_name="marie", pw_gecos="loco compte de partage")]
        with mock.patch.object(locosamba.pwd, "getpwall", lambda: entries):
            self.assertEqual(locosamba.loco_accounts(), {"lsmb3"})
            locosamba.drop_unix("lsmb4")
            locosamba.drop_unix("lsmb3")
        self.assertEqual([c for c, _stdin in self.calls], [["smbpasswd", "-x", "lsmb3"], ["userdel", "lsmb3"]])

    def test_installation(self):
        with mock.patch.object(locosamba, "alpine", lambda: True):
            locosamba.install()
        with mock.patch.object(locosamba, "alpine", lambda: False):
            locosamba.install()
        argv = [c for c, _stdin in self.calls]
        self.assertEqual(argv[0], ["apk", "add", "--no-progress", "samba-server", "samba-common-tools"])
        self.assertIn("--no-install-recommends", argv[1])
        self.assertEqual(argv[1][-1], "samba")


class AgentTest(unittest.TestCase):
    """Fil de l'agent avec ses dossiers d'échange dans un dossier temporaire et ses commandes interceptées."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="loco-samba-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.requests = os.path.join(self.work, "samba")
        os.makedirs(self.requests)
        self.written = {}
        patches = [mock.patch.object(locosamba, "REQUESTS", self.requests),
                   mock.patch.object(locosamba, "STATUS", os.path.join(self.work, "samba.json")),
                   mock.patch.object(locosamba, "SPEC", os.path.join(self.work, "samba-spec.json")),
                   mock.patch.object(locosamba, "CONF", os.path.join(self.work, "smb.conf")),
                   mock.patch.object(locosamba, "USER_MAP", os.path.join(self.work, "loco-users.map")),
                   mock.patch.object(locosamba, "DFREE", os.path.join(self.work, "samba-dfree")),
                   mock.patch.object(locosamba, "partitions", lambda mountinfo=None: [
                       {"mount": "/", "device": "/dev/sda3", "fstype": "ext4", "size": 100 * GIB, "free": 60 * GIB,
                        "used": 40 * GIB, "base": "/srv/partages"}]),
                   mock.patch.object(locosamba, "installed", lambda: True),
                   mock.patch.object(locosamba, "running", lambda: True),
                   mock.patch.object(locosamba, "_version", lambda: "4.23.8"),
                   mock.patch.object(locosamba, "_allowed_networks", lambda: ["192.168.42.0/24"]),
                   mock.patch.object(locosamba, "ensure_owner", lambda: None),
                   mock.patch.object(locosamba, "ensure_unix", lambda name: None),
                   mock.patch.object(locosamba, "loco_accounts", lambda: set()),
                   mock.patch.object(locosamba, "prepare_folder", lambda path, mount: None),
                   mock.patch.object(locosamba, "with_password", lambda: []),
                   mock.patch.object(locosamba, "service", lambda action: True),
                   mock.patch.object(locosamba, "_run", lambda cmd, stdin=None, timeout=120, env=None: (0, ""))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.worker = locosamba.Worker()

    def spec(self, limit=GIB):
        return {"users": [{"unix": "lsmb1", "login": "admin"}],
                "shares": [{"name": "Documents", "mount": "/", "folder": "documents", "limit": limit, "write": ["lsmb1"]}]}

    def conf(self):
        with open(locosamba.CONF, encoding="utf-8") as f:
            return f.read()


class TestAgent(AgentTest):
    def test_requests_are_private_and_read_once(self):
        locosamba.request("password", unix="lsmb1", password=SECRET)
        names = os.listdir(self.requests)
        self.assertEqual(len(names), 1)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.requests, names[0])).st_mode), 0o600)
        seen = []
        with mock.patch.object(self.worker, "handle", lambda req: seen.append((req, os.listdir(self.requests)))):
            self.worker.requests()
        self.assertEqual(seen, [({"unix": "lsmb1", "password": SECRET, "op": "password"}, [])])   # effacée avant
        self.assertEqual(os.listdir(self.requests), [])

    def test_the_password_lands_nowhere_but_in_samba(self):
        given = []
        self.worker.handle({"op": "apply", "spec": self.spec(), "enabled": True})
        with mock.patch.object(locosamba, "set_password", lambda unix, password: given.append((unix, password)) or True):
            self.worker.handle({"op": "password", "unix": "lsmb1", "password": SECRET})
            self.worker.handle({"op": "password", "unix": "lsmb7", "password": SECRET})       # compte inconnu : rien
        self.assertEqual(given, [("lsmb1", SECRET)])
        for path in (locosamba.STATUS, locosamba.SPEC, locosamba.CONF, locosamba.USER_MAP):
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(SECRET, f.read(), path)
        self.assertEqual(stat.S_IMODE(os.stat(locosamba.SPEC).st_mode), 0o600)

    def test_applied_description_and_its_digest(self):
        self.worker.handle({"op": "apply", "spec": self.spec(), "enabled": True})
        self.assertIn("[Documents]", self.conf())
        self.assertEqual(stat.S_IMODE(os.stat(locosamba.DFREE).st_mode), 0o755)
        with open(locosamba.DFREE, encoding="utf-8") as f:
            self.assertIn(f"{locosamba.HERE} dfree", f.read())
        published = locosamba.status()
        self.assertEqual(published["applied"], locosamba.digest(self.spec()))
        self.assertEqual((published["enabled"], published["installed"], published["version"]), (True, True, "4.23.8"))
        again = locosamba.Worker()                                                 # agent relancé : description relue
        self.assertEqual(again.applied, published["applied"])
        self.assertEqual(again.spec["shares"][0]["path"], "/srv/partages/documents")

    def test_a_foreign_configuration_is_left_alone_until_taken_over(self):
        with open(locosamba.CONF, "w", encoding="utf-8") as f:
            f.write("[global]\n   workgroup = MAISON\n")
        self.worker.handle({"op": "apply", "spec": self.spec(), "enabled": True})
        self.assertEqual(self.conf(), "[global]\n   workgroup = MAISON\n")
        self.assertFalse(locosamba.status()["managed"])
        self.worker.handle({"op": "takeover"})
        self.assertIn("[Documents]", self.conf())
        with open(locosamba.CONF + ".avant-loco", encoding="utf-8") as f:
            self.assertIn("MAISON", f.read())

    def test_a_full_share_turns_read_only_then_back(self):
        self.worker.handle({"op": "apply", "spec": self.spec(limit=GIB), "enabled": True})
        self.assertIn("write list = lsmb1", self.conf())
        with mock.patch.object(locosamba, "measure", lambda path, timeout=600: 2 * GIB), \
                mock.patch.object(locosamba.os.path, "isdir", lambda path: True):
            self.worker.measure_all()
        self.assertNotIn("write list", self.conf())
        self.worker.publish()
        self.assertTrue(locosamba.status()["shares"]["Documents"]["full"])
        with mock.patch.object(locosamba, "measure", lambda path, timeout=600: GIB // 2), \
                mock.patch.object(locosamba.os.path, "isdir", lambda path: True):
            self.worker.measure_all()
        self.assertIn("write list = lsmb1", self.conf())

    def test_disabled_accounts_are_locked_without_losing_their_password(self):
        locked = []
        spec = {"users": [{"unix": "lsmb1", "login": "admin"}, {"unix": "lsmb2", "login": "scanner", "disabled": True}],
                "shares": self.spec()["shares"]}
        with mock.patch.object(locosamba, "with_password", lambda: ["lsmb1", "lsmb2"]), \
                mock.patch.object(locosamba, "lock", lambda name, value: locked.append((name, value))), \
                mock.patch.object(locosamba, "set_password", lambda unix, password: True):
            self.worker.handle({"op": "apply", "spec": spec, "enabled": True})
            self.assertEqual(sorted(locked), [("lsmb1", False), ("lsmb2", True)])
            locked.clear()
            self.worker.handle({"op": "password", "unix": "lsmb2", "password": SECRET})   # nouveau mot de passe
        self.assertEqual(locked, [("lsmb2", True)])                                     # toujours désactivé

    def test_a_takeover_restarts_samba_and_a_change_only_reloads_it(self):
        actions = []
        with mock.patch.object(locosamba, "service", lambda action: actions.append(action) or True):
            self.worker.handle({"op": "takeover"})
            self.worker.handle({"op": "apply", "spec": self.spec(), "enabled": True})
        self.assertEqual(actions, ["restart", "reload"])                    # les ports ne changent qu'au redémarrage

    def test_turned_off(self):
        stopped = []
        self.worker.handle({"op": "apply", "spec": self.spec(), "enabled": True})
        with mock.patch.object(locosamba, "service", lambda action: stopped.append(action) or True):
            self.worker.handle({"op": "stop"})
        self.assertEqual(stopped, ["stop"])
        self.assertFalse(locosamba.status()["enabled"])

    def test_size_seen_by_the_computers(self):
        folder = os.path.join(self.work, "documents")
        os.makedirs(os.path.join(folder, "sous-dossier"))
        with open(locosamba.STATUS, "w", encoding="utf-8") as f:
            json.dump({"shares": {"Documents": {"path": folder, "limit": 10 * GIB, "used": 3 * GIB}}}, f)
        with mock.patch.object(locosamba.os, "statvfs", lambda path: FakeStatvfs()):
            self.assertEqual(locosamba.dfree(os.path.join(folder, "sous-dossier")), f"{10 * GIB // 1024} {7 * GIB // 1024}")
            self.assertEqual(locosamba.dfree(self.work), f"{1000 * GIB // 1024} {550 * GIB // 1024}")   # hors partage


class TestFolders(unittest.TestCase):
    def setUp(self):
        self.mount = tempfile.mkdtemp(prefix="loco-samba-mount-")
        self.addCleanup(shutil.rmtree, self.mount, True)

    def test_created_by_loco(self):
        path = os.path.join(self.mount, "partages", "documents")
        with mock.patch.object(locosamba, "_owner_ids", lambda: (os.getuid(), os.getgid())):
            locosamba.prepare_folder(path, self.mount)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o777, 0o770)

    def test_foreign_files_are_never_taken(self):
        path = os.path.join(self.mount, "partages", "films")
        os.makedirs(path)
        with open(os.path.join(path, "vacances.mkv"), "w") as f:
            f.write("x")
        with mock.patch.object(locosamba, "_owner_ids", lambda: (os.getuid() + 4242, os.getgid())):
            with self.assertRaises(ValueError):
                locosamba.prepare_folder(path, self.mount)


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="loco-samba-portal-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.requests = os.path.join(self.work, "samba")
        os.makedirs(self.requests)
        self.status_path = os.path.join(self.work, "samba.json")
        for patch in (mock.patch.object(locosamba, "REQUESTS", self.requests),
                      mock.patch.object(locosamba, "STATUS", self.status_path)):
            patch.start()
            self.addCleanup(patch.stop)
        self.publish()
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (features.OPTIONAL_SETTING, '["samba"]'))
            for table in ("samba_shares", "samba_accounts"):
                conn.execute(f"DELETE FROM {table}")
            self.admin_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        # La page renvoie d'elle-même la description à un agent pas à jour (essayé à part) : neutralisée ici pour que
        # chaque essai ne voie que les demandes de l'action qu'il fait.
        self.sync = mock.patch.object(samba, "sync", lambda st: None)
        self.sync.start()
        self.addCleanup(self.sync.stop)
        self.addCleanup(self.reset)
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)

    def reset(self):
        with transaction() as conn:
            for key in (features.OPTIONAL_SETTING, features.EXPERIMENTAL_SETTING, features.SETTING):
                conn.execute("DELETE FROM settings WHERE key=?", (key,))
            for table in ("samba_shares", "samba_accounts"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM users WHERE username LIKE 'sm-%'")
            conn.execute("DELETE FROM access_roles WHERE id LIKE 'sm-%'")

    def publish(self, **values):
        data = {"at": time.time(), "support": "", "installed": True, "running": True, "managed": True, "enabled": True,
                "version": "4.23.8", "partitions": [{"mount": "/", "device": "/dev/sda3", "fstype": "ext4", "size": 100 * GIB,
                                                      "free": 60 * GIB, "used": 40 * GIB, "base": "/srv/partages"}],
                "shares": {}, "sessions": [], "networks": ["192.168.42.0/24"], "applied": "", "busy": "", "error": ""}
        data.update(values)
        with open(self.status_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def deposited(self):
        """Demandes déposées pour l'agent, dans l'ordre, puis effacées (comme l'agent le ferait)."""
        found = []
        for name in sorted(os.listdir(self.requests)):
            with open(os.path.join(self.requests, name), encoding="utf-8") as f:
                found.append(json.load(f))
            os.unlink(os.path.join(self.requests, name))
        return found

    def member(self, name, grants):
        from loco import auth, permissions
        with transaction() as conn:
            conn.execute("INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)",
                         ("sm-" + name, "Sm " + name, permissions.role_key("Sm " + name), json.dumps(sorted(permissions.expand(grants)))))
            auth.create_user(conn, "sm-" + name, SECRET + "-loco", "sm-" + name, must_change=False)
            uid = conn.execute("SELECT id FROM users WHERE username=?", ("sm-" + name,)).fetchone()[0]
        browser = fixture.Browser()
        self.assertEqual(browser.login("sm-" + name, SECRET + "-loco").status_code, 302)
        return browser, uid

    def user(self, **values):
        """Ajoute un utilisateur Samba seul (par défaut « comptable ») ; renvoie son numéro."""
        form = {"kind": "samba", "login": "comptable", "label": "Cabinet comptable", "new": "Compta-Secret-2026",
                "confirm": "Compta-Secret-2026"}
        form.update(values)
        self.admin.post("/shares/users/add", form, page="/shares")
        with transaction() as conn:
            row = conn.execute("SELECT id FROM samba_accounts WHERE login=?", (form.get("login") or "",)).fetchone() \
                if "loco" not in values else conn.execute("SELECT id FROM samba_accounts WHERE user_id=?", (int(values["loco"]),)).fetchone()
        return row[0] if row else None

    def add(self, **values):
        form = {"name": "Documents", "mount": "/", "size": "10", "comment": "Équipe"}
        form.update(values)
        return self.admin.post("/shares/add", form, page="/shares")

    def share_id(self):
        with transaction() as conn:
            return conn.execute("SELECT id FROM samba_shares").fetchone()[0]

    def account(self, account_id):
        with transaction() as conn:
            return conn.execute("SELECT * FROM samba_accounts WHERE id=?", (account_id,)).fetchone()


class TestPortal(PortalTest):
    def test_optional_and_off_by_default(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (features.OPTIONAL_SETTING,))
        self.assertEqual(self.admin.c.get("/shares").status_code, 403)

    def test_nothing_about_shares_in_the_account_page(self):
        page = self.admin.c.get("/account").get_data(as_text=True)
        self.assertNotIn('id="partages"', page)
        self.assertNotIn("/shares/", page)

    def test_without_samba_the_page_offers_to_install_it(self):
        self.publish(installed=False, managed=True, version="")
        html = self.admin.c.get("/shares").get_data(as_text=True)
        self.assertIn("Samba n'est pas installé", html.replace("&#39;", "'"))
        self.assertEqual(self.admin.post("/shares/install", {}, page="/shares").status_code, 302)
        self.assertEqual([r["op"] for r in self.deposited()], ["install"])

    def test_a_share_is_created_and_described_to_the_agent(self):
        account = self.user()
        self.deposited()
        self.assertEqual(self.add(**{f"access-{account}": "write"}).status_code, 302)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM samba_shares").fetchone()
            rights = conn.execute("SELECT account_id, can_write FROM samba_access").fetchall()
        self.assertEqual((row["name"], row["mount"], row["folder"], row["size_limit"], row["comment"]),
                         ("Documents", "/", "documents", 10 * GIB, "Équipe"))
        self.assertEqual([tuple(r) for r in rights], [(account, 1)])
        sent = self.deposited()
        self.assertEqual(sent[-1]["op"], "apply")
        self.assertEqual(sent[-1]["spec"]["users"], [{"unix": f"lsmb{account}", "login": "comptable", "disabled": False}])
        self.assertEqual(sent[-1]["spec"]["shares"][0]["write"], [f"lsmb{account}"])
        html = self.admin.c.get("/shares").get_data(as_text=True)
        self.assertIn("/srv/partages/documents", html)
        self.assertIn('value="\\\\', html)                                         # adresse \\serveur\partage à copier

    def test_what_the_form_refuses(self):
        for values in ({"name": "homes"}, {"name": "a b"}, {"size": "0.5"}, {"mount": "/ailleurs"}, {"size": "500"}):
            self.add(**values)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_shares").fetchone()[0], 0)
        self.add()
        self.add(name="DOCUMENTS")                                                 # même nom (casse ignorée) : refusé
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_shares").fetchone()[0], 1)

    def test_the_folder_comes_from_the_name_without_typing_any_path(self):
        self.add(name="Comptabilité", folder="../../etc")                          # champ ignoré : rien à taper
        self.add(name="Comptabilite.")                                             # même dossier : suffixe
        with transaction() as conn:
            folders = [r[0] for r in conn.execute("SELECT folder FROM samba_shares ORDER BY id")]
        self.assertEqual(folders, ["comptabilite", "comptabilite-2"])
        html = self.admin.c.get("/shares").get_data(as_text=True)
        self.assertNotIn('name="folder"', html)
        self.assertIn("data-share-dest", html)

    def test_edited_then_removed_but_the_files_stay(self):
        account = self.user()
        self.add(**{f"access-{account}": "write"})
        share = self.share_id()
        self.deposited()
        r = self.admin.post(f"/shares/{share}", {"name": "Docs", "size": "20", "comment": "", f"access-{account}": "read"},
                            page="/shares")
        self.assertEqual(r.status_code, 302)
        spec = self.deposited()[-1]["spec"]
        self.assertEqual((spec["shares"][0]["name"], spec["shares"][0]["limit"], spec["shares"][0]["read"],
                          spec["shares"][0]["folder"]), ("Docs", 20 * GIB, [f"lsmb{account}"], "documents"))
        self.assertEqual(self.admin.post(f"/shares/{share}/delete", {}, page="/shares").status_code, 302)
        self.assertEqual(self.deposited()[-1]["spec"]["shares"], [])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_access").fetchone()[0], 0)

    def test_rights(self):
        viewer, _uid = self.member("lecteur", {"samba.view"})
        self.assertEqual(viewer.c.get("/shares").status_code, 200)
        self.assertEqual(viewer.post("/shares/add", {"name": "X", "mount": "/", "size": "5"}, page="/shares").status_code, 403)
        self.assertEqual(viewer.post("/shares/users/add", {"kind": "samba", "login": "x1", "new": "Compta-Secret-2026",
                                                           "confirm": "Compta-Secret-2026"}, page="/shares").status_code, 403)
        nobody, _uid = self.member("rien", {"dashboard.view"})
        self.assertEqual(nobody.c.get("/shares").status_code, 403)

    def test_the_page_sends_the_description_again_to_an_agent_behind(self):
        self.add()
        self.deposited()
        self.sync.stop()
        self.admin.c.get("/shares")
        sent = self.deposited()
        self.assertEqual([s["op"] for s in sent], ["apply"])
        self.publish(applied=locosamba.digest(sent[0]["spec"]))
        self.admin.c.get("/shares")
        self.assertEqual(self.deposited(), [])                                     # à jour : rien
        self.sync.start()

    def test_the_description_is_sent_again_in_the_background(self):
        self.add()
        self.deposited()
        self.assertTrue(samba.background())                                        # agent pas à jour : renvoyée
        sent = self.deposited()
        self.assertEqual(sent[0]["op"], "apply")
        self.publish(applied=locosamba.digest(sent[0]["spec"]))
        self.assertFalse(samba.background())                                       # à jour : rien
        self.assertEqual(self.deposited(), [])
        self.publish(at=time.time() - 3600)                                        # agent arrêté : rien non plus
        self.assertFalse(samba.background())

    def test_feature_turned_off_stops_samba(self):
        from loco import features as f
        with mock.patch.object(samba, "switched") as switched:
            r = self.admin.post("/administration/features", {k: "1" for k in f.FEATURES if k not in f.OPTIONAL and k not in f.EXPERIMENTAL},
                                page="/administration/features")
        self.assertEqual(r.status_code, 302)
        switched.assert_called_once_with(False)


class TestUsers(PortalTest):
    """Utilisateurs Samba, tous réglés sur la page des partages : un compte loco lié (son identifiant ; désactivé et
    retiré avec lui) ou un utilisateur Samba seul ; mot de passe choisi par l'administrateur, droits par partage."""

    def test_a_samba_only_user_with_its_password_and_rights(self):
        self.add()
        share = self.share_id()
        self.deposited()
        account = self.user(**{f"share-{share}": "write"})
        row = self.account(account)
        self.assertEqual((row["login"], row["label"], row["user_id"], row["disabled"]), ("comptable", "Cabinet comptable", None, 0))
        sent = self.deposited()
        self.assertEqual([s["op"] for s in sent], ["apply", "password"])
        unix = f"lsmb{account}"
        self.assertIn({"unix": unix, "login": "comptable", "disabled": False}, sent[0]["spec"]["users"])
        self.assertIn(unix, sent[0]["spec"]["shares"][0]["write"])
        self.assertEqual((sent[1]["unix"], sent[1]["password"]), (unix, "Compta-Secret-2026"))
        with transaction() as conn:
            audit = " ".join(r[0] for r in conn.execute("SELECT detail FROM audit WHERE action LIKE 'partages.%'"))
        self.assertNotIn("Compta-Secret-2026", audit)
        html = self.admin.c.get("/shares").get_data(as_text=True)
        self.assertIn("comptable", html)
        self.assertNotIn("Compta-Secret-2026", html)

    def test_a_loco_account_linked_by_the_administrator(self):
        _browser, uid = self.member("marie", {"dashboard.view"})
        account = self.user(kind="loco", loco=str(uid), login="ignore", new="Marie-Samba-2026", confirm="Marie-Samba-2026")
        row = self.account(account)
        self.assertEqual((row["login"], row["user_id"]), ("sm-marie", uid))
        sent = self.deposited()
        self.assertIn({"unix": f"lsmb{account}", "login": "sm-marie", "disabled": False}, sent[0]["spec"]["users"])
        self.assertEqual(sent[1]["password"], "Marie-Samba-2026")
        self.user(kind="loco", loco=str(uid), new="Marie-Samba-2027", confirm="Marie-Samba-2027")   # déjà lié : refusé
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_accounts WHERE user_id=?", (uid,)).fetchone()[0], 1)
        # Compte loco désactivé : son utilisateur Samba aussi ; supprimé : retiré de Samba.
        r = self.admin.post(f"/accounts/{uid}/edit", {"role": "sm-marie", "disabled": "1"}, page="/accounts")
        self.assertEqual(r.status_code, 302)
        self.assertIn({"unix": f"lsmb{account}", "login": "sm-marie", "disabled": True}, self.deposited()[-1]["spec"]["users"])
        self.assertEqual(self.admin.post(f"/accounts/{uid}/delete", {}, page="/accounts").status_code, 302)
        self.assertNotIn(f"lsmb{account}", json.dumps(self.deposited()[-1]["spec"]))
        self.assertIsNone(self.account(account))

    def test_what_is_refused(self):
        for values in ({"login": "admin"}, {"login": "root"}, {"login": "A B"}, {"login": "x"},
                       {"confirm": "Autre-Secret-2026"}, {"new": "court", "confirm": "court"},
                       {"new": "comptable-2026-xyz", "confirm": "comptable-2026-xyz"}, {"kind": "loco", "loco": "999999"}):
            self.user(**values)
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_accounts").fetchone()[0], 0)
        self.assertEqual(self.deposited(), [])
        self.user()
        self.user(login="COMPTABLE")                                                 # même identifiant
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samba_accounts").fetchone()[0], 1)

    def test_edited_disabled_and_deleted(self):
        self.add()
        share = self.share_id()
        account = self.user()
        unix = f"lsmb{account}"
        self.deposited()
        r = self.admin.post(f"/shares/users/{account}", {"label": "Expert", f"share-{share}": "read"}, page="/shares")
        self.assertEqual(r.status_code, 302)
        sent = self.deposited()
        self.assertEqual([s["op"] for s in sent], ["apply"])                         # sans nouveau mot de passe
        self.assertIn(unix, sent[0]["spec"]["shares"][0]["read"])
        self.admin.post(f"/shares/users/{account}", {"label": "Expert", "new": "Nouveau-Secret-2026",
                                                     "confirm": "Nouveau-Secret-2026"}, page="/shares")
        sent = self.deposited()
        self.assertEqual([s["op"] for s in sent], ["apply", "password"])
        self.assertEqual(sent[0]["spec"]["shares"][0]["read"], [])                  # droits du dialogue : aucun
        self.admin.post(f"/shares/users/{account}/toggle", {}, page="/shares")
        self.assertIn({"unix": unix, "login": "comptable", "disabled": True}, self.deposited()[-1]["spec"]["users"])
        self.admin.post(f"/shares/users/{account}/toggle", {}, page="/shares")
        self.assertIn({"unix": unix, "login": "comptable", "disabled": False}, self.deposited()[-1]["spec"]["users"])
        self.admin.post(f"/shares/users/{account}/delete", {}, page="/shares")
        self.assertNotIn(unix, json.dumps(self.deposited()[-1]["spec"]))
        self.assertIsNone(self.account(account))

    def test_rights_given_from_the_share_dialog(self):
        account = self.user()
        self.add(**{f"access-{account}": "write"})
        self.assertIn(f"lsmb{account}", self.deposited()[-1]["spec"]["shares"][0]["write"])


if __name__ == "__main__":
    unittest.main()
