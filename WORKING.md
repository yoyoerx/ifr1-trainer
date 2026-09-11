# WORKING.md

Task tracker for **octavi-ifr-trainer**. Newest status at the top of each list.
`[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped.

Last updated: 2026-09-11

---

## Current status  (2026-09-11, 789 tests green)

**M0–M16 all done.** The trainer runs end to end:

* **`gpsnav.py`** — variant-independent GNS 530/430 avionics core: flight plan
  (editable in flight on the Flight Plan page), Direct-To (frozen course +
  Select Direct-To identifier-entry page), automatic sequencing with turn
  anticipation, SUSP at the MAP / manual legs. **A holding pattern is actually
  flown** (`_start_hold`/`_step_hold`/`_complete_hold_lap`): the AIM 5-3-9 entry
  (direct/teardrop/parallel, via `navmath.hold_entry`) then one lap on synthetic
  outbound/inbound legs — a single-circuit HILPT (ARINC `HF`) auto-continues
  once re-established inbound, `HM`/`HA` repeat until SUSP is released. OBS, CDI
  source GPS↔VLOC, phase-of-flight CDI full-scale, `NEXT DTK`/`TURN TO`
  advisory, nav-data expiry + message queue. **`load_procedure` synthesises
  fixes for every non-fix ARINC leg** (CA/VA/FA/CD/VD/FD/FC/CR/VR/CI/VI/PI/
  HM/HA/FM/VM + DME/RF arcs), so a full published approach loads. WPT-page
  identifier lookup and NRST-page nearest lists both drive a Direct-To. The
  **PROC key** opens an on-screen selector (`ProcSelect`): menu → procedure list
  → transition list (incl. VECTORS) → `load_procedure`, plus Activate Approach /
  Vectors-To-Final once one is loaded. The **MNU key** opens a page-context
  menu (`FplMenu`) on the Flight Plan / Flight Plan Catalog pages — Invert
  Flight Plan, Copy Flight Plan, Sort Catalog, Delete Flight Plan — and the
  **Flight Plan Catalog** page stores up to 19 plans (FPL 01-19) alongside the
  active one (FPL 00), each carrying an auto-generated (or `catalog_set_comment`
  -renamed) ORIGIN/DEST comment line; recalled with ENT, deleted with CLR.
  `crossfill(other)` copies the active plan to another `GpsNav` instance (no
  live second FMS unit is wired into `main.World` yet - see "what's left" #3).
  The **VNAV page** (`vnav_set`/`vnav_status`, ALT bezel key) programs a target
  fix/altitude/descent-angle from the remaining flight plan and reports
  distance-to-target, top-of-descent distance, and vertical deviation once
  armed - computed on demand from ownship altitude rather than folded into
  `update()`/`NavState`, so every existing caller is untouched.
  `gns530.py` / `gns430.py` are thin `Variant` views; `--unit 430` runs the
  shorter unit. **Validated against the Pilot's Guide 190-00181-00 Rev. H —
  see `FINDINGS.md`.**
* **Nav data** — FAA CIFP + NASR (public domain, AIRAC-current) via `datasrc/`
  cache + `navdata/`, with a **cycle+file-keyed pickle cache** (`load()`
  ~2 s → ~0.7 s). **The only source** — the X-Plane `earth_*.dat` offline
  fallback was removed 2026-09-11 at the user's request (no coupling to a local
  X-Plane install's nav data, even as a last resort); `navdata.load()` now
  has one code path and raises `FileNotFoundError` with the fix-it command
  if nothing is cached. Validity window shown + annunciated.
* **`instruments.py` / `sim_model.py`** — CDI/HSI/bearing/DME/marker math + a
  kinematic ownship (commanded or leg-intercept, wind, coordinated turns).
* **`radios.py` / `autopilot.py`** — COM/NAV/XPDR stack (localizer↔runway
  pairing, IDENT, emergency 121.5) and an S-TEC Fifty Five X style AP. A
  localizer's published course is **magnetic**, converted with the airport's
  own station declination (`Airport.magvar_deg`), not a smooth WMM model or a
  hardcoded zero — the earlier zero-magvar shortcut for the CIFP section P·I
  path put the CDI persistently off-centre (a real, felt "flying right of the
  magenta line"). **VLOC Morse ident** (`morse_pattern`/`morse_is_keyed`) is a
  pure dot-dash timeline the render layer samples each frame to blink an ident
  indicator beside the station ident, letting the pilot positively ID a
  tuned VOR/localizer the way the audible tone does on the real unit.
* **`render.py`** — `--layout gps` (530/430 faceplate SVG + track-up moving
  map; Default NAV / Flight Plan / Flight Plan Catalog / **VNAV** / **in-screen
  Map** / **WPT / NRST** / **AUX (Trip Planning, Utility, Setup, Nav Data,
  Weather, Charts)** page bodies; MAP/HOLD/FAF tags + map symbols) and `--layout steam` (large
  six-pack + stacked NAV heads at matched diameter + full-width radio strip +
  S-TEC programmer, both with a blinking VLOC ident dot). NAV heads default
  to an old-school round VOR needle; `H` toggles NAV1 to an HSI face.
* **`scoring.py`** — grades the flying (lateral/CDI/glideslope RMS + max,
  needle-peg count, TKE, altitude-vs-selected); `main` prints the summary +
  a letter grade on exit.
* **`main.py`** — ~30 Hz loop; IFR-1 mode selector routing (**FMS AP-row →
  bezel keys**, knob-latched shift per mode with an on-screen hint, AP-mode
  ALT/VS knobs + **shift → IAS set-point** (pseudo speed manager, no throttle
  model — feeds sim TAS/GS via ISA density), XPDR digit cursor). Confirmed on
  real hardware. Activating a staged VLOC frequency (`World._auto_vloc`) is
  only ever automatic when *nobody* is at the controls (GPS-follow with the
  autopilot off) — with the autopilot flying an ILS via APR/GS (the normal
  way), the pilot must press SWAP themselves, same as the real unit, so a
  one-shot `TUNE VLOC ...` message reminds them near the FAF instead of the
  approach silently never capturing. **`World.t`** (elapsed session seconds)
  drives the VLOC ident blink and the AUX/Utility flight timer. **Time warp**
  (keys `1`/`2`/`3`/`4` -> 1x/5x/10x/20x, `--time-warp` for the starting
  speed) runs N nav/physics ticks per rendered frame, each with the same
  real-time `dt` as 1x, so capture bands and sequencing stay exactly as
  fine-grained - only the picture updates less often per second of flight;
  an amber `WARP nx` annunciator shows whenever it's active. Keyboard also
  gained `Shift+PgUp`/`Shift+PgDn` for the large-knob page GROUP change
  (NAV/WPT/AUX/NRST) - previously the only way to reach WPT, AUX (incl. the
  Weather page), or NRST at all was the IFR-1's physical outer knob.
* **`ifr1.py`** — **long-press events**: a button held past `LONG_PRESS_S`
  (0.6 s) fires a synthetic `Event.long_press` even with no new device report
  (the IFR-1 only pushes on a state change, so `poll()` times the hold off the
  wall clock instead). `route_event` wires `CLR`-hold → Default NAV (FMS mode)
  and `SWAP`-hold → the COM 121.500 emergency channel — the real long-press
  behaviour behind F8/F9, with the keyboard `Home`/`F11` kept as a no-hardware
  fallback.
* **`xplane_feed.py`** (`--xplane-feed`, seamless fallback), **`gdl90_out.py`**
  (`--gdl90` tablet EFB), **`foreflight_discovery.py`** (`--gdl90-discover`
  listens for ForeFlight's own UDP broadcast on :63093 and retargets the
  GDL90 stream to a direct unicast once a tablet answers, falling back to
  broadcast if it goes stale), **`config.py`** (`octavi.toml`/`.json`),
  **`wmm.py`** (WMM 2025 ownship magvar), **`pyproject.toml`**
  (`octavi-trainer` script).
* **`windsaloft.py` / `datasrc/wx.py`** — wind is no longer a single uniform
  DIR/SPD for the whole flight. `windsaloft.WindsAloftProfile` is a small
  table of altitude/wind/temperature levels (`--winds-aloft
  "3000:280/20/-05 9000:300/35/-15 18000:310/55/-30"`, ALT:DIR/SPD[/TEMPC])
  that `SimModel` interpolates by the ownship's current altitude (direction
  interpolated via vector components so it blends the short way across
  north); a carried temperature feeds `density_ratio`'s ISA-deviation term so
  a non-standard-day OAT actually changes TAS-for-a-given-IAS, not just the
  wind. `datasrc/wx.py` (mirrors `datasrc/faa.py`'s stubbed-HTTP/cached
  pattern, but time-sensitive rather than AIRAC-cycled) fetches + caches live
  METARs and TAFs (NWS Aviation Weather Center JSON API) and a winds/temps
  aloft (FD) forecast by NWS region (`python -m datasrc.wx winds-aloft BOS`,
  `metar`, `taf`, `status`) — `decode_fd_text`/`decode_fd_group` parse the
  classic fixed-width ddff[tt] FD encoding. `--wx-region`/`--wx-station` load
  a cached fetch's profile into the sim (`_load_cached_winds_aloft`); fetching
  stays a separate offline CLI, same "no network in the runtime loop"
  principle as the AIRAC nav-data cache. The new **AUX Weather page** shows
  raw METAR/TAF for the flight plan's airports (`GpsNav.wx_station_idents`,
  route order, deduplicated), one station at a time — outer knob scrolls the
  station strip across the top — reading straight from `datasrc.wx`'s local
  cache (`render._wx_read`, memoised ~5 s against `Scene.t` so the page isn't
  re-reading a JSON file every frame). `datasrc.wx` now caches METAR/TAF
  **per station** (`<IDENT>_latest.json`) rather than only under a fetch's
  combined ident list, so any station is independently addressable regardless
  of how it was fetched.
* **`datasrc/dtpp.py`** — real FAA approach plates (d-TPP), fetched + cached
  individually (`aeronav.faa.gov/d-tpp/<cycle>/<pdf_name>`, no bulk archive)
  against a chart index (`d-TPP_Metafile.xml`, ~16 MB, pickle-cached after
  first parse). The **AUX Charts page** browses by airport (outer knob,
  same list as Weather) then by chart (inner knob); `ENT` fetches the
  selected plate (if needed, off the main thread) and hands it to the OS's
  own PDF viewer (`os.startfile`/`open`/`xdg-open`) — no PDF rendering
  inside the trainer. `python -m datasrc.dtpp update-index|list|fetch|open`.

**What's left** — nothing is blocking; the remaining work is polish:

1. NRST-VOR class filter (M3 follow-up). (`earth_awy.dat` X-Plane-fallback
   airways is moot - the X-Plane fallback itself was dropped 2026-09-11.)
2. Position page body (still falls through to Default NAV); NAV/COM page body.
   (`FINDINGS.md` D6 remainder.)
3. A live second FMS unit (`FMS2`) in `main.World` so `GpsNav.crossfill` has
   somewhere real to send a plan, and a bezel/keyboard path to
   `catalog_set_comment` (currently API-only - no on-screen free-text editor).
   Tracked from M8's "FMS2 addressing a distinct second GPS unit" follow-up.
4. VLOC ident audio (the Morse *tone* is a visual blink only - no sound
   backend in this trainer); IFR-1 long-press events are real now (`ifr1.
   LONG_PRESS_S`), but only `CLR`/COM-`SWAP` are wired to an action.
5. Weather: the AUX Weather page (raw METAR/TAF for flight-plan airports) is
   built, and `--wx-auto-refresh` (`wx_auto.WxAutoUpdater`) now keeps METAR
   (20 min default), TAF (60 min), and - with `--wx-region` - winds-aloft
   (60 min) refreshed in the background on their own schedule, in a daemon
   thread that never touches the ~30 Hz render/physics loop; still opt-in
   (off by default) so the trainer never talks to the network on its own
   unless asked. **`baro_inhg` is deliberately NOT auto-set from a fetched
   METAR's altimeter setting** (user, 2026-09-11: "one of the training steps
   is that the pilot has to fetch and set the barometer intentionally") -
   the Weather page shows the METAR's altimeter reading, the pilot reads it
   and dials COM2-shift's baro knob themselves, same as the real workflow.
   Don't wire that up later without asking. `wx_auto`'s METAR/TAF fetch path
   has now been verified against a real live request (network was available
   this session; a real KLNS METAR+TAF round-tripped end to end through the
   background updater into the on-disk cache) - the FD winds-aloft decode
   (`datasrc.wx.decode_fd_group`) itself is still only checked against
   synthetic fixtures, not a real captured FD text sample. No
   enroute-fix-specific weather (nearest-station lookup for a non-airport
   enroute fix) - the page shows flight-plan *airports* only.
6. The GNS **MNU key** (page-context menu: Invert/Copy/Sort/Delete Flight
   Plan) has no keyboard binding - `--no-device` play can't reach it. Left
   unbound rather than forced onto an unintuitive key (see the 2026-09-11
   keyboard-audit Done-log entry for why).

Parked / opportunistic: PyInstaller one-file bundle, GTN 650 variant, failure
injection.

---

## Milestones

### M0 — Scaffold & device I/O  ✅ (protocol confirmed on hardware)
- [x] Project skeleton, `requirements.txt`
- [x] `ifr1.py` — HID interface, `State`/`Event` decode, LED output
- [x] `ifr1.py` `__main__` live protocol explorer
- [x] `ARCHITECTURE.md`, `WORKING.md`, `README.md`
- [x] Rework `ifr1.py` framing: one canonical frame (report-id kept at [0],
      synthesised if the backend stripped it). Fixed an off-by-one — the old
      code stripped the id *and* used with-id offsets, so encoders/mode read one
      byte late. `Layout` dataclass; `tests/test_ifr1.py` (21) pins the decode.
- [x] `python ifr1.py raw` — diagnostic dump (each changed frame + which byte
      moved + reports the hidapi frame length / first byte)
- [x] **on-device confirmed (user + IFR-1, Windows, 2026-09-09):** every
      control decodes correctly. hidapi keeps the report id (`0B` at [0]); mode
      = byte [7] values 0-7; outer/inner = [5]/[6] signed; buttons exactly as
      `LAYOUT`; byte [4] stayed 0 (no extra buttons — the unit has 12). Device:
      `product='IFR1'`, interface 2, HID usage 1/5. 23 captured frames pinned in
      `tests/test_ifr1.py`.

### M1 — navmath.py + tests  ✅
- [x] `Point` type + angle helpers (`norm360`, `norm180`, `angle_diff`, `reciprocal`)
- [x] Great-circle: `great_circle_nm`, `initial_bearing`, `final_bearing`
- [x] `destination(point, bearing, dist)`
- [x] `cross_track_nm`, `along_track_nm` (signed, per ARCHITECTURE conventions)
- [x] `radial_dme(station, radial_mag, dme, magvar)` → Point
- [x] `intersect_radials(p1, brg1, p2, brg2)` → Point | None
- [x] `wind_triangle(tas, course, wind_from, wind_kt)` → (heading, gs, wca)
- [x] `wind_components(course, wind_from, wind_kt)` → (headwind, crosswind)
- [x] `turn_radius_nm(gs, bank)`, `standard_rate_turn_radius_nm(gs)`
- [x] `turn_anticipation_nm(gs, course_change, bank)`
- [x] `hold_entry(inbound_course, heading, turn)` → "direct"|"teardrop"|"parallel"
- [x] `tests/test_navmath.py` — 67 tests, analytic + round-trip references
- [x] `requirements-dev.txt` (pytest)
- [ ] later: swap caller-supplied magvar for a WMM model (also in M5)

### M2 — nav data sourcing (FAA) + `datasrc/`  ✅
- [x] `datasrc/airac.py` — `Cycle` type, `current_cycle`, `cycle_from_ident`,
      `cycle_from_date`, `.next/.prev/.contains/.days_until_expiry/.is_expired`,
      13-vs-14-cycle-year handling, ident validation. Anchor `2601` = 2026-01-22.
- [x] `tests/test_airac.py` — 67 tests (known ICAO/FAA effective dates, 2020 as a
      14-cycle year, rollover, round-trips, bad idents)
- [x] `datasrc/faa.py` — CIFP candidate URLs + NASR landing-page scrape,
      `fetch(cycle, kinds, force)` → download/unzip/sha256, `data/faa/<ident>/`
      + `manifest.json`, `load_manifest`, `Manifest.validity_line`,
      zip-slip guard, CLI `status | update [--cycle] [--kinds] [--force] | list`
- [x] `tests/test_faa.py` — 19 tests, HTTP layer stubbed, no real network
- [x] real end-to-end check: `update --cycle 2609 --kinds cifp` pulls the live
      FAA zip, extracts `FAACIFP18` (53 MB), writes a valid manifest
- [x] no new runtime deps (stdlib `urllib`, `zipfile`, `hashlib`)
- [x] `.gitignore` excludes `/data/`
- [x] NASR real end-to-end: `update --kinds nasr` pulls the CSV zip, keeps
      APT/AWY/FIX/FRQ/ILS/NAV_BASE (~10 MB each), used by `navdata.nasr`

### M3 — navdata  ✅
- [x] `navdata/model.py` — `Waypoint` / `VhfNavaid` / `NdbNavaid` / `LegType`
      (24 ARINC path terminators) / `NavDatabase` (ident→list stores, `find`,
      `nearest_fix`, `counts`, `notes`)
- [x] `navdata/arinc424.py` — `parse_lat` / `parse_lon` / `parse_magvar`
      (east +) / `parse_vhf_freq_mhz` / `parse_ndb_freq_khz` / `opt_int`
- [x] `navdata/cifp.py` — section D (VHF, incl. DME/TACAN-only 2nd-slot layout),
      DB (NDB), EA (enroute waypoints); area filter; skip-and-note vs `strict`
- [x] `navdata/__init__.load()` — resolve newest/〈cycle〉 cached FAA data via
      `datasrc`, parse, stamp cycle + validity window
- [x] CIFP section P·A airports + P·C terminal waypoints + P·G runways
      (`Airport` / `Runway` models; runways carry ILS ident + category, letters
      in the category column tolerated; orphan runways noted)
- [x] `tests/test_arinc424.py` (32) + `tests/test_cifp.py` (25), fixtures are
      verbatim public-domain CIFP lines
- [x] CIFP procedures — P·F approaches / P·D SIDs / P·E STARs → `Procedure`
      (per-transition legs + `assemble()`), `ProcedureLeg` (24 `LegType`s,
      fix/navaid refs, course, distance-or-hold-time, alt constraints,
      FAF/MAP/IAF/fly-over flags); continuation records skipped
- [x] CIFP section ER airways → `Airway` (`points`, `fix_idents()`,
      `segment(from,to)` both directions); records sorted by sequence
- [x] `NavDatabase.nearest_airports / nearest_navaids / nearest_waypoints`
      (bounding-box prefilter then haversine sort; `n` + `max_nm`)
- [x] perf: procedure legs parsed lazily (per-procedure, cached); `slots=True`
      on the value dataclasses. `load()` ~2 s.
- [x] `navdata/nasr.py` — parse `FRQ.csv` + `APT_BASE.csv` crosswalk, merge VHF
      voice comms (TWR/GND/ATIS/CLNC/APP/DEP/CTAF/UNICOM) onto `Airport.comms`;
      `load()` auto-merges when the CSVs are cached (6491 airports, KJFK/KBOS
      spot-checked). `tests/test_nasr.py` (17)
- [x] `navdata/xplane.py` — offline fallback: `earth_fix.dat` + `earth_nav.dat`
      (VOR/NDB only) → same `NavDatabase`. `resolve_data_dir` accepts the X-Plane
      root / `Resources/default data` / `Custom Data`. `load(xplane_dir=...)` or
      `XPLANE_DIR` env. `tests/test_xplane.py` (11)
      **[-] dropped 2026-09-11** - removed at the user's request; FAA CIFP/NASR
      is the sole nav-data source now, no X-Plane coupling even as a
      fallback. See "Done log" 2026-09-11 and `ARCHITECTURE.md` §5.2.

M3 follow-ups (not blocking M4):
- [ ] split IAF/feeder transitions vs common for approaches that have them
      (KJFK I04R is single-segment so it didn't surface); RF-arc centre fix;
      speed-limit column needs a real-data check
- [x] `nearest_navaids` now excludes localizers by default (`loc=False`); the
      CIFP parse also reads section P·I so terminal localizers are complete
- [-] X-Plane fallback: add `earth_awy.dat` (edge list → reconstruct `Airway`s)
      - moot: the X-Plane fallback it would extend was dropped 2026-09-11
- [ ] pickle/msgpack cache of the parsed DB keyed by cycle+sha for instant reloads
- [ ] cross-check CIFP navaid freq/magvar against NASR `NAV_BASE.csv`

### M4 — gns530.py state machine + tests  ✅ (core)
- [x] Page-group scaffold: NAV / WPT / AUX / NRST (`PageCursor`); big-knob =
      group / cursor-field, small-knob = page / (field edit stubbed), CRSR toggle
- [x] Flight plan: `FlightPlan` append / insert / delete / `activate_leg`;
      `load_flight_plan(idents)` resolves via `nearest_fix`; `load_procedure`
      pulls a CIFP procedure's fix-terminating legs (dedupes repeated fixes)
- [x] Direct-To (`direct_to(ident|wp)`): frozen course from present position;
      if the target is on the plan, sequencing resumes into it at the fix;
      off-plan target → SUSP at the fix. `DCT` then `ENT` = direct active wpt.
- [x] Automatic waypoint sequencing: turn-anticipation corner-cut for fly-by
      legs, overfly guard, SUSP at the end of the plan. OBS blocks sequencing.
- [x] OBS mode: `set_obs(course)` — DTK = selected course through the active
      fix, TO/FROM from along-track, no sequencing; `clear_obs` resumes
- [x] CDI source toggle GPS ↔ VLOC (`SWAP` in FMS mode / `toggle_cdi_source`)
- [x] `NavState`: dtk, brg, xtk (+ = right), tke, dist, dtg, `wpt_alert`,
      `turn_anticipation`, `next_dtk`, annunciators (SUSP / OBS / NAV DATA EXPIRED)
- [x] Nav-data-expiry annunciation + one-shot message when `today >= db.expires`
- [x] `tests/test_gns530.py` — 26 tests: 3-leg plan sequencing, XTK sign both
      ways, DTO (in-plan / off-plan / frozen course), OBS TO/FROM, events,
      expiry. Synthetic fixtures on the -74 meridian; no hardware / real data.
- [x] M9: CDI full-scale is core-owned (`NavState.cdi_scale_nm`) — 5.0 nm
      enroute, gradual slew to 1.0 terminal / 0.30 approach; `turn_now` flag
      drives the `TURN TO` advisory; `toggle_obs` / `nudge_obs`; `peek_messages`
      + render Message page; `CLR`-idle -> Default NAV; Direct-To cancel resumes
      the nearest leg
- [ ] parked (need render tier): rich cursor field + character entry for
      ident/freq; CDI GPS→VLOC auto-switch on approach; synthesise fixes for
      non-fix legs (VA/CA/FM/CD…) so full procedures load, not just fix legs
      (FINDINGS.md D4); Message page has no *composed* text beyond the expiry
      string yet

### M5 — instruments.py + sim_model.py + render.py + main.py  ✅
- [x] `instruments.py`: `compute_panel(own, nav_state, nav1/nav2/adf, phase,
      markers)` → `Panel`. GPS CDI linear (2/1/0.3 nm by `Phase`); VOR/LOC CDI
      angular from position vs the course line through the station (TO/FROM
      unambiguous in every quadrant); bearing pointers (VOR/ADF/GPS), DME slant
      range + time, marker footprints. All displayed values magnetic; `+`
      deflection = fly right. `tests/test_instruments.py` (17).
- [x] `sim_model.py`: `SimModel` point-mass ownship — `command(heading/altitude/
      tas)` or `follow_leg(nav_state)` (proportional intercept + wind crab);
      3 deg/s coordinated turns, capped VS, speed lag, vector wind solve →
      `Ownship`. `tests/test_sim_model.py` (11). Smoke: sim→gns530→instruments
      flies KBOS→BOS→PVD with the CDI centring.
- [x] `render.py`: `Renderer.draw(Scene)` — annunciator strip, 530 unit
      (NAV-default + Flight-Plan pages, CDI strip, bezel labels), HSI face
      (heading card, course pointer + deviation bar, VOR/ADF/GPS bearing
      pointers), DME window, marker lamps, ALT/VS/IAS. Headless-safe.
- [x] `render.py`: line-only track-up moving map — flight-plan legs (magenta
      active leg), DTO course, nearby airports/VORs, range rings, ownship.
- [x] `main.py`: `parse_args`/`build_world`/`step_once`/`run`. ~30 Hz loop,
      IFR-1 events → `gns530.handle_event`, keyboard fallback (steer / alt /
      speed / zoom / page / cursor / DTO / CDI / AP-NAV), `--no-device`,
      `--headless`, `--xplane-dir`, `--wind`. `tests/test_render.py` (8).
- [ ] non-FMS IFR-1 modes in `main.py`: COM/NAV standby tuning + SWAP flip,
      XPDR digits, AP row → annunciators (currently all events go to gns530
      which only acts on FMS mode)  → M6
- [ ] Frame-cost check: idle < a few % CPU, no GPU spikes (not yet measured)
- [ ] render polish: map label declutter, CDI-strip TO/FROM flag, OBS/DTO pages
- [ ] parked: slant-range DME needs station elevations wired from nav data;
      `Phase` currently a caller arg — gns530 should drive it (dep/dest distance,
      approach active); marker beacons need a loader (CIFP ILS records)

### M6 — steam-gauge panel + radios + autopilot  ✅
- [x] `sim_model.py`: `Ownship` gains `pitch_deg` / `turn_rate_dps` / `slip_skid`
      (pitch from VS+TAS, always coordinated); `command(vs=, clear_vs=)` explicit
      VS-hold that overrides the altitude target.
- [x] `radios.py`: `ComRadio` (25 kHz), `NavReceiver` (50 kHz + OBS +
      `resolve(db, pos)` → VOR *or* localizer, localizer paired to a runway for
      front course + a `(threshold, elev, 3.0°)` glideslope ref), `Transponder`
      (octal digits + mode cycle), `RadioStack`. `tests/test_radios.py` (13).
- [x] `instruments.py`: `glideslope_deviation` (±0.7°, + = fly up);
      `SixPack` + `six_pack(own, magvar)`; `NavHead` + `nav_head(receiver, …)`
      (VOR/LOC CDI, TO/FROM, GS, DME, bearing). `compute_panel` unchanged and
      still used by the GPS layout. `tests/test_instruments.py` (24).
- [x] `autopilot.py`: **S-TEC Fifty Five X** style (rate-based). Lateral
      OFF/LVL/HDG/NAV/APR/REV, vertical OFF/VS/ALT/GS; NAV/APR arm→capture,
      **GPSS** roll-steering modifier, **REV** back-course, VS/ALT knob,
      altitude-selector preselect capture, TRIM annunciator; `update(...) →
      Commands`. `tests/test_autopilot.py` (18).
- [x] `render.py`: `Scene.layout` `"gps"` | `"steam"`. Steam = `draw_six_pack`
      (ASI/AI/ALT/TC/HI/VSI), two `draw_nav_head` (compass card, course arrow,
      CDI dots, GS diamond, TO/FROM, DME, OBS) with `draw_hsi_head` as the NAV1
      alternate, `draw_ap_panel` (mode chips + bug + preselect + VS),
      `draw_radio_strip`. `tests/test_render.py` (12, headless).
- [x] `main.py`: `World` (+`RadioStack` +`Autopilot`) with `tick()→Frame`;
      `route_event` dispatches by IFR-1 mode selector — FMS→GPS, COM/NAV→standby
      tune + SWAP-flip + SWAP-held OBS, XPDR→octal digits + SWAP mode, AP row
      buttons→autopilot (any mode). Keys: `A` master, `F1..F5` modes, `o/O`
      NAV1 OBS, `k/p` NAV2 OBS, `H` NAV1 CDI↔HSI, `L` layout, `N` GPS-follow.
- [x] assets: bundled **B612 / B612 Mono** + **DSEG7 / DSEG14 Classic** (SIL OFL,
      `assets/fonts/`) + the **GNS 530 faceplate SVG** (Apache-2.0, from
      `allanglen/c172-flight-sim-panel`, `assets/instruments/garmin-gns-530/`).
      `render.py`: B612 Mono for text, `Renderer.lcd()` (DSEG7) for digital
      fields, `_load_bezel()` recolours + draws the faceplate as the GPS-layout
      bezel (hand-drawn fallback). Provenance in `assets/instruments/
      PROVENANCE.md`. `jQuery-Flight-Indicators` rejected (LICENSE is GPLv3).
- [x] LED feedback: `Autopilot.led_bitmask()` (AP/HDG/NAV/APR/ALT/VS bits, lit
      when active *or* armed); `run` writes it to the IFR-1 only on change.
- [x] render polish: attitude roll-scale arc + bank pointer; map label declutter
      (placed-rect overlap test; flight-plan labels forced, nearby labels yield);
      CDI-strip TO/FROM text + tucked triangle; DSEG bumped 15->16 px; approximate
      button legends over the 530 faceplate.
- [x] Frame-cost check (headless, 1000x640): tick ~0.11 ms, draw ~1.2 ms (gps) /
      ~1.4 ms (steam) -> ~4-5 % of a 30 Hz budget. Idle cost is negligible.
- [ ] Frame-cost check: idle < a few % CPU, no GPU spikes (still not measured)

### M7 — X-Plane feed + config + WMM + packaging  ✅
- [x] `xplane_feed.py`: pure `RREF` codec (`build_rref_request` 413-byte datagram,
      `parse_rref_response`), 11 position datarefs (lat/lon/elev/gs/hpath/psi/
      magvar/vs/tas/phi/theta), `feed_state_from_values` -> `FeedState` (mirrors
      `sim_model.Ownship`). `XPlaneFeed` binds a UDP socket, subscribes, `poll()`
      drains, `.alive`/`.state` gate on `stale_after_s`. `tests/test_xplane_feed.py`
      (9, incl. a 127.0.0.1 loopback "fake X-Plane").
- [x] Seamless fallback: `main.World.tick` uses `feed.state` when alive, else the
      sim; `_sync_sim_from` parks the sim on the live position so a dropout does
      not teleport the aircraft. `--xplane-feed` / `--xplane-host`.
- [x] `gdl90_out.py`: `crc16` (CRC-16-CCITT, checked against the spec worked
      example), `frame_message` (`0x7E` framing + `0x7D` stuffing), `heartbeat`
      /`ownship_report`/`ownship_geo_altitude`/`foreflight_id`, `GDL90Sender`
      (rate-limited). `--gdl90` (off by default — X-Plane emits GDL90 natively).
      `tests/test_gdl90_out.py` (10).
- [x] `config.py`: `octavi.toml` / `octavi.json` (cwd or `~/.config/...`, or
      `--config`), `DEFAULTS < file < CLI`. `parse_args` uses `argparse.SUPPRESS`
      defaults so only passed flags override the file. `tests/test_config.py` (7).
- [x] `wmm.py`: WMM 2025 declination (degree/order 12 spherical-harmonic
      synthesis, NOAA public-domain method) reading the vendored
      `assets/wmm/WMM2025.COF`. `main._local_magvar` prefers it, nearest-navaid
      is the fallback; refreshed as the aircraft moves. `tests/test_wmm.py`
      (103) checks every row of NOAA's `WMM2025_TestValues.txt` (< 0.01 deg,
      < 2 nT). Provenance in `assets/wmm/PROVENANCE.md`.
- [x] Packaging: `pyproject.toml` — `octavi-trainer = main:cli` console script,
      `pygame-ce` dep, `device`/`dev` extras, pytest config. `pip install -e .`
      is the supported install mode.
- [ ] parked: session scoring (procedure flown within tolerances, **done M16**);
      PyInstaller one-file bundle; ForeFlight discovery-JSON listener on
      :63093 to auto-find the tablet (**done 2026-09-11**, see Done log -
      `foreflight_discovery.py` / `--gdl90-discover`); refine the X-Plane
      magvar sign across sim versions.

### M8 — GNS 430 variant  ✅
- [x] Extracted the variant-independent GPS core into `gpsnav.py` (`GpsNav`):
      flight plan, Direct-To, OBS, sequencing, `NavState`, `PageCursor`,
      `handle_event`, expiry — all of the old `gns530.py`, renamed.
- [x] `gns530.py` / `gns430.py` are thin: `class Gns530(GpsNav)` /
      `class Gns430(GpsNav)` each bind one `Variant` (`name`, `short`,
      `screen_rows`, `screen_px`, `bezel_dir`, `bezel_aspect`, `screen_frac`)
      and re-export the shared types. No forked logic.
- [x] `render.py`: `_gns_unit` reads `Scene.variant` (falls back to
      `gns.variant` / `VARIANT_530`) for the faceplate SVG + aspect + screen
      cutout; `visible_fpl_rows()` caps `_below_bezel` / `_draw_fpl` to
      `variant.screen_rows`; `_draw_nav_default` compresses rows on the short
      430 screen; 530-specific bezel legends skipped for the 430.
- [x] `main.py`: `--unit 530|430` picks the class; `Scene.variant` passed
      through. (FMS2-as-second-GPS deferred — single unit for now.)
- [x] Vendored `assets/instruments/garmin-gns-430/faceplate.svg` (Apache-2.0,
      same `allanglen/c172-flight-sim-panel` repo); PROVENANCE.md updated.
- [x] Tests: `tests/test_gpsnav.py` (7) — scenario against bare `GpsNav`,
      530/430 produce identical `NavState`, `visible_fpl_rows` truncation,
      custom `Variant` pass-through, 430 layout draws headless. 422 total.
- [ ] parked: FMS2 addressing a distinct second GPS unit; refine the 430
      `screen_frac` against the real art; a 430-specific bezel key legend set.

### M9 — GNS 530 validation vs the Pilot's Guide  ✅
Reference: **Garmin GNS 530(A) Pilot's Guide and Reference, 190-00181-00
Rev. H** (Main SW 6.03). Full matrix + deferred list in `FINDINGS.md`.
- [x] F1 enroute GPS CDI full-scale `2.0 -> 5.0 nm` (`instruments`)
- [x] F2 CDI scale is a gradual, phase-of-flight slew owned by `gpsnav`
      (`NavState.cdi_scale_nm`; `update(..., dt)`), not a hard step on
      `dist_nm`. `instruments.compute_panel` uses it, phase table is the
      fallback.
- [x] F3 numeric CDI full-scale drawn at both ends of the graphic CDI
- [x] F4 active-leg marker above the CDI (`D>` / `->` / OBS / SUSP)
- [x] F5 `NEXT DTK ###°` / `TURN TO ###°` advisory (`NavState.turn_now`), lower
      right of the 530 screen
