package org.loco.agent

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
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Service visible : contacts réguliers avec loco, relais du bureau distant, et capture d'écran si autorisée. */
class LocoService : Service() {
    @Volatile private var checking = false
    private lateinit var agent: Agent
    private var remote: RemoteClient? = null
    @Volatile private var capture: ScreenCapture? = null

    override fun onCreate() {
        super.onCreate()
        agent = Agent(this)
        startForeground(NOTIF_ID, notification(getString(R.string.svc_connecting)))
        remote = RemoteClient(this, agent.store) { capture }
        Thread({ remote?.run() }, "loco-remote").apply { isDaemon = true }.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_SCREEN_ON -> startCapture(intent)
            ACTION_SCREEN_OFF -> stopCapture()
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
            try {
                wait = agent.checkin().optInt("interval", 120)
                lastContact = System.currentTimeMillis(); offline = false; fails = 0
                refresh()
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
            while (checking && slept < wait) {
                try { Thread.sleep(1000) } catch (_: InterruptedException) {}
                slept++
            }
        }
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
            val ch = NotificationChannel(CHANNEL, "loco", NotificationManager.IMPORTANCE_LOW)
            ch.description = getString(R.string.svc_channel_desc)
            manager().createNotificationChannel(ch)
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("loco")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat_loco)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun refresh(text: String? = null) {
        val base = text ?: if (capture != null) getString(R.string.svc_screen_on)
        else getString(R.string.svc_connected, clock())
        manager().notify(NOTIF_ID, notification(base))
    }

    private fun manager() = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    private fun clock() = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())

    override fun onDestroy() {
        checking = false
        remote?.stop()
        stopCapture()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL = "loco"
        private const val NOTIF_ID = 1

        /** Dernier contact réussi avec loco (millisecondes), et état du dernier essai. */
        @Volatile var lastContact = 0L
            private set
        @Volatile var offline = false
            private set

        /** Vrai tant que l'écran est réellement partagé (reflète la capture, pas un drapeau persistant). */
        @Volatile var sharing = false
            private set
        const val ACTION_SCREEN_ON = "org.loco.agent.SCREEN_ON"
        const val ACTION_SCREEN_OFF = "org.loco.agent.SCREEN_OFF"
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

        fun stop(context: Context) = context.stopService(Intent(context, LocoService::class.java))
    }
}
