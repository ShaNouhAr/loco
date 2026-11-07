package org.loco.agent

import android.app.Activity
import android.app.Dialog
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/** Composants de l'application, à l'image du portail loco : mêmes couleurs (thème clair, ou « sombre doux » de
 *  l'apparence Brume quand l'appareil est en mode sombre), même police (Bricolage Grotesque, et IBM Plex Mono pour les
 *  adresses, empreintes et versions), mêmes panneaux, boutons, champs et pastilles d'état. */
class Ui(private val context: Context) {
    enum class Tone { OK, WARN, ERR, NEUTRAL }
    enum class Kind { DEFAULT, PRIMARY, DANGER }

    val canvas = color(R.color.loco_canvas)
    val bg = color(R.color.loco_bg)
    val bg2 = color(R.color.loco_bg2)
    val bg3 = color(R.color.loco_bg3)
    val field = color(R.color.loco_field)
    val line = color(R.color.loco_line)
    val line2 = color(R.color.loco_line2)
    val text = color(R.color.loco_text)
    val text2 = color(R.color.loco_text2)
    val text3 = color(R.color.loco_text3)
    val ok = color(R.color.loco_ok)
    val warn = color(R.color.loco_warn)
    val err = color(R.color.loco_err)

    private fun color(id: Int) = context.getColor(id)

