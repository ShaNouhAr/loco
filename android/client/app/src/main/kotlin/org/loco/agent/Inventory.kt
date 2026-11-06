package org.loco.agent

import android.content.Context
import android.os.Build
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.net.NetworkInterface

/** Ce que l'appareil déclare à loco : identité et inventaire des applications. Android ne gère ni mises à jour
 *  système ni catalogue de logiciels côté loco, donc « updates » reste vide. */
object Inventory {
    fun identity(context: Context): JSONObject = JSONObject().apply {
        put("hostname", deviceName(context))
        put("os", JSONObject().put("family", "android").put("name", osName()))
        put("agent_version", BuildConfig.VERSION_NAME)
        put("addresses", JSONArray(addresses()))
    }

    fun full(context: Context): JSONObject = identity(context).apply {
        put("inventory", JSONObject()
            .put("updates", JSONArray())
            .put("apps", apps(context)))
    }

    fun deviceName(context: Context): String {
        val n = Settings.Global.getString(context.contentResolver, "device_name")
        return if (!n.isNullOrBlank()) n else "${Build.MANUFACTURER} ${Build.MODEL}".trim()
    }

    fun osName(): String = "Android ${Build.VERSION.RELEASE} · ${Build.MODEL}"

    private fun addresses(): List<String> {
        val out = ArrayList<String>()
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                for (a in ni.inetAddresses) {
                    if (a.isLoopbackAddress || a.isLinkLocalAddress) continue
                    a.hostAddress?.let { out.add(it) }
                }
            }
        } catch (_: Exception) {
        }
        return out.distinct().take(16)
    }

    private fun apps(context: Context): JSONArray {
        val pm = context.packageManager
        val arr = JSONArray()
        for (p in pm.getInstalledPackages(0)) {
            val label = try {
                pm.getApplicationLabel(p.applicationInfo).toString()
            } catch (_: Exception) {
                p.packageName
            }
            arr.put(JSONObject().put("name", label).put("version", p.versionName ?: "").put("publisher", p.packageName))
            if (arr.length() >= 2000) break
        }
        return arr
    }
}
