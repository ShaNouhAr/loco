package org.loco.agent

import android.accessibilityservice.AccessibilityService
import android.annotation.TargetApi
import android.content.Context
import android.graphics.Bitmap
import android.hardware.display.DisplayManager
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.Display
import java.util.concurrent.Executors

/**
 * Écran lu par le service d'accessibilité (Android 11 et plus) : une fois ce service activé par la personne, loco peut
 * montrer l'écran à chaque session sans repasser par la fenêtre de partage d'Android. Rien n'est lu en dehors d'une
 * session ouverte depuis le portail, et l'appareil affiche alors « Contrôle à distance en cours ».
 *
 * Android limite cette lecture à trois images par seconde environ : assez pour dépanner ; le partage d'écran
 * (MediaProjection), quand il est actif, reste préféré pour sa fluidité.
 */
@TargetApi(30)
class AccessibilityCapture(private val service: AccessibilityService) : FrameSource {
    @Volatile override var width = 0
        private set
    @Volatile override var height = 0
        private set
    @Volatile override var screenWidth = 0
        private set
    @Volatile override var screenHeight = 0
        private set

    private val worker = Executors.newSingleThreadExecutor()
    @Volatile private var latest: Frame? = null
    @Volatile private var pending = false
    @Volatile private var lastShot = 0L

    init {
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        (service.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).getDisplay(Display.DEFAULT_DISPLAY).getRealMetrics(metrics)
        size(metrics.widthPixels, metrics.heightPixels)
    }

    /** Taille réelle de l'écran et taille transmise (plafonnée, paire), comme le partage d'écran. */
    private fun size(rw: Int, rh: Int) {
        val scale = minOf(1.0, MAX_SIDE.toDouble() / maxOf(rw, rh))
        screenWidth = rw; screenHeight = rh
        width = maxOf(2, (rw * scale).toInt() / 2 * 2)
        height = maxOf(2, (rh * scale).toInt() / 2 * 2)
    }

    override fun frame(): Frame? {
        val now = SystemClock.uptimeMillis()
        if (now - lastShot > STALE) latest = null          // personne ne regardait : ne pas resservir une vieille image
        if (!pending && now - lastShot >= INTERVAL) {
            pending = true
            lastShot = now
            try {
                service.takeScreenshot(Display.DEFAULT_DISPLAY, worker, object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(shot: AccessibilityService.ScreenshotResult) {
                        try { convert(shot)?.let { latest = it } } catch (_: Throwable) {} finally { pending = false }
                    }
                    override fun onFailure(code: Int) { pending = false }
                })
            } catch (_: Throwable) {
                pending = false
            }
        }
        // Première image d'une session : l'attendre un instant plutôt que d'afficher un écran noir.
        val limit = now + FIRST
        while (latest == null && pending && SystemClock.uptimeMillis() < limit) {
            try { Thread.sleep(20) } catch (_: InterruptedException) { break }
        }
        return latest
    }

    private fun convert(shot: AccessibilityService.ScreenshotResult): Frame? {
        val buffer = shot.hardwareBuffer
        val soft = try {
            Bitmap.wrapHardwareBuffer(buffer, shot.colorSpace)?.let { hard -> hard.copy(Bitmap.Config.ARGB_8888, false).also { hard.recycle() } }
        } finally {
            buffer.close()
        } ?: return null
        size(soft.width, soft.height)                       // suit la rotation
        val w = width
        val h = height
        val scaled = if (w != soft.width || h != soft.height) Bitmap.createScaledBitmap(soft, w, h, true).also { soft.recycle() } else soft
        val argb = IntArray(w * h)
        scaled.getPixels(argb, 0, w, 0, 0, w, h)
        scaled.recycle()
        val out = ByteArray(w * h * 4)                      // BGRX, attendu par l'encodeur
        var o = 0
        for (c in argb) {
            out[o] = c.toByte(); out[o + 1] = (c shr 8).toByte(); out[o + 2] = (c shr 16).toByte()
            o += 4
        }
        return Frame(out, w, h)
    }

    private companion object {
        const val MAX_SIDE = 1600          // même plafond que le partage d'écran
        const val INTERVAL = 350L          // Android refuse plus d'une lecture toutes les 333 ms
        const val STALE = 3000L
        const val FIRST = 1500L
    }
}
