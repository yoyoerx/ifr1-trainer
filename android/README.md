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
- `input/UsbHidInput.kt` — still deliberately empty (needs the Android
  Studio build to write real Kotlin against). The §3.1 spike now *has* run
  on real hardware — see `ANDROID_PORT_PLAN.md` §3.1 "Spike result
  (2026-09-25)" for the confirmed `BTN_*`/`REL_DIAL` mapping this file
  should implement: an `InputDevice` listener for every button + the outer
  knob (works today, no raw USB code needed), plus an open question on the
  inner knob (evdev sees nothing at all for it — needs `UsbManager.
  claimInterface()` tried from an actual app to know if raw HID access is
  possible once `usbhid` has already claimed the interface).
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
3. ~~Run the actual §3.1 hardware spike~~ **Done (2026-09-25)**, real Pixel 9
   + real IFR-1 + USB-C OTG adapter, over wireless adb (paired/connected
   per below). Full result in `docs/ANDROID_PORT_PLAN.md` §3.1 "Spike
   result" — short version: every button and the outer knob show up as a
   standard evdev gamepad device (`/dev/input/eventN`, name "Octavi IFR1")
   with a confirmed `BTN_*` mapping table, no raw USB code needed. The
   inner knob produces **no evdev events at all** (confirmed twice, capture
   pipeline itself verified working both times) — open question, needs
   step 4 below.
4. **Do next**: once Android Studio is synced (step 1) and a real build
   exists on the phone, try `UsbManager.requestPermission()` +
   `claimInterface()` against the IFR-1's HID interface specifically to see
   whether raw report access is possible for the inner knob despite
   `usbhid` already having claimed it for the working controls — `adb`
   alone can't answer this, it needs code actually running as the app.
   Write `input/UsbHidInput.kt` for the confirmed button/outer-knob path
   either way; the inner knob's resolution (raw HID if claiming succeeds,
   otherwise touch-only per §3.2, or a firmware ask to Octavi) follows from
   this result.
