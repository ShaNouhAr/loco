package org.loco.agent

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/** Applications demandées par loco : APK vérifié puis installé par Android (confirmation sur l'appareil pour une
 *  première installation), ou fiche Google Play ouverte sur l'appareil. Rien ne s'installe sans commande du serveur. */
class Apps(private val context: Context, private val store: Store) {
    private class Refused(message: String) : Exception(message)

    /** Commande « install-apk » : chaque application dans l'ordre, l'avancement puis le résultat envoyés à loco. */
    fun run(cmd: JSONObject) {
        val id = cmd.getLong("id")
        val all = cmd.optJSONArray("apps") ?: JSONArray()
        // Installations d'abord, fiches Google Play ensuite : leurs fenêtres ne se superposent pas.
        val items = (0 until all.length()).map { all.getJSONObject(it) }.sortedBy { it.optString("store") == "play" }
        val lines = ArrayList<String>()
        var failed = false
        for ((i, item) in items.withIndex()) {
            val name = item.optString("name", item.optString("package"))
            lines.add(try {
                if (item.optString("store") == "play") openStore(item.getString("package"), name)
                else install(id, item, name, i + 1, items.size)
            } catch (e: Exception) {
                failed = true
                context.getString(R.string.apps_failed, name, e.message ?: e.javaClass.simpleName)
            })
        }
        report(id, if (failed) "failed" else "success", lines.joinToString("\n"), withInventory = true)
    }

    /** Action que l'application Android ne sait pas faire : le dire au lieu de laisser la commande expirer. */
    fun unsupported(cmd: JSONObject) = report(cmd.getLong("id"), "failed", context.getString(R.string.apps_unsupported))

    private fun install(cmdId: Long, item: JSONObject, name: String, index: Int, total: Int): String {
        val pkg = item.getString("package")
        val sha = item.getString("sha256").lowercase()
        val size = item.getLong("size")
        val file = File(File(context.cacheDir, "apk").apply { mkdirs() }, "$sha.apk")
        try {
            progress(cmdId, "download", name, index, total, 0)
            var shown = 0
            val got = Net.download(store.server + item.getString("url"), file, store.caPem, store.token, size) { done ->
                val percent = (done * 100 / maxOf(size, 1)).toInt()
                if (percent >= shown + 10) { shown = percent; progress(cmdId, "download", name, index, total, percent) }
            }
            progress(cmdId, "verify", name, index, total)
            if (got != sha || file.length() != size) refuse(R.string.apps_bad_hash)
            val archive = archiveInfo(file) ?: refuse(R.string.apps_bad_apk)
            if (archive.packageName != pkg) refuse(R.string.apps_bad_package)
            val apkSigners = signers(archive)
            val expected = item.optString("signer").lowercase()
            if (expected.isNotEmpty() && apkSigners.isNotEmpty() && expected !in apkSigners) refuse(R.string.apps_bad_signer)
            installedInfo(pkg)?.let { current ->
                val have = signers(current)
                if (have.isNotEmpty() && apkSigners.isNotEmpty() && have.intersect(apkSigners).isEmpty()) {
                    refuse(R.string.apps_other_signer, installerOf(pkg))
                }
            }
            // Sans l'autorisation « installer des applications », Android 14 annule l'installation quand on l'accorde
            // depuis sa propre fenêtre : la demander d'abord, puis attendre qu'elle soit accordée.
            if (!context.packageManager.canRequestPackageInstalls()) {
                progress(cmdId, "confirm", name, index, total)
                if (!awaitInstallPermission(name)) refuse(R.string.apps_not_allowed)
            }
            progress(cmdId, "install", name, index, total)
            val result = commit(file, pkg, name) { progress(cmdId, "confirm", name, index, total) }
                ?: refuse(R.string.apps_not_confirmed)
            if (result.status != PackageInstaller.STATUS_SUCCESS) throw Refused(Installs.describe(context, result))
            return context.getString(R.string.apps_installed, name, item.optString("version"))
        } finally {
            file.delete()
        }
    }

