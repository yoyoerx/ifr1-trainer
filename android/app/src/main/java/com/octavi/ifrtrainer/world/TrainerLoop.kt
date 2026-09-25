package com.octavi.ifrtrainer.world

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.octavi.ifrtrainer.bridge.BrainBridge

/**
 * §3.7's background sim-loop thread + Android lifecycle handling: ticks
 * [BrainBridge]'s `World` at ~30 Hz on a dedicated daemon thread, suspending
 * cleanly on `onStop` (backgrounded/Doze) rather than free-running or
 * crashing on resume, per docs/ANDROID_PORT_PLAN.md §3.7/§6 Phase 1.
 *
 * `startWorld()` is idempotent (see [BrainBridge.startWorld]), so `onStart`
 * re-attaching after a background/foreground cycle just resumes ticking the
 * same flight rather than restarting it.
 */
class TrainerLoop(private val context: Context) : DefaultLifecycleObserver {

    fun interface Listener {
        /** Called on the main thread with one formatted debug-state line per tick. */
        fun onTick(line: String)
    }

    var listener: Listener? = null

    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var thread: Thread? = null

    override fun onStart(owner: LifecycleOwner) {
        BrainBridge.startWorld(context)
        if (running) return
        running = true
        val t = Thread({ loop() }, "TrainerLoop")
        t.isDaemon = true
        thread = t
        t.start()
    }

    override fun onStop(owner: LifecycleOwner) {
        running = false
        thread?.interrupt()
        thread = null
    }

    private fun loop() {
        var lastNanos = System.nanoTime()
        while (running) {
            val nowNanos = System.nanoTime()
            val dt = ((nowNanos - lastNanos) / 1_000_000_000.0).coerceIn(0.0, 0.1)
            lastNanos = nowNanos
            val line = BrainBridge.tickWorld(dt)
            mainHandler.post { listener?.onTick(line) }
            try {
                Thread.sleep(33)   // ~30 Hz, matches the desktop loop's own clock.tick(30)
            } catch (_: InterruptedException) {
                return
            }
        }
    }
}
