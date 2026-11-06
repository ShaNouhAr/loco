package org.loco.agent

import android.content.Context
import android.os.Build
import android.os.Environment
import android.os.storage.StorageManager
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile

/**
 * Gestionnaire de fichiers du portail : même protocole que l'agent Linux (trames [u32 en-tête][u32 données] + JSON
 * + binaire), exécuté avec les droits de l'application. Le stockage partagé demande l'« accès à tous les fichiers »,
 * accordé par la personne dans l'appli ; sans lui, les erreurs le disent.
 */
class FileSession(private val context: Context) {
    private val uploads = HashMap<String, Pair<RandomAccessFile, File>>()

    fun serve(input: Pipe, output: Pipe) {
        try {
            while (true) {
                val prefix = try { input.read(8) } catch (_: EOFException) { return }
                val hlen = u32(prefix, 0)
                val dlen = u32(prefix, 4)
                if (hlen > HEADER_MAX || dlen > DATA_MAX) throw IOException("trame trop grande")
                val header = JSONObject(String(input.read(hlen.toInt()), Charsets.UTF_8))
                val data = if (dlen > 0) input.read(dlen.toInt()) else ByteArray(0)
                val id = header.opt("id")
                var payload = ByteArray(0)
                val reply = try {
                    val (r, p) = handle(header, data)
                    payload = p
                    r.put("id", id).put("ok", true)
                } catch (e: Exception) {
                    JSONObject().put("id", id).put("ok", false).put("error", message(e)).put("errno", errno(e) ?: JSONObject.NULL)
                }
                frame(output, reply, payload)
            }
        } finally {
            cleanup()
        }
    }

    private fun frame(output: Pipe, header: JSONObject, data: ByteArray) {
        val blob = header.toString().toByteArray(Charsets.UTF_8)
        val o = ByteArrayOutputStream(8 + blob.size + data.size)
        for (n in intArrayOf(blob.size, data.size)) {
            o.write((n ushr 24) and 0xff); o.write((n ushr 16) and 0xff); o.write((n ushr 8) and 0xff); o.write(n and 0xff)
        }
        o.write(blob)
        o.write(data)
        output.write(o.toByteArray())
    }

    private fun cleanup() {
        for ((handle, tmp) in uploads.values) {
            try { handle.close() } catch (_: IOException) {}
            tmp.delete()
        }
        uploads.clear()
    }

    private fun handle(h: JSONObject, data: ByteArray): Pair<JSONObject, ByteArray> = when (h.optString("op")) {
        "hello" -> hello()
        "list" -> list(path(h))
        "stat" -> stat(path(h))
        "mkdir" -> { Os.mkdir(path(h).path, "777".toInt(8)); JSONObject() to EMPTY }
        "rename" -> rename(path(h), File(h.getString("name")).name)
        "move" -> transfer(path(h), File(h.getString("to")).absoluteFile.normalize(), keep = false)
        "copy" -> transfer(path(h), File(h.getString("to")).absoluteFile.normalize(), keep = true)
        "delete" -> delete(path(h))
        "chmod" -> { Os.chmod(path(h).path, h.getInt("mode") and "7777".toInt(8)); JSONObject() to EMPTY }
        "read" -> read(path(h), h.optLong("offset", 0), h.optLong("length", CHUNK.toLong()))
        "write" -> write(path(h), h.optLong("offset", 0), data, h.optBoolean("final"))
        "write_cancel" -> { uploads.remove(path(h).path)?.let { it.first.close(); it.second.delete() }; JSONObject() to EMPTY }
        else -> throw IOException("opération inconnue")
    }

    private fun path(h: JSONObject, key: String = "path") = File(h.getString(key)).absoluteFile.normalize()

    // --- inspection ---------------------------------------------------------
    private fun hello(): Pair<JSONObject, ByteArray> {
        val home = Environment.getExternalStorageDirectory()
        val roots = JSONArray()
        val sm = context.getSystemService(StorageManager::class.java)
        for (v in sm.storageVolumes) {
            val dir = if (Build.VERSION.SDK_INT >= 30) v.directory else if (v.isPrimary) home else null
            if (dir != null) roots.put(JSONObject().put("name", v.getDescription(context)).put("path", dir.path))
        }
        if (roots.length() == 0) roots.put(JSONObject().put("name", context.getString(R.string.files_internal)).put("path", home.path))
        return JSONObject().put("os", "android").put("sep", "/").put("home", home.path).put("user", "")
            .put("host", Inventory.deviceName(context)).put("roots", roots) to EMPTY
    }

    private fun entry(name: String, f: File): JSONObject {
        var type = "other"; var size = 0L; var mtime = 0L; var mode = 0; var link = false
        try { link = OsConstants.S_ISLNK(Os.lstat(f.path).st_mode) } catch (_: ErrnoException) {}
        try {
            val st = Os.stat(f.path)                       // suit le lien : un lien vers un dossier se parcourt
            mode = st.st_mode; size = st.st_size; mtime = st.st_mtime
            type = if (OsConstants.S_ISDIR(mode)) "dir" else if (OsConstants.S_ISREG(mode)) "file" else "other"
        } catch (_: ErrnoException) {
            try { val st = Os.lstat(f.path); mode = st.st_mode; size = st.st_size; mtime = st.st_mtime } catch (_: ErrnoException) {}
        }
        return JSONObject().put("name", name).put("type", type).put("size", size).put("mtime", mtime)
            .put("mode", mode and "7777".toInt(8)).put("link", link)
    }