    /** Écrit l'APK dans une session d'installation et attend la réponse d'Android (null : pas de réponse à temps). */
    private fun commit(file: File, pkg: String, name: String, onConfirm: () -> Unit): Installs.Result? {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(pkg)
        params.setSize(file.length())
        params.setInstallReason(PackageManager.INSTALL_REASON_USER)
        // Android 12+ : une mise à jour d'une application que loco a installée se fait sans rien demander.
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        val session = installer.createSession(params)
        val waiter = Installs.expect(session, name, onConfirm)
        try {
            installer.openSession(session).use { s ->
                file.inputStream().use { input ->
                    s.openWrite("base.apk", 0, file.length()).use { out ->
                        input.copyTo(out, 1 shl 16)
                        s.fsync(out)
                    }
                }
                s.commit(InstallReceiver.sender(context, session))
            }
            return try {
                waiter.get(WAIT_MINUTES, TimeUnit.MINUTES)
            } catch (_: TimeoutException) {
                null
            }
        } finally {
            Installs.forget(context, session)
        }
    }

    /** Demande l'autorisation d'installer (notification, et carte dans l'application) ; vrai dès qu'elle est accordée. */
    private fun awaitInstallPermission(name: String): Boolean {
        val settings = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        Installs.waitingPermission = name
        Installs.show(context, PERMISSION_NOTIFICATION, context.getString(R.string.apps_allow_title),
            context.getString(R.string.apps_allow_text, name), settings, "allow")
        try {
            val end = System.currentTimeMillis() + WAIT_MINUTES * 60_000
            while (System.currentTimeMillis() < end) {
                if (context.packageManager.canRequestPackageInstalls()) return true
                Thread.sleep(1500)
            }
            return false
        } finally {
            Installs.waitingPermission = null
            Installs.cancel(context, "allow", PERMISSION_NOTIFICATION)
        }
    }

    /** Fiche de l'application dans le Play Store (ou la boutique de l'appareil) : la personne touche « Installer ». */
    private fun openStore(pkg: String, name: String): String {
        val pm = context.packageManager
        val market = Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$pkg")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val play = Intent(market).setPackage(PLAY_STORE)
        val (intent, line) = when {
            play.resolveActivity(pm) != null -> play to context.getString(R.string.apps_play_opened, name)
            market.resolveActivity(pm) != null -> market to context.getString(R.string.apps_store_opened, name)
            else -> Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$pkg"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) to context.getString(R.string.apps_play_web, name)
        }
        Installs.show(context, pkg.hashCode(), context.getString(R.string.apps_play_title, name),
            context.getString(R.string.apps_play_text), intent, "store")
        return line
    }

    /** Mise à jour de l'application loco elle-même, proposée par le serveur : version plus récente, même clé. */
    fun selfUpdate(info: JSONObject) {
        val sha = info.optString("sha256").lowercase()
        if (sha.length != 64 || !newer(info.optString("version"), BuildConfig.VERSION_NAME)) return
        if (store.updateTried == sha && System.currentTimeMillis() - store.updateTriedAt < 86_400_000L) return
        store.updateTried = sha
        store.updateTriedAt = System.currentTimeMillis()
        val file = File(File(context.cacheDir, "apk").apply { mkdirs() }, "loco-$sha.apk")
        try {
            val got = Net.download("${store.server}/agent/files/loco-agent.apk", file, store.caPem, null, -1)
            val archive = archiveInfo(file) ?: return
            val self = context.packageManager.getPackageInfo(context.packageName, 0)
            if (got != sha || archive.packageName != context.packageName || code(archive) <= code(self)) return
            // Pas d'attente : Android arrête l'application pour la remplacer ; elle repart seule (MY_PACKAGE_REPLACED).
            val installer = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            params.setAppPackageName(context.packageName)
            params.setSize(file.length())
            if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            val session = installer.createSession(params)
            Installs.name(session, "loco")
            installer.openSession(session).use { s ->
                file.inputStream().use { input ->
                    s.openWrite("base.apk", 0, file.length()).use { out -> input.copyTo(out, 1 shl 16); s.fsync(out) }
                }
                s.commit(InstallReceiver.sender(context, session))
            }
        } catch (_: Exception) {
        } finally {
            file.delete()
        }
    }

