package org.loco.agent

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Build
import android.os.SystemClock
import android.provider.Settings

/**
 * Réseaux de confiance : la personne choisit où l'agent est actif — partout, hors données mobiles, ou seulement sur les
 * Wi-Fi qu'elle a nommés. Ailleurs l'agent est en pause : aucun contact avec loco, aucune session, et le débogage sans
 * fil n'est pas rallumé.
 *
 * Android ne donne le nom d'un Wi-Fi qu'à une application autorisée à la localisation, « toujours » pour un service
 * d'arrière-plan. loco ne lit jamais la position : l'autorisation n'est demandée que si la personne choisit le mode par
 * nom, et ne sert qu'à cette lecture. Sans elle le nom reste illisible et l'agent en pause : il n'est jamais actif par
 * défaut sur un réseau qu'il ne sait pas nommer.
 */
object Networks {
    const val ALL = "all"
    const val WIFI = "wifi"
    const val TRUSTED = "trusted"

    /** Pourquoi l'agent est actif ou en pause sur le réseau du moment. */
    enum class Why { OK, MOBILE, WIRED, OTHER_WIFI, UNNAMED }

    class Status(val active: Boolean, val why: Why, val wifi: String?)

    @Volatile private var cached: Status? = null
    @Volatile private var cachedAt = 0L

    /** État du moment, gardé quelques secondes : le relais des sessions le consulte à chaque échange. */
    fun current(context: Context, store: Store): Status {
        val now = SystemClock.elapsedRealtime()
        cached?.let { if (now - cachedAt < 4000) return it }
        return status(context, store).also { cached = it; cachedAt = now }
    }

    /** Le réseau ou le réglage a changé : relire au prochain appel. */
    fun invalidate() {
        cached = null
    }

    fun status(context: Context, store: Store): Status {
        val mode = store.netMode
        if (mode != WIFI && mode != TRUSTED) return Status(true, Why.OK, null)
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        // Aucun réseau : rien à retenir, l'agent est simplement hors ligne.
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return Status(true, Why.OK, null)
        val wifi = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
        val wired = caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
        if (mode == WIFI) return if (wifi || wired) Status(true, Why.OK, null) else Status(false, Why.MOBILE, null)
        if (!wifi) return Status(false, if (wired) Why.WIRED else Why.MOBILE, null)
        val name = wifiName(context) ?: return Status(false, Why.UNNAMED, null)
        return if (name in store.trustedWifi) Status(true, Why.OK, name) else Status(false, Why.OTHER_WIFI, name)
    }

    /** Nom du Wi-Fi auquel l'appareil est relié ; null s'il n'y en a pas ou si Android le cache (localisation). */
    fun wifiName(context: Context): String? {
        val raw = try {
            val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            @Suppress("DEPRECATION")
            wm.connectionInfo?.ssid
        } catch (_: Exception) {
            null
        }
        if (raw.isNullOrEmpty() || raw == "<unknown ssid>") return null
        return if (raw.length >= 2 && raw.startsWith("\"") && raw.endsWith("\"")) raw.substring(1, raw.length - 1) else raw
    }

    fun onWifi(context: Context): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        return cm.getNetworkCapabilities(cm.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
    }

    fun fineGranted(context: Context) =
        context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** « Toujours autoriser » : exigé depuis Android 10 pour lire le nom du Wi-Fi quand l'application n'est pas à l'écran. */
    fun alwaysGranted(context: Context) = Build.VERSION.SDK_INT < 29 ||
        context.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** Localisation de l'appareil allumée : coupée, Android cache le nom du Wi-Fi même à une application autorisée. */
    fun locationOn(context: Context): Boolean = try {
        if (Build.VERSION.SDK_INT >= 28) (context.getSystemService(Context.LOCATION_SERVICE) as LocationManager).isLocationEnabled
        else @Suppress("DEPRECATION") Settings.Secure.getInt(context.contentResolver, Settings.Secure.LOCATION_MODE) != Settings.Secure.LOCATION_MODE_OFF
    } catch (_: Exception) {
        true
    }
}
