# WORKING.md

Task tracker for **octavi-ifr-trainer**. Newest status at the top of each list.
`[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped.

Last updated: 2026-09-18 (stack layout: Phase 0-5 complete + several
follow-ups, + NAV1/GPS-CDI wiring fix + PROC Load?/Activate? fix,
860 tests green)

Prior task history (M0–M16, the initial build through the playtest-fix rounds)
is archived in `archive/WORKING-2026-09-11.md`, refreshed here to start a clean
slate for the next project. `FINDINGS.md` remains the permanent log of every
bug fixed against the Pilot's Guide/playtest feedback (F1–F35 so far) and is
**not** archived — keep adding to it as before.

---

## Android port (started 2026-09-23, branch `feature/android-port`)

Plan: `docs/ANDROID_PORT_PLAN.md`. Chaquopy hybrid (Kotlin/Compose shell +
the existing Python brain unmodified), Octavi IFR-1 required for v1,
landscape then portrait. First target device: Pixel 9.

- [x] Plan doc written and decisions recorded (tech stack, IFR-1-required,
      both orientations) — `docs/ANDROID_PORT_PLAN.md`.
- [x] `androidbridge/` package: `TrainerSession` (thin `GpsNav` + `SimModel`
      wrapper returning plain dicts) + `selftest.py` (the exact entry point
      Kotlin's `BrainBridge.kt` calls) — pure Python, covered by
      `tests/test_androidbridge.py`, runs under the normal desktop suite.
- [x] `android/` Gradle/Kotlin project scaffold: Chaquopy wired to stage the
      curated brain-module list from the repo root at build time (single
      source of truth, no duplication), a Compose self-test screen, the
      §3.6 draw-command-list contract shape (`DrawCommand.kt` +
      `InstrumentCanvas.kt`, replay mechanism only — no real layout data
      yet), `UsbHidInput.kt`. **Builds and runs on real hardware
      (2026-09-24)** — see `android/README.md` for exact status.
- [x] **Wireless debugging confirmed working (2026-09-25)**: paired and
      connected to the real Pixel 9 over Wi-Fi (`adb pair`/`adb connect`,
      §3.1 step 0) — `192.168.1.133:44523`, model `Pixel_9` (codename
      `tokay`), Android build `ro.build.version.release`=17. Shell access
      verified (`adb shell getprop ...`). This was the prerequisite for
      running the §3.1 spike without the IFR-1's OTG adapter tying up the
      phone's only USB-C port — confirmed workable, not just planned.
- [x] **§3.1 hardware spike run (2026-09-25)**, real Pixel 9 + real IFR-1 +
      USB-C OTG adapter, over the wireless adb connection above. Result:
      **every button + the outer knob work as standard input** —
      `/dev/input/eventN` shows up as a generic evdev gamepad, name
      "Octavi IFR1", with a confirmed 1:1 `BTN_*` mapping for all 12
      testable buttons (`DCT`→`BTN_WEST`, `MNU`→`BTN_Z`, `CLR`→`BTN_TL`,
      `ENT`→`BTN_TR`, `SWAP`→`BTN_TL2`, `KNOB`→`BTN_TR2`,
      `AP/CDI`→`BTN_THUMBR`, `HDG/OBS`→raw `0x13f`,
      `NAV/MSG`→`BTN_TRIGGER_HAPPY`, `APR/FPL`→`_HAPPY2`,
      `ALT/VNAV`→`_HAPPY3`, `VS/PROC`→`_HAPPY4`) and the outer knob as
      `REL_DIAL` ±1/detent — no raw USB code needed for any of that. **The
      inner knob produces zero evdev events**, confirmed twice with the
      capture pipeline itself verified working both times. Full writeup:
      `ANDROID_PORT_PLAN.md` §3.1 "Spike result".
- [x] **`input/UsbHidInput.kt` written (2026-09-25)**: rather than split
      logic between `InputDevice`/`KeyEvent` (buttons + outer knob) and a
      separate raw-HID path (inner knob only), it claims the whole HID
      interface with `force = true` (detaching `usbhid` entirely, same as
      `hidapi` does implicitly on desktop) and decodes every control - mode,
      all buttons, both knobs - through one code path mirroring `ifr1.py`'s
      `Mode`/`State`/`Event`/`LAYOUT` field-for-field. Also added the
      manifest's `usb.host` feature + `USB_DEVICE_ATTACHED` intent-filter +
      `res/xml/usb_device_filter.xml` (VID/PID). **Compiles cleanly
      (2026-09-24)**, part of a real successful `assembleDebug` build.
- [x] **Android Studio build + on-device self-test confirmed (2026-09-24)**:
      fixed six real build-toolchain errors (Compose-compiler plugin
      version, JDK/Gradle version chain, AGP-vs-Gradle-9 incompatibility,
      a missing Gradle task dependency, a missing launcher icon resource —
      see `android/README.md` "Status" and commit `1976a11` for the full
      chain) and got `BUILD SUCCESSFUL`. Installed + launched the APK on the
      real Pixel 9; self-test screen confirmed: `"OK: navmath/navdata/
      gpsnav/sim_model/androidbridge imported and ticked -
      to='BRAVO' pos=(39.9006,-74.0000)"` — §3.4 on-device confirmation
      done, not just a documented plan.