    // --- Vérifications -------------------------------------------------------
    @Suppress("DEPRECATION")
    private val signFlags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES

    private fun archiveInfo(file: File): PackageInfo? = context.packageManager.getPackageArchiveInfo(file.path, signFlags)

    private fun installedInfo(pkg: String): PackageInfo? = try {
        context.packageManager.getPackageInfo(pkg, signFlags)
    } catch (_: PackageManager.NameNotFoundException) {
        null
    }

    /** Empreintes SHA-256 des certificats qui signent le paquet (rotation de clé comprise). */
    @Suppress("DEPRECATION")
    private fun signers(info: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= 28) {
            info.signingInfo?.let { if (it.hasMultipleSigners()) it.apkContentsSigners else it.signingCertificateHistory }
        } else info.signatures
        return signatures?.map { sha256(it.toByteArray()) }?.toSet() ?: emptySet()
    }

    private fun installerOf(pkg: String): String {
        val name = try {
            if (Build.VERSION.SDK_INT >= 30) context.packageManager.getInstallSourceInfo(pkg).installingPackageName
            else @Suppress("DEPRECATION") context.packageManager.getInstallerPackageName(pkg)
        } catch (_: Exception) { null }
        return if (name == PLAY_STORE) "Google Play" else name ?: "?"
    }

    private fun refuse(res: Int, vararg args: Any): Nothing = throw Refused(context.getString(res, *args))

    // --- Échanges avec loco ---------------------------------------------------
    private fun progress(id: Long, step: String, name: String, index: Int, total: Int, percent: Int? = null) {
        val p = JSONObject().put("step", step).put("app", name).put("index", index).put("total", total)
        percent?.let { p.put("percent", it.coerceIn(0, 100)) }
        report(id, "running", "", p)
    }

    fun report(id: Long, state: String, output: String, progress: JSONObject? = null, withInventory: Boolean = false) {
        val body = JSONObject().put("id", id).put("state", state).put("output", output)
        if (state != "running") body.put("exit_code", if (state == "success") 0 else 1)
        progress?.let { body.put("progress", it) }
        if (withInventory) body.put("inventory", Inventory.full(context).getJSONObject("inventory"))
        // Le résultat final compte : quelques essais si le réseau hoquette (l'avancement, lui, peut se perdre).
        repeat(if (state == "running") 1 else 4) { attempt ->
            try {
                Net.postJson("${store.server}/agent/api/result", body.toString(), store.caPem, store.token)
                return
            } catch (e: Net.HttpError) {
                if (e.code in 400..499) return
            } catch (_: Exception) {
            }
            if (attempt < 3) try { Thread.sleep(5000L * (attempt + 1)) } catch (_: InterruptedException) {}
        }
    }

    companion object {
        const val PLAY_STORE = "com.android.vending"
        private const val PERMISSION_NOTIFICATION = 7101
        private const val WAIT_MINUTES = 330L          // le serveur abandonne une commande au bout de six heures

        fun sha256(data: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

        @Suppress("DEPRECATION")
        fun code(info: PackageInfo): Long = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()

        /** a plus récent que b (versions « 2.139.0 ») ? */
        fun newer(a: String, b: String): Boolean {
            fun parts(v: String) = v.split('.', '-').map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0)
            val x = parts(a); val y = parts(b)
            for (i in 0 until 3) if (x[i] != y[i]) return x[i] > y[i]
            return false
        }
    }
}
