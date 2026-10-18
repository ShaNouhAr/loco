"""SSO : vrais JWT signés, approbation obligatoire, isolation et révocation des identités."""
import hashlib
import json
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa

import test_portal as fixture
from loco import config, db, permissions, sso
from loco._vendor import jwt


class TestSSO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid='key-1', alg='RS256', use='sig')

    def setUp(self):
        self.admin = fixture.Browser()
        self.assertEqual(self.admin.login('admin', fixture.ADMIN_PW).status_code, 302)
        self.meta = {'issuer': 'https://idp.test', 'authorization_endpoint': 'https://idp.test/authorize',
                     'token_endpoint': 'https://idp.test/token', 'jwks_uri': 'https://idp.test/jwks'}
        self.addCleanup(self.clean)
        self.provider = self.add_provider()
        self.browser = fixture.Browser()
        self.subject = 'immutable-user-123'
        self.patch_claims = {}
        self.signature_key = self.key
        self.network_calls = []
        self.network = mock.patch.object(sso, '_json', side_effect=self.provider_response)
        self.network.start()
        self.addCleanup(self.network.stop)

    def clean(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM users WHERE auth_source='sso'")
            conn.execute("DELETE FROM sso_providers")

    def add_provider(self, preset='oidc', issuer='https://idp.test'):
        response = self.admin.post('/comptes/sso/ajouter', {'label': 'Test SSO', 'preset': preset, 'issuer': issuer,
                                  'client_id': 'test-client', 'client_secret': 'secret-never-in-output', 'enabled': '1'}, page='/compte')
        self.assertEqual(response.status_code, 302)
        with db.transaction() as conn:
            return conn.execute('SELECT * FROM sso_providers ORDER BY rowid DESC LIMIT 1').fetchone()

    def provider_response(self, url, data=None, headers=None):
        self.network_calls.append((url, data, headers))
        if url.endswith('/.well-known/openid-configuration'):
            return self.meta
        if url == self.meta['token_endpoint']:
            params = parse_qs(data.decode())
            self.assertEqual(params['redirect_uri'], [config.HTTPS_URL + '/sso/' + self.provider['id'] + '/retour'])
            self.assertEqual(params['code_verifier'], [self.verifier])
            self.assertIn('Authorization', headers)
            claims = {'iss': self.meta['issuer'], 'aud': 'test-client', 'sub': self.subject, 'nonce': self.nonce,
                      'exp': int(time.time()) + 120, 'iat': int(time.time()), 'name': 'Admin apparent', 'email': 'admin@example.test',
                      **self.patch_claims}
            return {'id_token': jwt.encode(claims, self.signature_key, algorithm='RS256', headers={'kid': 'key-1'})}
        if url == self.meta['jwks_uri']:
            return {'keys': [self.jwk]}
        raise AssertionError(url)

    def begin(self, browser=None):
        browser = browser or self.browser
        response = browser.c.get('/sso/' + self.provider['id'] + '/connexion')
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlsplit(response.headers['Location']).query)
        self.nonce = params.get('nonce', [''])[0]
        self.state = params['state'][0]
        self.assertEqual(params['code_challenge_method'], ['S256'])
        with db.transaction() as conn:
            payload = conn.execute('SELECT payload FROM sso_flows WHERE state_hash=?', (hashlib.sha256(self.state.encode()).hexdigest(),)).fetchone()[0]
            self.verifier = json.loads(sso._open(payload))['verifier']
        self.assertNotIn(self.verifier, response.headers['Location'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        return '/sso/' + self.provider['id'] + '/retour?state=' + self.state + '&code=test-code'

    def identity(self):
        with db.transaction() as conn:
            return conn.execute("SELECT * FROM users WHERE auth_source='sso'").fetchone()

    def approve(self, role='viewer'):
        row = self.identity()
        response = self.admin.post('/comptes/' + str(row['id']) + '/approuver', {'role': role}, page='/compte')
        self.assertEqual(response.status_code, 302)

    def test_new_identity_has_no_session_or_access_until_explicit_approval_and_cannot_take_admin_account(self):
        path = self.begin()
        response = self.browser.c.get(path)
        self.assertEqual(response.status_code, 202)
        self.assertIn("approbation", response.get_data(as_text=True))
        row = self.identity()
        self.assertEqual((row['role'], row['pending'], row['disabled']), ('pending', 1, 1))
        with db.transaction() as conn:
            self.assertFalse(permissions.role_permissions(conn, row['role']))
            self.assertEqual(conn.execute("SELECT role,auth_source FROM users WHERE username='admin'").fetchone()[:], ('admin', 'local'))
        with self.browser.c.session_transaction() as sess:
            self.assertNotIn('uid', sess)
        for page in ('/', '/parc', '/comptes', '/compte', '/comptes/sso'):
            self.assertEqual(self.browser.c.get(page).status_code, 302, page)
        self.assertEqual(self.browser.c.get(path).status_code, 400, 'État réutilisé')
        # La simple édition du compte ne permet pas de contourner l'approbation.
        self.assertEqual(self.admin.post('/comptes/' + str(row['id']) + '/modifier', {'role': 'admin', 'disabled': '0'}, page='/compte').status_code, 400)
        self.approve()
        completed = self.browser.c.get(self.begin())
        self.assertEqual(completed.status_code, 200)
        self.assertIn('sso-complete.js', completed.get_data(as_text=True))
        self.assertIn('SameSite=Strict', '\n'.join(completed.headers.getlist('Set-Cookie')))
        self.assertEqual(self.browser.c.get('/parc').status_code, 200)
        self.assertEqual(self.browser.c.get('/comptes').status_code, 403)
        self.assertEqual(self.browser.c.post('/compte', data={'csrf': self.browser.token('/compte'), 'current': 'x'}).status_code, 403)
        self.assertNotIn('name="current"', self.browser.c.get('/compte').get_data(as_text=True))
        # Les mots de passe des identités fédérées ne sont pas réinitialisables localement.
        self.assertEqual(self.admin.post('/comptes/' + str(row['id']) + '/reinitialiser', page='/compte').status_code, 403)

    def test_approval_needs_its_own_permission_and_snapshot_drops_login_transactions(self):
        from pathlib import Path
        import sys
        import tempfile
        sys.path.insert(0, str(Path(fixture.ROOT, 'portal')))
        from locosnapshot import snapshot
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        self.begin()
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory, 'copy.db'))
            snapshot(config.DB_PATH, destination)
            import sqlite3
            with sqlite3.connect(destination) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sso_flows').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sso_identities').fetchone()[0], 1)
        row = self.identity()
        with self.browser.c.session_transaction() as sess:
            sess.update(uid=row['id'], pwv=row['pw_version'])
        self.assertEqual(self.browser.c.get('/compte').status_code, 302)
        from loco import auth
        with db.transaction() as conn:
            auth.create_user(conn, 'sso-test-reader', 'Sso-Reader-2026', 'viewer', must_change=False)
        try:
            reader = fixture.Browser()
            self.assertEqual(reader.login('sso-test-reader', 'Sso-Reader-2026').status_code, 302)
            self.assertEqual(reader.post('/comptes/' + str(row['id']) + '/approuver', {'role': 'admin'}, page='/compte').status_code, 403)
        finally:
            with db.transaction() as conn:
                conn.execute("DELETE FROM users WHERE username='sso-test-reader'")

    def test_wrong_browser_and_expired_flow_are_denied_before_contacting_token_endpoint(self):
        path = self.begin()
        self.network_calls.clear()
        self.assertEqual(fixture.Browser().c.get(path).status_code, 400)
        self.assertFalse(self.network_calls)
        with db.transaction() as conn:
            conn.execute('UPDATE sso_flows SET expires=0')
        self.assertEqual(self.browser.c.get(path).status_code, 400)
        self.assertFalse(self.network_calls)
        self.assertIsNone(self.identity())

    def test_real_signature_expiration_issuer_audience_nonce_and_azp_are_enforced(self):
        cases = [{'aud': 'other-client'}, {'nonce': 'wrong'}, {'iss': 'https://evil.test'}, {'exp': time.time() - 90},
                 {'aud': ['test-client', 'another']}, {'azp': 'other-client'}, {'iat': time.time() + 100}]
        for patch in cases:
            with self.subTest(patch=patch):
                self.patch_claims = patch
                self.assertEqual(self.browser.c.get(self.begin()).status_code, 400)
                self.assertIsNone(self.identity())
        self.patch_claims = {}
        self.signature_key = self.other_key
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 400)
        self.assertIsNone(self.identity())

    def test_sso_admin_cannot_remove_or_demote_the_last_local_administrator(self):
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        self.approve('admin')
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 200)
        with db.transaction() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        for path, values in [('modifier', {'role': 'viewer'}), ('supprimer', {})]:
            self.assertEqual(self.browser.post(f'/comptes/{uid}/{path}', values, page='/compte').status_code, 302)
            with db.transaction() as conn:
                row = conn.execute('SELECT role,disabled FROM users WHERE id=?', (uid,)).fetchone()
                self.assertEqual(tuple(row), ('admin', 0))

    def test_provider_disabling_revokes_web_sessions_and_blocks_inflight_login(self):
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        self.approve()
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 200)
        self.assertEqual(self.browser.c.get('/parc').status_code, 200)
        pending_browser = fixture.Browser()
        inflight = self.begin(pending_browser)
        self.assertEqual(self.admin.post('/comptes/sso/' + self.provider['id'] + '/modifier',
                         {'label': 'Test SSO', 'issuer': self.provider['issuer'], 'client_id': 'test-client'}, page='/compte').status_code, 302)
        self.assertEqual(self.browser.c.get('/parc').status_code, 302)
        self.assertEqual(pending_browser.c.get(inflight).status_code, 404)

    def test_settings_never_return_secrets_and_issuer_changes_require_new_provider(self):
        page = self.admin.c.get('/comptes/sso').get_data(as_text=True)
        self.assertNotIn('secret-never-in-output', page)
        self.assertNotIn(self.provider['client_secret'], page)
        self.assertNotEqual(self.provider['client_secret'], 'secret-never-in-output')
        with db.transaction() as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM audit WHERE detail LIKE '%secret-never-in-output%'").fetchone())
        self.admin.post('/comptes/sso/' + self.provider['id'] + '/modifier',
                        {'label': 'changed', 'issuer': 'https://evil.test', 'client_id': 'test-client', 'enabled': '1'}, page='/compte')
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT issuer FROM sso_providers WHERE id=?', (self.provider['id'],)).fetchone()[0], 'https://idp.test')
        page = fixture.Browser().c.get('/login').get_data(as_text=True)
        self.assertIn('Continuer avec Test SSO', page)

    def test_same_email_with_different_subject_or_provider_stays_separate(self):
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        self.subject = 'second-user'
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        self.subject = 'immutable-user-123'
        self.provider = self.add_provider()
        self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        with db.transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE auth_source='sso'").fetchone()[0], 3)

    def test_github_oauth_uses_numeric_subject_without_oidc_assumption(self):
        self.provider = self.add_provider('github', 'https://github.com')
        def github(url, data=None, headers=None):
            if url.endswith('/access_token'):
                self.assertEqual(parse_qs(data.decode())['code_verifier'], [self.verifier])
                return {'access_token': 'github-test-token'}
            if url == 'https://api.github.com/user':
                self.assertEqual(headers['Authorization'], 'Bearer github-test-token')
                return {'id': 123456, 'login': 'octocat', 'email': None}
            raise AssertionError(url)
        self.network.stop()
        with mock.patch.object(sso, '_json', side_effect=github):
            self.assertEqual(self.browser.c.get(self.begin()).status_code, 202)
        with db.transaction() as conn:
            self.assertEqual(conn.execute('SELECT subject FROM sso_identities').fetchone()[0], '123456')

    def test_navigation_places_fleet_immediately_after_overview(self):
        page = self.admin.c.get('/').get_data(as_text=True)
        self.assertLess(page.index("<span>Vue d'ensemble</span>"), page.index('<span>Mon parc</span>'))
        self.assertLess(page.index('<span>Mon parc</span>'), page.index('<span>Linux</span>'))
