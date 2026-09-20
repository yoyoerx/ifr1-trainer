# FINDINGS.md — GNS 530 function/graphics validation

Validation of the trainer's GNS 530 surface against the **Garmin GNS 530(A)
Pilot's Guide and Reference, 190-00181-00 Rev. H** (Dec 2009, Main SW 6.03).
Modules audited: `gpsnav.py`, `gns530.py`, `instruments.py`, `render.py`,
`main.py`.

Date: 2026-09-10. Re-validated against the actual Pilot's Guide PDF 2026-09-11
(prior passes worked from a paraphrased summary of the manual's text, not the
document itself).

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

**2026-09-11 re-validation:** pulled the actual 288-page manual PDF
(`static.garmin.com/pumac/GNS530_PilotsGuide.pdf`, Dec 2009 / Rev H — matches
this document's own citation) and cross-checked every §3.3/§6.2/§10.4 quote
verbatim. All of F1/F2/F3 hold as written. One real gap survived the original
pass: §10.4's departure-side CDI terminal-scale arm (symmetric with the
arrival-side one already implemented) was never coded — see the F2 addendum
below for the fix. D4 and D5 were independently re-checked against the manual
(non-fix leg types, DME/RF arcs, MAP/HOLD/FAF symbols, holding-pattern entry
and flown circuit) and are, as the table already stated, genuinely complete —
no further code was needed there.

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

**Re-verified 2026-09-11** against the actual Pilot's Guide PDF (not just a
paraphrase): §6.2 (p. ~89, "Non-Precision Approach Operations") and §10.4
("Setup Page") both confirm the exact wording and the 30 nm / 2 nm arm
distances above, word for word. §10.4 additionally states an arrival-side
ramp is only half the picture: *"when leaving the departure airport the CDI
scale is set to 1.0 nm and gradually ramps UP to 5 nm beyond 30 nm (from the
departure airport)"* — a **departure-side terminal-scale arm, symmetric with
the arrival-side one**, which the original F2 fix omitted (`_target_cdi_scale`
only ever looked at distance-to-destination). **Fixed this pass:**
`GpsNav._dist_to_departure()` (great-circle nm to `fpl.waypoints[0]`) added
alongside `_dist_to_destination()`; `_target_cdi_scale` now arms terminal scale
when *either* distance is `<= 30 nm`, not just the destination-side one.
Covered by `test_cdi_scale_is_terminal_near_departure_airport`
(`tests/test_gns530.py`); the two prior enroute-scale tests moved their sample
point to genuinely clear both 30 nm arms (their old point was only ~6 nm from
the departure fix, which is why they passed before the bug existed to catch).

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

### F12 — Approach CDI scale (0.30 nm) widened again right after the FAF
Playtest round 3: flying KLNS I08, the CDI scale correctly tightened to 0.30 nm
approaching POLCU (the FAF) but then opened back up to 1.0 nm just past it,
instead of staying at 0.30 nm through the MAP as the Pilot's Guide describes.
`_target_cdi_scale`'s approach arm was keyed off distance to whatever fix was
at the end of the *currently active leg* — fine while flying toward the FAF
itself, but once sequenced onto the FAF→MAP leg that "fix" became the MAP,
often several miles past the FAF, so the 2 nm arm distance no longer held.
**Fix:** `GpsNav._dist_to_faf` locates the loaded approach's actual FAF by its
`is_faf` flag and returns `0.0` (still armed) once it has been sequenced past,
so the 0.30 nm scale latches through the FAF→MAP segment instead of
re-evaluating against a leg endpoint that keeps changing.

### F13 — CDI scale visibly ramped 5.0 → 1.0 nm on a fresh start near the departure airport
Playtest round 3: starting a flight already inside the departure airport's
terminal arm still showed the scale begin at the 5.0 nm enroute default and
slew down to 1.0 nm over the first few seconds, instead of reading 1.0 nm
immediately. `_cdi_scale` was seeded to the enroute default in `__init__`
regardless of the actual starting position, and `_step_cdi_scale` always
slewed toward the target rather than snapping on the very first sample.
**Fix:** a `_cdi_scale_init` latch makes the first `_step_cdi_scale` call
snap directly to the phase-correct target; every call after that still slews
normally.

### F14 — No keyboard route to cancel an active Direct-To
Playtest round 3: activating Direct-To (`D`, identifier, `ENT ENT`) worked,
but nothing in the keyboard map could cancel it afterward — `Home` only jumps
to the Default NAV page, it doesn't send a CLR press. The IFR-1's own CLR key
already canceled correctly (`GpsNav._cancel` → `cancel_direct_to`); the
keyboard simply had no equivalent for a bare CLR outside a modal dialog.
**Fix:** keyboard `C` now sends a plain CLR event (`main._on_key`), matching
the hardware — cancels Direct-To / resumes the nearest leg, deletes the
selected Flight Plan or Catalog row, or backs out to Default NAV depending on
context, same as the IFR-1's CLR key.

