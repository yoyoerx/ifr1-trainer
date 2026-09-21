# octavi-ifr-trainer

Lightweight IFR procedures trainer driven by the **Octavi IFR-1**. Simulates a
Garmin GNS 530 / GNS 430 and the associated nav instruments (CDI/HSI, bearing pointer,
DME, marker beacons) with cheap 2D rendering. Runs standalone with a basic
scripted flight model; optionally consumes live aircraft position from X-Plane
when the sim is running.

Not affiliated with Octavi, Garmin, or Laminar Research. For training practice,
not real-world navigation.

## Status

See `WORKING.md` for the detailed task tracker and `ARCHITECTURE.md` for the map.
`FINDINGS.md` records the GNS 530 validation against the Garmin Pilot's Guide.
860 tests passing. The trainer runs:

```
python main.py --plan "KBOS PVD KJFK" --wind 300/25       # 530 + moving map
python main.py --approach "KLNS I08"                      # CIFP approach: synth legs, SUSP at the MAP, ILS staged to VLOC standby
python main.py --unit 430                                 # GNS 430 (shorter unit, 5-row screen)
python main.py --layout steam                             # six-pack + CDIs + autopilot
python main.py --layout stack                             # one-page IFR panel, mouse-driven (see below)
python main.py --dual                                     # a 530 on FMS1 + a 430 on FMS2, stacked
python main.py --no-device                                # hand-fly, keyboard only
python main.py --xplane-feed                              # take ownship position from X-Plane
python main.py --gdl90 --gdl90-host 192.168.1.255         # feed a tablet EFB (ForeFlight etc.)
```

`L` switches layout, `H` toggles NAV1 needle<->HSI, `A`/`F1..F5` work the autopilot.
`S` suspend, `V` CDI source, `B` OBS (`-`/`=` course), `M` messages,
`Home` Default NAV, `D` opens the Select Direct-To page (arrows edit, Enter activates),
`R` opens the PROC selector (approach / arrival / departure, arrows + Enter, CLR backs out),
`,`/`.` trim the IAS set-point.

