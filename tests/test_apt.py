"""Isolation réelle des dépôts : .deb, index et signatures ; fichiers temporaires uniquement."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_portal as fixture
from loco import auth, config, linux
from loco.db import transaction


class TestAptRepositories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in (linux.APT_FTPARCHIVE, 'gpg', 'dpkg-deb'):
            if shutil.which(command) is None:
                raise RuntimeError('Installez apt-utils et gnupg pour les tests APT.')
        os.makedirs(config.GNUPGHOME, mode=0o700, exist_ok=True)
        subprocess.run(['gpg', '--homedir', config.GNUPGHOME, '--batch', '--pinentry-mode', 'loopback',
                        '--passphrase', '', '--quick-generate-key', 'loco tests <tests@invalid>',
                        'ed25519', 'sign', '0'], check=True, capture_output=True)

    def setUp(self):
        with transaction() as c:
            c.execute('DELETE FROM apt_repositories')
        shutil.rmtree(config.APT_REPOSITORIES_DIR, ignore_errors=True)
        shutil.rmtree(config.POOL_DIR, ignore_errors=True)
        linux.rebuild_apt()
        fixture.reset_failures()

    def tearDown(self):
        with transaction() as c:
            c.execute('DELETE FROM apt_repositories')
        shutil.rmtree(config.APT_REPOSITORIES_DIR, ignore_errors=True)
        shutil.rmtree(config.POOL_DIR, ignore_errors=True)
        linux.rebuild_apt()

    def build_deb(self, content):
        root = Path(tempfile.mkdtemp(dir=fixture.TMP))
        (root / 'pkg/DEBIAN').mkdir(parents=True)
        (root / 'pkg/usr/share/isolation-test').mkdir(parents=True)
        (root / 'pkg/DEBIAN/control').write_text('Package: isolation-test\nVersion: 1.0\nArchitecture: all\n'
                                              'Maintainer: Tests <tests@invalid>\nDescription: isolation test\n')
        (root / 'pkg/usr/share/isolation-test/content').write_text(content)
        dest = root / 'test.deb'
        subprocess.run(['dpkg-deb', '--root-owner-group', '-b', str(root / 'pkg'), str(dest)],
                       check=True, capture_output=True)
        return str(dest)

    def admin(self):
        b = fixture.Browser()
        self.assertEqual(b.login('admin', fixture.ADMIN_PW).status_code, 302)
        return b

    def test_migrated_repository_keeps_apt_identity(self):
        repo = linux.create_repository('debian', 'migration-test', '13')
        release = Path(linux.repository_path(repo), 'Release')
        release.write_text(release.read_text().replace('Origin: loco', 'Origin: local-repo')
                           .replace('Label: loco', 'Label: local-repo'))
        linux.rebuild_apt(repo)
        self.assertIn('Origin: local-repo\n', release.read_text())
        self.assertIn('Label: local-repo debian/migration-test\n', release.read_text())

    def test_apt_init_seeds_default_repositories(self):
        from loco import cli, config
        cli.main(['apt-init'])
        keys = {r['key'] for r in linux.repositories()}
        for distribution, suite, _version in config.APT_DEFAULT_REPOSITORIES:
            self.assertIn(f'{distribution}/{suite}', keys)
        cli.main(['apt-init'])                        # idempotent : rien dupliqué au second passage
        self.assertEqual({r['key'] for r in linux.repositories()}, keys)

    def test_apt_repo_add_is_idempotent(self):
        from loco import cli
        cli.main(['apt-repo', 'add', 'debian', 'trixie', '13'])
        cli.main(['apt-repo', 'add', 'debian', 'trixie', '13'])    # « add » d'un dépôt existant : ne lève pas
        self.assertEqual([r['key'] for r in linux.repositories()].count('debian/trixie'), 1)

    def test_same_package_versions_stay_separate(self):
        first = linux.create_repository('debian', 'test-one', '13')
        second = linux.create_repository('ubuntu', 'test-two', '24.04')
        a, b = self.build_deb('DEBIAN'), self.build_deb('UBUNTU')
        a_bytes, b_bytes = Path(a).read_bytes(), Path(b).read_bytes()
        name = linux.add_deb(a, first)
        self.assertEqual(linux.add_deb(b, second), name)
        for repo, expected in [(first, a_bytes), (second, b_bytes)]:
            root = Path(linux.repository_path(repo))
            self.assertEqual((root / 'pool' / name).read_bytes(), expected)
            self.assertEqual(len(linux.list_debs(repo)), 1)
            subprocess.run(['gpg', '--homedir', config.GNUPGHOME, '--verify', str(root / 'InRelease')],
                           check=True, capture_output=True)
        admin = self.admin()
        self.assertEqual(admin.post('/linux/delete', {'repository': first, 'file': name}).status_code, 302)
        self.assertEqual(linux.list_debs(first), [])
        self.assertEqual(len(linux.list_debs(second)), 1)
        self.assertEqual(len(linux.list_debs()), 1)
        html = admin.c.get('/linux?repository=' + second).get_data(as_text=True)
        self.assertIn('value="ubuntu/test-two"', html)
        self.assertIn('/apt/repositories/ubuntu/test-two/pool/', html)

    def test_future_release_and_public_sources(self):
        key = linux.create_repository('debian', 'future-release', '99')
        c = fixture.loco.app.test_client()
        r = c.get('/linux/repositories/debian/future-release.sources', base_url='https://repo.test:8443')
        self.assertEqual(r.status_code, 200)
        self.assertIn('URIs: https://repo.test:8443/apt/repositories/' + key + '/', r.get_data(as_text=True))
        self.assertIn('Signed-By: /etc/apt/keyrings/loco.gpg', r.get_data(as_text=True))
        self.assertNotIn('Set-Cookie', r.headers)
        self.assertEqual(c.get('/linux/repositories/debian/not-created.sources').status_code, 404)
        os.unlink(os.path.join(linux.repository_path(key), 'InRelease'))
        self.assertEqual(c.get('/linux/repositories/debian/future-release.sources').status_code, 503)

    def test_invalid_paths_and_missing_destination_rejected(self):
        for value in ('', '../etc', 'debian/../ubuntu', 'ubuntu/noble/../../etc', 'debian/a;touch', 'fedora/next',
                      'debian/stable\n', 'debian/-option'):
            with self.assertRaises(ValueError, msg=value):
                linux.require_repository(value)
        admin = self.admin()
        for target in ('', '../etc', 'debian/not-created'):
            self.assertEqual(admin.post('/linux/upload', {'repository': target}).status_code, 400)
            self.assertEqual(admin.post('/linux/delete', {'repository': target, 'file': 'x.deb'}).status_code, 400)

    def test_legacy_kept_and_copy_does_not_move_original(self):
        key = linux.create_repository('debian', 'test-one')
        name = linux.add_deb(self.build_deb('OLD'), linux.LEGACY)
        root = Path(config.APT_DIR)
        legacy_release = (root / 'Release').read_text()
        self.assertIn('Label: loco\n', legacy_release)
        self.assertIn('Suite: stable\n', legacy_release)
        self.assertNotIn('repositories/', legacy_release)
        original = (root / 'pool' / name).read_bytes()
        admin = self.admin()
        r = admin.post('/linux/copy', {'repository': linux.LEGACY, 'destination': key, 'file': name})
        self.assertEqual(r.status_code, 302)
        self.assertEqual((root / 'pool' / name).read_bytes(), original)
        self.assertEqual((Path(linux.repository_path(key)) / 'pool' / name).read_bytes(), original)

    def test_failed_signing_preserves_existing_metadata(self):
        key = linux.create_repository('debian', 'test-one')
        root = Path(linux.repository_path(key))
        before = {name: (root / name).read_bytes() for name in ('Packages', 'Release', 'InRelease', 'Release.gpg')}
        run = subprocess.run
        def fail_gpg(args, **kwargs):
            if args[0] == 'gpg':
                raise subprocess.CalledProcessError(1, args)
            return run(args, **kwargs)
        with mock.patch('loco.linux.subprocess.run', side_effect=fail_gpg):
            with self.assertRaises(subprocess.CalledProcessError):
                linux.rebuild_apt(key)
        self.assertEqual({name: (root / name).read_bytes() for name in before}, before)
        self.assertFalse(list(root.glob('.metadata-*')))

    def test_repository_management_requires_admin(self):
        with transaction() as c:
            auth.create_user(c, 'repo-viewer', 'Read-Only-Password-2026', 'viewer', must_change=False)
        try:
            b = fixture.Browser()
            b.login('repo-viewer', 'Read-Only-Password-2026')
            self.assertEqual(b.post('/linux/repositories', {'distribution': 'debian', 'suite': 'test-one'}).status_code, 403)
            self.assertEqual(b.post('/linux/copy', {'repository': 'legacy'}).status_code, 403)
            self.assertEqual(b.post('/linux/upload', {'repository': 'legacy'}).status_code, 403)
            self.assertEqual(b.c.get('/linux').status_code, 200)
        finally:
            with transaction() as c:
                c.execute("DELETE FROM users WHERE username='repo-viewer'")

    def test_existing_package_cannot_be_replaced_with_different_contents(self):
        key = linux.create_repository('debian', 'test-one')
        name = linux.add_deb(self.build_deb('ORIGINAL'), key)
        path = Path(linux.repository_path(key)) / 'pool' / name
        before = path.read_bytes()
        candidate = self.build_deb('MODIFIED')
        with self.assertRaises(ValueError):
            linux.add_deb(candidate, key)
        self.assertEqual(path.read_bytes(), before)
        with tempfile.TemporaryDirectory(dir=fixture.TMP) as stage:
            copy = Path(stage) / name
            copy.write_bytes(before)
            self.assertEqual(linux.add_deb(str(copy), key), name)
