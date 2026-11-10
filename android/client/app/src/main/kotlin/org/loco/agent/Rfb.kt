package org.loco.agent

import android.graphics.Bitmap
import android.os.Build
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.util.zip.Deflater

/** Une image de l'écran, en RVBX (octets R, V, B, opaque) rangés par lignes, de haut en bas. */
class Frame(val data: ByteArray, val width: Int, val height: Int)

interface FrameSource {
    /** Dernière image disponible, ou null si l'écran n'est pas encore capturé. */
    fun frame(): Frame?
    /** Taille de l'image transmise (éventuellement réduite). */
    val width: Int
    val height: Int
    /** Taille réelle de l'écran, pour recaler les entrées quand l'image est réduite. */
    val screenWidth: Int get() = width
    val screenHeight: Int get() = height
}

interface InputSink {
    fun pointer(buttons: Int, x: Int, y: Int)
    fun key(down: Boolean, keysym: Long)
    /** Texte envoyé au presse-papiers de l'appareil (ClientCutText). */
    fun clipboard(text: String) {}
}

/**
 * Protocole RFB du bureau distant, côté agent : même format que les agents Windows et Linux de loco, pour que le
 * client du navigateur (remote.js) l'affiche sans changement. L'image vient de [source], les entrées vont à [input].
 * Lit les messages du navigateur sur [toAgent], écrit l'écran encodé sur [toBrowser].
 *
 * Fluidité : les zones qui bougent partent en JPEG (dix fois plus léger qu'un pavé sans perte) quand le client sait le
 * lire, puis reviennent sans perte dès que l'écran se pose ; et le navigateur n'a plus à redemander chaque image
 * (mises à jour continues), l'agent se réglant sur ce qu'il a réellement affiché (barrières) pour ne jamais prendre de
 * retard. Un client qui ne sait ni l'un ni l'autre (l'assistant IA de loco) garde l'échange d'origine, sans perte.
 */
object Rfb {
    private const val TILE = 128
    private const val JPEG_QUALITY = 78
    private const val MIN_GAP = 33L             // au plus ~30 images par seconde
    private const val WINDOW = 4                // images en vol au plus, en mises à jour continues
    private const val REFINE_AFTER = 600L       // immobilité avant de renvoyer sans perte ce qui est passé en JPEG
    private const val FENCE_LOST = 2000L        // barrières sans réponse depuis ce délai : on repart, plutôt que de figer l'image
    private const val REFINE_TILES = 20         // pavés rendus sans perte par envoi
    private const val EMPTY_AFTER = 150L        // sans changement : réponse vide à une demande après ce délai

