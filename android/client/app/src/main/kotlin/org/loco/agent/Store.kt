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

    /** Identifiant stable de cet appareil (32 hex), généré une fois ; le serveur peut en attribuer un autre à
     *  l'inscription (identifiant déjà pris par une fiche dont on n'a pas le jeton). */
    var uid: String
        get() {
            prefs.getString("uid", null)?.let { return it }
            val b = ByteArray(16); SecureRandom().nextBytes(b)
            val u = b.joinToString("") { "%02x".format(it) }
            prefs.edit().putString("uid", u).apply()
            return u
        }
        set(v) = prefs.edit().putString("uid", v).apply()

    /** Empreinte (SHA-256) du dernier jeton reçu, gardée quand l'appareil quitte loco : s'il s'y réinscrit, elle lui
     *  permet de prouver que c'est bien lui, et il retrouve sa fiche. Elle ne permet pas de contacter le serveur. */
    val proof: String get() = prefs.getString("proof", "") ?: ""

    /** Dernière mise à jour de l'application tentée (empreinte, instant) : pas de nouvel essai avant un jour. */
    var updateTried: String
        get() = prefs.getString("update_sha", "") ?: ""
        set(v) = prefs.edit().putString("update_sha", v).apply()
    var updateTriedAt: Long
        get() = prefs.getLong("update_at", 0L)
        set(v) = prefs.edit().putLong("update_at", v).apply()

    /** loco a appairé cet appareil par adb et demande de garder son débogage sans fil joignable (dernière consigne reçue). */
    var keepAdb: Boolean
        get() = prefs.getBoolean("keep_adb", false)
        set(v) = prefs.edit().putBoolean("keep_adb", v).apply()

    val enrolled: Boolean get() = token.isNotEmpty() && server.isNotEmpty() && caPem.isNotEmpty()

    fun clear() {
        val edit = prefs.edit()
        val secret = token.substringAfter('.', "")
        if (secret.isNotEmpty()) edit.putString("proof", Apps.sha256(secret.toByteArray()))
        edit.remove("server").remove("token").remove("ca").remove("keep_adb").apply()
    }
}
