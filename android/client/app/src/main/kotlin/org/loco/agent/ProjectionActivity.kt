package org.loco.agent

import android.app.Activity
import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Process

/**
 * Obtient le partage d'écran sans rien afficher, quand Android l'a autorisé une fois pour toutes à loco : ce réglage
 * (PROJECT_MEDIA) est posé par loco, par adb, quand la personne appaire son appareil. Android affiche toujours son
 * indicateur de partage pendant la session. Sans ce réglage, cette activité n'est jamais lancée : la fenêtre d'accord
 * d'Android ne surgit pas toute seule.
 */
class ProjectionActivity : Activity() {
    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        if (saved != null) return
        val manager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        try {
            val intent = if (Build.VERSION.SDK_INT >= 34) manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
            else manager.createScreenCaptureIntent()
            @Suppress("DEPRECATION")
            startActivityForResult(intent, 1)
        } catch (_: Exception) {
            finish()
        }
    }

    @Deprecated("startActivityForResult")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode == RESULT_OK && data != null) LocoService.startScreen(this, resultCode, data)
        finish()
    }

    companion object {
        /** Android laisse-t-il loco partager l'écran sans fenêtre d'accord ? */
        fun silent(context: Context): Boolean = try {
            val ops = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            val mode = if (Build.VERSION.SDK_INT >= 29) ops.unsafeCheckOpNoThrow("android:project_media", Process.myUid(), context.packageName)
            else @Suppress("DEPRECATION") ops.checkOpNoThrow("android:project_media", Process.myUid(), context.packageName)
            mode == AppOpsManager.MODE_ALLOWED
        } catch (_: Exception) {
            false
        }

        fun request(context: Context) {
            context.startActivity(Intent(context, ProjectionActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION or Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS))
        }
    }
}
