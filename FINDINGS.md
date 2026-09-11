# FINDINGS.md — GNS 530 function/graphics validation

Validation of the trainer's GNS 530 surface against the **Garmin GNS 530(A)
Pilot's Guide and Reference, 190-00181-00 Rev. H** (Dec 2009, Main SW 6.03).
Modules audited: `gpsnav.py`, `gns530.py`, `instruments.py`, `render.py`,
`main.py`.

Date: 2026-09-10.

---

## Verdict

The avionics **core the project claims** — flight plan, Direct-To, waypoint
sequencing with turn anticipation, SUSP, OBS, CDI source, nav-data expiry — is
present and behaviourally faithful to the manual. The gaps are (a) three CDI
fidelity bugs, (b) features that exist in the core but were unreachable from the
running trainer, and (c) UI-surface / procedure richness that is milestone-scale
and already tracked as parked work in `WORKING.md`.

Items **F1–F11** below are fixed in this pass (code + tests). Items **D1–D9** are
documented as deferred, with rationale.

---

## Fixed this pass

### F1 — Enroute GPS CDI full-scale was 2.0 nm, should be 5.0 nm
`instruments.GPS_FULL_SCALE_NM[Phase.ENROUTE]` was `2.0`. The 530 Pilot's Guide
(§3.3, §10.4) states the GPS CDI full-scale distances are **0.3 / 1.0 / 5.0 nm**
and "the CDI scale is set to 5 nm during the [enroute phase]". A WAAS unit uses
2.0 nm enroute; the GNS 530 (non-WAAS, this manual revision) uses 5.0.
**Fix:** `Phase.ENROUTE` full-scale → `5.0`.