    fun serveDesktop(toAgent: Pipe, toBrowser: Pipe, source: FrameSource, input: InputSink) {
        // Poignée de main RFB 3.8, sécurité « None », true-color 32 bits BGRX.
        toBrowser.write("RFB 003.008\n".toByteArray(Charsets.US_ASCII))
        if (!toAgent.read(12).contentEquals("RFB 003.008\n".toByteArray(Charsets.US_ASCII)))
            throw IllegalStateException("version RFB")
        toBrowser.write(byteArrayOf(1, 1))
        if (toAgent.readByte() != 1) throw IllegalStateException("sécurité RFB")
        toBrowser.write(byteArrayOf(0, 0, 0, 0))
        toAgent.read(1)                                   // ClientInit (partage)

        var width = source.width
        var height = source.height
        val pixel = byteArrayOf(32, 24, 0, 1, 0, 255.toByte(), 0, 255.toByte(), 0, 255.toByte(), 16, 8, 0, 0, 0, 0)
        val name = "loco Android".toByteArray(Charsets.UTF_8)
        val init = ByteArrayOutputStream()
        u16(init, width); u16(init, height); init.write(pixel); u32(init, name.size.toLong()); init.write(name)
        toBrowser.write(init.toByteArray())

        val encoder = Encoder()
        var extendedSize = false           // le client comprend ExtendedDesktopSize (-308)
        var desktopSize = false            // ou au moins DesktopSize (-223)
        var jpeg = false                   // il annonce un niveau de qualité : il sait lire le JPEG
        var announced = false              // mises à jour continues proposées (une seule fois)
        var continuous = false             // … et acceptées : plus de demande image par image
        var requested = false              // une demande d'image attend sa réponse
        var requestedAt = 0L
        var full = true                    // la prochaine image part entière
        var sent = 0L                      // images envoyées en mises à jour continues, et dernière affichée par le client
        var shown = 0L
        var lastUpdate = 0L
        var lastShown = 0L                 // dernière barrière revenue (ou dernier envoi sans rien en vol)
        val stats = Stats("écran")

        while (true) {
            // Rien à lire : c'est le moment d'envoyer une image, si une est attendue et que le client suit.
            if (toAgent.available() == 0 && (requested || continuous)) {
                val now = System.currentTimeMillis()
                if (sent == shown) lastShown = now
                else if (now - lastShown > FENCE_LOST) { shown = sent; lastShown = now }     // le client ne répond plus aux barrières
                if (now - lastUpdate >= MIN_GAP && (!continuous || sent - shown < WINDOW)) {
                    val f = source.frame()
                    var update: ByteArray? = null
                    if (f != null && (f.width != width || f.height != height)) {
                        if (extendedSize || desktopSize) {                   // rotation : nouvelle taille annoncée
                            width = f.width; height = f.height
                            toBrowser.write(if (extendedSize) sizeUpdate(0, 0, width, height) else desktopSizeRect(width, height))
                            update = encoder.update(f.data, width, height, true, jpeg, now)
                        }
                    } else if (f != null) {
                        if (extendedSize && full) toBrowser.write(sizeUpdate(0, 0, width, height))
                        update = encoder.update(f.data, width, height, full, jpeg, now)
                    }
                    // Une demande ne reste pas sans réponse : sans changement (ou sans écran), une mise à jour vide.
                    if (update == null && requested && !continuous && now - requestedAt >= EMPTY_AFTER) update = EMPTY
                    if (update != null) {
                        stats.add(update.size, System.currentTimeMillis() - now, if (continuous) "(en continu${if (jpeg) ", JPEG" else ""})" else "(à la demande)")
                        toBrowser.write(update)
                        if (continuous && update !== EMPTY) { sent++; toBrowser.write(fence(sent)) }
                        requested = false; full = false; lastUpdate = now
                        continue
                    }
                }
                toAgent.await(4)
                continue
            }
            when (val kind = toAgent.readByte()) {
                0 -> toAgent.read(19)                      // SetPixelFormat : TPIXEL RGB imposé, on ignore
                2 -> {                                     // SetEncodings
                    val count = u16(toAgent.read(3), 1)
                    if (count > 128) throw IllegalStateException("encodages")
                    val enc = toAgent.read(count * 4)
                    var fences = false
                    var updates = false
                    jpeg = false
                    for (i in 0 until count) when (val e = i32(enc, i * 4)) {
                        -308 -> extendedSize = true
                        -223 -> desktopSize = true
                        -312 -> fences = true
                        -313 -> updates = true
                        else -> if (e in -32..-23) jpeg = true      // niveau de qualité : client à décodeur JPEG (noVNC)
                    }
                    // Mises à jour continues : proposées au client qui sait aussi répondre aux barrières.
                    if (fences && updates && !announced) { announced = true; toBrowser.write(byteArrayOf(150.toByte())) }
                }
                3 -> {                                     // FramebufferUpdateRequest
                    val req = toAgent.read(9)
                    if (req[0].toInt() == 0) full = true   // demande non incrémentale : l'écran entier
                    requested = true
                    requestedAt = System.currentTimeMillis()
                }
                150 -> {                                   // EnableContinuousUpdates : actif, puis la zone (ignorée)
                    continuous = toAgent.read(9)[0].toInt() != 0
                    if (!continuous) toBrowser.write(byteArrayOf(150.toByte()))
                    sent = 0; shown = 0
                }
                248 -> {                                   // ClientFence : le client a affiché l'image qui précédait
                    val head = toAgent.read(8)
                    val payload = toAgent.read(head[7].toInt() and 0xff)
                    if (payload.size == 4) { shown = maxOf(shown, minOf(sent, u32(payload, 0))); lastShown = System.currentTimeMillis() }
                }
                251 -> {                                   // SetDesktopSize : pad, largeur, hauteur, nb d'écrans, pad
                    val head = toAgent.read(7)
                    toAgent.read((head[5].toInt() and 0xff) * 16)
                    // La taille suit l'écran du téléphone : demande refusée (1 = interdit), l'image est mise à l'échelle.
                    if (extendedSize) toBrowser.write(sizeUpdate(1, 1, width, height))
                }
                4 -> {                                     // KeyEvent
                    val k = toAgent.read(7)
                    input.key(k[0].toInt() != 0, u32(k, 3))
                }
                5 -> {                                     // PointerEvent
                    val p = toAgent.read(5)
                    input.pointer(p[0].toInt() and 0xff, u16(p, 1), u16(p, 3))   // masque, X (octets 1-2), Y (octets 3-4)
                }
                6 -> {                                     // ClientCutText : vers le presse-papiers de l'appareil
                    val len = u32(toAgent.read(7), 3)
                    if (len > 262144) throw IllegalStateException("presse-papiers")
                    if (len > 0) input.clipboard(String(toAgent.read(len.toInt()), Charsets.ISO_8859_1))
                }
                else -> throw IllegalStateException("message RFB $kind")
            }
        }
    }

