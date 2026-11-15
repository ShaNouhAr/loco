"""Cycle des sauvegardes du serveur natif Windows, avec fichiers et SQLite réels."""

from contextlib import closing
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portal'))
import locowin  # noqa: E402


class TestWindowsBackups(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='lr-win-backup-')
        root = Path(self.work.name)
        self.cfg = {name + '_dir': str(root / name) for name in ('data', 'state', 'etc', 'backup')}
        self.cfg['backup_keep'] = 1
        for value in list(self.cfg.values())[:-1]:
            Path(value).mkdir()
        (root / 'data' / '.tmp').mkdir()
        (root / 'data' / 'apt').mkdir()
        (root / 'data' / 'win').mkdir()
        (root / 'data' / 'apt' / 'test.deb').write_bytes(b'original package')
        (root / 'etc' / 'loco.json').write_text(json.dumps({'host': 'before.test'}))
        (root / 'etc' / 'portal.secret').write_bytes(b'original key')
        self.db = root / 'state' / 'portal.db'
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('CREATE TABLE accounts (name TEXT)')
            con.execute('INSERT INTO accounts VALUES (?)', ('admin',))

    def tearDown(self):
        self.work.cleanup()

    def test_restore_preserves_machine_configuration_and_restores_payload_and_accounts(self):
        archive = locowin.backup_create(self.cfg)
        root = Path(self.work.name)
        (root / 'data' / 'apt' / 'test.deb').unlink()
        (root / 'etc' / 'portal.secret').write_bytes(b'changed key')
        configuration = root / 'etc' / 'loco.json'
        configuration.write_text(json.dumps({'host': 'current.test'}))
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('DELETE FROM accounts')
        locowin.backup_restore(self.cfg, archive)
        self.assertEqual(json.loads(configuration.read_text()), {'host': 'current.test'})
        self.assertEqual((root / 'data' / 'apt' / 'test.deb').read_bytes(), b'original package')
        self.assertEqual((root / 'etc' / 'portal.secret').read_bytes(), b'original key')
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute('SELECT * FROM accounts').fetchall(), [('admin',)])

    def test_retention_only_removes_older_application_backups(self):
        old = Path(self.cfg['backup_dir']) / 'loco-old-20000101-000000.tar.gz'
        old.write_bytes(b'old')
        os.utime(old, (1, 1))
        other = Path(self.cfg['backup_dir']) / 'other-backup.tar.gz'
        other.write_bytes(b'unrelated')
        archive = locowin.backup_create(self.cfg)
        self.assertFalse(old.exists())
        self.assertTrue(Path(archive).exists())
        self.assertTrue(other.exists())

    def test_restore_does_not_replace_current_config(self):
        root = Path(self.work.name)
        current = (root / 'etc/loco.json').read_bytes()
        archive = root / 'other.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, content in [('portal.db', self.db.read_bytes()), ('etc/loco.json', b'{"host":"other"}'),
                                  ('data/apt/restored.deb', b'restored package')]:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
        locowin.backup_restore(self.cfg, archive)
        self.assertEqual((root / 'etc/loco.json').read_bytes(), current)
        self.assertEqual((root / 'data/apt/restored.deb').read_bytes(), b'restored package')

    def test_unsafe_archive_is_rejected_before_any_files_are_changed(self):
        for name in ('etc/../outside', 'etc/..\\outside', 'etc/C:/outside', '/outside'):
            with self.subTest(name=name):
                archive = Path(self.work.name) / 'invalid.tar.gz'
                before = self.db.read_bytes()
                with tarfile.open(archive, 'w:gz') as tar:
                    for entry, data in [('portal.db', b'bad database'), ('etc/loco.json', b'{}'), (name, b'unsafe')]:
                        member = tarfile.TarInfo(entry)
                        member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
                with self.assertRaises(ValueError):
                    locowin.backup_restore(self.cfg, str(archive))
                self.assertEqual(self.db.read_bytes(), before)

    def test_linux_archive_is_not_mistaken_for_a_windows_backup(self):
        archive = Path(self.work.name) / 'linux.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            member = tarfile.TarInfo('portal.db')
            tar.addfile(member, io.BytesIO(b''))
        with self.assertRaises(SystemExit):
            locowin.backup_restore(self.cfg, str(archive))


if __name__ == '__main__':
    unittest.main()
