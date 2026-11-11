package org.loco.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import org.json.JSONObject

/**
 * Appairage adb fait depuis l'appareil lui-même, pour une image fluide à chaque session : la personne ouvre
 * « Débogage sans fil › Associer l'appareil avec un code d'association » et tape le code affiché dans la notification
 * de loco, sans quitter cette fenêtre (elle se ferme si l'on change d'application). L'application trouve sur le
 * réseau local le port d'association que l'appareil annonce, et transmet port et code au serveur loco, qui appaire
 * avec l'adresse de cet appareil. C'est le même appairage que depuis la fiche du poste dans le portail : au contact
 * suivant, loco autorise le partage d'écran de l'application une fois pour toutes (plus de fenêtre « Projeter »).
 */
object Pairing {
    private const val SERVICE = "_adb-tls-pairing._tcp"
    private const val CHANNEL = "pairing"
    private const val NOTIFICATION = 7301
    const val KEY_CODE = "code"
    private const val ACTION = "org.loco.agent.PAIR"

    @Volatile private var port = 0
    private var listener: NsdManager.DiscoveryListener? = null
    /** Dernier résultat, montré dans l'application ; vide tant que rien n'a été tenté. */
    @Volatile var result = ""
        private set

    /** Prépare l'appairage : notification où saisir le code, recherche du port, réglages du débogage sans fil. */
    fun start(context: Context) {
        result = ""
        discover(context.applicationContext)
        notifyAsk(context, context.getString(R.string.pair_notif_text))
        openWirelessDebugging(context)
    }

    /** Ouvre directement le réglage « Débogage sans fil » quand Android le permet, sinon les options pour les développeurs. */
    fun openWirelessDebugging(context: Context) {
        val args = Bundle().apply { putString(":settings:fragment_args_key", "toggle_adb_wireless") }
        val intent = Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
            .putExtra(":settings:fragment_args_key", "toggle_adb_wireless")
            .putExtra(":settings:show_fragment_args", args)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try { context.startActivity(intent) } catch (_: Exception) {
            try { context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (_: Exception) {}
        }
    }

    private fun notifyAsk(context: Context, text: String) {
        val input = RemoteInput.Builder(KEY_CODE).setLabel(context.getString(R.string.pair_code_label)).build()
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
        val reply = PendingIntent.getBroadcast(context, NOTIFICATION,
            Intent(context, PairingReceiver::class.java).setAction(ACTION), flags)
        val action = Notification.Action.Builder(Icon.createWithResource(context, R.drawable.ic_stat_loco),
            context.getString(R.string.pair_code_action), reply).addRemoteInput(input).build()
        show(context, text, action, ongoing = true)
    }

    fun show(context: Context, text: String, action: Notification.Action? = null, ongoing: Boolean = false) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.pair_channel),
                NotificationManager.IMPORTANCE_HIGH))
        }
        val builder = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_loco)
            .setContentTitle(context.getString(R.string.pair_notif_title))
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setOngoing(ongoing)
            .setAutoCancel(!ongoing)
            // Groupe à elle : Android ne la range pas sous la notification du service, repliée et sans son champ de saisie.
            .setGroup("org.loco.agent.pairing")
            .setCategory(Notification.CATEGORY_STATUS)
        action?.let { builder.addAction(it) }
        nm.notify(NOTIFICATION, builder.build())
    }

    /** Code saisi dans la notification : port trouvé, envoi au serveur, résultat affiché. Hors du fil principal. */
    fun submit(context: Context, code: String) {
        show(context, context.getString(R.string.pair_working), ongoing = true)
        Thread({
            val store = Store(context)
            var waited = 0
            while (port == 0 && waited < 6000) { Thread.sleep(200); waited += 200 }
            val message = when {
                !Regex("\\d{6}").matches(code.trim()) -> context.getString(R.string.pair_bad_code)
                port == 0 -> context.getString(R.string.pair_no_port)
                else -> try {
                    Net.postJson("${store.server}/agent/api/adb-pair",
                        JSONObject().put("code", code.trim()).put("port", port).toString(), store.caPem, store.token, 60000)
                    stop(context)
                    LocoService.checkNow(context)
                    ""
                } catch (e: Net.HttpError) {
                    try { JSONObject(e.body).optString("error") } catch (_: Exception) { "" }.ifEmpty { "HTTP ${e.code}" }
                } catch (e: Exception) {
                    context.getString(R.string.pair_unreachable)
                }
            }
            result = if (message.isEmpty()) context.getString(R.string.pair_done) else message
            if (message.isEmpty()) show(context, result) else notifyAsk(context, result + "\n" + context.getString(R.string.pair_notif_text))
        }, "loco-pairing").start()
    }

    @Synchronized private fun discover(context: Context) {
        if (listener != null) return
        port = 0
        val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
        val l = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) {
                @Suppress("DEPRECATION")
                nsd.resolveService(info, object : NsdManager.ResolveListener {
                    override fun onServiceResolved(resolved: NsdServiceInfo) {
                        val address = resolved.host?.hostAddress?.substringBefore('%') ?: return
                        // Seulement l'annonce de cet appareil : un autre appareil du réseau peut aussi être en association.
                        if (address in AdbProbe.ownAddresses() || resolved.host?.isLoopbackAddress == true) port = resolved.port
                    }
                    override fun onResolveFailed(info: NsdServiceInfo, code: Int) {}
                })
            }
            override fun onServiceLost(info: NsdServiceInfo) {}
            override fun onDiscoveryStarted(type: String) {}
            override fun onDiscoveryStopped(type: String) {}
            override fun onStartDiscoveryFailed(type: String, code: Int) { synchronized(this@Pairing) { listener = null } }
            override fun onStopDiscoveryFailed(type: String, code: Int) {}
        }
        listener = l
        try { nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, l) } catch (_: Exception) { listener = null }
    }

    @Synchronized private fun stop(context: Context) {
        val l = listener ?: return
        listener = null
        try { (context.getSystemService(Context.NSD_SERVICE) as NsdManager).stopServiceDiscovery(l) } catch (_: Exception) {}
    }
}

/** Réponse saisie dans la notification d'appairage. */
class PairingReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val code = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(Pairing.KEY_CODE)?.toString() ?: return
        Pairing.submit(context.applicationContext, code)
    }
}