    fun dp(v: Number) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), context.resources.displayMetrics).toInt()

    fun tone(t: Tone) = when (t) { Tone.OK -> ok; Tone.WARN -> warn; Tone.ERR -> err; Tone.NEUTRAL -> text3 }

    // --- Texte ---------------------------------------------------------------
    fun text(s: CharSequence, size: Float = 14f, weight: Int = 400, color: Int = text, mono: Boolean = false): TextView =
        TextView(context).apply {
            this.text = s
            textSize = size
            setTextColor(color)
            typeface = if (mono) Fonts.mono(context, weight >= 500) else Fonts.sans(context, weight, size)
            if (!mono && size >= 18f) letterSpacing = -0.02f
            setLineSpacing(0f, if (size >= 18f) 1.15f else 1.3f)
        }

    // --- Panneaux (.panel), note (.note) et séparateur -----------------------
    /** Panneau du portail : fond légèrement translucide sur la photo, bordure fine, coins de 14. */
    fun panel(radius: Int = 14): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = rounded(withAlpha(bg, 0.92f), radius, line)
    }

    fun note(message: CharSequence, t: Tone): TextView = text(message, 13.5f, color = if (t == Tone.NEUTRAL) text2 else text).apply {
        val accent = tone(t)
        background = if (t == Tone.NEUTRAL) rounded(bg2, 14, line)
        else rounded(mix(accent, bg, 0.08f), 14, mix(accent, line, 0.35f))
        setPadding(dp(16), dp(12), dp(16), dp(12))
    }

    fun divider(): View = View(context).apply {
        setBackgroundColor(line)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
    }

    // --- Boutons (.btn, .btn.primary, .btn.danger) ----------------------------
    fun button(label: String, kind: Kind = Kind.DEFAULT, onClick: () -> Unit): TextView = text(label, 14f, 500).apply {
        gravity = Gravity.CENTER
        minHeight = dp(44)
        setPadding(dp(16), 0, dp(16), 0)
        isClickable = true
        isFocusable = true
        val ink = this@Ui.text
        val (fill, stroke, fg) = when (kind) {
            Kind.PRIMARY -> Triple(ink, ink, bg)
            Kind.DANGER -> Triple(bg, line2, err)
            Kind.DEFAULT -> Triple(bg, line2, ink)
        }
        setTextColor(fg)
        background = RippleDrawable(ColorStateList.valueOf(withAlpha(fg, 0.16f)), rounded(fill, 9, stroke), rounded(Color.WHITE, 9, null))
        setOnClickListener { onClick() }
    }

    // --- États (.st) et pastilles (.pill) ------------------------------------
    fun status(label: String, t: Tone): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        addView(dot(tone(t), 7), LinearLayout.LayoutParams(dp(7), dp(7)).apply { marginEnd = dp(8) })
        addView(text(label, 13.5f, 400, if (t == Tone.ERR) err else text2))
    }

    fun pill(label: String, t: Tone): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        minimumHeight = dp(26)
        setPadding(dp(9), dp(3), dp(11), dp(3))
        val accent = tone(t)
        background = rounded(if (t == Tone.NEUTRAL) bg3 else withAlpha(accent, 0.14f), 999, null)
        addView(dot(accent, 6), LinearLayout.LayoutParams(dp(6), dp(6)).apply { marginEnd = dp(7) })
        addView(text(label, 12.5f, 500, if (t == Tone.NEUTRAL) text2 else accent).apply {
            maxLines = 1; ellipsize = TextUtils.TruncateAt.END
        })
    }

    private fun dot(c: Int, size: Int) = View(context).apply {
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(c); setSize(dp(size), dp(size)) }
    }

    // --- Champs (label.f + input) --------------------------------------------
    fun label(s: String): TextView = text(s, 13f, 500).apply { setPadding(0, 0, 0, dp(6)) }

    fun field(hint: String, mono: Boolean = false): EditText = EditText(context).apply {
        this.hint = hint
        textSize = 15f
        setTextColor(this@Ui.text)
        setHintTextColor(text3)
        typeface = if (mono) Fonts.mono(context, false) else Fonts.sans(context, 400, 15f)
        setSingleLine()
        minHeight = dp(46)
        setPadding(dp(12), 0, dp(12), 0)
        background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), rounded(field, 9, text3))
            addState(intArrayOf(), rounded(field, 9, line2))
        }
    }

    // --- Logo ----------------------------------------------------------------
    fun logo(heightDp: Int): ImageView = ImageView(context).apply {
        setImageResource(R.drawable.loco_logo)
        imageTintList = ColorStateList.valueOf(text)
        adjustViewBounds = true
        contentDescription = "loco"
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(heightDp))
    }

    // --- Fenêtre de confirmation (dialog du portail) --------------------------
    /** Titre, texte, contenu éventuel (empreinte…), puis « Annuler » et l'action ; l'action peut être destructive. */
    fun confirm(activity: Activity, title: String, message: String, extra: View?, cancel: String, action: String,
                kind: Kind = Kind.PRIMARY, onAction: () -> Unit) {
        val dialog = Dialog(activity)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        val box = panel(16).apply {
            background = rounded(bg, 16, line)
            setPadding(dp(22), dp(20), dp(22), dp(18))
            addView(this@Ui.text(title, 18f, 600))
            addView(this@Ui.text(message, 14f, 400, text2), spaced(8))
            if (extra != null) addView(extra, spaced(14))
            val actions = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.END }
            actions.addView(button(cancel) { dialog.dismiss() })
            actions.addView(button(action, kind) { dialog.dismiss(); onAction() },
                LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) })
            addView(actions, spaced(20))
        }
        dialog.setContentView(box)
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        dialog.window?.setLayout((context.resources.displayMetrics.widthPixels * 0.9).toInt().coerceAtMost(dp(440)),
            ViewGroup.LayoutParams.WRAP_CONTENT)
        dialog.show()
    }

    /** Valeur technique encadrée (empreinte, adresse), comme le <code> du portail. */
    fun code(value: String): TextView = text(value, 13f, 400, text, mono = true).apply {
        background = rounded(bg3, 9, null)
        setPadding(dp(12), dp(10), dp(12), dp(10))
        setTextIsSelectable(true)
    }

    fun spaced(top: Int, width: Int = ViewGroup.LayoutParams.MATCH_PARENT) =
        LinearLayout.LayoutParams(width, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(top) }

    // --- Outils --------------------------------------------------------------
    fun rounded(fill: Int, radius: Int, stroke: Int?): Drawable = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(radius).toFloat()
        if (stroke != null) setStroke(dp(1), stroke)
    }

    fun withAlpha(c: Int, a: Float) = Color.argb((a * 255).toInt(), Color.red(c), Color.green(c), Color.blue(c))

    /** color-mix(in srgb, a p, b) du portail. */
    fun mix(a: Int, b: Int, p: Float) = Color.rgb(
        (Color.red(a) * p + Color.red(b) * (1 - p)).toInt(),
        (Color.green(a) * p + Color.green(b) * (1 - p)).toInt(),
        (Color.blue(a) * p + Color.blue(b) * (1 - p)).toInt())
}

/** Polices du portail, livrées dans l'APK (licence SIL OFL, voir assets/fonts). Bricolage Grotesque est variable :
 *  graisse et taille optique sont fixées à l'usage, comme le fait un navigateur. */
object Fonts {
    private val cache = HashMap<String, Typeface>()

    @Synchronized
    fun sans(context: Context, weight: Int, size: Float): Typeface {
        val opsz = size.coerceIn(12f, 96f).toInt()
        return cache.getOrPut("sans/$weight/$opsz") {
            try {
                Typeface.Builder(context.assets, "fonts/BricolageGrotesque-loco.ttf")
                    .setFontVariationSettings("'wght' $weight, 'opsz' $opsz, 'wdth' 100").build()
            } catch (_: Exception) {
                Typeface.create(Typeface.SANS_SERIF, if (weight >= 600) Typeface.BOLD else Typeface.NORMAL)
            }
        }
    }

    @Synchronized
    fun mono(context: Context, medium: Boolean): Typeface = cache.getOrPut("mono/$medium") {
        try {
            Typeface.createFromAsset(context.assets, if (medium) "fonts/IBMPlexMono-Medium.ttf" else "fonts/IBMPlexMono-Regular.ttf")
        } catch (_: Exception) {
            Typeface.MONOSPACE
        }
    }
}
