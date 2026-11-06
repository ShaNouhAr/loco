package org.loco.agent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Injection des entrées du bureau distant, via le service d'accessibilité (même principe qu'AnyDesk). Activé une
 * fois par l'utilisateur dans les réglages ; sans lui, l'écran reste visible mais non pilotable.
 *
 * Souris : clic gauche = toucher (appui long si maintenu, glisser en suivant la souris), clic droit = Retour,
 * clic milieu = Accueil, molette = défilement. Clavier : texte inséré au curseur du champ actif, Retour arrière,
 * Entrée, Ctrl+A/C/V/X, Échap = Retour, et quelques touches de navigation Android (voir [key]).
 */
class InputService : AccessibilityService(), InputSink {
    private val main = Handler(Looper.getMainLooper())
    private var prevButtons = 0
    private var downAt = 0L
    private val path = ArrayList<FloatArray>()
    private var wheel = 0
    private var wheelX = 0f
    private var wheelY = 0f
    private var ctrl = false

    override fun onServiceConnected() { instance = this }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    // --- Souris ------------------------------------------------------------
    override fun pointer(buttons: Int, x: Int, y: Int) {
        val fx = x.toFloat()
        val fy = y.toFloat()
        val pressed = buttons and prevButtons.inv()
        if (pressed and 4 != 0) performGlobalAction(GLOBAL_ACTION_BACK)       // clic droit
        if (pressed and 2 != 0) performGlobalAction(GLOBAL_ACTION_HOME)       // clic milieu
        if (pressed and 8 != 0) scroll(-1, fx, fy)                            // molette vers le haut
        if (pressed and 16 != 0) scroll(1, fx, fy)                            // molette vers le bas

        val left = buttons and 1 != 0
        val wasLeft = prevButtons and 1 != 0
        if (left && !wasLeft) {
            path.clear(); path.add(floatArrayOf(fx, fy)); downAt = System.currentTimeMillis()
        } else if (left) {
            val last = path.last()
            if (Math.hypot((fx - last[0]).toDouble(), (fy - last[1]).toDouble()) >= 6 && path.size < 60) path.add(floatArrayOf(fx, fy))
        } else if (wasLeft) {
            if (path.isEmpty() || Math.hypot((fx - path.last()[0]).toDouble(), (fy - path.last()[1]).toDouble()) >= 2) path.add(floatArrayOf(fx, fy))
            gesture(System.currentTimeMillis() - downAt)
        }
        prevButtons = buttons
    }

    /** Toucher, appui long ou glisser, selon la distance parcourue et la durée d'appui. */
    private fun gesture(heldMs: Long) {
        if (path.isEmpty()) return
        val start = path.first()
        val end = path.last()
        val moved = Math.hypot((end[0] - start[0]).toDouble(), (end[1] - start[1]).toDouble())
        val p = Path()
        p.moveTo(start[0].coerceAtLeast(0f), start[1].coerceAtLeast(0f))
        val duration: Long
        if (moved < 10) {
            p.lineTo(start[0] + 1f, start[1] + 1f)
            duration = if (heldMs >= 500) heldMs.coerceAtMost(3000) else 40      // appui long ou toucher
        } else {
            for (i in 1 until path.size) p.lineTo(path[i][0].coerceAtLeast(0f), path[i][1].coerceAtLeast(0f))
            duration = heldMs.coerceIn(80L, 3000L)
        }
        dispatch(p, duration)
    }

    /** Molette : les crans proches sont regroupés en un seul glissement. */
    private fun scroll(direction: Int, x: Float, y: Float) {
        val first = wheel == 0
        wheel += direction; wheelX = x; wheelY = y
        if (!first) return
        main.postDelayed({
            val metrics = resources.displayMetrics
            val step = metrics.heightPixels / 8f
            val distance = (Math.abs(wheel) * step).coerceAtMost(metrics.heightPixels * 0.6f)
            val dy = if (wheel > 0) -distance else distance                    // molette bas : le doigt remonte
            val endY = (wheelY + dy).coerceIn(1f, metrics.heightPixels - 1f)
            val p = Path()
            p.moveTo(wheelX, wheelY)
            p.lineTo(wheelX, endY)
            wheel = 0
            dispatch(p, 220)
        }, 70)
    }

