package org.loco.agent

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock

/**
 * Rallume l'écran pour une session d'écran ouverte depuis loco : à son ouverture, puis au premier clic ou à la première
 * touche si l'appareil s'est rendormi entre-temps — comme on touche un téléphone en veille. La personne qui prend la
 * main arrive ainsi sur l'écran de verrouillage et peut saisir le code, au lieu de rester devant un écran éteint.
 * Rien n'est déverrouillé ni contourné : l'écran s'allume, visiblement, et Android demande toujours le code.
 *
 * Deux moyens, du plus discret au plus sûr : le réveil d'Android (WAKE_LOCK, avec TURN_SCREEN_ON sur Android 13+), puis,
 * si l'écran reste éteint (certains appareils ignorent le premier), une activité sans interface autorisée à l'allumer.
 */
object Wake {
    private const val HOLD_MS = 4000L          // écran tenu allumé le temps d'arriver ; ensuite, le délai normal de l'appareil
    private const val CHECK_MS = 500L

    private fun power(context: Context) = context.getSystemService(Context.POWER_SERVICE) as PowerManager

    /** L'écran est-il éteint (appareil en veille) ? */
    fun asleep(context: Context): Boolean = try { !power(context).isInteractive } catch (_: Exception) { false }

    /**
     * Allume l'écran s'il est éteint. [waitMs] : attend au plus ce délai qu'il soit allumé (ouverture d'une session :
     * la première image doit montrer l'écran, pas la veille). Renvoie vrai si un réveil a été demandé.
     */
    fun screen(context: Context, waitMs: Long = 0): Boolean {
        val app = context.applicationContext
        if (!asleep(app)) return false
        try {
            @Suppress("DEPRECATION")
            power(app).newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or
                    PowerManager.ON_AFTER_RELEASE, "loco:reveil").acquire(HOLD_MS)
        } catch (_: Exception) {
            // permission retirée ou appareil qui refuse : l'activité ci-dessous prend le relais
        }
        Handler(Looper.getMainLooper()).postDelayed({ if (asleep(app)) WakeActivity.start(app) }, CHECK_MS)
        val limit = SystemClock.uptimeMillis() + waitMs
        while (asleep(app) && SystemClock.uptimeMillis() < limit) {
            try { Thread.sleep(50) } catch (_: InterruptedException) { break }
        }
        return true
    }
}

/**
 * Sans interface : allume l'écran par-dessus l'écran de verrouillage, puis se referme (le verrouillage reste en place).
 * Lancée pour déverrouiller ([dismiss]), elle demande en plus à Android d'afficher la saisie du code
 * (requestDismissKeyguard, le moyen officiel, quelle que soit la surcouche du constructeur) et se referme dès que
 * l'appareil est déverrouillé ou que la saisie est abandonnée.
 */
class WakeActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private var asked = false

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        main.postDelayed({ finish() }, if (intent?.getBooleanExtra(EXTRA_DISMISS, false) == true) DISMISS_MS else 800L)
    }

    // Demandée une fois visible : Android refuse la demande d'une activité qui ne l'est pas encore.
    override fun onResume() {
        super.onResume()
        if (asked || intent?.getBooleanExtra(EXTRA_DISMISS, false) != true) return
        asked = true
        try {
            (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).requestDismissKeyguard(this,
                object : KeyguardManager.KeyguardDismissCallback() {
                    override fun onDismissSucceeded() { finish() }
                    override fun onDismissCancelled() { finish() }
                    override fun onDismissError() { finish() }
                })
        } catch (_: Exception) {
            finish()
        }
    }

    override fun onDestroy() {
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        private const val EXTRA_DISMISS = "dismiss"
        private const val DISMISS_MS = 60_000L        // saisie du code laissée ouverte, au plus une minute

        fun start(context: Context) { launch(context, false) }

        /** Fait monter la saisie du code de l'écran de verrouillage. Faux si Android a refusé de lancer l'activité. */
        fun dismiss(context: Context): Boolean = launch(context, true)

        private fun launch(context: Context, dismiss: Boolean): Boolean = try {
            context.startActivity(Intent(context, WakeActivity::class.java).putExtra(EXTRA_DISMISS, dismiss)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION or Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS))
            true
        } catch (_: Exception) {
            false                // lancement refusé par Android (application en arrière-plan sans dérogation)
        }
    }
}