- [x] **`UsbHidInput` runtime-confirmed on real hardware (2026-09-24)**:
      wired into a disposable test harness (`input/UsbHidTestScreen.kt`,
      reachable from the self-test screen), run against the real Pixel 9 +
      real IFR-1 over USB-C OTG. `claimInterface(force = true)` **succeeds**
      (status `CONNECTED`, no `usbhid` fallback needed). Buttons, outer
      knob, AND the inner knob (the reason this path exists — produces zero
      evdev events under `usbhid`, per §3.1's original finding) all decode
      correctly through the one raw-HID code path, byte-for-byte matching
      `ifr1.py`'s `LAYOUT` (verified via on-screen raw frame hex: inner CW
      `inner=1`/byte6=`0x01`, inner CCW `inner=-1`/byte6=`0xFF`, `KNOB`
      button toggling byte2 bit `0x02`). Full writeup:
      `ANDROID_PORT_PLAN.md` §3.1 "Runtime-confirmed". §3.1's hard project
      gate has passed — IFR-1 support on Android v1 is fully confirmed, no
      `InputDevice`/`KeyEvent` fallback needed.
- [x] **Phase 1 core loop confirmed on real hardware (2026-09-24)**: real
      IFR-1 events now route through `main.route_event` into a real `World`
      (gns/sim/radios/ap/baro/shift-latch state), ticked ~30 Hz on a
      background thread with lifecycle-aware suspend/resume
      (`world/TrainerLoop.kt`). `androidbridge/session.py` was rewritten to
      wrap `World`/`route_event` directly instead of reimplementing the
      mode-routing table — see `androidbridge/__init__.py`'s corrected
      docstring (the old "main.py can't be reused, it imports pygame"
      rationale was wrong: `main.py`'s module-level imports are stdlib-only).
      Verified end-to-end on the real Pixel 9 + real IFR-1: COM1/COM2 knob
      tuning, NAV1/NAV2 OBS shift-latch, AP-row buttons, XPDR all confirmed
      correct via a live debug-text screen (`world/Phase1LoopScreen.kt`).
      FMS mode's own dispatch path (DCT/MNU/CLR/ENT) reuses the exact same
      `route_event` call, but isn't independently visually confirmed yet —
      the debug panel doesn't surface flight-plan/cursor state, that's
      deferred rendering-pass work.
      Two real on-device bugs found and fixed along the way: (1) `ifr1.py`
      had a top-level `import hid` that raised `SystemExit` on any `import
      ifr1` without hidapi installed (true under Chaquopy by design) -
      crashed even the Phase 0 self-test; made lazy, guarded in
      `IFR1.__init__`/`find_devices()` instead. (2) Chaquopy's Java
      `List`/`ArrayList` → Python marshaling for a `callAttr` argument
      produced an object `tuple()`/`list()` couldn't consume
      ("TypeError: 'ArrayList' object is not iterable" - real crash on every
      knob turn); worked around by passing `pressed`/`released`/`long_press`
      as comma-joined strings across the Chaquopy boundary instead of lists.
      Full writeup: `ANDROID_PORT_PLAN.md` §6 Phase 1 / §3.1.
- [x] **§3.6 rendering, first slice — HSI head confirmed on real hardware
      (2026-09-24)**: new `render_commands.py` ports `draw_hsi_head` and its
      helpers (`_cdi_card`/`_card_geometry`/`_gs_scale`/`_ident_dot`/`_dial`/
      `_panel_box`), same trig/layout math as `render.py`, appending encoded
      command strings instead of calling `pygame.draw.*`. Crosses to Kotlin
      as a newline/pipe-delimited **string** (not a list/`PyObject` — the
      same Chaquopy List-marshaling bug found in the core-loop milestone
      above would apply to the reverse direction too). `InstrumentCanvas
      .kt`'s `Text` case (a TODO since Phase 0) is now implemented via
      `nativeCanvas.drawText`. Verified on the real Pixel 9 + real IFR-1:
      HSI dial/compass card/heading readout render correctly, and NAV1's
      shift-latched knob live-rotates the course pointer on screen.
      AP panel graphics, moving map, and GNS softkey/page UI are still
      plain text / not yet ported — deliberately one instrument at a time.
      (The six-pack gauge cluster is not planned for Android at all — see
      the 2026-09-24 decision below.) Full writeup:
      `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.6 rendering, second slice — AP panel confirmed on real hardware
      (2026-09-24)**: `render_commands.py` ports `draw_ap_panel` (S-TEC 55X:
      RDY lamp, HDG/NAV/APR/REV/ALT/VS mode row, VS window, HDG BUG/ALT
      SEL/IAS SET info box). Surfaced and fixed a systemic text-positioning
      bug the HSI slice's loose spacing had hidden: Android's `Paint
      .drawText` positions by baseline, `render.py`'s pygame text by
      top-left - ad hoc per-call offsets didn't generalize to the AP
      panel's tightly-packed info box (overlapped badly). Fixed at the
      root in `InstrumentCanvas.kt` (`y - paint.ascent()`, once) rather
      than patching every call site; `render_commands.py`'s `y` values are
      now plain top-anchored numbers matching `render.py`'s own arguments
      directly. Verified on the real Pixel 9: all six mode keys, RDY lamp,
      VS readout, and info box render correctly with no overlap, matching
      the plain-text debug panel's values exactly. Full writeup:
      `ANDROID_PORT_PLAN.md` §3.6.
- [x] **Decision (2026-09-24): the six-pack gauge cluster is out of scope
      for Android, not just deferred.** IFR training is the point
      (GPS/HSI-primary panel), not a round-gauge steam-panel trainer;
      Android v1's instrument set is HSI + AP panel + GNS pages + moving
      map. §3.6 rendering passes should not budget time toward it. Full
      writeup: `ANDROID_PORT_PLAN.md` §7.
- [x] **§3.6 rendering, third slice — GNS default NAV page confirmed on
      real hardware (2026-09-24)**: `render_commands.py` gains
      `gns_commands`, porting `_gns_unit`'s chrome (header, no-bezel flat
      screen frame) + `_draw_nav_default` (active-leg symbol/from/to, OBS
      annunciation, DTK/TRK/DIS/GS/ETE/XTK rows) + `_turn_advisory` +
      `_cdi_strip` + `_bezel_labels`. **Only the default NAV page renders**
      — `_gns_unit` is a ~15-page router (Map/FPL/VNAV/NAV-COM/WPT/NRST/AUX
      + 8 modal dialogs), all deliberately deferred to later passes.
      `route_event` already dispatches real FMS bezel-key input correctly
      (proven by the core-loop milestone), so the page state does change
      server-side when the FMS knob turns — the screen just always shows
      the default-NAV layout regardless until those other pages land. Got
      the top-anchored-`y`/`_centered_y()` text convention right from the
      start this time. Verified on the real Pixel 9 with the demo flight
      plan (ALFA→BRAVO→CHAR) active: header, active-leg line, DTK/TRK/DIS/
      GS/ETE/XTK rows, and CDI strip all render correctly, matching the
      plain-text debug panel's values exactly. Full writeup:
      `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.6 rendering, fourth slice — Flight Plan page confirmed on real
      hardware (2026-09-25)**: `gns_commands` now dispatches its body by
      `cursor.page_name` (`_nav_default_body`/`_fpl_body`) instead of
      always showing the default page; `_fpl_body` ports `_draw_fpl`/
      `_fpl_tag`/`visible_fpl_rows` (waypoint list, active-leg marker,
      per-leg DTK/DIS, procedure headers, tags, DTO status line, scroll
      window). Read-only — the in-place ident-edit buffer isn't rendered
      yet. Found and fixed a real test-fixture bug while writing this
      slice's coverage: the synthetic nav db was missing a waypoint
      (`CHAR`) the test flight plan referenced, silently truncating the
      loaded plan and producing a false rendering-bug signal — the fix was
      in the fixture, not `gns_commands`. Verified on the real Pixel 9:
      header, waypoint list (ALFA past, `-> BRAVO` active leg in magenta,
      CHAR upcoming), per-leg DTK/DIS, and CDI strip all render correctly.
      Full writeup: `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.6 rendering, fifth slice — VNAV and NAV/COM pages confirmed on
      real hardware (2026-09-25)**: `gns_commands` now also dispatches
      `"VNAV"` to `_vnav_body` (ports `_draw_vnav_page`: TARGET/TGT ALT/VS
      PROFILE fields, VNV ARMED/OFF, then DIS/TOD/REQ VS/DEV status rows
      once armed) and `"NAV/COM"` to `_navcom_body` (ports
      `_draw_navcom_page` + `_draw_freq_rows`: airport role + ident, then
      its tunable frequency list). Verified on the real Pixel 9: VNAV shows
      the unarmed "no active VNAV target" fallback correctly; NAV/COM shows
      "no airport in flight plan" correctly, since the synthetic demo db
      has no airports — both are real `_vnav_body`/`_navcom_body` code
      paths, not stubs, just the no-data branch of each. Full writeup:
      `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.6 rendering, sixth slice — Flight Plan Catalog, WPT, NRST, and
      AUX (Nav Data/Trip Planning/Utility/Setup) confirmed on real hardware
      (2026-09-25)**: `gns_commands` now dispatches by `cursor.group_name`
      as well as `page_name` - `_fpl_catalog_body` (ports
      `_draw_fpl_catalog`), `_wpt_body` (ports `_draw_wpt_page` - all six
      Airport/Airport Runway/Airport Freq/Intersection/NDB/VOR sub-pages,
      the char-cell ident entry with underline cursor), `_nrst_body` (ports
      `_draw_nrst_page` + `_draw_nrst_airports`/`_draw_nrst_facility`/
      `_draw_nrst_airspace` - all eight NRST sub-pages), and `_aux_body`
      (ports `_draw_aux_navdata`/`_draw_aux_trip`/`_draw_aux_utility`/
      `_draw_aux_setup` - Weather/Charts intentionally excluded, they need
      a live `datasrc.wx` cache read / PDF rendering, out of scope same as
      the six-pack decision). `gns_commands` gained `t`/`baro_inhg`
      parameters for the Utility timer and Setup baro readout. Verified on
      the real Pixel 9: Flight Plan Catalog (9 empty slots), WPT Airport
      (empty char-cell entry + hint), NRST Nearest APT ("none within
      range" - the demo db has no airports), and all four AUX tabs
      (Nav Data's SOURCE/CYCLE/EFF/EXP + APT/VOR/NDB/WPT/AWY counts, Trip
      Planning's ALFA->CHAR totals, Utility's live flight timer, Setup's
      UNIT/CDI SRC/BARO) all render correctly. Only Map and the modal
      dialogs (PROC/DTO/confirms/message page) remain unported.
      Full writeup: `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.6 rendering, seventh slice — all 8 modal dialogs confirmed on
      real hardware (2026-09-25)**: `gns_commands` gained a `_gns_dialog`
      overlay, drawn last (over whatever page body/turn-advisory/CDI
      strip/bezel already rendered), dispatched in the exact priority order
      `_gns_unit` checks - PROC, Activate Leg?, Remove/Restart confirm, DTO
      menu, Direct-To, Message page, FPL menu, Airspace info - all sharing
      a new `_dialog_box` chrome helper. `gns_commands` gained
      `messages`/`show_messages` params (`World.gns` message queue /
      `World.show_msg`, the MSG key toggle - already routed correctly by
      `route_event` since it lives on the same `World`). One real design
      snag found while wiring this: `TrainerSession.tick()` already drains
      `gns.messages` for the debug-panel snapshot (its own "read=cleared"
      policy), but desktop's Message page calls `gns.peek_messages()`
      (non-destructive) - by the time `render_gns()` runs after `tick()`,
      the queue would already read empty. Fixed by stashing the drained
      batch as `self._last_messages` in `tick()` for `render_gns()`'s
      dialog to reuse, rather than re-reading an already-emptied queue.
      Verified on the real Pixel 9: the Message dialog (MSG key) shows the
      amber box chrome, "MESSAGES" title, and "no messages" correctly over
      the default NAV page - confirms the whole overlay pipeline (z-order,
      `World.show_msg` wiring, shared box chrome) the other 7 dialogs
      reuse; those 7 are unit-tested but not individually confirmed
      on-device (PROC/DTO need an approach or a loaded plan the synthetic
      demo db doesn't support well). Full writeup: `ANDROID_PORT_PLAN.md`
      §3.6.
- [x] **§3.6 rendering, eighth and final slice — the moving map confirmed
      on real hardware (2026-09-25)**: new `render_commands.map_commands`
      ports render.py's `_map`/`_ownship_symbol`/`_hold_track_points`/
      `_pt_symbol_points`/`_draw_vor_symbol`/`_draw_ndb_symbol`/
      `_draw_airport_symbol` plus main.py's `_nearby` helper (folded in,
      not shared elsewhere) - track-up, own-ship-offset-toward-bottom-third
      projection, range rings, flight-plan legs + waypoint symbols (MAP X,
      hold racetrack, procedure-turn chevron, FAF/plain dots), the DTO
      course line, nearby airport/VOR/NDB symbols, and the ownship
      triangle. Deliberately deferred: Class B/C/D airspace polygon
      overlays and label-overlap declutter (needs real font-metrics text
      width this module doesn't have without pygame - every label just
      draws unconditionally instead). New `TrainerSession.render_map()` /
      `demo_session.render_map()` / `BrainBridge.renderMap()` / a fourth
      `InstrumentCanvas` box in `Phase1LoopScreen.kt`, same wiring pattern
      as every other slice. Found and fixed a real on-device rendering bug
      getting there: Compose's `Canvas` does **not** clip its drawing to
      its own layout bounds by default (unlike `render.py`'s explicit
      `pygame.set_clip`) - the map's outer range-ring circle, sized against
      the box height, legitimately extends past the box's top/bottom edges
      and bled into the debug text underneath on first build. Fixed at the
      root in `InstrumentCanvas.kt` with `Modifier.clipToBounds()`, applied
      unconditionally to every instrument (not just the map), rather than
      relying on the invariant that every other instrument's geometry
      happens to stay in-bounds. Verified on the real Pixel 9 after the
      fix: range rings correctly clipped, ownship triangle, ALFA waypoint
      dot + label, and the active-leg magenta line all render correctly
      inside the panel. **§3.6 is now complete** - every GNS page/dialog
      and the moving map are real instrument graphics. Full writeup:
      `ANDROID_PORT_PLAN.md` §3.6.
- [x] **§3.2 touch/rotary controls, first pass, confirmed on real hardware
      (2026-09-25)**: new `android/.../input/TouchControls.kt` (`RotaryKnob`/
      `BezelButton` primitives, `GnsBezelOverlay`, `ApPanelOverlay`,
      `FmsKnobs`/`ApKnobs`, `RadioControlPanel`) feeds the exact same
      `BrainBridge.dispatchEvent` call real IFR-1 events use - a second,
      independent input source, not a separate code path. Iterated twice
      on real-device feedback: v1 put every control (including the GNS's
      own softkeys, relabeled from the physical IFR-1's AP-row names) in
      one generic panel below all instruments - functional but you
      couldn't see the GNS while pressing its own buttons, and the AP-row
      buttons looked like they belonged to the GNS when `main.py`'s
      `_FMS_BEZEL` only *reuses* them for GNS softkeys in FMS mode. v2
      anchors each control to the instrument it belongs to instead: the
      GNS's real bezel buttons (`render_commands.gns_commands`'s two key
      rows, now the *real* 530 set - CDI/OBS/MSG/FPL/VNAV/PROC +
      RNG/D>/MENU/CLR/ENT, replacing a prior ad hoc mixed 8-key row that
      included a fake "CRSR" key) sit directly on the GNS canvas with a
      dedicated FMS knob beside it; the AP panel's 5 wired buttons
      (HDG/NAV/APR/ALT/VS - REV has no IFR-1 mapping at all, matching real
      hardware) sit on its own canvas with ALT SEL/VS/IAS knobs beside it;
      only COM/NAV/XPDR (no dedicated radio-stack instrument exists yet)
      keep a small generic fallback cluster. New `TrainerSession
      .adjust_map_range`/`.adjust_vs`/`.adjust_ias_target` bypass
      `route_event` entirely for controls that either aren't
      `route_event`-routed buttons at all (RNG - desktop's own
      `ui["map_range"]` UI-loop state) or are real-hardware shift-latch-
      shared knobs that don't work as a touch gesture without a visible
      toggle state (VS/IAS - now two independent knobs). Fixed three real
      on-device bugs from feedback: knob sensitivity (doubled
      degrees-per-detent), the RNG key redrawn+re-hit-tested as a real
      two-sided rocker (was tap=out/long-press=in on one key - not
      discoverable), and a genuine layout overflow (AP/GNS knobs placed
      beside their 520dp-wide panel ran past the screen's right edge in
      this vertical-only-scrolling layout - moved below the panel
      instead). Full writeup: `ANDROID_PORT_PLAN.md` §3.2.
- [ ] **Do next**: touch control ergonomics need real design/UX work
      (acknowledged as "good enough for now, fix later" after this pass -
      knob feel, hit-target sizing, visual affordance are all first-draft).
      §3.5 (real FAA nav data on-device) would unlock on-device
      confirmation of the pages/dialogs that need real airports/procedures
      (PROC, WPT/NRST with matches, NAV/COM, VNAV armed) which the
      synthetic demo db can't exercise.

---

## Current status (2026-09-19, 860 tests green)

The trainer runs end to end on four layouts (`gps`, `steam`, `stack`,
`dual`), driven by the IFR-1 or keyboard (`stack` also takes mouse input),
validated against the GNS 530(A) Pilot's Guide 190-00181-00 Rev. H. Five
rounds of structured playtesting are complete on `gps`/`steam`/`dual` — round
5 was a clean 40/40 pass. See `FINDINGS.md` F1–F28 for the full list of what
was found and fixed. `stack` is newly built (this pass) and has not yet been
through a playtest round of its own.

---

## `stack` layout — a single-page IFR panel (built 2026-09-17)

**Goal** (user, 2026-09-17): a new layout putting everything relevant to
instrument flight on one page, replacing the current back-and-forth between
GNS pages and separate reference material with one screen — mocked up in
`docs/mockups/stack-layout-mockup.png`. Design was worked out via Q&A before
any code was written; the decisions below are the spec to build against.

### Design decisions (from the 2026-09-17 conversation)

- **Three columns.**
  - **Left**: a tabbed reference panel (WX / MAP / PLATE / SETTINGS), plus
    HDG/IAS/ALT autopilot-bug boxes underneath it.
  - **Middle**: NAV1 and NAV2 as Bendix/King-style round CDI heads (needle,
    glideslope diamond, rotating OBS card — reuse the existing round-VOR-head
    drawing code from the `steam` layout), plus a standalone heading
    indicator with a heading bug below them.
  - **Right**: GNS 530 (top) + GNS 430 + transponder + S-TEC 55X autopilot,
    stacked as one visually continuous column.
- **WX tab**: the same METAR/TAF the GNS AUX>Weather page shows today
  (`datasrc.wx`'s local cache), just surfaced here instead.
- **MAP tab**: the existing track-up moving map, unchanged, moved into this
  tab.
- **PLATE tab**: a real behavior change — renders the selected approach-plate
  PDF **inline** in the tab (rasterized), instead of today's hand-off to the
  OS's PDF viewer. Needs a new dependency (below) since nothing in this repo
  or the underlying system can rasterize a PDF today.
- **SETTINGS tab**: application-level settings a pilot would plausibly want to
  change *in flight* rather than only via a CLI flag at launch — explicitly
  wind / winds-aloft and time-warp to start. (Not the GNS's own CDI/Alarms
  Setup page — that stays on the GNS itself, where it already works.) Treat
  this tab as the general home for "settable at launch only today" trainer
  options going forward, not just these two.
- **HDG/IAS/ALT boxes**: autopilot target bugs (heading bug, IAS set-point,
  altitude preselect), shown *and* directly editable here (click + scroll or
  +/- controls) — not read-only current-value readouts.
- **GNS 530/430 visual style, in this layout only**: drop the photorealistic
  faceplate SVG bezel used by `gps`/`dual`; draw them as clean vector panels
  in the same visual language already used for the transponder and S-TEC
  boxes, so the whole right-hand column reads as one consistent instrument
  stack.
- **GNS frequency annunciation** (a real, standing gap, not layout-specific):
  the real GNS 530/430 screen shows COM/NAV active+standby frequencies inline
  at the top of the display; this trainer's GNS screen never has. Add it —
  decide during implementation whether this belongs on all layouts (it's a
  faithfulness fix, arguably not `stack`-specific) or just `stack`.
- **FMS pages unchanged**: the GNS boxes in `stack` still show their normal
  pages (Default NAV, Flight Plan, DTO, PROC, MSG, etc.) and respond to the
  same bezel keys/knobs as `gps`/`dual` today — `stack` changes their skin and
  adds frequency annunciation, it doesn't remove any existing functionality.
- **Mouse input is new.** Nothing in the trainer handles mouse events today
  (keyboard + IFR-1 only). `stack` introduces real click support (tabs,
  SETTINGS fields, AP bug boxes) — scope it to this layout; no requirement to
  retrofit mouse support onto `gps`/`steam`/`dual`.
- **New dependency**: `pypdfium2` (small, MIT-licensed, pure-wheel — no
  system `poppler` needed, which this machine doesn't have) for PDF→image
  rasterization, PLATE tab only.
- **Name + access**: `--layout stack`; added to the `L` keyboard cycle
  (`gps → steam → stack → gps`, with `dual` staying a 4th stop only under
  `--dual`, unchanged).
- **Window size**: `stack` has meaningfully more on-screen content than
  1000×640 comfortably fits — resize when this layout is active; leave the
  other layouts' window size alone.

### Task checklist

**Phase 0 — scaffolding**
- [x] Add `pypdfium2` to `requirements.txt` / `pyproject.toml`
- [x] `--layout stack` plumbed through `config.py` / `main.parse_args` /
      the `L`-key cycle (`main._next_layout`)
- [x] Window resizes for `stack` (other layouts unaffected)
- [x] Mouse input: `pygame.MOUSEBUTTONDOWN` handling in `main.run()`'s event
      loop, routed only when `stack` is active (`main._on_stack_click`); hit
      rects registered every frame in `Renderer._stack_hit`

**Phase 1 — right column: the avionics stack**
- [x] GNS 530/430 drawn as clean vector panels (no bezel SVG) for `stack`
      (`_gns_unit(..., no_bezel=True)`)
- [x] COM/NAV active+standby frequency annunciation on the GNS screen
      (`_gns_unit(..., freq=(com, nav, label))`)
- [x] Transponder + S-TEC AP boxes restyled to sit directly under the GNS
      pair as one continuous column (`Renderer._stack_xpdr` + `draw_ap_panel`)
- [x] Confirm FPL/DTO/PROC/MSG/etc. still route correctly through the
      existing bezel-key handling with the new visual skin (unchanged code
      path - `no_bezel` only affects `_gns_unit`'s drawing, not input)

**Phase 2 — middle column: instrument cluster**
- [x] NAV1/NAV2 round CDI+GS+OBS heads, reusing `draw_nav_head`/
      `_vor_cdi_face`
- [x] Standalone HDG indicator with a heading bug - `draw_hdg_indicator`,
      sharing the six-pack's HDG card via the extracted `_hdg_card` helper
      rather than duplicating it

**Phase 3 — left column: tabbed panel**
- [x] Tab framework: click to switch WX/MAP/PLATE/SETTINGS
      (`Renderer._stack_tabs`)
- [x] WX tab (reuse `_draw_aux_weather`'s data path)
- [x] MAP tab (reuse `_map`)
- [x] PLATE tab: `pypdfium2` rasterization of the selected `datasrc.dtpp`
      chart, rendered inline (`main._open_stack_plate` fetches+rasterizes
      off-thread; `Renderer._draw_stack_plate` only reads the cache)
- [x] SETTINGS tab: wind + time-warp control, click-driven
      (`Renderer._draw_stack_settings`). Scoped down from the original
      "wind/winds-aloft" idea to the uniform `--wind` value only
      (`World.sim.set_wind`) - a full winds-aloft *profile* editor (per
      altitude, multiple rows) didn't fit this pass; still on the backlog
      below if wanted later.

**Phase 4 — AP bug boxes**
- [x] HDG/IAS/ALT boxes wired to `Autopilot.heading_bug` / `World.ias_target`
      / `Autopilot.alt_preselect`, click editable (`Renderer._stack_ap_bugs`
      +/- buttons -> `main._on_stack_click`). Click-only, not scroll - a
      `MOUSEWHEEL` alternative wasn't added this pass.

**Phase 5 — tests + docs**
- [x] Headless render smoke tests for `stack` (same pattern as the other
      layouts in `tests/test_render.py`) - single-unit, `--dual`, and all
      four tabs
- [x] Mouse hit-testing unit tests (synthetic `MOUSEBUTTONDOWN` events) -
      tab switch, AP bugs, wind, time-warp
- [x] `README.md`: document the new layout, its mouse controls, and the new
      dependency
- [x] `ARCHITECTURE.md`: move the §8 "planned" write-up (added 2026-09-17) to
      the normal module-map/decisions sections once real, matching how every
      other layout is documented

---

## Backlog / ideas (unscheduled)

- Non-US coverage via a Navigraph FMS Data API backend (paid; no redistribution)
- Autopilot mode logic driven by the IFR-1 AP row (HDG/NAV/APR/ALT/VS arm+capture)
- GNS 430 / GTN 650 screen variants beyond what `stack` adds
- Failure injection (VOR out, GPS LOI) for partial-panel practice
- Record/replay of an IFR-1 session for regression tests of `gns530`
- `stack` SETTINGS tab: a real winds-aloft *profile* editor (per-altitude rows,
  not just the uniform `--wind` value the click UI edits today)
- `stack` AP-bug boxes: `MOUSEWHEEL` support alongside the existing +/- click
  buttons (WORKING.md's original "click + scroll" idea - only click shipped)
- `stack` layout: its own playtest round, same structured format as
  `gps`/`steam`/`dual` got (F1-F28) - not yet run

## Side project — "faa2xp": back-port FAA data into X-Plane (done, in-repo)

**Goal:** give a *running X-Plane 12 install* current US nav data (frequencies,
navaids, approaches) from the free FAA CIFP, without a Navigraph subscription.
X-Plane's bundled data is stuck at AIRAC 2406. Built 2026-09-20 as the `faa2xp/`
subpackage (not a separate repo); see ARCHITECTURE §5.4 and README "X-Plane
data back-port". Installed on the author's X-Plane 12 on 2026-09-21: it starts
cleanly, no navdata errors in `Log.txt`, and FDK shows the new 116.85 MHz.
Accepted as good; open items below were not checked.

- [x] `python -m faa2xp build|install|restore|status` - stage, install (dry run
      unless `--yes`), reversible via manifest + `.faa2xp-backup/`
- [x] `earth_nav.dat` (VOR/VORTAC/TACAN/DME/NDB/LOC/GS), `earth_fix.dat`,
      `earth_awy.dat`, `earth_hold.dat` merged over the default files
- [x] `FAACIFP18` copied into `Custom Data/`; X-Plane overrides terminal
      procedures per US airport from it. Per Laminar's "Navdata in X-Plane 11
      and 12", `Custom Data/earth_*.dat` replace the base layer wholesale, so
      install also copies the default `CIFP/` (non-US procedures) and restamps
      `earth_mora/msa` to the cycle (first attempt omitted `CIFP/` and X-Plane
      refused to start: "No Procedures found in 'CIFP' folder")
- [ ] Not confirmed in the sim: the FAA procedures actually win (e.g. HADCI
      hold at 0G7 reads 7.4 deg / 3900 ft, old data 7.0 / 3400), an ILS
      tunes/captures, a non-US airport (CYYZ/EGLL) still lists procedures
- [ ] `earth_mora/msa` are still 2406 data with only the header cycle changed
- [ ] ILS/LOC elevation is the airport elevation (CIFP P.I has no localizer
      elevation); ILS-DME, markers, LPV/GLS path points are kept from 2406
- [ ] Fix type code (7-digit column in `earth_fix.dat`) is a reused constant per
      enroute/terminal; its meaning is undocumented

## Backlog - autopilot fidelity vs the S-TEC 55X POH

`docs/AUTOPILOT_POH_REVIEW.md` compares the POH (local copy `docs/reference/STEC_System55X_POH_4thEd.pdf`,
gitignored) with `autopilot.py` and lists 30+ gaps in priority order. The big ones: VOR/LOC intercept is a
22 deg proportional law rather than the POH's 45 deg / CAP -> CAP SOFT -> SOFT sequence; one 3 deg/s turn rate
for every mode (POH: 90%, GPSS 90-130%); NAV silently flies the GPS DTK (that is GPSS); GS arms on the APR press
and captures at 75% deflection (POH: 5%); no RDY/roll-mode interlock; ALT knob 100 ft (POH 20 ft, +/-360).
Not scheduled - awaiting a decision on order (review sec.5).

## Backlog - deliberately skipped (autopilot review, decided 2026-09-21)

Pilot-selectable intercept angle (R9), control-wheel steering, yaw damper: not wanted. Power-up / pre-flight tests
(POH sec.2): back burner - the trainer always starts airborne, so there is no start-up sequence to hang them on.

## Done log

- **2026-09-24** — F78: moving map declutter + real symbols (requested
  directly: too many small airports cluttering a PVD->KJFK route).
  Checked the real Pilot's Guide's Map Setup page (p.35-36, a real
  documented feature: Large/Medium/Small airport classes, separate VOR/
  NDB groups, per-category range limits). Added `Airport.size_class`,
  reworked `main._nearby()` to range-cap by size class and include NDBs
  (previously `ndb=False` - never shown at all) and VOR-DME/VORTAC
  navaids, and added real symbol-drawing (`_draw_airport_symbol`,
  `_draw_vor_symbol` compass-rose hexagon, `_draw_ndb_symbol` ring)
  replacing the uniform dot. 1059 tests pass.

- **2026-09-24** — F77: the Flight Plan page never scrolled past its
  first screenful (reported directly: `--plan "KBOS PVD KJFK" --approach
  "KJFK I22R"`, a 15-waypoint plan on the 530W's ~11-row screen - the
  whole approach was unreachable). `render._draw_fpl` always drew
  `wps[:cap]`; now scrolls a `top` offset to keep the cursor centred in
  the visible window (same fix the NRST list page already had for the
  identical bug). 1056 tests pass.

- **2026-09-23** — F76: broadened the F74/F75 validation sweep beyond ILS
  to every approach type (RNAV/GPS, RNP, LOC-only, VOR, LDA - ~90
  approaches across ~50 major airports). Found KIND's RNP approach
  H05LZ steering ~35° off its own course onto RW05L's unrelated,
  separate ILS - `GpsNav._approach_vloc_freq`'s "runway -> its ILS"
  auto-tune fired for any approach type landing on an ILS-equipped
  runway, not just the loaded ILS itself. Gated it to the ILS/LOC/
  LDA/SDF route-type family; RNAV/RNP/VOR/NDB approaches no longer
  auto-tune an unrelated ILS at all. Several other large excursions the
  broadened sweep surfaced (KIND H32-Z, KMDW H22LX, KEWR H29-Z, KRSW
  S24, KPDX VOR-A) were traced individually and are not bugs - real
  large course changes on offset/curved-final approaches, or the
  validation script's own simplified FAF/MAP-adjacency assumption not
  holding for procedures with extra legs. 1055 tests pass.

- **2026-09-23** — F75: a randomized closed-loop validation sweep of ILS
  approaches at major-metro airports (requested directly, follow-up to
  F74) found one more real bug: KIAD ILS 19L swung ~172° off course at
  the CDI's GPS->VLOC switch. Root cause distinct from F74 - KIAD's
  110.1 is shared between the two ENDS of the same runway (ISGC/RW19L
  and IIAD/RW01R, exact reciprocals, standard FAA practice), which are
  too close to geometry to disambiguate reliably; ordinary track noise
  flipped the pick mid-approach. Fixed by threading `GpsNav.
  approach_ref` through as a `prefer_ident` hint into
  `RadioReceiver.resolve`/`RadioStack.resolve`, so the GNS's own known
  station always wins over a closer/marginally-better-aligned
  reciprocal-end station. A 28-approach sweep (8 + a follow-up 20)
  across major airports nationwide is clean after the fix. 1054 tests
  pass.

- **2026-09-23** — F74: KJFK ILS/LOC 22R playtest report ("aircraft way
  right chasing the needle, then shoots way left"), reproduced headlessly
  with a real closed-loop `World`/`SimModel` run rather than guessed at.
  Two bugs, both firing at the GPS->VLOC CDI auto-switch near the FAF: (1)
  `radios.py`'s station-resolve picked a course-less navdata duplicate /
  the wrong same-frequency parallel-runway localizer (KJFK's 109.5 serves
  both 22R and 04R) by raw nearest-distance - now ranks by
  (has-course-data, beam-alignment error, distance); (2)
  `autopilot.py`'s coupler fully reset (zeroing wind trim, restarting the
  capture timeline) on any CDI-source label change, not just a genuine
  lateral-mode change - now only resets fully on a real mode change or a
  far-off-centre new reading. Fixed `xtk` holds within ~0.02 nm through
  the switch and to the MAP now, vs. the wild swing before. 1051 tests
  pass.

- **2026-09-23** — F73: cancelling an active Direct-To used a bare CLR
  press (F14's approximation) instead of the Pilot's Guide's documented
  DCT > MENU > "Cancel Direct-To NAV?" > ENT (sec.3 pp.47-48). Added
  `GpsNav._dto_menu`/`_apply_dto_menu` (the Direct-to Options pop-up,
  reusing the existing `FplMenu` machinery) and `render._dto_menu_page`;
  dropped the CLR shortcut. Keyboard `X` (MNU) now reaches this menu while
  the DTO dialog has the keyboard (`main._on_key` previously swallowed
  every unhandled key there). 1047 tests pass.

- **2026-09-20/21** — `faa2xp/` built and installed (see the side-project
  section above): FAA CIFP -> X-Plane 12 `Custom Data`, reversible. F48 fixed a
  trainer CIFP parser bug found on the way (letter-category localizers dropped).
  894 tests pass.

See `archive/WORKING-2026-09-11.md` for the full dated log through M0–M16 and
the "gps"/"steam"/"dual" layouts' initial build, and `FINDINGS.md` for every
playtest-round fix (F1–F29) since.

- **2026-09-17** — `stack` layout built end to end, Phase 0-5 of the
  checklist above: `--layout stack` (+ `L`-cycle, window resize, mouse
  input), the right-hand GNS 530/430 + XPDR + S-TEC 55X column
  (`_gns_unit(no_bezel=True, freq=...)`, `Renderer._stack_xpdr`), the middle
  NAV1/NAV2 + standalone HDG column (`draw_hdg_indicator`, sharing the
  six-pack's card via the new `_hdg_card` helper), and the left
  WX/MAP/PLATE/SETTINGS tab panel + clickable HDG/IAS/ALT AP-bug boxes.
  `pypdfium2` added for the PLATE tab's inline PDF rasterization. 825 -> 834
  tests. Two items scoped down from the original design (see Backlog): the
  SETTINGS wind editor is a single uniform value, not a winds-aloft profile;
  AP-bug boxes are click-only, no scroll-wheel. Not yet playtested.

- **2026-09-18** — F29: WX/PLATE tabs could only ever show the departure
  airport - no click target existed to switch stations, and PLATE had no
  airport strip at all. `_draw_aux_weather`/`_draw_stack_plate` now register
  `wx:airport:N`/`plate:airport:N`/`plate:chart:N` hit rects, dispatched by
  `main._on_stack_click` the same way the tab/bug clicks already were. 837 ->
  839 tests.

- **2026-09-18** — F30: PLATE's chart list ("STR STR STR IAP IAP DP DP DP DP
  DP") was `chart_code`-only, indistinguishable and unbounded; the middle
  column (NAV1/NAV2/HDG) was window-width-wide when `draw_nav_head` only
  ever uses ~440px of it. `_draw_stack_plate` now shows a `plate:filter:CODE`
  chip row over a scrollable full-chart-name list; `_stack_layout`'s middle
  column is now a fixed 440px, freeing the rest for the left tab column. 839
  -> 843 tests.

- **2026-09-18** — F31: 440px still left real dead space, and rendering an
  actual frame to a PNG (rather than guessing from code) found
  `draw_ap_panel`'s VS-window column position assumed a `steam`-width box and
  ran off `stack`'s narrower one, overlapping whatever sat next to it.
  Narrowed the middle column to 380px; `draw_ap_panel` gained
  `show_info=False` (drops its now-redundant side info box, wraps the VS
  window onto its own line instead of overflowing - `steam` unaffected,
  still the `show_info=True` default); HDG/IAS/ALT bugs moved from a
  separate bottom row into `Renderer._stack_hdg_info`, next to the HDG dial
  at the same x NAV1/NAV2 place their own OBS info column. Dropping that
  bottom row also gave the tab content area its height back, and
  `_draw_stack_plate`'s image already sizes off the passed rect, so PLATE
  grows with the column for free. 843 -> 844 tests.

- **2026-09-18** — F32: `draw_hdg_indicator` still centered the dial even
  after F31's info column moved to a left-biased x - two independently
  computed geometries for one box, drifted apart, overlapping. Also, that
  info column showed the *autopilot's* set points, editable right there,
  when the placement (beside the dial, like NAV1/NAV2's OBS) implied
  actuals. `draw_hdg_indicator` now calls `_card_geometry` itself (one call
  site, not two); the info column split into `_stack_hdg_actuals`
  (read-only, from `Scene.sixpack`) and `_stack_setpoint_bugs` (editable,
  restored under the tab column where the boxes lived before F31). 844 ->
  846 tests.

- **2026-09-18** — F33: the round NAV1 head was always built from the raw
  tuned NAV1 receiver, ignoring `nav.cdi_source` entirely - on the real
  airplane it's a GPS-slaved analog CDI, so it should show GPS deviation
  while the GNS's own CDI key is on GPS, the receiver only once it's VLOC.
  `instruments.gps_nav_head(nav, panel)` repackages `panel.cdi` (already
  correctly source-switched) into a `NavHead`; `Renderer._nav1_view` picks
  it or the tuned receiver per `cdi_source`. NAV2 unaffected. 846 -> 852
  tests.

- **2026-09-18** — F34: PROC only ever loaded a selected procedure - no
  Load?/Activate? choice existed, so activating one right after selecting
  it took a second PROC-menu round trip. Verified against the Pilot's Guide
  p.61 step 5 (text extracted via `pypdfium2` from a mirror with a real
  text layer): "'Load?' or 'Activate?' (approaches only)" is the actual next
  step after picking the transition, not a separate later action.
  `ProcSelect` gained a `LOADACT` step between TRANS and closing;
  "Activate?" (approaches only - SIDs/STARs get "Load?" only, per the
  manual) loads then calls the same `_activate_approach` the PROC menu's
  "Activate Approach?" already uses. 852 -> 855 tests.

- **2026-09-19** — F35: single-unit `stack` gained a COM2/NAV2 tuning panel
  (`Renderer._stack_radio2`, `ap_h` tall, click flip-flop/MHz/kHz); in
  `--dual`, FMS2 now drives NAV2 like FMS1 drives NAV1 (`World._panel2`,
  `Renderer._nav2_view`, FMS2's CDI strip on its own panel). 855 -> 860
  tests.
