package org.loco.agent

import android.content.Context
import android.os.Environment
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.File
import java.io.IOException
import java.io.OutputStream

/**
 * Terminal distant d'Android, avec les droits de l'application (pas de root). Android ne donne pas de
 * pseudo-terminal aux applications : chaque ligne est exécutée par `sh -c` dans le dossier courant, et l'édition de
 * ligne est faite ici (écho, effacement, Ctrl+C, Ctrl+U, Ctrl+L, historique). `cd` est conservé d'une commande à
 * l'autre, pas les variables. Les programmes plein écran (vi, top interactif) ne fonctionnent pas.
 */
class Terminal(private val context: Context, private val cols: Int, private val rows: Int) {
    private lateinit var out: Pipe
    private val storage = Environment.getExternalStorageDirectory()
    private var cwd: File = if (StorageAccess.granted(context) && storage.canRead()) storage else context.filesDir
    private val line = StringBuilder()
    private val history = ArrayList<String>()
    private var historyAt = 0
    private val utf8 = ByteArrayOutputStream()
    private var utf8Left = 0
    private var escape = 0                 // 0 : aucune ; 1 : ESC reçu ; 2 : séquence CSI/SS3 en cours
    private val escBuf = StringBuilder()
    private var lastCr = false
    @Volatile private var running: Process? = null
    @Volatile private var stdin: OutputStream? = null
    private var ended = false

    fun serve(input: Pipe, output: Pipe) {
        out = output
        say("\u001b[2mloco · terminal Android, avec les droits de l'application (sans root). `cd` est conservé, pas les variables.\u001b[0m\r\n")
        prompt()
        try {
            while (!ended) feed(input.readByte())
        } catch (_: EOFException) {
        } finally {
            running?.destroy()
        }
    }

    private fun say(text: String) = out.write(text.toByteArray(Charsets.UTF_8))

    private fun prompt() {
        val home = storage.path
        val where = if (cwd.path == home || cwd.path.startsWith("$home/")) "~" + cwd.path.removePrefix(home) else cwd.path
        say("\u001b[1;32mandroid\u001b[0m:\u001b[1;34m$where\u001b[0m$ ")
    }

    private fun redraw() { say("\r\u001b[K"); prompt(); say(line.toString()) }

    // --- clavier ---------------------------------------------------------------
    private fun feed(b: Int) {
        if (escape == 1) { escape = if (b == '['.code || b == 'O'.code) 2 else 0; escBuf.setLength(0); return }
        if (escape == 2) {
            if (b in 0x40..0x7e) { escape = 0; arrow(b.toChar()) } else escBuf.append(b.toChar())
            return
        }
        if (utf8Left > 0) {
            utf8.write(b); utf8Left--
            if (utf8Left == 0) typed(String(utf8.toByteArray(), Charsets.UTF_8))
            return
        }
        val cr = lastCr
        lastCr = b == 0x0d
        when {
            b == 0x1b -> escape = 1
            b == 0x0d || (b == 0x0a && !cr) -> enter()
            b == 0x0a -> {}                                             // \n qui suit \r : même Entrée
            b == 0x7f || b == 0x08 -> backspace()
            b == 0x03 -> interrupt()
            b == 0x04 -> if (running != null) closeInput() else if (line.isEmpty()) { say("exit\r\n"); ended = true }
            b == 0x15 -> { line.setLength(0); redraw() }                // Ctrl+U
            b == 0x0c -> { say("\u001b[H\u001b[2J"); prompt(); say(line.toString()) }   // Ctrl+L
            b >= 0xc0 -> { utf8.reset(); utf8.write(b); utf8Left = if (b >= 0xf0) 3 else if (b >= 0xe0) 2 else 1 }
            b >= 0x20 && b < 0x7f -> typed(b.toChar().toString())
        }
    }

    private fun typed(s: String) { line.append(s); say(s) }

    private fun backspace() {
        if (line.isEmpty()) return
        val cut = line.offsetByCodePoints(line.length, -1)
        line.setLength(cut)
        say("\b \b")
    }

    private fun arrow(c: Char) {
        if (running != null || history.isEmpty()) return
        when (c) {
            'A' -> if (historyAt > 0) historyAt--                      // flèche haut
            'B' -> if (historyAt < history.size) historyAt++           // flèche bas
            else -> return
        }
        line.setLength(0)
        if (historyAt < history.size) line.append(history[historyAt])
        redraw()
    }

    private fun enter() {
        say("\r\n")
        val text = line.toString()
        line.setLength(0)
        val p = running
        if (p != null) {                                                // saisie destinée à la commande en cours
            try { stdin?.write((text + "\n").toByteArray()); stdin?.flush() } catch (_: IOException) {}
            return
        }
        if (text.isNotBlank() && history.lastOrNull() != text) history.add(text)
        historyAt = history.size
        execute(text.trim())
    }

    private fun interrupt() {
        say("^C\r\n")
        val p = running
        if (p != null) { p.destroy(); return }                          // l'invite revient à la fin de la commande
        line.setLength(0)
        prompt()
    }

    private fun closeInput() { try { stdin?.close() } catch (_: IOException) {} }

    // --- commandes ---------------------------------------------------------------
    private fun execute(cmd: String) {
        when {
            cmd.isEmpty() -> prompt()
            cmd == "exit" -> ended = true
            cmd == "clear" -> { say("\u001b[H\u001b[2J"); prompt() }
            CD.matches(cmd) -> { changeDir(CD.find(cmd)!!.groupValues[1].trim()); prompt() }
            else -> run(cmd)
        }
    }

    private fun changeDir(arg: String) {
        val target = when {
            arg.isEmpty() || arg == "~" -> storage
            arg.startsWith("~/") -> File(storage, arg.substring(2))
            arg.startsWith("/") -> File(arg)
            else -> File(cwd, arg)
        }.absoluteFile.normalize()
        when {
            !target.isDirectory -> say("cd: $arg: dossier introuvable\r\n")
            !target.canExecute() -> say("cd: $arg: accès refusé\r\n")
            else -> cwd = target
        }
    }

    private fun run(cmd: String) {
        val pb = ProcessBuilder("/system/bin/sh", "-c", cmd).directory(cwd).redirectErrorStream(true)
        pb.environment().apply {
            put("HOME", storage.path); put("PWD", cwd.path); put("TERM", "dumb")
            put("COLUMNS", cols.toString()); put("LINES", rows.toString())
        }
        val p = try { pb.start() } catch (e: IOException) { say("sh: ${e.message}\r\n"); prompt(); return }
        running = p
        stdin = p.outputStream
        Thread({
            var last = '\n'.code
            try {
                val buf = ByteArray(8192)
                val src = p.inputStream
                while (true) {
                    val n = src.read(buf)
                    if (n < 0) break
                    val o = ByteArrayOutputStream(n + 64)
                    for (i in 0 until n) {
                        val c = buf[i].toInt() and 0xff
                        if (c == 0x0a && last != 0x0d) o.write(0x0d)           // pas de pseudo-terminal : \n -> \r\n
                        o.write(c)
                        last = c
                    }
                    out.write(o.toByteArray())
                }
                p.waitFor()
            } catch (_: Exception) {
            }
            if (last != '\n'.code) say("\r\n")
            running = null
            stdin = null
            if (!ended) prompt()
        }, "loco-terminal").apply { isDaemon = true }.start()
    }

    private companion object {
        val CD = Regex("^cd(?:\\s+([^;&|<>`$]*))?$")
    }
}