    /** Mesures pour le journal de l'appareil (logcat, étiquette « loco ») : une ligne toutes les cinq secondes d'activité. */
    class Stats(private val what: String) {
        private var since = System.currentTimeMillis()
        private var count = 0
        private var bytes = 0L
        private var millis = 0L
        fun add(size: Int, ms: Long, note: String = "") {
            if (!android.util.Log.isLoggable("loco", android.util.Log.DEBUG)) return
            count++; bytes += size; millis += ms
            val now = System.currentTimeMillis()
            if (now - since < 5000) return
            val seconds = (now - since) / 1000.0
            android.util.Log.d("loco", "$what : %.1f/s, %d Kio/s, %d ms en moyenne %s".format(count / seconds, (bytes / seconds / 1024).toInt(), millis / maxOf(1, count), note))
            since = now; count = 0; bytes = 0; millis = 0
        }
    }

    /** FramebufferUpdate sans rectangle : « rien n'a changé ». */
    private val EMPTY = byteArrayOf(0, 0, 0, 0)

    /** Barrière (ServerFence) : le client la renvoie quand il a traité tout ce qui la précède, donc affiché l'image. */
    private fun fence(counter: Long): ByteArray {
        val o = ByteArrayOutputStream()
        o.write(248); o.write(0); o.write(0); o.write(0)
        u32(o, 0x80000001L)                                // Request | BlockBefore
        o.write(4); u32(o, counter)
        return o.toByteArray()
    }

    /** Écran encodé en rectangles Tight par tuiles de 128, sans perte, comme rfb_frame des autres agents. */
    fun encodeFrame(data: ByteArray, width: Int, height: Int, previous: ByteArray?): ByteArray {
        val encoder = Encoder()
        if (previous != null) encoder.remember(previous, width, height)
        return encoder.update(data, width, height, previous == null, false, 0L) ?: EMPTY
    }

    /**
     * Compare chaque image à la précédente et n'encode que ce qui a changé. Pixels en RGBX (octets R, V, B, inutilisé),
     * de haut en bas. Avec [jpeg], les zones modifiées partent en JPEG (sous-encodage Tight 0x90) et les pavés ainsi
     * envoyés sont notés : une fois l'écran immobile, ils sont renvoyés sans perte, quelques-uns par envoi.
     */
    class Encoder {
        private var previous: ByteArray? = null
        private var lossy = BooleanArray(0)
        private var lastChange = 0L
        private var bitmap: Bitmap? = null
        private val deflater = Deflater(1)
        private var rgb = ByteArray(TILE * TILE * 3)

        fun remember(data: ByteArray, width: Int, height: Int) {
            previous = data
            lossy = BooleanArray(((width + TILE - 1) / TILE) * ((height + TILE - 1) / TILE))
        }

