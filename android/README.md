# android/ — Phase 0 scaffold

Companion to `docs/ANDROID_PORT_PLAN.md` (read that first). This is the
Chaquopy hybrid app shell: Kotlin/Compose UI + the existing Python avionics
brain, unmodified, staged in from the repo root at build time.

## Status: builds and runs on real hardware (2026-09-24)

**Confirmed, not just written.** `.\gradlew.bat :app:assembleDebug` succeeds
end-to-end (`BUILD SUCCESSFUL`) via Android Studio + its JetBrains MCP
Server, and the resulting debug APK was installed and launched on a real
Pixel 9 over wireless adb. The Phase 0 self-test screen shows, on-device:

```
octavi-ifr-trainer — Android Phase 0
OK: navmath/navdata/gpsnav/sim_model/androidbridge imported and ticked -
to='BRAVO' pos=(39.9006,-74.0000)
```

That's real proof the ported Python avionics brain runs under Chaquopy on
actual Android hardware — §3.4 is no longer a documented guess.

Getting there required fixing six real, concrete build/toolchain errors, in
order (see commit `1976a11` for the full list): a missing Compose-compiler
plugin version (Kotlin 2.0+ split it out of `kotlin.android`), the wrong JVM
for Gradle (system JDK 8), AGP 8.6.1 being fundamentally incompatible with
Gradle 9.x (`org.gradle.util.VersionNumber` was removed there — no wrapper
could even be *generated* under it, so the working Gradle 8.9 wrapper had to
be fetched directly rather than bootstrapped), Gradle 8.9 not supporting
Android Studio's bundled JDK 25 JBR (needed a separate JDK 17 install), a
missing Gradle task dependency (`mergeDebugPythonSources` reading
`stageBrainPython`'s output without a declared edge), and a missing launcher
icon resource (`@mipmap/ic_launcher` referenced but never created).

The Gradle wrapper (`gradlew`, `gradlew.bat`, `gradle/wrapper/*`) is now
committed, pinned to Gradle 8.9 — the version confirmed compatible with
AGP 8.6.1 and JDK 17. Use that combination; don't let Android Studio "helpfully"
upgrade Gradle without re-checking AGP compatibility first.

What else is verified, without Android: every `.py` file this project
stages in (`navmath.py`, `gpsnav.py`, `instruments.py`, `autopilot.py`,
`radios.py`, `windsaloft.py`, `wmm.py`, `scoring.py`, `sim_model.py`,
`navdata/`, `androidbridge/`) is exercised by the normal desktop `pytest`
suite, `androidbridge/` included (`tests/test_androidbridge.py`) — see the
repo root `README.md`/`CLAUDE.md`.

Also confirmed, on real hardware (2026-09-24): `UsbHidInput.kt`'s runtime
behavior. `claimInterface(force = true)` **succeeds** against the real
IFR-1, and buttons, outer knob, and inner knob all decode correctly through
the one raw-HID code path — see §3.1's "Runtime-confirmed" writeup in
`ANDROID_PORT_PLAN.md` for the exact observed frames. §3.1's hard project
gate has passed.

## Prerequisites to actually open/build this

- **Android Studio** (current stable; it bundles a compatible JDK — don't
  rely on this machine's JDK 8, it's too old for AGP 8.x).
- **Android SDK** (Android Studio's SDK Manager, compileSdk/targetSdk 35).
- A **Pixel 9** (or any Android 14/15 device) for the real §3.1 USB HID
  spike — an emulator has no USB host passthrough for a plugged-in Octavi
  IFR-1.
- The Gradle wrapper is committed (`gradlew`, `gradlew.bat`,
  `gradle/wrapper/*`), pinned to Gradle 8.9 — confirmed working with AGP
  8.6.1. Requires `JAVA_HOME` pointed at a JDK 17 (Android Studio's bundled
  JBR is JDK 25, which Gradle 8.9 can't run on — install a standalone
  Temurin 17 or similar and set `JAVA_HOME` before invoking `gradlew`).

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
  `ifr1.py`'s `Mode`/`State`/`Event` field-for-field. **Compiles and runs
  correctly** (confirmed 2026-09-24) — `claimInterface(force = true)`
  succeeds against the real IFR-1, and buttons/outer knob/inner knob all
  decode correctly through this path, verified via `input/
  UsbHidTestScreen.kt` (a disposable test harness reachable from the
  self-test screen's "IFR-1 raw-HID test" button).
- `input/UsbHidTestScreen.kt` — the disposable §3.1 step 4 verification
  harness: a Compose screen showing live connection status and a scrolling
  log of decoded `Ifr1Event`s with raw frame hex, reachable from the
  self-test screen's "IFR-1 raw-HID test" button. Not shipped product UI —
  results belong in `ANDROID_PORT_PLAN.md` §3.1, not preserved as app code
  long-term; fine to delete once Phase 1's real input-handling UI exists.
- `AndroidManifest.xml` — landscape-locked (§6 Phase 1 builds landscape
  first); now declares `android.hardware.usb.host` (`required="true"`, IFR-1
  is required for v1) and a `USB_DEVICE_ATTACHED` intent-filter (+
  `res/xml/usb_device_filter.xml`, VID/PID) so the app launches and gets
  USB permission auto-granted when the IFR-1 is plugged in with the app not
  already running.

## Next steps, in order (matches `ANDROID_PORT_PLAN.md` §6 Phase 0)

1. ~~Open this project in Android Studio, let it sync, fix whatever surfaces~~
   **Done (2026-09-24)** — required six real build-fixes, see "Status" above
   and commit `1976a11`.
2. ~~Run the self-test screen on an emulator or device; confirm `BrainBridge
   .selfTest()` returns its `"OK: ..."` string~~ **Done (2026-09-24)** —
   confirmed on a real Pixel 9 via a pulled screenshot, exact string in
   "Status" above. §3.4 on-device confirmation complete.
3. ~~Run the actual §3.1 hardware spike~~ **Done (2026-09-25)**, real Pixel 9
   + real IFR-1 + USB-C OTG adapter, over wireless adb (paired/connected
   per below). Full result in `docs/ANDROID_PORT_PLAN.md` §3.1 "Spike
   result" — short version: every button and the outer knob show up as a
   standard evdev gamepad device (`/dev/input/eventN`, name "Octavi IFR1")
   with a confirmed `BTN_*` mapping table, no raw USB code needed. The
   inner knob produces **no evdev events at all** (confirmed twice, capture
   pipeline itself verified working both times) — open question, needs
   step 4 below.
4. ~~Wire `UsbHidInput` into a real screen and run it against the actual
   IFR-1~~ **Done (2026-09-24)**: `input/UsbHidTestScreen.kt` confirmed both
   open questions — (a) `claimInterface(force = true)` succeeds, status goes
   `CONNECTED`, no `usbhid` fallback needed; (b) the raw report stream
   matches `ifr1.py`'s `LAYOUT` byte-for-byte (inner knob CW/CCW, `KNOB`
   button, mode byte all observed decoding correctly). §3.1's hard project
   gate has passed — see `ANDROID_PORT_PLAN.md` §3.1 "Runtime-confirmed" for
   the full evidence. **Do next**: Phase 1 (landscape core loop with real
   IFR-1 input wired into the actual trainer UI, per §6) — the disposable
   test screen has served its purpose.
