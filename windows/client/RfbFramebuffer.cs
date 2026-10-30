// Images RFB natives : rectangles modifiés, zones d'interface sans perte (Tight zlib) et zones complexes en JPEG, curseur à transparence.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;

namespace Loco
{
    internal sealed class RfbFramebuffer
    {
        readonly int width, height;
        byte[] previous;
        internal const int Tile = 128;
        // Au-delà de ce nombre de couleurs distinctes, une tuile est « photographique » (photo, dégradé, fond d'écran) : JPEG.
        // En dessous (texte, aplats, icônes), compression Tight zlib sans perte, nette au pixel.
        const int ColorThreshold = 256;
        static readonly EncoderParameters JpegQuality = JpegParams(80L);
        static ImageCodecInfo jpegCodec;
        static bool jpegResolved;
        static EncoderParameters JpegParams(long quality)
        {
            var p = new EncoderParameters(1); p.Param[0] = new EncoderParameter(Encoder.Quality, quality); return p;
        }
        // Résolu à la première tuile complexe seulement : sans GDI+/libgdiplus, on reste simplement en zlib sans perte.
        static ImageCodecInfo JpegCodec()
        {
            if (!jpegResolved)
            {
                jpegResolved = true;
                try { foreach (var codec in ImageCodecInfo.GetImageEncoders()) if (codec.FormatID == ImageFormat.Jpeg.Guid) { jpegCodec = codec; break; } }
                catch (Exception) { jpegCodec = null; }
            }
            return jpegCodec;
        }
        public RfbFramebuffer(int width, int height)
        {
            if (width < 1 || height < 1 || width > 65535 || height > 65535 || (long)width * height > 16777216)
                throw new ArgumentOutOfRangeException();
            this.width = width; this.height = height;
        }
        static void U16(Stream stream, int n) { stream.WriteByte((byte)(n >> 8)); stream.WriteByte((byte)n); }
        static void U32(Stream stream, uint n) { U16(stream, (int)(n >> 16)); U16(stream, (int)n); }
        static void Compact(Stream stream, int n)
        {
            stream.WriteByte((byte)((n & 127) | (n > 127 ? 128 : 0)));
            if (n > 127) stream.WriteByte((byte)(((n >> 7) & 127) | (n > 16383 ? 128 : 0)));
            if (n > 16383) stream.WriteByte((byte)(n >> 14));
        }
        static byte[] Zlib(byte[] data)
        {
            using (var memory = new MemoryStream())
            {
                // DeflateStream (.NET Framework) produit du DEFLATE brut, Tight attend un flux zlib.
                memory.WriteByte(0x78); memory.WriteByte(0x01);
                using (var deflate = new DeflateStream(memory, CompressionLevel.Fastest, true)) deflate.Write(data, 0, data.Length);
                uint a = 1, b = 0;
                for (int pos = 0; pos < data.Length;)
                {
                    int end = Math.Min(pos + 5552, data.Length);
                    while (pos < end) { a += data[pos++]; b += a; }
                    a %= 65521; b %= 65521;
                }
                U32(memory, (b << 16) | a); return memory.ToArray();
            }
        }
        /// <summary>Pseudo-rectangle « curseur VMware alpha » (0x574d5664) : point chaud et pixels RGBA non prémultipliés.</summary>
        public static byte[] CursorRect(int hotX, int hotY, int w, int h, byte[] rgba)
        {
            if (w < 1 || h < 1 || w > 256 || h > 256 || rgba.Length != w * h * 4) throw new ArgumentException(Lang.T("Curseur invalide", "Invalid cursor", "Cursor no válido"));
            using (var rect = new MemoryStream())
            {
                U16(rect, Math.Min(hotX, w - 1)); U16(rect, Math.Min(hotY, h - 1)); U16(rect, w); U16(rect, h); U32(rect, 0x574d5664u);
                rect.WriteByte(1); rect.WriteByte(0); rect.Write(rgba, 0, rgba.Length);
                return rect.ToArray();
            }
        }
                bool Changed(byte[] pixels, int x, int y, int w, int h)
        {
            for (int row = y; row < y + h; row++)
                for (int p = (row * width + x) * 4, end = p + w * 4; p < end; p++)
                    if (pixels[p] != previous[p]) return true;
            return false;
        }
        // Nombre de couleurs distinctes borné : sortie dès le seuil atteint, donc peu coûteux sur les photos.
        bool Complex(byte[] pixels, int x, int y, int w, int h)
        {
            var seen = new HashSet<int>();
            for (int row = y; row < y + h; row++)
                for (int p = (row * width + x) * 4, end = p + w * 4; p < end; p += 4)
                {
                    seen.Add(pixels[p] | (pixels[p + 1] << 8) | (pixels[p + 2] << 16));
                    if (seen.Count > ColorThreshold) return true;
                }
            return false;
        }
        // JPEG d'une tuile à partir des pixels BGRX de la capture ; l'ordre mémoire 24bppRgb est déjà B,G,R.
        // null si JPEG indisponible ou en échec : l'appelant retombe alors sur la compression zlib sans perte.
        byte[] TryJpeg(byte[] pixels, int x, int y, int w, int h)
        {
            ImageCodecInfo codec = JpegCodec();
            if (codec == null) return null;
            try { return JpegTile(codec, pixels, x, y, w, h); }
            catch (Exception) { return null; }
        }
        byte[] JpegTile(ImageCodecInfo codec, byte[] pixels, int x, int y, int w, int h)
        {
            using (var bitmap = new Bitmap(w, h, PixelFormat.Format24bppRgb))
            {
                var bits = bitmap.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, PixelFormat.Format24bppRgb);
                try
                {
                    byte[] row = new byte[w * 3];
                    for (int r = 0; r < h; r++)
                    {
                        int src = ((y + r) * width + x) * 4, next = 0;
                        for (int c = 0; c < w; c++, src += 4) { row[next++] = pixels[src]; row[next++] = pixels[src + 1]; row[next++] = pixels[src + 2]; }
                        Marshal.Copy(row, 0, IntPtr.Add(bits.Scan0, r * bits.Stride), w * 3);
                    }
                }
                finally { bitmap.UnlockBits(bits); }
                using (var memory = new MemoryStream()) { bitmap.Save(memory, codec, JpegQuality); return memory.ToArray(); }
            }
        }
        /// <param name="mask">Tuiles à comparer (zones signalées par la capture) ; null : toutes.</param>
        public byte[] Update(byte[] pixels, bool incremental, bool tight, bool rgbOrder, byte[] cursor = null, bool[] mask = null, bool allowJpeg = true)
        {
            if (pixels.Length != width * height * 4) throw new ArgumentException(Lang.T("Taille de capture invalide", "Invalid capture size", "Tamaño de captura no válido"));
            var tiles = new List<int[]>();
            int index = 0;
            for (int y = 0; y < height; y += Tile)
                for (int x = 0; x < width; x += Tile, index++)
                {
                    int w = Math.Min(Tile, width - x), h = Math.Min(Tile, height - y);
                    if (!incremental || previous == null || (mask == null || mask[index]) && Changed(pixels, x, y, w, h)) tiles.Add(new[] { x, y, w, h });
                }
            using (var frame = new MemoryStream())
            {
                frame.WriteByte(0); frame.WriteByte(0); U16(frame, tiles.Count + (cursor != null ? 1 : 0));
                // Forme du curseur (pseudo-rectangle) : affichée localement par le navigateur, sans latence.
                if (cursor != null) frame.Write(cursor, 0, cursor.Length);
                foreach (var tile in tiles)
                {
                    int x = tile[0], y = tile[1], w = tile[2], h = tile[3];
                    U16(frame, x); U16(frame, y); U16(frame, w); U16(frame, h); U32(frame, tight ? 7u : 0u);
                    // Sous-encodage JPEG (0x90) de Tight pour les tuiles complexes : bien moins de données qu'en zlib sans perte.
                    if (tight && allowJpeg && Complex(pixels, x, y, w, h))
                    {
                        byte[] jpeg = TryJpeg(pixels, x, y, w, h);
                        if (jpeg != null) { frame.WriteByte(0x90); Compact(frame, jpeg.Length); frame.Write(jpeg, 0, jpeg.Length); continue; }
                    }
                    byte[] data = new byte[w * h * (tight ? 3 : 4)]; int next = 0;
                    for (int row = y; row < y + h; row++)
                        for (int p = (row * width + x) * 4, end = p + w * 4; p < end; p += 4)
                        {
                            data[next++] = pixels[p + (tight || rgbOrder ? 2 : 0)];
                            data[next++] = pixels[p + 1];
                            data[next++] = pixels[p + (tight || rgbOrder ? 0 : 2)];
                            if (!tight) data[next++] = 0;
                        }
                    if (tight)
                    {
                        bool solid = true;
                        for (int i = 3; i < data.Length && solid; i++) solid = data[i] == data[i % 3];
                        if (solid) { frame.WriteByte(0x80); frame.Write(data, 0, 3); continue; }
                        // Réinitialisation du flux 0 à chaque rectangle : aucun état zlib partagé.
                        frame.WriteByte(0x01);
                        if (data.Length >= 12) { data = Zlib(data); Compact(frame, data.Length); }
                    }
                    frame.Write(data, 0, data.Length);
                }
                if (previous == null) previous = new byte[pixels.Length];
                Buffer.BlockCopy(pixels, 0, previous, 0, pixels.Length);
                return frame.ToArray();
            }
        }
    }
}
