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


### F43 — Missed-approach hold at KUPPS (KLNS ILS 08) turned the wrong way
Playtest (2026-09-19): "when going missed on the ILS 08 at KLNS, the hold at
KUPPS is incorrectly flown." KUPPS is a left-turn hold (inbound 152.7 mag).
Flying the missed in a headless sim: the entry choice was right (arriving
on the outbound course -> teardrop, leg heading outbound+30), but the turn
from the teardrop/outbound leg back onto the inbound course went
**right ~150 deg** (352 -> 44 -> 134 -> 162) instead of left ~210 deg. The
hold is flown as synthetic legs and the sim only ever takes the shortest
turn, so the return turn ignored the hold's direction; the aircraft ended
up southeast of the fix flying *away* from it while "inbound", and the lap
"completed" at the closest point of that bad pass. The same shortest-way
ambiguity applied to every racetrack lap's 180 deg turn, and to a parallel
entry's first turn, which AIM 5-3-8 flies opposite the hold direction.
**Fix:** `GpsNav._step_hold` records `entry`, and when the INBOUND leg
begins sets `turn_dir` (the hold's direction; opposite for a parallel
entry's first lap). Until the track is within 100 deg of the inbound course
(`_HOLD_TURN_DONE_DEG`) it publishes a course 80 deg ahead of the track on
that side, so the follower keeps turning the correct way, then hands back
to the normal intercept. Sim now: 352 -> 300 -> 210 -> 141, intercepts
inbound, and later laps turn left too. Two new tests fail without the fix.


### F44 — AP NAV/GPSS/APR wandered slowly about the course in any wind
Playtest (2026-09-19): "The autopilot does not seem to hold course tightly,
there is a lot of deviation and slow oscillating compared to holding a
perfect line." Measured in a headless EMI-KLNS run: still air was perfect
(0.001 nm), but a 25 kt crosswind gave 0.10 nm RMS / 0.30 nm max cross-track
in a slow (~5 min) damped oscillation. Cause: the AP commands a *heading*
(DTK +/- an intercept angle) and knew nothing about wind, so the needed crab
was learned only by the slow integral trim, overshooting on the way.
**Fix:** the AP now measures drift (`own.track_deg - own.heading_deg`) and
subtracts it from the heading command (`Autopilot._drift`, capped at 30 deg)
in NAV (GPS and VLOC), GPSS and APR/REV - i.e. it steers a desired *track*
and crabs into the wind immediately, as a real GPS-steering AP effectively
does. Same runs: 25 kt crosswind 0.003 nm RMS / 0.009 max (NAV), 0.003 /
0.007 (GPSS), 40 kt crosswind 0.001; the ILS 08 intercept still converges.
The F38 integral trim stays as a small residual term.

---

### F45 — DME arcs were flown as 4 straight chords (0.8 nm off on a long arc)
Playtest (2026-09-19): fly a DME arc and verify it. KLNS D08 (11 nm, 55 deg)
was within 0.15 nm, but PABR S26 (15.9 nm, 178 deg - the longest arc in the
2609 CIFP) scalloped **0.8 nm inside the arc** (0.59 nm RMS): `_arc_points`
replaced every AF/RF leg with a fixed 4 straight chords. **Fix:** the arc is
now a real constant-radius leg. The fix that ENDS an arc carries
`PlanWaypoint.arc_centre` / `arc_turn` (no synthetic `~n` points any more);
`update()` computes DTK as the arc tangent at ownship, XTK as the distance off
the circle (`navmath.arc_xtk_nm`, + == right of the path, so inside a clockwise
arc is +), DTG as the arc length remaining, and turn anticipation from the
tangent at the arc end. `_distance_to_index`, `_closest_leg` and the map
(`render`: arcs drawn as curves via `navmath.arc_points`) are arc-aware. Same
runs: PABR S26 0.08-0.13 nm RMS (0.06 nm steady state; the rest is the
initial capture), KLNS D08 0.01 nm RMS (was 0.10); a coupled AP in NAV holds it
in a 30 kt wind. **Manual note** (docs/reference/GNS530_Pilots_Guide.pdf, sec.5
"Flying a DME Arc Approach", p.71-73): this 2003 manual describes the arc as
Jeppesen-provided *waypoints* along the arc (`D258G`...) flown leg to leg with
"NEXT DTK"/"TURN TO" and a periodic "Set course to ###" message for an
external CDI - i.e. the older units approximated the arc with database
waypoints. The CIFP/ARINC 424 data we load codes it as an AF leg (centre +
radius), which is what a modern WAAS unit flies as a true arc, so the trainer
follows the AF leg; the manual's DTK-updates-along-the-arc behaviour is
preserved (DTK is the live tangent). Not implemented: the "Set course to ###"
and "Steep turn ahead" (arc turn-anticipation > 90 s) messages.

---

### F46 — DCT on a highlighted flight-plan waypoint / Activate Leg?
Request (2026-09-20): on the real 530 you select a step in the flight plan and
press direct-to to jump to that leg, instead of the dialog asking for a new
waypoint. Pilot's Guide text (docs/reference/GNS530_Pilots_Guide.pdf; page
numbers are the manual's own), quoted rather than paraphrased:
- **Activate a leg by DCT (sec.4, p.60):** "1. Press the small right knob to
  activate the cursor and rotate the large right knob to highlight the desired
  destination waypoint. 2. Press direct-to twice to display an 'Activate Leg?'
  confirmation window. 3. With 'Activate?' highlighted, press ENT." Applies to
  procedure legs too (procedure turn, DME arc, hold). Repeated in the Troubleshooting
  Q&A ("How do I skip a waypoint in an approach...": "highlighting the desired
  waypoint and pressing the direct-to key twice, then ENT to approve").
- **Activate a leg by menu (sec.4, p.55):** highlight the destination waypoint,
  "Press MENU, select the 'Activate Leg?' option ... and press ENT. A
  confirmation window appears. With 'Activate?' highlighted, press ENT."
  "'Activate Leg?' selects the highlighted flight plan leg as the leg currently
  in use for navigation guidance (even if it isn't the closest leg)."
- **DCT on a list (sec.3, p.47):** "If a list of waypoints is displayed on-screen,
  press the small right knob to activate the cursor, rotate the large right knob
  to highlight the desired waypoint, then press direct-to followed by ENT twice."
  (p.44: "Pressing the direct-to key displays the Select Direct-to Waypoint Page.")

**Not stated by the manual:** what the screen shows between the first and the
second DCT press when it is used to activate a leg. Only the end state ("Activate
Leg?" after two presses) is specified. The trainer therefore shows the Direct-To
page pre-filled with the highlighted waypoint after press 1 (the p.47 list
behaviour) and swaps it for "Activate Leg?" on press 2; that intermediate frame
is the trainer's choice, not the manual's.

Before: DCT always opened the dialog pre-filled with the *active* TO waypoint
whatever the cursor highlighted, and the FPL menu had no "Activate Leg?".
**Fix (`gpsnav`):** cursor on a flight-plan waypoint -> first DCT pre-fills that
waypoint (`_fpl_cursor_row`; ENT, ENT is then an ordinary Direct-To); second DCT
-> the "Activate Leg?" window (`_leg_confirm`, drawn by
`render._activate_leg_page`); ENT -> `_activate_leg`: the plan leg (previous
waypoint -> highlighted one) becomes active, dropping any Direct-To, SUSP or
hold - the course is the plan's own leg, not present position to the fix. The
MENU path (`ACTIVATE LEG`, first option when a leg is highlighted) reaches the
same window. CLR cancels. Row 0 (the departure) has no leg ending at it, so it
stays a plain Direct-To. Keyboard: `D` twice.

---

### F47 — Glideslope needle on the face of the NAV head
Request (2026-09-20): put the horizontal GS needle on the head itself, as on a GA
VOR/OBS/ILS indicator, not in a scale beside it. F42's needle rode a separate
vertical dot scale to the right of the card. **Fix:** `render._gs_scale` now
draws on the dial face - dots down the left and right edges and a horizontal
needle sweeping the full width of the face (above centre = fly up), with the red
"GS" flag on the face when the glideslope is invalid. The info column no longer
shifts right for a GS scale (`draw_nav_head`, `draw_hsi_head`).

---

### F48 — CIFP parser dropped localizers whose category is a letter

Found while building `faa2xp`: `_localizer_from_pi_line` parsed the P.I category
byte with `opt_int`, so the 17 localizers whose category is a letter (e.g. `A`,
LOC-only/LDA-style) raised `ValueError` and were skipped, silently, as "malformed".
Now a non-digit category is 0 (LOC-only). Regression coverage: the KBIH LOC-only
record in `tests/test_faa2xp.py`.

### F49 — Autopilot / GPS-follow intercept of an active leg: 45 deg cut, then roll onto the course

**Symptom:** off a leg, the trainer's autopilot (and the scripted GPS-follow) curved
gradually toward the next fix instead of cutting to the leg and tracking it. Cause: the
intercept was purely proportional (`-xtk * 8 deg/nm`, cap 30; follow-leg `12 deg/nm`),
so the angle shrank continuously as the offset closed - 6 nm off, heading was still
~5 deg from the bearing to the fix after 7 minutes, reaching the course line only
asymptotically near the fix.

**What the documents say.** The GNS 530 Pilot's Guide is silent on the autopilot's
intercept angle: it only says the autopilot follows the course selected on the external
CDI/HSI (pp.80, 94, 189). The behaviour is the autopilot's. For the S-TEC 55X the POH
(4th Ed., Nov 30 2007, sec.3.1.2, printed p.3-4) states: "If the Course Deviation
Indication (CDI) is at full scale (100%) needle deflection from center, then the
autopilot will establish the aircraft on a 45 degree intercept angle relative to the
selected course. Even if CDI needle deflection is less than 100%, the autopilot may
still establish an intercept angle of 45 degrees, provided that the aircraft's closure
rate to the selected course is sufficiently slow. Otherwise, the intercept angle will
be less than 45 degrees." ... "the turn will always begin between 100% and 20% CDI
needle deflection" ... "it limits the aircraft's turn rate to 90% of a standard rate
turn" ... "When the aircraft arrives at 15% CDI needle deflection, the course is
captured." Sec.3.1.3.1 gives NAV GPSS the same sequence ("establish the aircraft on the
selected intercept angle ... until it must turn the aircraft onto the next course segment
to prevent overshoot"). Source: https://www.flying20club.org/documents/Sys_55_X_POH_(4th_Ed).pdf

**Fix.** `autopilot.nav_intercept_deg(xtk, scale, gs)`: a flat 45 deg cut back toward the
course until the turn-in distance, then an angle shrinking linearly to zero at the course
line. Turn-in distance = the lead a 90%-standard-rate turn from 45 deg needs (x1.5
margin), clamped to 20%..100% of the current CDI full scale (5 / 1 / 0.3 nm). Used by
NAV and GPSS on GPS legs and by `SimModel.follow_leg` (same function, so the two paths
agree). The wind-drift integral only accumulates inside the 15% capture band (it wound
up during the 45 deg cut); the "still intercepting" annunciator clears at 15% of the CDI
scale instead of a fixed 1.2 nm. Headless: 6 nm off, 110 kt, wind 0 / 270@30 / 120@35 ->
max overshoot 0.00-0.01 nm, on the course line in 235-380 s; KLNS D08 / PABR S26 arcs
unchanged (RMS 0.01 / 0.08 nm); ILS 08 (VLOC path) byte-identical.

**Not modelled** (POH-described, not implemented): a sub-45 deg cut at high closure rate,
the CAP / CAP SOFT gain steps 15 / 30 / 75 s after capture, and the pilot-selectable
intercept angle (HDG bug + HDG/NAV). The VOR/LOC (VLOC) intercept keeps its own tuned
law. The 1.5x turn-in margin is the trainer's choice (POH: 20-100%, "variable").

### F50 — NAV / APR / REV coupler follows the S-TEC 55X intercept-capture-track timeline

Second step of the POH review (`docs/AUTOPILOT_POH_REVIEW.md`, items R1-R8, R10). The VOR/LOC
path was a 22 deg/unit proportional law capped at 30 deg, one authority level throughout,
and every mode turned at 100% of standard rate. Now one coupler (`Autopilot._couple`) serves
NAV on a GPS leg, NAV on VOR/LOC, APR and REV, per S-TEC 55X POH (4th Ed.) sec.3.1.2, pp.3-4/3-5:

* **INTERCEPT** - flat 45 deg cut to the course ("full scale ... 45 intercept angle"), rolling
  out over the last stretch before the 15% band; the turn-in deflection is closure-rate based,
  held to the POH's 20-100% ("the turn will always begin between 100% and 20%").
* **CAP** at 15% deflection ("the course is captured"), turn-rate limit 90% of standard rate,
  gain stepping down; **CAP SOFT** at +15 s (45%); crosswind correction from +30 s; **SOFT** at
  +75 s (15%, NAV only - APR "engage[s] ... the higher authority CAP SOFT"), which filters short needle
  excursions and falls back to CAP SOFT after >50% for 60 s; a new course >= 10 deg reverts to
  CAP; engaging <10% off and within 5 deg of the course goes straight to SOFT.
* HDG turns at 90% (sec.4.1); `Commands.turn_rate_dps` -> `SimModel.turn_rate_limit`
  (main resets it to the standard rate when the AP is off).

Headless: GPS NAV from 0.5/2/6 nm off, wind 0/270@30/120@35 -> overshoot 0.04-0.18 nm on a
1 nm CDI scale (<=18% of full scale), tracking within ~0.02 nm afterwards (no limit cycle: the
drift trim is limited to 3 deg and starts 30 s after capture); ILS 08 in 0/270@25/090@30 wind ->
needle within 2% by ~200 s. GPSS turn rate followed (item 2): `_GPSS_RATE_FRAC` = 110% of standard rate, the
Prog/Comp hardware mod code AR-and-above value (POH sec.3.1.3 / 4.1 list 130% AM and below,
90% AN/AP, 110% AR and above); GPS-steering headless runs overshoot <= 0.01 nm from 0.5-6 nm off
in 0/270@30/120@35 wind, arcs unchanged (PABR S26 RMS 0.11 nm in 30 kt). Gaps that remain:
sub-45 deg cuts at high closure rate, pilot-selectable intercept angle, NAV flashing. The
intercept turn-in model (11.5 s of closure + the 15% band) and the stage gains (50/30/22 deg per
unit) are the trainer's; the POH gives the bounds and the timeline, not the curves.

### F51 — NAV flies the HSI course pointer; GPSS flies the GPS; "Set course to ###"

Third step of the POH review (R11). NAV on a GPS leg used the leg's DTK directly - it behaved as
GPSS with a different label, and the HSI course card was slaved to the DTK. Per the S-TEC 55X POH
(4th Ed.) sec.3.1.2 NAV intercepts and tracks the course *set on the HSI* ("Set Course Pointer to
desired course"), whereas NAV GPSS (sec.3.1.3) "will not accept any course error input from the
Course Pointer". GNS 530 Pilot's Guide p.175: "Set course to [###] - The course select for the external
CDI (or HSI) should be set to the specified course. The message only occurs when the current selected
course is greater than 10 deg different from the desired track" (also p.80 for arcs).

* One HSI pointer, the NAV1 CRS card (unchanged key bindings: `O`/`Shift+O`, IFR-1 shift+knob).
  `World._gps_pointer()` feeds it to `Autopilot.update(gps_course_deg=...)` and to
  `compute_panel(gps_course_deg=...)`, so the CDI/HSI card shows the pilot's course.
* `Autopilot._lateral_command`: NAV/GPS = coupler(deviation = -xtk/scale, course = pointer);
  GPSS unchanged (DTK). A pointer left off parks the aircraft off the leg: headless 2 nm off,
  0 wind, 1 nm CDI scale - pointer on DTK 0.01 nm; +8 deg 0.37 nm; +20 deg 0.81 nm; -20 deg 0.20 nm;
  GPSS with +20 deg 0.00 nm.
* `GpsNav.check_course_select(pointer, magvar)`: condition-driven message, posted above 10 deg,
  updated if the required course changes, withdrawn when set right, off in OBS mode / VLOC / slaved.
* `--ap-course manual|auto` (config `ap_course`), **default manual** (faithful). `auto` keeps the pointer
  slaved to DTK for unattended scripted runs, which otherwise wander at each turn. FMS2 (`--dual`) is unchanged.
* Not modelled: the 530's reading of the external selector is assumed to be the NAV1 card at all times
  (the p.173 "Heading input failure" installation fault is not simulated).

### F52 — RDY / roll-mode interlock, NAV APR on GPS, LOC pointer, "Steep turn ahead"

Fourth step of the POH review (R16) plus the six "not faithful / unverified" items from the F51 follow-up.

**RDY and the roll-mode interlock (POH p.2-3, sec.3.1.4, 3.1.5, 4.2).** The POH has no roll button:
"roll mode" is the category HDG / NAV / NAV APR / REV / REV APR / NAV GPSS. The master brings up **RDY**
alone (no roll servo); HDG/NAV/APR/REV engage the roll axis; ALT and VS "can only be engaged if a roll mode
... is already engaged" (the S-TEC Cirrus transition deck agrees: "A roll mode must be selected prior to
engaging a pitch mode"). `Lat.LVL` (an invented wings-level base) is replaced by `Lat.RDY`;
`Autopilot.roll_engaged` gates `update()` and the World tick (RDY leaves the pilot / demo follower flying);
ALT/VS presses before a roll mode are ignored and do not switch the AP on; the RDY lamp is lit only in RDY.
*Trainer choice, unverified:* pressing the button of the mode that is already engaged releases it to RDY
(and drops the pitch mode); the POH does not say what a repeat press does. The GPSS "third press
deletes it" step is likewise not in this POH (sec.3.1.3 only says press NAV once if NAV is engaged).

**Follow-up on F51's list:**
1. *LOC pointer was auto-set.* `NavReceiver.course_deg` (the beam) fed both the needle and the AP.
   Now `NavReceiver.card_deg` is the pilot's OBS card when `--ap-course manual` and the AP/HSI use it, while
   a localizer's needle stays on the beam (a localizer does not move with the OBS card). S-TEC POH sec.3.3.3:
   "Set Course Pointer to FRONT INBOUND LOC course". Headless KLNS ILS 08: pointer on the course centres the
   needle; +10 deg leaves 0.28 deflection; +30 deg pins it; left at 0 deg the needle is pegged.
2. *How the AP senses the course.* POH sec.1.3: it "senses turn rate, as well as closure rate to the selected
   course, along with the non-rate quantities of heading error, course error, and course deviation
   indication"; the Cirrus 55X deck: in NAV "it will determine what guidance corrections are needed by
   reference to what is happening on the HSI". Consistent with pointer-as-course-datum + needle; the exact mixing
   is unpublished, so the coupler's track-based form stays the trainer's model.
3. *GPSS.* The same deck says GPSS also "will command a default 45 deg intercept angle" at full-scale CDI
   (so the shared 45 deg law is right) and that it "allows turn anticipation" (the HSI cannot know groundspeed or
   the course change). The 530 sends roll steering on ARINC 429 label 121 "Horizontal Command (to
   Autopilot)" (500 Series Installation Manual 190-00181-02 Rev J); bank-limited roll-steering dynamics are not
   modelled beyond the 110% rate limit. Implemented from Pilot's Guide p.175: **"Steep turn ahead"** ~60 s
   before a turn needing >25 deg of bank, a course change >175 deg, or (DME arc) an anticipation >90 s.
4. *GPSS with the CDI on VLOC.* POH sec.3.2.2: after switching to VLOC "press the NAV mode selector switch to
   engage the NAV APR mode" - APR replaces GPSS. `press_apr/rev/hdg` now clear GPSS, and NAV APR on a **GPS**
   source (sec.3.5.1 GPS approaches) flies the coupler on the GPS needle and the pointer (it previously
   held heading). Unverified: what a real installation does with the GPSS flag while the CDI is on VLOC (the
   trainer flies the VOR).
5. *Does the 530 read the pointer?* 500 Series Installation Manual: the manual course device "is required for
   the GNS 530 VOR receiver, and optional for the 500/530 GPS receiver"; "An OBS resolver connection to the GPS is
   preferred, but not required." So "Set course to" exists only where that input is wired; the trainer assumes
   it is. The p.173 "Heading input failure" fault is not simulated.
6. *Pointer on the HSI.* Rendered and checked: with the pointer 25 deg off DTK the NAV1 card shows the pointer,
   the needle is the GPS deviation, MSG lights with "Set course to 220"; the info label now reads **CRS**
   (it said DTK over a value that was the pointer).

Not modelled / open: REV on a GPS source; the ILS/GPS-approach output that raises autopilot gain
(Installation Manual 4.5.1.11); the CAP/SOFT gain numbers remain the trainer's.

### F53 — Glideslope auto-arm / 5% capture / APR re-arm; localizer needle vs course card

Fifth step of the POH review (A3-A6, R6), plus the check the user asked for on localizers.

**Localizer needle vs the course card (verified, no change needed).** A localizer's deflection comes from
the radio signal and does not move with the OBS/course card (Pilots of America "Localizer Approach, CDI & OBS":
"turning the OBS knob when a localizer is in-use does not affect the CDI displacement"; only a VOR needle follows
the card). The trainer already had this right after F52 (`radios.NavReceiver.course_deg` is the beam;
`card_deg` is only the card). New unit test `test_localizer_needle_ignores_the_course_card_but_a_vor_needle_
follows_it`. The autopilot is *not* indifferent to the card, though: the S-TEC POH sec.3.3.3 has the pilot "Set
Course Pointer to FRONT INBOUND LOC course" and sec.3.1.2 gives the pointer "sufficient authority to
complete the intercept"; a wrong card leaves the autopilot aiming at the wrong course while trying to null the
needle (autopilot forum reports for the KAP 140 describe the same offset; that is another autopilot, so
supporting only). F52's headless ILS runs show the same thing in the trainer.

**Glideslope (POH sec.3.2.1.1, software rev 5+, p.3-12).**
* APR no longer arms the GS by itself. `Autopilot._track_glideslope` arms it after 1 s of: NAV APR + ALT
  engaged, NAV flag and GS flag out, LOC frequency selected (CDI on VLOC, localizer), within 50% CDI of the
  centreline, and more than 10% GDI *below* the beam. It engages at 5% GDI below centreline and the ALT
  annunciation goes out.
* APR while armed disarms it (the GS annunciation flashes); APR again re-arms it (back after 1 s). Only
  when no GS is armed or disarmed does APR release the mode (trainer's choice; POH silent).
* ALT with APR + ALT engaged and a usable beam engages GS at once ("slightly above the GS centerline");
  ALT again leaves it. The >20% above caution is not enforced.
* Flashing (`Autopilot.flashing`, blinking lamps in the S-TEC panel): NAV/APR/REV at >50% deflection or no
  valid needle (p.3-5), GS at >50% GDI or a GS flag (p.3-13), NAV+GPSS with no course (p.3-7).
* The descent-rate law used the wrong nominal (5.0 fpm/kt) so the aircraft rode ~0.1 unit above the beam;
  now 5.31 (tan 3 deg). Headless KLNS ILS 08, 2500 ft: GS armed at ~55 s once within 50% of the localizer,
  engaged ~75-90 s at 5% GDI, then |GDI| <= 0.05 to 1 nm in 0/270@25/090@30 wind.

Not modelled: software rev 4 (10 s / 60% arming), GPS glideslopes on a GPS source (LPV/LNAV+V, POH sec.3.5.1),
the ">20% above" caution, the ILS/GPS-approach output that raises autopilot gain (Installation Manual 4.5.1.11).

### F54 — Modifier-knob ranges, VS capture and flashing, TRIM timing, AP disconnect

The "small items" of the POH review (P1-P3, P5, O2), all pitch-axis / annunciation details.

* **ALT knob** (POH sec.3.1.4, p.3-8): 20 ft per detent, +/-360 ft from the captured altitude (it was 100 ft
  with no limit). The IFR-1 AP-mode inner knob now goes through `Autopilot.turn_vs_knob`, so it is the
  "modifier knob" on both ALT and VS (it used `set_vs_target` with a +/-2000 fpm limit).
* **VS** (sec.3.1.5, 4.2): engaging VS holds the *present* vertical speed (captured, rounded to 100 fpm) instead of
  the last dialled window value; knob 100 fpm per detent, +/-1600 fpm from the captured rate, 1600 fpm absolute
  (was 2000). "During a climb, should the aircraft become unable to hold the captured vertical speed for a period
  of fifteen seconds, the VS annunciation will flash" - `Autopilot.flashing` gets "VS" after 15 s more than
  200 fpm short of a climb target (the POH gives no tolerance; that number is the trainer's).
* **TRIM UP/DN** (sec.3.1.7.1): appears after 3 s of servo loading and flashes 4 s later (was instant). The trainer
  has no servo, so a held rate above 200 fpm stands in for the loading; the periodic audible tone is not modelled.
* **Disconnect** (sec.3.7, pre-flight step 50): the IFR-1 has one AP key, standing in for the POH's separate
  controls - with a roll mode engaged it is the yoke AP DISC switch (every mode drops, RDY flashes for 5 s, then
  stays), from RDY it is the master going off, from off it switches the unit on (RDY). Trainer mapping, not POH text.

Still open in the review: R9 pilot-selectable intercept angle (needs a hold-HDG-then-NAV chord the input layer does
not carry), A8 GPS glideslopes, O1 CWS, O5 yaw damper, O3 power-up/pre-flight tests, R2 (intercept turn-in is a
model, not the POH's closure-rate curve), R8, R17 (DG heading systems).

### F55 — GNS 530W (WAAS): LPV / L/VNAV glidepath, level of service, angular approach scaling

The plain 530 in this trainer is the non-WAAS unit (Pilot's Guide 190-00181-00; FINDINGS F-early: enroute CDI 5.0 nm),
which has no vertical guidance from the GPS. LPV needs a WAAS receiver, so it is a **new opt-in unit, `--unit 530w`**
(`gpsnav.VARIANT_530W`, `gns530.Gns530W`), leaving the validated 530 untouched. References saved beside the others:
`docs/reference/GNS500W_Pilots_Guide.pdf` (500W Series Pilot's Guide 190-00357-00 Rev K, SHA-256
`5cb5851c720d8cb6a6d98ec189632efaad21c3c92c538e40530ecaa728720253`, from `wayman.edu/wp-content/uploads/2016/06/GNS530-Pilot-Guide.pdf`).

* **Data.** The FAA CIFP carries the SBAS path point of every RNAV approach: `navdata.cifp._path_point_from_line`
  reads section P.P (LTP, GPA, TCH, FPAP, course width) and the "W" continuation on the FAF leg gives the levels of
  service (LPV / LNAV/VNAV / LNAV / LP). Offsets were verified offline against X-Plane's own type-14/16 rows for 4711
  approaches (LTP position and TCH agree for ~99%, the rest are 2406-vs-2609 amendments); `Runway.elev_ft` (P.G) supplies
  the threshold elevation. Cache schema 4.
* **Vertical guidance.** `GpsNav.glidepath(alt_ft)` -> `GlidePath(valid, vdev, service, gpa, error)`: a glidepath of the
  GPA through TCH above the LTP, valid from 2 nm before the FAF until the MAP, ILS convention (+ = fly up), rides
  `panel.cdi.vdev` and drives the NAV head's glideslope needle (`gps_nav_head`). Assumption: the needle scale is the
  ILS-equivalent +/-0.7 deg - the 500W guide says LPV "can be flown identically to a standard ILS" but gives no vertical
  scale.
* **Level of service annunciation** (guide p.85): ENR / TERM, then LPV / L/VNAV / LP / LNAV, on `NavState.service` and the
  CDI strip.
* **CDI scaling** (guide p.85, sec.5, Appendix C): enroute 2.0 nm (the 530: 5.0), terminal 1.0; inside 2 nm of the FAF
  0.3 nm or the angular path-point scale (the course width widening with distance from the FPAP, like a localizer -
  ~0.3 nm at the FAF), tightening to 350 ft at the threshold/MAP; non-LPV approaches tighten 0.3 nm -> 350 ft
  along the final segment.
* **Autopilot** (S-TEC POH sec.3.5.1): with the CDI on GPS, `World.tick` feeds NAV APR the GPS glidepath in place of the
  radio glideslope; the same arm-at-1 s / capture-at-5% logic as an ILS runs on the GPS needle and the GPS lateral
  deviation (within 50% of its scale). Headless KFDK RNAV (GPS) RWY 23 Z, `--unit 530w`, 0 and 270@25 wind: TERM ->
  LPV at 2 nm before SHUEY, scale 1.0 -> 0.30 -> 0.13, GS arms below the path, captures at 5%, |GDI| <= 0.05 to 1.5 nm.

Not modelled here (LNAV+V / LP+V: see F56): the HAL/VAL
integrity downgrade ("Approach downgraded - Use LNAV minima"), the KAP 140 "Enable A/P APR Outputs?" prompt, the 530W's
other differences (SBAS status pages, terrain, etc.), and the LOW ALT annunciation.

### F56 — LNAV+V and LP+V advisory glidepaths on the 530W

Follow-up to F55. On a `--unit 530w`, an RNAV (GPS) approach that publishes no SBAS path point but does publish a
final-segment descent angle gets an **advisory** glidepath, annunciated **LNAV+V** (500W Pilot's Guide p.85: "GPS approach
using published LNAV minima. Advisory vertical guidance is provided") - or **LP+V** when the approach publishes LP minima
(p.85/p.117, SW 5.10+). The guide (p.117): the glidepath "is provided to assist the pilot in maintaining a constant vertical
glidepath, similar to an ILS glideslope"; the pilot is still responsible for the step-down altitudes and the MDA
(p.115).

* **Data.** `ProcedureLeg.vertical_angle_deg` parses ARINC 5.70 at [102:106] (`-341` = 3.41 deg down; present on ~10,200
  approaches; 3.00 is by far the commonest, then 3.01/3.04/3.50). `GpsNav._advisory_angle` takes the runway leg's angle of
  an approach whose route type is `R` (RNAV); ILS / VOR overlays stay plain LNAV.
* **Which label.** `GpsNav._vertical_service`: LPV (SBAS path + LPV) > L/VNAV (SBAS path + LNAV/VNAV) > LP+V / LNAV+V
  (published angle) > LP / LNAV. The advisory path crosses the threshold at 50 ft - **not published**, the trainer's
  assumption - along the FAF->threshold course, with the same +/-0.7 deg needle assumption as F55.
* **Autopilot.** POH sec.3.5.1 lists LNAV+V with LPV/L-VNAV as vertical approaches the autopilot executes, so NAV APR couples
  it like the others (and "the aircraft will not automatically level off at the DH or MDA"). New `gs_angle_deg`
  feeds the descent-rate feed-forward the path angle (3.00 deg = 5.31 fpm/kt, scaled by tan(angle)): KFDK RNAV
  (GPS) RWY 23 Y (3.41 deg), headless, was drifting to 0.25 GDI high at 1.5 nm and now holds |GDI| <= 0.05 in 0 and 270@25
  wind.

Not modelled here (LP+V removal, "Approach downgraded", MAPR/TERM: see F58).

### F57 — Closing out the modelled autopilot items (R2, R8, FAIL) and what stays the trainer's own

* **R8 (built from the POH).** POH p.3-5 (and the Cirrus 2nd Ed. POH, sec.3.1.2): APR pressed "while tracking in the SOFT
  condition and within 50% CDI needle deflection" raises authority to the higher CAP SOFT condition - it does not restart
  the intercept. `press_apr` now carries the captured course over (SOFT -> CAP SOFT) when NAV was past capture and
  within 50%; otherwise APR starts a fresh intercept as before.
* **FAIL (built from the POH).** "The NAV annunciation will flash whenever CDI needle deflection exceeds 50%, or the NAV
  Flag is in view. In the latter event, the FAIL annunciation will also appear" (p.3-5); NAV GPSS with no course: FAIL, NAV
  and GPSS flash, wings level (p.3-7). `Autopilot.fail` + a red FAIL in the S-TEC panel.
* **R2 stays a model.** The manuals give bounds and a sequence, not curves: the turn begins between 100% and 20% of full
  scale (enforced), earlier at higher closure rate (measured in needle units, so distance from the station counts), and
  the cut is "gradually" shallowed onto the course (Cirrus 2nd Ed.). Inside those bounds the turn-in point (11.5 s of closure
  plus the 15% band), the 50 / 30 / 22 deg-per-unit stage gains, the 15 s SOFT needle filter and the 3 deg drift-trim
  limit are the trainer's numbers, tuned against the behaviour the documents do specify (no overshoot beyond ~18% of full
  scale, settling within a few % in SOFT, ILS within 2% by ~200 s). Nothing published lets them be pinned down further.
* Also unresolved, undocumented in all three POHs read: what a repeat press of an engaged mode's button does (HDG, APR,
  REV), and a third NAV press after GPSS - both stay trainer choices.

### F58 — 530W: SBAS integrity (downgrade / abort / silent LP+V removal) and MAPR / TERM

The three "not modelled" items from F55/F56 (500W Pilot's Guide 190-00357-00 Rev K).

* **Missed approach: MAPR / TERM** (p.100 c/d). Once the OBS key sequences past the MAP the approach annunciation is
  replaced by MAPR (CDI 0.3 nm) or TERM (CDI 1.0 nm): "MAPR ... for missed approach procedures in which the first leg is a
  climb straight ahead to a waypoint, whereas TERM ... for missed approach procedures requiring a turn". In the FAA
  CIFP virtually every missed approach opens with a CA climb along the final course (768 of 769 sampled), so the first *leg*
  cannot tell the two apart; the trainer decides by the turn onto the first **waypoint** the climb heads for - a bearing
  from the MAP more than 30 deg off the final course = TERM. The 30 deg is the trainer's threshold (the guide gives none);
  on a 600-approach sample 55% come out MAPR and 45% TERM. Before the OBS press (SUSP at the MAP) the approach
  annunciation stays.
* **SBAS condition** (a training control - the trainer has no GPS integrity data, and the guide describes what the unit
  does, not when): `GpsNav.sbas` in `OK / ADV LOST / DEGRADED / LOSS`, cycled with **`Y`** on a 530W (the top bar shows
  "SBAS ..." in amber; it is not something the unit displays).
  * **DEGRADED** - WAAS integrity below the LPV / L/VNAV / LNAV+V / LP limits: 60 s before the FAF the unit posts
    **"Approach downgraded - Use LNAV minima"**, the annunciation becomes LNAV, the glidepath is flagged and the CDI keeps
    the 0.3 nm approach scale (p.114; a plain LNAV approach is unaffected). Once past the FAF the same loss **aborts**
    instead ("After the aircraft has passed the FAF, a loss of WAAS integrity will cause the approach to abort").
  * **LOSS** - below even the non-precision limits: **"Abort Approach - Loss of Navigation"** at any time; the unit reverts to
    terminal limits (TERM, CDI 1.0 nm).
  * **ADV LOST** - LP+V only (p.115): "the advisory vertical guidance could be removed without annunciation due to the vertical
    guidance not being within tolerances. This does not constitute a downgrade" - the approach stays annunciated LP+V, the
    glidepath goes away, no message.
  Approaches loaded again reset the downgrade / abort flags.

Not modelled: the LPV annunciation's yellow background ("safe to continue but a downgrade may occur"), RAIM prediction, the
message acknowledgement step before the unit "will revert to terminal limits", LOW ALT.

### F59 - Tuning frequencies from the GNS pages (NRST / WPT / NAV/COM)

GNS 530 Pilot's Guide (190-00181-00) sec.1 "Auto-Tuning" p.23-25, sec.6 p.94-95 / p.104-105, sec.7 p.116-118: "highlight
the desired frequency on any of the main pages and press ENT" puts it in the **standby** field of the COM or VLOC window
(COM frequencies to COM, navigation frequencies to VLOC); the pilot then presses the flip-flop key.

* **Nearest Airport**: the row shows the tower / CTAF frequency; the large knob steps identifier -> frequency -> next row,
  ENT on the frequency -> COM standby (p.116). **Nearest VOR**: same, ENT -> VLOC standby (p.118).
* **WPT > Airport Freq** (new page, third in the guide's WPT group): the airport's ATIS (marked RX, receive only), clearance,
  ground, tower, CTAF, unicom, approach, departure, then each runway's ILS/LOC (p.94-95). Large knob off the end of the
  identifier reaches the list; ENT tunes COM or VLOC by the row's type.
* **WPT > VOR**: the frequency field is highlighted with the large knob; ENT -> VLOC standby (p.105).
* **NAV > NAV/COM** (the page was only a name before): frequencies for the flight-plan airports, small knob picks the airport
  (Departure / Enroute / Arrival), large knob the frequency, ENT -> standby (p.24).
* `GpsNav.tune_requests` -> `main.World._apply_tunes`: FMS1 fills COM1/NAV1, FMS2 COM2/NAV2 (out-of-band values ignored).

Not modelled (guide shows them; the data or the units aren't there): (Nearest Airport columns: F61; the Airport Runway page: F60), "Info?" usage restrictions on frequencies, TX/PT designations, Nearest
User / ARTCC / FSS / Airspace pages, the 30 s tuning-cursor return timer, 8.33 kHz spacing. NDB frequencies are shown but
not tunable (no ADF). **ENT on a highlighted Nearest identifier** now opens that facility's WPT page
(Airport / VOR / NDB / Intersection; p.117) and CLR returns to the Nearest page; the **D-> key** with a Nearest row
highlighted seeds the Direct-To page, then ENT, ENT (p.115). The old ENT-to-Direct-To shortcut (Nearest and WPT pages) is
gone. Not modelled: the "Done?" field (CLR only), and extra Airport Location fields (fuel, city).

### F60 - WPT > Airport Runway page

Pilot's Guide sec.6 p.93: the page shows runway designations, length and width, surface and lighting for the selected
airport; the cursor goes to the "Runway" field, the small knob lists the runways, ENT displays one.

* New page second in the WPT group (guide order: Location, Runway, Frequency). Large knob off the identifier reaches the
  Runway field, the small knob steps through the runway ends (by designation), the large knob back returns to the identifier.
  Shows designation, length x width, magnetic heading, threshold elevation, surface, lighting and the ILS/LOC frequency.
* **New data**: NASR `APT_RWY.csv` is now kept by `datasrc.faa` (re-run `python -m datasrc.faa update --kinds nasr --force`
  on an existing cache; without it the page shows Unknown) and merged by `navdata.nasr.merge_runways`; nav-DB cache schema 5.
  NASR surface codes are mapped to the guide's words (Hard / Turf / Sealed / Gravel / Dirt / Water, else Unknown; a composite
  code like ASPH-TURF takes its first part).
* **Gap**: NASR gives lighting *intensity* (High / Medium / Low / Perimeter ...), not the guide's schedule (No Lights / Part
  Time / Full Time / Frequency), so the page shows the intensity and Unknown where NASR is blank. Not modelled: the runway
  map image and RNG scaling, and the Public/Military/Private type. Default runway shown first is the lowest designation
  (the guide does not say).

### F61 - Nearest Airport columns

Pilot's Guide sec.7 p.116: for each nearest airport the page shows identifier, bearing, distance, **best available
approach**, tower / CTAF frequency and the **longest runway**, "detailed information for four nearest airports".

* Two lines per airport: identifier, bearing, distance, approach; then the frequency (large knob, ENT -> COM standby, F59)
  and longest runway (NASR/CIFP `longest_runway_ft`).
* Best approach uses the guide's list (p.91) best-first - ILS, LOC, LDA, SDF, GPS, VOR, RNAV, NDB, TACAN - from the CIFP
  approach identifiers, "VFR" when there is none. A CIFP "R" (RNAV (GPS)) counts as GPS; that mapping is the trainer's (the
  guide's list predates RNAV (GPS) naming). MLS / LORAN / helicopter are not in the data.
* The trainer screen has room for three airports at a time, not four (the list scrolls).
* Not modelled: the AUX > Setup "Nearest Airport Criteria" (minimum runway length / surface filter, p.152).

### F62 - Nearest ARTCC / FSS / Airspace / User pages, and Class B/C/D on the map

The last four NRST pages (Pilot's Guide p.113, "eight pages ... under the NRST group") and airspace on the moving
map. Page order now matches the guide exactly: Airport, Intersection, NDB, VOR, User Waypoint, ARTCC, FSS, Airspace
(p.113) - the first four were reordered from the trainer's original APT/VOR/NDB/INT.

**New public data, none of it in the CSV NASR product this trainer already used:**

* **ARTCC "points of communication"** (p.119: "the five nearest ... points of communication", not one frequency per
  ARTCC). The 28-Day NASR *CSV* subscription has no per-sector ARTCC frequency data at all - `FRQ.csv`'s own ARTCC
  rows carry only the emergency/backup frequencies (121.5, 243.0). The real per-site data is in `AFF.txt`, a legacy
  fixed-width text file at the root of the *full* 28-Day NASR zip (undocumented field widths, so `datasrc/aff.py`
  parses it by anchor - a `RCAG`/`ARTCC` + date marker splits each line, then DMS lat/lon is regexed out of the
  tail) - each RCAG (remote air/ground) ground station, its controlling ARTCC, and its frequencies (UHF military
  frequencies are dropped; the trainer's COM window only tunes 118.000-136.990). Cached as `artcc.json`
  (`db.centers`, `db.nearest_centers`).
* **FSS "points of communication"** (p.119, same wording). `FSS_BASE.csv`'s own facility positions turned out to be
  mostly decommissioned local stations (18 nationwide, nearly all Alaska) - in the contiguous US, Flight Service was
  consolidated years ago to a handful of hubs (e.g. Leesburg) reached through many **RCO** (remote comm outlet)
  ground sites, which is what a real Nearest FSS query needs. `navdata/nasr.py`'s `load_fss` now reads `FRQ.csv`
  rows with `FACILITY_TYPE` "RCO" (position + frequency, grouped by controlling FSS + site name) and
  `FSS_BASE.csv`'s `VOICE_CALL` for the on-air callsign ("Leesburg Radio"). `FSS_BASE.csv` is newly kept from NASR
  (re-run `python -m datasrc.faa update --kinds nasr --force` on an existing cache).
* **Class B/C/D airspace** (p.121, map legend p.11/36). Not in the CSV NASR product either (`CLS_ARSP.csv` is a
  per-airport Y/N flag with no shape). The FAA separately publishes a **Class Airspace shapefile**
  (`class_airspace_shape_files.zip`, ~150 MB) each cycle. `datasrc/shp.py` is a small stdlib-only .shp/.dbf reader
  (no pyshp/shapely dependency) plus an iterative Douglas-Peucker simplifier: the raw boundaries run to ~3,200
  points/ring on average (surveyed to a few metres) - at a trainer map's 5-40 nm range that's pointless precision,
  so each ring is simplified to ~0.0008 deg (~90 m) tolerance, cutting the Class B/C/D total from ~4.15M points to
  ~42,500 with no visible change at map scale. Class E is dropped (nearly ubiquitous - a surface extension at
  almost every instrument airport - and would swamp both the list and the map). Cached as `airspace.json`
  (`db.airspaces`, `db.nearest_airspaces` - by distance to the nearest **boundary**, `0.0` when inside, not
  centroid distance).

Both are large/one-time enough that they are **opt-in**, not part of the default `cifp,nasr` fetch:
`python -m datasrc.faa update --kinds cifp,nasr,artcc,airspace`. Without them the pages are correctly empty (a
`db.notes` line says so, same convention as a missing NASR comms cache) rather than the trainer pretending they
don't exist.

**Nearest ARTCC / Nearest FSS.** "The Nearest ARTCC and Nearest FSS Pages present detailed information for up to
five nearby facilities - displaying only one facility at a time" (p.114) - the small knob steps the facility, the
large knob its frequency list, ENT tunes COM standby (p.119) - the opposite knob assignment from every other NRST
page, per the guide's own steps ("rotate the small right knob to select the desired center, then rotate the large
right knob to highlight the desired frequency"). Direct-To is **not** offered on these two pages or Airspace - the
guide's Direct-To list (p.115) is airport/VOR/NDB/intersection/user waypoint only.

**Nearest User Waypoint.** Always empty - the trainer has no user-waypoint store (creation is a separate feature,
deferred by design decision). This is correct behaviour for a unit that has never had one entered, not a stub.

**Nearest Airspace - what's built and what's not.** Built: the list (name, class, floor/ceiling, distance), sorted
by boundary distance; all four alert status words in the guide's exact wording ("Ahead", "Ahead < 2nm", "Within
2nm of airspace", "Inside of airspace") and the matching MSG-queue alert messages - see F63. Class B/C/D outlines
on the moving map, sectional-ish colors (B blue, C magenta, D dashed blue), own-position bounding-box prefiltered
so a nationwide list costs nothing off-screen (~10 ms/frame with the overlay on, well inside a 33 ms/30 Hz
budget). Controlling agency + primary frequency, tunable by ENT: F64. **Not built** (p.121-123): the separate
drill-down "Airspace Information Page" and its "View Frequencies?" sectorized list (F64 shows/tunes only the
primary frequency directly on the list row); the "Done?" field; the yellow/background alert coloring on the map;
the Setup page's alert-messages-enabled toggle (F63). The large-knob column-toggle other NRST pages use is inert
here (verified not to crash or tune anything) - ENT alone does the tuning.

**Trainer choices, undocumented in the guide:** the 30 s round-trip through `AFF.txt`'s anchor-based parsing
instead of fixed columns (the FAA no longer publishes the column layout for this legacy file); showing "ZDC
CENTER"/"<callsign> RADIO" as the facility name in place of the ARTCC's full name (not in this data, only the
3-letter id); the 0.0008 deg simplification tolerance; Douglas-Peucker over a simpler decimation (kept concave
detail - a Class B "wedding cake" shelf stays recognizable - unlike, say, every-Nth-point).

### F63 - Airspace alert messages (Pilot's Guide p.121-122)

The four conditions F62 left as "not built": course-projected proximity alerts to a nearby Class B/C/D area, tied
into the existing MSG-annunciator queue (the same `self.messages`/`peek_messages`/`ack_messages` plumbing as every
other trainer message - F58's SBAS messages, "TUNE VLOC", etc.). Quoting the guide exactly:

* *"If your projected course will take you inside an airspace within the next ten minutes"* -> **"Airspace ahead -
  less than 10 minutes"** (Nearest Airspace Page: "Ahead").
* *"If you are within two nautical miles of an airspace and your current course will take you inside"* ->
  **"Airspace near and ahead"** ("Ahead < 2nm").
* *"If you are within two nautical miles ... and your current course will not take you inside"* -> **"Near
  airspace less than 2nm"** ("Within 2nm of airspace").
* *"If you have entered an airspace"* -> **"Inside Airspace"** ("Inside of airspace").

`Airspace.time_to_entry_s` projects a straight line at the current true track/groundspeed, sampled every 15 s out
to 10 minutes, and reports the first time it lands inside the polygon (`None` if it never does within the window;
also `None` below 30 kt, where a straight-line projection is meaningless - taxiing, a stationary test). Below 2 nm
of the boundary, whether that projection ever lands inside picks "near_ahead" vs. "near"; at or past 10 minutes it
picks "ahead" vs. nothing. `Airspace.alert_category` is the single source both the message check and the Nearest
Airspace page's status column read, so the two can't drift apart - the *wording* differs (the guide itself uses
different phrasing for the message than for the page), the *category* doesn't.

`GpsNav._check_airspace_alerts` runs every `update()` (all variants - this is base-500-series behaviour, not
WAAS-gated) and considers the 6 nearest airspaces within a speed-scaled search radius (`gs_kt/60*10 + 5` nm, so it
never misses a real 10-minute projection at higher speeds). It posts a message only on a **change** of condition -
"once one of the described conditions exists" (p.122) - keyed on (airspace, category), not every tick, so holding
inside/near one area doesn't spam the queue; flying clear and re-approaching re-alerts, as a real transition would.

**Not modelled**: the Setup page's "airspace alert messages enabled" toggle (p.147, referenced in this same
paragraph) - alerts are always on here; the "less than 10 minutes" / "less than 2 nm" thresholds are the guide's
own numbers, not tunable. Considering only the 6 *nearest-by-boundary-distance* airspaces is a trainer
simplification - guide behaviour is presumably exhaustive within range; in the crowded DC Class B "wedding cake"
this can occasionally let a closer-but-behind area crowd out a farther one that's actually dead ahead, which
hasn't come up in testing but is a known edge case worth flagging.

### F64 - Airspace controlling agency + frequency (Pilot's Guide p.123)

The other gap F62 left open: "additional details are provided - including controlling agency, communication
frequencies and floor/ceiling limits" for a Nearest Airspace Page entry. This data is already in `FRQ.csv` - the
same NASR file the trainer already fetches by default (no new datasrc kind, unlike F62's `artcc`/`airspace`):

* **Class B / Class C**: FRQ.csv rows literally tagged `FREQ_USE` = `"CLASS B"` / `"CLASS C"`, keyed by
  `SERVICED_FACILITY` (the same FAA id `Airspace.ident` uses) - the TRACON's own name (`FAC_NAME`, e.g. "POTOMAC
  TRACON") and its sectorized frequencies (e.g. Washington's Class B: 119.850 west/south, 124.200 east). These
  rows are otherwise invisible to the trainer - they don't match any of `merge_comms`'s existing `_USE_MAP`
  substrings ("APCH"/"DEP"/...), so a Class B/C airport's approach frequency was never being captured at all
  before this.
* **Class D**: FRQ.csv has no "CLASS D" tag anywhere (0 rows checked) - a Class D's controlling agency simply *is*
  the airport's own tower, already merged onto `Airport.comms["TWR"]` by `merge_comms` (F1). `merge_airspace_controlling`
  runs after `merge_comms` for exactly this reason and reads that back out, labelled "<airport name> TOWER".

`NavDatabase.airspace_controlling: dict[(FAA id, class), (facility name, frequencies)]`, `controlling_agency(aw)`.
`GpsNav.airspace_controlling`/`airspace_controlling_entry` (a `FreqEntry` for the *primary* - first/lowest -
frequency only). The Nearest Airspace Page now shows a third line per entry (controlling agency name + primary
frequency, "+N" if there are more) and **ENT tunes it to COM standby** - real Washington data: `DCA CLASS B
Inside of airspace / 1500ft - 10000ft / POTOMAC TRACON 119.850 +1`.

**Superseded by F65**: the first pass tuned the primary frequency directly from the Nearest Airspace list row.
The guide's actual workflow is a separate drill-down "Airspace Information Page" with a "View Frequencies?" field
that scrolls every sectorized frequency (p.123) - F65 replaces the direct-tune shortcut with that, so every
frequency (e.g. Potomac's 124.200 EAST, not just 119.850) is reachable.

### F65 - The Airspace Information / Frequency Pages (Pilot's Guide p.122-123)

Replaces F64's "tune the primary frequency directly" shortcut with the guide's actual three-level flow, quoted
exactly:

1. **Nearest Airspace Page** (built in F62/F63): "Rotate the large right knob to scroll through the list,
   highlighting the desired airspace. Press ENT to display the Airspace Information Page."
2. **Airspace Information Page**: airspace name, status + time-to-entry (F63's four conditions), floor/ceiling
   limits, and two fields scrolled together by the large knob - **"View Frequencies?"** and **"Done?"**. ENT on
   "View Frequencies?" opens the Frequency Page; ENT on "Done?" (or CLR) returns to the Nearest Airspace Page.
3. **Frequency Page**: "Rotate the large right knob to scroll through the list, highlighting the desired
   frequency. Press ENT to place the selected frequency in the standby field of the COM window" - then the COM
   flip-flop key activates it, same as every other auto-tune page (F59). Its own "Done?" row (scrolled to like any
   frequency, p.123 doesn't treat it as separate) or CLR returns to the Airspace Information Page.

Implemented as a modal overlay (`GpsNav.AirspaceInfo`/`_airspace_info_event`), the same pattern as the Direct-To
dialog, the PROC selector and the MNU pop-up - `handle_event` gives it every input while open, matching how those
other multi-field GNS "pages within a page" already work. The airspace is held **by reference**, not re-looked-up
by ident+class, because Class B areas publish several overlapping shelf records that share both (Washington's
Class B is three separate records at KFDK's range) - re-deriving "the" airspace from ident+class alone could land
on the wrong shelf's floor/ceiling.

Verified against real data: KIAD-area Class B opens to "DCA CLASS B / Inside of airspace / 1500ft - 10000ft /
View Frequencies? / Done?", then "POTOMAC TRACON / 119.850 / 124.200 / Done?" - both of Potomac's published
sectors are now reachable and tunable, not just the primary.

Not modelled: the guide's separate physical "Done?" *field marker* vs. this trainer's list-row "Done?" (visually
identical, functionally identical - CLR is offered everywhere the guide offers it as an alternative). The status
line inside the Airspace Information Page recomputes live from current position/track/groundspeed rather than
freezing at the moment ENT opened it - the guide doesn't say either way, and a live status seems more useful in a
trainer than a stale one.

### F66 - Directory-services audit (F59-F65 vs the Pilot's Guide)

A full page-by-page re-check of every auto-tune/NRST/WPT directory page's button sequences and displayed data
against the guide text, prompted by a request to validate consistency. No behavioural bugs found - every knob/ENT/
CLR/DCT sequence re-verified matches its cited page, including a word-for-word re-check of the ARTCC/FSS small
knob = facility / large knob = frequency split (p.119 caption, quoted in F62/F65).

Two genuinely new, previously-undocumented **data** gaps turned up on the Nearest FSS Page (p.120):

* *"'RX' and 'TX' indications appear beside the listed frequencies -- indicating 'receive only' or 'transmit
  only' frequencies."* `FRQ.csv`'s RCO rows (F62's data source for this page) carry no such flag - checked the
  raw columns directly, confirmed absent. Every frequency this trainer shows is presented as full-duplex; a real
  site may not be.
* *"The associated VOR is also provided for reference"* - for **duplex** FSS operation, tune COM to the RCO
  frequency and VLOC to the paired VOR to transmit/receive; ENT on that list entry tunes **VLOC**, not COM (p.120:
  "Press ENT to place the selected frequency in the standby field of the **COM or VLOC** window"). `FRQ.csv`'s RCO
  rows carry no associated-navaid reference either, so this trainer's Nearest FSS frequency list is COM-only, and
  correctly so given the data - there's nothing to mistakenly mistag, but the feature itself can't be built without
  a data source that has it.

Neither is fixable from data this trainer already fetches; noted here rather than acted on.

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