        /** Mise à jour à envoyer, ou null s'il n'y a rien à dire. [full] : tout l'écran, quel que soit le précédent. */
        fun update(data: ByteArray, width: Int, height: Int, full: Boolean, jpeg: Boolean, now: Long): ByteArray? {
            val cols = (width + TILE - 1) / TILE
            val rows = (height + TILE - 1) / TILE
            val before = previous?.takeIf { !full && it.size == data.size }
            if (lossy.size != cols * rows) lossy = BooleanArray(cols * rows)
            val rects = ByteArrayOutputStream()
            var count = 0
            if (before !== data) {
                // 1. Pavés modifiés.
                val changed = BooleanArray(cols * rows)
                var any = false
                for (r in 0 until rows) for (c in 0 until cols) {
                    val x = c * TILE
                    val y = r * TILE
                    if (before == null || !same(data, before, x, y, minOf(TILE, width - x), minOf(TILE, height - y), width)) {
                        changed[r * cols + c] = true; any = true
                    }
                }
                previous = data
                if (any) {
                    lastChange = now
                    // 2. Regroupés en bandes (pavés contigus d'une rangée), fusionnées quand elles se suivent à l'identique.
                    var bandX = -1; var bandW = 0; var bandY = 0; var bandH = 0
                    fun flush() {
                        if (bandX < 0) return
                        val x = bandX * TILE; val y = bandY * TILE
                        val w = minOf(bandW * TILE, width - x); val h = minOf(bandH * TILE, height - y)
                        // Un pavé isolé (horloge, curseur) reste sans perte : net, et plus léger qu'un JPEG à cette taille.
                        if (jpeg && (bandW > 1 || bandH > 1)) {
                            rects.write(jpegRect(data, x, y, w, h, width, height)); count++
                            for (r in bandY until bandY + bandH) for (c in bandX until bandX + bandW) lossy[r * cols + c] = true
                        } else {
                            for (r in bandY until bandY + bandH) for (c in bandX until bandX + bandW) {
                                val tx = c * TILE; val ty = r * TILE
                                rects.write(tileRect(data, tx, ty, minOf(TILE, width - tx), minOf(TILE, height - ty), width)); count++
                                lossy[r * cols + c] = false
                            }
                        }
                        bandX = -1
                    }
                    for (r in 0 until rows) {
                        val runs = ArrayList<IntArray>()           // bandes de la rangée : [premier pavé, nombre]
                        var c = 0
                        while (c < cols) {
                            if (!changed[r * cols + c]) { c++; continue }
                            val start = c
                            while (c < cols && changed[r * cols + c]) c++
                            runs.add(intArrayOf(start, c - start))
                        }
                        // Seule bande de sa rangée et de mêmes bords que celle du dessus : elle la prolonge.
                        if (runs.size == 1 && bandX == runs[0][0] && bandW == runs[0][1] && bandY + bandH == r) { bandH++; continue }
                        flush()
                        if (runs.size == 1) {
                            bandX = runs[0][0]; bandW = runs[0][1]; bandY = r; bandH = 1
                        } else for (run in runs) {
                            bandX = run[0]; bandW = run[1]; bandY = r; bandH = 1
                            flush()
                        }
                    }
                    flush()
                }
            }
            // 3. Écran immobile : ce qui est parti en JPEG revient sans perte, peu à peu.
            if (count == 0 && jpeg && now - lastChange >= REFINE_AFTER) {
                var t = 0
                while (t < lossy.size && count < REFINE_TILES) {
                    if (lossy[t]) {
                        val x = (t % cols) * TILE; val y = (t / cols) * TILE
                        rects.write(tileRect(data, x, y, minOf(TILE, width - x), minOf(TILE, height - y), width)); count++
                        lossy[t] = false
                    }
                    t++
                }
            }
            if (count == 0) return null
            val out = ByteArrayOutputStream(rects.size() + 4)
            out.write(0); out.write(0)                         // FramebufferUpdate + pad
            u16(out, count)
            rects.writeTo(out)
            return out.toByteArray()
        }

        private fun same(a: ByteArray, b: ByteArray, x: Int, y: Int, w: Int, h: Int, width: Int): Boolean {
            val rowBytes = w * 4
            if (Build.VERSION.SDK_INT >= 33) {
                for (row in y until y + h) {
                    val p = (row * width + x) * 4
                    if (!java.util.Arrays.equals(a, p, p + rowBytes, b, p, p + rowBytes)) return false
                }
                return true
            }
            for (row in y until y + h) {
                val p = (row * width + x) * 4
                for (i in 0 until rowBytes) if (a[p + i] != b[p + i]) return false
            }
            return true
        }

