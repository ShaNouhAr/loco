package org.loco.agent

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** Échanges HTTPS avec loco. L'autorité (CA) est épinglée : après l'inscription, aucun autre certificat n'est accepté. */
object Net {
    class HttpError(val code: Int, val body: String) : Exception("HTTP $code")

    /** Autorité (CA) de loco, servie en clair par /agent/ca.pem. nginx ne présente que le certificat serveur à la
     *  poignée de main : l'autorité est donc récupérée ici, SANS valider la connexion ; sa confiance repose ensuite
     *  sur la confirmation de son empreinte par la personne, comparée à celle affichée par le portail. */
    fun fetchCaPem(server: String): String {
        val trustAll = object : X509TrustManager {
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) {}
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(trustAll), SecureRandom())
        val conn = URL("$server/agent/ca.pem").openConnection() as HttpsURLConnection
        conn.sslSocketFactory = ctx.socketFactory
        conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
        conn.connectTimeout = 15000; conn.readTimeout = 15000
        val out = ByteArrayOutputStream()
        conn.inputStream.use { it.copyTo(out) }
        conn.disconnect()
        return out.toString("UTF-8")
    }

    private fun certs(pem: String): List<X509Certificate> =
        CertificateFactory.getInstance("X.509").generateCertificates(pem.byteInputStream())
            .filterIsInstance<X509Certificate>()

    /** Empreinte du premier certificat du faisceau = autorité loco, comme l'affiche le portail. */
    fun fingerprintOfFirst(pem: String): String {
        val first = certs(pem).firstOrNull() ?: throw IllegalArgumentException("aucun certificat")
        return MessageDigest.getInstance("SHA-256").digest(first.encoded).joinToString("") { "%02x".format(it) }
    }

    private fun contextFor(caPem: String): SSLContext {
        val ks = KeyStore.getInstance(KeyStore.getDefaultType())
        ks.load(null, null)
        certs(caPem).forEachIndexed { i, c -> ks.setCertificateEntry("loco$i", c) }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(ks)
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, tmf.trustManagers, SecureRandom())
        return ctx
    }

    /** Fabrique de connexions pour l'autorité épinglée, gardée d'un appel à l'autre. Une fabrique neuve à chaque requête
     *  empêchait Android de réutiliser la connexion : chaque échange du bureau distant (des dizaines par seconde)
     *  refaisait une poignée de main TLS complète, ce qui coûte cher sur un vrai réseau. */
    @Volatile private var pinned: Pair<String, SSLSocketFactory>? = null

    /** Même fabrique pour le canal persistant (RelaySocket) : même autorité épinglée que les échanges HTTPS. */
    fun socketFactory(caPem: String): SSLSocketFactory = factoryFor(caPem)

    private fun factoryFor(caPem: String): SSLSocketFactory {
        pinned?.let { if (it.first == caPem) return it.second }
        return contextFor(caPem).socketFactory.also { pinned = caPem to it }
    }

    /** Télécharge url dans dest (autorité épinglée), sans dépasser la taille attendue ; renvoie l'empreinte SHA-256.
     *  expected < 0 : taille inconnue, plafonnée à MAX_DOWNLOAD. */
    fun download(url: String, dest: File, caPem: String, bearer: String?, expected: Long, onProgress: (Long) -> Unit = {}): String {
        val conn = URL(url).openConnection() as HttpsURLConnection
        conn.sslSocketFactory = factoryFor(caPem)
        bearer?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        conn.connectTimeout = 30000; conn.readTimeout = 120000
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw HttpError(code, "")
            val limit = if (expected > 0) expected else MAX_DOWNLOAD
            val digest = MessageDigest.getInstance("SHA-256")
            var done = 0L
            conn.inputStream.use { input ->
                dest.outputStream().use { out ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        done += n
                        if (done > limit) throw IOException("fichier plus gros qu'annoncé")
                        digest.update(buf, 0, n)
                        out.write(buf, 0, n)
                        onProgress(done)
                    }
                }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        } finally {
            conn.disconnect()
        }
    }

    private const val MAX_DOWNLOAD = 256L * 1024 * 1024

    fun postJson(url: String, body: String, caPem: String, bearer: String? = null, timeoutMs: Int = 30000): String {
        val conn = URL(url).openConnection() as HttpsURLConnection
        conn.sslSocketFactory = factoryFor(caPem)
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json")
        bearer?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        conn.doOutput = true
        conn.connectTimeout = timeoutMs; conn.readTimeout = timeoutMs
        val payload = body.toByteArray(Charsets.UTF_8)
        conn.setFixedLengthStreamingMode(payload.size)         // envoi direct, sans copie tampon du corps
        val out = ByteArrayOutputStream()
        val code: Int
        try {
            conn.outputStream.use { it.write(payload) }
            code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else (conn.errorStream ?: conn.inputStream)
            // Réponse lue jusqu'au bout puis fermée : la connexion retourne dans la réserve et sert à l'échange suivant.
            stream.use { it.copyTo(out) }
        } catch (e: IOException) {
            conn.disconnect()                                   // connexion en mauvais état : ne pas la réutiliser
            throw e
        }
        val text = out.toString("UTF-8")
        if (code !in 200..299) throw HttpError(code, text)
        return text
    }
}
