package org.loco.agent

import android.content.Context
import org.json.JSONObject
import java.io.RandomAccessFile

/** Configuration gravée dans l'APK par loco (route /agent/android/enroll.apk) : adresse du serveur, clé
 *  d'inscription et empreinte de l'autorité. Permet d'inscrire l'appareil sans rien saisir. */
class Provision(val server: String, val key: String, val ca: String)

/** Lit la configuration que loco a gravée dans le « bloc de signature » de notre propre APK, sans toucher à sa
 *  signature (voir portal/loco/apksign.py). Renvoie null pour l'APK générique (aucune paire loco) ou si l'APK est
 *  illisible : l'application demande alors l'adresse et la clé à la personne. */
object Provisioning {
    private const val LOCO_ID = 0x6C6F636FL                       // « loco »
    private val MAGIC = "APK Sig Block 42".toByteArray(Charsets.US_ASCII)

    fun read(context: Context): Provision? = try {
        val value = RandomAccessFile(context.applicationInfo.sourceDir, "r").use { locoBlock(it) }
        value?.let {
            val o = JSONObject(String(it, Charsets.UTF_8))
            val server = o.optString("server").trimEnd('/')
            val key = o.optString("key")
            if (server.startsWith("https://") && key.isNotEmpty()) Provision(server, key, o.optString("ca")) else null
        }
    } catch (_: Exception) { null }

    /** Valeur de la paire loco du bloc de signature de l'APK, ou null. Même lecture que apksign.py, à l'envers. */
    private fun locoBlock(f: RandomAccessFile): ByteArray? {
        val len = f.length()
        // EOCD : signature « PK\x05\x06 » cherchée depuis la fin (nos APK n'ont pas de commentaire ZIP).
        val scan = minOf(len, 22L + 0xFFFF).toInt()
        val tail = ByteArray(scan)
        f.seek(len - scan); f.readFully(tail)
        var eocd = tail.size - 22
        while (eocd >= 0 && !(tail[eocd] == 0x50.toByte() && tail[eocd + 1] == 0x4b.toByte() &&
                    tail[eocd + 2] == 0x05.toByte() && tail[eocd + 3] == 0x06.toByte())) eocd--
        if (eocd < 0) return null
        val cdOff = u32(tail, eocd + 16)
        if (cdOff < 24 || cdOff > len) return null
        val magic = ByteArray(16); f.seek(cdOff - 16); f.readFully(magic)
        if (!magic.contentEquals(MAGIC)) return null
        val sizeBuf = ByteArray(8); f.seek(cdOff - 24); f.readFully(sizeBuf)
        val blockStart = cdOff - 8 - u64(sizeBuf, 0)
        if (blockStart < 0) return null
        var pos = blockStart + 8
        val end = cdOff - 24
        while (pos < end) {
            val lb = ByteArray(8); f.seek(pos); f.readFully(lb)
            val length = u64(lb, 0)
            if (length < 4 || pos + 8 + length > end) return null
            val idb = ByteArray(4); f.seek(pos + 8); f.readFully(idb)
            if (u32(idb, 0) == LOCO_ID) {
                val value = ByteArray((length - 4).toInt()); f.seek(pos + 12); f.readFully(value)
                return value
            }
            pos += 8 + length
        }
        return null
    }

    private fun u32(b: ByteArray, o: Int): Long =
        (b[o].toLong() and 0xFF) or ((b[o + 1].toLong() and 0xFF) shl 8) or
        ((b[o + 2].toLong() and 0xFF) shl 16) or ((b[o + 3].toLong() and 0xFF) shl 24)

    private fun u64(b: ByteArray, o: Int): Long {
        var v = 0L; for (k in 7 downTo 0) v = (v shl 8) or (b[o + k].toLong() and 0xFF); return v
    }
}
