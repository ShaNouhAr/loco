"""Console sans X11 : vrai flux noVNC/RFB, PTY, ANSI, touches et fin du shell."""
import importlib.util
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_portal as fixture
from loco import fleet
from test_desktop_encoding import decode

ROOT = Path(fixture.ROOT)
sys.path.insert(0, str(ROOT / 'portal/agents/console'))
if os.name == "posix":
    import console_rfb
    import pyte


@unittest.skipUnless(os.name == 'posix', 'PTY Linux')
class TestTextConsole(unittest.TestCase):
    def test_vt_screen_renders_ansi_accents_and_box_glyphs_without_losing_cursor(self):
        screen = console_rfb.TextScreen(20, 5)
        stream = pyte.ByteStream(screen)
        stream.feed('\x1b[2J\x1b[H\x1b[31mété\x1b[0m ──┐'.encode())
        self.assertEqual(screen.display[0][:7], 'été ──┐')
        before = screen.render()
        self.assertEqual(len(before), 20 * 8 * 5 * 16 * 4)
        self.assertTrue(any(before))
        # Curseur déplacé : son ancienne position doit aussi être restaurée.
        stream.feed(b'\x1b[3;2H')
        self.assertNotEqual(screen.render(), before)
        self.assertEqual(screen.render(), screen.render())
        stream.feed(b'\x1b[6n')
        self.assertEqual(screen.responses, b'\x1b[3;2R')
        self.assertEqual(console_rfb.key_bytes(ord('c'), {0xffe3}), b'\x03')
        self.assertEqual(console_rfb.key_bytes(0xff52, set()), b'\x1b[A')
        self.assertEqual(console_rfb.key_bytes(0x010000e9, set()), 'é'.encode())

    def test_standalone_agent_serves_a_real_shell_as_tight_rfb_and_closes_it(self):
        generated = fleet._agent_source('loco-client.py')
        self.assertNotIn('@CONSOLE_LIB@', generated)
        self.assertEqual(generated, fleet._agent_source('loco-client.py'), 'Agent reproductible')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'agent.py')
            path.write_text(generated)
            process = subprocess.Popen([sys.executable, '-c', 'import runpy,sys; runpy.run_path(sys.argv[1])["text_console"]()', str(path)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(self.cleanup, process)
            def read(n):
                result = b''
                until = time.monotonic() + 10
                while len(result) < n:
                    remaining = until - time.monotonic()
                    if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                        self.fail('Délai RFB dépassé')
                    part = os.read(process.stdout.fileno(), n - len(result))
                    if not part:
                        self.fail('Fin RFB inattendue : ' + process.stderr.read().decode())
                    result += part
                return result
            def send(data):
                process.stdin.write(data)
                process.stdin.flush()
            def update(request=True):
                if request:
                    send(b'\x03\x01' + struct.pack('>HHHH', 0, 0, 1024, 640))
                header = read(4)
                frame = bytearray(header)
                for _ in range(struct.unpack_from('>H', header, 2)[0]):
                    rect = read(12); frame.extend(rect)
                    w, h = struct.unpack_from('>HH', rect, 4)
                    control = read(1); frame.extend(control)
                    if control == b'\x80':
                        frame.extend(read(3))
                    else:
                        self.assertEqual(control, b'\x01')
                        if w * h * 3 < 12:
                            frame.extend(read(w * h * 3))
                        else:
                            size = read(1)[0]; frame.append(size)
                            length = size & 127
                            if size & 128:
                                size = read(1)[0]; frame.append(size); length |= (size & 127) << 7
                                if size & 128:
                                    size = read(1)[0]; frame.append(size); length |= size << 14
                            frame.extend(read(length))
                return bytes(frame)
            self.assertEqual(read(12), b'RFB 003.008\n')
            send(b'RFB 003.008\n'); self.assertEqual(read(2), b'\x01\x01')
            send(b'\x01'); self.assertEqual(read(4), b'\0' * 4)
            send(b'\x01'); init = read(24)
            self.assertEqual(struct.unpack_from('>HH', init), (1024, 640))
            self.assertIn(b'loco-console root@', read(struct.unpack_from('>I', init, 20)[0]))
            send(b'\x02\0\0\x01' + struct.pack('>i', 7))
            initial = update()
            pixels, _ = decode(initial, 1024, 640)
            # Le shell ne reçoit que des événements clavier RFB, comme le navigateur noVNC.
            command = "printf '\\033[2J\\033[H%s\\n' 'CONSOLE_RFB_OK'; stty size\r"
            for ch in command:
                sym = 0xff0d if ch == '\r' else ord(ch)
                send(b'\x04\x01\0\0' + struct.pack('>I', sym))
                send(b'\x04\0\0\0' + struct.pack('>I', sym))
            until = time.monotonic() + 5
            found = False
            expected = console_rfb.TextScreen()
            expected_stream = pyte.ByteStream(expected)
            expected_stream.feed(b'CONSOLE_RFB_OK')
            expected.cursor.hidden = True
            expected_pixels = expected.render()
            while time.monotonic() < until:
                pixels, _ = decode(update(), 1024, 640, pixels)
                if pixels[:1024 * 16 * 4] == expected_pixels[:1024 * 16 * 4]:
                    found = True
                    break
                time.sleep(0.05)
            self.assertTrue(found, 'Le texte du vrai shell n’apparaît pas dans les pixels RFB')
            # Négociation du redimensionnement : ACK séparé, nouveau PTY et nouveaux pixels.
            send(b'\x02\0\0\x02' + struct.pack('>ii', 7, -308))
            send(b'\xfb\0' + struct.pack('>HHBBIHHHHI', 1001, 577, 1, 0, 0, 0, 0, 1001, 577, 0))
            ack = read(36)
            self.assertEqual(struct.unpack_from('>HHHHi', ack, 4), (1, 0, 1001, 577, -308))
            pixels, _ = decode(update(), 1001, 577)
            self.assertEqual(len(pixels), 1001 * 577 * 4)
            # Rejet borné : conserver le flux et la résolution après un écran invalide.
            send(b'\xfb\0' + struct.pack('>HHBBIHHHHI', 0, 577, 1, 0, 0, 0, 0, 0, 577, 0))
            ack = read(36)
            self.assertEqual(struct.unpack_from('>HHHH', ack, 4), (1, 3, 1001, 577))
            command = "printf '\\033[2J\\033[H'; stty size\r"
            for ch in command:
                sym = 0xff0d if ch == '\r' else ord(ch)
                send(b'\x04\x01\0\0' + struct.pack('>I', sym))
                send(b'\x04\0\0\0' + struct.pack('>I', sym))
            expected = console_rfb.TextScreen(125, 36)
            expected.resize_pixels(1001, 577)
            pyte.ByteStream(expected).feed(b'36 125')
            expected.cursor.hidden = True
            expected_row = expected.render()[:1001 * 16 * 4]
            until = time.monotonic() + 5
            while time.monotonic() < until:
                pixels, _ = decode(update(), 1001, 577, pixels)
                if pixels[:1001 * 16 * 4] == expected_row:
                    break
                time.sleep(0.05)
            else:
                self.fail('Le vrai PTY ne reçoit pas les dimensions adaptées')
            # Plusieurs demandes arrivées ensemble donnent une image ; conserver le rafraîchissement complet.
            request = b'\x03\x01' + struct.pack('>HHHH', 0, 0, 1001, 577)
            full = b'\x03\0' + struct.pack('>HHHH', 0, 0, 1001, 577)
            send(request + full + request)
            ack = read(36)
            self.assertEqual(struct.unpack_from('>HHHH', ack, 4), (0, 0, 1001, 577))
            pixels, count = decode(update(request=False), 1001, 577, pixels)
            self.assertEqual(count, 40)
            # La commande suivante ne doit ni être absorbée ni recevoir une ancienne image en guise d'ACK.
            send(b'\xfb\0' + struct.pack('>HHBBIHHHHI', 1024, 640, 1, 0, 0, 0, 0, 1024, 640, 0))
            self.assertEqual(struct.unpack_from('>HHHH', read(36), 4), (1, 0, 1024, 640))
            process.stdin.close()
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())

    @staticmethod
    def cleanup(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()

    def test_no_graphical_session_requires_explicit_terminal_permission_from_server(self):
        spec = importlib.util.spec_from_file_location('console_test_agent', ROOT / 'portal/agents/loco-client.py')
        agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
        with mock.patch.object(agent, 'x11_environment', side_effect=RuntimeError('Aucune session X11')), mock.patch.object(agent, 'text_console') as fallback:
            with self.assertRaisesRegex(RuntimeError, 'droit Terminal distant'):
                agent.linux_desktop()
            fallback.assert_not_called()
            agent.linux_desktop(terminal_fallback=True)
            fallback.assert_called_once()
        with mock.patch.object(agent, 'x11_environment', side_effect=RuntimeError('Bureau Wayland')), mock.patch.object(agent, 'text_console') as fallback:
            with self.assertRaisesRegex(RuntimeError, 'Wayland'):
                agent.linux_desktop(terminal_fallback=True)
            fallback.assert_not_called()
