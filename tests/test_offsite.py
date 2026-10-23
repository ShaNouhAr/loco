"""Copie hors-site : chiffrement des secrets, transport « dossier », déduplication, rétention, routes."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import test_portal
from test_portal import Browser, ADMIN_PW
import loco
from loco import offsite, config
from loco.db import transaction


def write_backup(directory, name):
    import io
    import tarfile
    path = os.path.join(directory, name)
    with tarfile.open(path, "w:gz") as tar:
        for member, content in (("etc/loco/loco.conf", b"DATA=/srv/loco\n"), ("portal.db", b"db")):
            info = tarfile.TarInfo(member)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return path


class OffsiteTest(unittest.TestCase):
    def setUp(self):
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (offsite.CONFIG, offsite.STATE))
        self.backups = tempfile.mkdtemp()
        self.dest = tempfile.mkdtemp()
        self._bak = config.BACKUP_DIR
        config.BACKUP_DIR = self.backups

    def tearDown(self):
        config.BACKUP_DIR = self._bak
        shutil.rmtree(self.backups, ignore_errors=True)
        shutil.rmtree(self.dest, ignore_errors=True)

    def folder_cfg(self, **extra):
        cfg = offsite.defaults()
        cfg["type"] = "folder"
        cfg["folder"]["path"] = self.dest
        cfg.update(extra)
        return cfg

    def ctx(self):
        return loco.app.test_request_context()


class TestConfig(OffsiteTest):
    def test_secrets_sealed_and_masked(self):
        with self.ctx():
            cfg = offsite.defaults()
            cfg["s3"]["secret_key"] = "SEKRET-abc-123"
            cfg["crypt"]["passphrase"] = "PASSPHRASE-xyz-789"
            offsite.save(cfg)
            raw = loco.db.db().execute("SELECT value FROM settings WHERE key=?", (offsite.CONFIG,)).fetchone()[0]
            self.assertNotIn("SEKRET-abc-123", raw)
            self.assertNotIn("PASSPHRASE-xyz-789", raw)
            got = offsite.load()
            self.assertEqual(got["s3"]["secret_key"], "SEKRET-abc-123")
            self.assertEqual(offsite.public(got)["s3"]["secret_key"], offsite.MASK)
            self.assertEqual(offsite.public(got)["crypt"]["passphrase"], offsite.MASK)


class TestFolderSync(OffsiteTest):
    def test_push_dedup_and_retention(self):
        write_backup(self.backups, "loco-h-20260101-000000.tar.gz")
        with transaction() as conn:
            offsite.save(self.folder_cfg(keep=2), conn)
        with transaction() as conn:
            offsite.sync(conn)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "loco-h-20260101-000000.tar.gz")))
        with transaction() as conn:
            self.assertEqual(offsite.state(conn)["last_name"], "loco-h-20260101-000000.tar.gz")
        # 2e passage sans nouvelle sauvegarde : ne recopie pas (déduplication)
        with mock.patch("loco.offsite._put") as put:
            with transaction() as conn:
                offsite.sync(conn)
            put.assert_not_called()
        # nouvelle sauvegarde + vieilles archives distantes : rétention à 2
        for old in ("loco-h-20250101-000000.tar.gz", "loco-h-20250601-000000.tar.gz"):
            open(os.path.join(self.dest, old), "wb").close()
        write_backup(self.backups, "loco-h-20260201-000000.tar.gz")
        with mock.patch("loco.system.backup_keep", return_value=2):
            with transaction() as conn:
                offsite.sync(conn)
        kept = [n for n in os.listdir(self.dest) if offsite.NAME_RE.fullmatch(n)]
        self.assertEqual(len(kept), 2)
        self.assertIn("loco-h-20260201-000000.tar.gz", kept)

    def test_encryption_uploads_enc(self):
        write_backup(self.backups, "loco-h-20260301-000000.tar.gz")
        with transaction() as conn:
            offsite.save(self.folder_cfg(crypt={"passphrase": "motdepasse"}), conn)
            offsite.sync(conn)
        files = os.listdir(self.dest)
        self.assertTrue(any(n.endswith(".tar.gz.enc") for n in files), files)

    def test_failure_is_recorded(self):
        write_backup(self.backups, "loco-h-20260401-000000.tar.gz")
        cfg = self.folder_cfg()
        cfg["folder"]["path"] = "/proc/nonexistent/forbidden"      # écriture impossible
        with transaction() as conn:
            offsite.save(cfg, conn)
            offsite.sync(conn)
            st = offsite.state(conn)
        self.assertTrue(st.get("last_error"))
        self.assertNotIn("last_name", st)

    def test_test_destination_ok(self):
        ok, _msg = offsite.test_destination(self.folder_cfg())
        self.assertTrue(ok)
        self.assertEqual([n for n in os.listdir(self.dest)], [])   # objet de test supprimé


class TestRoutes(OffsiteTest):
    def setUp(self):
        super().setUp()
        self.admin = Browser()
        self.admin.login("admin", ADMIN_PW)

    def test_save_selects_type_and_keeps_secret(self):
        self.admin.post("/system/offsite", {"offsite_type": "s3", "offsite_keep": "5", "s3_endpoint": "s3.test",
                                            "s3_region": "eu", "s3_bucket": "b", "s3_access_key": "ak",
                                            "s3_secret_key": "sekret", "s3_use_ssl": "1"}, page="/system")
        with self.ctx():
            cfg = offsite.load()
        self.assertEqual(cfg["type"], "s3")
        self.assertEqual(cfg["s3"]["secret_key"], "sekret")
        # ré-enregistrer avec le masque : secret conservé
        self.admin.post("/system/offsite", {"offsite_type": "s3", "s3_endpoint": "s3.test", "s3_bucket": "b",
                                            "s3_access_key": "ak", "s3_secret_key": offsite.MASK}, page="/system")
        with self.ctx():
            self.assertEqual(offsite.load()["s3"]["secret_key"], "sekret")

    def test_test_route_folder(self):
        r = self.admin.post("/system/offsite/test", {"offsite_type": "folder", "folder_path": self.dest}, page="/system")
        self.assertEqual(r.json["ok"], True)


if __name__ == "__main__":
    unittest.main()