- [x] F6 OBS reachable: `GpsNav.toggle_obs` / `nudge_obs`; keys `B`, `-` / `=`;
      OBS course drawn on the faceplate layout
- [x] F7 message queue surfaced: `GpsNav.peek_messages`; `MSG` annunciator;
      `render` Message page; key `M`
- [x] F8 `CLR` with nothing to cancel -> Default NAV (`PageCursor.
      go_to_default_nav`); key `Home` (true long-press needs D9)
- [x] F9 `ComRadio.set_emergency()` (121.5); key `F11` (device wiring needs D9)
- [x] F10 `cancel_direct_to` resumes the flight plan on the closest leg
- [x] F11 per-leg DTK / DIS in the flight-plan list
- [x] tests: +14 (`test_gns530` CDI-scale / turn / OBS / cancel / CLR / peek,
      `test_instruments` scale, `test_radios` emergency, `test_render` message
      page). 563 total.
- [ ] deferred (milestone-scale, see `FINDINGS.md` D2-D9): flight-plan catalog,
      interactive PROC + transitions/VTF, non-fix leg synthesis, `SUSP` at MAP,
      WPT/AUX/NRST/Map page bodies, VNAV, VLOC auto-tune / ident / GPS->VLOC
      auto-switch, IFR-1 long-press events

