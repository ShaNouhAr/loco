package org.loco.agent

import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Service visible : contacts réguliers avec loco, relais du bureau distant, et capture d'écran si autorisée. */
class LocoService : Service() {
    @Volatile private var checking = false
    private lateinit var agent: Agent
    private var remote: RemoteClient? = null
    @Volatile private var capture: ScreenCapture? = null
    private val work = Executors.newSingleThreadExecutor()     // installations demandées par loco, une à la fois
    private val updating = AtomicBoolean(false)
    @Volatile private var wake = false                         // réseau ou réglage changé : réévaluer sans attendre
    private var netWatch: ConnectivityManager.NetworkCallback? = null

    override fun onCreate() {
        super.onCreate()
        agent = Agent(this)
        // Type explicite : sans lui, Android 14+ exigerait aussi le type « projection d'écran », pas encore autorisé ici.
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notification(getString(R.string.svc_connecting)), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification(getString(R.string.svc_connecting)))
        }
        remote = RemoteClient(this, agent.store, ::screenSource, ::screenReady, ::onTrustedNetwork) { on -> watched = on; showControl() }
        // Après un redémarrage, sans attendre le premier contact — jamais hors d'un réseau de confiance.
        if (agent.store.keepAdb && onTrustedNetwork()) AdbProbe.keep(this)
        watchNetwork()
        Thread({ remote?.run() }, "loco-remote").apply { isDaemon = true }.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_SCREEN_ON -> startCapture(intent)
            ACTION_SCREEN_OFF -> stopCapture()
            ACTION_NETWORKS -> { Networks.invalidate(); wake = true }      // réglage changé dans l'application
            ACTION_CHECKIN -> wake = true                                 // application ouverte : contact tout de suite
            ACTION_UPDATE -> installOffered()
        }
        if (!checking && agent.store.enrolled) {
            checking = true
            Thread(::checkinLoop, "loco-checkin").start()
        }
        return START_STICKY
    }

    private fun checkinLoop() {
        var fails = 0
        while (checking) {
            var wait = 120
            wake = false
            val net = Networks.current(this, agent.store)
            if (!net.active) {
                // Hors réseau de confiance : aucun contact. Le service reste visible et dit pourquoi ; il reprend dès
                // que le réseau change (watchNetwork) ou que la personne change son réglage.
                paused = pauseText(net); offline = false; fails = 0
                refresh()
                wait = 30                   // la localisation rallumée ne prévient pas : relire de temps en temps
            } else try {
                paused = null
                val answer = agent.checkin()
                wait = answer.optInt("interval", 120)
                lastContact = System.currentTimeMillis(); offline = false; fails = 0
                refresh()
                handle(answer)
            } catch (e: Net.HttpError) {
                if (e.code == 401) { agent.store.clear(); stopSelf(); return }
                offline = true; fails++
                refresh(getString(R.string.svc_server_error, e.code))
            } catch (e: Exception) {
                offline = true; fails++
                refresh(getString(R.string.svc_offline))
            }
            // Hors ligne : on réessaie vite, avec un délai qui croît (5, 10, 20, 40, 60 s) puis plafonne, pour
            // retrouver le serveur sans attendre l'intervalle complet ni marteler le réseau.
            if (fails > 0) wait = minOf(wait, 5 * (1 shl minOf(fails - 1, 4)), 60)
            var slept = 0
            while (checking && slept < wait && !wake) {
                try { Thread.sleep(1000) } catch (_: InterruptedException) {}
                slept++
                // Réseaux de confiance : l'état peut basculer sans que le réseau change (application qui quitte
                // l'écran sans l'autorisation « toujours », localisation coupée) — le relire toutes les cinq secondes.
                if (slept % 5 == 0 && agent.store.netMode != Networks.ALL && onTrustedNetwork() != (paused == null)) break
            }
        }
    }

    // --- Réseaux de confiance ----------------------------------------------
    private fun onTrustedNetwork() = Networks.current(this, agent.store).active

    private fun pauseText(net: Networks.Status) = when (net.why) {
        Networks.Why.OTHER_WIFI -> getString(R.string.svc_paused_wifi, net.wifi ?: "")
        Networks.Why.UNNAMED -> getString(R.string.svc_paused_unnamed)
        Networks.Why.WIRED -> getString(R.string.svc_paused_wired)
        else -> getString(R.string.svc_paused_mobile)
    }

    /** Changement de réseau : l'agent se met en pause ou reprend tout de suite, sans attendre son prochain contact. */
    private fun watchNetwork() {
        val watch = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = changed()
            override fun onLost(network: Network) = changed()
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) = changed()
        }
        try {
            (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).registerDefaultNetworkCallback(watch)
            netWatch = watch
        } catch (_: Exception) {
        }
    }

    private fun changed() {
        if (agent.store.netMode == Networks.ALL) return
        Networks.invalidate()
        // Ne réveiller la boucle que si l'état bascule : ces événements arrivent aussi pour un simple changement de signal.
        if (Networks.current(this, agent.store).active != (paused == null)) wake = true
    }

    /** Commandes reçues (installations, retrait) et mise à jour de l'application proposée par le serveur. */
    private fun handle(answer: JSONObject) {
        piloted = answer.optBoolean("control", false)
        showControl()
        // loco a appairé cet appareil par adb : garder son débogage sans fil joignable tant que c'est le cas.
        val keep = answer.optJSONObject("adb")?.optBoolean("keep", false) ?: false
        if (keep != agent.store.keepAdb) agent.store.keepAdb = keep
        if (keep) AdbProbe.keep(this)
        val commands = answer.optJSONArray("commands") ?: JSONArray()
        for (i in 0 until commands.length()) {
            val cmd = commands.getJSONObject(i)
            when (cmd.optString("action")) {
                "install-apk" -> work.execute { Apps(this, agent.store).run(cmd) }
                "uninstall" -> work.execute { leave(cmd) }
                else -> work.execute { Apps(this, agent.store).unsupported(cmd) }
            }
        }
        // Mise à jour de l'application proposée par le serveur : gardée pour l'écran de l'application, qui permet de
        // l'installer tout de suite ; et tentée seule, hors session, une fois par jour.
        val update = answer.optJSONObject("agent_apk")?.takeIf {
            it.optString("sha256").length == 64 && Apps.newer(it.optString("version"), BuildConfig.VERSION_NAME)
        }
        offered = update
        if (update == null) updateProblem = null
        if (update != null && remote?.inUse != true && updating.compareAndSet(false, true)) {
            work.execute {
                try { Apps(this, agent.store).selfUpdate(update) } finally { updating.set(false) }
            }
        }
    }

    /** « Installer la mise à jour », touché dans l'application : téléchargement puis fenêtre d'installation d'Android. */
    private fun installOffered() {
        val update = offered ?: return
        if (paused != null) return                                  // hors des réseaux de confiance : aucun échange avec loco
        if (!updating.compareAndSet(false, true)) return           // déjà en cours
        updateProblem = null
        installing = true
        work.execute {
            try { updateProblem = Apps(this, agent.store).selfUpdate(update, asked = true) } finally { installing = false; updating.set(false) }
        }
    }

    /** « Retirer l'agent » depuis le portail : l'appareil quitte loco ; l'application reste installée. */
    private fun leave(cmd: JSONObject) {
        Apps(this, agent.store).report(cmd.getLong("id"), "success", getString(R.string.apps_left))
        checking = false
        agent.store.clear()
        stopSelf()
    }

    // --- Écran d'une session -----------------------------------------------
    /** Écran à montrer à la session qui s'ouvre, ou null si rien n'est autorisé sur l'appareil. */
    private fun screenSource(): FrameSource? = if (screenReady()) LiveScreen() else null

    private fun screenReady() = capture != null || ProjectionActivity.silent(this) || InputService.instance?.screen != null

    /**
     * Écran d'une session, obtenu sans geste sur l'appareil : le partage d'écran s'il est en cours ou si Android l'a
     * autorisé une fois pour toutes (il est alors repris, sans fenêtre), sinon la lecture par le service d'accessibilité,
     * plus lente. La source est choisie à chaque image : si Android coupe le partage (verrouillage de l'appareil),
     * l'accessibilité prend le relais, et le partage est redemandé une fois l'appareil déverrouillé.
     */
    private inner class LiveScreen : FrameSource {
        private var asked = 0L

        init {
            if (capture == null && askProjection()) {
                val limit = SystemClock.uptimeMillis() + 4000
                while (capture == null && SystemClock.uptimeMillis() < limit) pause(100)
            }
            val limit = SystemClock.uptimeMillis() + 1500          // première image prête : pas d'écran noir à l'ouverture
            while (frame() == null && SystemClock.uptimeMillis() < limit) pause(50)
        }

        private fun pause(ms: Long) { try { Thread.sleep(ms) } catch (_: InterruptedException) {} }

        private fun askProjection(): Boolean {
            val now = SystemClock.uptimeMillis()
            if (now - asked < 5000 || !ProjectionActivity.silent(this@LocoService)) return false
            if ((getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked) return false
            asked = now
            return try { ProjectionActivity.request(this@LocoService); true } catch (_: Exception) { false }
        }

        private var fresh = 0L          // dernière image venue du partage
        private var last: Frame? = null

        override fun frame(): Frame? {
            val now = SystemClock.uptimeMillis()
            val shared = capture
            var f = shared?.frame()
            if (shared == null) askProjection()
            // Partage absent, ou repris à l'instant et encore muet : l'accessibilité fournit l'image en attendant.
            if (f != null) fresh = now else if (shared == null || now - fresh > 2000) f = InputService.instance?.screen?.frame()
            if (f != null) last = f
            return f ?: last            // jamais d'écran noir au passage d'une source à l'autre
        }

        private fun known(): FrameSource? = capture ?: InputService.instance?.screen
        override val width get() = known()?.width ?: 0
        override val height get() = known()?.height ?: 0
        override val screenWidth get() = known()?.screenWidth ?: 0
        override val screenHeight get() = known()?.screenHeight ?: 0
    }

    // --- Capture d'écran (MediaProjection) ---------------------------------
    private fun startCapture(intent: Intent) {
        if (capture != null) return
        val code = intent.getIntExtra(EXTRA_CODE, 0)
        val data = intent.getParcelableExtra<Intent>(EXTRA_DATA) ?: return
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notification(getString(R.string.svc_screen_on)),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION or ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        }
        val mgr = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val projection = mgr.getMediaProjection(code, data) ?: return
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() { stopCapture() }
        }, Handler(Looper.getMainLooper()))
        capture = try {
            ScreenCapture(this, projection)
        } catch (e: Exception) {
            projection.stop(); null
        }
        sharing = capture != null
        refresh()
    }

    private fun stopCapture() {
        capture?.stop()
        capture = null
        sharing = false
        refresh()
    }

    // --- Notification ------------------------------------------------------
    private fun notification(text: String): Notification {
        if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW)
            ch.description = getString(R.string.svc_channel_desc)
            manager().createNotificationChannel(ch)
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat_loco)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun refresh(text: String? = null) {
        val base = text ?: paused ?: if (capture != null) getString(R.string.svc_screen_on)
        else getString(R.string.svc_connected, clock())
        manager().notify(NOTIF_ID, notification(base))
    }

    @Volatile private var piloted = false      // loco pilote l'écran par adb (annoncé à chaque contact)
    @Volatile private var watched = false      // une session d'écran est ouverte par l'agent

    private fun showControl() = Handler(Looper.getMainLooper()).post { controlNotice(piloted || watched) }

    /** Garde-fou : tant qu'un administrateur voit ou pilote l'écran (session de l'agent, ou adb sans dialogue système),
     *  l'appareil l'affiche, bien en vue. */
    private fun controlNotice(on: Boolean) {
        val nm = manager()
        if (!on) { nm.cancel(CONTROL_NOTIF); return }
        if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(CONTROL_CHANNEL, getString(R.string.control_channel), NotificationManager.IMPORTANCE_HIGH)
            nm.createNotificationChannel(ch)
        }
        nm.notify(CONTROL_NOTIF, Notification.Builder(this, CONTROL_CHANNEL)
            .setContentTitle(getString(R.string.control_title))
            .setContentText(getString(R.string.control_text))
            .setStyle(Notification.BigTextStyle().bigText(getString(R.string.control_text)))
            .setSmallIcon(R.drawable.ic_stat_loco)
            .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
            .setOngoing(true)
            .build())
    }

    private fun manager() = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    private fun clock() = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())

    override fun onDestroy() {
        checking = false
        paused = null
        netWatch?.let { try { (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(it) } catch (_: Exception) {} }
        remote?.stop()
        controlNotice(false)
        stopCapture()
        work.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL = "loco"
        private const val NOTIF_ID = 1
        private const val CONTROL_CHANNEL = "control"
        private const val CONTROL_NOTIF = 2

        /** Dernier contact réussi avec loco (millisecondes), et état du dernier essai. */
        @Volatile var lastContact = 0L
            private set
        @Volatile var offline = false
            private set

        /** Mise à jour de l'application proposée par le serveur à son dernier contact (version, sha256), ou null. */
        @Volatile var offered: JSONObject? = null
            private set
        /** Une installation demandée dans l'application est en cours ; et ce qui a empêché la dernière, ou null. */
        @Volatile var installing = false
            private set
        @Volatile var updateProblem: String? = null
            private set

        /** Texte de la pause quand l'appareil est hors de ses réseaux de confiance ; null quand l'agent est actif. */
        @Volatile var paused: String? = null
            private set

        /** Vrai tant que l'écran est réellement partagé (reflète la capture, pas un drapeau persistant). */
        @Volatile var sharing = false
            private set
        const val ACTION_SCREEN_ON = "org.loco.agent.SCREEN_ON"
        const val ACTION_SCREEN_OFF = "org.loco.agent.SCREEN_OFF"
        const val ACTION_NETWORKS = "org.loco.agent.NETWORKS"
        const val ACTION_CHECKIN = "org.loco.agent.CHECKIN"
        const val ACTION_UPDATE = "org.loco.agent.UPDATE"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"

        fun start(context: Context) {
            val i = Intent(context, LocoService::class.java)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
        }

        fun startScreen(context: Context, code: Int, data: Intent) {
            val i = Intent(context, LocoService::class.java).setAction(ACTION_SCREEN_ON)
                .putExtra(EXTRA_CODE, code).putExtra(EXTRA_DATA, data)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
        }

        fun stopScreen(context: Context) {
            context.startService(Intent(context, LocoService::class.java).setAction(ACTION_SCREEN_OFF))
        }

        /** Application ouverte : un contact tout de suite, pour savoir sans attendre si une mise à jour est proposée. */
        fun checkNow(context: Context) {
            try { context.startService(Intent(context, LocoService::class.java).setAction(ACTION_CHECKIN)) } catch (_: Exception) {}
        }

        /** Installe maintenant la mise à jour proposée (voir [offered]). */
        fun installUpdate(context: Context) {
            try { context.startService(Intent(context, LocoService::class.java).setAction(ACTION_UPDATE)) } catch (_: Exception) {}
        }

        /** Réseaux de confiance changés dans l'application : le service réévalue tout de suite. */
        fun networksChanged(context: Context) {
            Networks.invalidate()
            try { context.startService(Intent(context, LocoService::class.java).setAction(ACTION_NETWORKS)) } catch (_: Exception) {}
        }

        fun stop(context: Context) = context.stopService(Intent(context, LocoService::class.java))
    }
}