    private fun dispatch(p: Path, durationMs: Long) {
        try {
            val g = GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(p, 0, durationMs)).build()
            dispatchGesture(g, null, null)
        } catch (_: Exception) {
        }
    }

    // --- Clavier -----------------------------------------------------------
    override fun key(down: Boolean, keysym: Long) {
        if (keysym == 0xffe3L || keysym == 0xffe4L) { ctrl = down; return }     // Ctrl gauche / droit
        if (!down) return
        if (ctrl) { shortcut(keysym); return }
        when (keysym) {
            0xff1bL, XF86_BACK -> performGlobalAction(GLOBAL_ACTION_BACK)        // Échap, touche Précédent
            XF86_HOME, 0xffebL, 0xffecL -> performGlobalAction(GLOBAL_ACTION_HOME)   // Accueil, Super
            XF86_TASKPANE -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            XF86_MENUKB -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            0xff08L -> if (!deleteBack()) performGlobalAction(GLOBAL_ACTION_BACK) // Retour arrière
            0xff0dL, 0xff8dL -> enter()                                         // Entrée
            0xff09L -> insert("\t")
            else -> character(keysym)?.let { insert(it) }
        }
    }

    /** Caractère d'une touche : Latin-1 (0x20-0xff) ou Unicode (0x01000000 + point de code). */
    private fun character(keysym: Long): String? {
        val code = when {
            keysym in 0x20L..0x7eL || keysym in 0xa0L..0xffL -> keysym.toInt()
            keysym and 0xff000000L == 0x01000000L -> (keysym and 0x00ffffffL).toInt()
            else -> return null
        }
        if (code < 0x20 || code > 0x10ffff || code in 0xd800..0xdfff) return null
        return String(Character.toChars(code))
    }

    private fun shortcut(keysym: Long) {
        val n = focusedEditable() ?: return
        when (keysym or 0x20L) {                                                // minuscule
            'a'.code.toLong() -> select(n, 0, textOf(n).length)
            'c'.code.toLong() -> n.performAction(AccessibilityNodeInfo.ACTION_COPY)
            'v'.code.toLong() -> n.performAction(AccessibilityNodeInfo.ACTION_PASTE)
            'x'.code.toLong() -> n.performAction(AccessibilityNodeInfo.ACTION_CUT)
        }
    }

    private fun focusedEditable(): AccessibilityNodeInfo? {
        val n = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return null
        return if (n.isEditable) n else null
    }

    private fun textOf(n: AccessibilityNodeInfo): String = if (n.isShowingHintText) "" else n.text?.toString() ?: ""

    private fun selection(n: AccessibilityNodeInfo, length: Int): Pair<Int, Int> {
        var a = n.textSelectionStart
        var b = n.textSelectionEnd
        if (a < 0 || b < 0 || a > length || b > length) { a = length; b = length }
        return if (a <= b) a to b else b to a
    }

    private fun insert(text: String) {
        val n = focusedEditable() ?: return
        if (n.isPassword) { paste(n, text); return }       // le texte réel d'un mot de passe n'est pas lisible
        val cur = textOf(n)
        val (lo, hi) = selection(n, cur.length)
        setText(n, cur.substring(0, lo) + text + cur.substring(hi), lo + text.length)
    }

    private fun deleteBack(): Boolean {
        val n = focusedEditable() ?: return false
        val cur = textOf(n)
        val (lo, hi) = selection(n, cur.length)
        if (lo == hi && lo == 0) return true
        val from = if (lo == hi) cur.offsetByCodePoints(lo, -1) else lo
        setText(n, cur.substring(0, from) + cur.substring(hi), from)
        return true
    }

    private fun enter() {
        val n = focusedEditable()
        if (n != null && Build.VERSION.SDK_INT >= 30 &&
            n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)) return
        if (n != null && !n.isMultiLine) return
        insert("\n")
    }

    private fun setText(n: AccessibilityNodeInfo, value: String, cursor: Int) {
        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        select(n, cursor, cursor)
    }

    private fun select(n: AccessibilityNodeInfo, start: Int, end: Int) {
        val args = Bundle()
        args.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, start)
        args.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, end)
        n.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
    }

    /** Champ mot de passe : collage par le presse-papiers (remplace son contenu du moment). */
    private fun paste(n: AccessibilityNodeInfo, text: String) {
        val clip = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clip.setPrimaryClip(ClipData.newPlainText("loco", text))
        n.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    companion object {
        @Volatile
        var instance: InputService? = null

        // Touches de navigation Android envoyées par le portail (keysyms XF86).
        const val XF86_BACK = 0x1008ff26L
        const val XF86_HOME = 0x1008ff18L
        const val XF86_TASKPANE = 0x1008ff7fL     // applications récentes
        const val XF86_MENUKB = 0x1008ff65L       // volet des notifications
    }
}