        /** Zone en JPEG (Tight, sous-encodage 0x90), encodée par la bibliothèque du système. */
        private fun jpegRect(data: ByteArray, x: Int, y: Int, w: Int, h: Int, width: Int, height: Int): ByteArray {
            var full = bitmap
            if (full == null || full.width != width || full.height != height || bitmapOf !== data) {
                if (full == null || full.width != width || full.height != height) {
                    full?.recycle()
                    full = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also { it.setHasAlpha(false) }
                    bitmap = full
                }
                full!!.copyPixelsFromBuffer(ByteBuffer.wrap(data))
                bitmapOf = data
            }
            val jpg = ByteArrayOutputStream(w * h / 8 + 1024)
            if (x == 0 && y == 0 && w == width && h == height) {
                full!!.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, jpg)
            } else {
                val part = Bitmap.createBitmap(full!!, x, y, w, h)
                part.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, jpg)
                part.recycle()
            }
            val rect = ByteArrayOutputStream(jpg.size() + 16)
            u16(rect, x); u16(rect, y); u16(rect, w); u16(rect, h); i32(rect, 7)
            rect.write(0x90); rect.write(compactLen(jpg.size())); jpg.writeTo(rect)
            return rect.toByteArray()
        }
        private var bitmapOf: ByteArray? = null

        private fun tileRect(data: ByteArray, x: Int, y: Int, w: Int, h: Int, width: Int): ByteArray {
            // RVB du pavé, puis Tight : uni, brut (< 12 o) ou zlib.
            val size = w * h * 3
            val rgb = this.rgb
            var o = 0
            for (row in y until y + h) {
                var p = (row * width + x) * 4
                repeat(w) {
                    rgb[o] = data[p]; rgb[o + 1] = data[p + 1]; rgb[o + 2] = data[p + 2]
                    o += 3; p += 4
                }
            }
            val rect = ByteArrayOutputStream(size / 4 + 32)
            u16(rect, x); u16(rect, y); u16(rect, w); u16(rect, h); i32(rect, 7)   // en-tête + encodage Tight
            if (solid(rgb, size)) {
                rect.write(0x80); rect.write(rgb, 0, 3)
            } else if (size < 12) {
                rect.write(0x01); rect.write(rgb, 0, size)
            } else {
                val z = zlib(rgb, size)
                rect.write(0x01); rect.write(compactLen(z.size)); rect.write(z)
            }
            return rect.toByteArray()
        }

        private fun solid(rgb: ByteArray, size: Int): Boolean {
            var i = 3
            while (i < size) {
                if (rgb[i] != rgb[0] || rgb[i + 1] != rgb[1] || rgb[i + 2] != rgb[2]) return false
                i += 3
            }
            return true
        }

        private val zbuf = ByteArray(8192)
        private fun zlib(data: ByteArray, size: Int): ByteArray {
            val d = deflater
            d.reset()
            d.setInput(data, 0, size); d.finish()
            val out = ByteArrayOutputStream(size / 2 + 16)
            while (!d.finished()) out.write(zbuf, 0, d.deflate(zbuf))
            return out.toByteArray()
        }
    }

    private fun compactLen(n: Int): ByteArray {
        val b = ByteArrayOutputStream()
        b.write((n and 127) or (if (n > 127) 128 else 0))
        if (n > 127) b.write(((n shr 7) and 127) or (if (n > 16383) 128 else 0))
        if (n > 16383) b.write(n shr 14)
        return b.toByteArray()
    }

    /** Pseudo-rectangle DesktopSize (-223) : nouvelle taille, sans corps. */
    private fun desktopSizeRect(width: Int, height: Int): ByteArray {
        val o = ByteArrayOutputStream()
        o.write(0); o.write(0); u16(o, 1)
        u16(o, 0); u16(o, 0); u16(o, width); u16(o, height); i32(o, -223)
        return o.toByteArray()
    }

    /** Pseudo-rectangle ExtendedDesktopSize : annonce ou confirme la taille de l'écran. */
    private fun sizeUpdate(reason: Int, status: Int, width: Int, height: Int): ByteArray {
        val o = ByteArrayOutputStream()
        o.write(0); o.write(0); u16(o, 1)                  // FramebufferUpdate, 1 rectangle
        u16(o, reason); u16(o, status); u16(o, width); u16(o, height); i32(o, -308)
        o.write(1); o.write(0); o.write(0); o.write(0)     // 1 écran + 3 pad
        u32(o, 0); u16(o, 0); u16(o, 0); u16(o, width); u16(o, height); u32(o, 0)
        return o.toByteArray()
    }

    // --- entiers gros-boutistes -------------------------------------------
    private fun u16(o: ByteArrayOutputStream, n: Int) { o.write((n shr 8) and 0xff); o.write(n and 0xff) }
    private fun u32(o: ByteArrayOutputStream, n: Long) {
        o.write(((n shr 24) and 0xff).toInt()); o.write(((n shr 16) and 0xff).toInt())
        o.write(((n shr 8) and 0xff).toInt()); o.write((n and 0xff).toInt())
    }
    private fun i32(o: ByteArrayOutputStream, n: Int) {
        o.write((n shr 24) and 0xff); o.write((n shr 16) and 0xff); o.write((n shr 8) and 0xff); o.write(n and 0xff)
    }
    private fun u16(b: ByteArray, off: Int) = ((b[off].toInt() and 0xff) shl 8) or (b[off + 1].toInt() and 0xff)
    private fun i32(b: ByteArray, off: Int) = ((b[off].toInt() and 0xff) shl 24) or ((b[off + 1].toInt() and 0xff) shl 16) or
        ((b[off + 2].toInt() and 0xff) shl 8) or (b[off + 3].toInt() and 0xff)
    private fun u32(b: ByteArray, off: Int) = (i32(b, off).toLong() and 0xffffffffL)
}