### F15 — GNS 530 bezel legend missing VNAV; GNS 430 legend on the wrong cutouts
Playtest round 3: the 530's bottom softkey row is 6 keys (CDI/OBS/MSG/FPL/
VNAV/PROC); the legend loop only had 5 `fx` positions, so VNAV was never
drawn and PROC's label sat in VNAV's slot. Separately, the 430's faceplate
art was mislabeled entirely: the right-side RNG/D>/MENU/CLR/ENT cluster had
been labeled CDI/OBS/MSG/FPL/PROC, and the actual CDI/OBS/MSG/FPL/PROC
cutouts along the bottom were left blank.
**Fix:** `render._bezel_key_labels` — re-measured every cutout's center
against the actual rendered SVG art for both variants; the 530 row now
carries all 6 labels (CDI/OBS/MSG/FPL/VNAV/PROC) at the corrected `fy`, and
the 430 labels its own bottom row (CDI/OBS/MSG/FPL/PROC) plus its
RNG/D>/MENU/CLR/ENT cluster on the right, matching that variant's real
physical layout.

### F16 — Moving map drew a hold (or a procedure turn) as an undifferentiated ring/dot
Playtest round 3: "trainer is not drawing holds or PTs on the maps as would be
expected from the 530." The map's per-waypoint loop had exactly one symbol for
any waypoint with `hold=True` (a bare 6px ring, same for a single-circuit
HILPT and a repeating missed-approach hold), and a synthetic PI-leg "PT"
waypoint drew as a plain dot indistinguishable from any other fix — nothing
conveyed the actual racetrack shape or turn direction a real GNS 530 moving
map draws for a loaded hold, or the course-reversal shape for a procedure turn.
**Fix:** `render._hold_track_points` builds the real stadium/racetrack shape
(outbound leg, 180° turn, parallel inbound leg, 180° turn back to the fix)
from the waypoint's own `hold_inbound_true`/`hold_turn`/`hold_leg_nm` (or
leg-time-at-groundspeed when only `hold_leg_min` is published, matching
`GpsNav._start_hold`'s own conversion) — verified against the actual "turn at
the fix" geometry (a right-turn hold's racetrack sits on the inbound course's
+90° side), not just a plausible-looking sketch. `render._pt_symbol_points`
draws a small chevron, oriented along the course flown inbound after the
turn, at a PI leg's synthetic "PT" point instead of a plain dot — needed
`gpsnav._expand_leg`'s PI branch to also stash `hold_turn`/`hold_inbound_true`
on that waypoint (harmless: `hold` itself stays `False`, so nothing about how
a PI leg is actually flown changes, only what render.py has to draw it with).
Both shapes are ordinary `Point` lists run through the map's existing
track-up `project()`, so they rotate with the aircraft like everything else
on the map.

### F17 — A hold's INBOUND leg could "capture" (and sequence onward) miles off to the side of the fix
Follow-up question on F16: for KLNS I08's DALAC hold-in-lieu-of-PT, after the
single circuit, does the aircraft actually return to DALAC, or does it cut a
corner to intercept the DALAC→POLCU course instead? Traced with a real flight
simulation: it cut the corner, sequencing onto POLCU's course from **2.08 nm**
off DALAC — a real bug, not the intended "resume the flight plan" behavior.

Root cause: `_step_hold`'s INBOUND phase tracks progress against a synthetic
60 nm reference line running through the fix (needed to give the intercept
math room to work), and used **along-track distance on that line** as both
the pilot-facing DTG and the "close enough to the fix" capture test. Along-
track distance measures the position projected onto the line - it reads
near-zero whenever the aircraft is near the *far* end of the line, regardless
of how far off to the side (cross-track) it actually is. A wide entry (this
hold picked a teardrop entry, whose return leg is intentionally offset ~30°
- ~1.25 nm laterally - from the inbound course, on top of a course reversal
of ~210°) doesn't fully re-establish on the centerline before the aircraft's
along-track position reaches the fix's, so the old check declared "captured"
while genuinely still ~2 nm off the fix.

**First fix attempted, and why it wasn't enough on its own:** switching the
capture check to real straight-line distance to the fix (`great_circle_nm`)
fixes the false-early trigger, but on its own is too strict for a wide entry
whose intercept simply can't converge inside `_FIX_CAPTURE_NM` (0.30 nm) in
the available track distance - in testing, the aircraft's closest approach to
DALAC on this exact entry was ~1.7-1.8 nm, so a strict distance-only check
never fires and the aircraft flies on past the fix forever, chasing a hit it
will never land.

