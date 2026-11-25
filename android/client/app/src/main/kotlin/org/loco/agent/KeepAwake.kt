package org.loco.agent

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.PowerManager
import android.os.SystemClock
import org.json.JSONObject

/**
 * loco joignable en permanence, écran éteint et appareil verrouillé. Android endort le processeur quand plus rien ne
 * le tient : les attentes de l'agent ne s'écoulent plus, ses contacts cessent et loco le perd au bout d'une heure de
 * veille environ. Donc :
 *  - sur secteur (ou toujours, si la personne l'a choisi), le processeur reste éveillé (verrou partiel, écran éteint) et
 *    le Wi-Fi hors de son économie d'énergie : contacts, canal persistant et débogage sans fil continuent comme écran
 *    allumé ;
 *  - sur batterie, un réveil d'Android toutes les cinq minutes, même en veille profonde, refait un contact et rouvre le
 *    canal (Android en espace les réveils au besoin) ;
 *  - l'optimisation de la batterie doit être levée pour loco, sinon Android coupe aussi le réseau en veille : demandée
 *    dans l'application, posée par adb sur un appareil appairé.
 */
object KeepAwake {
    private const val EVERY = 5 * 60_000L
    private var cpu: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null          // Wi-Fi hors économie d'énergie, écran éteint (service au premier plan)

    private fun power(context: Context) = context.getSystemService(Context.POWER_SERVICE) as PowerManager

    /** Optimisation de la batterie levée pour loco (Android ne le met pas en veille) ? */
    fun unrestricted(context: Context): Boolean = try {
        power(context).isIgnoringBatteryOptimizations(context.packageName)
    } catch (_: Exception) {
        false
    }

    /** Sur secteur (ou sans batterie : boîtier TV) ? */
    fun plugged(context: Context): Boolean {
        val intent = try { context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) } catch (_: Exception) { null }
            ?: return true
        if (!intent.getBooleanExtra(BatteryManager.EXTRA_PRESENT, true)) return true
        return intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0
    }

    /** Ce que la fiche de l'appareil en montre : optimisation levée, et mode « toujours » ou « sur secteur ». */
    fun state(context: Context): JSONObject = JSONObject()
        .put("unrestricted", unrestricted(context))
        .put("awake", if (Store(context).awakeAlways) "always" else "charging")
        .put("held", cpu?.isHeld == true)

    /** Tient le processeur éveillé ou le relâche selon l'alimentation et le réglage ; arme le réveil suivant. */
    @Synchronized
    fun apply(context: Context) {
        val app = context.applicationContext
        val hold = plugged(app) || Store(app).awakeAlways
        if (hold && cpu?.isHeld != true) {
            cpu = power(app).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "loco:joignable").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
        if (hold && wifi?.isHeld != true) {
            wifi = try {
                @Suppress("DEPRECATION")
                (app.getSystemService(Context.WIFI_SERVICE) as WifiManager)
                    .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "loco:joignable").apply {
                        setReferenceCounted(false)
                        acquire()
                    }
            } catch (_: Exception) {
                null
            }
        }
        if (!hold) release()
        arm(app)
    }

    @Synchronized
    fun release() {
        cpu?.let { if (it.isHeld) it.release() }
        cpu = null
        wifi?.let { try { if (it.isHeld) it.release() } catch (_: Exception) {} }
        wifi = null
    }

    /** Prochain réveil, même en veille profonde (setAndAllowWhileIdle, sans permission d'alarme exacte). */
    fun arm(context: Context) {
        val alarms = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val intent = PendingIntent.getBroadcast(context, 0, Intent(context, KeepAwakeReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        try {
            alarms.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + EVERY, intent)
        } catch (_: Exception) {
        }
    }
}

/** Réveil programmé : le processeur reste éveillé quinze secondes, le temps d'un contact et de rouvrir le canal (sur
 *  batterie, sans le garder plus : un message de loco sur le canal le réveille ensuite de lui-même). */
class KeepAwakeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        try {
            (context.getSystemService(Context.POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "loco:contact").acquire(15_000L)
        } catch (_: Exception) {
        }
        KeepAwake.apply(context)
        LocoService.wakeUp(context)
    }
}
