package org.loco.agent

import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageInstaller
import android.os.Build
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap

/** Réponses d'Android aux installations lancées par loco. Une confirmation demandée s'ouvre si Android le permet,
 *  et reste de toute façon proposée par une notification : rien ne s'installe sans que la personne touche « Installer ». */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val session = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
            Installs.confirm(context, session, confirm)
        } else {
            Installs.finish(context, session, status, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "")
        }
    }

    companion object {
        private const val ACTION = "org.loco.agent.INSTALL_STATUS"

        fun sender(context: Context, session: Int): IntentSender {
            val i = Intent(context, InstallReceiver::class.java).setAction(ACTION)
            // Modifiable : Android y ajoute l'état de l'installation. Intention explicite, vers ce seul récepteur.
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            return PendingIntent.getBroadcast(context, session, i, flags).intentSender
        }
    }
}

/** Installations en cours : qui attend quelle session, et les notifications de confirmation. */
object Installs {
    class Result(val status: Int, val message: String)

    private val waiting = ConcurrentHashMap<Int, CompletableFuture<Result>>()
    /** Confirmations en attente (session -> nom, fenêtre d'Android) : l'application les propose aussi, au premier plan. */
    val pending = ConcurrentHashMap<Int, Pair<String, Intent>>()
    /** Application qui attend l'autorisation « installer des applications », ou null. */
    @Volatile var waitingPermission: String? = null
    private val confirming = ConcurrentHashMap<Int, () -> Unit>()
    private val names = ConcurrentHashMap<Int, String>()
    private const val CHANNEL = "installs"
    private const val TAG = "install"

    fun expect(session: Int, name: String, onConfirm: () -> Unit): CompletableFuture<Result> {
        names[session] = name
        confirming[session] = onConfirm
        return CompletableFuture<Result>().also { waiting[session] = it }
    }

    fun name(session: Int, name: String) { names[session] = name }

    fun forget(context: Context, session: Int) {
        waiting.remove(session); confirming.remove(session); names.remove(session); pending.remove(session)
        manager(context).cancel(TAG, session)
    }

    fun confirm(context: Context, session: Int, intent: Intent) {
        val name = names[session] ?: "loco"
        pending[session] = name to Intent(intent).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        confirming[session]?.let { report -> Thread(report, "loco-confirm").start() }
        show(context, session, context.getString(R.string.apps_confirm_title, name), context.getString(R.string.apps_confirm_text),
            Intent(intent).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), TAG)
    }

    fun finish(context: Context, session: Int, status: Int, message: String) {
        pending.remove(session)
        manager(context).cancel(TAG, session)
        waiting[session]?.complete(Result(status, message))
    }

    fun describe(context: Context, result: Result): String = when (result.status) {
        PackageInstaller.STATUS_FAILURE_ABORTED -> context.getString(R.string.apps_aborted)
        PackageInstaller.STATUS_FAILURE_BLOCKED -> context.getString(R.string.apps_blocked)
        PackageInstaller.STATUS_FAILURE_CONFLICT -> context.getString(R.string.apps_conflict)
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> context.getString(R.string.apps_incompatible)
        PackageInstaller.STATUS_FAILURE_INVALID -> context.getString(R.string.apps_invalid)
        PackageInstaller.STATUS_FAILURE_STORAGE -> context.getString(R.string.apps_storage)
        else -> context.getString(R.string.apps_error, result.message.ifEmpty { result.status.toString() })
    }

    /** Ouvre la fenêtre si l'application est au premier plan ; sinon notification (Android y bloque l'ouverture directe,
     *  sauf service d'accessibilité actif : on tente quand même). Rien ne se perd : l'application la propose aussi. */
    fun show(context: Context, id: Int, title: String, text: String, intent: Intent, tag: String) {
        if (foreground()) {
            try { context.startActivity(intent); return } catch (_: Exception) {}
        }
        notify(context, id, title, text, intent, tag)
        try { context.startActivity(intent) } catch (_: Exception) {}
    }

    private fun foreground(): Boolean {
        val info = ActivityManager.RunningAppProcessInfo()
        ActivityManager.getMyMemoryState(info)
        return info.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
    }

    fun notify(context: Context, id: Int, title: String, text: String, intent: Intent, tag: String = "store") {
        val nm = manager(context)
        if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(CHANNEL, context.getString(R.string.channel_installs), NotificationManager.IMPORTANCE_HIGH)
            ch.description = context.getString(R.string.channel_installs_desc)
            nm.createNotificationChannel(ch)
        }
        val open = PendingIntent.getActivity(context, id, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(tag, id, Notification.Builder(context, CHANNEL)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSmallIcon(R.drawable.ic_stat_loco)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build())
    }

    fun cancel(context: Context, tag: String, id: Int) = manager(context).cancel(tag, id)

    private fun manager(context: Context) = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
}

/** Relance le service après un redémarrage de l'appareil ou une mise à jour de l'application (s'il est inscrit). */
class StartReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        if (Store(context).enrolled) LocoService.start(context)
    }
}
