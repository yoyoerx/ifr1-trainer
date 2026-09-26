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
  draw-command-list contract and Kotlin-side replay mechanism. **§3.6 is
  now complete (2026-09-24/25)**: the HSI head, AP panel, all ten GNS text
  pages, all 8 modal dialogs, and the moving map are all real instrument
  graphics, confirmed on real hardware. Repo-root `render_commands.py` (a
  new pure module, no pygame) ports `draw_hsi_head`/`draw_ap_panel`/
  `_gns_unit`'s chrome/`_draw_nav_default`/`_draw_fpl`/`_draw_vnav_page`/
  `_draw_navcom_page`/`_draw_fpl_catalog`/`_draw_wpt_page`/
  `_draw_nrst_page`/`_draw_nrst_airports`/`_draw_nrst_facility`/
  `_draw_nrst_airspace`/`_draw_aux_navdata`/`_draw_aux_trip`/
  `_draw_aux_utility`/`_draw_aux_setup`/`_draw_freq_rows`/
  `_turn_advisory`/`_cdi_strip`/`_bezel_labels`/`_proc_page`/
  `_activate_leg_page`/`_remove_confirm_page`/`_restart_confirm_page`/
  `_dto_menu_page`/`_direct_to_page`/`_message_page`/`_fpl_menu_page`/
  `_airspace_info_page`/`_map`/`_ownship_symbol`/`_hold_track_points`/
  `_pt_symbol_points`/`_draw_vor_symbol`/`_draw_ndb_symbol`/
  `_draw_airport_symbol` (plus main.py's `_nearby` helper) and their
  helpers from `render.py`, same trig/layout math, and
  `InstrumentCanvas`'s `Text` case (formerly a TODO) is implemented via
  `nativeCanvas.drawText` + `android.graphics.Paint`. The draw-command
  list crosses to Kotlin as a newline/pipe-delimited **string**
  (`parseDrawCommands` decodes it) rather than a list/`PyObject` - see the
  `List`-marshaling bug noted below, which applies to this direction too.

  **The GNS screen renders ten pages plus all 8 modal dialogs**: default
  NAV, Flight Plan, VNAV, NAV/COM, Flight Plan Catalog, all six WPT search
  pages (Airport/Airport Runway/Airport Freq/Intersection/NDB/VOR), all
  eight NRST pages (APT/INT/NDB/VOR/User/ARTCC/FSS/Airspace), AUX's Nav
  Data/Trip Planning/Utility/Setup tabs, and every modal dialog (PROC,
  Activate Leg?, Remove/Restart confirm, DTO menu, Direct-To, Message
  page, FPL menu, Airspace info - drawn last, over whatever page body
  already rendered, in the same priority order `_gns_unit` checks them
  in). `gns_commands` dispatches by `cursor.page_name`/`group_name`,
  falling back to the default NAV page for anything else. **Still not
  ported**: AUX Weather/Charts (need a live `datasrc.wx` cache read / PDF
  rendering - out of scope, same carve-out as the six-pack decision).
  `route_event` already dispatches real FMS bezel-key input correctly
  (proven by the core-loop milestone), so `gns.cursor.page_name`/
  `group_name` do change server-side when the FMS knob turns - pages
  without a body here just fall back to the default-NAV layout. Not a
  bug; documented scope. The Flight Plan page is also **read-only** - the
  in-place ident-edit buffer (live typing while adding/editing a waypoint)
  doesn't render yet, though real bezel input still edits the plan
  server-side.

  **The moving map** (`map_commands`) is track-up, own-ship-offset toward
  the bottom third, with range rings, flight-plan legs + waypoint symbols
  (MAP X, hold racetrack, procedure-turn chevron, FAF/plain dots), the DTO
  course line, nearby airport/VOR/NDB symbols, and the ownship triangle.
  **Deliberately not ported**: Class B/C/D airspace polygon overlays and
  label-overlap declutter (needs real font-metrics text-width measurement
  this pygame-free module doesn't have - labels always draw, so a busy
  screen may overlap rather than declutter like desktop does).
  `TrainerSession` gained a `map_range_nm` field (a fixed 20nm default -
  desktop's range is UI-loop-only state, and Android has no range-control
  input yet, §3.2).

  Wiring the Message dialog surfaced a real design conflict:
  `TrainerSession.tick()` already drains `World.gns.messages` for the
  debug-panel snapshot (its own "read=cleared" policy), but desktop's
  Message page reads the non-destructive `gns.peek_messages()` - by the
  time `render_gns()` runs after `tick()` on Android, that queue would
  already be empty. Fixed by having `tick()` stash the batch it just
  drained as `self._last_messages`, which `render_gns()` passes into
  `gns_commands` instead of re-reading an already-emptied queue.

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

  The map slice found and fixed another systemic bug the same way:
  Compose's `Canvas` does **not** clip its own drawing to its layout
  bounds by default, unlike `render.py`'s explicit `pygame.set_clip(rect)`
  for the map. The map's outer range-ring circle, sized against the box's
  height with the own-ship center offset toward the bottom third,
  legitimately extends past the box's top/bottom edges - on first build it
  bled into the debug text below the map. Every earlier instrument's
  geometry happened to stay in-bounds by construction, so this had never
  surfaced. Fixed at the root with `Modifier.clipToBounds()`, applied
  unconditionally to every `InstrumentCanvas` (not just the map's), rather
  than relying on "this instrument's geometry happens to stay in-bounds"
  as a per-instrument invariant a future draw command could silently
  violate again.

  §3.6 is done. The six-pack gauge cluster is **not planned for Android at
  all** (decision, 2026-09-24 — see `ANDROID_PORT_PLAN.md` §7): IFR
  training is the point, not a round-gauge steam-panel trainer.
- `input/TouchControls.kt` — §3.2 touch/rotary controls, confirmed on real
  hardware (2026-09-25) after two design iterations from real-device
  feedback. `RotaryKnob` (circular drag) and `BezelButton`
  (tap/long-press) primitives feed the exact same `BrainBridge
  .dispatchEvent` call real IFR-1 events use - a second, independent
  input source. **Anchored to the instrument each control belongs to**,
  not one generic panel: `GnsBezelOverlay` sits on the GNS canvas
  (mirroring `render_commands.gns_commands`'s own bezel-row layout math,
  now the *real* 530 button set - CDI/OBS/MSG/FPL/VNAV/PROC +
  RNG/D>/MENU/CLR/ENT, replacing a prior invented 8-key mix that included
  a fake "CRSR" key) with `FmsKnobs` beside it; `ApPanelOverlay` sits on
  the AP panel canvas (its 5 wired buttons - REV has no IFR-1 mapping at
  all, matching real hardware) with `ApKnobs` (ALT SEL + two **independent**
  VS/IAS knobs, not one shared shift-latched knob like real hardware -
  found unusable as a touch gesture) below it; `RadioControlPanel` is the
  one remaining generic surface, for COM/NAV/XPDR, which have no dedicated
  rendered instrument yet. `TrainerSession.adjust_map_range`/`.adjust_vs`/
  `.adjust_ias_target` bypass `route_event` for controls that either
  aren't `route_event`-routed buttons at all (RNG, now drawn+touch-split
  as a real two-sided rocker) or need to skip its shift-latch gating
  (VS/IAS). Three more real bugs fixed from device feedback: knob
  sensitivity (doubled degrees-per-detent), and a genuine layout overflow
  that looked like knob overlap (AP/GNS knobs placed beside their 520dp
  panel ran past the phone's right edge in this vertical-only-scrolling
  layout - moved below the panel instead). Ergonomics are explicitly
  first-draft ("good enough for now, fix later," 2026-09-25) - knob feel,
  hit-target sizing, and visual press feedback are all unfinished design
  work, not a completed pass.
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
  Phase 1's Kotlin-facing entry points (`tick_line`, `dispatch`,
  `render_*`). `new_session()` builds a small synthetic nav database (same
  one `selftest.py` uses); `new_real_session(root)` (§3.5, confirmed on
  real hardware 2026-09-25) is the real-FAA-data equivalent - every other
  function is nav-data-agnostic past construction, so both kinds of
  session work through the exact same calls. `tick_line` returns one
  formatted string per tick rather than a dict, reusing `selfTest()`'s
  already-proven String-marshaling pattern instead of introducing
  dict/PyObject marshaling as a second, separately-risky path.
