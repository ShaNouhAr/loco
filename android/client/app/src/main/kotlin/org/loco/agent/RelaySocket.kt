package org.loco.agent

import android.util.Base64
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import javax.net.ssl.SSLSocket

/**
 * Connexion persistante au relais de loco (WebSocket, RFC 6455, autorité épinglée et nom vérifié comme pour les
 * échanges HTTPS) : loco y prévient l'appareil de ce qui arrive (session demandée, saisie), qui n'a plus à interroger à
 * vide. Les échanges sont ceux de /agent/api/remote : si le canal manque ou tombe, l'appareil reprend ses
 * allers-retours HTTPS sans perdre une session.
 */
class RelaySocket(server: String, token: String, caPem: String, private val onWake: () -> Unit) {
    /** Serveur et jeton de cette connexion : s'ils changent, elle est refaite. */
    val key = "$server\n$token"
    @Volatile var broken = false
        private set
    private val socket: SSLSocket
    private val input: InputStream
    private val output: OutputStream
    private val answers = ArrayDeque<String>()
    private val lock = Object()
    private val random = SecureRandom()

    init {
        val url = URL(server)
        val port = if (url.port > 0) url.port else 443
        val plain = Socket()
        try {
            plain.connect(InetSocketAddress(url.host, port), 10000)
            plain.tcpNoDelay = true
            socket = Net.socketFactory(caPem).createSocket(plain, url.host, port, true) as SSLSocket
            socket.soTimeout = 15000
            socket.sslParameters = socket.sslParameters.also { it.endpointIdentificationAlgorithm = "HTTPS" }   // le nom du certificat compte aussi
            socket.startHandshake()
            input = socket.inputStream
            output = socket.outputStream
            val nonce = ByteArray(16).also { random.nextBytes(it) }
            val secret = Base64.encodeToString(nonce, Base64.NO_WRAP)
            val host = if (url.port > 0) "${url.host}:${url.port}" else url.host
            output.write(("GET ${url.path.trimEnd('/')}/agent/relay HTTP/1.1\r\nHost: $host\r\nUpgrade: websocket\r\n" +
                    "Connection: Upgrade\r\nSec-WebSocket-Key: $secret\r\nSec-WebSocket-Version: 13\r\n" +
                    "Authorization: Bearer $token\r\nUser-Agent: loco-android\r\n\r\n").toByteArray(Charsets.US_ASCII))
            output.flush()
            val head = StringBuilder()
            while (!head.endsWith("\r\n\r\n")) {
                val b = input.read()
                if (b < 0 || head.length > 16384) throw IOException("relais : réponse incomplète")
                head.append(b.toChar())
            }
            val lines = head.split("\r\n")
            val accept = lines.firstOrNull { it.startsWith("Sec-WebSocket-Accept:", ignoreCase = true) }?.substringAfter(':')?.trim()
            val expected = Base64.encodeToString(MessageDigest.getInstance("SHA-1").digest((secret + MAGIC).toByteArray(Charsets.US_ASCII)), Base64.NO_WRAP)
            if (!lines[0].startsWith("HTTP/1.1 101") || accept != expected) throw IOException("relais indisponible")
            socket.soTimeout = 0         // le fil de lecture attend sans limite : loco ne parle que s'il a quelque chose à dire
        } catch (e: Exception) {
            try { plain.close() } catch (_: Exception) {}
            throw e
        }
        Thread(::read, "loco-relay").also { it.isDaemon = true }.start()
    }

    private fun exact(n: Int): ByteArray {
        val out = ByteArray(n)
        var got = 0
        while (got < n) {
            val k = input.read(out, got, n - got)
            if (k < 0) throw EOFException()
            got += k
        }
        return out
    }

    private fun frame(opcode: Int, data: ByteArray) {
        val mask = ByteArray(4).also { random.nextBytes(it) }
        val out = ByteArrayOutputStream(data.size + 14)
        out.write(0x80 or opcode)
        when {
            data.size < 126 -> out.write(0x80 or data.size)
            data.size < 65536 -> { out.write(0x80 or 126); out.write(data.size shr 8); out.write(data.size) }
            else -> { out.write(0x80 or 127); for (i in 7 downTo 0) out.write((data.size.toLong() shr (8 * i)).toInt()) }
        }
        out.write(mask)
        val masked = ByteArray(data.size)
        for (i in data.indices) masked[i] = (data[i].toInt() xor mask[i and 3].toInt()).toByte()
        out.write(masked)
        synchronized(output) { output.write(out.toByteArray()); output.flush() }
    }

    /** Fil de lecture : réponses rangées pour l'échange en cours ; un « wake » réveille la boucle des sessions. */
    private fun read() {
        try {
            val parts = ByteArrayOutputStream()
            while (true) {
                val head = exact(2)
                val fin = head[0].toInt() and 0x80 != 0
                val opcode = head[0].toInt() and 0x0f
                var size = (head[1].toInt() and 0x7f).toLong()
                if (size == 126L) { val e = exact(2); size = ((e[0].toLong() and 0xff) shl 8) or (e[1].toLong() and 0xff) }
                else if (size == 127L) { size = 0; for (b in exact(8)) size = (size shl 8) or (b.toLong() and 0xff) }
                if (size < 0 || size > LIMIT || parts.size() + size > LIMIT) throw IOException("relais : message trop volumineux")
                val mask = if (head[1].toInt() and 0x80 != 0) exact(4) else null
                val data = exact(size.toInt())
                if (mask != null) for (i in data.indices) data[i] = (data[i].toInt() xor mask[i and 3].toInt()).toByte()
                when {
                    opcode == 8 -> throw IOException("relais : connexion fermée par le serveur")
                    opcode == 9 -> frame(10, data)
                    opcode <= 2 -> {
                        parts.write(data)
                        if (fin) {
                            val text = parts.toString("UTF-8")
                            parts.reset()
                            if (JSONObject(text).optBoolean("wake")) onWake()
                            else synchronized(lock) { answers.addLast(text); lock.notifyAll() }
                        }
                    }
                }
            }
        } catch (_: Exception) {
            // connexion rompue : signalée ci-dessous
        } finally {
            broken = true
            synchronized(lock) { lock.notifyAll() }
            onWake()
        }
    }

    /** Un échange : même réponse que POST /agent/api/remote ; un refus du portail lève la même erreur que par HTTPS. */
    fun exchange(body: String, timeoutMs: Long): String {
        if (broken) throw IOException("relais : connexion rompue")
        frame(1, body.toByteArray(Charsets.UTF_8))
        val answer = synchronized(lock) {
            val until = System.currentTimeMillis() + timeoutMs
            while (answers.isEmpty()) {
                if (broken) throw IOException("relais : connexion rompue")
                val left = until - System.currentTimeMillis()
                if (left <= 0) { broken = true; throw IOException("relais : réponse attendue en vain") }
                lock.wait(left)
            }
            answers.removeFirst()
        }
        if (answer.length < 64 && answer.startsWith("{\"status\"")) throw Net.HttpError(JSONObject(answer).getInt("status"), "")
        return answer
    }

    fun close() {
        broken = true
        try { socket.close() } catch (_: Exception) {}
    }

    private companion object {
        const val MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        const val LIMIT = 8L * 1024 * 1024
    }
}