With `--dual`, the IFR-1's FMS1/FMS2 mode-selector positions drive two
independent GNS units - a real dual 530/430 stack - each with its own flight
plan, cursor and nav state; both start on the same plan, then diverge as you
page/fly them separately. `--unit` picks FMS1's unit, `--dual` puts the other
one on FMS2 (`--unit 530 --dual` -> 530/430; `--unit 430 --dual` -> 430/530).
Without the IFR-1, `U` toggles which unit the keyboard's GNS-page keys drive
(an annunciator shows `KBD FMS2` while it's the second unit); `L` also cycles
through a fourth layout, `dual`, that draws both units stacked on the left
with the moving map + HSI (tied to FMS1) on the right.

**`--layout stack`** puts everything relevant to instrument flight on one
page: GNS 530 + GNS 430 (flat vector skin, COM/NAV frequencies annunciated
inline, same bezel-key/knob input as the other layouts) + transponder + the
S-TEC 55X programmer in a right-hand column; NAV1/NAV2 as round
Bendix/King-style CDI+GS+OBS heads plus a standalone heading indicator in the
middle; and a tabbed WX/MAP/PLATE/SETTINGS reference panel with clickable
HDG/IAS/ALT autopilot-bug boxes on the left. It's the only layout with mouse
support - click a tab to switch it, click a bug box's `+`/`-` to edit it,
click an airport ident to switch which flight-plan station WX/PLATE shows.
Single-unit, a COM2/NAV2 panel (flip-flop + standby tuning buttons) sits above the transponder;
with `--dual`, FMS2 drives NAV2 the way FMS1 drives NAV1 instead.
The PLATE tab rasterizes the selected AUX>Charts plate inline (`pypdfium2`,
click-switchable between airports and, when more than one is cached, charts)
instead of handing the PDF to the OS viewer; SETTINGS exposes wind and
time-warp for changing in flight. The window resizes larger while `stack` is
active and back down when you leave it.

Any long flag can go in a config file instead (`octavi.toml` / `octavi.json` in
the working dir or `~/.config/octavi-ifr-trainer/`, or `--config PATH`); the
command line overrides it. Example:

```toml
# octavi.toml
plan = "KBOS PVD KJFK"
unit = "430"
layout = "steam"
xplane_feed = true
```

Bundled assets: **B612 Mono** + **DSEG7 Classic** fonts (`assets/fonts/`, SIL
OFL); **GNS 530 + GNS 430 faceplate SVGs** (`assets/instruments/`, Apache-2.0,
from [allanglen/c172-flight-sim-panel](https://github.com/allanglen/c172-flight-sim-panel));
and the **WMM 2025 coefficients** (`assets/wmm/WMM2025.COF`, NOAA/NGA public
domain) for ownship magnetic variation. Each origin + licence is logged in the
`PROVENANCE.md` alongside it.

- [x] `ifr1.py` - HID interface + protocol **confirmed on hardware**
- [x] `navmath.py` - great-circle, XTK, radial/DME, turn anticipation, holds
- [x] `datasrc/` - AIRAC cycle math + FAA CIFP/NASR fetch, cache, validity
- [x] `navdata/` - CIFP (navaids/waypoints/airports/runways/procedures/airways)
      + NASR comm-freq merge + nearest-N (FAA data is the sole source)
- [x] `gpsnav.py` - variant-independent GPS core: flight plan (editable in
      flight) / Direct-To / sequencing / OBS / phase-of-flight CDI scale /
      `NEXT DTK`-`TURN TO` / CDI source / expiry + message queue;
      `load_procedure` synthesises every non-fix ARINC leg + auto-SUSP at the
      MAP; **holds are actually flown** (AIM entry + one circuit, auto-continue
      for a single-circuit HILPT) rather than just suspended; WPT & NRST pages
      drive a Direct-To; the **PROC selector**
      (`ProcSelect`) loads an approach/arrival/departure + transition on-screen.
      `update()` -> `NavState` (validated vs the Pilot's Guide - see `FINDINGS.md`)
- [x] `scoring.py` - grades the flying (xtk / CDI / glideslope RMS, needle-peg
      count, TKE, altitude) and prints an ACS-style letter grade on exit
- [x] `gns530.py` / `gns430.py` - thin `Variant` views over `gpsnav.GpsNav`
      (screen height / row count / faceplate only; logic does not fork)
- [x] `instruments.py` - CDI/HSI, bearing pointers, DME, markers, glideslope, six-pack
- [x] `sim_model.py` - kinematic ownship: cmd hdg/alt/spd/vs or leg intercept
- [x] `radios.py` - COM/NAV stack, per-nav OBS, localizer+runway pairing, XPDR
- [x] `autopilot.py` - S-TEC Fifty Five X style AP (HDG/NAV/APR/REV, GPSS, VS/ALT/GS)
- [x] `render.py` - "gps" layout (530 + map), "steam" layout (six-pack + CDIs
      + AP), "dual" layout (two stacked GNS units + map, for `--dual`), and
      "stack" layout (one-page IFR panel, mouse-driven - see above)
- [x] `main.py` - ~30 Hz loop; IFR-1 mode selector routing (FMS AP-row -> bezel
      keys, knob-latched per-mode shift, AP-mode ALT/VS knobs); AP-row LEDs follow state
- [x] GNS 430 variant - `gpsnav.py` core split, `--unit 430`, own faceplate SVG
- [x] `--dual` - a second, independent GNS unit driven by the IFR-1's FMS2
      selector position (a real 530/430 stack); `"dual"` layout stacks both
      on screen, keyboard `U` swaps which unit the GNS-page keys drive
- [x] `xplane_feed.py` - optional live ownship from X-Plane over UDP (`RREF`),
      seamless fallback to the scripted model
- [x] `gdl90_out.py` - GDL90 broadcaster (`--gdl90`) for a tablet EFB
- [x] `config.py` - `octavi.toml` / `octavi.json`, merged under the CLI
- [x] `wmm.py` - WMM 2025 declination for ownship magvar (matches NOAA's table)
- [x] `pyproject.toml` - `pip install -e .` gives the `octavi-trainer` command

## Quick start

```
git clone https://github.com/yoyoerx/ifr1-trainer.git
cd ifr1-trainer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python ifr1.py raw      # diagnostic: raw frames + which byte each control moves
python ifr1.py          # decoded view using the current LAYOUT
```

If the explorer says "No IFR-1 found" but it is plugged in, another process is
holding it open. Close X-Plane (the bundled Octavi `win.xpl` plugin grabs the
device), MobiFlight, or the Octavi config app.

## IFR-1 HID protocol

**Confirmed on real hardware** (IFR-1, Windows, 2026-09-09) — every control
decodes correctly with the layout below. hidapi keeps the report-id byte;
`product='IFR1'`, HID interface 2, usage 1/5. Diagnose any future unit with
`python ifr1.py raw`. Reference: <https://github.com/acehoss/IFR1-FlyWithLua>.

| Property      | Value                                                        |
|---------------|-------------------------------------------------------------|
| VID / PID     | `0x04D8` / `0xE6D6`                                          |
| Input report  | ID `0x0B`, pushed on any change                             |
| Output report | ID `0x0B`, 1 payload byte = AP-row LED bitmask              |

**Canonical frame** — `ifr1.py` always keeps the report-id byte at index 0
(synthesising it if a hidapi backend stripped it), so every offset below is
relative to that byte:

| Byte | Meaning                                                             |
|------|-------------------------------------------------------------------|
| 0    | report id `0x0B`                                                   |
| 1    | buttons A: `DCT 0x10`  `MNU 0x20`  `CLR 0x40`  `ENT 0x80`          |
| 2    | buttons B: `SWAP 0x01`  `KNOB 0x02`  `AP 0x40`  `HDG 0x80`         |
| 3    | buttons C: `NAV 0x01`  `APR 0x02`  `ALT 0x04`  `VS 0x08`           |
| 4    | reserved — stayed `00` for every control (the unit has 12 buttons); non-zero is surfaced as `UNKNOWN` |
| 5    | outer knob: signed delta since last report (-128..127)            |
| 6    | inner knob: signed delta since last report                        |
| 7    | mode selector: `0 COM1 1 COM2 2 NAV1 3 NAV2 4 FMS1 5 FMS2 6 AP 7 XPDR` |

> An earlier version parsed a *stripped* frame with these same offsets, i.e. it
> read the encoders/mode one byte too far (mode stuck on COM1). Fixed 2026-09-09;
> the decode logic + 23 captured frames are pinned by `tests/test_ifr1.py`.

The **KNOB press** toggles a latched "shift" in the modes that have a secondary
axis (COM1 heading, COM2 baro, NAV1/NAV2 course, XPDR mode); it clears on a
mode-selector change. This is how one dual encoder covers ~12 functions. (An
earlier build used *held* SWAP for this; that was removed - SWAP is now the
flip-flop / IDENT key only.)

LED bitmask (output): `bit0 AP  bit1 HDG  bit2 NAV  bit3 APR  bit4 ALT  bit5 VS`.

### Mapping to the GNS 530

The IFR-1 was built around this interaction model, so FMS mode is close to 1:1:

| IFR-1 (FMS1/FMS2 mode) | GNS 530 bezel                          |
|------------------------|----------------------------------------|
| outer knob             | large (outer) knob - page group / cursor field (stops at the ends, no wrap) |
| inner knob             | small (inner) knob - page / character (no wrap) |
| KNOB push              | CRSR                                    |
| DCT                    | opens the **Select Direct-To** page: big knob = cursor, small knob = character, ENT resolves + activates, CLR cancels |
| MNU                    | MENU                                    |
| CLR                    | CLR (idle press = Default NAV)          |
| ENT                    | ENT                                     |
| SWAP                   | CDI source toggle (GPS <-> VLOC)        |
| **AP row → bezel keys**: AP=CDI, HDG=OBS, NAV=MSG, APR=FPL, ALT=VNAV, VS=PROC ||
| VS (PROC)              | opens the **PROC selector**: menu (Select Approach / Arrival / Departure, plus Activate Approach / Vectors-To-Final once one is loaded) → procedure list → transition list (incl. VECTORS) → loads it and jumps to the Flight Plan page. Knob scrolls, ENT confirms, CLR steps back / closes. |

In the other modes the mode selector routes the knobs and the AP row drives the
autopilot. In COM1/COM2/NAV1/NAV2/XPDR the **KNOB press toggles a latched
"shift"** — it stays on until you press the knob again or move the mode
selector, and a `SHIFT …` hint shows what it does:

| Mode | knobs (normal) | SWAP | shift latched |
|---|---|---|---|
| COM1 | outer MHz, inner kHz | flip active/standby | knob → heading bug |
| COM2 | outer MHz, inner kHz | flip active/standby | knob → altimeter setting |
| NAV1 / NAV2 | outer MHz, inner kHz | flip active/standby | knob turns the OBS / CRS card |
| XPDR | inner = digit value, outer = digit select (underlined) | **IDENT** pulse | knob → transponder mode |
| AP | outer = altitude select (100 ft), inner = vertical speed (100 fpm) | — | inner → **IAS set-point** (5 kt) |

The **IAS set-point** is a pseudo speed manager (there is no throttle model): it
holds an indicated airspeed and the sim converts it to TAS for the current
altitude, so a constant-IAS climb reads rising TAS / groundspeed. Keyboard
`,` / `.` trim the same set-point.

## Keyboard reference

The keyboard works alongside or instead of the IFR-1 (`--no-device` needs no
hardware at all). This is the full list, straight from `main._on_key`.

**Global** (work anytime):

| Key | Action |
|---|---|
| `Esc` | Quit |
| `←` / `→` | Manual heading −5° / +5° |
| `↑` / `↓` | Target altitude +500 / −500 ft |
| `,` / `.` | IAS set-point −5 / +5 kt |
| `1` `2` `3` `4` | Time warp: 1x / 5x / 10x / 20x |
| `L` | Cycle layout: gps → steam → stack → gps (→ `dual` too, with `--dual`) |
| `H` | NAV1 CDI ↔ HSI face |

**GNS 530 pages & flight plan:**

| Key | Action |
|---|---|
| `PgUp` / `PgDn` | Page within the current group (small knob) |
| `Shift+PgUp` / `Shift+PgDn` | Page **group**: NAV → WPT → AUX → NRST (large knob) — the only keyboard route to WPT, AUX (incl. Weather), and NRST |
| `Tab` | CRSR (cursor on/off) |
| `D` | Open the Direct-To entry page |
| `C` | CLR — cancels an active Direct-To (resumes the nearest flight-plan leg), deletes the selected Flight Plan/Catalog row, or backs out to Default NAV |
| `R` | Open PROC (approach/SID/STAR selector) |
| `X` | MNU — opens the Flight Plan / Flight Plan Catalog page menu (Invert/Copy/Sort/Delete); Up/Down/Enter/Esc drive it once open |
| `[` / `]` | Map range out / in |
| `Home` | Jump to Default NAV page |
| `S` | Suspend (SUSP) |
| `V` | CDI source (GPS ↔ VLOC) |
| `B` | OBS on/off |
| `-` / `=` | OBS course −1 / +1 (hold `Shift` for ×10) |
| `M` | Messages page |
| `U` | *(`--dual` only)* Swap which unit — FMS1 or FMS2 — every key in this table drives; shows a `KBD FMS2` annunciator while it's the second unit |

**Radios & autopilot:**

| Key | Action |
|---|---|
| `O` / `Shift+O` | NAV1 OBS −1 / +1 |
| `K` / `P` | NAV2 OBS −1 / +1 |
| `F11` | COM1 emergency (121.5) |
| `A` | AP master |
| `F1` `F2` `F3` `F4` `F5` `F6` | AP: HDG / NAV / APR / REV / ALT / VS |
| `G` | GPSS toggle |
| `9` / `0` | AP VS knob −1 / +1 |

**Autopilot NAV and the course pointer (`--ap-course`).** Like the real S-TEC 55X + GNS 530, **NAV on a
GPS leg flies the course selected on the HSI** (S-TEC POH sec.3.1.2) against the needle, so you must keep
the pointer on the leg's course - turn it with the NAV1 CRS knob (`O` / `Shift+O`) - and the 530 posts
**"Set course to ###°"** when it is more than 10° off (Pilot's Guide p.175). Leave it off and the
aircraft settles parallel to, not on, the leg. **GPSS** (press NAV a second time / `G`) flies the GPS and
ignores the pointer (POH sec.3.1.3). The default is `--ap-course manual`; `--ap-course auto` slaves the
pointer to the desired track (use it for unattended scripted runs, which would otherwise wander at every
turn). Config key `ap_course`. The same applies to a localizer: NAV APR flies **your** course card, so set
it to the front course (POH sec.3.3.3) - the needle itself is always the beam.

**Autopilot states.** Master (`A`) brings up **RDY** and nothing steers; `F1`/`F2`/`F3`/`F4` (HDG/NAV/APR/REV)
engage a roll mode; ALT/VS (`F5`/`F6`) are ignored until one is engaged (S-TEC POH sec.3.1.4/3.1.5). Pressing
the engaged mode's button again releases it back to RDY. NAV APR replaces GPSS.

**Glideslope.** Pressing APR on an ILS arms only the localizer. With **ALT engaged** (S-TEC POH sec.3.2.1.1) the GS
arms itself once you are within 50% of the localizer and more than 10% below the beam, and engages at 5% GDI. APR
while it is armed disarms it (GS flashes) and APR again re-arms it; ALT with the beam available engages it
immediately. NAV, APR, REV and GS blink beyond 50% needle deflection or on a flag.

**Knobs and disconnect.** The AP-mode inner knob (or `9`/`0`) is the S-TEC modifier knob: **ALT** moves the held altitude
20 ft per click (+/-360 ft from where it was captured), **VS** 100 fpm per click (+/-1600 fpm from the rate that VS
captured, 1600 max). VS captures the rate you are flying when you press it. With a roll mode engaged the AP key
disconnects to a flashing RDY (5 s); from RDY it switches the unit off.

**While a modal page is open** (these keys are captured, overriding the tables above):

*Direct-To entry page* (opened by `D` or the IFR-1 DCT key):

| Key | Action |
|---|---|
| `←` / `→` | Move identifier cursor |
| `↑` / `↓` | Scroll character at cursor |
| `Enter` | Confirm ident → "Activate?" → confirm again to activate |
| `Esc` / `Backspace` | Back out one step (editing → close) |

*PROC selector* (opened by `R`):

| Key | Action |
|---|---|
| `↑`/`←`, `↓`/`→` | Move selection |
| `Enter` | Confirm / advance |
| `Esc` / `Backspace` | Back out |

One known gap: COM/NAV/XPDR standby-frequency tuning is IFR-1-only — it has
no keyboard fallback today.

## Time warp

Real time isn't always the interesting part. Keys `1`/`2`/`3`/`4` set the
simulation speed to 1x/5x/10x/20x at any point while running (`--time-warp`
picks the starting speed: `python main.py --time-warp 5`). An amber
`WARP 5x`-style annunciator appears in the top strip whenever it's above 1x,
so it's never silently running fast.

Under the hood this runs several nav/physics ticks per rendered frame, each
with the *same* real-time step it would use at 1x — not one tick with an
inflated one — so altitude/glideslope capture, waypoint sequencing, and turn
anticipation stay exactly as fine-grained as at 1x; only the picture updates
less often per second of flight. Rendering, the IFR-1/keyboard, and the score
tracker all still run at the normal ~30 Hz. Warp has no effect while
`--xplane-feed` is connected — X-Plane's own clock is real-world and can't be
sped up from here, same as it would be in the aircraft.

## Nav data

**The FAA's free public-domain digital products are the only source** (US
only), refreshed on the 28-day AIRAC cycle — nothing here reads from a local
X-Plane install:

* **CIFP** (`FAACIFP18`, one ARINC 424-18 file) — instrument procedures, enroute
  airways, VHF/NDB navaids, waypoints, runways.
* **28-Day NASR Subscription** (CSV bundle) — comm frequencies merged onto
  airports, FAA↔ICAO crosswalk.

`python -m datasrc.faa update` fetches + caches them under `data/faa/<cycle>/`
with a `manifest.json`; `navdata.load()` parses them and stamps the validity
window the trainer displays and annunciates. No network in the runtime loop.
The parsed database is pickle-cached alongside the raw file (`navdb-*.pkl`), so
subsequent loads are ~0.7 s instead of ~2 s. `navdata.load()` raises
`FileNotFoundError` with the exact fix-it command if nothing is cached yet.

(An X-Plane `earth_*.dat` offline fallback existed through 2026-09-11 and was
deliberately removed — the trainer no longer couples to whatever nav data
happens to be sitting in a local X-Plane install; FAA CIFP/NASR is the only
source now, full stop. `--xplane-feed`, below, is unrelated — that's live
*ownship position* from a running sim, not nav data.)

See `ARCHITECTURE.md` §5 for the full sourcing story.

## Winds aloft & weather (optional)

Wind used to be a single uniform DIR/SPD for the whole flight. It's now a
multi-altitude profile the sim interpolates against the ownship's altitude,
with an optional temperature that actually changes TAS for a held IAS:

```
python main.py --winds-aloft "3000:280/20/-05 9000:300/35/-15 18000:310/55/-30"
```

`ALT:DIR/SPD[/TEMPC]` — altitude ft MSL, direction true degrees, speed knots,
temperature (optional) Celsius. `--wind DIR/SPD` still works as the uniform
fallback when `--winds-aloft` is not given.

For a live forecast instead of hand-typed numbers, `datasrc/wx.py` fetches +
caches FAA/NWS aviation weather (METAR, TAF, and the winds/temps-aloft "FD"
product) the same way `datasrc/faa.py` fetches nav data — a separate, cached,
offline CLI; nothing here runs inside the trainer loop:

```
python -m datasrc.wx metar KLNS KJFK        # current METARs
python -m datasrc.wx taf KLNS               # current TAF
python -m datasrc.wx winds-aloft BOS        # NWS FD region forecast
python -m datasrc.wx status                 # what's cached, and how stale

python main.py --wx-region BOS --wx-station BOS   # fly the cached forecast
```

`--wx-region`/`--wx-station` read whatever the most recent `winds-aloft` fetch
cached (`data/wx/windtemp/<REGION>_latest.json`) — a one-off manual fetch
doesn't auto-refresh (see `--wx-auto-refresh` below for that). `winds-aloft`
takes one of AWC's 6 CONUS FD **regions** — `BOS MIA CHI DFW SLC SFO` — not a
station/airport ident (`KBWI`, `BWI`, etc. will 400); `--wx-station` is then
any station **within** that region's forecast table (e.g. `BOS`'s table also
covers `JFK`, `ALB`, `BUF`, ...).

Once `metar`/`taf` have been fetched, the GNS **AUX group has a Weather page**
that shows the raw METAR and TAF for the flight plan's airports — departure,
enroute stops, destination — one station at a time; press the KNOB (CRSR) and
turn the outer knob to scroll between stations. Reaching AUX needs the large
(outer) knob — on the keyboard that's `Shift+PgUp`/`Shift+PgDn` (plain
`PgUp`/`PgDn` is the small knob, page-within-group). It reads straight from
`datasrc/wx.py`'s local cache, so fetch first:

```
python -m datasrc.wx metar KBOS KJFK
python -m datasrc.wx taf KBOS KJFK
python main.py --plan "KBOS ... KJFK"   # then Shift+PgUp to AUX, PgDn to Weather
```

The page shows flight-plan *airports* only (no substitute station for a plain
enroute fix).

### Keeping it live: `--wx-auto-refresh`

By default weather is a manual, one-off fetch — the trainer never talks to
the network on its own, same as the AIRAC nav-data cache. Add
`--wx-auto-refresh` to change that: it starts a background thread that keeps
re-fetching METAR/TAF for whatever airports are currently in the flight plan
(and, with `--wx-region`, the winds-aloft forecast too), each on its own
schedule, and writes to the same local cache the Weather page and
`--wx-region`/`--wx-station` already read — so nothing else needs to change,
the numbers just get newer on their own:

```
python main.py --plan "KBOS ... KJFK" --wx-auto-refresh
python main.py --plan "KBOS ... KJFK" --wx-region BOS --wx-station BOS --wx-auto-refresh
```

Default intervals (override with `--wx-metar-minutes` / `--wx-taf-minutes` /
`--wx-winds-aloft-minutes`):

| Product | Default | Why |
|---|---|---|
| METAR | 20 min | frequent enough to catch a SPECI reasonably promptly, without hammering a free public API |
| TAF | 60 min | TAFs are issued ~4x/day plus occasional amendments; hourly is generous |
| Winds/temps aloft (FD) | 60 min | also issued ~4x/day for a 6/12/24 h window |

It fetches once immediately on start (so the Weather page isn't empty for
the first 20 minutes), then on schedule; a fetch is retried on the intervals
above if it fails (no network, station not found, etc.) rather than giving
up, and any failure is silent past the console — check
`python -m datasrc.wx status` if numbers seem stale. The background thread
is stopped cleanly on exit and never blocks the ~30 Hz render/physics loop —
all the actual HTTP work happens off the main thread. If the active
winds-aloft profile came from `--wx-region`/`--wx-station` (not a hand-typed
`--winds-aloft`), a fresh fetch is applied to the sim automatically too.

## X-Plane data back-port (optional, `faa2xp`)

X-Plane's bundled nav data is AIRAC 2406. `faa2xp` refreshes the US portion from
the cached FAA CIFP (after `python -m datasrc.faa update`):

```
python -m faa2xp build   --xplane "E:/SteamLibrary/steamapps/common/X-Plane 12"
python -m faa2xp install --xplane "..."          # dry run; add --yes to apply
python -m faa2xp status  --xplane "..."
python -m faa2xp restore --xplane "..." --yes    # exact undo (originals put back)
```

It writes only into `Custom Data/` (`earth_nav/fix/awy/hold.dat` merged over the
default files, plus a copy of the default `CIFP/` and `FAACIFP18`, which X-Plane overrides US
procedures from) and never
touches `Resources/default data`. Unofficial; not for real-world navigation.

## Approach plates (d-TPP)

Real charts, not a redrawn approximation — FAA d-TPP (digital Terminal
Procedures Publication) is a PDF per chart (approach, SID, STAR, airport
diagram, takeoff/alternate minimums). The GNS **AUX group has a Charts
page**: outer knob picks an airport (from the flight plan, same list as the
Weather page), inner knob scrolls that airport's charts, `ENT` fetches the
selected one (if not already cached) and hands it to your OS's default PDF
viewer — no PDF rendering happens inside the trainer itself, so you get a
real viewer's pan/zoom/search instead of a reimplementation.

```
python -m datasrc.dtpp update-index         # fetch the chart index (~16 MB, once per cycle)
python -m datasrc.dtpp list KLNS            # see what's available before flying
python -m datasrc.dtpp open KLNS 4          # fetch + open chart #4 from that list directly
```

The index maps airport + chart name to a PDF filename; individual plates are
fetched on demand (not the whole cycle, which is many gigabytes) and cached
forever once downloaded — a cycle's charts don't change after publication.
The Charts page needs `update-index` run at least once; it says so on
screen (`no charts cached for KLNS - datasrc.dtpp update-index`) rather than
silently showing nothing.

## X-Plane feed (optional)

`python main.py --xplane-feed` (add `--xplane-host <ip>` if X-Plane is on
another machine). `xplane_feed.py` subscribes over UDP (`RREF`) to
`sim/flightmodel/position/` `latitude` / `longitude` / `elevation` /
`groundspeed` / `hpath` / `psi` / `magnetic_variation` / `vh_ind_fpm` /
`true_airspeed` / `phi` / `theta`. While the sim is answering, its position
drives the trainer; if the feed goes quiet for ~2 s the trainer falls back to
its own kinematic model (kept synced to the last live position, so no jump).

## Tablet EFB output (optional)

`python main.py --gdl90` broadcasts ownship as **GDL90** on UDP:4000 so
ForeFlight / Garmin Pilot / FltPlan Go show the aeroplane moving. Off by
default — when slaved to X-Plane, use the sim's own "Send to ForeFlight"
instead. `--gdl90-host` sets the destination (default `255.255.255.255`).

Broadcast doesn't reach every network — some Wi-Fi setups (hotel/FBO
guest networks, some home routers) isolate clients from each other and
drop it. Add `--gdl90-discover` to fix that for ForeFlight specifically:
ForeFlight itself broadcasts a small "here I am" UDP message on port 63093
every ~5 s (`{"App":"ForeFlight","GDL90":{"port":4000}}`, per the
[GDL 90 Extended spec](https://www.foreflight.com/connect/spec/)); the
trainer listens for it, learns the iPad's actual IP, and switches from
broadcasting blindly to sending GDL90 straight to that address. It falls
back to broadcast automatically if the tablet goes quiet (ForeFlight closed,
walked out of range). `--gdl90-discover` implies `--gdl90` (there's no point
discovering a peer you're not going to send to):

```
python main.py --gdl90-discover
```