- `androidbridge/nav_update.py` (repo root) — §3.5's on-device FAA data
  fetch, confirmed on real hardware (2026-09-25): `fetch_kind(root, kind)`
  / `status(root)`, thin wrappers around `datasrc.faa.fetch()` /
  `newest_cached_manifest()`. `datasrc/faa.py` turned out to already be
  stdlib-only `urllib` networking (not `requests` - that wrong assumption
  had kept `datasrc/` off `app/build.gradle.kts`'s staged module list;
  it's staged now, alongside `navdata`/`androidbridge`). Default kinds are
  `cifp`, `nasr`, `artcc` - **not** `airspace`, which `datasrc/faa.py`'s
  own docstring says is a ~150MB one-time zipped download, "never fetched
  by default"; a first draft included it by mistake (assuming, wrongly,
  that it was as cheap as `artcc`'s ~170KB) and was caught and fixed
  before any real-device testing. `nav/NavDataScreen.kt` (reachable from
  the self-test screen) drives the download with real per-kind progress,
  then switches `BrainBridge`'s active session from demo to real data.
  `AndroidManifest.xml` gained the `INTERNET` permission for this - nothing
  before it had needed network access. A real crash was found and fixed
  via on-device testing after switching to real data: the debug panel's
  `tick_line` format string didn't handle `NavState.xtk_nm` being `None`
  (real sessions start with no flight plan loaded, unlike the demo one) -
  see `docs/ANDROID_PORT_PLAN.md` §3.5 for the fix and full detail. Not
  yet done: loading a real flight plan on the real-nav-data session, an
  automatic first-run wizard, and an explicit "airspace" opt-in control.
- `androidbridge/charts.py` (repo root) — §3.4/§3.5's approach-plate PDF
  "click to load," confirmed on real hardware (2026-09-25):
  `update_index(root)` / `list_charts(root, ident)` /
  `fetch_chart_path(root, ident, index)`, thin wrappers around
  `datasrc.dtpp` - same "turned out to already be stdlib-only" story as
  `datasrc.faa` (`urllib`/`xml.etree`/`pickle`, no extra packaging), staged
  automatically as part of the `datasrc` `brainPackages` entry §3.5 added.
  `BrainBridge.openPdf` is the Android-native replacement for `dtpp
  .open_with_os_default` (`os.startfile`/`subprocess.Popen`, meaningless
  here): a `content://` URI via a new `FileProvider` (`AndroidManifest.xml`
  + `res/xml/file_paths.xml` - required since a raw `file://` URI is
  blocked crossing into another app since Android 7) fed into
  `Intent.ACTION_VIEW`. New `nav/ChartsScreen.kt` (reachable from the
  self-test screen): fetch the chart index (~16MB, once per AIRAC cycle),
  list an airport's charts, tap one to fetch + open - the Android-native
  equivalent of desktop's `main._open_selected_chart` (every non-`stack`
  layout's AUX>Charts ENT key), not the `stack` layout's separate inline
  `pypdfium2` rasterization (still desktop-only, its native-wheel-on-Android
  question is still open). Not yet wired into the GNS's own AUX>Charts
  page - that page isn't rendered on Android at all (§3.6 deferred it,
  same "needs live data this module doesn't have" reasoning as AUX
  Weather) - this is a standalone verification screen. Confirmed
  end-to-end on the real Pixel 9: index fetch, chart list for a real
  airport, tap opens the PDF in the device's own viewer via the system
  chooser.
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
11. ~~§3.6 draw-command-list instrument rendering, sixth slice~~ **Done
    (2026-09-25)**: Flight Plan Catalog, all six WPT search pages, all
    eight NRST pages, and AUX's Nav Data/Trip Planning/Utility/Setup tabs
    are real instrument graphics now too, confirmed on the real Pixel 9 -
    Flight Plan Catalog's 9 empty slots, WPT Airport's empty char-cell
    entry, NRST Nearest APT's "none within range", and all four AUX tabs'
    correct field values all render correctly. AUX Weather/Charts stay
    unported (need live `datasrc.wx`/PDF data, out of scope like the
    six-pack decision).
