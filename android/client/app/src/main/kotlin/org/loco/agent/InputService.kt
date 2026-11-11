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
    private var wheel = 0
    private var wheelX = 0f
    private var wheelY = 0f
    private var ctrl = false

    /** Écran lu par ce service (Android 11 et plus) : disponible dès qu'il est activé, sans fenêtre de partage. */
    val screen: FrameSource? by lazy { if (Build.VERSION.SDK_INT >= 30) AccessibilityCapture(this) else null }

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

        // Clic gauche : le doigt se pose, suit la souris et se lève en même temps qu'elle (toucher, appui long, glisser).
        val left = buttons and 1 != 0
        val wasLeft = prevButtons and 1 != 0
        prevButtons = buttons
        if (left || wasLeft) main.post { finger(left, fx.coerceAtLeast(0f), fy.coerceAtLeast(0f)) }
    }

    // --- Doigt suivi en direct -----------------------------------------------
    // Le geste est envoyé à Android par petits segments enchaînés (continueStroke) : l'écran défile pendant que la
    // souris glisse, au lieu de rejouer tout le trajet une fois le bouton relâché. Tout se passe sur le fil principal.
    private var stroke: GestureDescription.StrokeDescription? = null   // segment en cours, à prolonger
    private var sending = false                                        // Android exécute un segment
    private var down = false                                           // bouton encore enfoncé
    private var atX = 0f; private var atY = 0f                         // fin du dernier segment envoyé
    private var toX = 0f; private var toY = 0f                         // position de la souris

    private fun finger(pressed: Boolean, x: Float, y: Float) {
        toX = x; toY = y; down = pressed
        if (!sending) advance()
    }

    private fun advance() {
        if (stroke == null) {
            if (!down) return                                           // rien en cours, bouton relâché
            atX = toX; atY = toY                                        // nouveau doigt, posé là où est la souris
        }
        segment()
    }

    /** Envoie le segment suivant : de la fin du précédent à la souris, en gardant le doigt posé tant que le bouton l'est. */
    private fun segment() {
        val previous = stroke
        if (previous == null && !down) return                           // rien en cours
        val p = Path()
        p.moveTo(atX, atY)
        p.lineTo(toX, toY)
        val moved = Math.hypot((toX - atX).toDouble(), (toY - atY).toDouble())
        // Doigt posé et souris immobile : rien à envoyer, Android le garde posé (appui long). Un segment sans mouvement
        // ni lever ne produit aucun événement, et Android le refuserait en lâchant le geste.
        if (previous != null && down && moved < 1) return
        // Court pour suivre la main ; un peu plus long quand elle va vite, pour un glissement régulier.
        val duration = (SEGMENT_MOVE + moved / 40).toLong().coerceAtMost(60)
        val next = try {
            if (previous == null) GestureDescription.StrokeDescription(p, 0, duration, down)
            else previous.continueStroke(p, 0, duration, down)
        } catch (_: Exception) {
            stroke = null; down = false; return
        }
        atX = toX; atY = toY
        stroke = if (down) next else null
        sending = true
        val accepted = try {
            dispatchGesture(GestureDescription.Builder().addStroke(next).build(), object : GestureResultCallback() {
                override fun onCompleted(gesture: GestureDescription?) { sending = false; advance() }
                // Geste coupé par Android (un vrai doigt sur l'écran, par exemple) : on repart de zéro au prochain clic.
                override fun onCancelled(gesture: GestureDescription?) { sending = false; stroke = null; down = false }
            }, main)
        } catch (_: Exception) {
            false
        }
        if (!accepted) { sending = false; stroke = null; down = false }
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
        }, 40)
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
            0xff09L, 0xff89L -> insert("\t")
            else -> (keypad(keysym) ?: character(keysym))?.let { insert(it) }
        }
    }

    /** Pavé numérique (XK_KP_*) : le caractère de la touche. */
    private fun keypad(keysym: Long): String? = when (keysym) {
        in 0xffb0L..0xffb9L -> ('0' + (keysym - 0xffb0L).toInt()).toString()
        0xff80L -> " "
        0xffaaL -> "*"
        0xffabL -> "+"
        0xffacL -> ","
        0xffadL -> "-"
        0xffaeL -> "."
        0xffafL -> "/"
        0xffbdL -> "="
        else -> null
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
        // Le cache d'accessibilité rend parfois le champ d'avant la dernière frappe : le relire, sinon la frappe suivante
        // réécrit l'ancien texte et efface le caractère qu'on venait de taper.
        if (!n.refresh()) return null
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

        // Durée de base d'un segment du doigt suivi en direct (allongée avec la distance parcourue).
        const val SEGMENT_MOVE = 16L

        // Touches de navigation Android envoyées par le portail (keysyms XF86).
        const val XF86_BACK = 0x1008ff26L
        const val XF86_HOME = 0x1008ff18L
        const val XF86_TASKPANE = 0x1008ff7fL     // applications récentes
        const val XF86_MENUKB = 0x1008ff65L       // volet des notifications
    }
}
