package org.loco.agent

import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.provider.Settings
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

/** Ce que loco doit savoir pour ouvrir un terminal adb : options pour les développeurs, débogage sans fil, et le port
 *  de connexion que l'appareil annonce sur le réseau local (il change à chaque activation). C'est la personne qui
 *  active le débogage et qui appaire loco, sur l'appareil ; ensuite seulement, [keep] le rallume quand il se coupe. */
object AdbProbe {
    private const val SERVICE = "_adb-tls-connect._tcp"
    @Volatile private var port = 0
    @Volatile private var host = ""
    private var listener: NsdManager.DiscoveryListener? = null

    fun snapshot(context: Context): JSONObject {
        val cr = context.contentResolver
        val developer = Settings.Global.getInt(cr, Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) == 1
        val wireless = Settings.Global.getInt(cr, "adb_wifi_enabled", 0) == 1
        if (wireless) start(context) else stop(context)
        return JSONObject().put("developer", developer).put("wireless", wireless)
            .put("port", if (wireless) port else 0).put("ip", if (wireless) host else "")
    }

    /** Rallume le débogage sans fil coupé par Android (redémarrage, Wi-Fi perdu puis retrouvé), pour que loco garde
     *  l'accès que la personne lui a donné en l'appairant. Possible seulement avec le droit que loco reçoit par adb à
     *  l'appairage (WRITE_SECURE_SETTINGS), en Wi-Fi, et tant que les options pour les développeurs restent activées.
     *  Sur un réseau Wi-Fi nouveau, Android demande encore de confirmer sur l'appareil. */
    fun keep(context: Context) {
        try {
            if (context.checkSelfPermission("android.permission.WRITE_SECURE_SETTINGS") != PackageManager.PERMISSION_GRANTED) return
            val cr = context.contentResolver
            if (Settings.Global.getInt(cr, Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0) != 1) return
            if (Settings.Global.getInt(cr, "adb_wifi_enabled", 0) == 1 || !onWifi(context)) return
            Settings.Global.putInt(cr, "adb_wifi_enabled", 1)
        } catch (_: Exception) {
        }
    }

    private fun onWifi(context: Context): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    @Synchronized private fun start(context: Context) {
        if (listener != null) return
        val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
        val mine = ownAddresses()
        val l = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) {
                @Suppress("DEPRECATION")
                nsd.resolveService(info, object : NsdManager.ResolveListener {
                    override fun onServiceResolved(resolved: NsdServiceInfo) {
                        val address = resolved.host?.hostAddress ?: return
                        // Seulement le service de cet appareil : un autre appareil du réseau peut aussi l'annoncer.
                        if (address in mine || resolved.host?.isLoopbackAddress == true) {
                            port = resolved.port
                            if (resolved.host is Inet4Address) host = address
                        }
                    }
                    override fun onResolveFailed(info: NsdServiceInfo, code: Int) {}
                })
            }
            override fun onServiceLost(info: NsdServiceInfo) {}
            override fun onDiscoveryStarted(type: String) {}
            override fun onDiscoveryStopped(type: String) {}
            override fun onStartDiscoveryFailed(type: String, code: Int) { synchronized(this@AdbProbe) { listener = null } }
            override fun onStopDiscoveryFailed(type: String, code: Int) {}
        }
        listener = l
        try {
            nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, l)
        } catch (_: Exception) {
            listener = null
        }
    }

    @Synchronized private fun stop(context: Context) {
        port = 0; host = ""
        val l = listener ?: return
        listener = null
        try {
            (context.getSystemService(Context.NSD_SERVICE) as NsdManager).stopServiceDiscovery(l)
        } catch (_: Exception) {
        }
    }

    internal fun ownAddresses(): Set<String> = try {
        NetworkInterface.getNetworkInterfaces().toList().flatMap { it.inetAddresses.toList() }
            .mapNotNull { it.hostAddress?.substringBefore('%') }.toSet()
    } catch (_: Exception) {
        emptySet()
    }
}
