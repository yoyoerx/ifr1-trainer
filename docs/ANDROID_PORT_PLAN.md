# ANDROID_PORT_PLAN.md

Living plan for an Android port of **octavi-ifr-trainer**, first target device
**Pixel 9** (phone, USB-C, Android 14/15). Companion to `ARCHITECTURE.md`
(desktop module map) — this doc only covers what's *different* on Android and
the decisions that follow from that. Update it as spikes resolve unknowns;
promote settled sections into `ARCHITECTURE.md`/`WORKING.md` once the port is
actually under way.

Two goals were given together and are treated as two workstreams below:
**(1)** port the existing validated avionics core to Android with Octavi
IFR-1 support, **(2)** build a comprehensive scenario training environment —
a genuinely new feature area, not just a port task, that both platforms will
eventually share.

---

## 1. What already works in our favor

- `gpsnav.py`, `navmath.py`, `instruments.py`, `autopilot.py`, `radios.py`,
  `windsaloft.py`, `wmm.py`, `scoring.py`, and all of `navdata/` are **pure
  Python, stdlib-only, no I/O, headless-tested** (~900 pytest cases, per
  `CLAUDE.md`). This is the entire avionics "brain" and it does not know
  pygame or the desktop exists. That's the single biggest asset for a port —
  none of the validated FINDINGS.md-backed behavior needs to be re-derived
  or re-proven on the new platform.
- The two platform-facing edges are already isolated as thin modules:
  `ifr1.py` (HID in) and `render.py` (pygame out). This is exactly the seam
  a port needs — the plan below leans on it rather than fighting it.
- Nav data (`datasrc/` fetch, `navdata/` parse) is offline/on-demand and
  file-cache based already — the *shape* of "fetch once, cache, check AIRAC
  validity" ports directly; only the storage location and UI trigger change.

---

## 2. The core tech-stack decision

This is the decision everything else hangs off. Three real options:

**A. pygame-ce on Android (python-for-android / buildozer).** Near-zero
rewrite of `render.py` itself (still pygame draw calls) and zero rewrite of
the brain. Android support in pygame-ce/SDL2 exists but is the least-traveled
path of the three for a HID-driven app specifically — touch/accelerometer
demos are common, generic USB HID input is not something p4a has a recipe
for out of the box. Would need custom Java glue for USB regardless, bridged
back into Python via `pyjnius`. Packaging (p4a recipes for `pypdfium2`,
`hidapi`-equivalent) is the main practical risk.

**B. Full native rewrite (Kotlin + Jetpack Compose/Canvas).** Best long-term
Android citizen — native USB Host API, native lifecycle/Doze handling, best
performance, cleanest Play Store story. Throws away every line of the
validated brain and the ~900-test safety net; the avionics state machine
(sequencing, holds, OBS/CDI scale, VNAV, PROC selector) would need a full
re-port and full re-validation against the Pilot's Guide from scratch. Given
how much of `FINDINGS.md` exists specifically because this state machine has
sharp edges, this is the highest-risk, highest-effort option.

**C. Hybrid — Kotlin app shell + embedded CPython brain (Chaquopy) +
Compose/Canvas UI.** `gpsnav.py`/`navmath.py`/`instruments.py`/`autopilot.py`/
`navdata/`/`scoring.py` run **unmodified** inside the app via Chaquopy
(CPython embedded in the APK, callable from Kotlin/Java and vice versa). Only
the two platform edges are rewritten natively: USB HID input becomes a small
Kotlin `UsbManager`-based module feeding the same `ifr1.Event` shape into
Python, and `render.py`'s pygame calls become a Kotlin Canvas/Compose
renderer. This keeps the entire validated, tested core untouched and matches
the project's existing architectural boundary (`CLAUDE.md`: *"pure-function
modules... are the easiest to test in isolation"*) instead of cutting across
it.

**Recommendation: C.** It's the only option that doesn't put the validated
avionics logic back into an unverified state, and it isolates all the new
Android-specific risk (USB, rendering, lifecycle) into genuinely new code
that needs new tests anyway — it doesn't ask old, trusted code to also serve
as new, unproven code. A is worth a short throwaway spike only if C's
Chaquopy packaging turns out to be a blocker (see §3.4). B is not recommended
unless C proves infeasible outright.

This is a real fork in the road and is flagged for your decision at the end
of this doc rather than assumed silently.

---

## 3. Open risks / required spikes (resolve before committing effort)

### 3.1 Octavi IFR-1 over Android USB Host — the critical unknown

This is the single highest-risk item in the whole plan and should be spiked
**first**, before any other Android work, because it can change the shape of
everything downstream.

The problem: Android phones normally run the standard Linux `usbhid` kernel
driver, which **claims** any USB device that declares itself as a HID-class
device — the same way desktop Linux/macOS/Windows do. When a kernel driver
already claims a device's interface, a regular (non-rooted) Android app
generally **cannot** open it via `UsbManager`/`UsbDeviceConnection` for raw
HID reports the way `hidapi` does on desktop — that path is normally only
available for devices with *no* claiming kernel driver.

