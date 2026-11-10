package org.loco.agent

import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.projection.MediaProjection
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLSurface
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.Display
import android.view.Surface
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Capture de l'écran via MediaProjection : l'utilisateur l'autorise explicitement (fenêtre système), et Android
 * affiche en permanence l'indicateur de diffusion — rien n'est capturé en douce.
 *
 * L'écran arrive dans une texture OpenGL, rendue dans un tampon hors écran puis relue : la méthode fonctionne quel que
 * soit le format produit par le GPU (une lecture CPU directe échoue par exemple sur les GPU qui produisent du BGRA).
 * La résolution est plafonnée (fluidité), la rotation suivie, et rien n'est relu tant que personne ne regarde.
 */
class ScreenCapture(context: Context, private val projection: MediaProjection) : FrameSource {
    @Volatile override var width = 0
        private set
    @Volatile override var height = 0
        private set
    @Volatile override var screenWidth = 0
        private set
    @Volatile override var screenHeight = 0
        private set
    private var density = 0

    private val displays = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
    private val thread = HandlerThread("loco-capture").apply { start() }
    private val handler = Handler(thread.looper)
    private val lock = Object()
    private var latest: ByteArray? = null

    private var eglDisplay: EGLDisplay = EGL14.EGL_NO_DISPLAY
    private var eglContext: EGLContext = EGL14.EGL_NO_CONTEXT
    private var eglSurface: EGLSurface = EGL14.EGL_NO_SURFACE
    private var program = 0
    private var oesTex = 0
    private var fbTex = 0
    private var fbo = 0
    private var surfaceTexture: SurfaceTexture? = null
    private var surface: Surface? = null
    private var display: VirtualDisplay? = null
    private var pixels: ByteBuffer? = null
    private val matrix = FloatArray(16)

    @Volatile private var dirty = false
    @Volatile private var lastWanted = 0L
    private var scheduled = false
    private var lastRead = 0L
    @Volatile private var released = false
    private val stats = Rfb.Stats("capture")

