"""Décode les vrais flux Tight Linux/.NET : pixels exacts, 4K, zones modifiées et rafraîchissement complet."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
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
