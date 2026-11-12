package org.loco.agent

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.util.TreeMap

/**
 * Relais du bureau/terminal/fichiers : interroge /agent/api/remote, relaie l'écran encodé et les entrées par paquets
 * numérotés et acquittés (jamais de rejeu après une coupure). Aucune connexion entrante : l'appareil sort en HTTPS.
 */
class RemoteClient(private val context: Context, private val store: Store, private val screen: () -> FrameSource?,
                   private val screenReady: () -> Boolean, private val allowed: () -> Boolean,
                   private val onWatching: (Boolean) -> Unit) {
    private val sessions = HashMap<String, Session>()
    @Volatile private var running = true
    /** Vrai tant qu'une session (écran, terminal, fichiers) est ouverte : pas de mise à jour de l'application pendant ce temps. */
    @Volatile var inUse = false
        private set
    private var watching = false     // une session d'écran est ouverte : l'appareil l'affiche (notification)
    private var lastActivity = 0L
    private var failures = 0
    private val stats = Rfb.Stats("échanges")

    private inner class Session(val id: String, val kind: String) {
        val toAgent = Pipe()         // navigateur -> agent (messages RFB, frappes, requêtes de fichiers)
        val toBrowser = Pipe()       // agent -> navigateur (écran encodé, sortie du terminal, réponses)
        var ack = 0L                 // dernier paquet entrant consommé
        var seq = 0L                 // compteur sortant
        var pending = ByteArray(0)   // un seul envoi en vol, gardé jusqu'à l'acquittement
        var state = "active"
        var error = ""
        var consent = ""
        private val inbound = TreeMap<Long, ByteArray>()
        var thread: Thread? = null

        /** Fil de la session ; à sa fin, ce qui reste à envoyer part encore, puis la session se ferme. */
        fun start(name: String, body: () -> Unit) {
            thread = Thread({
                try {
                    body()
                } catch (e: Exception) {
                    error = (e.message ?: "session terminée").take(300)
                } finally {
                    toBrowser.close()
                }
            }, "loco-$name-$id").also { it.isDaemon = true; it.start() }
        }

        fun ingest(packets: JSONArray) {
            for (i in 0 until packets.length()) {
                val p = packets.getJSONObject(i)
                val s = p.getLong("seq")
                if (s <= ack || inbound.containsKey(s)) continue
                inbound[s] = Base64.decode(p.getString("data"), Base64.DEFAULT)
            }
            while (inbound.containsKey(ack + 1)) {
                val s = ack + 1
                toAgent.write(inbound.remove(s)!!)
                ack = s
            }
        }

        fun payload(): JSONObject {
            if (state == "active" && pending.isEmpty()) {
                val data = toBrowser.take(256 * 1024)
                if (data.isNotEmpty()) { pending = data; seq += 1 }
                else if (toBrowser.isClosed) { state = "closed"; if (error.isEmpty()) error = "Session terminée." }
            }
            return JSONObject()
                .put("id", id).put("ack", ack).put("seq", seq)
                .put("data", Base64.encodeToString(pending, Base64.NO_WRAP))
                .put("state", state).put("error", error).put("consent", consent)
        }

        fun close() {
            state = "closed"
            toAgent.close(); toBrowser.close()
        }
    }

    /** Un échange avec le serveur. Renvoie le délai conseillé avant le prochain, en millisecondes. */
    fun exchange(): Long {
        val payload = JSONObject()
        val arr = JSONArray()
        for (s in sessions.values) arr.put(s.payload())
        payload.put("sessions", arr)

        val started = System.currentTimeMillis()
        val body = payload.toString()
        val resp = JSONObject(Net.postJson("${store.server}/agent/api/remote", body, store.caPem, store.token, timeoutMs = 20000))
        if (sessions.isNotEmpty()) stats.add(body.length, System.currentTimeMillis() - started)
        val descriptors = resp.optJSONArray("sessions") ?: JSONArray()
        val seen = HashMap<String, JSONObject>()
        for (i in 0 until descriptors.length()) {
            val d = descriptors.getJSONObject(i)
            seen[d.getString("id")] = d
        }
        // Sessions disparues ou fermées côté serveur
        for (id in sessions.keys.toList()) {
            val d = seen[id]
            if (d == null || d.optString("state") == "closed") sessions.remove(id)?.close()
        }
        var busy = false
        var opened = false
        for ((id, d) in seen) {
            val stateD = d.optString("state")
            if (stateD != "waiting" && stateD != "active") continue
            val s = sessions.getOrPut(id) {
                opened = true
                Session(id, d.optString("kind")).also { session ->
                    val src = screen()
                    when (session.kind) {
                        "desktop" -> if (src != null) {
                            session.start("rfb") { Rfb.serveDesktop(session.toAgent, session.toBrowser, src, ScaledInput(context, src)) }
                        } else {
                            session.state = "closed"; session.error = context.getString(R.string.remote_no_screen)
                        }
                        "terminal" -> session.start("terminal") {
                            Terminal(context, d.optInt("cols", 80), d.optInt("rows", 24)).serve(session.toAgent, session.toBrowser)
                        }
                        "files" -> session.start("files") { FileSession(context).serve(session.toAgent, session.toBrowser) }
                        else -> { session.state = "closed"; session.error = context.getString(R.string.remote_unsupported) }
                    }
                }
            }
            if (d.optLong("ack") == s.seq) s.pending = ByteArray(0)
            // Accord : sur son propre appareil, l'autorisation de capture d'écran vaut accord.
            if (d.has("consent") && s.consent.isEmpty()) s.consent = if (screenReady()) "given" else "nobody"
            d.optJSONArray("packets")?.let { if (it.length() > 0) { s.ingest(it); busy = true } }
            if (s.toBrowser.available() > 0 || s.pending.isNotEmpty()) busy = true
        }
        // La poignée de main et le flux sont interactifs : rester rapide tant qu'il y a de l'activité récente,
        // ralentir seulement après un vrai silence (onglet caché, écran figé sans demande du navigateur).
        inUse = sessions.isNotEmpty()
        val seenNow = sessions.values.any { it.kind == "desktop" && it.state == "active" }
        if (seenNow != watching) { watching = seenNow; onWatching(seenNow) }
        val now = System.currentTimeMillis()
        // Une session qui s'ouvre compte comme une activité : sans cela, après une longue pause, son premier texte
        // attendait le relevé lent (1,2 s) d'une session silencieuse.
        if (busy || opened || sessions.isNotEmpty() && lastActivity == 0L) lastActivity = now
        // Aucune session : cadence demandée par le serveur — courte quand la fiche de l'appareil est ouverte dans le
        // portail, pour qu'une session s'ouvre sans attendre —, jamais plus lente qu'avant (3 s).
        if (sessions.isEmpty()) return (resp.optDouble("interval", 3.0).let { if (it.isNaN()) 3.0 else it } * 1000).toLong().coerceIn(300L, 3000L)
        // Des octets attendent encore (image plus grosse qu'un envoi) : enchaîner sans pause.
        if (sessions.values.any { it.toBrowser.available() > 0 }) return 0
        val idle = now - lastActivity
        return when {
            idle < 4000 -> 10
            idle < 15000 -> 250
            else -> 1200
        }
    }

    fun run() {
        while (running) {
            if (!allowed()) {
                // Hors réseau de confiance : les sessions ouvertes se ferment, et rien n'est demandé à loco.
                if (sessions.isNotEmpty() || watching) pause()
                try { Thread.sleep(2000) } catch (_: InterruptedException) {}
                continue
            }
            val delay = try {
                exchange()
            } catch (e: Net.HttpError) {
                android.util.Log.e("loco-remote", "HTTP ${e.code} : ${e.body.take(120)}")
                if (e.code == 401) { stop(); return }
                2000
            } catch (e: Exception) {
                android.util.Log.e("loco-remote", "échange", e)
                // Une connexion gardée a pu être fermée par le serveur : un nouvel essai tout de suite la remplace
                // (rien n'est rejoué, les paquets sont numérotés). Ensuite seulement, espacer.
                if (++failures <= 2) 150 else 3000
            }
            if (delay < 150) failures = 0
            try { Thread.sleep(delay) } catch (_: InterruptedException) {}
        }
    }

    fun stop() {
        running = false
        pause()
    }

    private fun pause() {
        for (s in sessions.values) s.close()
        sessions.clear()
        inUse = false
        if (watching) { watching = false; onWatching(false) }
    }

    /** Entrées vers le service d'accessibilité, recalées sur la taille réelle de l'écran. Le service est cherché à
     *  chaque événement : activé en cours de session, il prend effet sans rouvrir le bureau. */
    private class ScaledInput(private val context: Context, private val source: FrameSource) : InputSink {
        private fun sx(x: Int) = if (source.width > 0) x * source.screenWidth / source.width else x
        private fun sy(y: Int) = if (source.height > 0) y * source.screenHeight / source.height else y
        override fun pointer(buttons: Int, x: Int, y: Int) { InputService.instance?.pointer(buttons, sx(x), sy(y)) }
        override fun key(down: Boolean, keysym: Long) { InputService.instance?.key(down, keysym) }
        override fun clipboard(text: String) {
            Handler(Looper.getMainLooper()).post {
                try {
                    (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                        .setPrimaryClip(ClipData.newPlainText("loco", text))
                } catch (_: Exception) {
                }
            }
        }
    }
}
