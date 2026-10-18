"""Groupes et campagnes : frontières HTTP, versions immuables et protocole réel des agents."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, WINDOWS, LINUX, device
from loco import auth, config, deployments, winapps, winget
from loco.db import transaction


class TestDeployments(FleetTest):
    def setUp(self):
        super().setUp()
        self.files = tempfile.TemporaryDirectory(prefix='loco-deployment-')
        self.addCleanup(self.files.cleanup)
        patch = mock.patch.object(config, 'WIN_DIR', self.files.name)
        patch.start()
        self.addCleanup(patch.stop)
        with transaction() as conn:
            for table in ('deployments', 'device_groups', 'software_groups'):
                conn.execute(f'DELETE FROM {table}')
            conn.execute("DELETE FROM settings WHERE key='deployment_gc'")
        self.addCleanup(self.cleanup)
        self.agent = self.enroll('pc-compta', 50)
        self.other = self.enroll('pc-atelier', 51)
        self.linux = Agent(LINUX, '192.168.43.52')
        self.linux.enroll()
        self.app = self.software('editeur-test', 'Outil Éditeur', b'version originale')

    @staticmethod
    def cleanup():
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'deploy-test-%'")
            conn.execute('DELETE FROM deployments')
            conn.execute('DELETE FROM device_groups')
            conn.execute('DELETE FROM software_groups')

    def enroll(self, name, address):
        agent = Agent(dict(WINDOWS, hostname=name), f'192.168.43.{address}')
        self.assertEqual(agent.enroll().status_code, 200)
        return agent

    def software(self, slug, name, content, version='1.0'):
        path = Path(config.WIN_DIR, slug, 'setup.msi')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        with transaction() as conn:
            ident = conn.execute("INSERT INTO win_apps(slug,name,version,kind,args,filename,sha256,status,detect) "
                                 "VALUES (?,?,?,'msi','/qn','setup.msi',?,'ready',?)", (slug, name, version, sha, name)).lastrowid
            return conn.execute('SELECT * FROM win_apps WHERE id=?', (ident,)).fetchone()

    def post(self, path, data=None, browser=None):
        return (browser or self.admin).post(path, data, page='/compte')

    def group(self, kind, name, members):
        prefix = '/windows-apps' if kind == 'software' else '/parc'
        response = self.post(prefix + '/groupes/enregistrer', {'name': name, 'members': [str(i) for i in members]})
        self.assertEqual(response.status_code, 302)
        return int(response.location.rsplit('/', 1)[1])

    def preview(self, data=None, browser=None):
        values = data if data is not None else {'app': self.app['slug'], 'device': str(device(self.agent.uid)['id'])}
        response = self.post('/windows-apps/deploiements/preparer', values, browser)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertIn('/deploiements/', response.location)
        return response.location

    def commands(self):
        with transaction() as conn:
            return conn.execute('SELECT * FROM device_commands ORDER BY id').fetchall()

    def account(self, role):
        with transaction() as conn:
            auth.create_user(conn, 'deploy-test-' + role, 'Test-Deployment-2026', role, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login('deploy-test-' + role, 'Test-Deployment-2026').status_code, 302)
        return browser

    def test_groups_allow_multiple_memberships_and_delete_only_the_group(self):
        dev_id = device(self.agent.uid)['id']
        first = self.group('device', 'Comptabilité', [dev_id])
        second = self.group('device', 'Paris', [dev_id])
        software = self.group('software', 'Socle', [self.app['id']])
        self.assertEqual(self.post(f"/windows-apps/{self.app['slug']}/groupe", {'group_id': software}).status_code, 302)
        with transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM device_group_members WHERE device_id=?', (dev_id,)).fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM software_group_members').fetchone()[0], 1)
        self.assertEqual(self.admin.c.get(f'/parc?group={first}').status_code, 200)
        self.assertEqual(self.post(f'/parc/groupes/{first}/supprimer').status_code, 302)
        with transaction() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM devices WHERE id=?', (dev_id,)).fetchone())
            self.assertIsNotNone(conn.execute('SELECT id FROM device_groups WHERE id=?', (second,)).fetchone())
        self.assertEqual(self.post('/parc/groupes/enregistrer', {'name': 'paris'}).status_code, 302)
        with transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM device_groups').fetchone()[0], 1)

    def test_preview_deduplicates_unions_and_confirmation_freezes_members_and_files(self):
        extra = self.software('outil-deux', 'Outil Deux', b'deux')
        bundle = self.group('software', 'Socle', [self.app['id'], extra['id']])
        group = self.group('device', 'Équipe', [device(self.agent.uid)['id'], device(self.linux.uid)['id']])
        path = self.preview({'software_group': str(bundle), 'app': self.app['slug'],
                             'device_group': str(group), 'device': str(device(self.agent.uid)['id'])})
        self.assertEqual(self.commands(), [])
        html = self.admin.c.get(path).get_data(as_text=True)
        self.assertIn('système non compatible', html)
        self.assertIn('Outil Deux', html)
        # La composition est figée à la prévisualisation, même si un groupe est réorganisé.
        self.post(f'/parc/groupes/{group}/enregistrer', {'name': 'Équipe', 'members': [str(device(self.other.uid)['id'])]})
        self.post(f'/windows-apps/groupes/{bundle}/supprimer')
        self.assertEqual(self.post(path + '/confirmer').status_code, 302)
        self.assertEqual(self.post(path + '/confirmer').status_code, 302)
        commands = self.commands()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]['device_id'], device(self.agent.uid)['id'])
        snapshots = json.loads(commands[0]['params'])['app_snapshots']
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(len({a['slug'] for a in snapshots}), 2)
        for app in snapshots:
            retained = Path(config.WIN_DIR, app['url'].removeprefix('/win/files/'))
            self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), app['sha256'])
        # Une mise à jour atomique, puis une suppression du catalogue, ne change pas le lot confirmé.
        replacement = Path(config.WIN_DIR, 'replacement')
        replacement.write_bytes(b'updated')
        os.replace(replacement, Path(config.WIN_DIR, self.app['slug'], 'setup.msi'))
        with transaction() as conn:
            conn.execute('DELETE FROM win_apps')
        payload = self.agent.checkin({'updates': [], 'apps': []}).get_json()['commands'][0]
        self.assertEqual(payload['apps'], snapshots)
        self.assertEqual(self.other.checkin().get_json()['commands'], [])
        self.assertEqual(self.linux.checkin().get_json()['commands'], [])
        for app in payload['apps']:
            retained = Path(config.WIN_DIR, app['url'].removeprefix('/win/files/'))
            self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), app['sha256'])
        self.assertEqual(self.agent.result(payload['id'], 'success', 'Les deux installations sont terminées.').status_code, 200)
        html = self.admin.c.get(path).get_data(as_text=True)
        self.assertIn('Déploiement terminé', html)
        self.assertIn('Les deux installations sont terminées.', html)
        self.assertIn('Non déployé', html)

    def test_concurrent_confirmations_queue_one_batch(self):
        path = self.preview()
        cookie_name = fixture.loco.app.config['SESSION_COOKIE_NAME']
        cookie = self.admin.c.get_cookie(cookie_name).value
        token = self.admin.token('/compte')
        def confirm(_):
            browser = fixture.loco.app.test_client()
            browser.set_cookie(cookie_name, cookie)
            return browser.post(path + '/confirmer', data={'csrf': token}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(confirm, range(2))), [302, 302])
        self.assertEqual(len(self.commands()), 1)

    def test_mutated_app_or_target_requires_a_new_preview(self):
        path = self.preview()
        with transaction() as conn:
            conn.execute("UPDATE win_apps SET args='/quiet CHANGED=1' WHERE id=?", (self.app['id'],))
        self.assertEqual(self.post(path + '/confirmer').status_code, 409)
        self.assertEqual(self.commands(), [])
        path = self.preview()
        with transaction() as conn:
            conn.execute("UPDATE devices SET state='removing' WHERE uid=?", (self.agent.uid,))
        self.assertEqual(self.post(path + '/confirmer').status_code, 409)
        self.assertEqual(self.commands(), [])

    def test_missing_or_altered_installer_rolls_back_the_entire_campaign(self):
        broken = self.software('broken', 'Second logiciel', b'original')
        Path(config.WIN_DIR, broken['slug'], broken['filename']).write_bytes(b'corrompu')
        path = self.preview({'app': [self.app['slug'], broken['slug']], 'device': str(device(self.agent.uid)['id'])})
        self.assertEqual(self.post(path + '/confirmer').status_code, 302)
        self.assertEqual(self.commands(), [])
        self.assertIn('Déploiement non lancé', self.admin.c.get(path).get_data(as_text=True))
        with transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM deployment_targets').fetchone()[0], 0)

    def test_bulk_permissions_and_browser_ownership_are_enforced_on_the_server(self):
        operator = self.account('operator')
        ids = [str(device(self.agent.uid)['id']), str(device(self.other.uid)['id'])]
        self.assertEqual(self.post('/windows-apps/deploiements/preparer', {'app': self.app['slug'], 'device': ids}, operator).status_code, 403)
        group = self.group('device', 'Un seul poste', [int(ids[0])])
        self.assertEqual(self.post('/windows-apps/deploiements/preparer', {'app': self.app['slug'], 'device_group': group}, operator).status_code, 403)
        path = self.preview(browser=operator)
        self.assertEqual(self.admin.c.get(path).status_code, 404)
        self.assertEqual(self.post(path + '/confirmer').status_code, 404)
        second = fixture.Browser()
        second.login('deploy-test-operator', 'Test-Deployment-2026')
        self.assertEqual(self.post(path + '/confirmer', browser=second).status_code, 403)
        self.assertEqual(self.post(path + '/confirmer', browser=operator).status_code, 302)
        self.assertEqual(len(self.commands()), 1)
        viewer = self.account('viewer')
        self.assertEqual(viewer.c.get(path).status_code, 200)
        self.assertEqual(self.post(path + '/confirmer', browser=viewer).status_code, 403)
        self.assertEqual(self.post('/parc/groupes/enregistrer', {'name': 'Interdit'}, viewer).status_code, 403)

    def test_rights_are_rechecked_before_agent_dispatch(self):
        operator = self.account('operator')
        path = self.preview(browser=operator)
        self.post(path + '/confirmer', browser=operator)
        with transaction() as conn:
            conn.execute("UPDATE users SET role='viewer' WHERE username='deploy-test-operator'")
        self.assertEqual(self.agent.checkin().get_json()['commands'], [])
        self.assertEqual(self.commands()[0]['state'], 'cancelled')

    def test_bulk_permission_is_rechecked_even_if_individual_install_remains_allowed(self):
        admin = self.account('admin')
        path = self.preview({'app': self.app['slug'], 'device': [str(device(a.uid)['id']) for a in (self.agent, self.other)]}, admin)
        self.post(path + '/confirmer', browser=admin)
        with transaction() as conn:
            conn.execute("UPDATE users SET role='operator' WHERE username='deploy-test-admin'")
        self.assertEqual(self.agent.checkin().get_json()['commands'], [])
        self.assertEqual(self.other.checkin().get_json()['commands'], [])
        self.assertEqual([c['state'] for c in self.commands()], ['cancelled', 'cancelled'])

    def test_snapshot_preserves_groups_and_pending_campaigns(self):
        import sqlite3
        from locosnapshot import snapshot
        group = self.group('software', 'Socle à sauvegarder', [self.app['id']])
        path = self.preview({'software_group': str(group), 'device': str(device(self.agent.uid)['id'])})
        self.post(path + '/confirmer')
        backup = Path(self.files.name, 'portal.db')
        snapshot(config.DB_PATH, backup)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT name FROM software_groups WHERE id=?', (group,)).fetchone()[0], 'Socle à sauvegarder')
            self.assertEqual(conn.execute('SELECT state FROM deployments WHERE id=?', (path.rsplit('/', 1)[1],)).fetchone()[0], 'queued')
            params = json.loads(conn.execute('SELECT params FROM device_commands').fetchone()[0])
        retained = Path(config.WIN_DIR, params['app_snapshots'][0]['url'].removeprefix('/win/files/'))
        self.assertTrue(retained.is_file())

    def test_installed_versions_and_pending_batches_are_skipped_but_force_reinstalls(self):
        self.agent.checkin({'updates': [], 'apps': [{'name': self.app['name'], 'version': '1.0'}]})
        path = self.preview()
        self.post(path + '/confirmer')
        self.assertEqual(self.commands(), [])
        self.assertIn('Version déjà installée', self.admin.c.get(path).get_data(as_text=True))
        path = self.preview({'app': self.app['slug'], 'device': str(device(self.agent.uid)['id']), 'force': '1'})
        self.post(path + '/confirmer')
        self.assertEqual(len(self.commands()), 1)
        another = self.preview()
        self.post(another + '/confirmer')
        self.assertEqual(len(self.commands()), 1)
        self.assertIn('Déjà en attente', self.admin.c.get(another).get_data(as_text=True))
        self.assertEqual(len(self.agent.checkin().get_json()['commands'][0]['apps']), 1)

    def test_stale_inventory_does_not_skip_installation(self):
        self.agent.checkin({'updates': [], 'apps': [{'name': self.app['name'], 'version': '1.0'}]})
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_scan='2020-01-01 00:00:00' WHERE uid=?", (self.agent.uid,))
        self.post(self.preview() + '/confirmer')
        self.assertEqual(len(self.commands()), 1)

    def test_cancel_only_stops_queued_commands_and_keeps_history(self):
        path = self.preview({'app': self.app['slug'], 'device': [str(device(a.uid)['id']) for a in (self.agent, self.other)]})
        self.post(path + '/confirmer')
        cmd = self.agent.checkin().get_json()['commands'][0]
        self.post(path + '/annuler')
        self.assertEqual([c['state'] for c in self.commands()], ['sent', 'cancelled'])
        self.assertEqual(self.other.checkin().get_json()['commands'], [])
        self.assertIn('Annulation demandée', self.admin.c.get(path).get_data(as_text=True))
        self.agent.result(cmd['id'], 'success', 'Déjà reçue : installation terminée.')
        self.assertIn('Déploiement annulé', self.admin.c.get(path).get_data(as_text=True))
        self.assertIn('Déjà reçue', self.admin.c.get(path).get_data(as_text=True))

    def test_forgetting_a_device_preserves_its_completed_result_and_logs(self):
        path = self.preview()
        self.post(path + '/confirmer')
        cmd = self.agent.checkin().get_json()['commands'][0]
        self.agent.result(cmd['id'], 'failed', 'Installeur en échec : code 1603')
        self.assertEqual(self.post(f"/parc/{device(self.agent.uid)['id']}/action", {'action': 'forget'}).status_code, 302)
        html = self.admin.c.get(path).get_data(as_text=True)
        self.assertIn('Déploiement terminé avec erreurs', html)
        self.assertIn('Installeur en échec : code 1603', html)
        self.assertIn('pc-compta', html)

    def test_expired_preview_and_all_incompatible_devices_never_queue(self):
        path = self.preview()
        with transaction() as conn:
            conn.execute('UPDATE deployments SET expires=0')
        self.assertEqual(self.post(path + '/confirmer').status_code, 409)
        with mock.patch.object(winget, 'import_package') as importer:
            response = self.post('/windows-apps/deploiements/preparer', {'package_id': 'Vendor.App', 'device': str(device(self.linux.uid)['id'])})
            self.assertEqual(response.status_code, 400)
            importer.assert_not_called()
        self.assertEqual(self.commands(), [])

    def test_store_import_is_shared_waits_for_verified_file_and_can_join_a_group(self):
        content = b'verified winget fixture'
        info = {'name': 'Fixture Store', 'version': '2.5', 'kind': 'msi', 'args': '/qn', 'url': 'https://example.test/setup.msi',
                'detect': 'Fixture Store', 'sha256': hashlib.sha256(content).hexdigest(), 'type': 'msi'}
        group = self.group('software', 'Socle', [])
        with mock.patch.object(winget, 'describe', return_value=info), mock.patch.object(winapps, 'start_fetch') as fetch:
            path = self.preview({'package_id': 'Vendor.Fixture', 'device': str(device(self.agent.uid)['id'])})
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(self.post(path + '/confirmer').status_code, 409)
            self.assertEqual(self.post('/windows-apps/store/Vendor.Fixture/groupe', {'group_id': group}).status_code, 302)
            self.assertEqual(fetch.call_count, 1)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM win_apps WHERE winget_id='Vendor.Fixture'").fetchone()
            self.assertEqual(row['expected_sha256'], info['sha256'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM software_group_members WHERE group_id=?', (group,)).fetchone()[0], 1)
        # Actualiser un import garde la vérification WinGet, y compris après un redémarrage du serveur.
        with fixture.loco.app.test_request_context(), mock.patch('loco.winapps.threading.Thread') as thread:
            winapps.start_fetch(row)
            self.assertEqual(thread.call_args.kwargs['args'][-2:], (info['sha256'], 'setup.msi'))
        local = Path(config.WIN_DIR, row['slug'], 'setup.msi')
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        with transaction() as conn:
            conn.execute("UPDATE win_apps SET status='ready',sha256=?,filename='setup.msi' WHERE id=?", (info['sha256'], row['id']))
        self.assertEqual(self.post(path + '/confirmer').status_code, 302)
        self.assertEqual(self.commands()[0]['action'], 'install-app')

    def test_store_group_import_failure_is_reported_without_mutating_members(self):
        group = self.group('software', 'Socle', [])
        with mock.patch.object(winget, 'import_package', side_effect=ValueError('Manifeste indisponible')):
            response = self.post('/windows-apps/store/Vendor.Fixture/groupe', {'group_id': group})
            self.assertEqual(response.status_code, 302)
        with transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM software_group_members').fetchone()[0], 0)

    def test_cache_retains_active_versions_and_only_removes_old_unused_deployment_files(self):
        path = self.preview()
        self.post(path + '/confirmer')
        payload = json.loads(self.commands()[0]['params'])['app_snapshots'][0]
        directory = Path(config.WIN_DIR, '_deployments', payload['sha256'])
        unused = directory.parent / ('0' * 64)
        unused.mkdir()
        (unused / 'installer.msi').write_bytes(b'obsolete')
        for entry in (directory, unused):
            os.utime(entry, (time.time() - 7200, time.time() - 7200))
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key='deployment_gc'")
            deployments.cleanup_artifacts(conn)
        self.assertTrue(directory.is_dir())
        self.assertFalse(unused.exists())
        self.assertTrue(Path(config.WIN_DIR, self.app['slug'], self.app['filename']).is_file())
        self.post(path + '/annuler')
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key='deployment_gc'")
            deployments.cleanup_artifacts(conn)
        self.assertFalse(directory.exists())

    def test_group_filtered_bulk_update_never_touches_other_devices(self):
        for agent in (self.agent, self.other):
            agent.checkin({'updates': [{'name': 'Correctif'}]})
        group = self.group('device', 'Équipe', [device(self.agent.uid)['id']])
        response = self.post('/parc/tout-mettre-a-jour', {'group': str(group)})
        self.assertEqual(response.status_code, 302)
        self.assertIn('group=', response.location)
        self.assertEqual([c['device_id'] for c in self.commands()], [device(self.agent.uid)['id']])
