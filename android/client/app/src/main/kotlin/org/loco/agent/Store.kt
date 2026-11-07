package org.loco.agent

import android.content.Context
import java.security.SecureRandom

/** Réglages persistants de l'agent, dans les préférences privées de l'application. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("loco", Context.MODE_PRIVATE)

    var server: String
        get() = prefs.getString("server", "") ?: ""
        set(v) = prefs.edit().putString("server", v).apply()

    /** Jeton « uid.secret » rendu par l'inscription ; porté dans l'en-tête Authorization. */
    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    /** Autorité (CA) de loco épinglée à l'inscription : seul certificat de confiance ensuite. */
    var caPem: String
        get() = prefs.getString("ca", "") ?: ""
        set(v) = prefs.edit().putString("ca", v).apply()

    /** Identifiant stable de cet appareil (32 hex), généré une fois. */
    val uid: String
        get() {
            prefs.getString("uid", null)?.let { return it }
            val b = ByteArray(16); SecureRandom().nextBytes(b)
            val u = b.joinToString("") { "%02x".format(it) }
            prefs.edit().putString("uid", u).apply()
            return u
        }

    /** Dernière mise à jour de l'application tentée (empreinte, instant) : pas de nouvel essai avant un jour. */
    var updateTried: String
        get() = prefs.getString("update_sha", "") ?: ""
        set(v) = prefs.edit().putString("update_sha", v).apply()
    var updateTriedAt: Long
        get() = prefs.getLong("update_at", 0L)
        set(v) = prefs.edit().putLong("update_at", v).apply()

    val enrolled: Boolean get() = token.isNotEmpty() && server.isNotEmpty() && caPem.isNotEmpty()

    fun clear() {
        prefs.edit().remove("server").remove("token").remove("ca").apply()
    }
}
