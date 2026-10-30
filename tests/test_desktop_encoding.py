"""Décode les vrais flux Tight Linux/.NET : pixels exacts, 4K, zones modifiées et rafraîchissement complet."""
import importlib.util
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]


CURSORS = []   # formes de curseur décodées (point chaud x, y, largeur, hauteur, RGBA)


def decode(frame, width, height, pixels=None):
    pixels = bytearray(width * height * 4) if pixels is None else bytearray(pixels)
    assert frame[:2] == b'\0\0'
    count = struct.unpack_from('>H', frame, 2)[0]
    pos = 4
    for _ in range(count):
        x, y, w, h, encoding = struct.unpack_from('>HHHHi', frame, pos); pos += 12
        if encoding == 0x574D5664:                      # curseur VMware : type 1 (alpha), octet de bourrage, RGBA
            assert frame[pos:pos + 2] == b'\x01\x00' and x < w and y < h
            CURSORS.append((x, y, w, h, frame[pos + 2:pos + 2 + w * h * 4])); pos += 2 + w * h * 4
            count -= 1
            continue
        assert x + w <= width and y + h <= height
        if encoding == 7:
            control = frame[pos]; pos += 1
            if control == 0x80:
                rgb = frame[pos:pos + 3] * (w * h); pos += 3
            elif control == 0x01:
                if w * h * 3 < 12:
                    rgb = frame[pos:pos + w * h * 3]; pos += len(rgb)
                else:
                    size = frame[pos] & 127; more = frame[pos] & 128; pos += 1
                    if more:
                        size |= (frame[pos] & 127) << 7; more = frame[pos] & 128; pos += 1
                        if more:
                            size |= frame[pos] << 14; pos += 1
                    rgb = zlib.decompress(frame[pos:pos + size]); pos += size
                assert len(rgb) == w * h * 3
            else:
                raise AssertionError('Compression Tight inconnue')
            block = bytearray(w * h * 4)
            block[0::4], block[1::4], block[2::4] = rgb[2::3], rgb[1::3], rgb[0::3]
        elif encoding == 0:
            block = frame[pos:pos + w * h * 4]; pos += len(block)
        else:
            raise AssertionError('Encodage inattendu')
        for row in range(h):
            offset = ((y + row) * width + x) * 4
            pixels[offset:offset + w * 4] = block[row * w * 4:(row + 1) * w * 4]
    assert pos == len(frame), 'Octets non décodés'
    return pixels, count