**Fix:** track the closest real approach seen during the INBOUND leg
(`hold_state["min_dist_to_fix"]`); capture on a clean hit (`<= 0.30` nm, the
common case - verified to land within ~0.03 nm of the exact threshold for a
simple direct entry) **or** once distance has opened back up by
`_HOLD_CPA_MARGIN_NM` (1.0 nm) past that closest point, i.e. the aircraft is
now moving away and this was as close as the intercept was going to get.
`dtg_nm`/`dist_nm` on the INBOUND leg both now report the same honest
straight-line distance to the fix throughout, instead of the old
along-track number that could read near-zero miles from the actual fix.

### F18 — Approach scale could arm from an unrelated, earlier leg's incidental proximity to the FAF
Playtest round 4 (re-test of F16): the 0.30 nm scale dipped in and then back
out again while still flying LRP→DALAC, well before DALAC's hold - let alone
the FAF-bound leg - was ever reached. `GpsNav._dist_to_faf` (F16's fix)
computed straight-line distance to the FAF from the aircraft's *current
position* regardless of which leg was active - on KLNS I08, LRP→DALAC
happens to track within ~0.3 nm of POLCU (the FAF) by pure incidental
geometry (the whole approach is roughly co-linear), which briefly armed and
then un-armed the scale on a leg that has nothing to do with the final
approach segment.
**Fix:** `_dist_to_faf` now returns `None` (no approach-scale evaluation at
all) whenever `fpl.active < faf_i` - i.e. before the FAF's own leg is even
active - instead of evaluating raw proximity regardless of procedural
progress. Only once actually flying the leg that ends at the FAF does real
distance start mattering; past it, the scale still locks at 0.30 nm as F16
already fixed.

### F19 — WPT sub-pages resolved any fix type, not just their own category
Playtest round 4 (G1): "WPT will show Airport, navaid, or intersection in any
of the pages, not filtered per page." The Airport/Intersection/NDB/VOR pages
(Pilot's Guide sec.4.2) all called the same unfiltered `GpsNav.lookup`/
`_resolve`, so typing an identifier on, say, the VOR page could resolve to an
airport or intersection sharing that identifier instead of only a VHF navaid.
**Fix:** `NavDatabase.find`/`nearest_fix` take an optional `kind` filter
("airport" / "vhf" / "ndb" / "waypoint"); `GpsNav.lookup(ident, page)` maps
the WPT page name to the matching kind via `_WPT_PAGE_KIND`. The Direct-To
page's own lookup (no `page` given) stays unfiltered, as it should - any
identifier type is a valid Direct-To target.

### F20 — VNAV had no vertical-speed or time-to-descend readout
Playtest round 4 (F1): "vnav should be in ft/min and yield time until
starting descent. angle not typical metric." The descent-angle field itself
is correct - it's the real GNS 530 VNAV page's own input (Pilot's Guide
sec.9) - but the page never derived the two numbers a pilot actually flies
to: required vertical speed and how long until top-of-descent.
**Fix:** `VnavStatus` gained `required_vs_fpm` (the descent rate that holds
the programmed angle at the current groundspeed) and `time_to_tod_min`
(ETE to the top of descent at the current groundspeed); `vnav_status` now
takes an optional `gs_kt`. `render._draw_vnav_page` shows both alongside the
existing DIS/TOD/DEV readouts. `angle_deg` is unchanged - it's still how the
profile itself is programmed.

### F21 — AUX > Setup had no editable fields at all; CDI/Alarms was missing entirely
Playtest round 4 (H1): "setup shows UNIT, CDI SRC, and BARO and NAV UNITS.
nothing is configurable. does not show CDI Alarms." The Setup page was pure
read-only display - Pilot's Guide sec.10.4 documents a CDI/Alarms field that
lets the pilot force a fixed maximum CDI scale instead of the automatic
phase-of-flight scaling, and nothing on this page did anything.
**Fix:** `GpsNav.cdi_alarm_max_nm` (`None` = Auto, the default) cycles
through Auto/5.0/1.0/0.30 nm via the Setup page's CRSR + inner knob
(`_cycle_cdi_alarm`); `_target_cdi_scale` applies it as a ceiling on the
auto-computed value (a genuinely tighter phase - e.g. an armed approach -
still tightens further than a looser fixed selection, it doesn't get
overridden by it). `render._draw_aux_setup` shows the field and its cursor.

### F22 — Weather page silently clipped the TAF instead of scrolling
Playtest round 4 (I1): "screen in 530 too small to show complete TAF and
cuts off data. CRSR should scroll?" `_draw_aux_weather` always showed the
first 3 METAR lines and however many TAF lines fit in the remaining space -
anything past that was simply never shown, with no indication more existed.
**Fix:** METAR and TAF are now built into one combined line list and CRSR
(cursor on, inner knob - `GpsNav.wx_scroll`) scrolls a window through it;
`^`/`v` markers in the corner show when there's more above/below. Switching
station (outer knob) resets the scroll position back to the top.

### F23 — VNAV's programmable field was a flight-path angle; the real GNS 530 programs a vertical speed
Playtest round 5 (F1): "vnav SETTING should be in ft/min not an angle... please validate why I think
this." Checked against the actual Pilot's Guide field labels (sec.10/11,
"Vertical Navigation"): the real page has **Target Altitude, Altitude
Reference (AGL/MSL), Target Distance + Before/After, Target Reference
waypoint, and "Vertical Speed Desired"** (ft/min, default a 400 fpm descent
rate) - there is no flight-path-angle field anywhere on the real unit. F20's
`angle_deg` field was a misremembered invention, and the round-4 response
defending it as "the real unit's own input" was wrong.
**Fix:** `VnavProfile.angle_deg` replaced with `vs_fpm` (+ = climb, - =
descend, default -400); `vnav_set`/`_vnav_edit` take/step it in ft/min
instead of degrees. `vnav_status` derives `required_alt_ft`/`tod_distance_nm`/
`deviation_ft` from `vs_fpm` at the current groundspeed (matching the real
unit's documented >35 kt groundspeed requirement for VNAV - below that the
page now shows why instead of just "target not ahead"), and `required_vs_fpm`
(VSR) is the live rate needed right now, independent of the programmed
profile - same as the real unit's VSR readout. `render._draw_vnav_page` shows
"VS PROFILE ±#### fpm" instead of "ANGLE #.#°".

### F24 — WPT page: sub-page name overlapped the identifier; no cursor underline
Playtest round 5 (G1): "word 'intersection' overlaps identifier on page.
identifier input should have underline hint for active character."
`_draw_wpt_page` drew the page name and the identifier on the same line only
70 px apart - "INTERSECTION" alone is wider than that at the small font - and
never drew a cursor indicator on the identifier at all (unlike the Direct-To
page and the Flight Plan edit field, which both already had one).
**Fix:** the page name now gets its own line; the identifier renders as
fixed-width per-character cells (same style as the Direct-To page) with an
amber underline on the character the knob is on.

### F25 — NRST lists couldn't scroll to reach farther-away results
Playtest round 5 (G2): "should be able to scroll the lists like the
metar/taf page to reach NRST POIs that are further away." `_draw_nrst_page`
always rendered `hits[:room]` regardless of the selected row - moving the
selection (outer knob) past the first screenful of results moved it
off-screen with nothing ever scrolling to show it.
**Fix:** the visible window now follows the selection (same idea as the
Weather page's scroll), with a `^`/`v` marker when there's more above/below.

### F26 — Weather page's scroll could overshoot and require scrolling back through it
Playtest round 5 (I1, re-test): "if you overshoot the bottom of the list and
scroll further, [it] makes you scroll back that number of steps before
returning to scroll behavior. should just cap and then reverse." `wx_scroll`
was incremented without limit in `GpsNav` (which has no way to know the
rendered line count - the same reason the Charts page's `chart_sel` is
deliberately left unclamped there too) while the display clamped it only at
draw time, so a fast scroll past the end left the underlying counter far
past the real maximum; scrolling back had to first "use up" that overshoot
before the screen moved again.
**Fix:** `render._draw_aux_weather` writes the clamped position back into
`gns.wx_scroll` each frame it draws, so the counter can never actually run
past what's real - reversing direction takes effect immediately.

### F27 — Autopilot NAV mode ignored the CDI source switch, always flew GPS
Playtest round 5 (L3): "NAV tuning and OBS rotation work but NAV source for
the AP does not change to the NAV VOR when CDI is pressed. Expected behavior
is to change the AP NAV source depending on the CDI state." `Autopilot`'s NAV
lateral mode always steered off the GPS `nav_state` regardless of which
source the GNS CDI was actually displaying - only APR/REV ever flew the raw
VOR/LOC needle. On the real S-TEC 55X, NAV couples to *whatever the CDI is
currently showing*: switch CDI source to VLOC (the SWAP/CDI key) and NAV
should track the VOR/LOC needle, the same way it would with a simple
non-GPS nav radio.
**Fix:** `Autopilot._lateral_command`/`_maybe_capture_lateral` read
`nav_state.cdi_source` (already on `NavState`, no new parameter needed) and
switch NAV's tracking source and capture criterion to the VLOC
course/deflection whenever it reads "VLOC", falling back to the existing
GPS/xtk tracking otherwise. APR/REV are unchanged (always VLOC, as before).

### F28 — MNU key had no keyboard route at all
Playtest round 5 (O1): "Menu button needs a key." The Flight Plan / Flight
Plan Catalog page menu (Invert/Copy/Sort/Delete) was reachable only via the
IFR-1's physical MNU key - documented as a known gap, but never actually
fixed until now.
**Fix:** keyboard `X` sends a plain MNU press (`main._on_key`); once the menu
pop-up is open, Up/Down/Enter/Esc drive it the same way the PROC selector's
keyboard handling already works.

### F29 — `stack` layout: WX/PLATE could only ever show the departure airport
User feedback after building `stack` (2026-09-18): "plates page is only
showing the departure airport. would like it to show both. WX displays, but
not possible to switch between the departing or arriving airport." Both
`_draw_aux_weather` (WX tab) and `_draw_stack_plate` (PLATE tab) already
picked their station off `gns.wx_sel`/`gns.chart_airport_sel`, and the WX
tab even drew a strip of every flight-plan airport - but nothing in `stack`
could ever change either value: normally the GNS bezel's outer knob does
that (`gpsnav._page_edit`'s "Weather"/"Charts" branches), and `stack`'s
mouse input had no equivalent click target. The PLATE tab didn't even draw
an airport strip, so there was no visual sign a second airport existed.
**Fix:** `_draw_aux_weather` now registers a `wx:airport:N` click rect per
ident in the strip it already drew; `_draw_stack_plate` gained its own
airport strip (`plate:airport:N`) plus, when an airport has more than one
cached plate, a chart strip (`plate:chart:N`). `main._on_stack_click`
dispatches all three, mirroring `_page_edit`'s own behavior (switching
airport resets `wx_scroll`/`chart_sel` back to the top/first chart rather
than leaving a stale scroll position or chart index from the previous
airport). Also fixed in passing: `plate:load` always fetches via the same
unit (`w.gns`) the tab actually displays, rather than a `kbd_fms2`-routed
unit that could silently mismatch it.

### F30 — `stack` layout: PLATE's chart list was redundant; middle column too wide
Two pieces of the same session's follow-up feedback (2026-09-18). First:
"plate list of procedures is redundant/overflows with STR STR STR IAP IAP DP
DP DP DP DP DP, maybe all these condense as a filter to show all the IAPs or
all the DPs etc. rather than repeating." F29's chart strip showed only
`chart_code` per chart - meaningless for telling two ILS/RNAV approaches
apart (both just say "IAP") and unbounded, so a busy airport's row ran off
the panel. Second: "NAV head layouts is janky. The boxes are way too wide.
they should shrink to the right and allow the tabs with the WX, MAP, PLATES
to get bigger." The middle column (`_stack_layout`) sized itself to
`W - left_w - right_w`, but `draw_nav_head` left-biases its round card
(`_card_geometry`) - past ~440px the extra width was just dead panel
background, and the fixed 340px left column was starved of it.
**Fix:** `_draw_stack_plate`'s chart section is now a scrollable, filterable
list (`_STACK` chip row: `ALL` + each distinct `chart_code` actually present,
click to narrow; a 4-row scroll window, same technique as `_draw_nrst_page`)
showing the full `chart_name` per row, not just its category - entries are
now distinguishable and bounded regardless of how many procedures an airport
has. Switching the filter snaps `gns.chart_sel` onto the first still-visible
chart if the old selection just got filtered out. Separately, `_stack_layout`
now gives the middle (NAV1/NAV2/HDG) column a fixed 440px width instead of
whatever the window happens to leave over, handing the freed space to the
left tab column.

### F31 — `stack` layout: still-wide nav heads, overlapping AP panel, no place for the AP bugs
Immediate follow-up in the same session (2026-09-18): "still a lot of dead
space around the nav heads. shrink more right. expand the tabs column. make
sure the PDF expands with it. there is some overlapping of the AP and things
at the bottom. actual IAS, HDG, ALT should be displayed next to the heading
indicator, in placement similar to the OBS displays next to NAV1/NAV2." F30's
440px middle column still left real dead space (NAV1/NAV2's info column only
ever reaches ~320px in). Rendering an actual frame to a PNG (rather than
guessing from code) found the real cause of "overlapping ... at the bottom":
`draw_ap_panel`'s VS-window column position (`vcol`) is computed assuming a
box as wide as `steam`'s (~900px) - at `stack`'s narrower right column it ran
straight off the box's own right edge, its text landing on top of whatever
sat next to it. The HDG/IAS/ALT bug boxes, meanwhile, sat in a separate row
under the tab panel with no visual link to the HDG dial they set - and
`draw_ap_panel`'s own side info box (HDG BUG/ALT SEL) duplicated them.
**Fix:** middle column narrowed 440 -> 380px. `draw_ap_panel` gained a
`show_info=False` mode (`stack` only - `steam` is unaffected, still
`show_info=True` by default) that drops its own side info box (now
redundant) and, when the VS window doesn't fit beside the mode-button row at
this width, wraps it onto its own line underneath instead of overflowing.
HDG/IAS/ALT are now shown *and* edited (click +/-) in a new
`Renderer._stack_hdg_info` column to the right of the HDG dial, at the exact
x `_card_geometry` gives NAV1/NAV2's own info columns - the same placement
pattern, not a coincidence. Dropping the bottom AP-bug row also handed its
height back to the tab content area, so PLATE/MAP/WX all gained *height* as
well as the *width* freed by the narrower middle column - `_draw_stack_plate`
already sized its rendered image off the passed-in rect rather than a fixed
constant, so the plate image grows with it for free, no separate fix needed.
(`_stack_hdg_info` was renamed `_stack_hdg_actuals` in F32, below - it split
into a read-only display and a separate editable widget.)

### F32 — `stack` layout: dial still centered, actuals conflated with AP set points
Immediate follow-up (2026-09-18): "heading indicator should be left aligned
in it's box so that is stops overlapping the ALT, IAS, HDG displays. I would
like the behavior change for the ALT, IAS, HDG. in the HG box, they should be
actuals, and the set point for these should be back under the tabbed
columns." F31's fix computed the HDG/IAS/ALT info column's x-position from
`_card_geometry` (left-biased), but `draw_hdg_indicator` itself still drew
the dial centered in its box (`cx = rect.centerx`, never actually changed
when the info column was added) - two different geometries for the same box,
so the (correctly positioned) info column overlapped the (still-centered)
dial. Separately, that info column showed the *autopilot's* HDG bug/IAS
target/ALT preselect, editable right there - not the aircraft's actual
heading/airspeed/altitude the placement (beside the dial, like NAV1/NAV2's
actual-value-adjacent OBS) implied.
**Fix:** `draw_hdg_indicator` now calls `_card_geometry` itself, the same
left-biased geometry `draw_nav_head` uses and the one `_stack_layout` was
already assuming for the info column - both come from one call site instead
of two independently-computed positions that could drift apart.
`Renderer._stack_hdg_info` (shown *and* edited) split into
`_stack_hdg_actuals` (next to the dial, read-only, sourced from
`Scene.sixpack` - the same actual-value snapshot `draw_six_pack` itself
reads) and `_stack_setpoint_bugs` (the editable HDG/IAS/ALT boxes, restored
under the tab column where they lived before F31 removed them).

### F33 — NAV1 round head never followed the GNS's own CDI/VLOC switch
User report (2026-09-18): "The nav1 head is not properly wired into GPS.
Expected behavior is that when GPS is selected on the CDI on the gns it
should display the CDI on the corresponding nav head. And then when CDI is
pressed it should switch over to the VOR or v-loc frequency." The round
NAV1 head (`steam` and `stack` layouts) was built purely from the tuned
NAV1 VOR/LOC receiver (`instruments.nav_head`), completely independent of
`nav.cdi_source` - it showed a raw VOR/LOC deviation (or "OFF" if untuned)
regardless of whether the GNS's own CDI key was set to GPS or VLOC, even
though `panel.cdi` (the CDI strip on the GNS screen itself) and the
autopilot's NAV mode (F27) already switch correctly. On the real airplane
this instrument is a GPS-slaved analog CDI: its GPS/VLOC mode literally is
the GNS's own CDI key output, not an independent reading.
**Fix:** `instruments.gps_nav_head(nav, panel)` repackages `panel.cdi`
(already the correct GPS-or-VLOC deflection) into a `NavHead`, so
`render.draw_nav_head`/`draw_hsi_head` don't need a second drawing path.
`Renderer._nav1_view(sc)` picks it (with `source_kind="GPS"`, which
relabels "OBS"/"CRS" -> "DTK" and "DME" -> "DIS", and skips the Morse-ident
blink dot - a waypoint doesn't transmit Morse) while `nav.cdi_source ==
"GPS"`, else falls back to the tuned NAV1 receiver exactly as before.
NAV2 is untouched - a second, always-independent VOR/LOC receiver, the way
a real second nav radio is.

### F34 — PROC selector only ever loaded a procedure, never offered to activate it
User report (2026-09-18): "when using the procedures button, when a
procedure is selected, it should provide an option to load or to activate.
Current behavior only loads and you have to do extra steps to activate.
Review the Garmin POH and verify the correct button sequences." Verified
against the Pilot's Guide (190-00181-00 Rev. H) sec.5, p.61 step 5 (text
extracted directly via `pypdfium2` from a mirror with a real text layer,
since the official Garmin PDF has none - see F23's note): "Rotate the large
right knob to highlight 'Load?' or 'Activate?' (approaches only) and press
ENT. ('Load?' adds the procedure to the flight plan without immediately
using it for navigation guidance... 'Activate?' adds the procedure to the
flight plan and begins navigating [it].)" This trainer's `ProcSelect`
wizard (`_proc_advance`) skipped straight from picking the transition to
loading (`_proc_load`, unconditionally) - the pilot could only activate
afterward by reopening PROC and choosing "Activate Approach?"/"Activate
Vectors-to-Final?" from the menu (which do exist, gated on `_approach_active`
once something is loaded - that part already matched the manual's *later*
re-activation flow on p.62, it's just the *initial* Load?/Activate? choice
that was missing).
**Fix:** `ProcSelect` gained a `LOADACT` step between TRANS and closing the
dialog: `_proc_begin_loadact` offers `["Load?", "Activate?"]` for an
approach (`"Load?"` highlighted by default, matching the manual) or just
`["Load?"]` for a SID/STAR (activation for those stays the later, separate
flow - the manual scopes "Activate?" here to approaches only). Choosing
"Activate?" loads the procedure then immediately calls the same
`_activate_approach(vtf=False)` the PROC-menu "Activate Approach?" item
uses - which naturally reduces to Vectors-to-Final behavior when the chosen
transition was VECTORS (no IAF ends up in the flight plan, so
`_activate_approach` targets the FAF instead, same as it always did).
CLR backs out through the new step correctly, including when TRANS itself
was skipped (a procedure with no named transitions) via a new
`has_trans_step` flag, so CLR doesn't try to reconstruct a transition list
that was never shown.

### F35 — `stack`: no way to tune COM2/NAV2 single-unit; FMS2 didn't drive NAV2 in --dual
User request (2026-09-19): "For the stack layout that is not dual, there
should be a radio 2 and nav 2 display that is the same height as the
autopilot so that we could tune those frequencies. And the dual
configuration, fms2 should drive nav2." Single-unit `stack` had nowhere to
see or tune COM2/NAV2 (only via the IFR-1's COM2/NAV2 modes, with no
on-screen readout); in `--dual`, NAV2's round head was always the raw NAV2
receiver and FMS2's CDI strip reused FMS1's panel - so FMS2's CDI key did
nothing visible outside its own screen.
**Fix:** single-unit `stack` gets `Renderer._stack_radio2`, a COM2/NAV2
panel (active + standby, flip-flop, standby MHz/kHz +/-; hit keys
`radio:<com2|nav2>:<swap|mhz+|mhz-|khz+|khz-|>`, handled in
`main._on_stack_click` through the same `ComRadio`/`NavReceiver` methods the
IFR-1 modes use) in the slot a second GNS would take, exactly `ap_h` tall.
`--dual` has no such panel - FMS2 is the second radio stack's front end.
For `--dual`, `World._panel2` builds FMS2's own `compute_panel` (NAV2 as its
VLOC source, mirroring how `_panel` uses NAV1 for FMS1), carried on
`Frame.panel2`/`Scene.panel2`; `Renderer._nav2_view` (mirror of
`_nav1_view`, F33) shows FMS2's GPS course deviation while FMS2's CDI source
is GPS and the tuned NAV2 receiver once FMS2's CDI key selects VLOC, and the
FMS2 unit's own CDI strip now uses `panel2` too (`_unit2_scene`, also in the
`dual` layout). FMS1's CDI key has no effect on NAV2.

### F36 — Untuned NAV head's card snapped to north instead of following the OBS
User report (2026-09-19): "even when off, the VOR OBS should match the
setting, not just default to north." `instruments.nav_head` returned a bare
`NavHead(valid=False, obs_deg=...)` for an untuned receiver, leaving
`course_deg` at its 0 default - so the round head's rotating card showed
north up (with the OBS readout correct beside it) whatever the OBS was set
to. **Fix:** an untuned head's `course_deg` now equals the OBS setting, so
the card follows the OBS knob with the OFF flag showing, like a real CDI.

### F37 — SETTINGS wind read 0 with a winds-aloft profile loaded
User report (2026-09-19): "the wind in the setting should show what the
current wind that is loaded is rather than just read 0." The SETTINGS tab
read `SimModel.wind_from/wind_kt` - the *uniform* wind, 0 whenever a
winds-aloft profile (`--winds-aloft`, `--wx-region`) is what's actually
acting. **Fix:** `SimModel.current_wind()` (the profile at the current
altitude, else the uniform wind) feeds the readout, which is labelled as
winds aloft when so; the +/- buttons start from that effective wind (and,
as before, replace the profile with a uniform wind once used).


### F38 — APR/NAV(VLOC) parked off-centre in a crosswind (no wind-drift trim)
Playtest (2026-09-19, KLNS ILS 08): "Tracking the klns ils 08 was very poor.
We were left of course compared the the CDI but the AP in APR with GS made
no effort to correct the course to the right." Reproduced in a headless sim
(`--approach "KLNS I08"`, APR, 20 kt crosswind): with no wind the AP tracked
fine, but with a crosswind the needle settled at ~0.4 deflection and stayed
there - the VOR/LOC path in `Autopilot._lateral_command` was
proportional-only (`dev * _VLOC_GAIN`), so a steady drift produced a steady
intercept angle that exactly balanced it, i.e. a permanent offset (the GPS
NAV/GPSS paths already had an integral `_xtk_i`).
**Fix:** `Autopilot._vloc_intercept` adds an integral wind-drift trim to
APR/REV and NAV-on-VLOC (shared `_xtk_i`, dt-based so time warp is unaffected). **First cut was
wrong** ("something is now majorly wrong with the ap"): it integrated
throughout the capture band (|dev| <= 0.75), so the trim wound up during a
normal intercept and overshot the localizer even in still air. Now it only
integrates while nearly centred (|dev| <= 0.2, `_VLOC_I_BAND`), is capped
at 8 deg, and bleeds off during an intercept. Headless KLNS I08 sims: no wind
now captures with only a small overshoot; 20 kt crosswind settles at ~0.02
deflection instead of parking at ~0.4.


### F39 — Winds-aloft decode read the wrong columns (135 kt at 5000 ft)
Playtest (2026-09-19, `--plan "EMI KLNS" --wx-region BOS --wx-station EMI`):
"the AP on NAV (either with or without GPSS) puts us on TRK of 083 even
though DTK is 051." Not an autopilot bug: the cached EMI profile held a
single level, `39000 ft 320@135`, which `windsaloft` (holding the nearest
level's value outside the table) applied at 5000 ft - a 135 kt crosswind on
a 140 kt aircraft that no lateral mode can hold. Root cause was
`datasrc.wx.decode_fd_text`: real NWS FD groups are **right-aligned** under
their header numbers, but the decoder sliced from each header's *left* edge
(the synthetic test fixture was left-justified, so it agreed with the bug).
On real text it read the wrong columns and only the last one survived.
**Fix:** slice the 7 columns ending at each header's right edge; the fixture
now uses the real right-aligned layout and a verbatim real-text regression
test was added. EMI now decodes to nine levels (e.g. 5000 ft ~ 8 kt); a
headless EMI-KLNS NAV run tracks DTK with ~0.0 nm xtk. Refetch stale caches
with `python -m datasrc.wx winds-aloft BOS` (the auto-refresh does it on
launch).


### F40 — VOR/LOC ident Morse was ~3x too fast
User report (2026-09-19): "the morse code character rate seems too fast
compared to the FAA standard rate on the VORs." `radios.morse_is_keyed`
defaulted to 20 wpm (0.06 s dit); the FAA AIM (1-1-3) puts VOR/localizer
identification at approximately 7 wpm. **Fix:** `radios.VOR_IDENT_WPM = 7.0`
(dit ~= 0.17 s, PARIS standard) is now the default, so the blinking ident
dot beside NAV heads/radios keys at the real rate. The gap before the ident
repeats is unchanged (still shortened from the real ~7-10 s).


### F41 — Ident repeat gap was ~1.5 s, real stations repeat every ~7.5 s
User request (2026-09-19): "lengthen the ident repeat gap to match the real
interval." The Morse ident looped with a 1.5 s pause (deliberately
"collapsed" earlier). A real VOR/localizer sends its ident about four times
per 30 s cycle (FAA: at least once every 30 s). **Fix:**
`radios.MORSE_REPEAT_PERIOD_S = 7.5` is now the start-to-start interval; the
silence is whatever remains after the ident (minimum 1.5 s so a long ident
still gets a gap). The 7.5 s figure is the typical four-per-30-s cadence,
not a value I looked up in the AIM this session.


### F42 — Glideslope was a diamond; now a horizontal needle with a GS flag
User request (2026-09-19): "i don't like the glide slope as a diamond, i
would rather have the horizontal needle showing GS and a gs flag when off."
`render._gs_scale` drew a green diamond (and nothing at all when the GS
was invalid). It now draws a horizontal needle riding the vertical dot scale
(above centre = fly up, as before) and a red "GS" flag box in place of the
needle whenever the glideslope isn't valid (a localizer with no usable GS,
or GS lost). Applies to `draw_nav_head` and `draw_hsi_head` in every layout.

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
