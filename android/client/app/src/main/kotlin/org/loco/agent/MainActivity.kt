package org.loco.agent

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Écran unique : inscription de l'appareil (adresse + clé, sans QR), puis état. */
class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var root: LinearLayout
    private val REQ_SCREEN = 7

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(28), dp(24), dp(24))
        }
        val scroll = ScrollView(this).apply { addView(root); setBackgroundColor(PAGE) }
        setContentView(scroll)
        render()
    }

    private fun render() {
        shown = signature()
        root.removeAllViews()
        add(title("loco"))
        if (store.enrolled) renderEnrolled() else renderEnrollForm()
    }

    // --- Inscrit -----------------------------------------------------------
    private fun renderEnrolled() {
        LocoService.start(this)
        add(muted(getString(R.string.state_enrolled, store.server)))
        val contact = LocoService.lastContact
        val (color, state) = when {
            LocoService.offline -> AMBER to getString(R.string.state_offline)
            contact > 0 -> GREEN to getString(R.string.state_connected,
                java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(java.util.Date(contact)))
            else -> MUTED to getString(R.string.state_connecting)
        }
        add(status(color, state), top = 4)
        add(muted(Inventory.deviceName(this) + " · Android " + Build.VERSION.RELEASE), top = 2)

        val sharing = LocoService.sharing
        section(getString(R.string.screen_title), sharing,
            getString(if (sharing) R.string.screen_state_on else R.string.screen_state_off),
            getString(R.string.screen_hint),
            getString(if (sharing) R.string.screen_off else R.string.screen_on)) { if (sharing) stopScreen() else requestScreen() }

        val control = controlEnabled()
        section(getString(R.string.control_title), control,
            getString(if (control) R.string.control_state_on else R.string.control_state_off),
            getString(R.string.control_hint),
            if (control) null else getString(R.string.control_on)) {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        val files = StorageAccess.granted(this)
        section(getString(R.string.files_title), files,
            getString(if (files) R.string.files_state_on else R.string.files_state_off),
            getString(R.string.files_hint),
            if (files) null else getString(R.string.files_on)) { requestFiles() }

        // Carte « arrière-plan » : seulement tant que le système peut mettre loco en veille (sinon le contact se perd
        // écran éteint). Disparaît une fois l'exemption accordée.
        if (!batteryExempt()) {
            section(getString(R.string.battery_title), false, getString(R.string.battery_state_off),
                getString(R.string.battery_hint), getString(R.string.battery_on)) { requestBattery() }
        }

        add(Button(this, null, android.R.attr.borderlessButtonStyle).apply {
            text = getString(R.string.action_leave)
            isAllCaps = false
            setTextColor(RED)
            setOnClickListener { confirmLeave() }
        }, top = 20)
    }

    /** Une capacité : titre, état coloré, explication, et le bouton seulement s'il y a quelque chose à faire. */
    private fun section(title: String, on: Boolean, state: String, hint: String, action: String?, onAction: () -> Unit) {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            background = GradientDrawable().apply {
                setColor(Color.WHITE); cornerRadius = dp(12).toFloat(); setStroke(dp(1), BORDER)
            }
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(TextView(this).apply {
            text = title; textSize = 16f; setTextColor(INK); setTypeface(typeface, Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        head.addView(status(if (on) GREEN else AMBER, state))
        card.addView(head)
        card.addView(TextView(this).apply { text = hint; textSize = 13f; setTextColor(MUTED); setPadding(0, dp(6), 0, 0) })
        if (action != null) {
            val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            lp.topMargin = dp(10)
            card.addView(Button(this).apply { text = action; isAllCaps = false; setOnClickListener { onAction() } }, lp)
        }
        add(card, top = 12)
    }

    private fun status(color: Int, text: String) = TextView(this).apply {
        this.text = "● $text"; textSize = 13f; setTextColor(color)
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
        controlEnabled(), StorageAccess.granted(this), batteryExempt()).joinToString()

    override fun onPause() {
        root.removeCallbacks(watcher)
        super.onPause()
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
        if (store.enrolled) render()
        root.removeCallbacks(watcher)
        root.postDelayed(watcher, 1500)
    }

    private fun confirmLeave() {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.action_leave))
            .setMessage(getString(R.string.leave_explain))
            .setPositiveButton(getString(R.string.action_leave)) { _, _ ->
                LocoService.stop(this)
                store.clear()
                render()
            }
            .setNegativeButton(getString(R.string.cancel), null)
            .show()
    }

    // --- Formulaire d'inscription ------------------------------------------
    private lateinit var serverField: EditText
    private lateinit var keyField: EditText

    private fun renderEnrollForm() {
        add(muted(getString(R.string.enroll_intro)))
        add(label(getString(R.string.field_server)), top = 18)
        serverField = EditText(this).apply {
            hint = "https://192.168.1.10:8443"
            setText("https://")
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        add(serverField)
        add(label(getString(R.string.field_key)), top = 10)
        keyField = EditText(this).apply {
            hint = getString(R.string.field_key_hint)
            setSingleLine()
        }
        add(keyField)
        add(Button(this).apply {
            text = getString(R.string.action_connect)
            setOnClickListener { startEnroll() }
        }, top = 22)
    }

    private fun startEnroll() {
        val server = serverField.text.toString().trim().trimEnd('/')
        val key = keyField.text.toString().trim()
        if (!server.startsWith("https://") || key.isEmpty()) {
            toast(getString(R.string.enroll_need_fields))
            return
        }
        busy(true)
        Thread {
            try {
                val caPem = Net.fetchCaPem(server)
                val fp = Net.fingerprintOfFirst(caPem)
                runOnUiThread { busy(false); confirmCa(server, key, caPem, fp) }
            } catch (e: Exception) {
                runOnUiThread { busy(false); toast(getString(R.string.enroll_unreachable)) }
            }
        }.start()
    }

    private fun confirmCa(server: String, key: String, caPem: String, fp: String) {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.ca_title))
            .setMessage(getString(R.string.ca_explain) + "\n\n" + grouped(fp))
            .setPositiveButton(getString(R.string.ca_confirm)) { _, _ -> finishEnroll(server, key, caPem) }
            .setNegativeButton(getString(R.string.cancel), null)
            .show()
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
                runOnUiThread { busy(false); toast(msg) }
            } catch (e: Exception) {
                runOnUiThread { busy(false); toast(getString(R.string.enroll_unreachable)) }
            }
        }.start()
    }

    // --- petits utilitaires d'interface ------------------------------------
    private fun add(v: View, top: Int = 0) {
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        lp.topMargin = dp(top)
        root.addView(v, lp)
    }

    private fun busy(on: Boolean) {
        for (i in 0 until root.childCount) root.getChildAt(i).isEnabled = !on
        if (on) toast(getString(R.string.please_wait))
    }

    private fun grouped(hex: String) = hex.chunked(2).joinToString(":").uppercase()
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    private fun title(t: String) = TextView(this).apply { text = t; textSize = 34f; setTextColor(INK) }
    private fun line(t: String) = TextView(this).apply { text = t; textSize = 18f; setPadding(0, dp(3), 0, dp(3)) }
    private fun muted(t: String) = TextView(this).apply { text = t; textSize = 14f; setTextColor(MUTED) }
    private fun label(t: String) = TextView(this).apply { text = t; textSize = 13f; setTextColor(INK) }

    private companion object {
        val PAGE = Color.parseColor("#F4F5F7")
        val INK = Color.parseColor("#202124")
        val MUTED = Color.parseColor("#5F6368")
        val BORDER = Color.parseColor("#E1E3E8")
        val GREEN = Color.parseColor("#1E8E3E")
        val AMBER = Color.parseColor("#B06000")
        val RED = Color.parseColor("#C5221F")
    }
}
