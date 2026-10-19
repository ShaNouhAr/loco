"""Console PTY rendue en RFB, sans X11 ni service réseau. Python >= 3.6.

Le serveur doit avoir autorisé remote.terminal avant de permettre ce repli root.
pyte gère VT/ANSI ; les glyphes font8x8 sont dans le domaine public.
"""
import fcntl
import json
import os
import pkgutil
import pty
import pwd
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import unicodedata

import pyte

COLORS = {"black": "000000", "red": "cc4444", "green": "55aa55", "brown": "aaaa55", "blue": "5588dd",
          "magenta": "bb66bb", "cyan": "55bbbb", "white": "cccccc", "brightblack": "777777",
          "brightred": "ff5555", "brightgreen": "55ff55", "brightbrown": "ffff55", "brightblue": "7799ff",
          "brightmagenta": "ff77ff", "brightcyan": "77ffff", "brightwhite": "ffffff"}
KEYS = {0xff08: b"\x7f", 0xff09: b"\t", 0xfe20: b"\x1b[Z", 0xff0d: b"\r", 0xff1b: b"\x1b",
        0xff50: b"\x1b[1~", 0xff51: b"\x1b[D", 0xff52: b"\x1b[A", 0xff53: b"\x1b[C", 0xff54: b"\x1b[B",
        0xff55: b"\x1b[5~", 0xff56: b"\x1b[6~", 0xff57: b"\x1b[4~", 0xff63: b"\x1b[2~", 0xffff: b"\x1b[3~",
        0xffbe: b"\x1b[[A", 0xffbf: b"\x1b[[B", 0xffc0: b"\x1b[[C", 0xffc1: b"\x1b[[D", 0xffc2: b"\x1b[[E",
        0xffc3: b"\x1b[17~", 0xffc4: b"\x1b[18~", 0xffc5: b"\x1b[19~", 0xffc6: b"\x1b[20~",
        0xffc7: b"\x1b[21~", 0xffc8: b"\x1b[23~", 0xffc9: b"\x1b[24~"}