### M10 — live IFR-1 passthrough + steam-panel fixes  ✅
- [x] Attitude indicator: blue-over-brown, masked round (`_attitude`)
- [x] FMS1/FMS2 AP row → GNS bezel keys (`_FMS_BEZEL`): AP=CDI HDG=OBS NAV=MSG
      APR=FPL ALT=VNAV VS=PROC; AP row no longer arms the AP in FMS mode
- [x] `PageCursor` knobs clamp instead of wrapping; `go_to_flight_plan`
- [x] AP mode: outer = ALT preselect (100 ft), inner = VS target (100 fpm)
- [x] COM1 shift knob = heading bug; COM2 shift knob = `World.baro_inhg`
      (Kollsman window + indicated-alt offset in `draw_six_pack`)
- [x] XPDR `Transponder.cursor` / `move_cursor`, digit underline in the strip
- [x] `World.show_msg` replaces the render `ui` flag. +4 tests → 567

### M11 — latched shift + Direct-To entry page  ✅
- [x] KNOB press toggles `World.shift_latched` per mode (COM1/COM2/NAV1/NAV2/
      XPDR); clears on a mode-selector change. The momentary SWAP-hold modifier
      is removed (`Event.shift` no longer routed). SWAP = flip-flop only;
      XPDR SWAP = `Transponder.ident()` (`World.ident_timer` decays it)
