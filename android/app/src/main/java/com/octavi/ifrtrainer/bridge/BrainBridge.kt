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
     * §3.5: where `androidbridge.nav_update`/`new_real_session` read and
     * write cached FAA data - Android's private app storage
     * (`context.filesDir`), not `datasrc.faa.default_data_root()`'s
     * repo-relative default, which resolves nowhere useful on Chaquopy's
     * staged asset filesystem. A subdirectory (not `filesDir` itself) so
     * this data has a clearly separate, easy-to-wipe location.
     */
    fun navDataRoot(context: Context): String =
        context.applicationContext.filesDir.resolve("navdata").absolutePath

    /**
     * §3.5: fetches one FAA data product ("cifp"/"nasr"/"artcc"/"airspace"
     * - see `androidbridge.nav_update.KINDS`) into [navDataRoot] for the
     * current AIRAC cycle. **Blocking, does real network I/O** - call from
     * a background thread/coroutine, never the main thread (Android would
     * throw `NetworkOnMainThreadException` regardless). Returns "ok" or a
     * message describing what went wrong - see
     * [androidbridge.nav_update.fetch_kind]'s docstring for why this never
     * raises across the Chaquopy boundary instead.
     */
    fun fetchNavDataKind(context: Context, kind: String): String {
        ensureStarted(context)
        val module = Python.getInstance().getModule("androidbridge.nav_update")
        return module.callAttr("fetch_kind", navDataRoot(context), kind).toString()
    }

    /** §3.5: one line describing the newest cycle cached under [navDataRoot],
     * or "no nav data cached" if [fetchNavDataKind] hasn't been run yet. */
    fun navDataStatus(context: Context): String {
        ensureStarted(context)
        val module = Python.getInstance().getModule("androidbridge.nav_update")
        return module.callAttr("status", navDataRoot(context)).toString()
    }

    /**
     * §3.5: the real-nav-data equivalent of [startWorld] - builds a
     * [androidbridge.demo_session.new_real_session] session from whatever
     * is cached under [navDataRoot] (must have at least the "cifp" kind
     * fetched - see [fetchNavDataKind]) and makes it the active session,
     * **replacing** any existing demo session (same shared `sessionObj`
     * every render/dispatch call already uses, so nothing else needs to
     * know which kind of session is backing it). Returns "ok" or an error
     * message (e.g. `navdata.load()`'s `FileNotFoundError` if nothing is
     * cached yet) - never throws, same convention as every other
     * `androidbridge` call.
     */
    fun startRealWorld(context: Context): String {
        ensureStarted(context)
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return try {
            sessionObj = module.callAttr("new_real_session", navDataRoot(context))
            "ok"
        } catch (t: Throwable) {
            "error: ${t.message}"
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
     * §3.6 second slice: the S-TEC 55X AP programmer panel's draw-command
     * list, same encoding/decoder/call-after-tickWorld convention as
     * [renderHsi].
     */
    fun renderApPanel(x: Float, y: Float, w: Float, h: Float): String {
        val session = sessionObj ?: return ""
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return module.callAttr("render_ap_panel", session, x, y, w, h).toString()
    }

    /**
     * §3.6: the GNS unit screen's draw-command list - see
     * `render_commands.gns_commands`'s docstring for exactly which pages/
     * dialogs render (all ten text pages plus all 8 modal dialogs, as of
     * 2026-09-25) and what's still deliberately not ported (AUX
     * Weather/Charts). The Map page has its own [renderMap] call, not this
     * one. Same encoding/decoder/call-after-tickWorld convention as
     * [renderHsi]/[renderApPanel].
     */
    fun renderGns(x: Float, y: Float, w: Float, h: Float): String {
        val session = sessionObj ?: return ""
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return module.callAttr("render_gns", session, x, y, w, h).toString()
    }

    /**
     * §3.6 final slice: the track-up moving map's draw-command list - see
     * `render_commands.map_commands`'s docstring for what's ported
     * (flight-plan legs/waypoint symbols, DTO course, nearby airport/VOR/
     * NDB symbols, ownship) and deferred (Class B/C/D airspace overlays,
     * label-overlap declutter). Same encoding/decoder/
     * call-after-tickWorld convention as [renderHsi]/[renderApPanel].
     */
    fun renderMap(x: Float, y: Float, w: Float, h: Float): String {
        val session = sessionObj ?: return ""
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        return module.callAttr("render_map", session, x, y, w, h).toString()
    }

    /**
     * §3.2 touch controls: the moving map's RNG key - see
     * [androidbridge.session.TrainerSession.adjust_map_range]'s docstring
     * for why this is a dedicated call rather than [dispatchEvent] with an
     * "RNG" button name (there is no such button in `route_event`/
     * `GpsNav.handle_event` - RNG is desktop-UI-loop-only state on the
     * Python side too). `factor` > 1 zooms out, < 1 zooms in.
     */
    fun adjustMapRange(factor: Float) {
        val session = sessionObj ?: return
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        module.callAttr("adjust_map_range", session, factor)
    }

    /**
     * §3.2 touch controls: the AP mode's VS knob, as its own independent
     * touch control - see
     * [androidbridge.session.TrainerSession.adjust_vs]'s docstring for why
     * this bypasses [dispatchEvent]'s shift-latch gating rather than
     * sharing one knob with [adjustIasTarget] the way real hardware does.
     */
    fun adjustVs(detents: Int) {
        val session = sessionObj ?: return
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        module.callAttr("adjust_vs", session, detents)
    }

    /** §3.2 touch controls: the AP mode's IAS set-point knob - see [adjustVs]. */
    fun adjustIasTarget(detents: Int) {
        val session = sessionObj ?: return
        val module = Python.getInstance().getModule("androidbridge.demo_session")
        module.callAttr("adjust_ias_target", session, detents)
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