    private val listener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(id: Int) {}
        override fun onDisplayRemoved(id: Int) {}
        override fun onDisplayChanged(id: Int) {
            if (id == Display.DEFAULT_DISPLAY) handler.post { resizeIfNeeded() }
        }
    }

    init {
        val ready = CountDownLatch(1)
        var failure: Throwable? = null
        handler.post {
            try { setup() } catch (e: Throwable) { failure = e }
            ready.countDown()
        }
        if (!ready.await(5, TimeUnit.SECONDS)) failure = failure ?: IllegalStateException("capture : délai dépassé")
        failure?.let { stop(); throw it }
        displays.registerDisplayListener(listener, handler)
    }

    /** Taille réelle de l'écran, et taille capturée (plafonnée, paire). Vrai si l'une a changé. */
    private fun measure(): Boolean {
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        displays.getDisplay(Display.DEFAULT_DISPLAY).getRealMetrics(metrics)
        val rw = metrics.widthPixels
        val rh = metrics.heightPixels
        val scale = minOf(1.0, MAX_SIDE.toDouble() / maxOf(rw, rh))
        val w = maxOf(2, (rw * scale).toInt() / 2 * 2)
        val h = maxOf(2, (rh * scale).toInt() / 2 * 2)
        val changed = w != width || h != height || rw != screenWidth || rh != screenHeight
        screenWidth = rw; screenHeight = rh; width = w; height = h; density = metrics.densityDpi
        return changed
    }

    private fun setup() {
        measure()
        eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        val version = IntArray(2)
        check(EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) { "EGL indisponible" }
        val attribs = intArrayOf(
            EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT, EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
            EGL14.EGL_NONE)
        val configs = arrayOfNulls<EGLConfig>(1)
        val found = IntArray(1)
        check(EGL14.eglChooseConfig(eglDisplay, attribs, 0, configs, 0, 1, found, 0) && found[0] > 0) { "EGL : aucune configuration" }
        eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT,
            intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0)
        check(eglContext != EGL14.EGL_NO_CONTEXT) { "EGL : contexte refusé" }
        eglSurface = EGL14.eglCreatePbufferSurface(eglDisplay, configs[0],
            intArrayOf(EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE), 0)
        check(EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) { "EGL : contexte inactif" }

        program = buildProgram()
        val t = IntArray(1)
        GLES20.glGenTextures(1, t, 0)
        oesTex = t[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        allocTarget()

        val st = SurfaceTexture(oesTex)
        st.setDefaultBufferSize(width, height)
        st.setOnFrameAvailableListener({ onFrame() }, handler)
        surfaceTexture = st
        surface = Surface(st)
        display = projection.createVirtualDisplay("loco", width, height, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, surface, null, handler)
    }

    private fun allocTarget() {
        if (fbo != 0) {
            GLES20.glDeleteFramebuffers(1, intArrayOf(fbo), 0)
            GLES20.glDeleteTextures(1, intArrayOf(fbTex), 0)
        }
        val t = IntArray(1)
        GLES20.glGenTextures(1, t, 0)
        fbTex = t[0]
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, fbTex)
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_RGBA, width, height, 0,
            GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, null)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_NEAREST)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_NEAREST)
        val f = IntArray(1)
        GLES20.glGenFramebuffers(1, f, 0)
        fbo = f[0]
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fbo)
        GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER, GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D, fbTex, 0)
        check(GLES20.glCheckFramebufferStatus(GLES20.GL_FRAMEBUFFER) == GLES20.GL_FRAMEBUFFER_COMPLETE) { "tampon hors écran incomplet" }
        pixels = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())
    }

    private fun buildProgram(): Int {
        fun shader(type: Int, src: String): Int {
            val s = GLES20.glCreateShader(type)
            GLES20.glShaderSource(s, src)
            GLES20.glCompileShader(s)
            val ok = IntArray(1)
            GLES20.glGetShaderiv(s, GLES20.GL_COMPILE_STATUS, ok, 0)
            check(ok[0] != 0) { "shader : " + GLES20.glGetShaderInfoLog(s) }
            return s
        }
        val p = GLES20.glCreateProgram()
        GLES20.glAttachShader(p, shader(GLES20.GL_VERTEX_SHADER, VERTEX))
        GLES20.glAttachShader(p, shader(GLES20.GL_FRAGMENT_SHADER, FRAGMENT))
        GLES20.glLinkProgram(p)
        val ok = IntArray(1)
        GLES20.glGetProgramiv(p, GLES20.GL_LINK_STATUS, ok, 0)
        check(ok[0] != 0) { "programme : " + GLES20.glGetProgramInfoLog(p) }
        return p
    }

    // --- Images : consommées dès qu'elles arrivent, relues seulement si une session regarde ------------------
    private fun onFrame() {
        if (released) return
        try { surfaceTexture?.updateTexImage() } catch (_: Exception) { return }
        dirty = true
        schedule()
    }

    private fun wanted() = SystemClock.uptimeMillis() - lastWanted < 3000

    private fun schedule() {
        if (scheduled || !dirty || released || !wanted()) return
        scheduled = true
        handler.postDelayed({ read() }, maxOf(0L, lastRead + MIN_INTERVAL - SystemClock.uptimeMillis()))
    }

    private fun read() {
        scheduled = false
        if (released || !dirty || !wanted()) return
        dirty = false
        lastRead = SystemClock.uptimeMillis()
        val w = width
        val h = height
        surfaceTexture?.getTransformMatrix(matrix)
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fbo)
        GLES20.glViewport(0, 0, w, h)
        GLES20.glUseProgram(program)
        val pos = GLES20.glGetAttribLocation(program, "aPos")
        val tex = GLES20.glGetAttribLocation(program, "aTex")
        GLES20.glEnableVertexAttribArray(pos)
        GLES20.glVertexAttribPointer(pos, 2, GLES20.GL_FLOAT, false, 0, QUAD)
        GLES20.glEnableVertexAttribArray(tex)
        GLES20.glVertexAttribPointer(tex, 2, GLES20.GL_FLOAT, false, 0, TEXCOORDS)
        GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(program, "uTex"), 1, false, matrix, 0)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex)
        GLES20.glUniform1i(GLES20.glGetUniformLocation(program, "sTex"), 0)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        val buf = pixels ?: return
        buf.clear()
        GLES20.glReadPixels(0, 0, w, h, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, buf)

        // L'image est dessinée tête en bas (QUAD), donc relue de haut en bas : une seule copie, sans boucle par pixel.
        val out = ByteArray(w * 4 * h)
        buf.position(0)
        buf.get(out)
        synchronized(lock) { latest = out }
        stats.add(out.size, SystemClock.uptimeMillis() - lastRead)
        if (dirty) schedule()          // une image est arrivée pendant la lecture
    }

    /** Rotation ou changement de résolution : l'affichage virtuel et le tampon suivent la nouvelle taille. */
    private fun resizeIfNeeded() {
        if (released || !measure()) return
        surfaceTexture?.setDefaultBufferSize(width, height)
        display?.resize(width, height, density)
        allocTarget()
        synchronized(lock) { latest = null }
        dirty = true
        schedule()
    }

    override fun frame(): Frame? {
        val idle = !wanted()
        lastWanted = SystemClock.uptimeMillis()
        if (idle || dirty) handler.post { schedule() }
        val d = synchronized(lock) { latest } ?: return null
        val w = width
        val h = height
        return if (d.size == w * h * 4) Frame(d, w, h) else null
    }

    fun stop() {
        released = true
        try { displays.unregisterDisplayListener(listener) } catch (_: Exception) {}
        val done = CountDownLatch(1)
        handler.post {
            try { display?.release() } catch (_: Exception) {}
            try { surface?.release() } catch (_: Exception) {}
            try { surfaceTexture?.release() } catch (_: Exception) {}
            if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
                if (eglSurface != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(eglDisplay, eglSurface)
                if (eglContext != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(eglDisplay, eglContext)
                EGL14.eglTerminate(eglDisplay)
            }
            done.countDown()
        }
        done.await(2, TimeUnit.SECONDS)
        try { projection.stop() } catch (_: Exception) {}
        thread.quitSafely()
        synchronized(lock) { latest = null }
    }

    private companion object {
        const val MAX_SIDE = 1280          // plafond du plus grand côté capturé (au-delà, mis à l'échelle)
        const val MIN_INTERVAL = 33L       // au plus ~30 relectures par seconde

        const val VERTEX = """
            attribute vec4 aPos;
            attribute vec4 aTex;
            uniform mat4 uTex;
            varying vec2 vTex;
            void main() { gl_Position = aPos; vTex = (uTex * aTex).xy; }
        """
        const val FRAGMENT = """
            #extension GL_OES_EGL_image_external : require
            precision mediump float;
            varying vec2 vTex;
            uniform samplerExternalOES sTex;
            void main() { gl_FragColor = texture2D(sTex, vTex); }
        """

        // Sommets renversés de haut en bas : glReadPixels rend alors les lignes dans l'ordre de l'écran.
        val QUAD: FloatBuffer = floats(-1f, 1f, 1f, 1f, -1f, -1f, 1f, -1f)
        val TEXCOORDS: FloatBuffer = floats(0f, 0f, 1f, 0f, 0f, 1f, 1f, 1f)

        fun floats(vararg v: Float): FloatBuffer =
            ByteBuffer.allocateDirect(v.size * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply { put(v); position(0) }
    }
}
