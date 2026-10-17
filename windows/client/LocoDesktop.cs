// Compagnon graphique non privilégié : RFB 3.8 sur un canal local privé du service loco-client.
// Aucune écoute TCP, aucun jeton loco, aucune action de mise à jour.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace Loco
{
    internal static class DesktopProgram
    {
        [STAThread]
        public static int Main(string[] args)
        {
            if (args.Length != 2 || args[0] != "--pipe" || !Regex.IsMatch(args[1], "^loco-desktop-[0-9a-f]{64}$")) return 2;
            SetProcessDPIAware();
            try
            {
                using (var pipe = new NamedPipeClientStream(".", args[1], PipeDirection.InOut, PipeOptions.None))
                { pipe.Connect(15000); new RfbDesktop(pipe).Run(); }
                return 0;
            }
            catch (Exception) { return 1; }
        }
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    }
    internal sealed class RfbDesktop
    {
        readonly Stream stream;
        readonly Rectangle bounds;
        readonly int width, height;
        readonly Dictionary<uint, Input> pressed = new Dictionary<uint, Input>();
        readonly RfbFramebuffer framebuffer;
        bool tight, rgbOrder, cursorShape;
        IntPtr lastCursor = new IntPtr(-1);
        byte mouseMask;
        readonly Stopwatch frameTime = Stopwatch.StartNew();
        public RfbDesktop(Stream stream)
        {
            this.stream = stream; bounds = SystemInformation.VirtualScreen;
            if (bounds.Width <= 0 || bounds.Height <= 0 || (long)bounds.Width * bounds.Height > 16777216) throw new IOException("Bureau indisponible");
            width = bounds.Width; height = bounds.Height;
            framebuffer = new RfbFramebuffer(width, height);
        }
        byte[] Read(int n)
        {
            if (n < 0 || n > 1024 * 1024) throw new InvalidDataException();
            var buffer = new byte[n]; int position = 0;
            while (position < n) { int count = stream.Read(buffer, position, n - position); if (count == 0) throw new EndOfStreamException(); position += count; }
            return buffer;
        }
        void Write(byte[] data) { stream.Write(data, 0, data.Length); stream.Flush(); }
        static byte[] U16(int n) { return new[] { (byte)(n >> 8), (byte)n }; }
        static byte[] U32(uint n) { return new[] { (byte)(n >> 24), (byte)(n >> 16), (byte)(n >> 8), (byte)n }; }
        static int I16(byte[] b, int p) { return (b[p] << 8) | b[p + 1]; }
        static uint I32(byte[] b, int p) { return ((uint)b[p] << 24) | ((uint)b[p + 1] << 16) | ((uint)b[p + 2] << 8) | b[p + 3]; }
        static byte[] Join(params byte[][] parts)
        { byte[] data = new byte[parts.Sum(p => p.Length)]; int i = 0; foreach (var p in parts) { Buffer.BlockCopy(p, 0, data, i, p.Length); i += p.Length; } return data; }
        public void Run()
        {
            try
            {
                Write(Encoding.ASCII.GetBytes("RFB 003.008\n"));
                if (Encoding.ASCII.GetString(Read(12)) != "RFB 003.008\n") throw new InvalidDataException();
                Write(new byte[] { 1, 1 }); if (Read(1)[0] != 1) throw new InvalidDataException();
                Write(new byte[4]); Read(1);
                byte[] name = Encoding.UTF8.GetBytes(Environment.MachineName);
                Write(Join(U16(width), U16(height), new byte[] { 32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0 }, U32((uint)name.Length), name));
                while (true)
                {
                    int kind = Read(1)[0];
                    if (kind == 0)
                    {
                        var p = Read(19);
                        if (p[3] != 32 || p[4] != 24 || p[5] != 0 || p[6] != 1 || I16(p, 7) != 255 || I16(p, 9) != 255 || I16(p, 11) != 255 ||
                            p[14] != 8 || !((p[13] == 16 && p[15] == 0) || (p[13] == 0 && p[15] == 16))) throw new InvalidDataException();
                        rgbOrder = p[13] == 0;
                    }
                    else if (kind == 2)
                    {
                        int count = I16(Read(3), 1); if (count > 128) throw new InvalidDataException();
                        var encodings = Read(count * 4); tight = false; cursorShape = false;
                        for (int i = 0; i < count; i++)
                        {
                            uint encoding = I32(encodings, i * 4);
                            if (encoding == 7) tight = true;
                            if (encoding == 0x574d5664) cursorShape = true;
                        }
                        lastCursor = new IntPtr(-1);
                    }
                    else if (kind == 3) { var request = Read(9); Frame(request[0] != 0); }
                    else if (kind == 4) { var p = Read(7); Key(I32(p, 3), p[0] != 0); }
                    else if (kind == 5) { var p = Read(5); Pointer(p[0], I16(p, 1), I16(p, 3)); }
                    else if (kind == 6) { var p = Read(7); uint length = I32(p, 3); if (length > 1024 * 1024) throw new InvalidDataException(); Read((int)length); }
                    else throw new InvalidDataException();
                }
            }
            catch (EndOfStreamException) { }
            finally
            {
                foreach (var item in pressed.Values) { var input = item; input.Data.Keyboard.Flags |= 2; try { Send(input); } catch (IOException) { } }
                pressed.Clear();
                foreach (var b in new[] { new[] { 1, 4 }, new[] { 2, 64 }, new[] { 4, 16 } })
                    if ((mouseMask & b[0]) != 0) { var input = new Input(); input.Data.Mouse.Flags = (uint)b[1]; try { Send(input); } catch (IOException) { } }
            }
        }
        void Frame(bool incremental)
        {
            int wait = 40 - (int)frameTime.ElapsedMilliseconds; if (wait > 0) Thread.Sleep(wait); frameTime.Restart();
            IntPtr desktop = OpenInputDesktop(0, false, 1);
            if (desktop == IntPtr.Zero) throw new IOException("Session verrouillée ou écran sécurisé");
            try
            {
                var name = new StringBuilder(256); int needed;
                if (!GetUserObjectInformation(desktop, 2, name, 512, out needed) || !String.Equals(name.ToString(), "Default", StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Écran sécurisé");
            }
            finally { CloseDesktop(desktop); }
            using (var source = new Bitmap(width, height, PixelFormat.Format32bppRgb))
            {
                using (var graphics = Graphics.FromImage(source)) graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                var bitmap = source.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
                byte[] data = new byte[width * height * 4];
                try
                {
                    for (int y = 0; y < height; y++) Marshal.Copy(IntPtr.Add(bitmap.Scan0, y * bitmap.Stride), data, y * width * 4, width * 4);
                }
                finally { source.UnlockBits(bitmap); }
                Write(framebuffer.Update(data, incremental, tight, rgbOrder, cursorShape ? CursorChange() : null));
            }
        }

        // Forme du curseur quand elle change (flèche, texte, main, redimensionnement…). Les curseurs monochromes
        // à inversion (comme le I du texte) sont dessinés en noir bordé de blanc, lisibles sur tous les fonds.
        byte[] CursorChange()
        {
            var info = new CursorInfo { Size = Marshal.SizeOf(typeof(CursorInfo)) };
            if (!GetCursorInfo(ref info)) return null;
            IntPtr handle = (info.Flags & 1) != 0 ? info.Cursor : IntPtr.Zero;
            if (handle == lastCursor) return null;
            lastCursor = handle;
            return CursorShape(handle);
        }
        internal static byte[] CursorShape(IntPtr handle)
        {
            if (handle == IntPtr.Zero) return RfbFramebuffer.CursorRect(0, 0, 1, 1, new byte[4]);
            IconInfo icon;
            if (!GetIconInfo(handle, out icon)) return null;
            int w, h;
            try
            {
                var bm = new BitmapInfo();
                IntPtr reference = icon.Color != IntPtr.Zero ? icon.Color : icon.Mask;
                if (GetObject(reference, Marshal.SizeOf(typeof(BitmapInfo)), ref bm) == 0) return null;
                w = bm.Width; h = icon.Color != IntPtr.Zero ? bm.Height : bm.Height / 2;
            }
            finally
            {
                if (icon.Mask != IntPtr.Zero) DeleteObject(icon.Mask);
                if (icon.Color != IntPtr.Zero) DeleteObject(icon.Color);
            }
            if (w < 1 || h < 1 || w > 256 || h > 256) return null;
            byte[] black = Render(handle, w, h, Color.Black), white = Render(handle, w, h, Color.White);
            var rgba = new byte[w * h * 4];
            var inverted = new bool[w * h];
            for (int i = 0; i < w * h; i++)
            {
                int p = i * 4;
                // Écart entre les deux fonds : 0 pour un pixel opaque, 255 pour un pixel transparent.
                int spread = (white[p] - black[p] + white[p + 1] - black[p + 1] + white[p + 2] - black[p + 2]) / 3;
                if (black[p] + black[p + 1] + black[p + 2] > 600 && white[p] + white[p + 1] + white[p + 2] < 165) { inverted[i] = true; continue; }
                int alpha = Math.Max(0, Math.Min(255, 255 - spread));
                if (alpha == 0) continue;
                for (int c = 0; c < 3; c++) rgba[p + c] = (byte)Math.Min(255, black[p + 2 - c] * 255 / alpha);
                rgba[p + 3] = (byte)alpha;
            }
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++)
                {
                    int i = y * w + x;
                    if (inverted[i]) { rgba[i * 4 + 3] = 255; continue; }
                    if (rgba[i * 4 + 3] != 0) continue;
                    bool edge = false;
                    for (int dy = -1; dy <= 1 && !edge; dy++)
                        for (int dx = -1; dx <= 1 && !edge; dx++)
                        {
                            int nx = x + dx, ny = y + dy;
                            edge = nx >= 0 && ny >= 0 && nx < w && ny < h && inverted[ny * w + nx];
                        }
                    if (edge) { rgba[i * 4] = rgba[i * 4 + 1] = rgba[i * 4 + 2] = rgba[i * 4 + 3] = 255; }
                }
            // Curseur entièrement clair (I du texte selon le thème) : contour noir d'un pixel, lisible sur fond clair.
            bool light = true, any = false;
            for (int i = 0; i < w * h && light; i++)
                if (rgba[i * 4 + 3] >= 128) { any = true; light = rgba[i * 4] + rgba[i * 4 + 1] + rgba[i * 4 + 2] > 600; }
            if (any && light)
            {
                var outline = (byte[])rgba.Clone();
                for (int y = 0; y < h; y++)
                    for (int x = 0; x < w; x++)
                    {
                        if (rgba[(y * w + x) * 4 + 3] >= 128) continue;
                        bool edge = false;
                        for (int dy = -1; dy <= 1 && !edge; dy++)
                            for (int dx = -1; dx <= 1 && !edge; dx++)
                            {
                                int nx = x + dx, ny = y + dy;
                                edge = nx >= 0 && ny >= 0 && nx < w && ny < h && rgba[(ny * w + nx) * 4 + 3] >= 128;
                            }
                        if (edge) { int o = (y * w + x) * 4; outline[o] = outline[o + 1] = outline[o + 2] = 0; outline[o + 3] = 255; }
                    }
                rgba = outline;
            }
            return RfbFramebuffer.CursorRect(icon.HotX, icon.HotY, w, h, rgba);
        }
        static byte[] Render(IntPtr cursor, int w, int h, Color background)
        {
            using (var bitmap = new Bitmap(w, h, PixelFormat.Format32bppRgb))
            {
                using (var graphics = Graphics.FromImage(bitmap))
                {
                    graphics.Clear(background);
                    IntPtr dc = graphics.GetHdc();
                    try { DrawIconEx(dc, 0, 0, cursor, w, h, 0, IntPtr.Zero, 3); }
                    finally { graphics.ReleaseHdc(dc); }
                }
                var bits = bitmap.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
                var data = new byte[w * h * 4];
                try { for (int y = 0; y < h; y++) Marshal.Copy(IntPtr.Add(bits.Scan0, y * bits.Stride), data, y * w * 4, w * 4); }
                finally { bitmap.UnlockBits(bits); }
                return data;   // ordre BGRX
            }
        }

        void Pointer(byte mask, int x, int y)
        {
            long px = bounds.Left + Math.Min(width - 1, x) * (long)bounds.Width / width;
            long py = bounds.Top + Math.Min(height - 1, y) * (long)bounds.Height / height;
            var input = new Input { Type = 0 }; input.Data.Mouse.X = (int)((px - bounds.Left) * 65535 / Math.Max(1, bounds.Width - 1));
            input.Data.Mouse.Y = (int)((py - bounds.Top) * 65535 / Math.Max(1, bounds.Height - 1)); input.Data.Mouse.Flags = 0x8000 | 0x4000 | 1;
            Send(input);
            foreach (var b in new[] { new[] { 1, 2, 4 }, new[] { 2, 32, 64 }, new[] { 4, 8, 16 } })
                if (((mouseMask ^ mask) & b[0]) != 0) { input.Data.Mouse.Flags = (uint)((mask & b[0]) != 0 ? b[1] : b[2]); Send(input); }
            foreach (var b in new[] { new[] { 8, 120 }, new[] { 16, -120 } })
                if ((mask & b[0]) != 0 && (mouseMask & b[0]) == 0) { input.Data.Mouse.Flags = 0x0800; input.Data.Mouse.Data = unchecked((uint)b[1]); Send(input); }
            mouseMask = mask;
        }
        void Key(uint sym, bool down)
        {
            Input input;
            if (!down)
            {
                if (!pressed.TryGetValue(sym, out input)) return;
                input.Data.Keyboard.Flags |= 2; Send(input); pressed.Remove(sym); return;
            }
            ushort vk = 0;
            var special = new Dictionary<uint, ushort> { {0xff08,8},{0xff09,9},{0xff0d,13},{0xff1b,27},{0xff50,36},{0xff51,37},{0xff52,38},{0xff53,39},{0xff54,40},
                {0xff55,33},{0xff56,34},{0xff57,35},{0xff63,45},{0xffff,46},{0xffe1,160},{0xffe2,161},{0xffe3,162},{0xffe4,163},
                {0xffe9,164},{0xffea,165},{0xffeb,91},{0xffec,92},{0xffe5,20},{0xff7f,144},{0xff14,145} };
            if (special.ContainsKey(sym)) vk = special[sym];
            else if (sym >= 0xffbe && sym <= 0xffd5) vk = (ushort)(112 + sym - 0xffbe);
            input = new Input { Type = 1 };
            if (vk != 0) { input.Data.Keyboard.Key = vk; if (vk >= 33 && vk <= 46 || vk == 163 || vk == 165 || vk == 91 || vk == 92) input.Data.Keyboard.Flags = 1; }
            else
            {
                uint scalar = sym >= 0x01000000 ? sym - 0x01000000 : sym;
                if (scalar < 32 || scalar > 0xffff) return;
                if (pressed.ContainsKey(0xffe3) || pressed.ContainsKey(0xffe4) || pressed.ContainsKey(0xffe9) || pressed.ContainsKey(0xffea) ||
                    pressed.ContainsKey(0xffeb) || pressed.ContainsKey(0xffec))
                { short mapped = VkKeyScan((char)scalar); if (mapped == -1) return; input.Data.Keyboard.Key = (ushort)(mapped & 255); }
                else { input.Data.Keyboard.Scan = (ushort)scalar; input.Data.Keyboard.Flags = 4; }
            }
            Send(input); pressed[sym] = input;
        }
        static void Send(Input input) { if (SendInput(1, new[] { input }, Marshal.SizeOf(typeof(Input))) != 1) throw new IOException("Saisie bloquée par le bureau Windows"); }
        [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputData Data; }
        [StructLayout(LayoutKind.Explicit)] struct InputData { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyboardInput Keyboard; }
        [StructLayout(LayoutKind.Sequential)] struct MouseInput { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct KeyboardInput { public ushort Key, Scan; public uint Flags, Time; public UIntPtr Extra; }
        [DllImport("user32.dll")] static extern uint SendInput(uint count, Input[] input, int size);
        [StructLayout(LayoutKind.Sequential)] struct CursorInfo { public int Size, Flags; public IntPtr Cursor; public int X, Y; }
        [StructLayout(LayoutKind.Sequential)] struct IconInfo { public bool Icon; public int HotX, HotY; public IntPtr Mask, Color; }
        [StructLayout(LayoutKind.Sequential)] struct BitmapInfo { public int Type, Width, Height, WidthBytes; public ushort Planes, BitsPixel; public IntPtr Bits; }
        [DllImport("user32.dll")] static extern bool GetCursorInfo(ref CursorInfo info);
        [DllImport("user32.dll")] static extern bool GetIconInfo(IntPtr icon, out IconInfo info);
        [DllImport("user32.dll")] static extern bool DrawIconEx(IntPtr dc, int x, int y, IntPtr icon, int w, int h, int step, IntPtr brush, int flags);
        [DllImport("gdi32.dll")] static extern int GetObject(IntPtr handle, int size, ref BitmapInfo info);
        [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr handle);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern short VkKeyScan(char c);
        [DllImport("user32.dll", SetLastError = true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder data, int length, out int needed);
        [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    }
}
