"""Consoles de l'agent : isolation, flux binaires acquittés, révocation et délais de fermeture."""
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import test_portal as fixture
from test_fleet import FleetTest, Agent, WINDOWS, LINUX, device
from loco import auth
from loco.db import transaction


class TestRemote(FleetTest):
    def setUp(self):
        super().setUp()
        self.agent = Agent(WINDOWS, '192.168.43.60')
        self.agent.enroll()
        self.dev_id = device(self.agent.uid)['id']

    def open(self, kind='terminal', browser=None):
        browser = browser or self.admin
        response = browser.post(f'/parc/{self.dev_id}/session', {'kind': kind})
        self.assertEqual(response.status_code, 302)
        return response.headers['Location'].rsplit('/', 1)[1]

    def exchange(self, sessions=None, agent=None):
        agent = agent or self.agent
        return agent.post('/agent/api/remote', {'sessions': sessions or []}, agent.token)

    def io(self, sid, data=None, browser=None):
        return (browser or self.admin).post(f'/parc/session/{sid}/io', data or {})

    def test_terminal_and_desktop_relay_binary_bytes_without_replaying_input(self):
        for kind in ('terminal', 'desktop'):
            sid = self.open(kind)
            self.assertEqual(self.admin.c.get('/parc/session/' + sid).status_code, 200)
            binary = base64.b64encode(b'\0\xff\x1b[31mtest\r').decode()
            self.assertEqual(self.io(sid, {'seq': 1, 'data': binary}).status_code, 200)
            # Même requête après perte de réponse : un seul paquet, donc une seule exécution.
            self.assertEqual(self.io(sid, {'seq': 1, 'data': binary}).status_code, 200)
            descriptor = next(d for d in self.exchange().get_json()['sessions'] if d['id'] == sid)
            self.assertEqual(descriptor['packets'], [{'seq': 1, 'data': binary}])
            response = self.exchange([{'id': sid, 'ack': 1, 'seq': 1, 'data': binary}])
            descriptor = next(d for d in response.get_json()['sessions'] if d['id'] == sid)
            self.assertEqual(descriptor['packets'], [])
            self.assertEqual(descriptor['ack'], 1)
            # Sortie renvoyée une seule fois après acquittement, jamais archivée dans le journal.
            self.exchange([{'id': sid, 'ack': 1, 'seq': 1, 'data': binary}])
            self.assertEqual(self.io(sid).get_json()['packets'], [{'seq': 1, 'data': binary}])
            self.assertEqual(self.io(sid, {'ack': 1}).get_json()['packets'], [])
            self.assertEqual(self.io(sid, {'close': 1}).get_json()['state'], 'closed')
            with transaction() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM remote_packets WHERE session_id=?', (sid,)).fetchone()[0], 0)
                details = '\n'.join(r[0] for r in conn.execute('SELECT detail FROM audit'))
                self.assertNotIn(binary, details)

    def test_viewer_anonymous_and_another_login_cannot_control_a_session(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='remote-viewer'")
            auth.create_user(conn, 'remote-viewer', 'Remote-Viewer-2026', 'viewer', must_change=False)
        viewer = fixture.Browser(); viewer.login('remote-viewer', 'Remote-Viewer-2026')
        sid = self.open()
        self.assertEqual(viewer.post(f'/parc/{self.dev_id}/session', {'kind': 'terminal'}).status_code, 403)
        self.assertEqual(viewer.c.get('/parc/session/' + sid).status_code, 403)
        self.assertEqual(self.io(sid, browser=viewer).status_code, 403)
        other = fixture.Browser(); other.login('admin', fixture.ADMIN_PW)
        self.assertEqual(other.c.get('/parc/session/' + sid).status_code, 404)
        self.assertEqual(self.io(sid, browser=other).status_code, 404)
        self.assertEqual(fixture.Browser().c.get('/parc/session/' + sid).status_code, 302)
        self.assertEqual(self.admin.c.post('/parc/session/' + sid + '/io').status_code, 400)

    def test_an_agent_cannot_get_or_write_another_devices_session(self):
        sid = self.open()
        other = Agent(LINUX, '192.168.43.61'); other.enroll()
        self.assertEqual(self.exchange(agent=other).get_json()['sessions'], [])
        self.exchange([{'id': sid, 'seq': 1, 'data': base64.b64encode(b'foreign').decode()}], agent=other)
        self.assertEqual(self.io(sid).get_json()['packets'], [])
        self.assertEqual(self.agent.post('/agent/api/remote', {}, 'invalid').status_code, 401)

    def test_logout_revokes_session_and_erases_pending_keystrokes(self):
        sid = self.open()
        self.io(sid, {'seq': 1, 'data': base64.b64encode(b'secret command').decode()})
        self.admin.post('/logout')
        descriptor = next(d for d in self.exchange([{'id': sid}]).get_json()['sessions'] if d['id'] == sid)
        self.assertEqual(descriptor['state'], 'closed')
        self.assertEqual(descriptor['packets'], [])

    def test_role_password_device_removal_and_timeouts_revoke_access(self):
        for field, value in [('browser_seen', time.time() - 61), ('expires', time.time() - 1),
                             ('agent_seen', time.time() - 31), ('created', time.time() - 31), ('pw_version', -1)]:
            sid = self.open()
            with transaction() as conn:
                conn.execute(f'UPDATE remote_sessions SET {field}=? WHERE id=?', (value, sid))
            response = self.io(sid)
            if field == 'pw_version':
                self.assertEqual(response.status_code, 404)
            else:
                self.assertEqual(response.get_json()['state'], 'closed', field)
        sid = self.open()
        with transaction() as conn:
            conn.execute("UPDATE devices SET state='removing' WHERE id=?", (self.dev_id,))
        self.assertEqual(self.io(sid).get_json()['state'], 'closed')

    def test_invalid_sequences_and_oversized_data_are_rejected(self):
        sid = self.open()
        encoded = base64.b64encode(b'test').decode()
        for data, status in [({'seq': 2, 'data': encoded}, 409), ({'seq': 1, 'data': '!not-base64'}, 400),
                             ({'ack': 1}, 400), ({'ack': -1}, 400), ({'cols': 100000}, 400),
                             ({'seq': 1, 'data': base64.b64encode(b'x' * (256 * 1024 + 1)).decode()}, 413)]:
            self.assertEqual(self.io(sid, data).status_code, status, json.dumps({k: str(v)[:20] for k, v in data.items()}))
        self.open('desktop')
        self.open('terminal')
        self.assertEqual(self.admin.post(f'/parc/{self.dev_id}/session', {'kind': 'terminal'}).status_code, 409)
        self.assertEqual(self.admin.post(f'/parc/{self.dev_id}/session', {'kind': 'arbitrary-command'}).status_code, 400)

    def test_backups_exclude_sessions_and_pending_input_without_touching_live_connections(self):
        sid = self.open()
        secret = b'PRIVATE_INPUT_MUST_NOT_BE_BACKED_UP'
        self.io(sid, {'seq': 1, 'data': base64.b64encode(secret).decode()})
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp, 'portal.db')
            # Même mode isolé que le service OpenRC : le dossier du script n'est pas dans sys.path.
            script = ("import importlib.util,sys; s=importlib.util.spec_from_file_location('locoadmin',sys.argv[1]); "
                      "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.locosnapshot.snapshot(sys.argv[2],sys.argv[3])")
            subprocess.run([sys.executable, '-I', '-c', script,
                            str(Path(fixture.ROOT, 'portal', 'locoadmin.py')), fixture.config.DB_PATH, str(backup)],
                           check=True, capture_output=True, cwd=tmp)
            with sqlite3.connect(backup) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM remote_sessions').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM remote_packets').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sso_flows').fetchone()[0], 0)
            self.assertNotIn(secret, backup.read_bytes())
        descriptor = next(d for d in self.exchange().get_json()['sessions'] if d['id'] == sid)
        self.assertEqual(base64.b64decode(descriptor['packets'][0]['data']), secret)

    def test_reloading_a_page_closes_the_old_stream_instead_of_replaying_its_input(self):
        sid = self.open()
        self.assertEqual(self.admin.c.get('/parc/session/' + sid).status_code, 200)
        self.io(sid, {'seq': 1, 'data': base64.b64encode(b'command').decode()})
        page = self.admin.c.get('/parc/session/' + sid)
        self.assertIn('Nouvelle session', page.get_data(as_text=True))
        self.assertEqual(self.io(sid).get_json()['state'], 'closed')

    def test_agent_restart_closes_the_old_stream_and_discards_pending_input(self):
        sid = self.open()
        self.exchange([{'id': sid, 'state': 'active'}])
        self.io(sid, {'seq': 1, 'data': base64.b64encode(b'never replay this command').decode()})
        self.assertEqual(self.exchange().get_json()['sessions'], [])
        self.assertEqual(self.io(sid).get_json()['state'], 'closed')
        with transaction() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM remote_packets WHERE session_id=?', (sid,)).fetchone()[0], 0)

    def test_new_tab_is_a_console_without_portal_navigation_and_keeps_access_checks(self):
        response = self.admin.post(f'/parc/{self.dev_id}/session', {'kind': 'desktop', 'view': 'console'})
        location = response.headers['Location']
        self.assertIn('?view=console', location)
        page = self.admin.c.get(location).get_data(as_text=True)
        self.assertIn('remote-detached', page)
        self.assertIn('id="remote-screen"', page)
        self.assertIn('Taille réelle (1:1)', page)
        self.assertNotIn('aria-label="Navigation principale"', page)
        other = fixture.Browser(); other.login('admin', fixture.ADMIN_PW)
        self.assertEqual(other.c.get(location).status_code, 404)

    def test_desktop_page_offers_the_command_panel(self):
        sid = self.open('desktop')
        page = self.admin.c.get(f'/parc/session/{sid}').get_data(as_text=True)
        for text in ('id="remote-toolbar"', 'data-keys="ctrl alt delete"', 'data-keys="super r"', 'data-keys="super l"',
                     'data-keys="ctrl shift escape"', 'data-hold="ctrl"', 'data-hold="super"', 'id="remote-type"'):
            self.assertIn(text, page)
        self.assertIn('>Win<', page)                                       # libellés Windows sur un poste Windows
        terminal = self.admin.c.get(f"/parc/session/{self.open('terminal')}").get_data(as_text=True)
        self.assertNotIn('id="remote-toolbar"', terminal)                 # pas de panneau pour le terminal
        script = (Path(__file__).resolve().parents[1] / 'portal/static/remote.js').read_text()
        self.assertIn('rfb.sendCtrlAltDel()', script)

    def test_browser_drains_a_large_frame_in_one_batch_without_raising_the_packet_limit(self):
        sid = self.open('desktop')
        blocks = [bytes([n]) * (256 * 1024) for n in range(1, 6)]
        for seq, block in enumerate(blocks, 1):
            self.assertEqual(self.exchange([{'id': sid, 'seq': seq, 'data': base64.b64encode(block).decode()}]).status_code, 200)
        response = self.io(sid).get_json()
        self.assertEqual([p['seq'] for p in response['packets']], [1, 2, 3, 4])
        self.assertEqual(b''.join(base64.b64decode(p['data']) for p in response['packets']), b''.join(blocks[:4]))
        response = self.io(sid, {'ack': 4}).get_json()
        self.assertEqual([p['seq'] for p in response['packets']], [5])
        self.assertEqual(base64.b64decode(response['packets'][0]['data']), blocks[4])

    def test_slow_browser_backpressure_retries_the_same_packet_without_losing_bytes(self):
        sid = self.open('desktop')
        block = base64.b64encode(b'x' * (256 * 1024)).decode()
        for seq in range(1, 9):
            self.assertEqual(self.exchange([{'id': sid, 'seq': seq, 'data': block}]).status_code, 200)
        pending = [{'id': sid, 'seq': 9, 'data': block}]
        self.assertEqual(self.exchange(pending).status_code, 429)
        first = self.io(sid).get_json()['packets']
        self.assertEqual([p['seq'] for p in first], [1, 2, 3, 4])
        self.io(sid, {'ack': 4})
        for _ in range(2):
            response = self.exchange(pending)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['sessions'][0]['ack'], 9)
        remaining = self.io(sid, {'ack': 4}).get_json()['packets']
        self.assertEqual([p['seq'] for p in remaining], [5, 6, 7, 8])
        last = self.io(sid, {'ack': 8}).get_json()['packets']
        self.assertEqual([p['seq'] for p in last], [9])
        self.assertEqual(base64.b64decode(last[0]['data']), b'x' * (256 * 1024))
