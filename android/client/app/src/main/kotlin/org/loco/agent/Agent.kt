package org.loco.agent

import android.content.Context
import org.json.JSONObject

/** Logique de l'agent : inscription puis contacts réguliers (check-in) avec loco. */
class Agent(private val context: Context) {
    val store = Store(context)

    /** Inscrit l'appareil. L'autorité a déjà été confirmée par l'utilisateur et est épinglée ici. */
    fun enroll(server: String, key: String, caPem: String) {
        val body = Inventory.identity(context)
        body.put("uid", store.uid)
        body.put("key", key)
        val resp = Net.postJson("$server/agent/api/enroll", body.toString(), caPem)
        val token = JSONObject(resp).getString("token")
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