12. ~~§3.6 draw-command-list instrument rendering, seventh slice~~ **Done
    (2026-09-25)**: all 8 modal dialogs (PROC, Activate Leg?, Remove/
    Restart confirm, DTO menu, Direct-To, Message page, FPL menu,
    Airspace info) are real instrument graphics now too, drawn as an
    overlay over whatever page body already rendered. Confirmed on the
    real Pixel 9: the MSG-key Message dialog shows the amber box chrome,
    "MESSAGES" title, and "no messages" correctly, validating the shared
    overlay pipeline the other 7 dialogs reuse (they're unit-tested but
    not individually confirmed on-device yet - most need an approach or
    flight-plan row the synthetic demo db doesn't support well). Found and
    fixed a real `TrainerSession.tick()`/Message-page ordering conflict
    getting there - see `ANDROID_PORT_PLAN.md` §3.6 for the detail.
13. ~~§3.6 draw-command-list instrument rendering, eighth and final
    slice~~ **Done (2026-09-25)**: the track-up moving map is real
    instrument graphics now too - flight-plan legs/waypoint symbols, DTO
    course, nearby airport/VOR/NDB symbols, ownship triangle (Class B/C/D
    airspace overlays and label declutter deliberately deferred). Found
    and fixed a real Compose bug getting there: `Canvas` doesn't clip to
    its own bounds by default, so the range-ring circle bled into the
    debug text below the map on first build - fixed at the root with
    `Modifier.clipToBounds()` in `InstrumentCanvas.kt`, applied to every
    instrument. Confirmed on the real Pixel 9 after the fix: range rings
    correctly clipped, ownship triangle, ALFA waypoint, and the
    active-leg magenta line all render correctly. **§3.6 is complete.**
14. ~~§3.2 touch/rotary controls, first pass~~ **Done (2026-09-25)**,
    confirmed on the real Pixel 9 after two design iterations from
    real-device feedback: every control now sits on the instrument it
    belongs to (GNS bezel buttons + FMS knob on the GNS canvas, AP panel
    buttons + ALT SEL/VS/IAS knobs on the AP panel, a small generic
    fallback only for COM/NAV/XPDR, which have no dedicated instrument
    yet). See `input/TouchControls.kt`'s entry above for the full detail
    and the three real bugs fixed along the way. Ergonomics are
    explicitly first-draft, called out by the person testing it as
    "good enough for now, fix later."
15. ~~§3.5 on-device nav data acquisition, first pass~~ **Done
    (2026-09-25)**, confirmed on the real Pixel 9: real FAA CIFP+NASR+ARTCC
    data downloads (`nav/NavDataScreen.kt`) and loads
    (`demo_session.new_real_session`) in place of the synthetic demo
    database. Two real problems caught and fixed along the way - a
    footprint mistake (the "airspace" kind is a ~150MB one-time download,
    not the ~170KB "cheap" size a first draft assumed, caught before any
    real-device testing) and a real crash (the debug panel's `tick_line`
    format string didn't handle a real session's flight-plan-less
    `NavState.xtk_nm` being `None`, found via on-device testing) - see
    `androidbridge/nav_update.py`'s entry above and
    `ANDROID_PORT_PLAN.md` §3.5 for the full detail.
16. ~~§3.4/§3.5 approach-plate PDF "click to load"~~ **Done (2026-09-25)**,
    confirmed on the real Pixel 9: the "hand plates off to an Android
    Intent for the system PDF viewer" fallback §3.4 flagged (instead of
    porting the `stack` layout's separate inline `pypdfium2`
    rasterization, still an open question) is real now - the
    Android-native equivalent of desktop's `main._open_selected_chart`.
    See `androidbridge/charts.py`'s entry above for the full detail.
    Confirmed end-to-end: chart index fetch, chart list for a real
    airport, tap opens the PDF in the device's own viewer via the system
    chooser.
17. **Do next**: touch control ergonomics (knob feel, hit-target sizing,
    visual press feedback) need a real design pass. §3.5 follow-ups:
    loading a real flight plan on the real-nav-data session (currently
    starts with none) so the pages/dialogs that need real airports/
    procedures can be confirmed on-device (PROC, WPT/NRST with matches,
    NAV/COM, VNAV armed), an automatic first-run wizard instead of a
    manual test-screen button, and an explicit "airspace" opt-in control.
    §3.4/§3.5 follow-up: wire the Charts flow into the GNS's own
    AUX>Charts page once that page is rendered, instead of the standalone
    verification screen. (The six-pack gauge cluster is not planned for
    Android at all - decision, 2026-09-24, `ANDROID_PORT_PLAN.md` §7.)
