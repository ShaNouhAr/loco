package org.loco.agent

import java.io.EOFException

/**
 * Tampon d'octets bloquant entre le relais et une session (RFB, fichiers, terminal). Les blocs écrits sont gardés
 * tels quels (sans copie) : l'appelant ne doit plus les modifier après [write].
 */
class Pipe {
    private val chunks = ArrayDeque<ByteArray>()
    private var head = 0                 // position de lecture dans le premier bloc
    private var size = 0
    private var closed = false
    private val lock = Object()

    fun write(data: ByteArray) {
        if (data.isEmpty()) return
        synchronized(lock) {
            if (closed) return
            chunks.addLast(data)
            size += data.size
            lock.notifyAll()
        }
    }

    /** Sous verrou : copie jusqu'à n octets disponibles dans out, à partir de off. */
    private fun takeInto(out: ByteArray, off: Int, n: Int): Int {
        var copied = 0
        while (copied < n && chunks.isNotEmpty()) {
            val c = chunks.first()
            val k = minOf(c.size - head, n - copied)
            System.arraycopy(c, head, out, off + copied, k)
            copied += k
            head += k
            if (head == c.size) { chunks.removeFirst(); head = 0 }
        }
        size -= copied
        return copied
    }

    /** Lit exactement n octets (bloque jusqu'à les avoir) ; EOFException si le tampon est fermé avant. */
    fun read(n: Int): ByteArray {
        val out = ByteArray(n)
        synchronized(lock) {
            var got = 0
            while (got < n) {
                while (size == 0 && !closed) lock.wait()
                if (size == 0) throw EOFException()
                got += takeInto(out, got, n - got)
            }
        }
        return out
    }

    fun readByte(): Int = read(1)[0].toInt() and 0xff

    /** Bloque jusqu'à au moins un octet, puis rend ce qui est présent (jusqu'à max) ; vide = fermé. */
    fun drain(max: Int): ByteArray {
        synchronized(lock) {
            while (size == 0 && !closed) lock.wait()
            return takeAvailable(max)
        }
    }

    /** Rend ce qui est présent (jusqu'à max) sans bloquer ; vide si rien. */
    fun take(max: Int): ByteArray = synchronized(lock) { takeAvailable(max) }

    private fun takeAvailable(max: Int): ByteArray {
        val n = minOf(max, size)
        if (n == 0) return ByteArray(0)
        val out = ByteArray(n)
        takeInto(out, 0, n)
        return out
    }

    fun available(): Int = synchronized(lock) { size }
    val isClosed: Boolean get() = synchronized(lock) { closed }

    fun close() {
        synchronized(lock) { closed = true; lock.notifyAll() }
    }
}