@unittest.skipUnless(os.name == 'posix', 'Agent Linux')
class TestLinuxDesktopEncoding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('loco_desktop_encoding', ROOT / 'portal/agents/loco-client.py')
        cls.agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.agent)
        cls.agent.LANG = "fr"  # messages en français (langue de référence) pour des assertions stables

    def test_lossless_edge_tiles_delta_empty_update_and_full_refresh(self):
        w, h = 257, 129
        image = bytes(v for y in range(h) for x in range(w) for v in (x % 256, y % 256, (x + y) % 256, 0))
        frame = self.agent.rfb_frame(image, w, h)
        decoded, count = decode(frame, w, h)
        self.assertEqual(decoded, image); self.assertEqual(count, 6)
        changed = bytearray(image); changed[(128 * w + 256) * 4:(128 * w + 256) * 4 + 4] = b'\x03\x02\x01\0'
        delta = self.agent.rfb_frame(changed, w, h, image)
        decoded, count = decode(delta, w, h, decoded)
        self.assertEqual(decoded, changed); self.assertEqual(count, 1)
        self.assertEqual(self.agent.rfb_frame(changed, w, h, changed), b'\0\0\0\0')
        decoded, count = decode(self.agent.rfb_frame(changed, w, h, changed, incremental=False), w, h)
        self.assertEqual(decoded, changed); self.assertEqual(count, 6)
        decoded, _ = decode(self.agent.rfb_frame(image, w, h, tight=False), w, h)
        self.assertEqual(decoded, image)

    def test_cursor_shape_is_sent_with_alpha_and_hotspot(self):
        # Pixels XFixes ARGB prémultipliés : opaque rouge, blanc à demi transparent, transparent.
        argb = [0xFFFF0000, 0x80808080, 0x00000000, 0xFF000000]
        cursor = self.agent.cursor_rect(1, 5, 2, 2, argb)
        image = b'\0\0\0\0' * 16
        CURSORS.clear()
        decoded, count = decode(self.agent.rfb_frame(image, 4, 4, cursor=cursor), 4, 4)
        self.assertEqual(count, 1)                      # une zone d'image, plus le curseur
        hotx, hoty, w, h, rgba = CURSORS[0]
        self.assertEqual((hotx, hoty, w, h), (1, 1, 2, 2))   # point chaud borné à la forme
        self.assertEqual(rgba, bytes([255, 0, 0, 255, 255, 255, 255, 128, 0, 0, 0, 0, 0, 0, 0, 255]))
        self.assertEqual(self.agent.rfb_frame(image, 4, 4, image, cursor=cursor)[:4], b'\0\0\0\1')

    def test_4k_capture_is_native_and_small_changes_do_not_resend_the_screen(self):
        w, h = 3840, 2160
        image = b'\xff\xff\xff\0' * (w * h)
        initial = self.agent.rfb_frame(image, w, h)
        changed = bytearray(image); changed[(140 * w + 130) * 4] = 10
        delta = self.agent.rfb_frame(changed, w, h, image)
        decoded, count = decode(delta, w, h, image)
        self.assertEqual(decoded, changed); self.assertEqual(count, 1)
        self.assertLess(len(delta), len(initial) // 10)


@unittest.skipUnless(shutil.which('mcs') and shutil.which('mono'), 'Compilateur .NET/Mono (job MSI)')
class TestWindowsDesktopEncoding(unittest.TestCase):
    def test_dotnet_deflate_stream_is_real_zlib_and_4k_delta_is_lossless(self):
        harness = r'''
using System; using System.IO; using Loco;
class Probe {
 static void Main(string[] args) {
  int w=257,h=129; var image=new byte[w*h*4];   // peu de couleurs par tuile : reste en Tight zlib sans perte
  for(int y=0;y<h;y++) for(int x=0;x<w;x++) { int p=(y*w+x)*4; image[p]=(byte)(x/16*16); image[p+1]=(byte)(y/16*16); image[p+2]=0; }
  var encoder=new RfbFramebuffer(w,h);
  File.WriteAllBytes(Path.Combine(args[0],"image.bin"),image);
  File.WriteAllBytes(Path.Combine(args[0],"initial.bin"),encoder.Update(image,true,true,false));
  image[(128*w+256)*4]=3; image[(128*w+256)*4+1]=2; image[(128*w+256)*4+2]=1;
  File.WriteAllBytes(Path.Combine(args[0],"delta.bin"),encoder.Update(image,true,true,false));
  File.WriteAllBytes(Path.Combine(args[0],"empty.bin"),encoder.Update(image,true,true,false));
  File.WriteAllBytes(Path.Combine(args[0],"full.bin"),encoder.Update(image,false,true,false));
  File.WriteAllBytes(Path.Combine(args[0],"raw.bin"),encoder.Update(image,false,false,false));
  var cursor=RfbFramebuffer.CursorRect(3,4,2,1,new byte[]{1,2,3,4,5,6,7,8});
  File.WriteAllBytes(Path.Combine(args[0],"cursor.bin"),encoder.Update(image,true,true,false,cursor));
  w=3840; h=2160; image=new byte[w*h*4]; for(int i=0;i<image.Length;i+=4) { image[i]=255; image[i+1]=255; image[i+2]=255; }
  encoder=new RfbFramebuffer(w,h);
  File.WriteAllBytes(Path.Combine(args[0],"4k.bin"),encoder.Update(image,true,true,false));
  image[(140*w+130)*4]=10;
  File.WriteAllBytes(Path.Combine(args[0],"4k-delta.bin"),encoder.Update(image,true,true,false));
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'Probe.cs').write_text(harness)
            subprocess.run(['mcs', '-warnaserror+', '-sdk:4.6.2', '-r:System.Drawing.dll', '-r:System.Core.dll',
                            '-out:' + str(path / 'Probe.exe'), str(ROOT / 'windows/client/Lang.cs'), str(ROOT / 'windows/client/RfbFramebuffer.cs'),
                            str(path / 'Probe.cs')], check=True, capture_output=True)
            subprocess.run(['mono', str(path / 'Probe.exe'), directory], check=True, capture_output=True)
            image = (path / 'image.bin').read_bytes()
            decoded, count = decode((path / 'initial.bin').read_bytes(), 257, 129)
            self.assertEqual(decoded, image); self.assertEqual(count, 6)
            changed = bytearray(image); changed[(128 * 257 + 256) * 4:(128 * 257 + 256) * 4 + 4] = b'\x03\x02\x01\0'
            decoded, count = decode((path / 'delta.bin').read_bytes(), 257, 129, decoded)
            self.assertEqual(decoded, changed); self.assertEqual(count, 1)
            self.assertEqual((path / 'empty.bin').read_bytes(), b'\0\0\0\0')
            for name in ('full.bin', 'raw.bin'):
                decoded, count = decode((path / name).read_bytes(), 257, 129)
                self.assertEqual(decoded, changed); self.assertEqual(count, 6)
            CURSORS.clear()
            self.assertEqual(decode((path / 'cursor.bin').read_bytes(), 257, 129, decoded)[1], 0)
            self.assertEqual(CURSORS, [(1, 0, 2, 1, bytes([1, 2, 3, 4, 5, 6, 7, 8]))])
            image = b'\xff\xff\xff\0' * (3840 * 2160)
            decoded, _ = decode((path / '4k.bin').read_bytes(), 3840, 2160)
            self.assertEqual(decoded, image)
            changed = bytearray(image); changed[(140 * 3840 + 130) * 4] = 10
            decoded, count = decode((path / '4k-delta.bin').read_bytes(), 3840, 2160, decoded)
            self.assertEqual(decoded, changed); self.assertEqual(count, 1)
            self.assertLess((path / '4k-delta.bin').stat().st_size, (path / '4k.bin').stat().st_size // 10)

    def test_complex_tiles_use_jpeg_and_flat_tiles_stay_lossless(self):
        """Tuile riche en couleurs : sous-encodage Tight JPEG (0x90) ; tuile à peu de couleurs : zlib sans perte (0x01)."""
        harness = r'''
using System; using System.IO; using Loco;
class Jpeg {
 static void Main(string[] args) {
  int w=128,h=128; var photo=new byte[w*h*4]; var flat=new byte[w*h*4];
  for(int y=0;y<h;y++) for(int x=0;x<w;x++) { int p=(y*w+x)*4;
   photo[p]=(byte)(x*2); photo[p+1]=(byte)(y*2); photo[p+2]=(byte)((x*y)%251);
   byte v=(byte)(((x+y)/16)%3==0?0x20:((x+y)/16)%3==1?0x80:0xd0); flat[p]=v; flat[p+1]=v; flat[p+2]=v; }
  File.WriteAllBytes(Path.Combine(args[0],"photo.bin"),new RfbFramebuffer(w,h).Update(photo,true,true,false));
  File.WriteAllBytes(Path.Combine(args[0],"flat.bin"),new RfbFramebuffer(w,h).Update(flat,true,true,false));
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'Jpeg.cs').write_text(harness)
            subprocess.run(['mcs', '-warnaserror+', '-sdk:4.6.2', '-r:System.Drawing.dll', '-r:System.Core.dll',
                            '-out:' + str(path / 'Jpeg.exe'), str(ROOT / 'windows/client/Lang.cs'), str(ROOT / 'windows/client/RfbFramebuffer.cs'),
                            str(path / 'Jpeg.cs')], check=True, capture_output=True)
            run = subprocess.run(['mono', str(path / 'Jpeg.exe'), directory], capture_output=True, text=True)
            if run.returncode:
                self.fail(run.stderr)
            photo = (path / 'photo.bin').read_bytes()
            self.assertEqual(struct.unpack_from('>H', photo, 2)[0], 1)          # une tuile
            self.assertEqual(struct.unpack_from('>HHHHi', photo, 4), (0, 0, 128, 128, 7))   # Tight, pleine tuile
            if photo[16] == 0x01:
                self.skipTest('JPEG indisponible (libgdiplus absent) : repli zlib sans perte')
            self.assertEqual(photo[16], 0x90)                                   # sous-encodage JPEG
            b = photo[17]; size = b & 0x7f; pos = 18
            if b & 0x80:
                b = photo[18]; size |= (b & 0x7f) << 7; pos = 19
                if b & 0x80:
                    size |= photo[19] << 14; pos = 20
            data = photo[pos:]
            self.assertEqual(len(data), size)                                  # longueur compacte Tight exacte
            self.assertEqual((data[:2], data[-2:]), (b'\xff\xd8', b'\xff\xd9'))  # flux JPEG complet
            self.assertEqual((path / 'flat.bin').read_bytes()[16], 0x01)        # peu de couleurs : zlib sans perte

    def test_capture_mask_limits_the_compared_tiles(self):
        """Zones signalées par la duplication DXGI : seules les tuiles marquées sont comparées (les autres sont réputées identiques)."""
        harness = r'''
using System; using System.IO; using Loco;
class Mask {
 static void Main(string[] args) {
  int w=256,h=128; var image=new byte[w*h*4];
  var e=new RfbFramebuffer(w,h); e.Update(image,true,true,false);
  image[(10*w+10)*4]=200; image[(10*w+200)*4]=200;          // une modification dans chacune des deux tuiles
  File.WriteAllBytes(Path.Combine(args[0],"left.bin"), e.Update(image,true,true,false,null,new[]{true,false}));
  File.WriteAllBytes(Path.Combine(args[0],"none.bin"), e.Update(image,true,true,false,null,new[]{false,false}));
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'Mask.cs').write_text(harness)
            subprocess.run(['mcs', '-warnaserror+', '-sdk:4.6.2', '-r:System.Drawing.dll', '-r:System.Core.dll',
                            '-out:' + str(path / 'Mask.exe'), str(ROOT / 'windows/client/Lang.cs'), str(ROOT / 'windows/client/RfbFramebuffer.cs'),
                            str(path / 'Mask.cs')], check=True, capture_output=True)
            subprocess.run(['mono', str(path / 'Mask.exe'), directory], check=True, capture_output=True)
            left = (path / 'left.bin').read_bytes()
            self.assertEqual(struct.unpack_from('>H', left, 2)[0], 1)                # seule la tuile marquée part
            self.assertEqual(struct.unpack_from('>HH', left, 4), (0, 0))
            self.assertEqual((path / 'none.bin').read_bytes(), b'\0\0\0\0')     # rien de marqué : mise à jour vide

    def test_resolution_choice_fills_the_window_before_matching_its_size(self):
        """Mode du pilote retenu : celui qui remplit la zone (rapport conservé), puis le plus proche de 100 %."""
        harness = r'''
using System; using System.Linq; using Loco;
class Pick {
 static void Main(string[] args) {
  var modes = "640x480 1024x768 800x600 2048x1280 1920x1200 1600x1000 1680x1050 1440x900 1280x800 848x480 1400x1050 1600x1200 1280x720 4096x2160 3840x2160 2560x1080 1920x1080"
   .Split(' ').Select(m => m.Split('x').Select(int.Parse).ToArray()).ToArray();
  foreach (var target in args) {
   var t = target.Split('x').Select(int.Parse).ToArray();
   var best = modes.OrderBy(m => DesktopResolution.Score(m[0], m[1], t[0], t[1])).First();
   Console.WriteLine(target + "=" + best[0] + "x" + best[1]);
  }
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'Pick.cs').write_text(harness)
            subprocess.run(['mcs', '-warnaserror+', '-sdk:4.6.2', '-r:System.Core.dll', '-out:' + str(path / 'Pick.exe'),
                            str(ROOT / 'windows/client/DesktopResolution.cs'), str(path / 'Pick.cs')], check=True, capture_output=True)
            out = subprocess.run(['mono', str(path / 'Pick.exe'), '1868x772', '1280x800', '1920x1080', '1400x800'],
                                 check=True, capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ['1868x772=2560x1080',     # fenêtre très large : 21:9 plutôt que 16:9 avec des bandes
                               '1280x800=1280x800', '1920x1080=1920x1080', '1400x800=1280x720'])


@unittest.skipUnless(os.name == 'posix', 'Agent Linux')
class TestWaylandDesktop(unittest.TestCase):
    """Chemin Wayland de l'agent Linux : lecture PPM de grim, conversion BGRX, table clavier, périphérique d'entrée."""
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('loco_wayland', ROOT / 'portal/agents/loco-client.py')
        cls.agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.agent)
        cls.agent.LANG = "fr"

    def test_ppm_parse_handles_comments_whitespace_and_rejects_bad_input(self):
        a = self.agent
        px = bytes([10, 20, 30, 40, 50, 60])                               # 2x1 RGB
        self.assertEqual(a._parse_ppm(b"P6\n2 1\n255\n" + px), (2, 1, px))
        self.assertEqual(a._parse_ppm(b"P6\n# grim\n2  1\n255 " + px), (2, 1, px))   # commentaire + espaces multiples
        for bad in (b"P5\n2 1\n255\n" + px, b"P6\n2 1\n255\n" + px[:-1], b"P6\n2 1\n100\n" + px):
            with self.assertRaises(RuntimeError):
                a._parse_ppm(bad)

    def test_rgb_to_bgrx_feeds_rfb_frame_with_the_original_pixels(self):
        a = self.agent
        self.assertEqual(a._rgb_to_bgrx(bytes([1, 2, 3, 4, 5, 6])), bytes([3, 2, 1, 0, 6, 5, 4, 0]))
        bgrx = a._rgb_to_bgrx(bytes([1, 2, 3, 4, 5, 6]))
        decoded, _ = decode(a.rfb_frame(bgrx, 2, 1), 2, 1)                 # décode -> BGRX : R,G,B retrouvés
        self.assertEqual((decoded[2], decoded[1], decoded[0]), (1, 2, 3))
        self.assertEqual((decoded[6], decoded[5], decoded[4]), (4, 5, 6))

    def test_keymap_covers_ascii_shares_physical_keys_and_stays_in_evdev_range(self):
        keys = self.agent.EVDEV_KEYS
        for ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~":
            self.assertIn(ord(ch), keys, ch)
        self.assertEqual(keys[ord('a')], keys[ord('A')])                   # casse : même touche physique
        self.assertEqual(keys[ord('1')], keys[ord('!')])                   # symbole Maj : touche de base
        self.assertEqual(keys[ord('2')], keys[ord('@')])
        for sym in (0xff0d, 0xff08, 0xff1b, 0xff09, 0xffff, 0xffe1, 0xffe3, 0xff51, 0xffbe, 0xffc9):
            self.assertIn(sym, keys, hex(sym))                             # Entrée, Retour, Échap, Tab, Suppr, Maj, Ctrl, flèches, F1, F12
        self.assertTrue(all(1 <= c <= 255 for c in keys.values()))

    def test_input_event_and_setup_struct_sizes_match_the_kernel_abi(self):
        self.assertEqual(struct.Struct("@llHHi").size, 24 if struct.calcsize("@l") == 8 else 16)
        setup = (struct.pack("@80sHHHHI", b"loco remote desktop", 0x06, 0x1234, 0x5678, 1, 0)
                 + struct.pack("@64i", *([0] * 64)) * 4)
        self.assertEqual(len(setup), 80 + 8 + 4 + 4 * 64 * 4)             # struct uinput_user_dev (ABS_CNT = 64)

    def test_wayland_backend_grab_runs_grim_and_returns_bgrx(self):
        a = self.agent
        with tempfile.TemporaryDirectory() as d:
            grim = Path(d) / "grim"                                        # faux grim : aucune session Wayland requise
            grim.write_text("#!/bin/sh\nprintf 'P6\\n2 1\\n255\\n'; printf '\\001\\002\\003\\004\\005\\006'\n")
            grim.chmod(0o755)

            class FakeUInput:                                             # pas de /dev/uinput ni de root en CI
                def __init__(self, w, h): self.width, self.height = w, h
                def close(self): pass
            saved_uinput, saved_path = a.UInput, os.environ["PATH"]
            a.UInput = FakeUInput
            os.environ["PATH"] = str(d) + os.pathsep + saved_path
            try:
                backend = a.WaylandBackend({"XDG_RUNTIME_DIR": "/run/user/0", "WAYLAND_DISPLAY": "wayland-0"})
                self.assertEqual((backend.width, backend.height), (2, 1))
                self.assertEqual(backend.grab(), bytes([3, 2, 1, 0, 6, 5, 4, 0]))
            finally:
                a.UInput, os.environ["PATH"] = saved_uinput, saved_path

    @unittest.skipUnless(os.path.exists('/dev/uinput') and os.access('/dev/uinput', os.W_OK), '/dev/uinput accessible (root)')
    def test_uinput_device_creates_and_emits_against_the_real_kernel(self):
        dev = self.agent.UInput(1920, 1080)
        try:
            dev.motion(960, 540); dev.button(1, True); dev.button(1, False)
            dev.key(self.agent.EVDEV_KEYS[ord('a')], True); dev.key(self.agent.EVDEV_KEYS[ord('a')], False)
            dev.wheel(1); dev.wheel(-1)
        finally:
            dev.close()


class _Pipe:
    """Flux binaire simulé pour sys.stdin / sys.stdout (attribut .buffer)."""
    def __init__(self, data=b""):
        self.buffer = io.BytesIO(data)


class _FakeBackend:
    width, height, interval = 2, 2, 0

    def __init__(self):
        self.keys, self.codes = [], []

    def grab(self): return b"\0" * 16
    def key(self, sym, down): self.keys.append((sym, bool(down))); return True
    def motion(self, x, y): pass
    def button(self, number, down): pass
    def set_cursor(self, enabled): pass
    def cursor(self): return None
    def flush(self): pass
    def close(self): pass


class _FakeKeycodeBackend(_FakeBackend):
    def keycode(self, code, down): self.codes.append((code, bool(down)))


@unittest.skipUnless(os.name == 'posix', 'Agent Linux')
class TestWaylandInputAndMutter(unittest.TestCase):
    """Touches physiques (extension QEMU de noVNC), backend Mutter (GNOME Wayland) et aiguillage des sessions."""
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('loco_mutter', ROOT / 'portal/agents/loco-client.py')
        cls.agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.agent)
        cls.agent.LANG = "fr"

    def serve(self, backend, client):
        stdin, stdout = _Pipe(client), _Pipe()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
            self.agent._rfb_serve(backend)
        return stdout.buffer.getvalue()

    CLIENT = (b"RFB 003.008\n" + b"\x01" + b"\x01" + struct.pack(">BxH", 2, 2) + struct.pack(">ii", 7, -258)
              + struct.pack(">BBHHHH", 3, 0, 0, 0, 2, 2)
              + struct.pack(">BBHII", 255, 0, 1, 0x3c, 0x56) + struct.pack(">BBHII", 255, 0, 0, 0x3c, 0x56)
              + struct.pack(">BBHII", 255, 0, 1, 0xe9, 0))         # é sans scancode, jamais relâché par le client
    ACK = struct.pack(">HHHHi", 0, 0, 0, 0, -258)

    def test_xt_scancodes_map_to_evdev(self):
        x = self.agent._xt_to_evdev
        self.assertEqual([x(0x1e), x(0x56), x(0x01), x(0x58)], [30, 86, 1, 88])   # A, touche < > ISO, Échap, F12
        self.assertEqual([x(0xc8), x(0xb8), x(0x9c), x(0xd3)], [103, 100, 96, 111])  # Haut, AltGr, Entrée pavé, Suppr
        self.assertEqual([x(0), x(0x7e)], [0, 0])

    def test_rfb_server_acks_qemu_keys_and_injects_physical_keys(self):
        backend = _FakeKeycodeBackend()
        out = self.serve(backend, self.CLIENT)
        self.assertIn(self.ACK, out)                                     # accusé de l'extension dans la mise à jour
        self.assertEqual(backend.codes, [(86, True), (86, False)])       # « < » par sa touche physique
        self.assertEqual(backend.keys, [(0xe9, True), (0xe9, False)])    # repli keysym, relâché à la fermeture

    def test_backend_without_keycodes_keeps_keysyms(self):
        backend = _FakeBackend()                                         # X11 : comportement inchangé
        out = self.serve(backend, self.CLIENT)
        self.assertNotIn(self.ACK, out)
        self.assertEqual(backend.keys[:2], [(0x3c, True), (0x3c, False)])

    def test_mutter_input_mapping(self):
        m = object.__new__(self.agent.MutterBackend)
        sent = []
        m._send, m._stream = (lambda method, sig, *args: sent.append((method, sig) + args)), "/stream"
        self.assertTrue(m.key(0x40, 1)); m.keycode(86, 0); m.motion(10, 20)
        m.button(1, True); m.button(3, False); m.button(4, True); m.button(4, False); m.button(5, True)
        self.assertEqual(sent, [
            ("NotifyKeyboardKeysym", "(ub)", 0x40, True), ("NotifyKeyboardKeycode", "(ub)", 86, False),
            ("NotifyPointerMotionAbsolute", "(sdd)", "/stream", 10.0, 20.0),
            ("NotifyPointerButton", "(ib)", 0x110, True), ("NotifyPointerButton", "(ib)", 0x111, False),
            ("NotifyPointerAxisDiscrete", "(ui)", 0, -1), ("NotifyPointerAxisDiscrete", "(ui)", 0, 1)])

    def test_mutter_pull_repacks_padded_rows(self):
        class Struct:
            def get_value(self, k): return 2
        class Caps:
            def get_structure(self, i): return Struct()
        class Buf:
            data = b"\x01" * 8 + b"PADD" + b"\x02" * 8 + b"PADD"           # 2 lignes de 2 px, pas de 12 octets
            def get_size(self): return len(self.data)
            def extract_dup(self, offset, size): return self.data
        class Sample:
            def get_caps(self): return Caps()
            def get_buffer(self): return Buf()
        m = object.__new__(self.agent.MutterBackend)
        m._Gst = types.SimpleNamespace(SECOND=1)
        m._sink = types.SimpleNamespace(emit=lambda name, timeout: Sample())
        self.assertEqual(m._pull(0), (2, 2, b"\x01" * 8 + b"\x02" * 8))

    def test_fit_frame_keeps_negotiated_size(self):
        fit = self.agent._fit_frame
        frame = bytes(range(16))                                         # 2x2
        self.assertIs(fit(frame, 2, 2, 2, 2), frame)
        self.assertEqual(fit(frame, 2, 2, 1, 1), frame[:4])               # rognage
        self.assertEqual(fit(frame[:4], 1, 1, 2, 1), frame[:4] + b"\0" * 4)   # complément noir

    def test_wayland_sessions_route_gnome_to_mutter(self):
        a = self.agent
        env = {"uid": 1000, "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0"}
        served = []
        for gnome, expected in ((True, "MutterBackend"), (False, "WaylandBackend")):
            with mock.patch.object(a, "x11_environment", side_effect=RuntimeError("Bureau Wayland")), \
                    mock.patch.object(a, "wayland_environment", return_value=env), \
                    mock.patch.object(a, "_gnome_session", return_value=gnome), \
                    mock.patch.object(a, "_login_screen", return_value=False), \
                    mock.patch.object(a, "MutterBackend", lambda e: "MutterBackend"), \
                    mock.patch.object(a, "WaylandBackend", lambda e: "WaylandBackend"), \
                    mock.patch.object(a, "_rfb_serve", served.append):
                a.linux_desktop()
            self.assertEqual(served[-1], expected)

    def test_login_screen_falls_back_to_text_console_or_explains(self):
        a = self.agent
        env = {"uid": 121, "XDG_RUNTIME_DIR": "/run/user/121", "WAYLAND_DISPLAY": "wayland-0"}
        with mock.patch.object(a, "x11_environment", side_effect=RuntimeError("Bureau Wayland")), \
                mock.patch.object(a, "wayland_environment", return_value=env), \
                mock.patch.object(a, "_login_screen", return_value=True), \
                mock.patch.object(a, "text_console", return_value="console"), \
                mock.patch.object(a, "MutterBackend", side_effect=AssertionError("pas de Mutter sur l'écran de connexion")):
            self.assertEqual(a.linux_desktop(terminal_fallback=True), "console")
            with self.assertRaisesRegex(RuntimeError, "Personne n'est connecté"):
                a.linux_desktop()

    def test_unlock_session_unlocks_only_the_locked_seat_session(self):
        a = self.agent
        calls, hints = [], iter(["yes", "no"])
        def run(cmd, **kw):
            calls.append(cmd[1:])
            out = {"list-sessions": "5 1000 testeur -     -    active no -\n16 1000 testeur seat0 tty2 active no -\n",
                   "show-session": None, "unlock-session": ""}[cmd[1]]
            return types.SimpleNamespace(stdout=next(hints) if out is None else out)
        with mock.patch.object(a.subprocess, "run", run), mock.patch.object(a.time, "sleep", lambda s: None):
            self.assertTrue(a._unlock_session(1000))
        self.assertIn(["unlock-session", "16"], calls)                  # session graphique (seat0), pas la session SSH
        with mock.patch.object(a.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
                stdout="16 1000 testeur seat0 tty2 active no -\n" if cmd[1] == "list-sessions" else "no")):
            self.assertFalse(a._unlock_session(1000))                    # déjà déverrouillée : rien à faire ni à rétablir

    def test_close_relocks_once_when_the_agent_unlocked(self):
        m = object.__new__(self.agent.MutterBackend)
        sent = []
        m._rd, m._pipe, m._bus, m._relock = None, None, object(), True
        m._call = lambda *args: sent.append(args)
        m.close(); m.close()
        self.assertEqual(sent, [("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "Lock")])
