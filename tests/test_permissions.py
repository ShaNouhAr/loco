"""Profils réels : frontières HTTP, délégation, migration, révocation et concurrence entre administrateurs."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from unittest import mock
from urllib.parse import urlsplit

import test_portal as fixture
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from loco import auth, config, db, permissions, remote, PUBLIC_ENDPOINTS

PASSWORD = 'Permissions-2026-Secret'


class TestPermissions(FleetTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_accounts)
        self.agent = Agent(WINDOWS, '192.168.43.60')
        self.agent.enroll()
        self.dev_id = device(self.agent.uid)['id']

    def cleanup_accounts(self):
        with db.transaction() as conn:
            conn.execute("UPDATE users SET disabled=0,role='admin' WHERE username='admin'")
            conn.execute("DELETE FROM users WHERE username LIKE 'perm-%'")
            conn.execute("DELETE FROM access_roles WHERE builtin=0 AND (id LIKE 'perm-%' OR label LIKE 'Perm test %')")

    def profile(self, name, grants):
        key = 'perm-' + name
        label = 'Perm test ' + name
        with db.transaction() as conn:
            conn.execute('INSERT INTO access_roles (id,label,label_key,permissions) VALUES (?,?,?,?)',
                         (key, label, permissions.role_key(label), json.dumps(sorted(permissions.expand(grants)))))
        return key

    def browser(self, name, role):
        username = 'perm-' + name
        with db.transaction() as conn:
            auth.create_user(conn, username, PASSWORD, role, must_change=False)
        browser = fixture.Browser()
        self.assertEqual(browser.login(username, PASSWORD).status_code, 302)
        return browser

    @staticmethod
    def post(browser, path, values=None):
        return browser.post(path, values, page='/compte')

    @staticmethod
    def uid(name):
        with db.transaction() as conn:
            return conn.execute('SELECT id FROM users WHERE username=?', ('perm-' + name,)).fetchone()[0]

    def test_every_private_route_has_an_explicit_policy_and_empty_profiles_deny_all_routes(self):
        private = set(fixture.loco.app.view_functions) - PUBLIC_ENDPOINTS - permissions.ACCOUNT_ENDPOINTS
        self.assertEqual(private, set(permissions.ENDPOINTS))
        role = self.profile('empty', [])
        reader = self.browser('empty', role)
        values = {'dev_id': self.dev_id, 'sid': '0' * 32, 'role_id': role,
                  'user_id': self.uid('empty'), 'provider_id': 'missing', 'group_id': 999999, 'deployment_id': 'missing', 'slug': 'not-present', 'package_id': 'Vendor.Application', 'job_id': 'not-present', 'script_id': 999999, 'run_id': 999999, 'directory_id': 'missing', 'cmd_id': 999999}
        with fixture.loco.app.test_request_context():
            from flask import url_for
            paths = [(rule, url_for(rule.endpoint, **{key: values[key] for key in rule.arguments}))
                     for rule in fixture.loco.app.url_map.iter_rules() if rule.endpoint in private]
        for rule, path in paths:
            if 'GET' in rule.methods:
                response = reader.c.get(path)
                self.assertEqual(response.status_code, 302 if rule.endpoint == 'index' else 403, path)
            if 'POST' in rule.methods:
                self.assertEqual(self.post(reader, path).status_code, 403, path)
        self.assertEqual(reader.c.get('/compte').status_code, 200)
        self.assertEqual(reader.c.get('/healthz').status_code, 200)
        self.assertEqual(self.agent.checkin().status_code, 200)
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM device_commands').fetchone()[0], 0)

    def test_store_only_profile_keeps_a_navigation_entry_without_catalog_access(self):
        reader = self.browser('store-reader', self.profile('store-reader', ['store.view']))
        with mock.patch('loco.winget.browse', return_value=([], 0, 1, 1)):
            response = reader.c.get('/windows-apps/store')
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/windows-apps/store"', response.get_data(as_text=True))
        self.assertEqual(reader.c.get('/windows-apps').status_code, 403)

    def test_linux_novnc_fallback_requires_terminal_permission_and_is_revoked_with_it(self):
        linux = Agent(LINUX, '192.168.43.62')
        linux.enroll()
        dev_id = device(linux.uid)['id']
        reader = self.browser('desktop-only', self.profile('desktop-only', ['remote.desktop']))
        response = self.post(reader, f'/parc/{dev_id}/session', {'kind': 'desktop', 'terminal_fallback': '1'})
        self.assertEqual(response.status_code, 302)
        sid = response.headers['Location'].rsplit('/', 1)[1]
        descriptors = linux.post('/agent/api/remote', {'sessions': []}, linux.token).get_json()['sessions']
        self.assertFalse(next(d for d in descriptors if d['id'] == sid)['terminal_fallback'])
        both_role = self.profile('both-consoles', ['remote.desktop', 'remote.terminal'])
        operator = self.browser('both-consoles', both_role)
        response = self.post(operator, f'/parc/{dev_id}/session', {'kind': 'desktop'})
        sid = response.headers['Location'].rsplit('/', 1)[1]
        descriptors = linux.post('/agent/api/remote', {'sessions': []}, linux.token).get_json()['sessions']
        self.assertTrue(next(d for d in descriptors if d['id'] == sid)['terminal_fallback'])
        # Même une réduction directe du profil est prise en compte avant le prochain paquet de l'agent.
        with db.transaction() as conn:
            conn.execute('UPDATE access_roles SET permissions=? WHERE id=?',
                         (json.dumps(sorted(permissions.expand(['remote.desktop']))), both_role))
        linux.post('/agent/api/remote', {'sessions': []}, linux.token)
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT state FROM remote_sessions WHERE id=?', (sid,)).fetchone()[0], 'closed')

    def test_inventory_right_does_not_allow_updates_reboot_removal_catalog_or_enrollment_key(self):
        operator = self.browser('scan', self.profile('scan', ['fleet.scan']))
        page = operator.c.get(f'/parc/{self.dev_id}').get_data(as_text=True)
        self.assertIn('name="action" value="scan"', page)
        for action in ('update', 'reboot', 'uninstall', 'forget', 'install-app'):
            self.assertNotIn('name="action" value="' + action + '"', page)
            self.assertEqual(self.post(operator, f'/parc/{self.dev_id}/action', {'action': action}).status_code, 403)
        self.assertNotIn('Ajouter un poste', operator.c.get('/parc').get_data(as_text=True))
        for path in ('/windows-apps', '/api/apps', '/systeme', '/journal', '/comptes'):
            self.assertEqual(operator.c.get(path).status_code, 403)
        self.assertEqual(self.post(operator, f'/parc/{self.dev_id}/action', {'action': 'scan'}).status_code, 302)
        commands = self.agent.checkin().get_json()['commands']
        self.assertEqual([command['action'] for command in commands], ['scan'])
        self.assertEqual(urlsplit(operator.c.get('/').headers['Location']).path, '/parc')

    def test_reboot_and_bulk_updates_require_their_own_rights(self):
        operator = self.browser('update', self.profile('update', ['fleet.update']))
        path = f'/parc/{self.dev_id}/action'
        self.assertEqual(self.post(operator, path, {'action': 'update', 'reboot': '1'}).status_code, 403)
        self.assertEqual(self.post(operator, '/parc/tout-mettre-a-jour').status_code, 403)
        self.assertEqual(self.post(operator, path, {'action': 'update'}).status_code, 302)
        command = self.agent.checkin().get_json()['commands'][0]
        self.assertFalse(command['reboot'])
        self.assertEqual(command['action'], 'update')

    def test_profiles_add_requirements_and_reject_unknown_or_excessive_grants(self):
        manager = self.browser('manager', self.profile('manager', permissions.VIEWER | {'users.create', 'roles.manage'}))
        self.assertEqual(self.post(manager, '/comptes/profils/ajouter', {'label': 'Perm test excessive', 'permissions': ['remote.terminal']}).status_code, 403)
        self.assertEqual(self.post(self.admin, '/comptes/profils/ajouter', {'label': 'Perm test unknown', 'permissions': ['*']}).status_code, 400)
        response = self.post(self.admin, '/comptes/profils/ajouter', {'label': 'Perm test store', 'permissions': ['store.import']})
        self.assertEqual(response.status_code, 302)
        key = response.headers['Location'].rsplit('/', 1)[1]
        with db.transaction() as conn:
            self.assertEqual(permissions.role_permissions(conn, key), {'store.import', 'store.view', 'windows.add', 'windows.view'})
            details = '\n'.join(row[0] for row in conn.execute("SELECT detail FROM audit WHERE action='profils.droits'"))
            self.assertIn('store.import', details)
        self.assertEqual(self.post(self.admin, '/comptes/profils/ajouter', {'label': 'PERM TEST STORE'}).status_code, 302)
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM access_roles WHERE label_key=?', ('perm test store',)).fetchone()[0], 1)

    def test_account_delegation_cannot_reset_edit_delete_or_assign_a_more_privileged_account(self):
        grants = permissions.VIEWER | {'users.create', 'users.edit', 'users.reset', 'users.delete'}
        manager = self.browser('accounts', self.profile('accounts', grants))
        other = self.browser('technician', 'operator')
        self.assertEqual(other.c.get('/parc').status_code, 200)
        for role in ('admin', 'operator'):
            self.assertEqual(self.post(manager, '/comptes/ajouter', {'username': 'perm-illegal', 'role': role}).status_code, 403)
        with db.transaction() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        for uid in (admin_id, self.uid('technician')):
            for action in ('reinitialiser', 'supprimer'):
                self.assertEqual(self.post(manager, f'/comptes/{uid}/{action}').status_code, 403)
            self.assertEqual(self.post(manager, f'/comptes/{uid}/modifier', {'role': 'viewer'}).status_code, 403)
        self.assertEqual(self.post(manager, '/comptes/ajouter', {'username': 'perm-reader', 'role': 'viewer'}).status_code, 302)
        self.assertEqual(self.post(manager, f'/comptes/{self.uid("reader")}/reinitialiser').status_code, 302)
        with db.transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE username='perm-illegal'").fetchone())
        # Même tous les droits d'un profil personnalisé n'autorisent pas à s'emparer d'un administrateur complet.
        custom_admin = self.browser('custom-admin', self.profile('all', permissions.PERMISSIONS))
        self.assertEqual(self.post(custom_admin, f'/comptes/{admin_id}/reinitialiser').status_code, 403)
        self.assertEqual(self.post(custom_admin, '/comptes/ajouter', {'username': 'perm-illegal', 'role': 'admin'}).status_code, 403)

    def test_builtin_current_and_assigned_roles_are_protected(self):
        for role in ('admin', 'viewer'):
            for action in ('modifier', 'supprimer'):
                self.assertEqual(self.post(self.admin, f'/comptes/profils/{role}/{action}', {'label': 'Perm test hacked'}).status_code, 403)
        own_role = self.profile('own', permissions.VIEWER | {'roles.manage'})
        manager = self.browser('own', own_role)
        self.assertEqual(self.post(manager, f'/comptes/profils/{own_role}/modifier', {'label': 'Perm test own'}).status_code, 403)
        self.assertEqual(self.post(self.admin, f'/comptes/profils/{own_role}/supprimer').status_code, 302)
        with db.transaction() as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM access_roles WHERE id=?', (own_role,)).fetchone())
        spare = self.profile('spare', ['fleet.view'])
        self.assertEqual(self.post(self.admin, f'/comptes/profils/{spare}/supprimer').status_code, 302)
        with db.transaction() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM access_roles WHERE id=?', (spare,)).fetchone())

    def test_desktop_and_terminal_rights_are_independent_and_profile_edits_revoke_all_affected_sessions(self):
        role = self.profile('remote', ['remote.desktop', 'fleet.scan'])
        first, second = self.browser('remote-a', role), self.browser('remote-b', role)
        path = f'/parc/{self.dev_id}/session'
        self.assertEqual(self.post(first, path, {'kind': 'terminal'}).status_code, 403)
        response = self.post(first, path, {'kind': 'desktop'})
        self.assertEqual(response.status_code, 302)
        sid = response.headers['Location'].rsplit('/', 1)[1]
        self.assertEqual(first.c.get('/parc/session/' + sid).status_code, 200)
        self.agent.post('/agent/api/remote', {'sessions': [{'id': sid, 'state': 'active'}]}, self.agent.token)
        self.post(first, '/parc/session/' + sid + '/io', {'seq': 1, 'data': base64.b64encode(b'held keys').decode()})
        self.post(first, f'/parc/{self.dev_id}/action', {'action': 'scan'})
        response = self.post(self.admin, '/comptes/profils/' + role + '/modifier', {'label': 'Perm test remote', 'permissions': ['fleet.view']})
        self.assertEqual(response.status_code, 302)
        for browser in (first, second):
            self.assertEqual(browser.c.get('/parc', headers={'X-Requested-With': 'fetch'}).status_code, 401)
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT state FROM remote_sessions WHERE id=?', (sid,)).fetchone()[0], 'closed')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM remote_packets WHERE session_id=?', (sid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT state FROM device_commands WHERE device_id=?', (self.dev_id,)).fetchone()[0], 'cancelled')
        self.assertEqual(first.login('perm-remote-a', PASSWORD).status_code, 302)
        self.assertEqual(first.c.get('/parc').status_code, 200)
        self.assertEqual(self.post(first, path, {'kind': 'desktop'}).status_code, 403)

    def test_dispatch_rechecks_permissions_even_if_database_role_changed_outside_the_portal(self):
        operator = self.browser('dispatch', self.profile('dispatch', ['fleet.remove']))
        self.post(operator, f'/parc/{self.dev_id}/action', {'action': 'uninstall'})
        self.assertEqual(device(self.agent.uid)['state'], 'removing')
        with db.transaction() as conn:
            conn.execute("UPDATE users SET role='viewer' WHERE username='perm-dispatch'")
        self.assertEqual(self.agent.checkin().get_json()['commands'], [])
        self.assertEqual(device(self.agent.uid)['state'], 'active')

    def test_password_reset_revokes_remote_access_and_cancels_only_not_yet_transmitted_actions(self):
        operator = self.browser('reset', self.profile('reset', ['fleet.remove']))
        uid = self.uid('reset')
        self.post(operator, f'/parc/{self.dev_id}/action', {'action': 'uninstall'})
        self.post(self.admin, f'/comptes/{uid}/reinitialiser')
        self.assertEqual(device(self.agent.uid)['state'], 'active')
        self.assertEqual(self.agent.checkin().get_json()['commands'], [])
        # Une demande déjà reçue par l'agent ne peut pas être rappelée comme une simple file serveur.
        with db.transaction() as conn:
            auth.set_password(conn, uid, PASSWORD, must_change=False)
        self.assertEqual(operator.login('perm-reset', PASSWORD).status_code, 302)
        self.post(operator, f'/parc/{self.dev_id}/action', {'action': 'uninstall'})
        self.assertEqual(self.agent.checkin().get_json()['commands'][0]['action'], 'uninstall')
        self.post(self.admin, f'/comptes/{uid}/reinitialiser')
        self.assertEqual(device(self.agent.uid)['state'], 'removing')

    def test_two_administrators_cannot_disable_each_other_concurrently_and_leave_no_admin(self):
        second = self.browser('admin-two', 'admin')
        with db.transaction() as conn:
            first_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        second_id = self.uid('admin-two')
        requests = [(self.admin, second_id, self.admin.token('/compte')), (second, first_id, second.token('/compte'))]
        barrier = threading.Barrier(2)
        def submit(item):
            browser, target, token = item
            barrier.wait(timeout=5)
            return browser.c.post(f'/comptes/{target}/modifier', data={'csrf': token, 'role': 'admin', 'disabled': '1'}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, requests))
        self.assertTrue(all(status in (302, 403) for status in statuses), statuses)
        with db.transaction() as conn:
            self.assertEqual(auth.active_admins(conn), 1)

    def test_malformed_and_unknown_role_definitions_never_grant_access(self):
        role = self.profile('broken', [])
        browser = self.browser('broken', role)
        for raw in ('["*","fleet.remove"]', '{"fleet.view": true}', 'broken-json'):
            with db.transaction() as conn:
                conn.execute('UPDATE access_roles SET permissions=? WHERE id=?', (raw, role))
            self.assertEqual(browser.c.get('/parc').status_code, 403)
        with db.transaction() as conn:
            conn.execute("UPDATE users SET role='nonexistent' WHERE username='perm-broken'")
        self.assertEqual(browser.c.get('/parc').status_code, 403)
        self.assertEqual(browser.c.get('/compte').status_code, 200)

    def test_existing_accounts_keep_their_roles_and_passwords_during_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'portal.db')
            with sqlite3.connect(path) as conn:
                for script in db.MIGRATIONS[:-1]:
                    conn.executescript(script)
                conn.execute(f'PRAGMA user_version={len(db.MIGRATIONS) - 1}')
                for name, role in [('old-admin', 'admin'), ('old-viewer', 'viewer')]:
                    conn.execute('INSERT INTO users (username,password_hash,role) VALUES (?,?,?)', (name, 'unchanged-hash', role))
            with mock.patch.object(config, 'DB_PATH', str(path)), mock.patch.object(config, 'STATE', directory):
                self.assertEqual(db.migrate(), len(db.MIGRATIONS))
                db.migrate()
                with db.transaction() as conn:
                    self.assertEqual([(r['username'], r['password_hash'], r['role']) for r in conn.execute('SELECT * FROM users ORDER BY username')],
                                     [('old-admin', 'unchanged-hash', 'admin'), ('old-viewer', 'unchanged-hash', 'viewer')])
                    self.assertEqual(permissions.role_permissions(conn, 'admin'), set(permissions.PERMISSIONS))
                    self.assertEqual(permissions.role_permissions(conn, 'viewer'), permissions.VIEWER)
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM access_roles').fetchone()[0], len(permissions.DEFAULTS))

    def test_permission_specific_pages_do_not_leak_other_sections_or_controls(self):
        auditor = self.browser('audit', 'auditor')
        self.assertEqual(auditor.c.get('/journal').status_code, 200)
        page = auditor.c.get('/comptes').get_data(as_text=True)
        self.assertNotIn('Nouveau compte', page)
        self.assertNotIn('>Réinitialiser<', page)
        self.assertNotIn('>Supprimer<', page)
        editor = auditor.c.get('/comptes/profils/admin').get_data(as_text=True)
        self.assertNotIn('>Enregistrer le profil<', editor)
        self.assertNotIn('>Dupliquer ce profil<', editor)
        self.assertEqual(self.post(auditor, '/comptes/profils/ajouter', {'label': 'Perm test audit'}).status_code, 403)
        scanner = self.browser('no-dashboard', self.profile('only-fleet', ['fleet.scan']))
        page = scanner.c.get('/parc').get_data(as_text=True)
        for path in ('/windows-apps', '/linux', '/journal', '/comptes'):
            self.assertNotIn('href="' + path + '"', page)
        self.assertIn('Mes droits', scanner.c.get('/compte').get_data(as_text=True))

    def test_remote_sessions_are_pruned_when_a_permission_is_removed_outside_http(self):
        role = self.profile('prune', ['remote.terminal'])
        browser = self.browser('prune', role)
        response = self.post(browser, f'/parc/{self.dev_id}/session', {'kind': 'terminal'})
        sid = response.headers['Location'].rsplit('/', 1)[1]
        with db.transaction() as conn:
            conn.execute("UPDATE access_roles SET permissions='[\"fleet.view\"]' WHERE id=?", (role,))
        with fixture.loco.app.test_request_context():
            remote._prune()
            db.db().commit()
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT state FROM remote_sessions WHERE id=?', (sid,)).fetchone()[0], 'closed')