    private fun list(dir: File): Pair<JSONObject, ByteArray> {
        val names = dir.list() ?: throw denied(dir)
        names.sort()
        val entries = names.take(LIST_MAX).map { entry(it, File(dir, it)) }
            .sortedWith(compareBy<JSONObject>({ it.getString("type") != "dir" }, { it.getString("name").lowercase() }))
        return JSONObject().put("path", dir.path).put("parent", dir.parent ?: JSONObject.NULL)
            .put("entries", JSONArray(entries)).put("truncated", names.size > LIST_MAX) to EMPTY
    }

    private fun stat(f: File): Pair<JSONObject, ByteArray> {
        val exists = try { Os.lstat(f.path); true } catch (_: ErrnoException) { false }
        val reply = if (exists) entry(f.name, f) else JSONObject()
        return reply.put("path", f.path).put("exists", exists) to EMPTY
    }

    // --- modifications -------------------------------------------------------
    private fun rename(f: File, name: String): Pair<JSONObject, ByteArray> {
        val dest = File(f.parentFile, name)
        if (exists(dest)) throw IOException(context.getString(R.string.files_name_taken))
        Os.rename(f.path, dest.path)
        return JSONObject().put("path", dest.path) to EMPTY
    }

    private fun transfer(f: File, to: File, keep: Boolean): Pair<JSONObject, ByteArray> {
        val dest = File(to, f.name)
        if (exists(dest)) throw IOException(context.getString(R.string.files_name_taken_dest))
        if (!keep) {
            try { Os.rename(f.path, dest.path); return JSONObject().put("path", dest.path) to EMPTY }
            catch (e: ErrnoException) { if (e.errno != OsConstants.EXDEV) throw e }      // autre volume : copie puis suppression
        }
        if (!f.copyRecursively(dest, overwrite = false)) throw IOException(context.getString(R.string.files_copy_failed))
        if (!keep && !f.deleteRecursively()) throw IOException(context.getString(R.string.files_delete_failed))
        return JSONObject().put("path", dest.path) to EMPTY
    }

    private fun delete(f: File): Pair<JSONObject, ByteArray> {
        val isDir = f.isDirectory && !OsConstants.S_ISLNK(try { Os.lstat(f.path).st_mode } catch (_: ErrnoException) { 0 })
        val ok = if (isDir) f.deleteRecursively() else f.delete()
        if (!ok) throw denied(f)
        return JSONObject() to EMPTY
    }

    private fun exists(f: File) = try { Os.lstat(f.path); true } catch (_: ErrnoException) { false }

    // --- transferts ----------------------------------------------------------
    private fun read(f: File, offset: Long, length: Long): Pair<JSONObject, ByteArray> {
        val start = maxOf(0L, offset)
        val n = length.coerceIn(0L, CHUNK.toLong()).toInt()
        RandomAccessFile(f, "r").use { raf ->
            val size = raf.length()
            raf.seek(start)
            val buf = ByteArray(minOf(n.toLong(), maxOf(0L, size - start)).toInt())
            raf.readFully(buf)
            return JSONObject().put("offset", start).put("size", size).put("eof", start + buf.size >= size) to buf
        }
    }

    private fun write(f: File, offset: Long, data: ByteArray, final: Boolean): Pair<JSONObject, ByteArray> {
        var entry = uploads[f.path]
        if (offset == 0L) {
            entry?.first?.close()
            val tmp = File(f.path + ".loco-part")
            entry = RandomAccessFile(tmp, "rw").apply { setLength(0) } to tmp
            uploads[f.path] = entry
        } else if (entry == null) {
            throw IOException(context.getString(R.string.files_upload_no_start))
        }
        val (raf, tmp) = entry
        raf.seek(offset)
        raf.write(data)
        if (final) {
            raf.fd.sync()
            raf.close()
            Os.rename(tmp.path, f.path)                    // remplacement atomique
            uploads.remove(f.path)
        }
        return JSONObject().put("offset", offset).put("written", data.size).put("final", final) to EMPTY
    }

    // --- erreurs lisibles ------------------------------------------------------
    private fun denied(f: File): IOException {
        val shared = f.path.startsWith("/storage/") || f.path.startsWith("/sdcard")
        return IOException(context.getString(if (shared && !StorageAccess.granted(context)) R.string.files_need_access
                                              else R.string.files_denied))
    }

    private fun errno(e: Exception): Int? = (e as? ErrnoException)?.errno ?: (e.cause as? ErrnoException)?.errno

    private fun message(e: Exception): String {
        val code = errno(e)
        val text = when (code) {
            OsConstants.EACCES, OsConstants.EPERM -> context.getString(
                if (!StorageAccess.granted(context)) R.string.files_need_access else R.string.files_denied)
            OsConstants.ENOENT -> context.getString(R.string.files_not_found)
            OsConstants.EEXIST -> context.getString(R.string.files_name_taken)
            OsConstants.ENOTEMPTY -> context.getString(R.string.files_not_empty)
            OsConstants.ENOSPC -> context.getString(R.string.files_no_space)
            else -> e.message ?: e.javaClass.simpleName
        }
        return text.take(500)
    }

    private fun u32(b: ByteArray, off: Int): Long =
        ((b[off].toLong() and 0xff) shl 24) or ((b[off + 1].toLong() and 0xff) shl 16) or
            ((b[off + 2].toLong() and 0xff) shl 8) or (b[off + 3].toLong() and 0xff)

    private companion object {
        const val HEADER_MAX = 64 * 1024L
        const val DATA_MAX = 1024 * 1024L
        const val CHUNK = 256 * 1024
        const val LIST_MAX = 5000
        val EMPTY = ByteArray(0)
    }
}
