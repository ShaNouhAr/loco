// Compagnon de contrôle graphique SYSTEM, isolé du tray utilisateur : RFB 3.8 sur un canal local privé du service loco-client.
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
        [MTAThread]
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
            catch (Exception e)
            {
                // Arrêt anormal : tracé dans le journal de l'agent pour le support (la session se ferme côté service).
                try
                {
                    string frames = String.Join(" < ", (e.StackTrace ?? "").Split('\n').Select(l => l.Trim()).Where(l => l.Length > 0).Take(3));
                    File.AppendAllText(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "loco-client", "loco-client.log"),
                        DateTime.Now.ToString("s") + Lang.T(" bureau distant interrompu : ", " remote desktop interrupted: ", " escritorio remoto interrumpido: ") + e.GetType().Name + " : " + e.Message + " | " + frames + Environment.NewLine);
                }
                catch (Exception) { }
                return 1;
            }
        }
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    }
    internal sealed class RfbDesktop
    {
        readonly Stream stream;
        readonly DesktopResolution resolution = new DesktopResolution();
        Rectangle bounds;
        int width, height;
        int requestedWidth, requestedHeight;
        bool pendingResolution;
        readonly Dictionary<uint, Input> pressed = new Dictionary<uint, Input>();
        RfbFramebuffer framebuffer;
        IntPtr inputDesktop;
        string desktopName = "";
        bool extendedSize, desktopNameEncoding;
        Rectangle captureArea;
        bool tight, jpeg, answerAlways, rgbOrder, cursorShape;
        IntPtr lastCursor = new IntPtr(-1);
        byte mouseMask;
        uint clipboardSeq;
        readonly Stopwatch frameTime = Stopwatch.StartNew();
        byte[] pixels; bool[] dirty;
        DesktopDuplication duplication; bool duplicationFresh; int duplicationRetryAt = Environment.TickCount, duplicationFailures;
        Bitmap gdiBitmap; Graphics gdiGraphics; int gdiInterval; readonly Stopwatch gdiTime = Stopwatch.StartNew();
        // Dernière forme de curseur (RGBA) : sert à reconnaître un curseur déjà dessiné dans l'image par Windows.
        byte[] cursorRgba; int cursorW, cursorH, cursorHotX, cursorHotY, bakedVotes; bool cursorInImage, sentBaked;
        public RfbDesktop(Stream stream)
        {
            this.stream = stream; bounds = SystemInformation.VirtualScreen;
            if (bounds.Width <= 0 || bounds.Height <= 0 || (long)bounds.Width * bounds.Height > 16777216) throw new IOException(Lang.T("Bureau indisponible", "Desktop unavailable", "Escritorio no disponible"));
            width = bounds.Width; height = bounds.Height;
            framebuffer = new RfbFramebuffer(width, height);
            captureArea = new Rectangle(0, 0, width, height);
            // Presse-papiers courant marqué « déjà vu » : on ne transmet que les changements survenant après l'ouverture de la session.
            clipboardSeq = GetClipboardSequenceNumber();
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
                bool pending = false, full = false, pipe = stream is NamedPipeClientStream;
                while (true)
                {
                    // Demande d'image en attente : y répondre dès que l'écran change, sans retarder la saisie qui arrive entre-temps.
                    if (pending && (!pipe || !InputPending()))
                    {
                        if (Update(full, !pipe || answerAlways)) { pending = false; full = false; }
                        continue;
                    }
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
                        var encodings = Read(count * 4); tight = false; jpeg = false; answerAlways = false; cursorShape = false; extendedSize = false; desktopNameEncoding = false;
                        for (int i = 0; i < count; i++)
                        {
                            uint encoding = I32(encodings, i * 4);
                            if (encoding == 7) tight = true;
                            // JPEG seulement pour un client qui annonce un niveau de qualité (noVNC) : un client sans
                            // décodeur JPEG (l'assistant IA de loco) reçoit du Tight sans perte (zlib).
                            if (encoding >= 0xffffffe0 && encoding <= 0xffffffe9) jpeg = true;
                            // Pseudo-encodage loco « LOC\x01 » : répondre à chaque demande, même vide, au lieu d'attendre un
                            // changement ; l'assistant IA sait alors tout de suite que l'image gardée est à jour.
                            if (encoding == 0x4c4f4301) answerAlways = true;
                            if (encoding == 0xfffffecc) extendedSize = true;
                            if (encoding == 0xfffffecd) desktopNameEncoding = true;
                            if (encoding == 0x574d5664) cursorShape = true;
                        }
                        lastCursor = new IntPtr(-1);
                    }
                    else if (kind == 3) { var request = Read(9); pending = true; full |= request[0] == 0; }
                    // Une saisie annonce souvent un changement d'écran : la capture GDI reprend aussitôt son rythme rapide.
                    else if (kind == 4) { var p = Read(7); gdiInterval = 0; if (AttachDesktop()) Key(I32(p, 3), p[0] != 0); }
                    else if (kind == 5) { var p = Read(5); gdiInterval = 0; if (AttachDesktop()) Pointer(p[0], I16(p, 1), I16(p, 3)); }
                    else if (kind == 6) { var p = Read(7); uint length = I32(p, 3); if (length > 256 * 1024) throw new InvalidDataException(); SetClipboard(FromCutText(Read((int)length))); }
                    else if (kind == 251)
                    {
                        var p = Read(7); int w = I16(p, 1), h = I16(p, 3), count = p[5];
                        var screens = Read(count * 16);
                        bool valid = extendedSize && count == 1 && I32(screens, 0) == 0 && I16(screens, 4) == 0 && I16(screens, 6) == 0 &&
                            I16(screens, 8) == w && I16(screens, 10) == h && I32(screens, 12) == 0 &&
                            w >= 160 && h >= 100 && w <= 4096 && h <= 2160;
                        if (valid)
                        {
                            requestedWidth = w; requestedHeight = h; pendingResolution = true;
                            AttachDesktop(); ApplyResolution();
                            // Toujours annoncer les pixels réels : le navigateur ajuste les modes imposés par le pilote.
                            SetFrameSize(bounds.Width, bounds.Height);
                        }
                        Write(SizeUpdate(1, valid ? 0 : 3));
                    }
                    else throw new InvalidDataException();
                }
            }
            catch (IOException) { }   // canal fermé par le service (fin de session), y compris pendant une écriture : sortie normale
            finally { ReleaseInput(); DropDuplication(); DropGdi(); resolution.Dispose(); }
        }
        void ReleaseInput()
        {
            foreach (var item in pressed.Values) { var input = item; input.Data.Keyboard.Flags |= 2; Send(input); }
            pressed.Clear();
            foreach (var button in new[] { new[] { 1, 4 }, new[] { 2, 64 }, new[] { 4, 16 } })
                if ((mouseMask & button[0]) != 0) { var input = new Input(); input.Data.Mouse.Flags = (uint)button[1]; Send(input); }
            mouseMask = 0;
        }
        bool InputPending()
        {
            uint read, available, left;
            var pipe = (NamedPipeClientStream)stream;
            // Canal fermé ou en erreur (fin de session côté service) : laisser Read() le constater, pour sortir aussitôt
            // et rétablir la résolution, au lieu d'attendre un changement d'écran jusqu'à être arrêté de force.
            if (!PeekNamedPipe(pipe.SafePipeHandle.DangerousGetHandle(), null, 0, out read, out available, out left)) return true;
            return available > 0;
        }
        // Répond à la demande d'image : true si une mise à jour est partie, false pour la garder (rien de neuf à l'écran).
        bool Update(bool full, bool force)
        {
            if (!AttachDesktop())
            {
                if (!full && !force) { Thread.Sleep(50); return false; }
                Write(new byte[4]); return true;
            }
            PollClipboard();
            if (pendingResolution) ApplyResolution();
            bool fresh = full;
            if (extendedSize && (bounds.Width != width || bounds.Height != height)) { SetFrameSize(bounds.Width, bounds.Height); fresh = true; }
            // Au plus ~30 images par seconde ; l'attente se fait par petites tranches pour laisser passer la saisie.
            int gap = 33 - (int)frameTime.ElapsedMilliseconds;
            if (!fresh && !force && gap > 0) { Thread.Sleep(Math.Min(gap, 10)); return false; }
            bool changed;
            // Une capture qui échoue (bureau en transition, mode en cours de changement…) ne doit jamais fermer la session.
            try { changed = Capture(fresh); }
            catch (Exception) { DropDuplication(); duplicationRetryAt = unchecked(Environment.TickCount + 1000); if (!fresh && !force) return false; Write(new byte[4]); return true; }
            if (changed && cursorShape && !cursorInImage && duplication != null) DetectCursorInImage();
            bool baked = cursorInImage && duplication != null;   // GDI n'incruste pas le curseur : en repli, renvoyer la vraie forme
            if (baked != sentBaked) { sentBaked = baked; lastCursor = new IntPtr(-1); }
            byte[] cursor = cursorShape ? CursorChange() : null;
            if (!fresh && !force && !changed && cursor == null) return false;
            byte[] update = framebuffer.Update(pixels, !fresh, tight, rgbOrder, cursor, fresh ? null : dirty, jpeg);
            Array.Clear(dirty, 0, dirty.Length);
            gdiInterval = update.Length > 4 ? 0 : Math.Min(400, gdiInterval + 50);
            if (!fresh && !force && update.Length == 4) return false;   // aucune tuile réellement modifiée
            if (extendedSize && fresh) Write(SizeUpdate(0, 0));
            Write(update); frameTime.Restart();
            return true;
        }
        // Image courante dans « pixels » : duplication DXGI quand c'est possible (lecture seulement si l'écran a changé),
        // sinon GDI. Renvoie true si une nouvelle image a été lue ; les tuiles à comparer sont marquées dans « dirty ».
        bool Capture(bool fresh)
        {
            if (pixels == null || pixels.Length != width * height * 4)
            {
                pixels = new byte[width * height * 4];
                dirty = new bool[((width + RfbFramebuffer.Tile - 1) / RfbFramebuffer.Tile) * ((height + RfbFramebuffer.Tile - 1) / RfbFramebuffer.Tile)];
                DropDuplication(); fresh = true;
            }
            if (width == bounds.Width && height == bounds.Height && String.Equals(desktopName, "Default", StringComparison.OrdinalIgnoreCase))
            {
                if (duplication == null && unchecked(Environment.TickCount - duplicationRetryAt) >= 0)
                {
                    try { duplication = DesktopDuplication.Open(bounds); } catch (Exception) { duplication = null; }
                    // Échec souvent transitoire juste après un changement de mode : nouvel essai rapide, puis espacé (GDI entre-temps).
                    if (duplication == null) duplicationRetryAt = unchecked(Environment.TickCount + Math.Min(10000, 250 << Math.Min(duplicationFailures++, 6)));
                    else { duplicationFresh = true; duplicationFailures = 0; }
                }
                if (duplication != null)
                {
                    // La première image d'une duplication couvre tout l'écran : l'attendre un peu plus longtemps.
                    int result;
                    try { result = duplication.Capture(pixels, duplicationFresh ? 300 : 16, RfbFramebuffer.Tile, dirty); }
                    catch (Exception) { result = -1; }
                    if (result > 0) { duplicationFresh = false; return true; }
                    if (result == 0 && !duplicationFresh) return false;   // rien de neuf : le tampon contient déjà l'écran courant
                    DropDuplication(); duplicationRetryAt = unchecked(Environment.TickCount + 100);   // perdue (bureau, mode) : GDI cette fois, réouverture aussitôt
                }
            }
            // GDI : coûteux sur certaines machines virtuelles, donc espacé tant que l'écran ne change pas.
            if (!fresh && gdiTime.ElapsedMilliseconds < gdiInterval) { Thread.Sleep(5); return false; }
            CaptureGdi(); gdiTime.Restart();
            for (int i = 0; i < dirty.Length; i++) dirty[i] = true;
            return true;
        }
        void CaptureGdi()
        {
            if (gdiBitmap == null || gdiBitmap.Width != width || gdiBitmap.Height != height)
            {
                DropGdi();
                gdiBitmap = new Bitmap(width, height, PixelFormat.Format32bppRgb); gdiGraphics = Graphics.FromImage(gdiBitmap);
            }
            if (width == bounds.Width && height == bounds.Height) gdiGraphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
            else using (var native = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppRgb))
            {
                using (var capture = Graphics.FromImage(native)) capture.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                gdiGraphics.Clear(Color.Black);
                gdiGraphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                gdiGraphics.DrawImage(native, captureArea);
            }
            var bits = gdiBitmap.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
            try { for (int y = 0; y < height; y++) Marshal.Copy(IntPtr.Add(bits.Scan0, y * bits.Stride), pixels, y * width * 4, width * 4); }
            finally { gdiBitmap.UnlockBits(bits); }
        }
        void DropDuplication() { if (duplication != null) { duplication.Dispose(); duplication = null; } }
        void DropGdi()
        {
            if (gdiGraphics != null) { gdiGraphics.Dispose(); gdiGraphics = null; }
            if (gdiBitmap != null) { gdiBitmap.Dispose(); gdiBitmap = null; }
        }
        byte[] SizeUpdate(int reason, int status)
        {
            return Join(new byte[] { 0, 0, 0, 1 }, U16(reason), U16(status), U16(width), U16(height), U32(0xfffffecc),
                new byte[] { 1, 0, 0, 0 }, U32(0), U16(0), U16(0), U16(width), U16(height), U32(0));
        }
        void UpdateCaptureArea()
        {
            double scale = Math.Min((double)width / bounds.Width, (double)height / bounds.Height);
            int w = Math.Max(1, (int)Math.Round(bounds.Width * scale)), h = Math.Max(1, (int)Math.Round(bounds.Height * scale));
            captureArea = new Rectangle((width - w) / 2, (height - h) / 2, w, h);
        }
        void SetFrameSize(int w, int h)
        {
            width = w; height = h;
            framebuffer = new RfbFramebuffer(w, h); lastCursor = new IntPtr(-1); UpdateCaptureArea();
        }
        void ApplyResolution()
        {
            // Le bureau sécurisé conserve la taille courante ; appliquer la dernière demande au retour du bureau.
            if (!pendingResolution || !String.Equals(desktopName, "Default", StringComparison.OrdinalIgnoreCase)) return;
            pendingResolution = false;
            resolution.Resize(requestedWidth, requestedHeight);
            bounds = SystemInformation.VirtualScreen;
            UpdateCaptureArea();
        }
        bool AttachDesktop()
        {
            // Aucun HWND ni hook dans ce thread : il peut suivre Default <-> Winlogon (UAC).
            IntPtr next = OpenInputDesktop(0, false, 0x1a1); // READOBJECTS | WRITEOBJECTS | JOURNALPLAYBACK | SWITCHDESKTOP
            if (next == IntPtr.Zero) return false;
            var name = new StringBuilder(256); int needed;
            if (!GetUserObjectInformation(next, 2, name, 512, out needed)) { CloseDesktop(next); return false; }
            if (inputDesktop != IntPtr.Zero && desktopName == name.ToString()) CloseDesktop(next);
            else
            {
                if (!SetThreadDesktop(next)) { CloseDesktop(next); return false; }
                if (inputDesktop != IntPtr.Zero) CloseDesktop(inputDesktop);
                inputDesktop = next;
            }
            if (desktopName != name.ToString())
            {
                // Ne pas rejouer une touche maintenue sur l'autre bureau.
                ReleaseInput(); DropDuplication();
                desktopName = name.ToString(); lastCursor = new IntPtr(-1);
                if (desktopNameEncoding)
                {
                    var label = Encoding.UTF8.GetBytes((String.Equals(desktopName, "Default", StringComparison.OrdinalIgnoreCase) ? "loco-desktop:" : "loco-secure:") + Environment.MachineName);
                    Write(Join(new byte[] { 0, 0, 0, 1 }, new byte[8], U32(0xfffffecd), U32((uint)label.Length), label));
                }
                framebuffer = new RfbFramebuffer(width, height);
            }
            var current = SystemInformation.VirtualScreen;
            if (current.Width > 0 && current.Height > 0 && (long)current.Width * current.Height <= 16777216)
            { bounds = current; UpdateCaptureArea(); }
            return true;
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
            byte[] shape = CursorShape(handle);
            if (shape != null && shape.Length > 14)
            {
                // Pseudo-rectangle : point chaud x, y, largeur, hauteur (U16), encodage (U32), type, bourrage, puis RGBA.
                cursorHotX = (shape[0] << 8) | shape[1]; cursorHotY = (shape[2] << 8) | shape[3];
                cursorW = (shape[4] << 8) | shape[5]; cursorH = (shape[6] << 8) | shape[7];
                cursorRgba = new byte[shape.Length - 14]; Buffer.BlockCopy(shape, 14, cursorRgba, 0, cursorRgba.Length);
            }
            // Curseur déjà incrusté dans l'image (pilote sans curseur matériel : VM, adaptateur de base) : n'en afficher
            // aucun second côté navigateur ; noVNC y montre un simple point pour le retour immédiat de la position.
            return sentBaked ? RfbFramebuffer.CursorRect(0, 0, 1, 1, new byte[4]) : shape;
        }
        // Reconnaît le curseur de Windows dans l'image capturée, à la position du pointeur : si ses pixels opaques y sont
        // (deux fois), le pilote dessine le curseur dans l'image et le curseur local du navigateur ferait double emploi.
        void DetectCursorInImage()
        {
            if (cursorRgba == null || cursorW * cursorH * 4 != cursorRgba.Length || width != bounds.Width || height != bounds.Height) return;
            var info = new CursorInfo { Size = Marshal.SizeOf(typeof(CursorInfo)) };
            if (!GetCursorInfo(ref info) || (info.Flags & 1) == 0) return;
            // Pixels nettement sombres ou clairs de la forme, comparés par classe de luminance : le contour incrusté est
            // mélangé au fond (noir légèrement teinté), mais le motif sombre/clair à la position du pointeur reste net.
            // Chaque classe est jugée à part : le remplissage clair doit y être (≥ 90 %) ET le contour sombre (≥ 60 %, ses bords
            // sont fondus dans le fond). Sans curseur incrusté, aucun fond ne réunit les deux à la forme exacte du pointeur.
            int ox = info.X - bounds.Left - cursorHotX, oy = info.Y - bounds.Top - cursorHotY, dark = 0, light = 0, darkMatch = 0, lightMatch = 0;
            for (int y = 0; y < cursorH; y++)
                for (int x = 0; x < cursorW; x++)
                {
                    int s = (y * cursorW + x) * 4, px = ox + x, py = oy + y;
                    if (cursorRgba[s + 3] < 250 || px < 0 || py < 0 || px >= width || py >= height) continue;
                    int d = (py * width + px) * 4, sum = cursorRgba[s] + cursorRgba[s + 1] + cursorRgba[s + 2], seen = pixels[d] + pixels[d + 1] + pixels[d + 2];
                    if (sum < 150) { dark++; if (seen < 220) darkMatch++; }
                    else if (sum > 600) { light++; if (seen > 540) lightMatch++; }
                }
            if (dark < 10 || light < 10) return;   // forme peu discriminante (point, curseur uni) : ne pas conclure
            if (lightMatch * 100 >= light * 90 && darkMatch * 100 >= dark * 60) { if (++bakedVotes >= 2) { cursorInImage = true; lastCursor = new IntPtr(-1); } }
            else if (lightMatch * 100 < light * 50 || darkMatch * 100 < dark * 30) bakedVotes = 0;
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
            long px = bounds.Left + Math.Max(0, Math.Min(captureArea.Width - 1, x - captureArea.Left)) * (long)bounds.Width / captureArea.Width;
            long py = bounds.Top + Math.Max(0, Math.Min(captureArea.Height - 1, y - captureArea.Top)) * (long)bounds.Height / captureArea.Height;
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
            // Ctrl+Alt+Suppr : la séquence sécurisée de Windows ne peut pas être simulée par une frappe.
            if ((sym == 0xffff || sym == 0xff9f) && (pressed.ContainsKey(0xffe3) || pressed.ContainsKey(0xffe4)) &&
                (pressed.ContainsKey(0xffe9) || pressed.ContainsKey(0xffea)))
            {
                SecureAttention();
                return;
            }
            ushort vk = 0;
            var special = new Dictionary<uint, ushort> { {0xff08,8},{0xff09,9},{0xff0d,13},{0xff1b,27},{0xff50,36},{0xff51,37},{0xff52,38},{0xff53,39},{0xff54,40},
                {0xff55,33},{0xff56,34},{0xff57,35},{0xff63,45},{0xffff,46},{0xffe1,160},{0xffe2,161},{0xffe3,162},{0xffe4,163},
                {0xffe9,164},{0xffea,165},{0xffeb,91},{0xffec,92},{0xffe5,20},{0xff7f,144},{0xff14,145},{0xff61,44},{0xff67,93},{0xff13,19} };
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
        static void Send(Input input) { SendInput(1, new[] { input }, Marshal.SizeOf(typeof(Input))); }
        static void SecureAttention()
        {
            // Seul un service peut envoyer la séquence sécurisée : la demander au service loco-client (événement réservé à SYSTEM).
            try { using (var request = EventWaitHandle.OpenExisting(@"Global\loco-sas")) request.Set(); }
            catch (Exception) { }
        }
        // Presse-papiers texte partagé, uniquement sur le bureau utilisateur (Default), jamais sur le bureau sécurisé.
        void PollClipboard()
        {
            if (!String.Equals(desktopName, "Default", StringComparison.OrdinalIgnoreCase)) return;
            uint seq = GetClipboardSequenceNumber();
            if (seq == clipboardSeq) return;
            if (!OpenClipboard(IntPtr.Zero)) return;   // occupé : nouvelle tentative à la frame suivante (seq inchangé)
            string text = null;
            try
            {
                IntPtr handle = GetClipboardData(13);   // CF_UNICODETEXT
                if (handle != IntPtr.Zero)
                {
                    IntPtr p = GlobalLock(handle);
                    if (p != IntPtr.Zero) { try { text = Marshal.PtrToStringUni(p); } finally { GlobalUnlock(handle); } }
                }
            }
            finally { CloseClipboard(); }
            clipboardSeq = seq;
            if (String.IsNullOrEmpty(text)) return;
            byte[] body = CutTextBytes(text);
            Write(Join(new byte[] { 3, 0, 0, 0 }, U32((uint)body.Length), body));   // ServerCutText
        }
        void SetClipboard(string text)
        {
            if (!String.Equals(desktopName, "Default", StringComparison.OrdinalIgnoreCase) || !OpenClipboard(IntPtr.Zero)) return;
            try
            {
                EmptyClipboard();
                byte[] bytes = Encoding.Unicode.GetBytes(text + "\0");
                IntPtr mem = GlobalAlloc(0x2042, (UIntPtr)bytes.Length);   // GHND : déplaçable et initialisé à zéro
                if (mem == IntPtr.Zero) return;
                IntPtr p = GlobalLock(mem);
                if (p == IntPtr.Zero) { GlobalFree(mem); return; }
                Marshal.Copy(bytes, 0, p, bytes.Length); GlobalUnlock(mem);
                if (SetClipboardData(13, mem) == IntPtr.Zero) GlobalFree(mem);   // libérer seulement si le presse-papiers n'a pas pris la main
            }
            finally { CloseClipboard(); clipboardSeq = GetClipboardSequenceNumber(); }   // ne pas se renvoyer sa propre écriture
        }
        // Le texte « cut » RFB est en Latin-1, sauts de ligne LF ; le presse-papiers Windows utilise UTF-16 et CRLF.
        static byte[] CutTextBytes(string text)
        {
            if (text.Length > 100000) text = text.Substring(0, 100000);
            var bytes = new List<byte>(text.Length);
            char prev = '\0';
            foreach (char c in text)
            {
                if (c == '\n') { if (prev != '\r') bytes.Add(10); }
                else if (c == '\r') bytes.Add(10);
                else bytes.Add(c <= 0xff ? (byte)c : (byte)'?');
                prev = c;
            }
            return bytes.ToArray();
        }
        static string FromCutText(byte[] body)
        {
            var text = new StringBuilder(body.Length);
            foreach (byte b in body) { if (b == 10) text.Append("\r\n"); else if (b != 13) text.Append((char)b); }
            return text.ToString();
        }
        [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputData Data; }
        [StructLayout(LayoutKind.Explicit)] struct InputData { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyboardInput Keyboard; }
        [StructLayout(LayoutKind.Sequential)] struct MouseInput { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct KeyboardInput { public ushort Key, Scan; public uint Flags, Time; public UIntPtr Extra; }
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool PeekNamedPipe(IntPtr pipe, byte[] buffer, uint size, out uint read, out uint available, out uint left);
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
        [DllImport("user32.dll", SetLastError = true)] static extern bool SetThreadDesktop(IntPtr desktop);
        [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
        [DllImport("user32.dll")] static extern bool OpenClipboard(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool CloseClipboard();
        [DllImport("user32.dll")] static extern bool EmptyClipboard();
        [DllImport("user32.dll")] static extern IntPtr GetClipboardData(uint format);
        [DllImport("user32.dll")] static extern IntPtr SetClipboardData(uint format, IntPtr data);
        [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();
        [DllImport("kernel32.dll")] static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
        [DllImport("kernel32.dll")] static extern IntPtr GlobalFree(IntPtr mem);
        [DllImport("kernel32.dll")] static extern IntPtr GlobalLock(IntPtr mem);
        [DllImport("kernel32.dll")] static extern bool GlobalUnlock(IntPtr mem);
    }
}
