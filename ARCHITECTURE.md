# ARCHITECTURE.md

Living structural map of **octavi-ifr-trainer**. Update this whenever a module is
added, its responsibility shifts, or a cross-cutting convention changes. Keep it
short — it is a map, not a manual.

Last updated: 2026-09-17

---

## 1. What this is

A lightweight IFR procedures trainer. It simulates a **Garmin GNS 530** and the
nav instruments around it (CDI/HSI, bearing pointer, DME, marker beacons),
rendered with cheap 2D drawing. Input comes from an **Octavi IFR-1** over USB
HID. It runs standalone on its own simple motion model, and can optionally slave
aircraft position to a running copy of X-Plane 12.

Navigation data comes from the **FAA's free public-domain digital products**
(CIFP + NASR), refreshed on the 28-day AIRAC cycle, with a validity window the
app displays and enforces like a real GPS. US coverage only for now.

Design priorities, in order: correct avionics behaviour, current & valid nav
data, low CPU/GPU cost, hackability. Not a scenery sim, not a real-world nav tool.

---

## 2. Runtime shape

Single Python process, one main loop (~20-30 Hz):

```
        ┌────────────┐      events        ┌───────────────┐
        │  ifr1.py   │ ─────────────────▶ │  gns530.py    │  avionics state machine
        │ (HID in)   │  mode/knob/keys    │  (the "brain") │
        └────────────┘                    └───────┬───────┘
              ▲  LEDs                             │ derived nav state
              │                                   ▼
        ┌─────┴──────┐   position/track   ┌───────────────┐   ┌────────────┐
        │ motion src │ ─────────────────▶ │ instruments.py │──▶│ render.py  │──▶ screen
        │ sim_model  │                    │ (CDI/HSI/DME) │   │ (pygame)   │
        │  or        │                    └───────────────┘   └────────────┘
        │ xplane_feed│
        └────────────┘
                            navdata.py  ──  loaded nav database, used by gns530 + sim_model
                            navmath.py  ──  pure geometry, used by everyone

   offline / on-demand, never in the loop:
        datasrc/  ──  fetch FAA CIFP + NASR for a cycle, cache, write manifest
                      navdata.py loads from this cache
```

The loop: drain IFR-1 events → feed `gns530` → step motion source → recompute
`instruments` from ownship + active nav source → `render`. No threads except an
optional UDP receiver in `xplane_feed`. **No network in the loop** — data
fetching is a separate CLI step.

---

## 3. Module map

