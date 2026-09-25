package com.octavi.ifrtrainer.bridge

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Thin Kotlin-side wrapper around the `androidbridge` Python package
 * (repo root `androidbridge/`, staged into this build by the
 * `stageBrainPython` Gradle task — see `app/build.gradle.kts`).
 *
 * Confirmed compiling and running on real hardware (2026-09-24) via
 * [selfTest]. The Phase 1 `startWorld`/`tickWorld`/`dispatchEvent` trio
 * below follows the exact same proven `Python.start` / `getModule` /
 * `callAttr` pattern.
 */
object BrainBridge {
    private var started = false
    private var sessionObj: PyObject? = null

    fun ensureStarted(context: Context) {
        if (!started) {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(context.applicationContext))
            }
            started = true
        }
    }

    /**
     * Phase 0 self-test: builds a tiny synthetic flight plan and ticks a
     * [androidbridge.TrainerSession] once, entirely in Python
     * (`androidbridge/selftest.py::run`), and returns a one-line summary.
     *
     * This is the on-device half of the §3.4 spike in
     * docs/ANDROID_PORT_PLAN.md — proving `navmath` -> `navdata` ->
     * `gpsnav` -> `sim_model` -> `androidbridge` actually imports and runs
     * under Chaquopy on real hardware, not just under desktop CPython
     * (which `tests/test_androidbridge.py` already confirms separately).
     */
    fun selfTest(context: Context): String {
        ensureStarted(context)
        val module = Python.getInstance().getModule("androidbridge.selftest")
        return module.callAttr("run").toString()
    }

    /**
     * Phase 1 core loop (§6): builds a [androidbridge.demo_session]
     * `TrainerSession` - a real `main.World` (gns/sim/radios/ap), on the
     * same small synthetic nav database `selfTest` uses (real on-device
     * nav-data acquisition is §3.5, not yet built). Idempotent - a second
     * call is a no-op if a session already exists, so a `Lifecycle`
     * observer can call it from `onResume` freely without restarting the
     * flight every time the app comes back to the foreground.
     */
    fun startWorld(context: Context) {
        ensureStarted(context)
        if (sessionObj == null) {
            val module = Python.getInstance().getModule("androidbridge.demo_session")
            sessionObj = module.callAttr("new_session")
        }
    }

    /**
     * Ticks the world `dtS` seconds and returns one formatted, human-readable
     * line of key state (mode/shift, heading/altitude/IAS/VS, COM/NAV
     * frequencies, XPDR, AP annunciators, CDI) - `androidbridge.demo_session
     * .tick_line` does the formatting Python-side, reusing [selfTest]'s
     * already-proven String-return marshaling rather than introducing
     * dict/PyObject marshaling as a second, unproven path. This is this
     * pass's verification UI, not the real instrument rendering (§3.6,
     * deferred - see docs/ANDROID_PORT_PLAN.md).
     */
    fun tickWorld(dtS: Double): String {
        val session = sessionObj ?: return "(world not started)"
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return module.callAttr("tick_line", session, dtS).toString()
    }

    /**
     * §3.6 first slice: the HSI head's draw-command list for the given
     * on-screen rect, as a newline/pipe-delimited string - see
     * `render_commands.py`'s module docstring for the encoding and
     * [com.octavi.ifrtrainer.render.parseDrawCommands] for the decoder.
     * Call after [tickWorld] each frame, not instead of it - Python reuses
     * that tick's already-computed `Frame` rather than re-ticking.
     */
    fun renderHsi(x: Float, y: Float, w: Float, h: Float): String {
        val session = sessionObj ?: return ""
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return module.callAttr("render_hsi", session, x, y, w, h).toString()
    }

    /**
     * Routes one real IFR-1 event through `main.route_event` - see
     * [androidbridge.session.TrainerSession.dispatch_event]. Fields map
     * directly from [com.octavi.ifrtrainer.input.Ifr1Event], already
     * confirmed field-for-field identical to `ifr1.py`'s `Event` (§3.1).
     *
     * `pressed`/`released`/`longPress` cross as comma-joined strings, not
     * lists - a real crash on-device (2026-09-24) found that Chaquopy's
     * Java `List` -> Python marshaling for a `callAttr` argument produces an
     * object `tuple()`/`list()` can't consume Python-side ("TypeError:
     * 'ArrayList' object is not iterable" - and the same failure for
     * `emptyList()`'s `EmptyList` singleton too, so it's not a
     * Kotlin-collection-type-specific issue). Button names never contain
     * commas, so joining loses nothing.
     */
    fun dispatchEvent(
        mode: String,
        pressed: List<String>,
        released: List<String>,
        outer: Int,
        inner: Int,
        modeChanged: Boolean,
        longPress: List<String>,
    ) {
        val session = sessionObj ?: return
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        module.callAttr(
            "dispatch",
            session,
            mode,
            pressed.joinToString(","),
            released.joinToString(","),
            outer,
            inner,
            modeChanged,
            longPress.joinToString(","),
        )
    }
}