- [x] `Scene.shift_hint` / `selector_mode` → `SHIFT <fn>` chip + amber field
- [x] `gpsnav.DirectToEntry` (6-char buffer, `seeded` / `move_cursor` /
      `scroll_char`); `_begin_direct_to` opens it, `_confirm` resolves via
      `direct_to(ident)` or posts `NO WAYPOINT`, `lookup()` for the preview;
      `handle_event` routes the knobs to the buffer while open
- [x] `render._direct_to_page`; keyboard `d` opens, arrows edit, Return/Backspace
      confirm/cancel. +4 tests → 571

### M12 — feedback round (hints, CDI source, heading bug, Direct-To FPL)  ✅
- [x] XPDR digit underline only when `selector_mode == "XPDR"`; active COM/NAV
      standby freq turns amber
- [x] `World._panel` feeds `_tuned_nav(nav1/nav2)` to `compute_panel` only when
      the CDI source is VLOC → the FMS CDI key actually drives the HSI from the
      NAV1 course
- [x] `draw_six_pack(..., hdg_bug=)` — cyan heading bug on the HDG card
- [x] `_below_bezel` / `_draw_fpl` / `_map` lead with `D> <ident>` and dim the
      plan while a Direct-To is active
- [x] `_begin_direct_to` seeds "" once the plan is flown (was the last waypoint).
      +2 tests → 573

### M13 — steam-panel layout rebalance  ✅
- [x] `_steam_layout`: bigger six-pack (`Rect(8,26,624,402)`), NAV1 over NAV2
      on the right, full-width radio strip, S-TEC programmer beneath it
- [x] `six_pack_gauge_radius(rect)` is the single source of truth for round-
      instrument diameter; `draw_nav_head` / `draw_hsi_head` take `radius=` and
      a `_card_geometry` helper → NAV cards drawn at the EXACT six-pack radius
- [x] NAV heads default to `_vor_cdi_face` (old-school round VOR needle: rotating
      card, centre CDI needle, 5-dot scale, TO/FROM triangle, OFF flag); `H`
      still toggles NAV1 to `draw_hsi_head`
- [x] Info column (ident / OBS-or-CRS / TO-FROM / DME) + GS scale right of the
      card; AP readout column pulled in from the right edge

### M14 — non-fix procedure legs + SUSP-at-MAP  ✅
- [x] `PlanWaypoint` gains procedure metadata (`is_iaf`/`is_faf`/`is_map`/
      `fly_over`/`hold`/`manual`/`synthetic`) + a `stop_here` property
- [x] `GpsNav._expand_leg` synthesises a fix for every non-fix ARINC leg:
      CA/VA/FA (to-altitude, distance from `alt1_ft`), CD/VD/FD/FC
      (to-DME/distance, marched along the course), CR/VR (radial intersection),
      CI/VI (short lead), PI (procedure-turn extremity), HM/HA/FM/VM (fix +
      manual flag). AF/RF emit intermediate arc points around the recnav.
- [x] `_leg_course_true` / `_magvar_at` (nearest-navaid declination) convert the
      magnetic ARINC courses; IF-then-HF at one fix merges the hold flag onto
      the existing waypoint
- [x] `update()` auto-suspends on reaching a `stop_here` fix (MAP / hold /
      manual), latched by `(active, ident)` so un-suspending doesn't re-trip
- [x] `render`: `_fpl_tag` (IAF/FAF/MAP/HOLD/MAN/~) on the FPL page; MAP = amber
      X, hold = ring, FAF = filled dot on the map
- [x] real approaches verified (KASE RNV-F, KJFK I13L, KLAX I25L now load their
      missed-approach + intercept legs). +5 tests

### M15 — cursor-field engine + WPT / NRST page bodies  ✅
- [x] `handle_event` restructured into a page-aware dispatch (`_page_edit` /
      `_page_ent` / `_page_clr` / `_on_cursor_toggle`); the Direct-To dialog is
      a modal overlay
- [x] **Flight Plan page** (cursor on): big knob picks a row (incl. an "add"
      row), ENT opens a char editor on it, ENT again resolves + replaces/
      inserts, CLR deletes the row
- [x] **WPT pages** (Airport/Intersection/NDB/VOR): `GpsNav.wpt_entry` buffer,
      live `lookup()`, ENT = Direct-To; `render._draw_wpt_page` shows name /
      elev / runway / freqs / magvar / BRG-DIST
- [x] **NRST pages**: `GpsNav.nearest_for_page` + `nrst_sel`; big knob scrolls,
      ENT = Direct-To the highlighted fix; `render._draw_nrst_page` lists
      ident / BRG / DIST. +6 tests
- [x] `--approach "ICAO IDENT [TRANSITION]"` CLI flag loads a CIFP approach at
      startup (until the interactive PROC picker lands)

### M16 — session scoring + nav-data DB cache  ✅
- [x] `scoring.py`: pure `ScoreTracker.sample(nav, own, panel, nav_head,
      alt_target)` → `ScoreSummary` (xtk / CDI / glideslope RMS + max, TKE RMS,
      needle-peg count, altitude-vs-selected RMS, 0-100 score + letter grade
      against ¾-scale ACS tolerances). Only `LEG` / `DTO` samples count.
      `main.World.tick` feeds it; `run` prints the summary on exit. +6 tests
- [x] `navdata.load(cache=True)`: pickle the parsed `NavDatabase` to
      `data/faa/<cycle>/navdb-<hash>.pkl`, keyed by CIFP size+mtime + `comms` +
      `areas` + a schema version; stale pickles pruned on write; atomic replace.
      `load()` ~2.06 s → ~0.71 s. Procedures stay lazy (not pickled). +2 tests

---

## Side project — "faa2xp": back-port FAA data into X-Plane  (separate repo)

**Goal:** give a *running X-Plane 12 install* current US nav data (frequencies,
navaids, approaches) from the free FAA CIFP + NASR, without a Navigraph
subscription. X-Plane's bundled data is stuck at AIRAC 2406.

- Reuses this project's `datasrc/` (fetch + AIRAC math) and the FAA CIFP/NASR
  parsers from M3. New part is an **output writer**, not new parsing.
- Emit X-Plane native formats: `earth_nav.dat` (spec 1200), `earth_fix.dat`,
  `earth_awy.dat`, `earth_hold.dat` (1140), and `CIFP/<ICAO>.dat` per-airport
  procedure files. Match Laminar's header/version lines exactly.
- US records only; **merge** over the existing file so non-US data is preserved
  (X-Plane keys on region/ident — replace US rows, keep the rest).
- Install target: `X-Plane 12/Custom Data/` (overrides `Resources/default data/`),
  so it is reversible by deleting the custom files.
- Ship a manifest + a restore command. Warn that this is unofficial and not for
  real-world navigation.
- Open question: does X-Plane's GPS/FMS accept CIFP procedure files richer than
  what Laminar ships? Test with a known amended approach before committing.
- Not on the trainer's milestone path; start after M3 (parsers exist).

## Backlog / ideas (unscheduled)
- d-TPP approach-plate PDFs: fetch via `d-tpp_Metafile.xml`, show alongside the
  530; use the metafile's Added/Changed/Deleted flags to highlight amendments
- Non-US coverage via a Navigraph FMS Data API backend (paid; no redistribution)
- Autopilot mode logic driven by the IFR-1 AP row (HDG/NAV/APR/ALT/VS arm+capture)
- GNS 430 / GTN 650 screen variants
- Failure injection (VOR out, GPS LOI) for partial-panel practice
- Record/replay of an IFR-1 session for regression tests of `gns530`
- Audio: Morse ident for tuned navaids, marker tones

