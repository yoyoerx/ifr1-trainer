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
- `input/UsbHidInput.kt` — real implementation now, written against the
  §3.1 spike's confirmed hardware topology and `ifr1.py`'s own
  hardware-confirmed byte layout (see `ANDROID_PORT_PLAN.md` §3.1 "Spike
  result (2026-09-25)"). Deliberately does **not** use `InputDevice`/
  `KeyEvent` even though buttons + the outer knob work that way for free —
  the inner knob doesn't (the kernel's `usbhid` driver claims the HID
  interface and silently drops whatever usage its field declares, since
  its own default translation table has no entry for it), so this claims
  the interface itself with `force = true` (detaching `usbhid` entirely,
  the Android equivalent of what `hidapi` does implicitly on desktop) and
  parses the same raw report every control comes through on, one code path
  matching the desktop protocol exactly instead of splitting logic between
  two different Android APIs. `Ifr1Mode`/`Ifr1State`/`Ifr1Event` mirror
  `ifr1.py`'s `Mode`/`State`/`Event` field-for-field. **Unverified** —
  written from real captured data but not yet compiled (no Android SDK/
  Gradle in the authoring environment); whether `claimInterface(force =
  true)` actually succeeds against a driver `usbhid` already holds is the
  first thing the real build needs to prove.
- `AndroidManifest.xml` — landscape-locked (§6 Phase 1 builds landscape
  first); now declares `android.hardware.usb.host` (`required="true"`, IFR-1
  is required for v1) and a `USB_DEVICE_ATTACHED` intent-filter (+
  `res/xml/usb_device_filter.xml`, VID/PID) so the app launches and gets
  USB permission auto-granted when the IFR-1 is plugged in with the app not
  already running.

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
4. `input/UsbHidInput.kt` is now written (2026-09-25) — a raw-HID reader for
   *every* control (not just the inner knob), `claimInterface(force = true)`
   detaching `usbhid` entirely rather than splitting logic between
   `InputDevice` and raw HID. **Do next**: once Android Studio is synced
   (step 1), wire it into a real screen (or a quick standalone test harness)
   and run it against the actual IFR-1 to prove two things `adb` alone
   couldn't: (a) does `claimInterface(force = true)` actually succeed
   against a driver `usbhid` already holds, and (b) does the resulting raw
   report stream match `ifr1.py`'s `LAYOUT` byte-for-byte on this specific
   unit. If claiming fails, the fallback path is `InputDevice`/`KeyEvent`
   for buttons + the outer knob (still free) with the inner knob resolved
   some other way (touch-only per §3.2, or a firmware ask to Octavi).
