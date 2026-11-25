package org.loco.agent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.KeyguardManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
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
        if (keysym == XF86_WAKEUP) {                                            // « Déverrouiller » du portail : le code suit
            unlocking = true
            unlockingUntil = SystemClock.uptimeMillis() + UNLOCK_WINDOW
            main.post { unlockBegin() }
            return
        }
        if (unlocking && SystemClock.uptimeMillis() > unlockingUntil) unlocking = false
        if (unlocking) {
            if (keysym == 0xff0dL || keysym == 0xff8dL) unlocking = false
            main.post { unlockKey(keysym) }
            return
        }
        if (locked()) { if (lockKey(keysym)) return } else if (lockTyped.isNotEmpty()) main.post { lockTyped.setLength(0) }
        if (ctrl) { shortcut(keysym); return }
        when (keysym) {
            0xff1bL, XF86_BACK -> performGlobalAction(GLOBAL_ACTION_BACK)        // Échap, touche Précédent
            XF86_HOME, 0xffebL, 0xffecL -> performGlobalAction(GLOBAL_ACTION_HOME)   // Accueil, Super
            XF86_TASKPANE -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            XF86_MENUKB -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            0xff08L -> if (!deleteBack()) performGlobalAction(GLOBAL_ACTION_BACK) // Retour arrière
            0xff52L -> dpad(16)                                                 // Flèche haut  -> D-pad haut
            0xff54L -> dpad(17)                                                 // Flèche bas   -> D-pad bas
            0xff51L -> dpad(18)                                                 // Flèche gauche -> D-pad gauche
            0xff53L -> dpad(19)                                                 // Flèche droite -> D-pad droite
            0xff0dL, 0xff8dL -> if (!enter()) dpad(20)                          // Entrée : valide un champ, sinon OK (D-pad centre)
            0xff09L, 0xff89L -> insert("\t")
            else -> (keypad(keysym) ?: character(keysym))?.let { insert(it) }
        }
    }

    // D-pad par accessibilité (Android 11+/API 30 ; GLOBAL_ACTION_DPAD_UP..CENTER = 16..20). Indispensable pour piloter
    // un Android TV / Google TV, qui se navigue à la télécommande ; ignoré avant Android 11 (où le D-pad n'existe pas ainsi).
    private fun dpad(action: Int) { if (Build.VERSION.SDK_INT >= 30) performGlobalAction(action) }

    // --- Écran de verrouillage ------------------------------------------------
    // Android masque l'écran de saisie du code dans l'image transmise (surface protégée) : la personne qui prend la main
    // ne voit pas le pavé. Elle donne donc le code de l'appareil — bouton « Déverrouiller » de la session, ou tapé sur son
    // clavier — et chaque touche appuie sur la touche du pavé de même nom : rien n'est contourné, il faut connaître le
    // code. La saisie du code est d'abord montée à l'écran (demande officielle à Android, sinon balayage vers le haut).
    // Schéma : les numéros des points, comptés comme sur un pavé de téléphone (1 en haut à gauche, 9 en bas à droite),
    // puis Entrée le trace d'un seul geste.
    private val lockTyped = StringBuilder()        // mot de passe ou schéma en cours (champ illisible) ; vidé hors verrouillage

    private fun locked(): Boolean = try {
        (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
    } catch (_: Exception) {
        false
    }

    /** Verrouille l'écran, comme le bouton marche/arrêt (Android 9 et plus) ; rien s'il l'est déjà. Rend ce qui s'est
     *  passé, pour le journal : « fait », « déjà verrouillé », « refusé par Android »… */
    fun lockScreen(): String {
        if (Build.VERSION.SDK_INT < 28) return "Android trop ancien (9 ou plus)"
        if (locked()) return "déjà verrouillé"
        return try {
            if (performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN)) "fait" else "refusé par Android"
        } catch (e: Exception) {
            "impossible (${e.javaClass.simpleName})"
        }
    }

    // Bouton « Déverrouiller » de la session : le portail envoie XF86WakeUp, puis le code (chiffres du PIN, numéros des
    // points du schéma ou mot de passe), puis Entrée. Tout ce qui suit le marqueur jusqu'à Entrée est le code : mis de
    // côté, appliqué à la saisie du code dès qu'elle est à l'écran, et jamais tapé ailleurs (appareil déjà déverrouillé :
    // il est oublié). `unlocking` est lu et écrit par le fil de la session, le reste sur le fil principal.
    @Volatile
    private var unlocking = false
    private var unlockingUntil = 0L
    private val unlockCode = ArrayList<Long>()

    private fun unlockBegin() {
        unlockCode.clear()
        lockTyped.setLength(0)
        val woke = Wake.screen(this)
        if (locked()) main.postDelayed({ if (locked()) raiseBouncer() }, if (woke) 600L else 0L)
    }

    private fun unlockKey(keysym: Long) {
        if (unlockCode.size < 128) unlockCode.add(keysym)
        if (keysym == 0xff0dL || keysym == 0xff8dL) unlockApply(1)
    }

    private fun unlockApply(attempt: Int) {
        if (!locked()) { unlockCode.clear(); return }
        val root = rootInActiveWindow
        if (root != null && root.packageName?.toString() == SYSTEM_UI && lockReady(root)) {
            lockTyped.setLength(0)
            for (k in unlockCode) lockApply(root, k)
            unlockCode.clear()
            return
        }
        if (attempt == BOUNCER_SWIPE_AT) swipeUp()                       // la demande à Android n'a rien donné
        if (attempt < BOUNCER_TRIES) main.postDelayed({ unlockApply(attempt + 1) }, BOUNCER_POLL) else unlockCode.clear()
    }

    private val lockQueue = ArrayList<Long>()      // touches tapées pendant que la saisie du code monte à l'écran
    @Volatile
    private var lockRising = false                 // écrit sur le fil principal, lu aussi par celui de la session

    /** Touche tapée au clavier, appareil verrouillé. Renvoie vrai si elle est prise en charge ici. Elle est traitée sur
     *  le fil principal, comme les suivantes : les touches gardent leur ordre, même tapées pendant que la saisie monte
     *  (l'activité qui la demande passe alors un instant au premier plan). */
    private fun lockKey(keysym: Long): Boolean {
        val typed = keypad(keysym) != null || character(keysym) != null
        if (!typed && keysym != 0xff0dL && keysym != 0xff8dL && keysym != 0xff08L) return false
        if (!lockRising) {
            val root = rootInActiveWindow ?: return false
            if (root.packageName?.toString() != SYSTEM_UI) return false       // autre fenêtre par-dessus le verrouillage
        }
        main.post { lockHandle(keysym, typed) }
        return true
    }

    private fun lockHandle(keysym: Long, typed: Boolean) {
        if (lockRising) {
            lockQueue.add(keysym)
            return
        }
        val root = rootInActiveWindow ?: return
        if (root.packageName?.toString() != SYSTEM_UI) return
        if (lockReady(root)) {
            lockApply(root, keysym)
            return
        }
        // Encore sur l'écran de verrouillage : la première touche fait monter la saisie du code ; elle et les suivantes
        // attendent qu'elle soit là, puis sont rejouées dans l'ordre.
        if (!typed) return
        lockQueue.add(keysym)
        lockRising = true
        raiseBouncer()
        main.postDelayed({ lockDrain(1) }, BOUNCER_POLL)
    }

    private fun lockDrain(attempt: Int) {
        val root = rootInActiveWindow
        val ready = locked() && root != null && root.packageName?.toString() == SYSTEM_UI && lockReady(root)
        if (!ready && locked() && attempt < BOUNCER_TRIES) {
            if (attempt == BOUNCER_SWIPE_AT) swipeUp()
            main.postDelayed({ lockDrain(attempt + 1) }, BOUNCER_POLL)
            return
        }
        val keys = lockQueue.toList()
        lockQueue.clear()
        lockRising = false
        if (ready && root != null) for (k in keys) lockApply(root, k)
    }

    /** Fait monter la saisie du code : demande officielle à Android (requestDismissKeyguard, depuis une activité sans
     *  interface), sinon le geste « balayer vers le haut ». */
    private fun raiseBouncer() {
        if (!WakeActivity.dismiss(this)) swipeUp()
    }

    /** Identifiant court d'un nœud (« lockPatternView »), en minuscules ; "" s'il n'en a pas. */
    private fun idName(n: AccessibilityNodeInfo): String = n.viewIdResourceName?.substringAfter(":id/")?.lowercase() ?: ""

    /** Premier nœud qui convient, en largeur d'abord (l'écran de verrouillage compte quelques dizaines de nœuds). */
    private fun findNode(root: AccessibilityNodeInfo, match: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var seen = 0
        while (queue.isNotEmpty() && seen++ < 600) {
            val n = queue.removeFirst()
            try {
                if (match(n)) return n
            } catch (_: Exception) {
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
        }
        return null
    }

    /** Grille du schéma : `lockPatternView` d'Android, `colorLockPatternView` d'OxygenOS et de ColorOS, sinon la plus
     *  petite vue dont l'identifiant dit « pattern view » (autres surcouches : la grille, pas son conteneur). */
    private fun lockGrid(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (id in GRID_IDS) {
            try {
                root.findAccessibilityNodeInfosByViewId("$SYSTEM_UI:id/$id")?.firstOrNull { it.isVisibleToUser }?.let { return it }
            } catch (_: Exception) {
            }
        }
        var best: AccessibilityNodeInfo? = null
        var bestArea = Long.MAX_VALUE
        val box = android.graphics.Rect()
        findNode(root) { n ->
            val id = idName(n)
            if (n.isVisibleToUser && (id.contains("patternview") || id.contains("pattern_view"))) {
                n.getBoundsInScreen(box)
                val area = box.width().toLong() * box.height()
                if (box.width() >= 100 && box.height() >= 100 && area < bestArea) { best = n; bestArea = area }
            }
            false                                                           // tout parcourir
        }
        return best
    }

    private fun lockField(root: AccessibilityNodeInfo): AccessibilityNodeInfo? =
        root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable && it.isPassword }

    /** La saisie du code est-elle à l'écran (pavé PIN, grille du schéma ou champ du mot de passe) ? */
    private fun lockReady(root: AccessibilityNodeInfo): Boolean =
        padKey(root, "key1", "1") != null || lockGrid(root) != null || lockField(root) != null

    private fun lockApply(root: AccessibilityNodeInfo, keysym: Long): Boolean {
        val digit = when (keysym) {
            in 0x30L..0x39L -> (keysym - 0x30L).toInt()
            in 0xffb0L..0xffb9L -> (keysym - 0xffb0L).toInt()
            else -> -1
        }
        val enter = keysym == 0xff0dL || keysym == 0xff8dL
        val back = keysym == 0xff08L
        // Code PIN : touches du pavé.
        val pad = when {
            digit >= 0 -> padKey(root, "key$digit", digit.toString())
            enter -> padKey(root, "key_enter", null)
            back -> padKey(root, "delete_button", null)
            else -> null
        }
        if (pad != null) {
            pad.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            return true
        }
        // Schéma : les numéros des points s'accumulent, Entrée trace le schéma d'un seul geste.
        val grid = lockGrid(root)
        if (grid != null) {
            when {
                digit in 1..9 -> if (lockTyped.length < 9 && !lockTyped.contains(digit.toString())) lockTyped.append(digit)
                back -> if (lockTyped.isNotEmpty()) lockTyped.setLength(lockTyped.length - 1)
                enter -> {
                    drawPattern(grid, lockTyped.toString())
                    lockTyped.setLength(0)
                }
            }
            return true
        }
        // Mot de passe : champ de saisie du verrouillage (son contenu ne se relit pas, on garde ce qui est tapé).
        val field = lockField(root) ?: return padKey(root, "key1", "1") != null     // pavé PIN : touche sans effet, mais prise
        when {
            enter -> {
                lockTyped.setLength(0)
                if (Build.VERSION.SDK_INT >= 30) field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                return true
            }
            back -> if (lockTyped.isNotEmpty()) lockTyped.setLength(lockTyped.length - 1)
            else -> lockTyped.append((keypad(keysym) ?: character(keysym)) ?: return true)
        }
        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, lockTyped.toString())
        field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        return true
    }

    /** Touche du pavé du verrouillage : par son identifiant d'Android, puis par le même nom en fin d'identifiant (autres
     *  surcouches), sinon (chiffres) par son nom lu par l'accessibilité. */
    private fun padKey(root: AccessibilityNodeInfo, id: String, label: String?): AccessibilityNodeInfo? {
        try {
            root.findAccessibilityNodeInfosByViewId("$SYSTEM_UI:id/$id")?.firstOrNull { it.isClickable && it.isVisibleToUser }?.let { return it }
        } catch (_: Exception) {
        }
        val suffix = id.lowercase()
        findNode(root) { it.isClickable && it.isVisibleToUser && idName(it).endsWith(suffix) }?.let { return it }
        if (label == null) return null
        return findNode(root) { n ->
            n.isClickable && n.isVisibleToUser && (n.contentDescription?.toString() == label || n.text?.toString() == label) &&
                n.className?.toString()?.contains("EditText") != true
        }
    }

    /** Trace un schéma : un seul geste qui passe par le centre des points donnés (1 à 9, ligne par ligne). */
    private fun drawPattern(grid: AccessibilityNodeInfo, dots: String) {
        if (dots.length < 2) return
        val box = android.graphics.Rect()
        grid.getBoundsInScreen(box)
        if (box.width() <= 0 || box.height() <= 0) return
        val path = Path()
        dots.forEachIndexed { i, dot ->
            val n = dot - '1'
            val x = box.left + (n % 3 + 0.5f) * box.width() / 3f
            val y = box.top + (n / 3 + 0.5f) * box.height() / 3f
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        try {
            dispatchGesture(GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 180L * (dots.length - 1))).build(), null, null)
        } catch (_: Exception) {
        }
    }

    /** Balayage vers le haut, du bas de l'écran (sous les notifications de l'écran de verrouillage) vers son milieu. */
    private fun swipeUp() {
        try {
            val m = resources.displayMetrics
            val path = Path().apply { moveTo(m.widthPixels / 2f, m.heightPixels * 0.94f); lineTo(m.widthPixels / 2f, m.heightPixels * 0.35f) }
            dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 250)).build(), null, null)
        } catch (_: Exception) {
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

    /** Entrée sur un champ éditable : valide (IME) ou saute une ligne. Renvoie faux s'il n'y a aucun champ focalisé —
     *  l'appelant fait alors « OK » (D-pad centre), ce qu'attend un Android TV. */
    private fun enter(): Boolean {
        val n = focusedEditable() ?: return false
        if (Build.VERSION.SDK_INT >= 30 &&
            n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)) return true
        if (!n.isMultiLine) return true
        insert("\n")
        return true
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

        // Interface système d'Android : c'est elle qui porte l'écran de verrouillage et sa saisie du code.
        const val SYSTEM_UI = "com.android.systemui"

        // Durée de base d'un segment du doigt suivi en direct (allongée avec la distance parcourue).
        const val SEGMENT_MOVE = 16L

        // Touches de navigation Android envoyées par le portail (keysyms XF86).
        const val XF86_BACK = 0x1008ff26L
        const val XF86_HOME = 0x1008ff18L
        const val XF86_TASKPANE = 0x1008ff7fL     // applications récentes
        const val XF86_MENUKB = 0x1008ff65L       // volet des notifications
        // Bouton « Déverrouiller » de la session : réveille l'appareil ; le code de l'appareil suit, jusqu'à Entrée.
        const val XF86_WAKEUP = 0x1008ff2bL

        private const val UNLOCK_WINDOW = 20_000L            // le code suit le marqueur de près ; au-delà, il est oublié
        private val GRID_IDS = listOf("lockPatternView", "colorLockPatternView")
        private const val BOUNCER_POLL = 400L                // attente de la saisie du code, par pas
        private const val BOUNCER_TRIES = 10                 // … au plus 4 s
        private const val BOUNCER_SWIPE_AT = 4               // si rien n'est monté après 1,6 s : balayage vers le haut
    }
}
