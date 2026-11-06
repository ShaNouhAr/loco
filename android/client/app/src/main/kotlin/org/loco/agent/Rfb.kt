package org.loco.agent

import java.io.ByteArrayOutputStream
import java.util.zip.Deflater

/** Une image de l'écran, en BGRX (octets B, G, R, inutilisé) rangés par lignes. */
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
 */
object Rfb {
    private const val TILE = 128

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

        var previous: ByteArray? = null
        var extendedSize = false           // le client comprend ExtendedDesktopSize (-308)
        var desktopSize = false            // ou au moins DesktopSize (-223)
        var lastFrame = 0L

        while (true) {
            when (val kind = toAgent.readByte()) {
                0 -> toAgent.read(19)                      // SetPixelFormat : TPIXEL RGB imposé, on ignore
                2 -> {                                     // SetEncodings
                    val count = u16(toAgent.read(3), 1)
                    if (count > 128) throw IllegalStateException("encodages")
                    val enc = toAgent.read(count * 4)
                    for (i in 0 until count) when (i32(enc, i * 4)) {
                        -308 -> extendedSize = true
                        -223 -> desktopSize = true
                    }
                }
                3 -> {                                     // FramebufferUpdateRequest
                    val req = toAgent.read(9)
                    val incremental = req[0].toInt() != 0
                    if (extendedSize && !incremental) toBrowser.write(sizeUpdate(0, 0, width, height))
                    val wait = 40 - (System.currentTimeMillis() - lastFrame)
                    if (wait > 0) Thread.sleep(wait)
                    val f = source.frame()
                    if (f != null && f.width == width && f.height == height) {
                        toBrowser.write(encodeFrame(f.data, width, height, if (incremental) previous else null))
                        previous = f.data
                    } else if (f != null && (extendedSize || desktopSize)) {   // rotation : nouvelle taille annoncée
                        width = f.width; height = f.height; previous = null
                        toBrowser.write(if (extendedSize) sizeUpdate(0, 0, width, height) else desktopSizeRect(width, height))
                        toBrowser.write(encodeFrame(f.data, width, height, null))
                        previous = f.data
                    } else {
                        toBrowser.write(encodeFrame(ByteArray(width * height * 4), width, height, previous))
                    }
                    lastFrame = System.currentTimeMillis()
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

    /** Écran encodé en rectangles Tight par tuiles de 128, comme rfb_frame des autres agents. */
    fun encodeFrame(data: ByteArray, width: Int, height: Int, previous: ByteArray?): ByteArray {
        val rects = ByteArrayOutputStream()
        var count = 0
        var y = 0
        while (y < height) {
            val h = minOf(TILE, height - y)
            var x = 0
            while (x < width) {
                val w = minOf(TILE, width - x)
                if (previous != null && tileUnchanged(data, previous, x, y, w, h, width)) { x += TILE; continue }
                rects.write(tileRect(data, x, y, w, h, width))
                count++
                x += TILE
            }
            y += TILE
        }
        val out = ByteArrayOutputStream()
        out.write(0); out.write(0)                         // FramebufferUpdate + pad
        u16(out, count)
        rects.writeTo(out)
        return out.toByteArray()
    }

    private fun tileUnchanged(a: ByteArray, b: ByteArray, x: Int, y: Int, w: Int, h: Int, width: Int): Boolean {
        val rowBytes = w * 4
        for (row in y until y + h) {
            val p = (row * width + x) * 4
            for (i in 0 until rowBytes) if (a[p + i] != b[p + i]) return false
        }
        return true
    }

    private fun tileRect(data: ByteArray, x: Int, y: Int, w: Int, h: Int, width: Int): ByteArray {
        // RGB du pavé (depuis BGRX), puis Tight : uni, brut (< 12 o) ou zlib.
        val rgb = ByteArray(w * h * 3)
        var o = 0
        for (row in y until y + h) {
            var p = (row * width + x) * 4
            repeat(w) {
                rgb[o] = data[p + 2]      // R
                rgb[o + 1] = data[p + 1]  // G
                rgb[o + 2] = data[p]      // B
                o += 3; p += 4
            }
        }
        val rect = ByteArrayOutputStream()
        u16(rect, x); u16(rect, y); u16(rect, w); u16(rect, h); i32(rect, 7)   // en-tête + encodage Tight
        if (solid(rgb)) {
            rect.write(0x80); rect.write(rgb, 0, 3)
        } else if (rgb.size < 12) {
            rect.write(0x01); rect.write(rgb)
        } else {
            val z = zlib(rgb)
            rect.write(0x01); rect.write(compactLen(z.size)); rect.write(z)
        }
        return rect.toByteArray()
    }

    private fun solid(rgb: ByteArray): Boolean {
        var i = 3
        while (i < rgb.size) {
            if (rgb[i] != rgb[0] || rgb[i + 1] != rgb[1] || rgb[i + 2] != rgb[2]) return false
            i += 3
        }
        return true
    }

    private fun zlib(data: ByteArray): ByteArray {
        val d = Deflater(1)
        d.setInput(data); d.finish()
        val out = ByteArrayOutputStream(data.size / 2 + 16)
        val buf = ByteArray(8192)
        while (!d.finished()) out.write(buf, 0, d.deflate(buf))
        d.end()
        return out.toByteArray()
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