| Module | Responsibility | Depends on | Status |
|---|---|---|---|
| `ifr1.py` | HID open/read/write for the IFR-1. `normalize_frame` → canonical frame (report-id kept at [0]); `Layout` holds byte offsets; decodes to `State` / edge-triggered `Event`. LED output. **Long-press events**: the device only pushes a report on a state CHANGE, so `IFR1.poll()` times a held button off the wall clock (`_press_time`, checked every call, not just on new frames) and fires one synthetic `Event.long_press` once a button crosses `LONG_PRESS_S` (0.6 s) — `main.route_event` wires `CLR`-hold → Default NAV (FMS mode) and COM `SWAP`-hold → 121.500 emergency. `__main__`: `raw` (diagnostic) / `explore`. | `hid` | **done — protocol confirmed on real hardware (Windows); 47 tests (logic + 23 captured frames + long-press timing)** |
| `navmath.py` | Pure functions: great-circle distance/bearing, destination, cross-track / along-track, radial-DME fixes, radial intersection, wind triangle, turn radius / anticipation, hold-entry sector, angle helpers. No I/O, stdlib only. | stdlib `math` | **done (67 tests)** |
| `datasrc/airac.py` | AIRAC cycle ↔ date math. `current_cycle(on)`, `cycle_from_ident("2610")`, `cycle_from_date`, next/prev, `contains`, `days_until_expiry`, `is_expired`. Handles 13- and 14-cycle years. Pure, no I/O. | stdlib `datetime` | **done (67 tests)** |
| `datasrc/faa.py` | Resolve per-cycle FAA URLs (CIFP candidate names; NASR by scraping the landing page), fetch CIFP (`FAACIFP18`) + NASR CSVs into `data/faa/<ident>/`, sha256, write `manifest.json`, zip-slip guard. CLI: `status` / `update [--cycle] [--kinds] [--force]` / `list`. HTTP behind `_http_get*` for test stubbing. | `airac`, stdlib `urllib`/`zipfile` | **done (19 tests, live-verified)** |
| `navdata/` | Load a nav database into memory. `model.py` (loader-agnostic types: `Waypoint`/`VhfNavaid`/`NdbNavaid`/`Runway`/`Airport`/`LegType`/`ProcedureLeg`/`Procedure`/`AirwayPoint`/`Airway`/`NavDatabase` with `find`/`nearest_*`), `arinc424.py` (ARINC 424-18 field parsers), `cifp.py` (parse `FAACIFP18` — sections D VOR/DME, DB NDB, EA/PC waypoints, PA airports, PG runways, **PI airport localizers** with course + runway, PD/PE/PF procedures, ER airways), `nasr.py` (merge `FRQ.csv` comm frequencies onto airports), `__init__.load()`. **Sole source: FAA CIFP + NASR comms** — no X-Plane fallback (`navdata/xplane.py` removed 2026-09-11, see §5.2); `load()` raises `FileNotFoundError` with the fix-it command if no cycle is cached. `load()` **pickle-caches the parsed DB** to `data/faa/<cycle>/navdb-<hash>.pkl` (keyed on CIFP size+mtime + `comms`/`areas` + schema version) → ~2 s cold, ~0.7 s warm; procedure legs stay **lazy** and are not pickled. | `navmath`, `datasrc` | **DONE — CIFP + NASR comm merge + nearest-N + DB cache (109 tests). Follow-ups (transitions split, VOR-only filter) parked in WORKING.md.** |
| `gpsnav.py` | **The trainer's core** — the variant-independent GPS/RNAV navigator. `FlightPlan` (append/insert/delete/activate-leg), `DirectTo` (frozen course; resumes into the plan if the target is on it; `cancel_direct_to` re-picks the closest leg), auto waypoint sequencing w/ turn anticipation (capped — `tan(x/2)` blows up near a 180° reversal) → SUSP at the end **and at the MAP / manual legs**; **a hold is actually flown, not just suspended** (`_start_hold`/`_step_hold`/`_complete_hold_lap`: the AIM 5-3-9 entry via `navmath.hold_entry`, then synthetic outbound/inbound legs through the normal dtk/xtk `NavState` fields so `follow_leg`/a coupled AP fly it unmodified; a single-circuit `HF` auto-continues once re-established inbound, `HM`/`HA` repeat until a hold-aware `toggle_suspend()` arms a release at the next inbound crossing) (`PlanWaypoint.stop_here`, latched), OBS (`toggle_obs` / `nudge_obs`), CDI source GPS/VLOC, **phase-of-flight CDI full-scale** (`NavState.cdi_scale_nm`, gradual 5.0→1.0→0.30 nm slew), `NEXT DTK`/`TURN TO` staging, nav-data-expiry annunciation + message queue. `load_procedure` assembles a CIFP procedure and **synthesises a fix for every non-fix ARINC leg** (`_expand_leg`: CA/VA/FA/CD/VD/FD/FC/CR/VR/CI/VI/PI/HM/HA/FM/VM + DME/RF arcs), so a full published approach loads; it also resolves an `RW..` MAP fix to the runway threshold (`_RwyFix` / `_resolve_proc_fix`) and stages the approach frequency (`approach_freq` / `approach_ref` via `_approach_vloc_freq` — runway `ils_ident`, else the first post-FAF `recnav_ident`) for `main` to drop into VLOC standby. `update(pos, track, gs, dt=None)` → `NavState`. `PageCursor` NAV·WPT·AUX·NRST + big/small-knob (clamped) + CRSR; `handle_event` is a **page-aware dispatch** — the Select Direct-To dialog (`DirectToEntry`) is a modal overlay: ENT always confirms the typed identifier first (`DirectToEntry.confirming` → "Activate?" highlighted, knobs locked out) and a **second** ENT actually activates (`_confirm`) — per the Pilot's Guide sec.4.1, this is true even re-centring on the already-active waypoint, no single-ENT shortcut; CLR while confirming backs out to editing rather than closing the page. The Flight Plan page edits/adds/deletes waypoints in place; WPT pages type an identifier (`wpt_entry` + `lookup()`); NRST pages scroll `nearest_for_page()` — WPT/NRST ENT flies a Direct-To directly (a documented page-specific shortcut, separate from the DCT-key dialog above). The **PROC key** opens a second modal overlay, `ProcSelect` (`begin_proc_select` / `_proc_event`): a MENU → PROC → TRANS wizard that ends in `load_procedure` (VECTORS = final segment only), with **Activate Approach / Vectors-To-Final** once an approach is loaded. The **MNU key** opens a third modal overlay, `FplMenu`, on the Flight Plan / Flight Plan Catalog pages: **Invert Flight Plan** (`invert_flight_plan` — reverses waypoint order, reactivates leg 1), **Copy Flight Plan** (stores the active plan, or a selected catalog entry, into the first empty catalog slot), **Sort Catalog** (`catalog_sort` — alphabetizes by comment, empties packed last), **Delete Flight Plan** (clears the active plan, or a catalog slot). The **Flight Plan Catalog** page (`fpl_catalog`, 19 stored plans = FPL 01-19; FPL 00 is always the active `self.fpl`) lists each slot's `FlightPlan.comment` (auto "ORIGIN/DEST", or `catalog_set_comment`) + waypoint count; cursor-on ENT recalls a slot as the active plan and jumps to Flight Plan, CLR deletes it in place (`catalog_store`/`catalog_store_first_empty`/`catalog_load`/`catalog_delete`). `crossfill(other)` copies the active plan to another `GpsNav` instance (no live 2nd FMS unit in `main.World` yet). The **VNAV page** (`VnavProfile`/`VnavStatus`, ALT bezel key → `PageCursor.go_to_vnav`) programs a target fix/altitude/descent-angle picked from the remaining flight plan (cursor-on outer = field, inner = value); `vnav_status(alt_ft)` reports distance-to-target, top-of-descent distance and vertical deviation once armed — deliberately decoupled from `update()`/`NavState` (altitude isn't otherwise part of this core's state) so no existing caller's signature changed. The **Charts page** (`chart_airport_sel`/`chart_sel`) tracks browse position only — no I/O in this pure core; ENT there is intercepted in `main.route_event` (`_open_selected_chart`) before `handle_event` even sees it, since opening a PDF is real file I/O, not avionics state. `Variant` + `VARIANT_530` / `VARIANT_430`. | `navmath`, `navdata`, `ifr1` | **done — core + 99 tests (92 gns530 scenario + 7 gpsnav)** |
| `gns530.py` / `gns430.py` | Thin views over `gpsnav.GpsNav`: `class Gns530(GpsNav)` binds `VARIANT_530`, `class Gns430(GpsNav)` binds `VARIANT_430`; each re-exports the shared types so `from gns530 import …` keeps working. **No avionics logic** — the state machine does not fork; only the display (screen height, visible-row count, faceplate art) differs. | `gpsnav` | **done (M8)** |
| `radios.py` | `ComRadio` (+ `set_emergency` 121.5) / `NavReceiver` (active+standby, split MHz/kHz knob tuning, swap, per-receiver OBS) / `Transponder` (octal digits + `cursor`/`move_cursor` + mode + `ident()`) / `RadioStack`. `NavReceiver.resolve(db, pos, alt_ft)` re-evaluates reception as the aircraft moves: a **VOR/DME** is usable inside its **altitude-dependent service volume** (`_navaid_range_nm` — terminal 25, low 40, high 40/100/130 nm by the ARINC class char) capped by radio line of sight; a **localizer is directional** (`_localizer_receivable` — ≤25 nm and within ±35° of the runway centreline, front *or* back). The locked station is held with hysteresis until it drops out of range or a decisively closer co-frequency station appears, so the receiver **times out when you leave a service volume and picks up a new emitter when you fly into one**, without flicker. LOC course + glideslope come from the CIFP section P·I record; geometric runway pairing is the fallback. A P·I localizer course is **magnetic** and doesn't carry its own `magvar_deg`, so `_bind` converts it with the **airport's** published station declination (`Airport.magvar_deg`) — not a smooth WMM value, and not zero (an earlier shortcut that put the CDI persistently off-centre by several degrees). **VLOC Morse ident**: `morse_pattern`/`morse_is_keyed(ident, t)` — a pure dot-dash timeline (dot=1 unit/dash=3/gaps per ITU timing), no audio backend; `render._ident_dot` samples it each frame to blink an indicator beside the station ident. Pure, no I/O. | `navmath`, `navdata` | **done (25 tests)** |
| `autopilot.py` | `Autopilot` - an **S-TEC Fifty Five X** style (rate-based) state machine. Lateral OFF/LVL(wings level)/HDG/NAV/APR/REV, vertical OFF/VS/ALT/GS. NAV & APR arm->capture on needle-alive; **GPSS** roll-steering modifier; **REV** = back-course; VS/ALT knob; altitude-selector preselect capture; TRIM annunciator. `update(nav, own, magvar, vloc_*, gs_*)` -> `Commands` for `SimModel`. Pure. | `navmath` | **done (18 tests)** |
| `instruments.py` | Pure CDI/HSI/bearing-pointer/DME/marker math + `compute_panel(own, nav_state, nav1/nav2/adf, phase, markers)` -> `Panel`. GPS CDI is linear — full-scale from `NavState.cdi_scale_nm` when set (gpsnav's gradual 5/1/0.30 nm slew), else the `GPS_FULL_SCALE_NM[Phase]` table (5/1/0.30); VOR/LOC CDI is angular, derived from position vs the course line through the station (unambiguous TO/FROM in every quadrant); DME is slant range; the ASI (`six_pack`) reads **indicated** airspeed (`Ownship.ias_kt`, TAS fallback); all displayed values magnetic. `+` deflection = fly right. | `navmath`, `gns530` (NavState, duck-typed) | **done (19 tests)** |
| `sim_model.py` | `SimModel` point-mass ownship: `command(heading/altitude/tas/ias/vs)` or `follow_leg(nav_state)`. Coordinated 3 deg/s turns, capped VS (or explicit VS hold), speed lag, vector wind solve -> `Ownship` (pos/heading/track/gs/tas/**ias**/alt/vs/bank/**pitch/turn_rate/slip**). A held **IAS set-point** (the trainer's pseudo speed manager — no throttle model) drives `target_tas` from the current altitude via `tas_from_ias` (ISA `density_ratio`), so a constant-IAS climb reads rising TAS/GS; an explicit `tas=` cancels it. `ias_from_tas` gives the ASI reading. **`winds_aloft`** (a `windsaloft.WindsAloftProfile`, optional) overrides the uniform `wind_from`/`wind_kt` — `_wind_solve`/`follow_leg`/the IAS-hold TAS conversion all read it at the current altitude; `set_wind()` clears it (one wind source wins, like `target_tas` vs `target_ias`). `density_ratio(altitude_ft, oat_c=None)` scales sigma by ISA-standard vs actual absolute temperature when an OAT is given, so a profile's temperature changes TAS for a held IAS, not just groundspeed. Fallback when no X-Plane feed. | `navmath`, `windsaloft` | **done (21 tests)** |
| `windsaloft.py` | Pure: `WindLevel`/`WindsAloftProfile` — an altitude-sorted table of wind (dir/kt) + optional temperature (C), interpolated by `wind_at(alt_ft)`/`temp_at(alt_ft)` (direction blended via vector components so it crosses the short way through north; holds the nearest level's value outside the table, no extrapolation). `parse_cli` reads the `--winds-aloft "ALT:DIR/SPD[/TEMPC] ..."` format; `from_fd_levels` bridges a `datasrc.wx` FD decode. | stdlib `math` | **done (14 tests)** |
| `datasrc/wx.py` | Live weather: fetch + cache METAR/TAF (NWS Aviation Weather Center JSON API) and a winds/temps-aloft (FD) forecast by NWS region, decoded by `decode_fd_text`/`decode_fd_group` (fixed-width ddff[tt] groups; +50/-50 on the direction-tens flags speeds >=100 kt; temperature negative below 24 000 ft unless signed — a best-effort read of the documented NWS FD encoding, checked against synthetic fixtures, not a real captured FD text sample). Time-sensitive, not AIRAC-cycled: each fetch writes a dated file plus a `<key>_latest.json` pointer under `data/wx/`, with a staleness check instead of a hard expiry. METAR/TAF are cached **per station** (`<IDENT>_latest.json`, even when several idents were fetched together) so `load_metar(root, ident)` / `load_taf(root, ident)` can address any one independently. `fetch_and_cache_metar`/`_taf`/`_winds_aloft` are the shared fetch+cache entry points — the CLI's `_cmd_*` functions and `wx_auto.WxAutoUpdater`'s background poller both call these rather than duplicating the logic. HTTP behind `_http_get_text` for test stubbing, same convention as `datasrc/faa.py`. CLI: `metar IDENT...` / `taf IDENT...` / `winds-aloft REGION [--fcst]` / `status`. `load_winds_aloft_profile(root, region, station)` bridges a cached fetch to a `windsaloft.WindsAloftProfile` for `main.py`'s `--wx-region`/`--wx-station`. | `windsaloft`, stdlib `urllib`/`json` | **done (30 tests) — METAR/TAF fetch path live-verified on real hardware/network** |
| `datasrc/dtpp.py` | Approach plates: fetch + cache the d-TPP chart index (`d-TPP_Metafile.xml`, `parse_metafile` -> `ChartRecord` list, pickle-cached alongside the raw XML since parsing ~16 MB / ~24k records isn't free) and individual plate PDFs on demand (`fetch_and_cache_chart` — cycle-scoped plates never change once published, so a cached one is never re-fetched). `charts_for_airport(records, ident)` matches the FAA LID the metafile uses against an ICAO ident by trying both with and without a leading "K". `open_with_os_default` hands a cached PDF to the OS's own viewer (`os.startfile` / `open` / `xdg-open`) — nothing here renders a PDF. CLI: `update-index` / `list IDENT` / `fetch IDENT SELECTOR` / `open IDENT SELECTOR` (selector = list-number or a case-insensitive chart-name substring). HTTP behind `_http_get` for test stubbing, same convention as the rest of `datasrc/`. | stdlib `urllib`/`xml.etree`/`pickle`/`subprocess` | **done (21 tests) — live-verified against real aeronav.faa.gov data (24 231 real chart records parsed, a real KLNS ILS plate PDF fetched and confirmed `%PDF-1.4`)** |
| `wx_auto.py` | Opt-in (`--wx-auto-refresh`) background weather refresh. `WxAutoUpdater` runs `datasrc.wx.fetch_and_cache_*` in its own daemon thread on a per-product schedule (`METAR_REFRESH_S` 20 min, `TAF_REFRESH_S`/`WINDS_ALOFT_REFRESH_S` 60 min — all overridable, floored at 60 s), re-deriving the station list (`stations()`, typically `GpsNav.wx_station_idents`) and region (`region()`) each cycle rather than freezing them at construction. Fetches once immediately on `start()`; a fetch failure is recorded (`last_metar_error` etc.) and retried next cycle, never raised. Scheduling (`poll_once(now=...)`) is separated from the thread loop (`_run`) so it's testable without real threads or sleeps. `on_winds_aloft` callback lets `main.World` re-apply a freshly-fetched winds-aloft profile to `SimModel` after each successful region fetch. | `datasrc.wx`, stdlib `threading` | **done (13 tests) — live-verified against a real METAR/TAF fetch** |
| `xplane_feed.py` | Optional live ownship from X-Plane. `build_rref_request` / `parse_rref_response` are pure `RREF`-protocol codecs; `feed_state_from_values` -> `FeedState` (same attrs/units as `sim_model.Ownship`). `XPlaneFeed` binds a UDP socket, subscribes to 11 position datarefs, `poll()` drains packets, `.alive` / `.state` gate on freshness (`stale_after_s`). `--xplane-feed`; `main.World` swaps motion source per-tick and syncs the fallback sim so a dropout doesn't teleport. | stdlib `socket`/`struct`, `navmath` | **done (9 tests + loopback)** |
| `gdl90_out.py` | Broadcast ownship as GDL90 over UDP:4000 for a tablet EFB (ForeFlight / Garmin Pilot). Pure encoders: `crc16` (CRC-16-CCITT), `frame_message` (`0x7E` framing + `0x7D` stuffing), `heartbeat` (0x00), `ownship_report` (0x0A), `ownship_geo_altitude` (0x0B), `foreflight_id` (0x65/00). `GDL90Sender.send(own)` rate-limits internally; `.addr` is a plain mutable `(host, port)` tuple, which is all `main._apply_ff_discovery` needs to retarget it live. `--gdl90` (off by default; X-Plane emits this natively). | stdlib `socket`/`struct` | **done (10 tests, CRC vs spec example)** |
| `foreflight_discovery.py` | Listens for ForeFlight's *own* UDP broadcast on port 63093 (`{"App":"ForeFlight","GDL90":{"port":4000}}` every ~5 s — the opposite direction from `gdl90_out.py`'s traffic; see the [GDL 90 Extended spec](https://www.foreflight.com/connect/spec/), "ForeFlight Broadcast"). `parse_discovery` is pure decode; `ForeFlightListener` is a non-blocking socket wrapper (same shape as `xplane_feed.py`'s RREF listener) tracking every device currently broadcasting, keyed by source IP, with `.primary` = most-recently-heard, `.devices` = all fresh ones. `main.py`'s `--gdl90-discover` (implies `--gdl90`) starts one and calls `_apply_ff_discovery` each tick to retarget `GDL90Sender.addr` to a discovered device's unicast `(ip, gdl90_port)`, falling back to the original broadcast address once the device goes stale (`stale_after_s`, default 15 s — a few missed ~5 s broadcasts). Nothing here sends anything; it only tells `main.py` where to aim. | stdlib `socket`/`json` | **done (13 tests incl. a loopback integration)** |
| `config.py` | Optional `octavi.toml` / `octavi.json` (cwd or `~/.config/octavi-ifr-trainer/`, or `--config PATH`). `DEFAULTS < file < CLI` via `merge()`; keys mirror the long flags. `main.parse_args` sets `argparse.SUPPRESS` defaults so only user-passed flags win. | stdlib `tomllib`/`json` | **done (7 tests)** |
| `wmm.py` | World Magnetic Model 2025 declination for ownship magvar. `WmmModel.from_cof` parses the vendored `assets/wmm/WMM2025.COF`; `field()` is the degree/order-12 spherical-harmonic synthesis (NOAA's public-domain method); `declination()` returns degrees **east-positive**. `main._local_magvar` prefers it, nearest-navaid declination is the fallback. Matches NOAA's test table to < 0.01 deg. | stdlib `math`, WMM2025.COF | **done (103 tests vs NOAA reference)** |
| `render.py` | Immediate-mode pygame-ce drawing, headless-safe. Bundled B612 Mono / DSEG7 fonts (`_load_font`, `Renderer.lcd`); GNS 530 faceplate SVG bezel (`_load_bezel`). `Renderer.draw(Scene)` branches on `Scene.layout`: **"gps"** = GNS bezel (530 or 430 faceplate SVG) + screen with page bodies (Default NAV, Flight Plan w/ per-leg DTK/DIS + procedure tags + in-place editing, **Flight Plan Catalog** (`_draw_fpl_catalog`), **VNAV** (`_draw_vnav_page`), an **in-screen Map** page (`_map` takes an optional `rect` so the same track-up drawing renders small inside the screen, not just beside the bezel), **WPT** identifier lookup, **NRST** nearest lists, **AUX** (`_draw_aux_page`: Trip Planning distance/ETE, Utility flight timer + GS/TAS/ALT, Setup unit/CDI-source/baro, Nav Data cycle/expiry/counts, **Weather** — `_draw_aux_weather` shows raw METAR/TAF for the flight plan's airports (`GpsNav.wx_station_idents`, outer-knob-scrolled `wx_sel`), word-wrapped (`Renderer._wrap`, pixel-measured) and read from `datasrc.wx`'s local cache via `Renderer._wx_read`, memoised ~5 s against `Scene.t` so the page isn't re-reading JSON every frame), **Charts** — `_draw_aux_charts` lists `datasrc.dtpp` plates for the same airport list, outer-knob airport / inner-knob chart (`chart_airport_sel`/`chart_sel`); the chart index is loaded once and held on the `Renderer` instance (`_dtpp_charts_for`/`self._dtpp_index`) since it covers every US airport and re-parsing it every frame would be a real cost, unlike the small per-station Weather reads; `ENT` doesn't open anything here (that's `main._open_selected_chart`'s job — real file I/O, kept out of the draw call and off gpsnav.py), active-leg marker, OBS course, `NEXT DTK`/`TURN TO` advisory, Message page, **Select Direct-To page** (`_direct_to_page` — identifier cells dim + lose the cursor once `DirectToEntry.confirming`, amber "Activate?" appears lower-right per Pilot's Guide Fig.4-3, hint line switches ENT=confirm -> ENT=activate/CLR=back), **PROC selector** (`_proc_page`), **MNU pop-up** (`_fpl_menu_page`)) + CDI strip (numeric full-scale) + FPL strip + track-up moving map (MAP-X / hold-ring / FAF-dot symbols) + HSI/DME/marker panel; **"steam"** = a large six-pack (left) + NAV1-over-NAV2 stacked at the exact six-pack gauge diameter (right; `draw_nav_head` — a classic round VOR needle via `_vor_cdi_face` (rotating card, centre CDI needle, TO/FROM triangle, OFF flag), GS diamond, right-hand info column; `draw_hsi_head` as the NAV1 `H`-key alt; ASI carries a cyan **IAS set-point bug** when managed) + full-width `draw_radio_strip` + `draw_ap_panel` (**S-TEC 55X** programmer, + `IAS SET`) beneath it. NAV heads and the radio strip both blink a `_ident_dot` in time with `radios.morse_is_keyed`, driven by a new `Scene.t` / `World.t` elapsed-seconds field. `Scene.time_warp` drives an amber `WARP nx` annunciator (top strip) whenever sim speed is above 1x. **"stack"** = `_stack_layout` — one-page IFR panel, mouse-driven, see §8. | `pygame-ce`, `navmath`, `datasrc.wx`, `datasrc.dtpp`, `pypdfium2` (stack PLATE tab only) | **done (render/main 62 tests, headless)** |
| `main.py` | `parse_args` -> `config.merge` -> `Config`; `World` (nav db + `Gns530`/`Gns430` per `--unit` + `SimModel` + optional `XPlaneFeed` + `RadioStack` + `Autopilot`; `show_msg` / `baro_inhg` UI state) with `tick(dt) -> Frame` (feed drives motion when alive, else the sim); `route_event` dispatches IFR-1 events by mode selector — **FMS1/2 remaps the AP row to GNS bezel keys** (`_FMS_BEZEL`); in COM1/COM2/NAV1/NAV2/XPDR the **KNOB press toggles a latched shift** (`World.shift_latched`, cleared on a mode-selector change; a `SHIFT <fn>` hint renders + the active radio's standby freq turns amber) that repurposes the knobs (COM1 heading bug, COM2 altimeter, NAV OBS/CRS card, XPDR mode); SWAP is flip-flop only, and in XPDR fires IDENT; AP mode outer/inner = ALT preselect / VS target, and **shift-latched inner = IAS set-point** (`World.set_ias_target` -> `sim.command(ias=)`; `,`/`.` keys too). `World._panel` feeds the tuned NAV receivers to `compute_panel` only while the CDI source is VLOC, so the FMS CDI key's toggle drives the HSI from the NAV1 course. `--approach "ICAO IDENT [TRANS]"` loads a CIFP approach at start-up and stages its frequency to VLOC standby; `World._auto_vloc` then activates it near the FAF **only when nobody is at the controls** (`gps_follow` and the autopilot off) and auto-switches the CDI GPS→VLOC once it's being received — the GNS 530W behaviour. With the autopilot flying the approach (APR/GS) the pilot must press SWAP themselves, same as the real unit, so a one-shot `TUNE VLOC ###.### (ident)` message reminds them near the FAF instead of APR/GS silently never capturing. In FMS mode the **VS key opens the PROC selector** (`_fms_bezel` "PROC" → `gns.begin_proc_select`; keyboard `R`), which then owns every FMS key until it closes, and the **ALT key opens the VNAV page** (`gns.cursor.go_to_vnav`). `route_event` also reads `Event.long_press`: `CLR`-hold → Default NAV in FMS mode, COM `SWAP`-hold → the 121.500 emergency channel (F8/F9's real trigger; keyboard `Home`/`F11` stay as a no-hardware fallback). `World.t` (elapsed session seconds, advanced in `tick`) drives the VLOC ident blink and the AUX/Utility flight timer. `--wx-auto-refresh` starts a `wx_auto.WxAutoUpdater` in `World.__init__` (stations = `gns.wx_station_idents`, region = `--wx-region` if given) and `run()` stops it on exit alongside the device/feed/GDL90 cleanup; `World._reload_cached_winds_aloft` is its `on_winds_aloft` callback, re-applying a freshly-fetched profile to `SimModel` — only when the active profile itself came from `--wx-region`/`--wx-station` (`World._wx_from_cache`), never overriding a hand-typed `--winds-aloft`. **Time warp** (`TIME_WARP_LEVELS = (1, 5, 10, 20)`, keys `1`-`4`, `--time-warp` for the starting speed): `run()`'s loop calls `w.tick(dt)` N times per rendered frame (N = the warp level) instead of once with an N-times-larger `dt`, so every dt-sensitive threshold in `sim_model`/`autopilot`/`gpsnav` sees the same real-time step it would at 1x — rendering/LEDs/nearby-airport refresh still run once per real frame off the last sub-tick's `Frame`; `Scene.time_warp` drives an amber `WARP nx` annunciator. Inert while `--xplane-feed` is connected (not special-cased — `World.tick` already ignores `dt` for kinematics whenever a feed is live). `run` owns the ~30 Hz loop, writes AP-row LEDs, feeds `GDL90Sender` when `--gdl90`. `--gdl90-discover` starts a `foreflight_discovery.ForeFlightListener`; each tick `_apply_ff_discovery` (a plain, directly testable function - no sockets/pygame needed to exercise it) retargets `GDL90Sender.addr` to whatever device is currently `.primary`, falling back to the original broadcast address once it goes stale. `route_event` intercepts ENT on the AUX Charts page (`_open_selected_chart`) before handing off to `gns.handle_event` — fetches the selected `datasrc.dtpp` plate (if not cached) and hands it to the OS's PDF viewer, on a one-shot background thread so a slow download can't stall the loop; progress/errors surface as `GpsNav.messages`, not a return value. `run` also keeps ownship magvar fresh via `wmm`. **`stack` layout**: `run()`
resizes the window (`render.STACK_W/STACK_H`) on entering/leaving it and
routes `pygame.MOUSEBUTTONDOWN` to `_on_stack_click` while it's active (see
§8); `_open_stack_plate` is the PLATE tab's off-thread fetch+rasterize.
Flags: `--config --unit --layout --xplane-feed --xplane-host --gdl90 --gdl90-host/-port --gdl90-discover --headless --time-warp --wx-auto-refresh --wx-metar-minutes --wx-taf-minutes --wx-winds-aloft-minutes` (+ the earlier plan/approach/wind/winds-aloft/wx-region/wx-station/tas/altitude/no-device/manual). `cli()` is the `octavi-trainer` entry point. | all | **done** |
| `scoring.py` | Pure session-scoring accumulator. `ScoreTracker.sample(nav, own, panel, nav_head, alt_target)` one snapshot/loop; `.summary()` → `ScoreSummary` (xtk / CDI / glideslope RMS + max, TKE RMS, needle-peg count, altitude-vs-selected RMS, a 0-100 score + A-F grade against ¾-scale ACS tolerances). Only `LEG` / `DTO` samples score. `main.World.tick` feeds it; `run` prints `.lines()` on exit. | stdlib `math` | **done (6 tests)** |
| `pyproject.toml` | setuptools packaging: `octavi-trainer = main:cli` console script, `pygame-ce` runtime dep, `device` (hidapi) + `dev` (pytest) extras, pytest config. Editable install (`pip install -e .`) is the supported mode (assets resolve via `__file__`). | setuptools | **done** |
| `tests/` | pytest. `navmath`, `airac`, `navdata` parsers get real coverage; `gpsnav` / `instruments` / `sim_model` get scenario + sign-convention tests; `wmm` is checked against NOAA's published table; `gdl90` CRC against the spec example. `faa.py`/`wx.py` network calls are mocked; CIFP tests use verbatim public-domain record lines as fixtures. | `pytest` | 846 passing (wmm 103, navmath 67, airac 67, gns530 92, ifr1 47, cifp 36, render/main 74, arinc424 35, nasr 27, instruments 26, radios 25, wx 32, sim_model 21, autopilot 19, faa 19, windsaloft 14, wx_auto 13, config 20, foreflight_discovery 13, dtpp 21, navdata_query 11, gdl90 9, xplane_feed 8, gpsnav 7, scoring 6) |

---

## 4. Conventions

- **Units:** nautical miles, knots, feet, degrees. Time in seconds internally.
- **Angles:** degrees, `0..360`. True unless a name says `_mag`. `navmath.norm360`
  / `norm180` / `angle_diff` (signed, `-180..180`) / `reciprocal` are the only
  blessed helpers — don't hand-roll modulo.
- **Cross-track sign:** `+` = ownship is **right** of the desired course, `-` =
  left. (A right deviation drives the CDI needle left / "fly left".)
- **Wind direction:** meteorological — the direction the wind blows *from*.
- **Bearings from a station:** a VOR "radial" is magnetic *from* the station;
  convert with `true = norm360(radial + station_magvar)`, `station_magvar` signed
  per the source (NASR / ARINC 424 give it explicitly).
- **Lat/lon:** decimal degrees, north/east positive. `navmath.Point(lat, lon)`.
- **Coordinates model:** spherical earth, `R = 3440.065 nm`. Good to well under a
  knot of error at GA ranges; no WGS84 ellipsoid, no terrain.
- **Magnetic variation:** station/airport magvar comes from the nav data.
  Ownship-position variation is the **WMM 2025** model (`wmm.py`, east-positive
  declination); nearest-navaid declination is the fallback if the coefficient
  file is missing. An X-Plane feed supplies its own magvar when connected.
- **AIRAC cycle ident:** ICAO `YYNN` (e.g. `2610` = 10th cycle of 2026). FAA
  d-TPP uses the same numbering. Anchor: cycle `2601` effective `2026-01-22`,
  28-day period. `datasrc/airac.py` is the only place that math lives.
- **Dates:** store as `datetime.date`; a cycle is `[effective, expiration)` where
  `expiration` == next cycle's effective.
- **No blocking I/O in the loop.** HID reads are non-blocking; UDP is threaded;
  FAA downloads happen only in the `datasrc` CLI.

---

## 5. Navigation data

### 5.1 Primary source — FAA digital products (public domain, US only)

Fetched by `datasrc/faa.py` into `data/faa/<cycle>/`, then loaded by `navdata.py`.

| Product | Gives us | Format |
|---|---|---|
| **CIFP** `FAACIFP18` | Instrument procedures (APPCH/SID/STAR), enroute airways, VHF/NDB navaids, waypoints, runway coding | one ARINC 424-18 fixed-width file |
| **28 Day NASR Subscription** | Comm frequencies (`navdata/nasr.py` merges TWR/GND/ATIS/CLNC/APP/DEP/CTAF/UNICOM onto `Airport.comms`); `APT_BASE` FAA↔ICAO crosswalk. Navaid freq/magvar cross-check still TODO. | CSV bundle (`FRQ.csv`, `APT_BASE.csv`, `NAV_BASE.csv`, `ILS_BASE.csv`, …) |
| **d-TPP** | Approach plate PDFs (`aeronav.faa.gov/d-tpp/<cycle>/<pdf_name>`, fetched individually - no bulk archive) + the chart index `d-TPP_Metafile.xml` (`.../xml_data/`, ~16 MB, per-chart `chart_code`/`chart_name`/`useraction` Added-Changed-Deleted flag/`pdf_name`) - **done 2026-09-11**, `datasrc/dtpp.py` | PDF + XML |

Both core products are US-Government public domain: we may cache, convert, and
redistribute them. Published every 28 days with explicit effective/expiration
dates. Download entry points (resolved per cycle in code):

- CIFP: <https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/>
- NASR: `…/aero_data/NASR_Subscription/<YYYY-MM-DD>/`

### 5.2 X-Plane fallback source — REMOVED (2026-09-11)

Through 2026-09-11 `navdata/xplane.py` parsed `earth_fix.dat` + `earth_nav.dat`
(VOR/NDB only — no airports, procedures, airways) into the same `NavDatabase`,
selected by `navdata.load(xplane_dir=…)` or the `XPLANE_DIR` env var, as an
offline / non-US fallback when no FAA cycle was cached.

**Removed at the user's explicit request** ("I don't want to do the X-Plane
fallback anymore... I'd rather just have us pull from the FAA data as the
only source") — deliberately dropping non-US / fully-offline coverage rather
than staying coupled to whatever nav data a local X-Plane install happens to
have (which is also Navigraph/Jeppesen-licensed and was stamped a stale
AIRAC 2406, ~15 cycles behind the FAA cycle). `navdata.load()` now has one
code path: the cached FAA CIFP, full stop — raises `FileNotFoundError` with
the exact `datasrc.faa update` command if nothing is cached. `navdata/xplane.py`
and `tests/test_xplane.py` are deleted, not archived; git history (once this
becomes a repo) is the record if it's ever needed again. This is unrelated
to `--xplane-feed` (`xplane_feed.py`, §3 module map) — that takes live
*ownship position* from a running X-Plane over UDP and was explicitly kept;
only the *nav database* fallback was in scope for removal.

### 5.3 Validity handling

`manifest.json` records the loaded cycle + effective/expiration. `navdata.py`
exposes it; `gns530.py` shows a `NAV DATA EXPIRES mm-dd-yy` line on the AUX page
and an amber annunciation once past expiration. The app still runs on expired
data (training tool) but says so, loudly.

### 5.4 Related side project (separate repo) — see WORKING.md backlog

Back-port the parsed FAA CIFP + NASR into X-Plane's `earth_*.dat` / `CIFP`
format so a *running X-Plane install* also gets current US nav data without a
Navigraph subscription. Shares `datasrc/` + the FAA parsers; different output
writer. Tracked as a backlog item, not part of the trainer's milestones.

---

## 6. Key decisions

| Decision | Why |
|---|---|
| Python + pygame-ce, single process | Fastest iteration on the state-machine work; rendering load is trivial at this fidelity. |
| Standalone motion model, X-Plane feed optional | Practice without launching a sim; still usable as a better 530 panel when the sim runs. |
| **FAA CIFP + NASR as the sole nav data source, US-only** | Authoritative, public-domain, AIRAC-current, free, redistributable. X-Plane's bundled data is stale (2406) and Navigraph-licensed — and, as of 2026-09-11, deliberately not a fallback either: the user didn't want the trainer coupled to whatever nav data a local X-Plane install happens to have, even as a last resort. Non-US / fully-offline coverage is a known, accepted gap rather than something worth that coupling. |
| **Data fetch is a separate CLI (`datasrc`), cached, with a manifest** | Keeps the network out of the runtime loop; gives a real validity window; makes updates explicit and reproducible. |
| `navmath` / `airac` are pure & dependency-free | Tricky math stays unit-testable in isolation; everything builds on them. |
| Spherical earth | Sub-knot error at these ranges; keeps the math readable. |
| IFR-1 byte offsets from 3rd-party script, kept behind `ifr1.py` + explorer | Single place to correct if on-hardware bytes differ. |
| **GPS state machine is one core (`gpsnav.py`), 530/430 are view configs** | The avionics logic is identical; only screen size / row count differ. Avoids a fork. |
| **Instrument faces drawn vector-native; only MIT/OFL assets vendored** | Keeps the repo permissive (see sec.7). We already vector-draw the HSI; CDI/AI/AP are the same kind of work. |
| Autopilot is a pure state machine emitting `SimModel` commands | Same test discipline as the rest; the AP can later drive an X-Plane feed instead. |
| **X-Plane feed swaps the motion source, doesn't replace it** | `World.tick` picks `feed.state` when the feed is alive, else the sim; the sim is kept synced to the last live position so a UDP dropout is seamless, not a teleport. |
| **WMM 2025 for ownship magvar; coefficient file vendored** | The `.COF` is public-domain U.S. Government data; the synthesis is NOAA's published method. Removes the "nearest VOR declination" approximation for the aircraft's own position. Nearest-navaid stays as the no-file fallback. |
| **GDL90 output is opt-in** | X-Plane already broadcasts GDL90 natively; this module matters only for the standalone `sim_model`. Off by default to avoid duplicate traffic. |
| **ForeFlight discovery retargets GDL90, doesn't replace broadcast** | `--gdl90-discover` upgrades from broadcast to direct unicast once a tablet answers, and degrades back to broadcast the moment it stops - never leaves the pilot silently un-tracked because a tablet's Wi-Fi napped. |

## 7. Instrument art / open-source assets

Priorities: keep the repo **permissive (MIT/OFL)**, keep GPU cost trivial, stay
hackable. So faces are **drawn as vectors** in `render.py` (circles, ticks,
needles, a moving diamond) rather than blitted from textures. Assets we *may*
vendor, all under `assets/`:

Every vendored asset is logged in **`assets/instruments/PROVENANCE.md`** (source
URL, author, licence, pull date, how it's used, how to swap it) so any of it can
be replaced cleanly.

| Need | Source | License | Status |
|---|---|---|---|
| Cockpit label / data font | **B612** & **B612 Mono** (Airbus + ENAC) | **OFL-1.1** | **bundled** `assets/fonts/`; `render.py` uses it for all text, Consolas fallback |
| Digital 7-/14-segment readouts (freqs, squawk, altitude, heading) | **DSEG7 / DSEG14 Classic** (keshikan) | **OFL-1.1** | **bundled** `assets/fonts/`; `Renderer.lcd()` — digits / `.` / `:` / `-` only |
| **GNS 530 faceplate** (bezel outline, button/knob cutouts, engraved labels) | `allanglen/c172-flight-sim-panel` `parts/avionics/garmin-gns-530/faceplate.svg` | **Apache-2.0** | **bundled** `assets/instruments/garmin-gns-530/`; `render._load_bezel` recolours the laser-cut red to grey and draws it as the GPS-layout bezel; hand-drawn panel is the fallback |
| **GNS 430 faceplate** (shorter unit — same width, ~half height) | `allanglen/c172-flight-sim-panel` `parts/avionics/garmin-gns-430/faceplate.svg` | **Apache-2.0** | **bundled** `assets/instruments/garmin-gns-430/`; used when `--unit 430`, same `_load_bezel` path; `gpsnav.VARIANT_430.screen_frac` measured off a raster |
| Six-pack faces (ASI, AI, ALT, TC, HI, VSI) | `sebmatton/jQuery-Flight-Indicators` (+ ports) | **GPL-3.0** *(README says MIT; the LICENSE file is GPLv3 — LICENSE wins)* | **not vendored** — gauges drawn as vectors; provenance noted for a possible later swap |
| CDI / HSI / RMI faces; S-TEC / King bezels; radio faces | **FlightGear** (`Aircraft/Instruments-3d`) | **GPL-2.0** | visual reference only |

pygame-ce 2.5.3 loads SVG natively (`pygame.image.load_sized_svg`), so a raster
or vector face set drops in through `_load_bezel` / a sibling loader.

---

## 8. `stack` layout — one-page IFR panel

Built 2026-09-17 (see `WORKING.md`'s Done log). A fourth layout
(`--layout stack`, mockup in `docs/mockups/stack-layout-mockup.png`; `L`
cycles `gps → steam → stack → gps`, `+ dual` as a 4th stop under `--dual`)
putting everything relevant to instrument flight on one screen instead of
paging between GNS pages and separate reference material. Window resizes to
`render.STACK_W, STACK_H` (1360×860) only while it's active; the other three
layouts stay at `WIN_W, WIN_H` (1000×640). `Renderer._stack_layout` draws
three columns:

- **Left** — a tabbed reference panel, independent of the GNS units
  (`Renderer._stack_tabs` + `_stack_tab_content`): WX = METAR/TAF for any of
  the flight plan's airports, click an ident in the strip to switch station
  (reuses `_draw_aux_weather`, which now also registers a `wx:airport:N` hit
  rect per ident - the strip existed before but had no way to drive it
  without a bezel outer knob), MAP = the existing moving map (`_map`), PLATE
  = the selected approach plate rendered **inline** as a rasterized PDF, with
  its own clickable airport strip (`plate:airport:N`) and, when an airport
  has more than one cached plate, a filter-chip row (`plate:filter:CODE`,
  one per distinct `chart_code` present plus `ALL`) over a scrollable list of
  full chart names (`plate:chart:N`, 4-row window, same technique as
  `_draw_nrst_page`) rather than a bare row of `chart_code`s, which was both
  meaningless (every approach chart reads "IAP") and unbounded for a busy
  airport - `_draw_stack_plate`, SETTINGS = in-flight-adjustable trainer
  options (wind, time-warp; `_draw_stack_settings`). Below it, HDG/IAS/ALT
  autopilot **set-point** bugs (`_stack_setpoint_bugs`), shown *and* directly
  editable (click `+`/`-`) - the corresponding *actual* values are shown
  next to the HDG dial instead (see Middle), not here, so this row is
  unambiguously "what you're telling the autopilot". A rendered plate image
  (`_draw_stack_plate`) sizes itself off the tab's actual rect, so it grows
  with the column rather than a hardcoded size.
- **Middle** — NAV1 and NAV2 as Bendix/King-style round CDI heads
  (`draw_nav_head`, reused unchanged from `steam`), plus a standalone heading
  indicator with a heading bug (`draw_hdg_indicator`, sharing its dial-drawing
  with `draw_six_pack`'s HDG cell via the extracted `_hdg_card` helper rather
  than duplicating it; left-biased via `_card_geometry`, the same call
  `draw_nav_head` and `_stack_layout`'s own info-column placement use - it
  used to draw centered while the info column beside it assumed a
  left-biased position, so the two overlapped). Fixed 380px width
  (`_stack_layout`'s `mid_w`, tuned down from an initial 440px - still not
  window-width-derived), leaving NAV1/NAV2 a small margin past their info
  column and the HDG box's own info column (next) enough room -
  `draw_nav_head` left-biases its round card, so past this the box is just
  dead panel background; the left tab column gets whatever the window leaves
  over instead. Actual HDG/IAS/ALT (read-only, from `Scene.sixpack` - the
  same snapshot `draw_six_pack` itself reads) are shown in
  `Renderer._stack_hdg_actuals`, a column to the right of the HDG dial at the
  same x `_card_geometry` gives NAV1/NAV2's own OBS info column - not the
  editable set points, which live under the tab column instead (see Left).
- **Right** — GNS 530 + GNS 430 (when `--dual`) + transponder + S-TEC 55X
  autopilot, stacked as one visually continuous column. The GNS units drop
  the photorealistic faceplate SVG bezel here (`_gns_unit(..., no_bezel=True)`
  forces the existing flat-panel fallback style, matching the transponder/AP
  boxes) and gain inline COM/NAV active+standby frequency annunciation
  (`_gns_unit(..., freq=(com, nav, label))`) the way the real unit's screen
  shows it — scoped to `stack` only for now, not the other layouts. Full GNS
  page navigation (Default NAV/Flight Plan/DTO/PROC/MSG/etc.) is unchanged,
  same bezel-key routing as `gps`/`dual`, just restyled. A compact
  `_stack_xpdr` box replaces the full `draw_radio_strip` (which doesn't fit
  the column's height budget) since COM/NAV are now on the GNS screens
  themselves. `draw_ap_panel(..., show_info=False)` drops its own side
  HDG BUG/ALT SEL info box (shown/edited elsewhere now, see Left/Middle) and
  wraps its VS-window readout onto its own line when it doesn't fit beside
  the mode-button row at this column's width, rather than overflowing past
  the box's own edge (`steam`'s call is unaffected - `show_info=True` is the
  default, and its much wider `ap_rect` never triggers the wrap).

**New capabilities, both scoped to `stack` only:**
- **Mouse input.** The only layout with any mouse surface. `_stack_layout`
  and its helpers rebuild `Renderer._stack_hit` (rect name → `pygame.Rect`)
  every frame; `main.run()`'s event loop calls `main._on_stack_click` on
  `pygame.MOUSEBUTTONDOWN` only while `stack` is active, which hit-tests the
  click and dispatches by name (`tab:*`, `warp:*`, `wind_dir:±`, `wind_kt:±`,
  `bug:hdg/ias/alt:±`, `wx:airport:N`, `plate:airport:N`, `plate:chart:N`,
  `plate:filter:CODE`, `plate:load`). Click-only — no `MOUSEWHEEL` handling
  yet (backlog in `WORKING.md`).
- **PDF rasterization**, via a new dependency: **`pypdfium2`** (small,
  MIT-licensed, pure-wheel — the dev machine has no system `poppler`, so a
  `pdftoppm`-shelling approach wasn't an option). Used only by the PLATE tab.
  `main._open_stack_plate` fetches (via the existing
  `datasrc.dtpp.fetch_and_cache_chart` — unchanged) and rasterizes page 1
  off-thread into `Renderer._plate_cache` (keyed by pdf filename, alongside
  `_plate_loading`/`_plate_error`); `Renderer._draw_stack_plate` only ever
  reads that cache, never blocks on I/O in a draw call — same
  fetch-off-thread convention as `main._open_selected_chart`'s OS-viewer
  hand-off for the other layouts' AUX>Charts page.

**Scoped down from the original design** (see `WORKING.md`'s backlog): the
SETTINGS wind editor adjusts the single uniform `--wind` value
(`SimModel.set_wind`) rather than a full per-altitude winds-aloft profile.