class TextScreen(pyte.Screen):
    def __init__(self, columns=128, lines=40):
        super().__init__(columns, lines)
        self.font = json.loads(pkgutil.get_data("console_rfb", "font.json").decode())
        self.pixel_width = columns * 8
        self.pixels = bytearray(columns * 8 * lines * 16 * 4)
        self.cells = {}
        self.responses = bytearray()

    def resize_pixels(self, width, height):
        self.resize(columns=max(20, width // 8), lines=max(5, height // 16))
        self.pixel_width = width
        self.pixels = bytearray(b"\x08\x08\x08\0" * (width * height))
        self.cells.clear()

    def write_process_input(self, data):
        # Réponses aux requêtes VT100 (position du curseur, identification, etc.).
        if len(self.responses) < 4096:
            self.responses.extend(data.encode())

    @staticmethod
    def color(value, default):
        value = COLORS.get(value, value)
        if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
            value = default
        return bytes((int(value[4:6], 16), int(value[2:4], 16), int(value[:2], 16), 0))

    def render(self):
        width = self.pixel_width
        cursor = (self.cursor.x, self.cursor.y) if not self.cursor.hidden else (-1, -1)
        for y in range(self.lines):
            for x in range(self.columns):
                char = self.buffer[y][x]
                at_cursor = (x, y) == cursor
                signature = (char, at_cursor)
                if self.cells.get((x, y)) == signature:
                    continue
                self.cells[x, y] = signature
                fg, bg = self.color(char.fg, "dddddd"), self.color(char.bg, "080808")
                if char.reverse:
                    fg, bg = bg, fg
                glyph = unicodedata.normalize("NFC", char.data or " ")
                bits = self.font.get(str(ord(glyph[0])), self.font["63"])
                for row in range(16):
                    mask = bits[row // 2]
                    if char.bold:
                        mask |= (mask << 1) & 255
                    if char.underscore and row >= 14 or at_cursor and row >= 14:
                        mask = 255
                    line = b"".join(fg if mask & (1 << col) else bg for col in range(8))
                    pos = ((y * 16 + row) * width + x * 8) * 4
                    self.pixels[pos:pos + 32] = line
        self.dirty.clear()
        return bytes(self.pixels)


def key_bytes(sym, modifiers):
    if sym in KEYS:
        value = KEYS[sym]
    else:
        code = sym & 0xffffff if sym & 0xff000000 == 0x01000000 else sym
        if not (32 <= code <= 0x10ffff) or 0xff00 <= sym <= 0xffff or 0xd800 <= code <= 0xdfff:
            return b""
        if modifiers & {0xffe3, 0xffe4}:  # Ctrl
            if 0x40 <= code <= 0x7f:
                code &= 31
            elif code == 32:
                code = 0
        value = chr(code).encode("utf-8")
    if modifiers & {0xffe9, 0xffea}:  # Alt
        value = b"\x1b" + value
    return value


def serve(frame_encoder, inp=None, out=None):
    """Flux RFB privé stdin/stdout ; aucun socket d'écoute, aucune console physique modifiée."""
    inp, out = inp or sys.stdin.buffer, out or sys.stdout.buffer
    screen = TextScreen()
    stream = pyte.ByteStream(screen)
    master, slave = pty.openpty()
    process = None
    previous_term = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", screen.lines, screen.columns, 0, 0))
        shell = "/bin/bash" if os.path.isfile("/bin/bash") else "/bin/sh"
        bootstrap = "import fcntl,termios,os; fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(%r,[%r,'-l'])" % (shell, shell)
        home = pwd.getpwuid(os.geteuid()).pw_dir
        env = dict(os.environ, TERM="linux", HOME=home, LANG="C.UTF-8", LC_ALL="C.UTF-8")
        process = subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave,
                                   close_fds=True, start_new_session=True, cwd=home, env=env)
        os.close(slave)
        slave = None
        fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)

        def read(n):
            data = bytearray()
            while len(data) < n:
                part = os.read(inp.fileno(), n - len(data))
                if not part:
                    raise EOFError()
                data.extend(part)
            return bytes(data)

        def write(data):
            out.write(data)
            out.flush()

        def interrupted(_signal, _frame):
            raise EOFError()

        previous_term = signal.signal(signal.SIGTERM, interrupted)
        write(b"RFB 003.008\n")
        if read(12) != b"RFB 003.008\n":
            raise ValueError("Version RFB non prise en charge")
        write(b"\x01\x01")
        if read(1) != b"\x01":
            raise ValueError("Négociation RFB invalide")
        write(b"\0\0\0\0")
        read(1)
        width, height = screen.columns * 8, screen.lines * 16
        pixel = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        name = ("loco-console root@" + socket.gethostname()).encode()
        write(struct.pack(">HH", width, height) + pixel + struct.pack(">I", len(name)) + name)
        def size_update(reason=0, status=0):
            return struct.pack(">BBHHHHHiBxxxIHHHHI", 0, 0, 1, reason, status, width, height, -308,
                               1, 0, 0, 0, width, height, 0)

        tight, rgb_order, previous, last_frame = False, False, None, 0
        extended_size = False
        modifiers, pending_input = set(), bytearray()
        while process.poll() is None:
            # Vider le PTY même lorsque le navigateur est momentanément inactif. Au plus 256 Kio par tour.
            received = 0
            while received < 256 * 1024:
                try:
                    data = os.read(master, min(65536, 256 * 1024 - received))
                except BlockingIOError:
                    break
                except OSError:
                    raise EOFError()
                if not data:
                    raise EOFError()
                received += len(data)
                stream.feed(data)
            if screen.responses:
                pending_input.extend(screen.responses)
                screen.responses.clear()
            if pending_input:
                try:
                    written = os.write(master, pending_input)
                    del pending_input[:written]
                except BlockingIOError:
                    pass
            if not select.select([inp], [], [], 0.025)[0]:
                continue
            kind = read(1)[0]
            if kind == 0:
                fmt = read(19)[3:]
                if fmt[:10] != pixel[:10] or fmt[10:13] not in (b"\x10\x08\x00", b"\x00\x08\x10"):
                    raise ValueError("Format de pixels RFB non pris en charge")
                rgb_order = fmt[10] == 0
            elif kind == 2:
                count = struct.unpack(">xH", read(3))[0]
                if count > 128:
                    raise ValueError("Trop d'encodages RFB")
                encodings = struct.unpack(">" + "i" * count, read(count * 4))
                tight, extended_size = 7 in encodings, -308 in encodings
            elif kind == 3:
                incremental = read(9)[0] != 0
                if extended_size and not incremental:
                    write(size_update())
                time.sleep(max(0, 0.04 - (time.monotonic() - last_frame)))
                data = screen.render()
                write(frame_encoder(data, width, height, previous, incremental, tight, rgb_order))
                previous, last_frame = data, time.monotonic()
            elif kind == 251:
                new_width, new_height, count = struct.unpack(">xHHBx", read(7))
                screens = read(count * 16)
                valid = (extended_size and count == 1 and 160 <= new_width <= 4096 and 100 <= new_height <= 2160 and
                         screens == struct.pack(">IHHHHI", 0, 0, 0, new_width, new_height, 0))
                if valid:
                    width, height = new_width, new_height
                    screen.resize_pixels(width, height)
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", screen.lines, screen.columns, 0, 0))
                    previous = None
                write(size_update(1, 0 if valid else 3))
            elif kind == 4:
                down, sym = struct.unpack(">BxxI", read(7))
                if sym in (0xffe1, 0xffe2, 0xffe3, 0xffe4, 0xffe9, 0xffea):
                    (modifiers.add if down else modifiers.discard)(sym)
                elif down:
                    pending_input.extend(key_bytes(sym, modifiers))
            elif kind == 5:
                read(5)  # La souris ne commande pas le shell.
            elif kind == 6:
                length = struct.unpack(">xxxI", read(7))[0]
                if length > 65536:
                    raise ValueError("Presse-papiers trop volumineux")
                read(length)  # Pas d'exécution d'un presse-papiers distant.
            else:
                raise ValueError("Message RFB non pris en charge")
            if len(pending_input) > 65536:
                raise ValueError("Saisie distante trop volumineuse")
    except EOFError:
        pass
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        if slave is not None:
            os.close(slave)
        if process:
            try:
                os.killpg(process.pid, signal.SIGHUP)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        os.close(master)
