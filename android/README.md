# android/ — Phase 0 scaffold

Companion to `docs/ANDROID_PORT_PLAN.md` (read that first). This is the
Chaquopy hybrid app shell: Kotlin/Compose UI + the existing Python avionics
brain, unmodified, staged in from the repo root at build time.

## Status: unbuilt, unverified

**This project has never been opened, synced, or built.** The environment
that wrote it has no Android SDK, no JDK 17 (only JDK 8), and no Gradle
installed — so every version pin in `build.gradle.kts`/`settings.gradle.kts`,
the Chaquopy `sourceSets` API call, and the Kotlin/Compose/Chaquopy code
under `app/src/main/java/` are **best-effort against documented APIs, not
tested against a real build**. Say so plainly rather than claim otherwise:
the first real Android Studio sync is what actually proves or disproves all
of it, and will likely need small fixes (dependency versions bumped, a
Chaquopy DSL detail corrected, etc.) that no amount of careful writing
in a sandboxed shell substitutes for.

What **is** verified, right now, without Android: every `.py` file this
project stages in (`navmath.py`, `gpsnav.py`, `instruments.py`,
`autopilot.py`, `radios.py`, `windsaloft.py`, `wmm.py`, `scoring.py`,
`sim_model.py`, `navdata/`, `androidbridge/`) is exercised by the normal
desktop `pytest` suite, `androidbridge/` included
(`tests/test_androidbridge.py`) — see the repo root `README.md`/`CLAUDE.md`.
That's the actual Phase 0 win so far: proof the brain runs correctly in
isolation, on this machine, today — the Android build is what's still owed.

## Prerequisites to actually open/build this

- **Android Studio** (current stable; it bundles a compatible JDK — don't
  rely on this machine's JDK 8, it's too old for AGP 8.x).
- **Android SDK** (Android Studio's SDK Manager, compileSdk/targetSdk 35).
- A **Pixel 9** (or any Android 14/15 device) for the real §3.1 USB HID
  spike — an emulator has no USB host passthrough for a plugged-in Octavi
  IFR-1.
- No Gradle wrapper is committed here — Android Studio will generate one on
  first open (or run `gradle wrapper` yourself if you have Gradle
  installed separately). Deliberately not shipping a `gradle-wrapper.jar`
  from this environment rather than guess at a binary that can't be
  verified here.

## What's actually in this scaffold

- `app/build.gradle.kts`'s `stageBrainPython` task — copies the curated
  brain-module list from the repo root into a build-generated (gitignored)
  directory, which Chaquopy's source set points at. **Single source of
  truth stays the repo-root `.py` files** — nothing here is a hand-maintained
  duplicate. See the task's own comment for exactly what's included/excluded
  and why (`datasrc/`, `tests/`, and every desktop-only pygame/hid-importing
  module are deliberately left out).
- `bridge/BrainBridge.kt` — starts Chaquopy, calls
  `androidbridge.selftest.run()` (repo root `androidbridge/selftest.py`),
  and returns its one-line result string.
- `MainActivity.kt` — a single Compose screen that calls `BrainBridge
  .selfTest()` on launch and displays the result. This is the whole app
  right now: a self-test screen, not a trainer UI.
- `render/DrawCommand.kt` + `render/InstrumentCanvas.kt` — the §3.6
  draw-command-list *contract shape* and a Kotlin-side replay mechanism.
  Nothing produces a real command list from actual GNS-530 layout math yet
  (`InstrumentCanvas` currently has no caller passing it real data, and its
  `Text` case is an explicit unimplemented TODO — Compose's `Canvas`
  `DrawScope` needs a native `Paint`/`drawText` call for this that wasn't
  worth guessing at without being able to check font-metrics behavior
  against a running app).
- `input/UsbHidInput.kt` — deliberately empty. Nothing here until the §3.1
  spike runs on real hardware and its result is known.
- `AndroidManifest.xml` — landscape-locked (§6 Phase 1 builds landscape
  first), no USB-host feature/intent-filter declared yet (belongs here once
  §3.1 resolves, not declared speculatively).

## Next steps, in order (matches `ANDROID_PORT_PLAN.md` §6 Phase 0)

1. Open this project in Android Studio on a machine that has it, let it
   sync, fix whatever the sync surfaces (expect at least a version bump or
   two — see "Status" above).
2. Run the self-test screen on an emulator or device; confirm `BrainBridge
   .selfTest()` returns its `"OK: ..."` string rather than throwing —
   this is the on-device confirmation of §3.4.
3. Run the actual §3.1 hardware spike: Pixel 9 + Octavi IFR-1 + USB-C OTG
   adapter, `adb shell dumpsys usb` / `adb shell getevent -lt` while
   operating every control. Write the result back into
   `docs/ANDROID_PORT_PLAN.md` §3.1 and update `input/UsbHidInput.kt`
   accordingly — this is the hard gate the whole v1 scope depends on.

   The IFR-1 occupies the phone's only USB-C port, so run `adb` over Wi-Fi
   for this: Developer options → Wireless debugging → "Pair device with
   pairing code" (no prior USB connection needed), then
   `adb pair <ip>:<port>` / `adb connect <ip>:<port>` from this machine.
   Shell commands work the same over either transport. See
   `ANDROID_PORT_PLAN.md` §3.1 step 0.

   **Confirmed working (2026-09-25)** against the real Pixel 9: paired and
   connected over Wi-Fi, `adb shell` verified live. Ready to plug in the
   IFR-1 and run the actual spike whenever the hardware's in hand — nothing
   about the wireless-adb setup itself is still open.
