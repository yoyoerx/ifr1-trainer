package com.octavi.ifrtrainer.bridge

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Thin Kotlin-side wrapper around the `androidbridge` Python package
 * (repo root `androidbridge/`, staged into this build by the
 * `stageBrainPython` Gradle task — see `app/build.gradle.kts`).
 *
 * UNVERIFIED SCAFFOLD (see android/README.md): these Chaquopy calls follow
 * the documented `Python.start` / `getModule` / `callAttr` pattern but have
 * not been compiled or run — this environment has no Android SDK/Gradle to
 * do that with. First real build is what actually proves this.
 */
object BrainBridge {
    private var started = false

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
}