## Done log
- 2026-09-11  d-TPP approach plates (user, after a design discussion: "it
  would be nice to have the listing by airport (option b) that hands off to
  the os (other option b)... As it stands now, I have to open airnav and
  pull up the listed PDFs for the approach if I want it"). 789 tests
  (+31: dtpp 21 new, gns530 5, render 5).
  * Verified the real FAA structure before writing any fetch code (same
    discipline as the ForeFlight spec / Pilot's Guide checks earlier today):
    fetched the live directory listings at aeronav.faa.gov rather than
    guessing. Confirmed plates are served flat and individually per cycle
    (`aeronav.faa.gov/d-tpp/<cycle>/<pdf_name>`, no bulk zip needed) and the
    chart index lives at `.../xml_data/d-TPP_Metafile.xml` (~16 MB). Cross-
    checked the `record` element's child tags (`chartseq`/`chart_code`/
    `chart_name`/`useraction`/`pdf_name`) against a known third-party parser
    (jlmcgraw/GeoReferencePlates) since the FAA doesn't publish a bare XSD.
  * New `datasrc/dtpp.py`: `parse_metafile` (pure XML parse -> `ChartRecord`
    list), `charts_for_airport` (tries the ICAO ident with and without a
    leading "K", since the metafile uses the FAA LID - "LNS" not "KLNS"),
    `fetch_and_cache_metafile`/`fetch_and_cache_chart` (individual plates,
    cached forever once downloaded - a published cycle's charts never
    change), `load_index` (pickle-caches the ~24k-record parsed list next
    to the raw XML so repeat reads are fast), `open_with_os_default`
    (`os.startfile`/`open`/`xdg-open` - hands the PDF to a real viewer
    instead of rendering one inside the trainer, per the design discussion's
    conclusion). CLI: `update-index` / `list IDENT` / `fetch IDENT
    SELECTOR` / `open IDENT SELECTOR`.
  * New **AUX "Charts" page**: `gpsnav.py` gained `chart_airport_sel`
    (outer knob, reuses `wx_station_idents()` - the exact same flight-plan-
    airports list the Weather page already had) and `chart_sel` (inner
    knob, deliberately left UNCLAMPED in gpsnav.py since the pure core has
    no way to know how many charts an airport has - that's `datasrc.dtpp`'s
    job, clamped defensively wherever the value is actually used). ENT on
    this page is intercepted in `main.route_event` *before* `gns.
    handle_event` even runs (`_open_selected_chart`) - opening a PDF is
    real file I/O, kept out of gpsnav.py's pure state machine the same way
    Weather's METAR/TAF reads live in `render.py`, not `gpsnav.py`. The
    fetch+open runs on a one-shot background thread (same reasoning as
    `wx_auto.py`) so a slow download can't stall the ~30 Hz loop; progress
    and errors post as `GpsNav.messages` ("OPENING CHART for KLNS...",
    "OPENED ILS OR LOC RWY 08", or a "FAILED"/"NO CHART INDEX" note).
    `render._draw_aux_charts` + `Renderer._dtpp_charts_for` load the ~24k-
    record index ONCE and hold it on the Renderer instance for the session
    - re-parsing 16 MB of XML every frame would be a real "low CPU" priority
    violation, unlike the Weather page's small per-station JSON reads.
  * Verified against REAL live FAA data, not just synthetic fixtures: fetched
    the actual 16 MB metafile (24 231 chart records), pulled KLNS's 14 real
    charts (correctly including "ILS OR LOC RWY 08", the same approach used
    throughout this project's own test fixtures), downloaded the real
    `00927IL8.PDF` and confirmed a genuine `%PDF-1.4` header, then ran a full
    headless flow (`EMI KLNS` flight plan -> AUX Charts page -> simulated
    ENT) end to end against that real cached data with the OS-open call
    stubbed - confirmed it resolves and "opens" the correct plate. The real
    fetched cache was copied into `data/faa/2609/dtpp/` only for that one
    smoke test and removed afterward; nothing real was left in the repo.
- 2026-09-11  ForeFlight discovery listener (user: "Lets build out the
  ForeFlight discovery listener" - the backlog item parked since M7). 758
  tests (+21: foreflight_discovery 13 new, render/main 5, config 3).
  * Verified the actual wire protocol against ForeFlight's own published spec
    (foreflight.com/connect/spec/) rather than guessing from memory - it's
    the *opposite* direction from what the name suggests: **ForeFlight**
    broadcasts `{"App":"ForeFlight","GDL90":{"port":4000}}` on UDP :63093
    every ~5 s while in the foreground, so a GDL90 *source* (us) can learn
    its IP and switch from blind broadcast to direct unicast - useful on
    Wi-Fi networks (hotel/FBO guest nets, some home routers) that isolate
    clients and drop broadcast traffic.
  * New `foreflight_discovery.py`: `parse_discovery` (pure JSON decode,
    tolerant of any `App` name since only the port matters here) +
    `ForeFlightListener` (non-blocking UDP socket, same shape as
    `xplane_feed.py`'s RREF listener) - tracks every device currently
    broadcasting, keyed by source IP (a re-broadcast from the same IP
    updates the existing entry, not a duplicate); `.primary` = most
    recently heard, `.devices` = all currently-fresh ones.
  * `main.py`: `--gdl90-discover` (implies `--gdl90` - discovering a peer
    you won't send to is pointless) starts a listener; each tick
    `_apply_ff_discovery` retargets `GDL90Sender.addr` (a plain mutable
    tuple - no change needed to `gdl90_out.py` at all) to the primary
    device's unicast `(ip, gdl90_port)`, and falls back to the original
    broadcast address the moment the device goes stale (`stale_after_s`,
    default 15 s). Pulled the retarget decision out into its own function
    specifically so it's testable with fake `gdl90`/`listener` objects - no
    sockets, no pygame, no real network needed to verify the logic.
  * Verified with a real loopback integration test (`test_listener_tracks_a_
    device_from_a_loopback_broadcast`): a fake broadcaster sends the exact
    documented JSON to the listener's bound port and it correctly tracks,
    ages out, and re-tracks the device.
- 2026-09-11  Dropped the X-Plane nav-data fallback (user: "I don't want to
  do the X-Plane fallback anymore. I don't think i want to couple to the
  data that x-plane has available to this project. I would rather just have
  us pull from the FAA data as the only source"). 737 tests (-7: `xplane`
  module deleted outright, not folded elsewhere).
  * Deleted `navdata/xplane.py` and `tests/test_xplane.py` wholesale - not
    deprecated, not archived behind a flag, just gone. `navdata.load()` lost
    its `xplane_dir` parameter and the `XPLANE_DIR` env-var branch; it now
    has exactly one path (cached FAA CIFP) and raises `FileNotFoundError`
    with the `datasrc.faa update` fix-it command if nothing is cached, same
    as before for that branch.
  * `main.py`: `--xplane-dir` CLI flag and `Config.xplane_dir` removed;
    `World.__init__` calls `navdata.load()` directly (the now-trivial
    `_load_db(cfg)` helper was removed too, not just emptied).
    `config.DEFAULTS["xplane_dir"]` removed.
  * Explicitly NOT touched: `xplane_feed.py` / `--xplane-feed` / `--xplane-host`
    - that takes live *ownship position* from a running X-Plane over UDP,
      which is a different concern from nav-data sourcing and the user's
      request was specifically about not coupling to X-Plane's *data*.
      Confirmed this reading is right by how the request was phrased
      ("the data that x-plane has available") rather than asking to stop
      the sim-position integration.
  * Real, accepted consequence of this: non-US coverage and fully-offline
    use (no FAA cache, no network ever) have no fallback anymore. Recorded
    as a deliberate trade-off in `ARCHITECTURE.md` §6's decisions table,
    not a silently-dropped capability.
  * Docs updated everywhere the old fallback was described as available:
    README.md ("Nav data" section rewritten, X-Plane fallback subsection
    replaced with a note explaining the removal), ARCHITECTURE.md (§5.2
    rewritten as a removal record with the reasoning, module map + decisions
    table + test counts updated), this file's M3 checklist (`[x]` entries
    marked `[-]` dropped rather than deleted, so the history stays honest),
    "what's left" #1 (the `earth_awy.dat` X-Plane-fallback follow-up is now
    moot, not just undone).
- 2026-09-11  Sim time warp + keyboard cleanup (user: "add 1x, 5x, 10x, and
  20x as warp options... this should be a keyboard interface item. let's
  look through the current keybindings and cleanup where we can"). 744 tests
  (+8: render 4, config 4).
  * **Time warp**: `TIME_WARP_LEVELS = (1, 5, 10, 20)`; keys `1`/`2`/`3`/`4`
    set `ui["time_warp"]` live, `--time-warp` (argparse `choices=`, so an
    invalid value is a hard CLI error, not a silent fallback there - only a
    malformed *config file* value falls back to 1x) picks the starting
    speed. `run()`'s loop calls `w.tick(dt)` **N times per rendered frame**
    (N = the warp level) rather than once with an N-times-larger `dt` - every
    dt-sensitive threshold in `sim_model`/`autopilot`/`gpsnav` (altitude/GS
    capture bands, waypoint sequencing, turn anticipation) sees exactly the
    same real-time step it would at 1x, so nothing gets coarser or less
    accurate at higher warp, only less *often drawn* per second of flight.
    Rendering/LEDs/nearby-airport refresh still run once per real frame off
    the last sub-tick's `Frame`. `Scene.time_warp` -> an amber `WARP nx`
    annunciator (top strip) whenever it's above 1x, so it's never silently
    running fast. No effect with `--xplane-feed` (X-Plane's own real-world
    clock can't be sped up from here) - not special-cased, just naturally
    inert since `World.tick` ignores `dt` for kinematics while a feed is
    live. Verified live: `--time-warp 20 --headless` (5 rendered frames)
    produced **100 score samples** vs 5 at 1x for the same run - exactly the
    20x-more-ticks-per-frame the design calls for.
  * **Keyboard audit** (the actual "cleanup" ask): went through every bound
    key in `_on_key` against the module docstring and found the docstring
    was accurate but incomplete (missing `R` PROC - fixed) and, more
    importantly, that **WPT/AUX/NRST were flat-out unreachable from the
    keyboard** - `PgUp`/`PgDn` only ever drove the *inner* knob (page within
    a group); there was no keyboard path to the *outer* knob (page GROUP),
    so `--no-device` play could never reach the WPT pages, NRST, or the new
    AUX Weather page at all. Fixed: `Shift+PgUp`/`Shift+PgDn` now drive the
    outer knob (`Event(outer=+-1)`), plain `PgUp`/`PgDn` unchanged (inner) -
    chosen because Shift-as-"bigger step" was already the established local
    convention (OBS course `-`/`=` keys already use Shift for x10). No
    keys were reassigned or removed - 1-4 and the group-change combo were
    free real estate, so this was pure addition, not a remap that would
    break muscle memory or existing docs elsewhere. Also flagged, not
    fixed: the GNS's physical **MENU key (MNU)** - the `FplMenu` pop-up
    (Invert/Copy/Sort/Delete Flight Plan) - still has no keyboard binding;
    no free key read as an obvious "menu" mnemonic without colliding with
    something more central (`M` is already Message-page, matching the AP-row
    bezel's own MSG/MNU distinction), so it's left as a named, deliberate gap
    rather than forced onto an arbitrary key.
- 2026-09-11  Background weather auto-refresh (user: "let's get the weather
  automatically updating at a reasonable rate maybe every 15-30 minutes for
  METARs to catch SPECIs and TAFs and winds aloft forecasts, as
  appropriate"). 736 tests (+21: wx 4, wx_auto 13 new, render 2, config 2).
  * **`wx_auto.py`** (new): `WxAutoUpdater` runs in its own daemon thread
    (`start`/`stop`), re-fetching whatever `stations()` (typically
    `GpsNav.wx_station_idents`, so it always tracks the *current* flight
    plan, not a snapshot) and `region()` say are due, each on its own
    schedule - `METAR_REFRESH_S` 20 min (within the asked 15-30 min range,
    biased toward catching a SPECI), `TAF_REFRESH_S` / `WINDS_ALOFT_REFRESH_S`
    60 min (both real products are issued ~4x/day; hourly is generous, not
    aggressive). Intervals are floored at 60 s so a caller can't accidentally
    hammer the API. Fetches once immediately on start so the Weather page
    isn't empty for the first interval. Scheduling logic is split into a
    testable `poll_once(now=...)` (no real threads/sleeps needed to test it)
    that `_run`'s loop just calls repeatedly. A fetch failure is recorded
    (`last_metar_error` etc.) and retried next cycle, never raised - a
    background poller must not be able to kill the process.
  * `datasrc/wx.py` gained `fetch_and_cache_metar`/`_taf`/`_winds_aloft` -
    the actual fetch+cache logic factored out of the CLI's `_cmd_*`
    functions so `wx_auto` reuses it exactly rather than duplicating it;
    the CLI commands are now thin wrappers over these.
  * `main.py`: new `--wx-auto-refresh` (off by default - the trainer still
    never talks to the network on its own unless asked), `--wx-metar-minutes`
    / `--wx-taf-minutes` / `--wx-winds-aloft-minutes` overrides.
    `World.__init__` starts a `WxAutoUpdater` when requested and stops it on
    exit (`run()`, alongside the device/feed/GDL90 cleanup - a daemon
    thread, so no join is needed to let the process exit). If the active
    winds-aloft profile came from `--wx-region`/`--wx-station` (not a
    hand-typed `--winds-aloft`), `World._reload_cached_winds_aloft`
    (the updater's `on_winds_aloft` callback) re-applies the newly-fetched
    profile to `SimModel` automatically - an explicit `--winds-aloft` is
    never silently overwritten. No locking around the `SimModel.winds_aloft`
    attribute set from the background thread - a plain attribute assignment
    is safe enough under the GIL for a hobby trainer with no other writer of
    that field; noted as a deliberate simplification, not an oversight.
  * Verified against a **real** live fetch (network was available this
    session): `WxAutoUpdater(stations=lambda: ["KLNS"], ...).start()`
    actually reached aviationweather.gov and cached a real METAR + TAF
    within seconds; `python main.py --wx-auto-refresh --plan "EMI KLNS"
    --headless` prints "weather auto-refresh: METAR/20min TAF/60min" and
    writes a real cached METAR before the 5-frame headless run exits
    cleanly (no hang on shutdown). Full suite 736/0 fail.
- 2026-09-11  Direct-To ENT-ENT activation (user: "in the real 530 you have to
  press direct-to, input your destination, then ENT, then it says 'Activate?'
  in the lower right corner, then ENT again... validate that against our
  manual"). Validated against the Pilot's Guide (190-00181-00 Rev.H sec.4.1):
  confirmed exactly as described — ENT confirms the identifier ("Activate?"
  highlighted, Fig.4-3), a second ENT activates. The user also asked about a
  possible exception when the entered destination equals the current active
  waypoint; the manual's own "Re-centering the CDI (HSI) needle to the same
  destination waypoint" procedure says explicitly "Press the Direct-to Key,
  followed by the ENT Key twice" — **no such exception exists**, confirmed by
  quoting the manual back rather than guessing. Implemented the always-two-ENT
  workflow uniformly. 715 tests (+4: gpsnav 3, render 1).
  * `DirectToEntry` gained `confirming: bool`. `GpsNav._confirm()`: first ENT
    validates the identifier (`lookup()`) and sets `confirming=True` instead
    of activating; a second ENT calls `direct_to()` and closes the dialog.
    While confirming, `handle_event` stops routing knob turns to the buffer
    (the identifier is locked in, matching the real unit). `_cancel()`: CLR
    while confirming backs out to editing (one step, not a full close) —
    matches the GNS's general "CLR steps back" convention; a second CLR then
    closes the page.
  * `render._direct_to_page`: identifier cells dim and lose the cursor
    underline once confirming; "Activate?" appears amber in the lower-right
    corner (Pilot's Guide Fig.4-3), with the hint line switching from
    "ENT=confirm" to "ENT=activate  CLR=back".
  * Deliberately out of scope (same DCT-key page only): the pre-existing
    WPT/NRST-page single-ENT-to-direct-to shortcuts (`_page_ent` for
    Airport/Intersection/NDB/VOR and Nearest-* pages) are untouched — they
    don't go through the Select Direct-To Waypoint page/dialog at all, and
    the user's request was specifically about the DCT-key workflow.
- 2026-09-11  Weather page (user: "implement a weather page that shows the
  relevant weather in standard TAF/METAR formats for the selected airports
  and enroute locations"). 711 tests (+13: gpsnav 6, wx 3, render 4).
  * New **AUX "Weather"** page (`PAGE_GROUPS["AUX"]`), same row-select pattern
    as NRST: `GpsNav.wx_station_idents()` lists every *airport* in the active
    flight plan in route order, deduplicated (departure, enroute stops,
    destination - an enroute fix that isn't an airport has no METAR/TAF to
    show), falling back to the nearest airport with no plan loaded. Outer
    knob (cursor on) scrolls `wx_sel` across the station strip at the top of
    the page, same clamp-don't-wrap convention as `cat_sel`/`nrst_sel`.
  * `render._draw_aux_weather` shows the raw METAR then raw TAF text for the
    selected station, word-wrapped to the page width (`Renderer._wrap`, pixel
    measured via `font.size()`) with a staleness note under the METAR
    (amber past 75 min); "no cached METAR/TAF - datasrc.wx metar/taf IDENT"
    when nothing is cached yet. Reads come from `datasrc.wx`'s existing local
    JSON cache - no network call in the render path - but are memoised
    per-(station,kind) against `Scene.t` for ~5 s (`Renderer._wx_read`) so
    parking on this page doesn't re-read a JSON file every ~33 ms frame.
  * `datasrc/wx.py`: METAR/TAF caching changed from one combined file per
    *fetch* (keyed by the joined ident list, e.g. `KLNS-KJFK_latest.json`) to
    one file **per station** (`KLNS_latest.json`, `KJFK_latest.json`, ...),
    so a station fetched alongside others is still independently
    addressable. New `load_metar(root, ident)` / `load_taf(root, ident)`.
  * Scope choices made explicitly: the page shows flight-plan *airports* only
    (no nearest-station substitute for a plain enroute fix); no auto
    `baro_inhg` from the fetched altimeter setting; the page reads a static
    cache snapshot, same as `--wx-region`/`--wx-station` - nothing
    auto-refreshes from inside the trainer loop, by design (WORKING.md "what's
    left" #5, kept but narrowed).
- 2026-09-11  Winds aloft + live FAA weather (user request: poll live METAR/TAF/
  winds-aloft data, replace the uniform wind with a multi-altitude profile,
  carry temperature through the CLI). 698 tests (+47: windsaloft 14, wx 23,
  sim_model +6, config +4).
  * **`windsaloft.py`** (new, pure): `WindLevel`/`WindsAloftProfile` — a small
    altitude-sorted table interpolated by `wind_at(alt_ft)` (direction blended
    via vector components, not raw degrees, so 350->010 crosses through 000
    the short way) and `temp_at(alt_ft)`; holds the nearest level's value
    outside the table rather than extrapolating. `parse_cli` reads the
    `--winds-aloft "ALT:DIR/SPD[/TEMPC] ..."` format.
  * **`sim_model.py`**: `SimModel(winds_aloft=...)` / `set_winds_aloft()` -
    `_wind_solve`, `follow_leg`, and the IAS-hold TAS conversion all read the
    profile at the current altitude when one is set (`set_wind()` clears it,
    "one knob wins" like `target_tas`/`target_ias`). `density_ratio(altitude_ft,
    oat_c=None)` gained the optional OAT term: sigma is scaled by ISA-standard
    vs actual absolute temperature at that altitude, so a warm/cold profile
    changes TAS-for-a-given-IAS, not just groundspeed.
  * **`datasrc/wx.py`** (new): live weather fetch, mirrors `datasrc/faa.py`'s
    stubbed-`_http_get_text`/cached-with-manifest pattern but time-sensitive
    (a `<key>_latest.json` pointer + staleness check instead of an AIRAC
    expiry). METAR/TAF via the NWS Aviation Weather Center JSON API;
    winds/temps aloft via the classic fixed-width NWS "FD" text product,
    decoded by `decode_fd_text`/`decode_fd_group` (ddff[tt] groups, +50/-50
    direction-tens flags >=100 kt, temperature negative below 24 000 ft
    unless signed) - a best-effort read of the documented encoding, not
    checked against a live fetch (no network access available here). CLI:
    `python -m datasrc.wx metar|taf|winds-aloft|status`.
  * **`main.py`**: `--winds-aloft` (overrides `--wind`) and `--wx-region`/
    `--wx-station` (reads a profile out of whatever `datasrc.wx winds-aloft`
    last cached, via `load_winds_aloft_profile`) both feed `SimModel`. Fetching
    stays a separate offline CLI - nothing in `datasrc/wx.py` runs inside the
    trainer loop, same principle as the AIRAC nav-data cache.
  * Scope choices made explicitly rather than gold-plated: no in-cockpit
    METAR/TAF display page, no auto `baro_inhg` from a fetched altimeter
    setting, `--wx-region`/`--wx-station` read a static cached snapshot (no
    auto-refresh) - see "what's left" #5.
- 2026-09-11  Cleared the rest of the "what's left" polish list in one pass
  (user: "hit 1, 2, and 4 right now" against the 4-item list this file had):
  * **VNAV** (item 1): `gpsnav.VnavProfile`/`VnavStatus` + `vnav_set`/
    `vnav_status(alt_ft)` — a straight-line descent profile to a chosen
    flight-plan fix/altitude/angle, reporting distance-to-target, top-of-
    descent distance, and vertical deviation. Deliberately decoupled from
    `update()`/`NavState` (altitude isn't otherwise part of this core's
    state) so no existing caller's signature changed. New "VNAV" page (NAV
    group, ALT bezel key -> `PageCursor.go_to_vnav`); cursor-on outer picks
    the field (target/altitude/angle), inner edits it, ENT arms/disarms, CLR
    clears. `render._draw_vnav_page`.
  * **VLOC Morse ident** (item 1): `radios.morse_pattern`/`morse_is_keyed` —
    a pure dot-dash timeline (no audio backend) sampled each frame to blink
    an ident dot beside the station ident on the steam-layout NAV heads and
    radio strip (`render._ident_dot`, threaded via a new `Scene.t` / `World.t`
    elapsed-seconds field).
  * **IFR-1 long-press events** (item 1): the device only pushes a report on
    a state CHANGE, so a sustained hold with nothing else moving produces no
    further frames - `IFR1.poll()` now times a held button's press
    (`_press_time`) off the wall clock on every call (not just on new
    frames) and fires one synthetic `Event.long_press` past `LONG_PRESS_S`
    (0.6 s). `route_event`: `CLR`-hold -> Default NAV in FMS mode, COM
    `SWAP`-hold -> the 121.500 emergency channel (F8/F9's real trigger now;
    keyboard `Home`/`F11` stay as a no-hardware fallback).
  * **In-screen Map page + AUX bodies** (item 2): `render._map` takes an
    optional `rect` so the exact same track-up drawing renders small inside
    the 530 screen's "Map" page, not just beside the bezel. AUX pages get
    real content: Trip Planning (total/remaining distance + ETE), Utility
    (flight timer off `Scene.t` + GS/TAS/ALT), Setup (unit/CDI
    source/baro), Nav Data (source/cycle/effective/expiry + APT/VOR/NDB/
    WPT/AWY counts) — previously all four AUX pages silently fell through to
    the Default NAV body.
  * **Flight Plan Catalog polish** (item 4): `FlightPlan.comment` (auto
    "ORIGIN/DEST", or set via `catalog_store(slot, comment=...)` /
    `catalog_set_comment` — no on-screen free-text editor yet, API only),
    `catalog_sort()` (alphabetize by comment, empties packed last, wired to
    a new "SORT CATALOG" MNU option on the Catalog page), `crossfill(other)`
    (copies the active plan to another `GpsNav` instance — there's no live
    second FMS unit in `main.World` to send it to yet, tracked as an M8
    follow-up).
  +36 tests (gns530 +20, ifr1 +3, radios +6, render +7) -> 651.
- 2026-09-11  Flight Plan Catalog (`FINDINGS.md` D2 — next-highest-impact item
  on the "what's left" list): `GpsNav.fpl_catalog` holds 19 stored plans
  (FPL 01-19; FPL 00 is always the active `self.fpl`) - `catalog_store` /
  `catalog_store_first_empty` / `catalog_load` / `catalog_delete`. A new
  **Flight Plan Catalog** page (NAV group, after Flight Plan) lists each slot's
  endpoints + waypoint count; cursor-on ENT recalls a slot as the active plan
  and jumps to Flight Plan (a no-op on an empty slot), CLR deletes it in place.
  The previously-unused **MNU key** now opens a page-context pop-up
  (`FplMenu`) on the Flight Plan / Flight Plan Catalog pages: **Invert Flight
  Plan** (`invert_flight_plan` - reverses waypoint order, reactivates leg 1,
  drops any Direct-To/hold in progress), **Copy Flight Plan** (active plan, or
  a selected catalog entry, into the first empty slot), **Delete Flight Plan**
  (clears the active plan, or a catalog slot). Comment line / sort / crossfill
  remain deferred (crossfill needs a second unit; low value for a single-box
  trainer). +9 gns530 tests, +1 render test -> 627.
- 2026-09-11  OBS/SUSP bezel key conflation (user: "pressing the SUSP button
  to remove the suspend also triggers obs mode"): on the real 530 this is ONE
  physical key (labelled OBS/SUSP) whose function depends on context - release
  the suspend when suspended (a MAP fix, or mid-hold), else toggle OBS. The
  trainer's `_fms_bezel` "OBS" case called `toggle_obs()` unconditionally,
  and `toggle_obs`'s `set_obs(on=True)` path itself clears `suspended` - so
  releasing a suspend also silently dropped the GPS into OBS mode. Fixed:
  `_fms_bezel` now checks `gns.suspended or gns._hold_state` first and calls
  `toggle_suspend()` (which also arms a hold-in-progress exit) instead. +2
  tests -> 618. render/main 28.
- 2026-09-11  Holding patterns are actually flown (user: "have the trainer
  better reflect the 530W's behaviour of flying the racetrack and then
  continuing inbound") + two bugs it surfaced:
  * `PlanWaypoint` gained hold geometry (`hold_single_circuit`,
    `hold_inbound_true`, `hold_turn`, `hold_leg_min`/`hold_leg_nm`), populated
    for `HF`/`HM`/`HA` legs in `_expand_leg` (and preserved through the
    IF/HF-at-the-same-fix merge, which previously dropped them).
    `GpsNav._start_hold` picks the AIM 5-3-9 entry via `navmath.hold_entry`
    (already implemented + tested - reused rather than re-derived) and
    computes an outbound leg-length from the ARINC time/DME field; `_step_hold`
    drives synthetic outbound/inbound legs through the *same* dtk/xtk
    `NavState` fields as any other leg, so `follow_leg` and a coupled
    autopilot fly it with no special-casing; `_complete_hold_lap` auto-
    resumes sequencing for a single-circuit `HF` once re-established inbound,
    or repeats for `HM`/`HA` until `toggle_suspend()` (now hold-aware: it arms
    a release at the *next* inbound crossing rather than ripping out mid-turn).
  * Bug found building this: `turn_anticipation_nm`'s `tan(x/2)` blows up
    for a near-180 deg course change (exactly what a hold's next "leg" is) -
    was cutting the corner to a MAP/hold/manual fix many miles early. Capped
    `anticip` at 5 nm, and stop-here fixes are now excluded from the generic
    corner-cutting sequencer entirely (`_should_sequence(..., 0.0, None)`
    once triggered/released) - they're fly-TO fixes by definition.
  * `_proc_airport` / runway-fix work from the PROC-selector session is
    unaffected; `mode="HOLD"` is a new `NavState` value (excluded from scoring
    like OBS/SUSP; `render._draw_nav_default` shows it amber).
  +5 tests -> 615.
- 2026-09-11  Two bugs from flying the KLNS ILS 08 with the autopilot (user):
  1. **GS armed with APR never captured** - `main.World._auto_vloc` only ever
     activated the staged VLOC frequency when `gps_follow` was on, which has
     nothing to do with whether the *autopilot* is flying the approach (a very
     normal way to fly one!) - with GPS-follow off and the AP coupled, NAV1
     silently stayed on standby forever, so `vloc_valid`/`gs_valid` were never
     true and APR/GS could never capture. Fixed: auto-activation now requires
     `gps_follow and not ap.engaged` (truly nobody at the controls); otherwise
     a one-shot `TUNE VLOC ###.### (ident)` message reminds the pilot near the
     FAF to press SWAP themselves, exactly like the real 530W requires.
  2. **Persistent "flying right of the magenta line" on the localizer** -
     `radios._bind` forced a localizer's `station_magvar` to `0.0`, but a
     CIFP section P·I course (`loc_bearing_deg`) is magnetic like any other
     ARINC course field and needs a real conversion to true. Fixed: use the
     station's own `magvar_deg` if it has one, else the **airport's** published
     station declination (`Airport.magvar_deg` - matches how the course was
     actually encoded; a smooth WMM value differs from it by several degrees
     at some fields). Verified on KLNS ILS 08 / ILNS: CDI deflection while
     coupled fell from a **persistent 0.55 (55% of full scale)** to ~0.09, and
     true cross-track error from the charted centreline to under 0.03 nm.
  +2 tests -> 616.
- 2026-09-11  IAS set-point / pseudo speed manager (no throttle model):
  `sim_model` gains `density_ratio` / `tas_from_ias` / `ias_from_tas` (ISA
  troposphere), `SimModel.command(ias=)` + `target_ias`, and `Ownship.ias_kt`.
  A held IAS drives `target_tas` from the *current* altitude each step, so a
  constant-IAS climb reads rising TAS / GS; an explicit `tas=` cancels the hold.
  `main`: `_SHIFT_FN["AP"]="IAS"` so the AP-mode knob press latches a shift and
  the shifted inner knob trims `World.ias_target` in 5 kt steps
  (`set_ias_target` -> `sim.command(ias=)`); the `,`/`.` keys route through it
  too. The ASI (`instruments.six_pack`, GPS `_hsi` panel, steam six-pack) now
  reads **indicated** airspeed; a cyan IAS bug + `IAS SET` readout show the
  set-point when managed (`Scene.ias_target` / `ias_managed`). +6 tests -> 610.
  sim_model 15, render/main 25.
- 2026-09-11  Interactive PROC selector (`FINDINGS.md` D3): new
  `gpsnav.ProcSelect` + `GpsNav.begin_proc_select` / `_proc_event` /
  `_proc_advance` / `_proc_back` / `_proc_load` / `_activate_approach`. A modal
  three-step wizard (MENU → PROC → TRANS) reached via the PROC bezel key
  (`main._fms_bezel` "PROC"; keyboard `R`) and a matching keyboard-capture
  branch. Menu lists only the kinds the destination airport actually has
  (`db.approaches/stars/sids`), plus **Activate Approach** / **Activate
  Vectors-To-Final** once an approach is loaded (drops SUSP, steers to the
  IAF / FAF). Transition list prepends **VECTORS** for approaches (loads the
  common segment only — `Procedure.assemble` falls through on an unknown key).
  Destination airport = last plan fix that is an airport, else the last-loaded
  procedure's airport (`GpsNav._proc_airport`), else nearest. `render._proc_page`
  draws the scrolling list overlay; `route_event` lets the selector own every
  FMS key while open. +8 tests → 604. gns530 53, render/main 23.
- 2026-09-11  Approach VLOC auto-tune + runway-fix resolution: `load_procedure`
  now resolves an `RW..` MAP fix to a synthetic `_RwyFix` at the runway
  threshold (`_resolve_proc_fix`, `apt` threaded through `_expand_leg`), so a
  full ILS approach loads its missed-approach point. `GpsNav._approach_vloc_freq`
  stages the approach frequency: the runway's `ils_ident` if the plan touches a
  runway, else the first post-FAF `recnav_ident` that resolves to a navaid with a
  frequency -> `GpsNav.approach_freq` / `approach_ref`. `main.World.__init__`
  puts it in **VLOC standby**; `World._auto_vloc(nav)` (both tick paths) then,
  when `gps_follow` is auto-flying, activates the staged freq near the FAF
  (`<15 nm` or past it) and auto-switches the **CDI GPS→VLOC** once that freq is
  actually being received and the aircraft is within ~2 nm of / past the FAF —
  the GNS 530W behaviour. New `--approach "ICAO IDENT [TRANS]"` CLI arg +
  `approach` config key. +2 tests -> 596.
- 2026-09-11  VOR/DME service volumes + robust re-resolve: `_navaid_range_nm`
  gives an altitude-dependent SSV per ARINC facility class (terminal 25, low 40,
  high 40/100/130 nm) capped by radio line of sight. `NavReceiver.resolve` takes
  `alt_ft` and re-scans when the aircraft has moved >0.5 nm / changed >500 ft;
  the locked station is kept with 1.15x hysteresis unless it drops out of range
  or a >30%-closer co-frequency station appears -> the receiver times out on
  leaving a service volume and hands off to a new emitter on entering one,
  without flicker. `RadioStack.resolve` + `main.World.tick` thread altitude
  through. +4 tests -> 594.
- 2026-09-11  ILS reception + section P·I: `NavReceiver.resolve` now treats a
  **localizer as directional** - `_localizer_receivable` gates on ≤25 nm +
  within ±35° of the runway centreline (front/back), so a shared ILS frequency
  no longer binds the geographically closer field (INHK near EMI). VOR range
  260 -> 130 nm. `navdata/cifp.py` now parses **CIFP section P·I** (airport
  localizer & glideslope) - 1279 terminal localizers that were missing (incl.
  KLNS `ILNS`) now load with their course + runway + category; a co-located
  section-D copy is replaced. `VhfNavaid` gained `loc_bearing_deg` /
  `runway_ident` / `airport_ident` / `ils_category` + an `is_localizer`
  property. `_CACHE_SCHEMA` 2 -> 3. +3 tests -> 590.
- 2026-09-11  M14-M16 DONE (the top-5 feature-depth priorities):
  * **M14 procedure legs**: `PlanWaypoint` procedure metadata + `stop_here`;
    `GpsNav._expand_leg` synthesises every non-fix ARINC leg (CA/VA/FA/CD/VD/FD/
    FC/CR/VR/CI/VI/PI/HM/HA/FM/VM) + AF/RF arc points; `_magvar_at` for the
    mag→true course conversion; `update()` auto-SUSP at MAP/hold/manual fixes
    (latched by `(active, ident)`); render `_fpl_tag` + map MAP-X / hold-ring /
    FAF-dot. KASE/KJFK/KLAX approaches now load complete.
  * **M15 cursor-field engine**: `handle_event` -> page-aware dispatch
    (`_page_edit`/`_page_ent`/`_page_clr`/`_on_cursor_toggle`); Flight Plan page
    row-edit / char-edit / delete; WPT pages `wpt_entry` + `lookup` + ENT=DTO;
    NRST pages `nearest_for_page` + `nrst_sel` + ENT=DTO. `render._draw_wpt_page`
    / `_draw_nrst_page` bodies.
  * **M16 scoring + DB cache**: new `scoring.py` (`ScoreTracker` -> `ScoreSummary`
    with ACS-style grade), wired into `World.tick`, printed on exit.
    `navdata.load` pickle-caches the parsed DB (2.06 s -> 0.71 s), keyed on CIFP
    size+mtime + options + schema version.
  573 -> 587 tests (+14). `scoring` added to `pyproject.toml` py-modules.
- 2026-09-10  M13 DONE (steam layout): `render._steam_layout` reflowed - six-pack
  `Rect(8,26,624,402)` (bigger), NAV1/NAV2 stacked in a right column, radio strip
  full-width then `draw_ap_panel` beneath it. New `six_pack_gauge_radius(rect)`
  is the single source of truth for round-instrument diameter; `draw_nav_head` /
  `draw_hsi_head` gained a `radius=` param and a `_card_geometry` helper that
  left-biases a fixed-radius card in its box - so the NAV cards are drawn at the
  EXACT six-pack gauge radius (was min(w/2,h/2)-26, giving 99 vs 66). Info column
  (ident/OBS-or-CRS/TO-FROM/DME) + GS scale sit right of the card. AP readout
  column pulled in from the right edge. ATT round-mask width 0.72->0.82.
  Then: new `render._vor_cdi_face` (old-school round VOR needle - rotating card,
  centre CDI needle translated by deviation, 5-dot scale, TO/FROM triangle, red
  OFF flag); `draw_nav_head` calls it instead of `_cdi_card` (which `draw_hsi_head`
  still uses for the `H`-key HSI face). No test-count change - 573.
- 2026-09-10  M12 DONE (feedback round): `draw_radio_strip` gains
  `selector_mode` - amber standby freq on the active radio row, XPDR digit
  underline only when `selector_mode == "XPDR"`. `World._panel(own, nav)` feeds
  `_tuned_nav(nav1/nav2)` to `compute_panel` only when `nav.cdi_source ==
  "VLOC"`, so the FMS CDI key's source toggle drives the CDI/HSI course from
  NAV1's OBS. `draw_six_pack(..., hdg_bug=)` draws a cyan bug on the HDG card.
  `_below_bezel` / `_draw_fpl` / `_map` lead with `D> <ident>` and dim the plan
  when a Direct-To is active. `_begin_direct_to` seeds "" once `suspended`
  (was seeding the last waypoint). +2 tests -> 573.
- 2026-09-10  M11 DONE (latched shift + Direct-To page): `main.route_event` -
  the KNOB press toggles `World.shift_latched` (per current mode; clears on
  mode change) for COM1/COM2/NAV1/NAV2/XPDR; the momentary SWAP-hold modifier
  is gone (`Event.shift` no longer consulted). SWAP = flip-flop only; XPDR SWAP
  = `Transponder.ident()` (`World.ident_timer` counts it down in `tick`).
  `render`: `Scene.shift_hint` / `selector_mode` -> `SHIFT <fn>` chip in the
  annunciator bar + amber field on the radio strip; `NavReceiver.crs_select`
  removed (derived from shift now). `gpsnav`: `DirectToEntry` (6-char buffer,
  `move_cursor`/`scroll_char`, `seeded`), `_begin_direct_to` opens it,
  `_confirm` resolves via `direct_to(ident)` or posts `NO WAYPOINT`, `lookup()`
  for the render preview; `handle_event` routes the knobs to the buffer while
  it is open. `render._direct_to_page` draws the page + live resolution.
  Keyboard: `d` opens, arrows edit, Return/Backspace confirm/cancel. +4 tests
  -> 571.
- 2026-09-10  M10 DONE (live IFR-1 + steam-panel fixes): attitude indicator
  corrected to blue-over-brown and masked round (`render._attitude`). IFR-1
  routing (`main.route_event`): FMS1/FMS2 AP row -> GNS bezel keys
  (`_FMS_BEZEL`: AP=CDI HDG=OBS NAV=MSG APR=FPL ALT=VNAV VS=PROC), AP row no
  longer arms the autopilot in FMS mode; `PageCursor.on_outer/on_inner` clamp
  instead of wrapping (`go_to_flight_plan` added); NAV1/NAV2 KNOB push ->
  `NavReceiver.toggle_crs()` latches CRS edit (render shows `CRS`); AP mode
  outer=ALT preselect (100 ft) / inner=VS target (100 fpm); COM1 SWAP-held
  knob=heading bug, COM2 SWAP-held knob=`World.baro_inhg` (28-31 inHg,
  Kollsman window + indicated-alt offset in `draw_six_pack`); XPDR SWAP-held
  knob=`cycle_mode`, `Transponder.cursor` + `move_cursor` with an underline in
  `draw_radio_strip`. `World.show_msg` replaces the `ui` flag. +4 tests -> 567.
- 2026-09-10  M9 DONE: validated the GNS 530 surface against the Pilot's Guide
  190-00181-00 Rev. H (`FINDINGS.md`). Fixed F1-F11: CDI full-scale 5.0 nm
  enroute + gradual phase slew owned by `gpsnav` (`NavState.cdi_scale_nm`,
  `update(..., dt)`), numeric scale end-labels, active-leg marker, `NEXT DTK`/
  `TURN TO` advisory (`turn_now`), OBS reachable (`toggle_obs`/`nudge_obs`, keys
  `B` `-` `=`, drawn on the faceplate), Message page (`peek_messages`, `MSG`
  annunciator, key `M`), `CLR`-idle -> Default NAV (+ `Home`), per-leg DTK/DIS
  in the plan list, Direct-To cancel resumes the nearest leg,
  `ComRadio.set_emergency()` (`F11`). +14 tests -> 563. Milestone-scale gaps
  (Direct-To page, FP catalog, interactive PROC, non-fix legs, page bodies,
  VNAV) catalogued as `FINDINGS.md` D1-D9.
- 2026-09-10  M7 DONE: `xplane_feed.py` (RREF codec + `XPlaneFeed` UDP client +
  `FeedState`; loopback-tested), `gdl90_out.py` (CRC/framing + heartbeat/ownship/
  geo-alt/FF-id encoders + `GDL90Sender`; CRC checked vs spec example),
  `config.py` (`octavi.toml`/`.json`, DEFAULTS < file < CLI via `argparse.SUPPRESS`),
  `wmm.py` (WMM 2025 declination, vendored `assets/wmm/WMM2025.COF`, 103 tests vs
  NOAA table), `pyproject.toml` (`octavi-trainer` script). `main`: `--xplane-feed`
  swaps the motion source with sim-sync fallback; `--gdl90` broadcasts each loop;
  ownship magvar now WMM-first. 549 tests (+127).
- 2026-09-09  M8 DONE: split the GPS state machine into `gpsnav.py` (`GpsNav`
  + `Variant` + `VARIANT_530` / `VARIANT_430`); `gns530.py` / `gns430.py` are
  ~10-line `Variant` views (no forked logic, shared types re-exported).
  `render._gns_unit` variant-aware (faceplate SVG / aspect / screen cutout /
  `visible_fpl_rows` truncation / compressed 430 rows); `main.py` `--unit
  530|430`. Vendored the GNS 430 faceplate SVG (Apache-2.0). `tests/
  test_gpsnav.py` (7). 422 tests.
- 2026-09-09  M6 DONE: AP-row LED feedback (`Autopilot.led_bitmask`, written on
  change), render polish (attitude roll arc + pointer, map label declutter,
  CDI TO/FROM text, DSEG 16 px, 530 button legends), frame-cost measured
  (~1.3 ms/frame gps, ~1.5 ms steam; ~4-5 % of 30 Hz). 415 tests.
- 2026-09-09  M6 AP + 530 face: autopilot re-modelled as an **S-TEC Fifty Five X**
  (rate-based, LVL/HDG/NAV/APR/REV + VS/ALT/GS, GPSS, TRIM, VS knob) — new
  `draw_ap_panel`, `route_event` + keys (F1-F6, g, 9/0). Vendored the **GNS 530
  faceplate SVG** (Apache-2.0, `allanglen/c172-flight-sim-panel`) as the GPS-layout
  bezel via `render._load_bezel`; `assets/instruments/PROVENANCE.md` tracks it.
  414 tests.
- 2026-09-09  M6 assets: bundled OFL fonts (B612 / B612 Mono for text, DSEG7 /
  DSEG14 Classic for LCD readouts) under `assets/fonts/` + attribution;
  `render.py` `_load_font` / `Renderer.lcd`. Rejected `jQuery-Flight-Indicators`
  (LICENSE is GPLv3, not MIT as its README claims). 409 tests.
- 2026-09-09  M6 core: `radios.py` (COM/NAV + OBS + localizer/runway pairing +
  XPDR; 13 tests), `autopilot.py` (lateral/vertical mode SM, arm->capture, ALT
  preselect, GS; 13 tests), `instruments.py` +`glideslope_deviation`/`six_pack`/
  `nav_head` (24), `sim_model` +pitch/turn-rate/VS-hold, `render.py` steam layout
  (six-pack + 2 NAV heads + HSI variant + AP panel + radio strip; 12),
  `main.py` `World`/`route_event` wires the IFR-1 mode selector to radios + AP.
  408 tests. Docs: M6 restructured (X-Plane feed -> M7), GNS 430 plan -> M8,
  instrument-asset survey added to ARCHITECTURE sec.7.
- 2026-09-09  M5 DONE: `render.py` (immediate-mode pygame: 530 screen + CDI
  strip + bezel, track-up line-only moving map, HSI/DME/marker panel) +
  `main.py` (`parse_args`/`build_world`/`step_once`/`run`; ~30 Hz loop, IFR-1
  events + keyboard fallback, `--no-device` / `--headless` / `--xplane-dir` /
  `--wind`). `tests/test_render.py` (8, headless). 371 tests. `python main.py
  --plan "KBOS BOS PVD KJFK" --wind 300/25` renders and flies the plan.
- 2026-09-09  M5 (pure half): `instruments.py` (`compute_panel`→`Panel`;
  GPS-linear + VOR/LOC-angular CDI, bearing pointers, DME slant range, markers;
  17 tests) + `sim_model.py` (`SimModel` commanded / `follow_leg` intercept +
  wind; 11 tests). 363 tests. Smoke test flies a real leg sim→gns530→instruments.
  Left: `render.py` + `main.py`.
- 2026-09-09  M4 core: `gns530.py` — `FlightPlan` (+`load_procedure`), `DirectTo`
  (frozen course, resumes into plan), auto sequencing w/ turn anticipation → SUSP,
  OBS (TO/FROM), CDI source, expiry annunciation, `update()`→`NavState`, page/CRSR
  scaffold, `handle_event`. `tests/test_gns530.py` (26). 335 tests. Real check:
  KASE RNV-F approach loads through `load_procedure`; DTO DBL from the field.
- 2026-09-09  M0 fix + confirm: `ifr1.py` framing reworked (canonical frame,
  off-by-one in encoder/mode decode fixed). Every control verified on the real
  IFR-1 (Windows); 23 captured frames + 21 logic tests in `tests/test_ifr1.py`.
- 2026-09-09  M3 DONE: `navdata/nasr.py` (NASR comm-freq merge, 6491 airports) +
  `navdata/xplane.py` (offline fallback: earth_fix/earth_nav). `load()` wires
  FAA+NASR or X-Plane. 265 tests.
- 2026-09-09  M3 cont'd: CIFP airways (`Airway`/`segment`), `NavDatabase`
  nearest-N, lazy procedure parsing + `slots=True` (load 23 s -> ~2 s). 231 tests.
  Real file: 1504 airways, 0 skips.
- 2026-09-09  M3 cont'd: CIFP procedures — `Procedure` + `ProcedureLeg`, 24
  ARINC leg types, transition assembly, FAF/MAP flags. 217 tests. Real file:
  14338 procedures, 0 skips.
- 2026-09-09  M3 cont'd: CIFP airports + terminal waypoints + runways (ILS
  ident/cat); `Airport`/`Runway` models. 210 tests. Real file: 13307 airports,
  16879 runways, 70085 fixes, 0 skips.
- 2026-09-09  M3 (partial): `navdata/` package — model, ARINC 424 field parsers,
  CIFP navaid/NDB/waypoint parsing, `load()`. 49 new tests (202 total). Real
  `FAACIFP18` parses clean (2084 VOR/DME, 382 NDB, 32457 fixes, 0 skips).
- 2026-09-09  M2 datasrc: `airac.py` + `faa.py`; 86 new tests (153 total); live
  FAA CIFP fetch verified. Docs restructured for FAA-primary sourcing; "faa2xp"
  side project noted.
- 2026-09-09  M1 navmath.py: 15 pure functions + `tests/test_navmath.py` (67 passing).
- 2026-09-09  M0 scaffold: `ifr1.py`, protocol explorer, README, ARCHITECTURE, WORKING.