### F2 — CDI scale changed in a hard step, not a gradual transition
The manual describes a *gradual* transition (§6.2: "gradual Course Deviation
Indicator (CDI) scale transition from 5.0 to 1.0 nm"), armed by phase of flight
(terminal within ~30 nm of the destination, approach ~2 nm from the FAF), not by
raw distance to the next fix. `main._phase_for` keyed the step to `dist_nm` to
the active TO waypoint only.
**Fix:** the avionics core now owns the scale. `GpsNav.update()` accepts an
optional `dt`, computes a target scale from distance-to-destination and whether
an approach procedure is active, and ramps the live scale toward it at
~4 nm / 30 s. `NavState.cdi_scale_nm` carries it; `instruments.compute_panel`
uses it when present and falls back to `GPS_FULL_SCALE_NM[phase]` otherwise
(so existing callers/tests are unaffected). With `dt=None` the scale snaps
(keeps non-loop callers deterministic).

### F3 — Graphic CDI had no numeric scale annotation
§3.3: "full scale limits for this CDI are … indicated at both ends of the CDI."
The `_cdi_strip` drew dots only.
**Fix:** `render._cdi_strip` prints the active full-scale value (`5.0` / `1.0` /
`0.30`) under each end of the scale when the source is GPS.

### F4 — No active-leg symbol above the CDI (Default NAV)
§3.3 Table 3-2 shows a symbol above the CDI for the active leg type (direct-to
arrow, course line, procedure turn, VTF, DME arc, hold).
**Fix:** `render._draw_nav_default` draws a marker beside the `from -> to` line:
`D>` (direct-to) for DTO, `->` (course line) for LEG, and the mode word for
OBS / SUSP. (PT / VTF / arc / hold glyphs depend on non-fix leg data the core
does not yet synthesise — see D4.)

### F5 — `NEXT DTK ###°` / `TURN TO ###°` advisory never rendered
§3.1 Fig 3-2 and the approach walkthroughs describe a two-stage advisory in the
lower-right of the screen: `NEXT DTK ###°` as a fix is approached, becoming
`TURN TO ###°` as the distance to the fix reaches zero. `NavState` carried
`wpt_alert` / `turn_anticipation` / `next_dtk` but nothing drew them, and there
was no "now turning" stage.
**Fix:** `NavState.turn_now` added (set when a >5° course change is pending and
the aircraft is within the turn-anticipation / capture distance). `render`
draws `TURN TO <mag>` when `turn_now`, else `NEXT DTK <mag>` when `wpt_alert`
and `next_dtk` is set, bottom-right of the 530 screen.

### F6 — OBS mode was unreachable from the running trainer
`set_obs` / `clear_obs` / `_obs_state` are implemented and tested, but the IFR-1
has no OBS key and no keyboard binding invoked them (`s` = `toggle_suspend`
only). The OBS course was also only drawn on the hand-drawn fallback bezel.
**Fix:**
* `GpsNav.toggle_obs()` — enable OBS seeded from the current DTK (or track),
  or cancel it.
* `GpsNav.nudge_obs(delta_deg)` — adjust the OBS course while OBS is active.
* `main` keyboard: `b` toggles OBS; `-` / `=` step the OBS course by 1°
  (`Shift` = 10°).
* `render._gns_unit` shows `OBS ###°` by the CDI strip on the real faceplate
  layout whenever OBS is active.

### F7 — Message queue was not surfaced (no MSG view, no indicator)
§1 / §16: the MSG key flashes and opens a Message Page. The core generates the
nav-data-expiry message into `self.messages` and `ack_messages()` exists, but
nothing consumed it and there was no on-screen indicator or page.
**Fix:**
* `GpsNav.peek_messages()` — read the queue without clearing it.
* `render`: `Scene.messages` + `Scene.show_messages`. When messages are pending
  an amber `MSG` appears in the annunciator bar; `show_messages` draws a
  Message box over the 530 screen listing them.
* `main` keyboard: `m` toggles the Message box; closing it acknowledges the
  queue.

### F8 — `CLR` held → Default NAV page was not implemented
§1.2: "Press and hold the CLR key to immediately display the Default NAV Page."
The IFR-1 event layer has no long-press, so a true hold is not available.
**Fix (approximation, documented):** `PageCursor.go_to_default_nav()`; a `CLR`
press with nothing to cancel (no Direct-To dialog, cursor off) jumps to the
Default NAV page. `main` also binds `Home` to the same action.

### F9 — COM flip-flop hold → 121.500 MHz not implemented
§1.2: "Press and hold to select emergency channel (121.500 MHz)." Same
long-press limitation as F8, and COM tuning is entirely IFR-1-driven (no screen
behaviour).
**Fix (capability + partial wiring):** `radios.ComRadio.set_emergency()` swaps
121.500 into the active field and preserves the previous active as standby.
Bound to `main` keyboard `F11`. Full IFR-1 wiring needs long-press events in
`ifr1.py` — see D9.

### F10 — Cancelling Direct-To did not resume on the nearest flight-plan leg
§4: "If a flight plan is still active, the GNS 530 resumes navigating the flight
plan along the closest leg." `cancel_direct_to` only dropped the Direct-To and
left `fpl.active` where it had been.
**Fix:** `cancel_direct_to` now recomputes the active leg as the flight-plan leg
whose segment is closest to the present position (`GpsNav._closest_leg`).

### F11 — Flight-plan list showed no per-leg DTK / DIS
§5.2: the Active Flight Plan Page shows desired track (DTK) and distance (DIS)
for each leg.
**Fix:** `render._draw_fpl` prints magnetic DTK and leg distance, right-aligned,
for each waypoint from the second onward. Truncation to `variant.screen_rows`
is unchanged (the 430 still shows fewer rows).

---

## Deferred — milestone-scale, tracked in WORKING.md

These are real gaps against the manual but each is a multi-day feature, not a
defect in the implemented surface. They are consistent with the project's stated
scope ("cheap 2D rendering", "correct avionics behaviour" first) and most are
already listed as parked in `WORKING.md` (M4 / M5 follow-ups).

**M14–M16 update (2026-09-11):** D4 + D5 done, D1 + D3 + D6 substantially done.
The remaining items are polish; each row below is annotated with its current
state.

| # | Gap vs manual | State | Tracked as |
|---|---|---|---|
| D1 | **Select Direct-To Waypoint page** — identifier spinner **done** (M11); WPT/NRST pick now available via their own pages + ENT (M15). **ENT-ENT activation fixed (2026-09-11)**: the page previously activated on a single ENT; sec.4.1 of the Pilot's Guide is explicit that ENT always confirms the identifier ("Activate?" highlighted) and a *second* ENT activates — with no shortcut even to re-centre on the already-active waypoint ("Press the Direct-to Key, followed by the ENT Key twice"). `DirectToEntry.confirming` gates this; CLR while confirming backs out to editing rather than closing the page outright. Still missing: facility/city name search (Spell'N'Find), a `CRS` course field, `+MAP` from the map pointer. | mostly done | WORKING.md "Done log" 2026-09-11 |
| D2 | **Flight Plan Catalog** — in-place add/delete/replace of the *active* plan (M15); **19 stored plans (FPL 01-19), recall/store/copy/invert/sort/delete + a comment line are DONE (2026-09-11)**: `GpsNav.fpl_catalog` + a Flight Plan Catalog page (ENT recalls, CLR deletes) + the MNU key's `FplMenu` pop-up (Invert/Copy/Sort/Delete Flight Plan); `FlightPlan.comment` (auto "ORIGIN/DEST", or `catalog_set_comment`) shown on the Catalog page and used to sort it; `crossfill(other)` copies the active plan to another `GpsNav` instance. Still deferred: an on-screen free-text comment editor (API only) and a live second FMS unit for crossfill to actually target (`main.World` runs one GPS; see M8 follow-up). | mostly done | WORKING.md "Done log" 2026-09-11 |
| D3 | **Interactive PROC picker** — on-screen approach/SID/STAR + transition selector. **DONE**: `gpsnav.ProcSelect` (`begin_proc_select`), a MENU → PROC → TRANS modal wizard on the PROC bezel key (`render._proc_page`), ending in `load_procedure`; Activate Approach / Vectors-To-Final once loaded. | **DONE** | WORKING.md "Done log" 2026-09-11 |
| D4 | **Non-fix procedure legs** — CA/VA/FA/CD/VD/FD/FC/CR/VR/CI/VI/PI/HM/HA/FM/VM synthesis + DME/RF arcs; MAP/HOLD/FAF map symbols. | **DONE (M14)** | — |
| D5 | **`SUSP` at the missed-approach point** and manual-termination legs (latched, no re-trip). **A holding pattern is actually flown**, not just suspended: the AIM 5-3-9 entry (`navmath.hold_entry`) + one circuit on synthetic outbound/inbound legs; a single-circuit HILPT (`HF`) auto-continues once re-established inbound, `HM`/`HA` repeat until SUSP is released. | **DONE (M14 + 2026-09-11)** | WORKING.md "Done log" 2026-09-11 |
| D6 | **Page bodies** — Default NAV, Flight Plan, Flight Plan Catalog, **VNAV**, an **in-screen Map** page, **WPT (Airport/INT/NDB/VOR)**, **NRST (APT/VOR/NDB/INT)** and the full **AUX group** (Trip Planning, Utility, Setup, Nav Data) are all drawn (M15 + 2026-09-11). Still stubs: NAV/COM and Position (both fall through to the Default NAV body). | mostly done | WORKING.md "Done log" 2026-09-11 |
| D7 | **VNAV** — **DONE (2026-09-11)**: `gpsnav.VnavProfile`/`VnavStatus` program a target fix/altitude/descent-angle and report distance-to-target, top-of-descent distance and vertical deviation once armed, on a dedicated VNAV page (ALT bezel key). Computed on demand from ownship altitude (`vnav_status(alt_ft)`) rather than folded into `update()`/`NavState`, so it's a page-and-a-half of guidance, not a fully-modelled vertical FMS (no "distance before" offset field, no coupling to the autopilot's VS/ALT capture). | **DONE** | WORKING.md "Done log" 2026-09-11 |
| D8 | **VLOC auto-tune** + **GPS→VLOC CDI auto-switch** on an active LOC/ILS approach are **DONE**: `--approach` stages the frequency to VLOC standby (`GpsNav._approach_vloc_freq`); `World._auto_vloc` auto-*activates* it near the FAF only when nobody is at the controls (GPS-follow, autopilot off) — with the AP flying APR/GS (the normal case) the pilot presses SWAP themselves and a one-shot `TUNE VLOC` message reminds them — and always auto-switches the CDI GPS→VLOC once the correct frequency is received. **Morse ident is DONE (2026-09-11)** as a visual blink (`radios.morse_is_keyed`, no audio backend) synced beside the station ident. | mostly done | WORKING.md "Done log" 2026-09-11 |
| D9 | **IFR-1 long-press events** — **DONE (2026-09-11)**: `ifr1.IFR1.poll()` times a held button off the wall clock (the device only pushes a report on a state change, so a sustained hold produces no further frames) and fires `Event.long_press` past `LONG_PRESS_S`; `route_event` binds `CLR`-hold → Default NAV (F8) and COM `SWAP`-hold → 121.5 (F9) directly to the device, with the keyboard `Home`/`F11` kept as a no-hardware fallback. | **DONE** | WORKING.md "Done log" 2026-09-11 |

---

## Not applicable to this trainer

TAWS / TERRAIN pages, TIS / GTS traffic, weather data link, FDE/RAIM pages,
Satellite Status page, checklists, fuel planning sensors, the instrument-panel
self-test page, crossfill to a second unit. These are out of scope per
`ARCHITECTURE.md` §1 (procedures trainer, not a systems simulator).
