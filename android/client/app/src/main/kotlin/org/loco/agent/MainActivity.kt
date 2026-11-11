package org.loco.agent

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Écran unique, à l'image du portail : inscription de l'appareil (adresse + clé, sans QR), puis état et accès. */
class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var ui: Ui
    private lateinit var root: LinearLayout
    private val REQ_SCREEN = 7
    private val REQ_LOCATION = 8

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        ui = Ui(this)
        // APK préconfiguré par loco : adresse, clé et empreinte gravées dedans → inscription sans rien saisir.
        if (!store.enrolled) provisioning = Provisioning.read(this)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        // Mode clair : icônes sombres dans la barre de navigation aussi (la barre d'état suit le thème).
        if (Build.VERSION.SDK_INT >= 27 && !night()) {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = window.decorView.systemUiVisibility or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        }
        // Fond « Brume » du portail, puis le contenu dans des panneaux.
        val frame = FrameLayout(this)
        frame.addView(ImageView(this).apply {
            setImageResource(R.drawable.bg_brume)
            scaleType = ImageView.ScaleType.CENTER_CROP
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        // Largeur de lecture bornée (600 dp, centrés) sur tablette, comme la largeur de contenu du portail.
        val side = maxOf(14, (resources.configuration.screenWidthDp - 600) / 2)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(side), ui.dp(14), ui.dp(side), ui.dp(24))
        }
        frame.addView(ScrollView(this).apply { isFillViewport = true; addView(root) })
        setContentView(frame)
        render()
    }

    private fun night() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    private fun render() {
        shown = signature()
        root.removeAllViews()
        when {
            store.enrolled -> renderEnrolled()
            provisioning != null && !autoFailed -> renderAutoEnroll(provisioning!!)
            else -> renderEnrollForm()
        }
    }

    // --- Inscrit -----------------------------------------------------------
    private fun renderEnrolled() {
        LocoService.start(this)
        root.gravity = Gravity.TOP
        root.addView(header())

        // Mise à jour de l'application : elle s'installe aussi seule, mais on peut la lancer d'ici sans attendre.
        val update = LocoService.offered
        if (update != null && LocoService.paused == null && Installs.pending.values.none { it.first == "loco" }) {
            val allowed = packageManager.canRequestPackageInstalls()
            val busy = LocoService.installing
            val text = getString(R.string.update_text, update.optString("version"), BuildConfig.VERSION_NAME) +
                (if (!allowed) "\n" + getString(R.string.update_need_permission) else "") +
                (LocoService.updateProblem?.let { "\n" + it } ?: "")
            root.addView(notice(getString(R.string.update_title), text,
                getString(if (!allowed) R.string.install_on else if (busy) R.string.update_working else R.string.update_action),
                getString(R.string.update_state)) {
                if (!allowed) openInstallSettings() else if (!busy) { LocoService.installUpdate(this); root.postDelayed({ render() }, 300) }
            }, ui.spaced(12))
        }

        // Installations demandées depuis loco qui attendent la personne : proposées ici, au premier plan, en plus de
        // la notification (un groupe de notifications replié ouvre l'application plutôt que la fenêtre d'Android).
        for ((name, intent) in Installs.pending.values.toList()) {
            root.addView(notice(getString(R.string.pending_title), getString(R.string.pending_hint, name),
                getString(R.string.pending_action)) { try { startActivity(intent) } catch (_: Exception) {} }, ui.spaced(12))
        }
        Installs.waitingPermission?.let { name ->
            root.addView(notice(getString(R.string.pending_title), getString(R.string.apps_allow_text, name),
                getString(R.string.install_on)) { openInstallSettings() }, ui.spaced(12))
        }

        val access = ui.panel()
        access.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(13))
            addView(ui.text(getString(R.string.access_title), 15f, 600))
            addView(ui.text(Inventory.deviceName(this@MainActivity) + " · Android " + Build.VERSION.RELEASE, 13f, 400, ui.text3),
                ui.spaced(2))
        })

        val sharing = LocoService.sharing
        // Sans partage en cours, l'écran reste disponible à chaque session dès que loco peut le montrer seul.
        val ready = sharing || ProjectionActivity.silent(this) || InputService.instance?.screen != null
        row(access, getString(R.string.screen_title), ready,
            getString(if (sharing) R.string.screen_state_on else if (ready) R.string.screen_state_ready else R.string.screen_state_off),
            getString(R.string.screen_hint),
            getString(if (sharing) R.string.screen_off else R.string.screen_on)) { if (sharing) stopScreen() else requestScreen() }

        val control = controlEnabled()
        row(access, getString(R.string.control_title), control,
            getString(if (control) R.string.control_state_on else R.string.control_state_off),
            // Android 13 et plus bloque l'accessibilité d'une application installée depuis un fichier : dire comment la permettre.
            getString(R.string.control_hint) + if (!control && Build.VERSION.SDK_INT >= 33) " " + getString(R.string.control_restricted) else "",
            if (control) null else getString(R.string.control_on)) {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        val files = StorageAccess.granted(this)
        row(access, getString(R.string.files_title), files,
            getString(if (files) R.string.files_state_on else R.string.files_state_off),
            getString(R.string.files_hint),
            if (files) null else getString(R.string.files_on)) { requestFiles() }

        val install = packageManager.canRequestPackageInstalls()
        row(access, getString(R.string.install_title), install,
            getString(if (install) R.string.install_state_on else R.string.install_state_off),
            getString(R.string.install_hint),
            if (install) null else getString(R.string.install_on)) { openInstallSettings() }

        // « Arrière-plan » : seulement tant que le système peut mettre loco en veille (sinon le contact se perd écran
        // éteint). Disparaît une fois l'exemption accordée.
        if (!batteryExempt()) {
            row(access, getString(R.string.battery_title), false, getString(R.string.battery_state_off),
                getString(R.string.battery_hint), getString(R.string.battery_on)) { requestBattery() }
        }
        root.addView(access, ui.spaced(12))
        root.addView(networksPanel(), ui.spaced(12))

        root.addView(ui.button(getString(R.string.action_leave), Ui.Kind.DANGER) { confirmLeave() },
            ui.spaced(20, ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.CENTER_HORIZONTAL })
        val version = "loco " + BuildConfig.VERSION_NAME + when {
            update != null -> " · " + getString(R.string.version_available, update.optString("version"))
            LocoService.lastContact > 0 && LocoService.paused == null && !LocoService.offline -> " · " + getString(R.string.version_current)
            else -> ""
        }
        root.addView(ui.text(version, 12f, 400, ui.text3, mono = true).apply { gravity = Gravity.CENTER },
            ui.spaced(12))
    }

    /** En-tête du portail : logo, « loco » et l'adresse du serveur, état du contact à droite. */
    private fun header(): LinearLayout = ui.panel(16).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(ui.dp(16), ui.dp(12), ui.dp(14), ui.dp(12))
        addView(ui.logo(26))
        addView(LinearLayout(this@MainActivity).apply {
            orientation = LinearLayout.VERTICAL
            addView(ui.text("loco", 16f, 600))
            addView(ui.text(Uri.parse(store.server).authority ?: store.server, 12f, 400, ui.text3, mono = true).apply {
                maxLines = 1; ellipsize = TextUtils.TruncateAt.END
            })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(10); marginEnd = ui.dp(10) })
        val contact = LocoService.lastContact
        addView(when {
            LocoService.paused != null -> ui.pill(getString(R.string.svc_paused), Ui.Tone.WARN)
            LocoService.offline -> ui.pill(getString(R.string.svc_offline), Ui.Tone.WARN)
            contact > 0 -> ui.pill(getString(R.string.svc_connected,
                java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(java.util.Date(contact))), Ui.Tone.OK)
            else -> ui.pill(getString(R.string.svc_connecting), Ui.Tone.NEUTRAL)
        })
    }

    /** Une capacité : titre et état, explication, et le bouton seulement s'il y a quelque chose à faire. */
    private fun row(panel: LinearLayout, title: String, on: Boolean, state: String, hint: String, action: String?, onAction: () -> Unit) {
        panel.addView(ui.divider())
        panel.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(16))
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(ui.text(title, 15f, 600), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                addView(ui.status(state, if (on) Ui.Tone.OK else Ui.Tone.WARN))
            })
            addView(ui.text(hint, 13.5f, 400, ui.text2), ui.spaced(5))
            if (action != null) {
                addView(ui.button(action) { onAction() }, ui.spaced(12, ViewGroup.LayoutParams.WRAP_CONTENT))
            }
        })
    }

    // --- Réseaux de confiance ----------------------------------------------
    private var askedAlways = false
    private var wifiDraft = ""

    /** Où l'agent est actif : partout, hors données mobiles, ou seulement sur les Wi-Fi nommés par la personne. */
    private fun networksPanel(): LinearLayout {
        val panel = ui.panel()
        val mode = store.netMode
        val now = Networks.status(this, store)
        panel.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(14))
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(ui.text(getString(R.string.net_title), 15f, 600), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                addView(ui.status(getString(if (now.active) R.string.net_here_on else R.string.net_here_off), if (now.active) Ui.Tone.OK else Ui.Tone.WARN))
            })
            addView(ui.text(getString(R.string.net_hint), 13.5f, 400, ui.text2), ui.spaced(5))
        })
        choice(panel, Networks.ALL, mode, R.string.net_all, R.string.net_all_hint)
        choice(panel, Networks.WIFI, mode, R.string.net_wifi, R.string.net_wifi_hint)
        choice(panel, Networks.TRUSTED, mode, R.string.net_trusted, R.string.net_trusted_hint)
        if (mode == Networks.TRUSTED) trustedWifi(panel)
        return panel
    }

    /** Un mode au choix : titre et explication, « Choisi » ou le bouton pour le choisir. */
    private fun choice(panel: LinearLayout, value: String, mode: String, title: Int, hint: Int) {
        val chosen = mode == value || (value == Networks.ALL && mode != Networks.WIFI && mode != Networks.TRUSTED)
        panel.addView(ui.divider())
        panel.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(ui.dp(18), ui.dp(12), ui.dp(14), ui.dp(12))
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
                addView(ui.text(getString(title), 14.5f, if (chosen) 600 else 500))
                addView(ui.text(getString(hint), 13f, 400, ui.text2), ui.spaced(2))
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = ui.dp(12) })
            if (chosen) addView(ui.status(getString(R.string.net_chosen), Ui.Tone.OK))
            else addView(ui.button(getString(R.string.net_choose)) { setNetMode(value) })
        })
    }

    private fun setNetMode(value: String) {
        store.netMode = value
        LocoService.networksChanged(this)
        render()
    }

    /** Mode « Wi-Fi choisis » : ce qu'Android exige pour lire le nom du Wi-Fi, le Wi-Fi du moment, et la liste. */
    private fun trustedWifi(panel: LinearLayout) {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(16))
        }
        panel.addView(ui.divider())
        panel.addView(box)
        // 1. L'autorisation, pas à pas : sans elle le nom du Wi-Fi est illisible et l'agent reste en pause.
        val need: Triple<Int, Int, () -> Unit>? = when {
            !Networks.fineGranted(this) -> Triple(R.string.net_perm_text, R.string.net_perm_on) {
                requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), REQ_LOCATION)
            }
            !Networks.alwaysGranted(this) -> Triple(R.string.net_always_text, R.string.net_always_on) {
                // Une demande, puis les réglages de l'application si Android ne la présente plus.
                if (Build.VERSION.SDK_INT >= 29 && !askedAlways) {
                    askedAlways = true
                    requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), REQ_LOCATION)
                } else {
                    startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                }
            }
            !Networks.locationOn(this) -> Triple(R.string.net_location_text, R.string.net_location_on) {
                startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
            }
            else -> null
        }
        if (need != null) {
            box.addView(ui.note(getString(need.first), Ui.Tone.WARN))
            box.addView(ui.button(getString(need.second), Ui.Kind.PRIMARY) { need.third() }, ui.spaced(10, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        // 2. Le Wi-Fi du moment, à ajouter d'un geste.
        val trusted = store.trustedWifi
        val name = Networks.wifiName(this)
        val line = when {
            name != null -> getString(R.string.net_current, name)
            Networks.onWifi(this) -> getString(R.string.net_current_unknown)
            else -> getString(R.string.net_current_none)
        }
        box.addView(ui.text(line, 13.5f, 400, ui.text2), ui.spaced(if (need != null) 14 else 0))
        if (name != null && name !in trusted) {
            box.addView(ui.button(getString(R.string.net_add_current), Ui.Kind.PRIMARY) { trust(name) }, ui.spaced(10, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        // 3. Les réseaux approuvés, et l'ajout par son nom d'un Wi-Fi où l'on n'est pas.
        box.addView(ui.label(getString(R.string.net_list_title)), ui.spaced(18))
        if (trusted.isEmpty()) box.addView(ui.note(getString(R.string.net_empty), Ui.Tone.WARN))
        for (wifi in trusted.sorted()) {
            box.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(ui.text(wifi, 14f, 500).apply { maxLines = 1; ellipsize = TextUtils.TruncateAt.END },
                    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = ui.dp(10) })
                if (wifi == name) addView(ui.status(getString(R.string.net_here), Ui.Tone.OK),
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginEnd = ui.dp(10) })
                addView(ui.button(getString(R.string.net_remove)) { store.trustedWifi = store.trustedWifi - wifi; LocoService.networksChanged(this@MainActivity); render() })
            }, ui.spaced(8))
        }
        val field = ui.field(getString(R.string.net_field_hint)).apply {
            setText(wifiDraft)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            filters = arrayOf(android.text.InputFilter.LengthFilter(32))
            addTextChangedListener(object : android.text.TextWatcher {
                override fun afterTextChanged(s: android.text.Editable?) { wifiDraft = s?.toString() ?: "" }
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            })
        }
        box.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(field, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = ui.dp(8) })
            addView(ui.button(getString(R.string.net_add)) { trust(field.text.toString()) })
        }, ui.spaced(12))
    }

    private fun trust(name: String) {
        val wifi = name.trim()
        if (wifi.isEmpty()) return
        store.trustedWifi = store.trustedWifi + wifi
        wifiDraft = ""
        LocoService.networksChanged(this)
        render()
    }

    /** Ce qui attend la personne (installation à confirmer), comme une note « à traiter » du portail. */
    private fun notice(title: String, message: String, action: String, state: String = getString(R.string.pending_state),
                       onAction: () -> Unit): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = ui.rounded(ui.mix(ui.warn, ui.bg, 0.10f), 14, ui.mix(ui.warn, ui.line, 0.45f))
            setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(16))
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(ui.text(title, 15f, 600), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                addView(ui.status(state, Ui.Tone.WARN))
            })
            addView(ui.text(message, 13.5f, 400, ui.text), ui.spaced(5))
            addView(ui.button(action, Ui.Kind.PRIMARY) { onAction() }, ui.spaced(12, ViewGroup.LayoutParams.WRAP_CONTENT))
        }

    /** Rafraîchit l'écran quand un état change (contact, partage, contrôle, fichiers), pas en continu. */
    private val watcher = object : Runnable {
        override fun run() {
            if (store.enrolled && signature() != shown) render()
            root.postDelayed(this, 1500)
        }
    }
    private var shown = ""
    private fun signature() = listOf(LocoService.lastContact / 60000, LocoService.offline, LocoService.sharing,
        controlEnabled(), StorageAccess.granted(this), batteryExempt(), packageManager.canRequestPackageInstalls(),
        Installs.pending.keys.sorted(), Installs.waitingPermission, LocoService.paused, networkSignature(),
        LocoService.offered?.optString("version"), LocoService.installing, LocoService.updateProblem).joinToString()

    /** Ce qui change l'affichage des réseaux de confiance (rien n'est relu hors du mode par nom de Wi-Fi). */
    private fun networkSignature(): String {
        val mode = store.netMode
        if (mode != Networks.TRUSTED) return mode + Networks.status(this, store).active
        return listOf(mode, Networks.wifiName(this), Networks.onWifi(this), Networks.fineGranted(this), Networks.alwaysGranted(this),
            Networks.locationOn(this)).joinToString()
    }

    override fun onPause() {
        root.removeCallbacks(watcher)
        super.onPause()
    }

    private fun openInstallSettings() {
        startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
    }

    private fun requestFiles() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
            } catch (_: Exception) {
                startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            }
        } else {
            requestPermissions(arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE), 2)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_LOCATION) LocoService.networksChanged(this)       // le nom du Wi-Fi devient lisible
        if (store.enrolled) render()
    }

    private fun requestScreen() {
        val mgr = getSystemService(MEDIA_PROJECTION_SERVICE) as android.media.projection.MediaProjectionManager
        startActivityForResult(mgr.createScreenCaptureIntent(), REQ_SCREEN)
    }

    private fun stopScreen() {
        LocoService.stopScreen(this)
        root.postDelayed({ render() }, 300)
    }

    @Deprecated("startActivityForResult")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_SCREEN && resultCode == RESULT_OK && data != null) {
            LocoService.startScreen(this, resultCode, data)
            root.postDelayed({ render() }, 400)   // laisser le service démarrer la capture
        }
    }

    /** Contrôle actif = service d'accessibilité réellement connecté (pas seulement coché dans les réglages). */
    private fun controlEnabled(): Boolean = InputService.instance != null

    /** L'appareil laisse-t-il loco tourner sans restriction de batterie (pas de veille forcée) ? */
    private fun batteryExempt(): Boolean {
        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
        return pm.isIgnoringBatteryOptimizations(packageName)
    }

    private fun requestBattery() {
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    override fun onResume() {
        super.onResume()
        // Autorisation ou localisation changée dans les réglages d'Android : le service relit le réseau tout de suite.
        if (store.enrolled && store.netMode != Networks.ALL) LocoService.networksChanged(this)
        if (store.enrolled) LocoService.checkNow(this)
        Installs.cancel(this, "update", 0)          // « Application mise à jour » : vue, puisque l'application est ouverte
        if (store.enrolled) render()
        root.removeCallbacks(watcher)
        root.postDelayed(watcher, 1500)
    }

    private fun confirmLeave() {
        ui.confirm(this, getString(R.string.action_leave), getString(R.string.leave_explain), null,
            getString(R.string.cancel), getString(R.string.action_leave), Ui.Kind.DANGER) {
            LocoService.stop(this)
            store.clear()
            render()
        }
    }

    // --- Inscription : comme la page de connexion du portail ------------------
    private lateinit var serverField: EditText
    private lateinit var keyField: EditText
    private lateinit var connect: TextView
    private lateinit var problem: TextView
    private var provisioning: Provision? = null
    private var autoStarted = false
    private var autoFailed = false
    private var pendingServer = ""
    private var pendingKey = ""

    /** APK préconfiguré par loco : inscription automatique. Écran « en cours » à l'image du portail, puis repli sur
     *  la saisie manuelle (adresse et clé pré-remplies) si le serveur est injoignable, la clé refusée, ou l'empreinte
     *  de l'autorité différente de celle gravée dans l'application (un serveur n'est jamais accepté à l'aveugle). */
    private fun renderAutoEnroll(cfg: Provision) {
        root.gravity = Gravity.CENTER_VERTICAL
        val card = ui.panel().apply { setPadding(ui.dp(22), ui.dp(22), ui.dp(22), ui.dp(24)) }
        card.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(ui.logo(24))
            addView(ui.text("loco", 16f, 600), LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = ui.dp(10) })
        })
        card.addView(ui.text(getString(R.string.auto_title), 22f, 600), ui.spaced(22))
        card.addView(ui.text(getString(R.string.auto_hint), 14f, 400, ui.text2), ui.spaced(6))
        card.addView(ui.text(Uri.parse(cfg.server).authority ?: cfg.server, 13f, 400, ui.text3, mono = true), ui.spaced(14))
        root.addView(card)
        if (autoStarted) return
        autoStarted = true
        Thread {
            try {
                val caPem = Net.fetchCaPem(cfg.server)
                if (cfg.ca.isNotEmpty() && !Net.fingerprintOfFirst(caPem).equals(cfg.ca, ignoreCase = true)) {
                    fallback(cfg, getString(R.string.auto_ca_mismatch)); return@Thread
                }
                Agent(this).enroll(cfg.server, cfg.key, caPem)
                runOnUiThread { render() }
            } catch (e: Net.HttpError) {
                fallback(cfg, if (e.code == 403) getString(R.string.enroll_bad_key) else getString(R.string.enroll_failed, e.code))
            } catch (e: Exception) {
                fallback(cfg, getString(R.string.enroll_unreachable))
            }
        }.start()
    }

    private fun fallback(cfg: Provision, message: String) = runOnUiThread {
        autoFailed = true
        pendingServer = cfg.server
        pendingKey = cfg.key
        render()
        fail(message)
    }

    private fun renderEnrollForm() {
        root.gravity = Gravity.CENTER_VERTICAL
        val card = ui.panel().apply { setPadding(ui.dp(22), ui.dp(22), ui.dp(22), ui.dp(24)) }
        card.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(ui.logo(24))
            addView(ui.text("loco", 16f, 600), LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = ui.dp(10) })
        })
        card.addView(ui.text(getString(R.string.enroll_title), 22f, 600), ui.spaced(22))
        card.addView(ui.text(getString(R.string.enroll_intro), 14f, 400, ui.text2), ui.spaced(6))

        card.addView(ui.label(getString(R.string.field_server)), ui.spaced(22))
        serverField = ui.field("https://192.168.1.10:8443").apply {
            setText(if (pendingServer.isNotEmpty()) pendingServer else "https://")
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        card.addView(serverField)
        card.addView(ui.label(getString(R.string.field_key)), ui.spaced(14))
        keyField = ui.field(getString(R.string.field_key_hint), mono = true).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            if (pendingKey.isNotEmpty()) setText(pendingKey)
        }
        card.addView(keyField)
        problem = ui.note("", Ui.Tone.ERR).apply { visibility = View.GONE }
        card.addView(problem, ui.spaced(14))
        connect = ui.button(getString(R.string.action_connect), Ui.Kind.PRIMARY) { startEnroll() }
        card.addView(connect, ui.spaced(18))
        root.addView(card)
    }

    private fun startEnroll() {
        val server = serverField.text.toString().trim().trimEnd('/')
        val key = keyField.text.toString().trim()
        if (!server.startsWith("https://") || key.isEmpty()) {
            fail(getString(R.string.enroll_need_fields))
            return
        }
        busy(true)
        Thread {
            try {
                val caPem = Net.fetchCaPem(server)
                val fp = Net.fingerprintOfFirst(caPem)
                runOnUiThread { busy(false); confirmCa(server, key, caPem, fp) }
            } catch (e: Exception) {
                runOnUiThread { busy(false); fail(getString(R.string.enroll_unreachable)) }
            }
        }.start()
    }

    private fun confirmCa(server: String, key: String, caPem: String, fp: String) {
        ui.confirm(this, getString(R.string.ca_title), getString(R.string.ca_explain), ui.code(grouped(fp)),
            getString(R.string.cancel), getString(R.string.ca_confirm)) { finishEnroll(server, key, caPem) }
    }

    private fun finishEnroll(server: String, key: String, caPem: String) {
        busy(true)
        Thread {
            try {
                Agent(this).enroll(server, key, caPem)
                runOnUiThread { busy(false); render() }
            } catch (e: Net.HttpError) {
                val msg = if (e.code == 403) getString(R.string.enroll_bad_key)
                else getString(R.string.enroll_failed, e.code)
                runOnUiThread { busy(false); fail(msg) }
            } catch (e: Exception) {
                runOnUiThread { busy(false); fail(getString(R.string.enroll_unreachable)) }
            }
        }.start()
    }

    /** Erreur dans le formulaire, comme une note du portail (pas de message éphémère). */
    private fun fail(message: String) {
        if (!::problem.isInitialized) return
        problem.text = message
        problem.visibility = View.VISIBLE
    }

    private fun busy(on: Boolean) {
        if (!::connect.isInitialized) return
        if (on) problem.visibility = View.GONE
        connect.isEnabled = !on
        connect.alpha = if (on) 0.6f else 1f
        connect.text = getString(if (on) R.string.please_wait else R.string.action_connect)
        serverField.isEnabled = !on
        keyField.isEnabled = !on
    }

    private fun grouped(hex: String) = hex.chunked(2).joinToString(":").uppercase()
}
