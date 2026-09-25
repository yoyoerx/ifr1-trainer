# android/ — Phase 1 in progress (core loop confirmed)

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

**Phase 1's core loop is also confirmed on real hardware (2026-09-24).**
`androidbridge/session.py` now wraps `main.World`/`main.route_event`
directly (Chaquopy now also stages `main.py`, `ifr1.py`, `config.py`,
`gns530.py`, `gns430.py` — all confirmed import-clean; `main.py`'s
module-level imports turned out to be stdlib-only all along, correcting
`androidbridge/__init__.py`'s old "can't reuse main.py, it imports pygame"
assumption). `world/TrainerLoop.kt` ticks it ~30 Hz on a background thread,
suspending cleanly on `onStop`/resuming on `onStart`. Real IFR-1 events
route through the exact mode-routing table desktop uses — on-device,
turning COM1/COM2 correctly tuned standby frequencies, NAV1/NAV2's
shift-latched knob correctly turned the OBS/CRS card, AP-row buttons
correctly changed AP annunciators, all visible live in
`world/Phase1LoopScreen.kt`'s plain-text debug panel (not the real
instrument graphics yet — §3.6 below).

Two real on-device bugs found and fixed getting this far:
1. `ifr1.py` had a **top-level** `import hid` inside a `try/except` that
   raised `SystemExit` on *any* `import ifr1` if hidapi wasn't installed -
   true by design under Chaquopy (§3.4 drops hidapi entirely). This broke
   even the already-working Phase 0 self-test the moment `androidbridge`
   started importing `ifr1` for its `Mode`/`Event` dataclasses. Fixed by
   making the import lazy/guarded (`_require_hid()`, called only from
   `IFR1.__init__`/`find_devices()` - both desktop-hardware-only paths
   `androidbridge` never touches).
2. Chaquopy's Java `List`/`ArrayList` → Python marshaling for a `callAttr`
   argument produced a Python-side object that `tuple()`/`list()` couldn't
   consume (`TypeError: 'ArrayList' object is not iterable` - and the same
   failure for Kotlin's `emptyList()` singleton too, so not a
   collection-type-specific bug). Crashed on every knob turn. Worked around
   by passing `pressed`/`released`/`long_press` as comma-joined strings
   across that boundary instead of lists - button names never contain
   commas, so nothing is lost.

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
  draw-command-list contract and Kotlin-side replay mechanism. **The HSI
  head, AP panel, and four GNS pages (default NAV, Flight Plan, VNAV,
  NAV/COM) are real now (2026-09-24/25)**, confirmed on real hardware:
  repo-root `render_commands.py` (a new pure module, no pygame) ports
  `draw_hsi_head`/`draw_ap_panel`/`_gns_unit`'s chrome/`_draw_nav_default`/
  `_draw_fpl`/`_draw_vnav_page`/`_draw_navcom_page`/`_draw_freq_rows`/
  `_turn_advisory`/`_cdi_strip`/`_bezel_labels` and their helpers from
  `render.py`, same trig/layout math, and `InstrumentCanvas`'s `Text` case
  (formerly a TODO) is implemented via `nativeCanvas.drawText` +
  `android.graphics.Paint`. The draw-command list crosses to Kotlin as a
  newline/pipe-delimited **string** (`parseDrawCommands` decodes it) rather
  than a list/`PyObject` - see the `List`-marshaling bug noted below, which
  applies to this direction too.

  **The GNS screen renders four pages: default NAV, Flight Plan, VNAV, and
  NAV/COM.** `gns_commands` dispatches its body by `cursor.page_name`,
  falling back to the default page for anything else. `_gns_unit` is a
  ~15-page router (Map, Flight Plan Catalog, WPT, NRST, AUX + 5 sub-tabs,
  plus 8 modal dialogs) - porting all of it at once wasn't realistic, same
  reasoning as doing HSI then AP panel separately. `route_event` already
  dispatches real FMS bezel-key input correctly (proven by the core-loop
  milestone), so `gns.cursor.page_name` does change server-side when the
  FMS knob turns - pages without a body here just fall back to the
  default-NAV layout until they get their own pass. Not a bug; documented
  scope. The Flight Plan page is also **read-only** - the in-place
  ident-edit buffer (live typing while adding/editing a waypoint) doesn't
  render yet, though real bezel input still edits the plan server-side.

  The AP panel slice found and fixed a systemic text-positioning bug the
  HSI slice's looser spacing had hidden: `nativeCanvas.drawText` positions
  by **baseline**, `render.py`'s pygame text by **top-left** - ad hoc
  per-call offsets tuned against the HSI alone didn't generalize to the AP
  panel's tightly-packed info box (overlapped badly). Fixed once, at the
  root, in `InstrumentCanvas.kt` (`y - paint.ascent()`) rather than
  patching every call site - `render_commands.py`'s `y` arguments are now
  plain top-anchored values matching `render.py`'s own arguments directly
  (a `_centered_y()` helper covers the few `center=True` call sites, which
  need both axes centered on a point, not just top-anchored).

  Everything else (moving map, GNS softkey/page UI) is still not ported -
  one instrument at a time. The six-pack gauge cluster is **not planned
  for Android at all** (decision, 2026-09-24 — see `ANDROID_PORT_PLAN.md`
  §7): IFR training is the point, not a round-gauge steam-panel trainer.
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
- `world/TrainerLoop.kt` — §3.7's background sim-loop thread: a
  `DefaultLifecycleObserver` that ticks `BrainBridge.tickWorld(dt)` at
  ~30 Hz on a dedicated daemon thread, suspending on `onStop` and resuming
  on `onStart` rather than free-running while backgrounded or crashing on
  resume. Confirmed on real hardware (2026-09-24).