However, Android also has a second path that may make this a non-issue: if a
HID device's report descriptor uses standard Generic-Desktop usage pages
(joystick/gamepad/consumer-control), the kernel HID driver translates it into
standard Linux input events, which Android's `InputManager` surfaces as
ordinary `KeyEvent`/`MotionEvent` to any app — **no special permission or
raw-HID code needed at all**, the same way a USB/Bluetooth game controller
just works in any Android app today. Whether the IFR-1's actual descriptor
(VID `0x04D8`/PID `0xE6D6`, custom gadget per `ifr1.py`'s header comment)
maps this way is unknown and must be tested on real hardware, not assumed.

**Spike plan:**
0. The IFR-1 occupies the Pixel 9's one USB-C port via the OTG adapter, so
   run `adb` over Wi-Fi instead of USB — Android 11+'s Wireless debugging
   (Developer options → Wireless debugging → "Pair device with pairing
   code") pairs over the network with no prior USB connection needed at
   all: `adb pair <ip>:<pairing-port>` (enter the 6-digit code), then
   `adb connect <ip>:<debug-port>`. Shell commands (`dumpsys`, `getevent`)
   work identically over this transport — nothing below changes because of
   it. Don't rely on a USB-C hub to free up the port instead; whether a
   given hub can pass both a HID device and USB debugging at once is
   hardware-dependent and untested, and wireless adb avoids the question
   entirely. **Confirmed working (2026-09-25)**: paired and connected to
   the real Pixel 9 over Wi-Fi (`192.168.1.133`, model `Pixel_9`/`tokay`),
   `adb shell` verified live — this step is no longer just a plan.
1. Plug the IFR-1 into the Pixel 9 via a USB-C OTG adapter, run
   `adb shell dumpsys usb` and `adb shell getevent -lt` while operating every
   control, and see whether button/knob activity shows up as generic input
   events at all.
2. If yes → build the small `InputDevice` listener path (ideal case, no raw
   USB code, maps closely to `ifr1.py`'s `LAYOUT`/`Event` model already).
3. If no (device enumerates but produces nothing via `getevent`) → check
   `dumpsys usb` for whether a kernel driver claimed it; if unclaimed, the
   `UsbManager` raw-HID path is available and a Kotlin equivalent of
   `ifr1.py`'s report-parsing loop can be written directly against the same
   byte offsets already reverse-engineered and hardware-confirmed in that
   file's header comment.
4. If claimed *and* not surfaced as usable input events → this is the true
   blocker case (would need root, a custom kernel HID-bypass, or asking
   Octavi for firmware/descriptor changes). Worth knowing early, not after
   months of otherwise-unrelated work.

**Spike result (2026-09-25), real Pixel 9 + real IFR-1 via USB-C OTG, over wireless adb:**

Steps 0-1 ran for real. The picture is better than the worst case but not the
clean "yes" either — a **partial, mixed result** the plan's four branches
didn't quite anticipate:

- `adb shell dumpsys usb` confirms the IFR-1 enumerates correctly: VID/PID
  1240/59094 decimal = `0x04D8`/`0xE6D6` (matches `ifr1.py`), manufacturer
  "Octavi", product "IFR1". It's a **composite** device (class 239, IAD):
  interface 0+1 are CDC-ACM (a virtual serial port - unused by anything
  we've tried so far), interface 2 is HID (class 3) with one interrupt-IN
  endpoint.
- `adb shell getevent -pl` shows the HID interface **is** claimed by the
  kernel's `usbhid` driver and translated to a standard evdev joystick/
  gamepad device: `/dev/input/eventN`, name **"Octavi IFR1"**, capabilities
  `KEY` (many `BTN_*` codes), `REL` (`REL_DIAL` only), `MSC` (`MSC_SCAN`).
- **Every button tested works** and maps cleanly to a `BTN_*` code, one
  press each, unambiguous (HID Button-page usage number in `MSC_SCAN`,
  decoded alongside):

  | Button | HID usage | evdev code |
  |---|---|---|
  | DCT | 0x05 | `BTN_WEST` |
  | MNU | 0x06 | `BTN_Z` |
  | CLR | 0x07 | `BTN_TL` |
  | ENT | 0x08 | `BTN_TR` |
  | SWAP | 0x09 | `BTN_TL2` |
  | KNOB (push) | 0x0a | `BTN_TR2` |
  | AP (= CDI in FMS mode) | 0x0f | `BTN_THUMBR` |
  | HDG (= OBS) | 0x10 | raw code `0x13f`, no evdev mnemonic |
  | NAV (= MSG) | 0x11 | `BTN_TRIGGER_HAPPY` |
  | APR (= FPL) | 0x12 | `BTN_TRIGGER_HAPPY2` |
  | ALT (= VNAV) | 0x13 | `BTN_TRIGGER_HAPPY3` |
  | VS (= PROC) | 0x14 | `BTN_TRIGGER_HAPPY4` |

  (HID usages 0x0b-0x0e are unaccounted for - not a gap in testing, the
  IFR-1 has no more distinct physical buttons than these per the user:
  "the other 8 are not real buttons, they are mode specific" - matching
  `main.py`'s own desktop AP-row dual-labeling, `AP=CDI, HDG=OBS, NAV=MSG,
  APR=FPL, ALT=VNAV, VS=PROC`, already the documented desktop behavior.
  There is also no dedicated RNG key - matches the real GNS bezel, range is
  a knob function. Mode selector and `BTN_GAMEPAD`/`BTN_EAST`/`BTN_NORTH`/
  `BTN_C`/`BTN_START`/`BTN_SELECT`/`BTN_MODE`/`BTN_THUMBL`/`BTN_TRIGGER_HAPPY5-16`
  remain untested - out of controls to press without a mode selector on the
  test unit, or genuinely unused HID usages.)
- **The outer knob works** - `REL_DIAL` value `+1`/`-1` per detent,
  confirmed repeatedly and reliably.
- **The inner knob produces nothing at all** - confirmed with two separate
  dedicated capture attempts (several clicks, then a couple of full
  rotations), zero events of any kind, while the very same capture
  correctly saw the outer knob moments later (ruling out a broken capture
  session). The capability list only advertises one relative axis
  (`REL_DIAL`), so this isn't a fluke of the test - the kernel's default
  HID-usage-to-evdev table has no entry for whatever usage the inner knob's
  encoder reports, and `usbhid` silently drops report fields it doesn't
  recognize rather than exposing them as anything.

**What this means for the plan:** rather than splitting logic across two
Android APIs - `InputDevice`/`KeyEvent` for the "ideal case" (buttons +
outer knob) and something else for the inner knob - the chosen direction
is to claim the whole HID interface with `force = true`
(`UsbDeviceConnection.claimInterface`), which detaches `usbhid` entirely
(the Android equivalent of what `hidapi` does implicitly on desktop), and
parse the raw report for *every* control through one code path matching
`ifr1.py`'s protocol exactly. `android/app/.../input/UsbHidInput.kt` is now
written against this design (2026-09-25) - `Ifr1Mode`/`Ifr1State`/
`Ifr1Event` mirroring `ifr1.py`'s `Mode`/`State`/`Event` field-for-field,
the same `LAYOUT` byte offsets, the same edge-triggered diff/long-press
logic. **Unverified**: whether `claimInterface(force = true)` actually
succeeds against a driver `usbhid` already holds for this device can only
be proven by running it, which needs the Android Studio build/sync this
spike was explicitly sequenced before. If claiming fails, the fallback is
back to `InputDevice`/`KeyEvent` for buttons + the outer knob (still free)
with the inner knob resolved separately - either a firmware/descriptor fix
from Octavi (usbhid's table is fixed kernel-side, not something this app
can extend without root) or leaning on touch for the inner-knob role (§3.2
already has to build touch knob emulation regardless, so that's an earlier
trigger to use it, not new UI surface).

**Runtime-confirmed (2026-09-24), real Pixel 9 + real IFR-1 via USB-C OTG.**
A disposable test harness (`input/UsbHidTestScreen.kt`, reachable from the
Phase 0 self-test screen's "IFR-1 raw-HID test" button) called
`UsbHidInput.start()` against the actual device. Result: `claimInterface
(force = true)` **succeeds** - status went straight to `CONNECTED`, detaching
`usbhid` cleanly with no fallback needed. Both previously-open questions
from §3.1 step 4 are answered:
- **(a) claim succeeds**: confirmed, `CONNECTED` status reached every time
  the IFR-1 was attached and the app (re)started.
- **(b) raw report stream matches `ifr1.py`'s `LAYOUT` byte-for-byte**:
  confirmed. Observed decoded events, with raw frame bytes alongside:
  - Inner knob CW: `inner=1`, raw `0B 00 00 00 00 00 01 00` (byte 6 = `0x01`).
  - Inner knob CCW: `inner=-1`, raw `0B 00 00 00 00 00 FF 00` (byte 6 =
    `0xFF`, decodes as signed -1 - `sByte()` working correctly).
  - `KNOB` button (inner-knob push) down/up: raw byte 2 toggling bit `0x02`,
    exactly `Ifr1Layout.BUTTONS["KNOB"]`.
  - Mode byte (`COM1` on connect) decoded correctly at byte 7.

So the inner knob - the control this whole raw-HID path exists for, and
that produces zero evdev events under the kernel's default `usbhid`
translation (§3.1's original finding) - now works end to end on Android.
"IFR-1 support on Android v1" is **fully confirmed**: buttons, outer knob,
and inner knob all read correctly through the one unified raw-HID code
path. The `InputDevice`/`KeyEvent` fallback this section describes is no
longer needed.

### 3.2 Touch-only operation (no IFR-1 attached)

IFR-1 is required for v1 release (§7), but touch/knob emulation still needs
to be built regardless — development and testing will constantly happen
without the hardware plugged in, and the same control surface is what
portrait (§6 Phase 2) needs anyway. This is new UI surface area, not a port
of anything — the desktop's `stack` layout
has mouse *clicks* only (tab switching, bug-box +/-); nothing today emulates
the IFR-1's **twist knobs**, which are load-bearing for FMS cursor movement,
frequency tuning, and OBS course setting. Needs a rotary-gesture control
(circular drag, or a simple up/down flick-to-increment strip — look at how
existing EFB apps like ForeFlight/Garmin Pilot render virtual GPS knobs) plus
an on-screen bezel for the button rows. This is genuinely new design work,
not a straightforward carry-over of the desktop's `S`/`B`/`D`/`R` keyboard
shortcuns — evaluate against the real GNS 530 bezel layout so the touch
version stays faithful to the same button *names and effects* documented in
`FINDINGS.md`, even though the physical affordance (touch vs. twist) differs.

### 3.3 Screen shape and layout

Pixel 9: ~6.3", 1080×2424, ~20:9. None of the four existing layouts (`gps`,
`steam`, `stack`, `dual`) were designed for this aspect ratio; they assume a
landscape desktop monitor. Decision (below): support **both** orientations,
not landscape-only. That means two phone-specific layouts, not one:

- **Landscape** (`phone-landscape`, ~2424×1080): the primary/kneeboard-mount
  mode. A real GNS 530/430 is itself roughly landscape (wide, short), so this
  is the more faithful of the two and should be built first — closest in
  spirit to the desktop `stack` layout, just narrower, and the one to use as
  the template for the draw-command-list bridge (§3.6) since it's the
  lower-risk shape to get right first.
- **Portrait** (`phone-portrait`, ~1080×2424): a genuinely different
  composition, not a rotated version of landscape — `stack`'s three-column
  design doesn't fit a ~1080px-wide column at any legible zoom, so this needs
  its own layout pass (likely GNS unit full-width on top, CDI/HSI + AP
  programmer stacked below, WX/MAP/PLATE reachable as a swipeable panel
  rather than a fixed column). Treat this as separate design work, not a
  free follow-on once landscape exists — budget it as such in phasing.

Both need to stay faithful to the same GNS behavior/labels `FINDINGS.md`
already validates; only the on-screen composition changes per orientation.

### 3.4 Chaquopy packaging of existing dependencies

**Confirmed on real hardware (2026-09-24).** After fixing six real
build-toolchain errors (see `android/README.md` "Status" and commit
`1976a11`), `.\gradlew.bat :app:assembleDebug` produced a working APK,
installed and launched on a real Pixel 9. The Phase 0 self-test screen
showed: `"OK: navmath/navdata/gpsnav/sim_model/androidbridge imported and
ticked - to='BRAVO' pos=(39.9006,-74.0000)"` — proof the curated brain
module set (stdlib-only slice: `navmath.py`, `gpsnav.py`, `sim_model.py`,
`navdata/`, `androidbridge/`) genuinely imports and runs correctly under
Chaquopy on-device, not just in theory. The risk this section originally
flagged as second-highest is resolved for the stdlib-only slice; the
`pypdfium2`/PDF-rasterization question below remains open (not yet
exercised — Phase 0's self-test doesn't touch it).

- `pygame-ce` itself is dropped entirely on the Chaquopy path (rendering
  moves to Kotlin) — good, since it's the dependency least likely to have
  clean Android wheels.
- `pypdfium2` (native C extension, used only for the `stack` layout's inline
  PLATE-tab PDF rasterization) needs verification that Chaquopy can install
  it for Android's ABI, or it gets deferred (v1 could hand approach plates
  off to an Android `Intent` for the system PDF viewer instead, exactly what
  the desktop did before the `stack` layout added inline rendering — a
  reasonable v1 fallback, not a regression from where the desktop app
  started).
- `hidapi` (desktop's `[device]` extra) is dropped entirely — replaced by
  the native Kotlin USB/input path from §3.1.
- Everything else the brain imports is stdlib (`dataclasses`, `enum`, `math`,
  `struct`, etc.) and should just work under Chaquopy with no changes.

### 3.5 Nav data acquisition and storage on-device

Desktop: `python -m datasrc.faa update` is a manual, networked CLI step the
user runs before first launch; data lands in a gitignored `data/` dir next
to the repo. On Android there's no terminal and no arbitrary working
directory — needs:
- An in-app "update nav data" flow (first-run wizard + a settings action for
  subsequent 28-day AIRAC refreshes), writing into the app's private storage
  (`context.filesDir`), which `navdata.load()`'s desktop path-resolution
  logic would need a platform-specific equivalent of.
- A decision on whether v1 **bundles a starter AIRAC cycle** in the APK/asset
  pack so the app works offline immediately after install (nicer first-run,
  but ties a release to a specific cycle and adds app size — need to check
  actual FAA CIFP+NASR cache size before committing to this) vs. requiring a
  first-run download over the user's own connection (simpler to ship, matches
  desktop's existing model, worse cold-start UX).
- Respecting that this is public-domain FAA data either way (no licensing
  change from what the desktop already redistributes/refetches).

### 3.6 Rendering bridge design (Chaquopy path)

Crossing the Python↔Kotlin boundary once per drawn primitive at 30 Hz would
be a lot of per-call JNI overhead for a screen with many text/line/circle
draws per frame (CDI needles, HSI, GNS text rows, moving map). Instead:
Python builds one compact **draw command list** per tick (typed ops — text,
line, rect, circle, polygon, each with position/size/color/font-id) using
the same layout math `render.py` already has; Kotlin crosses the JNI
boundary **once per frame** to fetch that list and replays it against a
Compose `Canvas`/`DrawScope`. This reuses `render.py`'s positioning logic
(the actually-validated part — where things go, when they're shown) while
swapping only the leaf-level `pygame.draw.*`/`pygame.font` calls for Canvas
equivalents. A new `render_commands.py` (or a refactor inside `render.py`
behind a `Surface`-like adapter) is the natural place to draw this line.

**First slice confirmed on real hardware (2026-09-24): the HSI head.**
`render_commands.py` ports `draw_hsi_head`/`_cdi_card`/`_card_geometry`/
`_gs_scale`/`_ident_dot`/`_dial`/`_panel_box` - same trig/layout constants
as `render.py`, rewritten to append encoded command strings instead of
calling `pygame.draw.*`. The draw-command list crosses as a single
**newline-delimited, pipe-field string**, not a Python list/Kotlin
`PyObject` - the same List-marshaling bug found in §6 Phase 1 (Chaquopy's
Java `List` → Python conversion producing a non-iterable object) would
apply to the reverse direction too, so every cross-boundary value stays a
primitive or a string, consistently. `InstrumentCanvas.kt`'s `Text` case
(previously a TODO) is now implemented via `nativeCanvas.drawText` +
`android.graphics.Paint` (Compose's `DrawScope` has no native text
primitive). Verified on the real Pixel 9 + real IFR-1: the HSI dial,
rotating compass card, heading digital readout, and course pointer all
render correctly, and turning NAV1's shift-latched knob live-rotates the
course pointer on screen. AP panel graphics, moving map, and GNS
softkey/page UI are still plain text / not yet ported - this slice is
deliberately just the one instrument. (The six-pack gauge cluster is not
planned for Android at all - see §7's 2026-09-24 decision.)

**Second slice confirmed on real hardware (2026-09-24): the AP panel.**
`render_commands.py` ports `draw_ap_panel` (S-TEC 55X programmer: RDY lamp,
HDG/NAV/APR/REV/ALT/VS mode row, VS window, and the HDG BUG/ALT SEL/IAS SET
info box) the same way. This slice surfaced a systemic text-positioning bug
the HSI slice's looser spacing had hidden: Android's `Paint.drawText`
positions by **baseline**, while `render.py`'s pygame text positions by
**top-left** - ad hoc per-call offsets tuned by eye against the HSI alone
didn't generalize to the AP panel's tightly-packed 17px-pitch info box,
which overlapped badly. Fixed at the root: `InstrumentCanvas.kt`'s `Text`
case now converts once (`y - paint.ascent()`), so `render_commands.py`'s
`y` arguments are plain top-anchored values matching `render.py`'s own
argument values directly (a `_centered_y()` helper handles the handful of
`center=True` call sites, which need both axes centered on a point, not
just top-anchored). The info box's real DSEG7-metric-tight packing isn't
reproducible without that font's real metrics (no pygame here), so it uses
a plainer, more generously-spaced layout instead of guessing at pixel-exact
overlap tolerances - same information, less visually compact. Verified on
the real Pixel 9: all six mode keys, the RDY lamp, VS readout, and the info
box render correctly with no overlap, matching the plain-text debug panel's
values exactly.

**Third slice confirmed on real hardware (2026-09-24): the GNS default NAV
page.** `render_commands.py` gains `gns_commands`, porting `_gns_unit`'s
always-drawn chrome (header group/page name + CRSR indicator, no-bezel
flat screen frame) plus `_draw_nav_default` (active-leg symbol/from/to,
OBS annunciation, DTK/TRK/DIS/GS/ETE/XTK rows), `_turn_advisory` (NEXT
DTK/TURN TO), `_cdi_strip` (the linear GPS/VLOC CDI + TO/FROM strip), and
`_bezel_labels` (the flat style's static key row). At the time, only the
default NAV page rendered - `_gns_unit` is a ~15-page router (Map, Flight
Plan, VNAV, NAV/COM, WPT, NRST, AUX with 5 sub-tabs, plus 8 modal
dialogs); porting all of it in one pass wasn't realistic, same reasoning
as HSI-then-AP-panel over attempting the whole panel at once. `route_event`
already dispatches real FMS bezel-key input into `GpsNav.handle_event`
correctly (proven by the core-loop milestone) - so turning the FMS knob
does move `gns.cursor.page_name` server-side, but the Android screen kept
showing the default-NAV layout regardless until other pages got their own
pass. Applied the top-anchored-`y`/`_centered_y()` convention correctly
from the start this time (no ad hoc offsets to fix later, unlike the AP
panel slice). Verified on the real Pixel 9 with the demo flight plan
(ALFA→BRAVO→CHAR) active: the header, active-leg line (`-> ALFA BRAVO`,
magenta), DTK/TRK/DIS/GS/ETE/XTK rows, and CDI strip (GPS source, centered
needle, TO indicator) all render correctly and match the plain-text debug
panel's own values exactly.

**Fourth slice confirmed on real hardware (2026-09-25): the Flight Plan
page.** `render_commands.py`'s `gns_commands` was refactored to dispatch
its body by `cursor.page_name` (`_nav_default_body`/`_fpl_body`, mirroring
`_gns_unit`'s own `if page == ...` structure) rather than always drawing
the default page, and gains `_fpl_body` - a port of `_draw_fpl`/`_fpl_tag`/
`visible_fpl_rows` (waypoint list with the active-leg `->` marker, per-leg
DTK/DIS, procedure-title headers, IAF/FAF/MAP/HOLD tags, the DTO status
line, scroll-window-follows-cursor logic, "no flight plan" fallback).
**Read-only**: the in-place ident-edit buffer (`gns._fpl_edit`'s live
typing cursor, shown while adding/editing a waypoint) is not rendered -
real bezel input still edits the plan server-side, this just doesn't show
that transient edit state yet, same accepted-gap framing as the
still-unported pages. A real bug was found and fixed while writing this
slice's test: the test fixture's synthetic nav database was missing the
third waypoint (`CHAR`) the test flight plan referenced, silently dropping
it from the loaded plan and producing a false rendering-bug signal - fixed
in the fixture, not the rendering code (`gns_commands` was correct against
the real, complete flight plan the whole time). Verified on the real
Pixel 9: header ("NAV Flight Plan"), the waypoint list with ALFA (past,
plain), `-> BRAVO` (magenta, active leg), CHAR (upcoming), correct per-leg
DTK/DIS columns, and the CDI strip all render correctly.

**Fifth slice confirmed on real hardware (2026-09-25): the VNAV and
NAV/COM pages.** `gns_commands` gains two more dispatch cases: `"VNAV"` to
`_vnav_body` (a port of `_draw_vnav_page` - TARGET/TGT ALT/VS PROFILE
fields with cursor-field highlighting, VNV ARMED/OFF, then once armed and
resolved: DIS/TOD-or-PAST-TOD/TIME-TO-TOD/REQ VS/DEV status rows from
`GpsNav.vnav_status`) and `"NAV/COM"` to `_navcom_body` (a port of
`_draw_navcom_page` plus a new shared `_freq_rows` helper ported from
`_draw_freq_rows` - the flight plan's tunable airport frequencies, role
label + ident, scrollable list with the highlighted-row/VLOC-cyan coloring
convention). Both are smaller, single-purpose pages compared to Flight
Plan, so this pass ported both together rather than one per slice. Verified
on the real Pixel 9: VNAV correctly shows the unarmed "no active VNAV
target" fallback (this session's demo flight has no VNAV profile set);
NAV/COM correctly shows "no airport in flight plan" (the synthetic demo db
has waypoints and one VOR but no airports) - both are real code paths
through `_vnav_body`/`_navcom_body`, not stubs, exercising each function's
no-data branch rather than its fully-populated one. The fully-armed/
fully-tuned branches are covered by `tests/test_render_commands.py` but
not yet confirmed on-device pending a demo database with airports and an
armed VNAV profile.

**Sixth slice confirmed on real hardware (2026-09-25): Flight Plan
Catalog, WPT, NRST, and AUX (Nav Data/Trip Planning/Utility/Setup).**
`gns_commands` now dispatches by `cursor.group_name` in addition to
`page_name`, since WPT/NRST/AUX page names aren't unique the way the NAV
group's are conceptually distinct pages. Four new bodies: `_fpl_catalog_body`
(`_draw_fpl_catalog` - the FPL 01-19 slot list), `_wpt_body`
(`_draw_wpt_page` - all six WPT sub-pages: Airport, Airport Runway,
Airport Freq, Intersection, NDB, VOR, with the shared char-cell ident
entry + underline cursor + per-kind info block), `_nrst_body`
(`_draw_nrst_page` plus its three specialized sub-bodies -
`_draw_nrst_airports`/`_draw_nrst_facility`/`_draw_nrst_airspace` - all
eight NRST pages: APT/INT/NDB/VOR/User/ARTCC/FSS/Airspace), and
`_aux_body` (dispatches Nav Data/Trip Planning/Utility/Setup to their own
small functions; Weather and Charts are **not** ported - they need a live
`datasrc.wx` cache read and PDF chart rendering respectively, out of scope
for the same reason the six-pack gauge cluster is (§7): real device data
Android doesn't have yet, not a rendering-complexity problem). `t` and
`baro_inhg` were added as `gns_commands` parameters (`World.t`/
`World.baro_inhg`) since AUX Utility's flight timer and AUX Setup's baro
readout need them - every other page ignores them. Verified on the real
Pixel 9: Flight Plan Catalog shows all 9 slots as "-- empty --" (no saved
plans this session); WPT Airport shows the six-cell empty entry buffer and
"knob: enter identifier"; NRST Nearest APT shows "none within range"
(the demo db has no airports); AUX Nav Data shows the demo db's
SOURCE/CYCLE/EFF/EXP fields and APT/VOR/NDB/WPT/AWY counts (0/1/0/3/0,
matching the demo db exactly); AUX Trip Planning shows "ALFA -> CHAR" with
correct TOTAL DIS/DIS REMAIN/GS/ETE; AUX Utility shows a live-updating
flight timer and current GS/TAS/ALT; AUX Setup shows UNIT "GNS 530", CDI
SRC "GPS", and BARO "29.92 in". The WPT-with-a-match and NRST-with-hits
branches (an airport actually found/nearby) are covered by
`tests/test_render_commands.py` but not yet confirmed on-device, same gap
as VNAV/NAV/COM's armed-profile branch. **What's left of §3.6**: the
moving map (a canvas, not a text page - its own slice) and the 8 modal
dialogs (PROC, DTO, confirms, message page).

### 3.7 Loop, threading, and Android lifecycle

The desktop loop is single-threaded except optional UDP/weather-refresh
threads (`ARCHITECTURE.md` §2, `CLAUDE.md`: *"No network in the main loop"*).
On Android the Python sim loop needs to run on a background thread (Chaquopy
supports this) independent of the UI thread, handing off a snapshot each
tick for Compose to draw — plus correct behavior on `onPause`/`onStop`
(background/Doze): the loop should suspend cleanly rather than free-running
while backgrounded or crashing on resume. This is new code with no desktop
analog to reuse (the desktop app doesn't get backgrounded by an OS).

### 3.8 Time warp

`CLAUDE.md`: *"Time warp re-runs the same fixed `dt` N times per frame
rather than scaling `dt`."* This convention carries over as-is to the
Chaquopy brain (it's a pure-logic concern, unaffected by the platform swap)
and should stay dt-based per the existing rule — no new design needed here,
just confirming it isn't accidentally broken by whatever drives the tick
loop on Android.

---

## 4. Workstream 2 — comprehensive scenario training environment

Distinct from the port itself; make it platform-agnostic where possible so
desktop gets it too, not just Android. Builds on `scoring.py` (already grades
xtk/CDI/glideslope RMS, needle pegs, TKE, altitude, ACS-style letter grade),
`radios.py`, and the existing PROC/approach-loading machinery.

- **Scenario definition format** — a declarative file (TOML, matching the
  existing `octavi.toml` config convention) describing: departure/arrival
  airports, initial position/altitude/heading/fuel, weather (wind, winds
  aloft, ceiling/vis), a flight plan or expected-to-be-flown procedure, a
  scripted **ATC clearance timeline** (events triggered by waypoint/altitude/
  time — "cleared direct JONJR," "descend and maintain 3000," "contact
  approach"), and optional **instructor-injected events** (GPS fail forcing
  VOR/ILS backup, lost comm, vacuum/AI failure for partial panel, CDI flag) —
  the kind of thing an actual CFII scripts by hand today.
- **Scenario library** — a curated set of canned lessons mapped to ACS/PTS
  task areas (hold entry, partial-panel VOR approach, missed approach + hold,
  lost comm, circling approach), shipped with the app. An in-app scenario
  *editor* (vs. hand-authored TOML) is a reasonable stretch goal, not v1.
- **ATC clearance delivery** — text-only for v1 (append to the existing
  message queue `gpsnav.py` already has); Android's built-in on-device
  `TextToSpeech` API is a low-effort later addition if voice delivery is
  wanted (no new dependency, no network).
- **Debrief** — extend `scoring.py`'s existing grading with a stored
  post-flight track log (position/CDI/altitude over time — the trainer
  already emits GDL90, so a lightweight recorder is a natural sibling, not a
  new subsystem) and a simple replay/summary screen.
- This workstream doesn't strictly require the Android port to start — it
  could land on desktop first as a lower-risk way to validate the format and
  scoring hooks before also building an Android scenario-picker UI on top of
  it. Worth sequencing after workstream 1's spikes resolve, so scenario UI
  design isn't done twice for two different rendering targets.

---

## 5. Testing strategy

- The existing ~900-test suite (`gpsnav`/`navmath`/`instruments`/`autopilot`/
  `navdata`/`scoring`) stays **exactly as-is** and keeps running on desktop
  CI under plain `pytest` — it never touches pygame or platform I/O today,
  and under plan C it won't need to under Chaquopy either. This is the
  biggest reason to prefer C: the port adds new code needing new tests
  without putting a single already-trusted line back into an unverified
  state.
- New coverage needed, all genuinely new (no desktop equivalent to lean on):
  Kotlin instrumented tests for the USB input path and the draw-command-list
  replay; a contract test asserting the Python-side command list and the
  Kotlin-side renderer agree on op shapes; on-device smoke tests for the
  nav-data download/storage flow.
- Manual playtest rounds (same methodology `FINDINGS.md` already uses for
  desktop) should repeat once touch/IFR-1 input is wired up, since input
  ergonomics are the one thing that can't be verified by a headless test.

---

## 6. Proposed phasing

Decisions made (§7): **Chaquopy hybrid**, **IFR-1 support required for v1**
(not deferred to a later phase), **both orientations** supported. This makes
Phase 0's §3.1 spike a hard gate on the whole project, not just on a later
phase — v1 cannot ship without it, so it must resolve, and resolve
favorably, before any other Android work is worth starting.

1. **Phase 0 — spikes only, no feature work, hard gate.** §3.1 (IFR-1 HID
   over Android USB, real Pixel 9 hardware) is the go/no-go for the entire
   project as scoped — if it resolves to the "claimed and not usable"
   worst case, the plan needs to be revisited (root, firmware change,
   Bluetooth bridge, or reconsidering the IFR-1-required decision) before
   continuing. **Resolved favorably (2026-09-24)**: buttons, outer knob, and
   inner knob all confirmed working via `claimInterface(force = true)` — see
   §3.1's "Runtime-confirmed" writeup. Also run §3.4 (Chaquopy import of the
   actual brain modules on-device) and a throwaway one-screen
   draw-command-list round trip in this phase, since a Chaquopy packaging
   blocker is the second-highest-risk unknown and is cheap to find out early
   alongside the USB spike. **§3.4 also resolved favorably (2026-09-24)** —
   see §3.4's "Confirmed on real hardware" note. The hard gate has passed;
   Phase 1 work is unblocked.
2. **Phase 1 — landscape core loop with IFR-1 input.** Build landscape first
   (§3.3 — lower-risk shape, closer to existing `stack` layout to draw from)
   with real IFR-1 input wired end to end per whatever §3.1 found. Touch/
   rotary-gesture controls (§3.2) are still built in this phase too, even
   though not required for v1 release — needed for development/testing
   without the hardware attached at every step, and lands naturally as the
   same UI work portrait will need in Phase 2.

   **Core loop (input → World → tick) confirmed on real hardware
   (2026-09-24).** `androidbridge/session.py` now wraps `main.World`/
   `main.route_event` directly (staged into Chaquopy: `main.py`, `ifr1.py`,
   `config.py`, `gns530.py`, `gns430.py` — all confirmed import-clean, since
   `main.py`'s module-level imports are stdlib-only, correcting
   `androidbridge/__init__.py`'s earlier "can't reuse main.py, it imports
   pygame" assumption). `world/TrainerLoop.kt` ticks it ~30 Hz on a
   background thread, lifecycle-aware (suspends on `onStop`). Real IFR-1
   events now route through the exact same `route_event` mode-table desktop
   uses, verified on-device: COM1/COM2 tuning, NAV1/NAV2 OBS shift-latch,
   AP-row buttons, XPDR all confirmed correct via a live debug-text screen
   (`world/Phase1LoopScreen.kt`) - not the real instrument graphics, §3.6
   below is still what renders those. FMS mode's dispatch path is wired
   identically but not independently visually confirmed yet (no
   flight-plan/cursor readout in the debug panel).

   Two real on-device bugs found and fixed getting here (see `WORKING.md`
   for the full detail): `ifr1.py` had a top-level `import hid` that raised
   `SystemExit` on any `import ifr1` without hidapi installed - true under
   Chaquopy by design, so it broke even the Phase 0 self-test until made
   lazy; and Chaquopy's Java `List`/`ArrayList` → Python argument marshaling
   produced a non-iterable object on the Python side (crashed on every knob
   turn), worked around by passing button-name lists as comma-joined
   strings across that boundary instead.
3. **Phase 2 — portrait layout.** Separate composition pass per §3.3, not a
   rotation of Phase 1 — budgeted as its own phase, not a quick follow-on.
4. **Phase 3 — nav data on-device**: fetch/cache/AIRAC-validity flow,
   starter-bundle decision from §3.5.
5. **Phase 4 — scenario training environment v1**: canned library + ATC
   clearance scripting + debrief screen (workstream 2).
6. **Phase 5 — polish/release readiness**: icon/branding, battery/Doze
   behavior, decide sideload-only vs. Play Store (data-safety form, target
   API level, no real policy risk expected — training aid, already
   disclaimed as such — but not yet scoped).

---

## 7. Decisions

Resolved 2026-09-23:

- **Tech stack: Chaquopy hybrid** (option C, §2) — Kotlin/Compose shell,
  Python brain embedded unmodified, only USB input + rendering rewritten
  natively.
- **IFR-1 hardware support is required for v1** — not deferred behind a
  touch-only release. This makes §3.1's spike a hard project gate (§6,
  Phase 0), not a nice-to-have-resolved-eventually item.
- **Both orientations supported** — landscape (built first, §6 Phase 1) and
  portrait (separate composition, §6 Phase 2), not landscape-only.

Resolved 2026-09-24:

- **The six-pack gauge cluster (`draw_six_pack`/`_hdg_card`, desktop's
  `steam` layout) is explicitly not a goal for this Android port.** IFR
  training is the point (GPS/HSI-primary panel, matching the `gps`/`stack`
  layouts' philosophy already), not a round-gauge steam-panel trainer; the
  Android v1 instrument set is HSI + AP panel + GNS pages + moving map,
  not a fifth layout choice. §3.6's rendering passes should not budget time
  toward it, and it should not be inferred as implicit scope from "port the
  remaining desktop instruments."

Still open, to be settled at Phase 3/5 rather than now (not currently
blocking any near-term work):

- **Starter nav-data bundling**: ship a bundled AIRAC cycle in the APK for
  offline-first-run (bigger app, simpler onboarding) vs. first-run download
  only (matches desktop's existing model).
- **Sideload-only vs. Play Store** distribution for the eventual release.

---

## 8. Growth area — Android Auto (assessed, not recommended as scoped)

Idea considered: project the GNS 530 face onto a car head unit via Android
Auto for a bigger training display. Assessed on request; **not recommended
in the form of actual Android Auto integration** — the fit is poor for
reasons specific to how Android Auto works, not just effort:

- Android Auto (phone-projection, distinct from Android Automotive OS, which
  runs Android natively *in* the car and is a different, unrelated SDK) only
  lets an app draw arbitrary custom content — the raw `Surface` a rendered
  GNS face would need — under the **Navigation** content category of the Car
  App Library. Every other category (media, messaging, POI, parking/
  charging) is template-only: fixed Google-defined lists/cards, no custom
  canvas. A flight-instrument panel doesn't fit any of the template
  categories, so the Navigation category's raw-surface access is the *only*
  technical path in — and that category exists for actual turn-by-turn
  navigation apps specifically, gated behind Google's own app review for
  that use case, not a general "give me a canvas" allowance.
- Even if that hurdle were cleared, Android Auto's core purpose is
  driving-safety-reviewed content shown *while driving*. Google's
  distraction guidelines are built around "is this relevant to and safe
  during active driving" — a flight-sim instrument panel is neither, and
  would be expected to fail review on content-relevance grounds independent
  of the technical template question.
- Net: pursuing real Android Auto integration means fighting both a
  category/template mismatch and a content-policy mismatch simultaneously,
  for a training app that was never meant to be used while operating a
  vehicle in the first place.

**What actually gets the stated underlying want — a bigger/secondary
screen — without any of that fight:** standard Android display output, which
needs no special Google approval, no car-specific SDK, and no departure from
the Chaquopy/Compose renderer already planned:

- **USB-C DisplayPort Alt Mode** — Pixel 9 supports video-out over USB-C to
  any DP-capable monitor/dock. Simplest, lowest-latency, no wireless
  dependency — closest desktop-monitor-like experience.
- **Cast/Miracast** to any Android TV, Chromecast, or Miracast-capable
  display (many car head units, including some running Android Auto,
  support Miracast *receive* independent of the Android Auto app framework
  entirely — worth checking the specific head unit, but this is a display
  protocol, not an Android Auto integration).
- Both are standard OS-level display features; the app doesn't need to know
  which one is in use — Android's multi-display/external-display APIs treat
  either as "another display to render Compose content on," which is a
  natural fit for the same renderer Phase 1/2 already build, not new
  rendering work.

Recommendation: if a bigger/parked-car or secondary-screen scenario becomes
a real want later, treat it as ordinary external-display support (cheap,
low-risk, reuses the existing renderer), not as an Android Auto feature.
Leave Android Auto itself out of scope unless the actual requirement changes
to genuine in-motion use — and even then, expect the content-policy mismatch
above to be the harder blocker, not the engineering.

---

## 9. Growth area — phone GPS as a motion source

Decided use case (2026-09-23): **both** ground/car rehearsal and real
in-flight companion use, ground scoped first — v1 engineering rigor targets
the ground/car case; in-flight fidelity is a later phase, not designed away.

### 9.1 This is not new architecture, it's a third motion source

`ARCHITECTURE.md` §2 already models "motion source" as a swappable box —
`sim_model.py` (scripted) or `xplane_feed.py` (live X-Plane) both feed the
same position/track/groundspeed shape into `instruments.py`. Phone GPS is a
third implementation of that same interface (`phone_gps_feed`, reading
Android's `FusedLocationProviderClient` via Chaquopy/JNI and publishing into
the identical shape) — this is low-risk precisely because the abstraction
boundary already exists and already has two other implementations proving
it out. No `gpsnav.py`/`instruments.py` changes needed; they don't know or
care where position comes from today and shouldn't start caring now.

### 9.2 Altitude: use the barometer, not raw GPS altitude

Raw phone GPS altitude is the weak link (typically ±10-30 m even with good
horizontal fix quality) — not good enough for glideslope/step-down-fix work,
which is exactly the content this trainer is for. Pixel phones (9 included)
carry a barometric pressure sensor. Recommend deriving indicated altitude
from **barometric pressure + a pilot-settable baro/Kollsman setting** instead
of GPS altitude — this is not just more accurate, it's *more faithful*,
since it's the same physics a real altimeter uses, and the trainer already
has a Baro-set control (`stack` layout, `render.py`'s `_stack_hdg_actuals`,
F72) to reuse for exactly this input rather than inventing a new one. GPS
altitude becomes a fallback only if a given device lacks a barometer.

### 9.3 Update-rate smoothing

GPS fixes typically arrive at ~1 Hz (Fused Location Provider can do better
with sensor fusion but isn't guaranteed to); the render/instrument loop runs
at ~30 Hz. Feeding raw fixes straight through would make CDI/HSI needles
visibly stair-step once per second instead of moving smoothly. Needs
dead-reckoning/interpolation between fixes (extrapolate position from last
known track+groundspeed until the next fix arrives, then reconcile) — this
is genuinely new code, no desktop analog, since `sim_model` generates every
tick itself and `xplane_feed` gets high-rate UDP updates already.

### 9.4 Low-speed jitter

GPS-derived track/bearing gets noisy or meaningless near zero groundspeed
(a phone sitting still has no real "track"). Matters for the ground/car
rehearsal case specifically at stops (red lights, parking) — needs a
groundspeed floor below which track holds its last valid value rather than
jittering, same category of problem sim_model doesn't have (it always knows
its own commanded heading even at low speed).

### 9.5 Wind model interaction

Real GPS-derived groundspeed/track already has any real-world wind baked
in — layering the trainer's synthetic `--wind`/winds-aloft model on top
would double-count it. Precedent already exists: this must already be true
for `xplane_feed` (X-Plane's own simulated wind is likewise not to be
double-modeled) — so the answer here is "auto-disable synthetic wind when
motion source is phone GPS," consistent with existing behavior, not a new
design question.

### 9.6 Heading vs. track

No wind means track ≈ heading, which is what the ground/car case gets by
construction (a driving car doesn't crab). In real flight there's a genuine
difference (crosswind crab angle) that GPS track alone can't show. v1 (ground
first, per the decided scope) can reasonably use track as heading, same
simplification implicit in a "no separate heading sensor" motion source;
flag it explicitly as a known simplification to revisit if/when the in-flight
phase is scoped for real, at which point the phone's magnetometer/rotation-
vector sensor becomes the natural additional input for a true heading
distinct from track.

### 9.7 Safety/framing — non-negotiable, gets more important here

The existing disclaimer (`README.md`: *"For training practice, not
real-world navigation"*) already covers this, but it's worth restating
explicitly once *real* position data are involved: a phone's single-antenna
GPS has no WAAS certification, no RAIM/integrity monitoring, and this
trainer must never be presented, marketed, or usable as if it were a
certified navigation source — including, and especially, the in-flight
companion use case, where the temptation to half-trust it is highest because
it's riding along in a real cockpit next to real avionics. This isn't a
technical task, just a hard constraint to keep enforcing at every UI surface
this feature touches (a persistent "SIMULATED — NOT FOR NAVIGATION"-style
annunciation is the kind of thing to keep, not defer).

### 9.8 Permissions and battery

Runtime `ACCESS_FINE_LOCATION` (and background location if the app can be
backgrounded mid-session — plausible for the in-flight case where the phone
might be screen-off in a kneeboard pocket between checks); continuous
GPS+screen-on is real battery drain over a multi-hour flight, worth a note
in user-facing docs (external power recommended) but not a blocker — the
same operational reality every EFB app already has.

### 9.9 Phasing note

Sequence this after Phase 1/2 layout work (§6) — it's a new motion source
plugged into an already-working render/input pipeline, not something that
needs to land before the core port does. Ground/car rehearsal (interpolation
+ low-speed handling, §9.3–9.4) is the v1 cut; in-flight fidelity (heading
sensor fusion, §9.6) is explicitly deferred, not designed out.
