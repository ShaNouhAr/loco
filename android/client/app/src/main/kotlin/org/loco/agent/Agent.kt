package org.loco.agent

import android.content.Context
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.json.JSONObject

/** Logique de l'agent : inscription puis contacts réguliers (check-in) avec loco. */
class Agent(private val context: Context) {
    val store = Store(context)

    /** Inscrit l'appareil. L'autorité a déjà été confirmée par l'utilisateur et est épinglée ici. */
    fun enroll(server: String, key: String, caPem: String) {
        val body = Inventory.identity(context)
        body.put("uid", store.uid)
        body.put("key", key)
        // Réinscription : preuve que cet appareil détenait son jeton (HMAC de son empreinte, jamais le jeton lui-même :
        // elle ne sert à rien à un autre serveur). Le serveur lui rend alors sa fiche ; sans elle, il l'inscrit comme
        // un nouvel appareil.
        val held = store.token.substringAfter('.', "").takeIf { it.isNotEmpty() }?.let { Apps.sha256(it.toByteArray()) } ?: store.proof
        if (held.isNotEmpty()) {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(held.toByteArray(), "HmacSHA256"))
            body.put("proof", mac.doFinal("loco-enroll-v1\n${store.uid}\n$key".toByteArray()).joinToString("") { "%02x".format(it) })
        }
        val answer = JSONObject(Net.postJson("$server/agent/api/enroll", body.toString(), caPem))
        val token = answer.getString("token")
        answer.optString("uid").takeIf { it.isNotEmpty() }?.let { store.uid = it }      // identité retenue par le serveur
        store.server = server
        store.caPem = caPem
        store.token = token
    }

    /** Un contact : envoie l'inventaire, renvoie la réponse du serveur (intervalle, commandes…). */
    fun checkin(): JSONObject {
        val resp = Net.postJson("${store.server}/agent/api/checkin", Inventory.full(context).toString(),
            store.caPem, store.token)
        return JSONObject(resp)
    }
}