- `world/Phase1LoopScreen.kt` — Phase 1's core-loop verification screen:
  wires a real `UsbHidInput` into `BrainBridge.dispatchEvent` and a
  `TrainerLoop` into a plain live-updating text panel (mode/shift,
  heading/altitude/IAS/VS, COM/NAV frequencies, XPDR, AP annunciators,
  CDI). Deliberately not real instrument graphics - that's §3.6, still to
  come; this proves the loop underneath it first. Reachable from the
  self-test screen's "Phase 1 core loop" button.
- `androidbridge/demo_session.py` (repo root, not under `android/`) —
  Phase 1's Kotlin-facing entry points (`new_session`, `tick_line`,
  `dispatch`), built on a small synthetic nav database (same one
  `selftest.py` uses) since real on-device FAA data acquisition is §3.5,
  not yet built. `tick_line` returns one formatted string per tick rather
  than a dict, reusing `selfTest()`'s already-proven String-marshaling
  pattern instead of introducing dict/PyObject marshaling as a second,
  separately-risky path.
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
   the full evidence.
5. ~~Phase 1 core loop: real IFR-1 input → `World` → live state, with
   background-thread ticking + Android lifecycle handling~~ **Done
   (2026-09-24)** — see "Status" above for the two real bugs found/fixed
   getting there. Verified on real hardware: COM/NAV tuning, NAV OBS
   shift-latch, AP-row buttons, XPDR all confirmed correct.
6. ~~§3.6 draw-command-list instrument rendering, first slice~~ **Done
   (2026-09-24)**: the HSI head is real instrument graphics now, confirmed
   on the real Pixel 9 + real IFR-1 - the dial/compass card/heading readout
   render correctly, and NAV1's shift-latched knob live-rotates the course
   pointer on screen. `Phase1LoopScreen` shows it alongside the plain-text
   panel (which still covers everything not yet ported).
7. ~~§3.6 draw-command-list instrument rendering, second slice~~ **Done
   (2026-09-24)**: the AP panel (S-TEC 55X mode row, RDY lamp, VS window,
   info box) is real instrument graphics now too, confirmed on the real
   Pixel 9 - see "Status" above for the baseline-vs-top-left text-position
   bug found and fixed getting there.
8. ~~§3.6 draw-command-list instrument rendering, third slice~~ **Done
   (2026-09-24)**: the GNS default NAV page is real instrument graphics
   now too, confirmed on the real Pixel 9 with the demo flight plan active
   - header, active-leg line, DTK/TRK/DIS/GS/ETE/XTK rows, and CDI strip
   all render correctly. **Default NAV page only** - see "What's actually
   in this scaffold" above for what's deliberately not ported yet.
9. ~~§3.6 draw-command-list instrument rendering, fourth slice~~ **Done
   (2026-09-25)**: the Flight Plan page is real instrument graphics now
   too, confirmed on the real Pixel 9 - waypoint list, active-leg marker,
   per-leg DTK/DIS, and CDI strip all render correctly. Read-only (no
   in-place ident-edit buffer yet).
10. ~~§3.6 draw-command-list instrument rendering, fifth slice~~ **Done
    (2026-09-25)**: the VNAV and NAV/COM pages are real instrument graphics
    now too, confirmed on the real Pixel 9 - each correctly shows its
    no-data fallback ("no active VNAV target" / "no airport in flight
    plan") since the synthetic demo db has neither an armed VNAV profile
    nor any airports; the fully-populated branches are unit-tested but not
    yet confirmed on-device.
11. **Do next**: more of §3.6 - Map or Flight Plan Catalog next, then
    WPT/NRST/AUX and the modal dialogs, then the moving map - one
    instrument/page at a time, same pattern as the prior slices. Touch/
    rotary-gesture controls (§3.2) are also still pending. (The six-pack
    gauge cluster is not planned for Android at all - decision, 2026-09-24,
    `ANDROID_PORT_PLAN.md` §7.)
