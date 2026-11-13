"""Bureau distant d'un poste à plusieurs écrans : liste annoncée au navigateur, choix de l'écran montré, saisie qui
reste juste. Agent Linux (serveur RFB, X11 par XRandR) et compagnon Windows (logique des écrans, compilée par Mono)."""
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCREENS, DESKTOP_SIZE = 0x4C4F4302, -223


def load_agent(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'portal/agents/loco-client.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.LANG = "fr"
    return module


class _Pipe:
    def __init__(self, data=b""):
        self.buffer = io.BytesIO(data)


class _TwoScreens:
    """Bureau de 6 × 2 pixels fait de deux écrans de 3 × 2 ; chaque pixel porte le numéro de l'écran montré."""
    interval = 0

    def __init__(self, count=2):
        self.width, self.height, self.selected, self.count, self.moves = 6, 2, 0, count, []

    def screens(self):
        return [{"id": i + 1, "x": 3 * i, "y": 0, "w": 3, "h": 2, "primary": i == 0} for i in range(self.count)] if self.count > 1 else []

    def select_screen(self, number):
        self.selected = number if 1 <= number <= self.count and self.count > 1 else 0
        self.width = 3 if self.selected else 6

    def grab(self): return bytes([self.selected]) * (self.width * self.height * 4)
    def key(self, sym, down): return True
    def motion(self, x, y): self.moves.append((x, y))
    def button(self, number, down): pass
    def set_cursor(self, enabled): pass
    def cursor(self): return None
    def flush(self): pass
    def close(self): pass


def updates(stream):
    """Mises à jour d'image d'une sortie RFB (après l'en-tête du serveur) : liste de listes (x, y, w, h, encodage, données)."""
    pos, found = 0, []
    while pos < len(stream):
        assert stream[pos] == 0, stream[pos]
        count = struct.unpack(">H", stream[pos + 2:pos + 4])[0]
        pos += 4
        rects = []
        for _ in range(count):
            x, y, w, h, encoding = struct.unpack(">HHHHi", stream[pos:pos + 12])
            pos += 12
            if encoding == SCREENS:
                length = struct.unpack(">I", stream[pos:pos + 4])[0]
                body = json.loads(stream[pos + 4:pos + 4 + length]); pos += 4 + length
            elif encoding == 0:
                body = stream[pos:pos + w * h * 4]; pos += w * h * 4
            else:
                body = None
            rects.append((x, y, w, h, encoding, body))
        found.append(rects)
    return found


@unittest.skipUnless(os.name == 'posix', 'Agent Linux')
class TestLinuxScreens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent('loco_screens')

    HELLO = b"RFB 003.008\n" + b"\x01" + b"\x01"
    REQUEST = struct.pack(">BBHHHH", 3, 1, 0, 0, 6, 2)

    def serve(self, backend, client):
        stdin, stdout = _Pipe(self.HELLO + client), _Pipe()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
            self.agent._rfb_serve(backend)
        out = stdout.buffer.getvalue()
        name = struct.unpack(">I", out[12 + 2 + 4 + 20:12 + 2 + 4 + 24])[0]
        return updates(out[12 + 2 + 4 + 24 + name:])

    @staticmethod
    def encodings(*values):
        return struct.pack(">BxH", 2, len(values)) + b"".join(struct.pack(">i", v) for v in values)

    @staticmethod
    def choose(number):
        return struct.pack(">BBH", 76, 1, number)

    def test_screens_are_numbered_primary_first_and_unusable_ones_dropped(self):
        found = [{"x": 1920, "y": 0, "w": 1280, "h": 1024, "primary": False}, {"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True},
                 {"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": False},        # miroir du premier : compté une fois
                 {"x": 3000, "y": 0, "w": 1280, "h": 1024, "primary": False},     # dépasse du bureau
                 {"x": 0, "y": 0, "w": 0, "h": 0, "primary": False}]
        self.assertEqual(self.agent.number_screens(found, 3200, 1080),
                         [{"id": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True},
                          {"id": 2, "x": 1920, "y": 0, "w": 1280, "h": 1024, "primary": False}])
        self.assertEqual(self.agent.number_screens([], 800, 600), [])
        # Un écran qui couvre tout le bureau à côté d'autres (miroir en plus petit) : « tous les écrans » le montre déjà.
        self.assertEqual([sc["w"] for sc in self.agent.number_screens([{"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True},
                                                                      {"x": 0, "y": 0, "w": 1280, "h": 720, "primary": False}], 1920, 1080)], [1280])
        self.assertEqual(len(self.agent.number_screens([{"x": 0, "y": 0, "w": 800, "h": 600, "primary": True}], 800, 600)), 1)

    def test_the_list_is_announced_then_the_chosen_screen_changes_the_size(self):
        backend = _TwoScreens()
        out = self.serve(backend, self.encodings(0, DESKTOP_SIZE, SCREENS) + self.REQUEST + self.choose(2) + self.REQUEST + self.REQUEST
                         + struct.pack(">BBHH", 5, 0, 5, 1) + self.choose(0) + self.REQUEST + self.REQUEST)
        first, resized, image, back, whole = out
        self.assertEqual(first[0][4], SCREENS)                                  # annonce jointe à la première image
        self.assertEqual(first[0][5], {"selected": 0, "screens": backend.screens()})
        self.assertEqual((first[1][2:5], first[1][5]), ((6, 2, 0), bytes(48)))
        # Écran 2 choisi : la nouvelle taille et l'annonce seules, puis l'image entière de cet écran.
        self.assertEqual([r[:5] for r in resized], [(0, 0, 3, 2, DESKTOP_SIZE), (2, 0, 2, 0, SCREENS)])
        self.assertEqual(resized[1][5]["selected"], 2)
        self.assertEqual([(r[2:5], r[5]) for r in image], [((3, 2, 0), bytes([2]) * 24)])
        self.assertEqual(backend.moves, [(2, 1)])                               # pointeur borné à l'écran montré
        self.assertEqual([r[:5] for r in back], [(0, 0, 6, 2, DESKTOP_SIZE), (0, 0, 2, 0, SCREENS)])
        self.assertEqual([(r[2:5], r[5]) for r in whole], [((6, 2, 0), bytes(48))])

    def test_an_unknown_number_shows_everything(self):
        backend = _TwoScreens()
        out = self.serve(backend, self.encodings(0, DESKTOP_SIZE, SCREENS) + self.REQUEST + self.choose(9) + self.REQUEST)
        self.assertEqual(backend.selected, 0)
        self.assertEqual(len(out), 2)
        self.assertEqual([r[4] for r in out[1]], [])                            # rien n'a changé : ni taille ni annonce

    def test_a_client_that_cannot_follow_a_size_change_never_gets_another_screen(self):
        for accepted in ((0, SCREENS), (0, DESKTOP_SIZE), (0,)):                # sans DesktopSize, ou sans la liste
            backend = _TwoScreens()
            out = self.serve(backend, self.encodings(*accepted) + self.REQUEST + self.choose(2) + self.REQUEST)
            self.assertEqual(backend.selected, 0, accepted)
            self.assertEqual([[r[4] for r in update] for update in out], [[0], []], accepted)

    def test_a_single_screen_announces_an_empty_list(self):
        out = self.serve(_TwoScreens(count=1), self.encodings(0, DESKTOP_SIZE, SCREENS) + self.REQUEST)
        self.assertEqual(out[0][0][5], {"selected": 0, "screens": []})

    def test_a_backend_without_screens_is_left_alone(self):
        class Plain:                                                           # moteur sans notion d'écrans (GNOME, KDE…)
            width, height, interval = 6, 2, 0
            def grab(self): return bytes(48)
            def key(self, sym, down): return True
            def motion(self, x, y): pass
            def button(self, number, down): pass
            def set_cursor(self, enabled): pass
            def cursor(self): return None
            def flush(self): pass
            def close(self): pass
        out = self.serve(Plain(), self.encodings(0, DESKTOP_SIZE, SCREENS) + self.REQUEST + self.choose(2) + self.REQUEST)
        self.assertEqual([[r[4] for r in update] for update in out], [[0], []])


def kscreen(*outputs):
    """Sortie de « kscreen-doctor -j » telle que la donne Plasma 6 (relevée sur un vrai poste) : nom, activé, position,
    mode courant, échelle, rotation (1 : aucune, 2 et 8 : quart de tour), priorité (1 : écran principal)."""
    found = []
    for i, (name, x, y, w, h, scale, rotation, enabled) in enumerate(outputs, 1):
        found.append({"connected": True, "enabled": enabled, "id": i, "name": name, "pos": {"x": x, "y": y}, "priority": i,
                      "rotation": rotation, "scale": scale, "currentModeId": "7", "size": {"width": w, "height": h},
                      "modes": [{"id": "3", "size": {"width": 640, "height": 480}}, {"id": "7", "size": {"width": w, "height": h}}]})
    return json.dumps({"features": 15, "outputs": found, "screen": {"id": 0}})


@unittest.skipUnless(os.name == 'posix', 'Agent Linux')
class TestKdeScreens(unittest.TestCase):
    """KDE Plasma en Wayland : le partage « Espace de travail complet » donne une seule image de tous les écrans ; l'agent
    y découpe l'écran choisi et place le pointeur virtuel, qui couvre toujours tout l'espace de travail."""
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent('loco_kde_screens')

    TWO = kscreen(("DP-1", 0, 0, 1280, 800, 1, 1, True), ("DP-2", 1280, 0, 1280, 800, 1, 1, True))

    def test_outputs_are_logical_scaled_rotated_and_only_the_enabled_ones(self):
        a = self.agent
        text = kscreen(("eDP-1", 0, 0, 1920, 1200, 1.5, 1, True), ("DP-1", 1280, 0, 1080, 1920, 1, 8, True), ("DP-2", 0, 0, 800, 600, 1, 1, False))
        self.assertEqual(a.kde_outputs(text), [(0, 0, 1280.0, 800.0, True, "eDP-1"), (1280, 0, 1920.0, 1080.0, False, "DP-1")])
        for broken in ("", "pas du JSON", "[]", '{"outputs": [{"enabled": true}]}', '{"outputs": "x"}'):
            self.assertEqual(a.kde_outputs(broken), [], broken)

    def test_the_workspace_image_is_cut_into_its_screens(self):
        a = self.agent
        self.assertEqual(a.kde_screens(a.kde_outputs(self.TWO), 2560, 800),
                         [{"id": 1, "x": 0, "y": 0, "w": 1280, "h": 800, "primary": True},
                          {"id": 2, "x": 1280, "y": 0, "w": 1280, "h": 800, "primary": False}])
        # Second écran à 150 % : l'image de l'espace de travail compte en pixels logiques (relevé : 2133 × 800).
        scaled = a.kde_outputs(kscreen(("DP-1", 0, 0, 1280, 800, 1, 1, True), ("DP-2", 1280, 0, 1280, 800, 1.5, 1, True)))
        self.assertEqual([(sc["x"], sc["y"], sc["w"], sc["h"]) for sc in a.kde_screens(scaled, 2133, 800)], [(0, 0, 1280, 800), (1280, 0, 853, 533)])
        # Écran secondaire à GAUCHE du principal (position négative) et plus bas : le principal reste le numéro 1.
        left = a.kde_outputs(kscreen(("DP-1", 0, 0, 1920, 1080, 1, 1, True), ("DP-2", -1280, 56, 1280, 1024, 1, 1, True)))
        self.assertEqual([(sc["id"], sc["x"], sc["y"], sc["w"], sc["h"]) for sc in a.kde_screens(left, 3200, 1080)],
                         [(1, 1280, 0, 1920, 1080), (2, 0, 56, 1280, 1024)])

    def test_nothing_to_choose_when_one_screen_is_active_or_shared(self):
        a = self.agent
        self.assertEqual(a.kde_screens(a.kde_outputs(kscreen(("DP-1", 0, 0, 1920, 1080, 1, 1, True))), 1920, 1080), [])
        self.assertEqual(a.kde_screens(a.kde_outputs(self.TWO), 1280, 800), [])          # un seul des deux écrans a été partagé
        self.assertEqual(a.kde_screens([], 2560, 800), [])

    def test_the_pointer_covers_the_whole_workspace_whatever_is_shared(self):
        a = self.agent
        two = a.kde_outputs(self.TWO)
        self.assertEqual(a.kde_pointer(two, 2560, 800), (0, 0, 2560, 800))               # espace de travail complet
        self.assertEqual(a.kde_pointer(two, 1280, 800, "DP-2"), (1280, 0, 2560, 800))    # l'écran que nomme le flux
        self.assertEqual(a.kde_pointer(two, 1280, 800, "DP-1"), (0, 0, 2560, 800))
        self.assertEqual(a.kde_pointer(two, 1280, 800), (0, 0, 1280, 800))               # deux écrans pareils, aucun nom : inchangé
        self.assertEqual(a.kde_pointer(two, 1280, 800, "HDMI-9"), (0, 0, 1280, 800))
        mixed = a.kde_outputs(kscreen(("DP-1", 0, 0, 1920, 1080, 1, 1, True), ("DP-2", 1920, 0, 1280, 1024, 1, 1, True)))
        self.assertEqual(a.kde_pointer(mixed, 1280, 1024), (1920, 0, 3200, 1080))        # un seul écran a cette forme
        scaled = a.kde_outputs(kscreen(("DP-1", 0, 0, 1280, 800, 1, 1, True), ("DP-2", 1280, 0, 1280, 800, 1.5, 1, True)))
        self.assertEqual(a.kde_pointer(scaled, 1280, 800, "DP-2"), (1920, 0, 3200, 1200))  # image en pixels réels, bureau en logique
        self.assertEqual(a.kde_pointer(a.kde_outputs(kscreen(("DP-1", 0, 0, 1920, 1080, 1, 1, True))), 1920, 1080), (0, 0, 1920, 1080))

    def backend(self, screens, pointer=None):
        class Pad:
            width = height = 1
            def __init__(self): self.moves = []
            def motion(self, x, y): self.moves.append((x, y, self.width, self.height))
        b = self.agent.KdePortalBackend.__new__(self.agent.KdePortalBackend)
        b._full = (6, 2)
        b.width, b.height, b.selected, b._origin, b._zone, b._closed = 6, 2, 0, (0, 0), None, False
        b._screens, b._pointer, b._uinput = screens, pointer or (0, 0, 6, 2), Pad()
        b._last = b"".join(bytes([x + 10 * y]) * 4 for y in range(2) for x in range(6))     # pixel = colonne + 10 × ligne
        b._ctx = type("Ctx", (), {"iteration": lambda self, block: None})()
        b._pull = lambda timeout: None
        return b

    SCREENS = [{"id": 1, "x": 0, "y": 0, "w": 3, "h": 2, "primary": True}, {"id": 2, "x": 3, "y": 0, "w": 3, "h": 2, "primary": False}]

    def test_the_chosen_screen_is_cut_out_and_the_pointer_shifted(self):
        b = self.backend(self.SCREENS)
        self.assertEqual(len(b.grab()), 48)
        b.select_screen(2)
        self.assertEqual((b.width, b.height, b.selected), (3, 2, 2))
        self.assertEqual([b.grab()[i] for i in range(0, 24, 4)], [3, 4, 5, 13, 14, 15])       # les colonnes de l'écran 2
        b.motion(1, 1)
        b.motion(2, 0)
        # Premier envoi vers un autre écran : doublé à un pixel près, pour franchir la barrière de bord de KWin.
        self.assertEqual(b._uinput.moves, [(5, 1, 6, 2), (4, 1, 6, 2), (5, 0, 6, 2)])
        b.select_screen(9)                                                                  # écran inconnu : toute l'image
        self.assertEqual((b.width, b.selected, len(b.grab())), (6, 0, 48))
        b._screens = [self.SCREENS[0]]                                                      # l'écran choisi a disparu
        b.select_screen(1)
        b._screens = []
        self.assertEqual((b.screens(), b.selected, b.width), ([], 0, 6))

    def test_a_single_shared_screen_is_placed_in_the_workspace(self):
        b = self.backend([], pointer=(6, 0, 12, 2))                                         # l'image est l'écran de droite
        b.motion(2, 1)
        self.assertEqual(b._uinput.moves[-1], (8, 1, 12, 2))


def _x11_ready():
    if not shutil.which("Xvfb"):
        return False
    try:
        for name in ("libX11.so.6", "libXtst.so.6", "libXrandr.so.2"):
            ctypes.CDLL(name)
    except OSError:
        return False
    return True


@unittest.skipUnless(os.name == 'posix' and _x11_ready(), 'Serveur X virtuel (Xvfb) et bibliothèques X11')
class TestX11Screens(unittest.TestCase):
    """Vrai serveur X (Xvfb) de 640 × 240 où XRandR déclare deux écrans : la capture et le pointeur suivent l'écran choisi."""
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent('loco_x11_screens')
        cls.display = ":%d" % (90 + os.getpid() % 400)
        cls.server = subprocess.Popen(["Xvfb", cls.display, "-screen", "0", "640x240x24", "-nolisten", "tcp"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        x = cls.x = ctypes.CDLL("libX11.so.6")
        x.XOpenDisplay.restype = ctypes.c_void_p
        for _ in range(100):
            cls.d = x.XOpenDisplay(cls.display.encode())
            if cls.d:
                break
            time.sleep(0.1)
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cls.root = x.XDefaultRootWindow(cls.d)
        # Deux écrans déclarés à XRandR (ils vivent tant que cette connexion reste ouverte) : celui de gauche fait
        # 400 × 240, celui de droite, le principal, 240 × 240. Xvfb garde aussi le sien, qui couvre tout le bureau.
        xrr, info = ctypes.CDLL("libXrandr.so.2"), cls.agent.XRRMonitorInfo
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xrr.XRRAllocateMonitor.restype = ctypes.POINTER(info)
        xrr.XRRAllocateMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xrr.XRRSetMonitor.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(info)]
        for name, primary, (left, width) in ((b"gauche", 0, (0, 400)), (b"droite", 1, (400, 240))):
            monitor = xrr.XRRAllocateMonitor(cls.d, 0)
            monitor.contents.name, monitor.contents.primary = x.XInternAtom(cls.d, name, 0), primary
            monitor.contents.x, monitor.contents.y, monitor.contents.width, monitor.contents.height = left, 0, width, 240
            xrr.XRRSetMonitor(cls.d, cls.root, monitor)
        x.XSync(cls.d, 0)

    @classmethod
    def tearDownClass(cls):
        cls.x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        cls.x.XCloseDisplay(cls.d)
        cls.server.terminate()
        cls.server.wait(10)

    def paint(self):
        """Fond : bleu pur à gauche de x = 400, vert pur à droite (BGRX : ff 00 00 / 00 ff 00)."""
        x, d, root = self.x, self.d, self.root
        x.XCreateGC.restype = ctypes.c_void_p
        x.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        gc = x.XCreateGC(d, root, 0, None)
        x.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        x.XFillRectangle.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p] + [ctypes.c_int] * 2 + [ctypes.c_uint] * 2
        for color, left, width in ((0x0000ff, 0, 400), (0x00ff00, 400, 240)):
            x.XSetForeground(d, gc, color)
            x.XFillRectangle(d, root, gc, left, 0, width, 240)
        x.XSync(d, 0)

    def pointer(self):
        self.x.XSync(self.d, 0)
        self.x.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
        values = [ctypes.c_ulong(), ctypes.c_ulong()] + [ctypes.c_int() for _ in range(4)] + [ctypes.c_uint()]
        self.x.XQueryPointer(self.d, self.root, *[ctypes.byref(v) for v in values])
        return values[2].value, values[3].value

    def test_capture_and_pointer_follow_the_chosen_screen(self):
        self.paint()
        backend = self.agent.X11Backend({"DISPLAY": self.display})
        try:
            self.assertEqual((backend.width, backend.height), (640, 240))
            screens = backend.screens()
            self.assertEqual([(sc["id"], sc["x"], sc["w"], sc["h"], sc["primary"]) for sc in screens],
                             [(1, 400, 240, 240, True), (2, 0, 400, 240, False)])    # le principal d'abord ; celui de Xvfb, qui couvre tout, n'en est pas un
            whole = backend.grab()
            self.assertEqual((whole[:3], whole[639 * 4:639 * 4 + 3]), (b"\xff\x00\x00", b"\x00\xff\x00"))
            backend.select_screen(1)
            self.assertEqual((backend.width, backend.height, backend.selected), (240, 240, 1))
            right = backend.grab()
            self.assertEqual(len(right), 240 * 240 * 4)
            self.assertEqual({right[i:i + 3] for i in range(0, len(right), 4)}, {b"\x00\xff\x00"})     # que du vert : l'écran de droite
            backend.motion(10, 20); backend.flush()
            self.assertEqual(self.pointer(), (410, 20))
            backend.select_screen(2)
            left = backend.grab()
            self.assertEqual((backend.width, len(left)), (400, 400 * 240 * 4))
            self.assertEqual({left[i:i + 3] for i in range(0, len(left), 4)}, {b"\xff\x00\x00"})
            backend.motion(10, 20); backend.flush()
            self.assertEqual(self.pointer(), (10, 20))
            backend.select_screen(7)                                       # écran inconnu : le bureau entier
            self.assertEqual((backend.width, backend.selected), (640, 0))
            self.assertEqual(len(backend.grab()), 640 * 240 * 4)
        finally:
            backend.close()


@unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'), 'Compilateur .NET/Mono (job MSI)')
class TestWindowsScreens(unittest.TestCase):
    def test_numbering_area_announcement_and_pointer_on_a_two_screen_desktop(self):
        harness = r'''
using System; using System.Collections.Generic; using System.Drawing; using Loco;
class Probe {
 static DesktopScreen S(int n, int x, int y, int w, int h, bool p) { return new DesktopScreen { Number = n, Bounds = new Rectangle(x, y, w, h), Primary = p }; }
 static string R(Rectangle r) { return r.X + "," + r.Y + "," + r.Width + "," + r.Height; }
 static void Main() {
  Console.WriteLine(DesktopScreens.Number(@"\\.\DISPLAY1") + " " + DesktopScreens.Number(@"\\.\DISPLAY12") + " " + DesktopScreens.Number("WinDisc") + " " + DesktopScreens.Number(null));
  // Écran 2 à GAUCHE du principal : coordonnées négatives, bureau entier de -1280 à 1920.
  var all = Rectangle.FromLTRB(-1280, 0, 1920, 1080);
  var screens = DesktopScreens.Order(new List<DesktopScreen> { S(2, -1280, 56, 1280, 1024, false), S(1, 0, 0, 1920, 1080, true) });
  Console.WriteLine(screens[0].Number + "" + screens[1].Number);
  Console.WriteLine(R(DesktopScreens.Area(screens, 0, all)) + " " + R(DesktopScreens.Area(screens, 2, all)) + " " + R(DesktopScreens.Area(screens, 1, all)) + " " + R(DesktopScreens.Area(screens, 5, all)));
  Console.WriteLine(DesktopScreens.Valid(screens, 2) + " " + DesktopScreens.Valid(screens, 5) + " " + DesktopScreens.Valid(screens, -1) + " " + DesktopScreens.Valid(new List<DesktopScreen> { S(1, 0, 0, 1920, 1080, true) }, 1));
  Console.WriteLine(DesktopScreens.Describe(screens, 2, all));
  Console.WriteLine(DesktopScreens.Describe(new List<DesktopScreen> { S(1, 0, 0, 1920, 1080, true) }, 1, new Rectangle(0, 0, 1920, 1080)));
  int x, y;
  DesktopScreens.Absolute(all, -1280, 0, out x, out y); Console.Write(x + "," + y + " ");
  DesktopScreens.Absolute(all, 1919, 1079, out x, out y); Console.Write(x + "," + y + " ");
  DesktopScreens.Absolute(all, 0, 540, out x, out y); Console.WriteLine(x + "," + y);
  // Numéros en double ou absents : renumérotés de gauche à droite.
  var odd = DesktopScreens.Order(new List<DesktopScreen> { S(0, 1920, 0, 1280, 1024, false), S(0, 0, 0, 1920, 1080, true) });
  Console.WriteLine(odd[0].Number + ":" + odd[0].Bounds.X + " " + odd[1].Number + ":" + odd[1].Bounds.X);
  Console.WriteLine(DesktopScreens.Valid(new List<DesktopScreen> { S(1, 0, 0, 1920, 1080, true), S(2, 1920, 0, 0, 0, false) }, 2));
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'Probe.cs').write_text(harness)
            subprocess.run(['mcs', '-warnaserror+', '-sdk:4.6.2', '-r:System.Drawing.dll', '-r:System.Core.dll', '-out:' + str(path / 'Probe.exe'),
                            str(ROOT / 'windows/client/DesktopScreens.cs'), str(path / 'Probe.cs')], check=True, capture_output=True)
            out = subprocess.run(['mono', str(path / 'Probe.exe')], check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out, [
            '1 12 0 0',
            '12',
            '-1280,0,3200,1080 -1280,56,1280,1024 0,0,1920,1080 -1280,0,3200,1080',
            '2 0 0 0',                                                           # un seul écran : rien à choisir
            '{"selected":2,"screens":[{"id":1,"x":1280,"y":0,"w":1920,"h":1080,"primary":true},{"id":2,"x":0,"y":56,"w":1280,"h":1024,"primary":false}]}',
            '{"selected":0,"screens":[{"id":1,"x":0,"y":0,"w":1920,"h":1080,"primary":true}]}',
            '0,0 65535,65535 26222,32797',
            '1:0 2:1920',
            '0',                                                                 # écran sans surface : pas montrable
        ])


if __name__ == '__main__':
    unittest.main()
