# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A lightweight IFR procedures trainer that simulates a Garmin GNS 530/430 and
surrounding nav instruments (CDI/HSI, bearing pointer, DME, marker beacons),
driven by an Octavi IFR-1 USB HID controller (keyboard works standalone too).
Runs on its own scripted flight model, or optionally slaved to a live X-Plane
12 session for ownship position. Nav data comes exclusively from the FAA's
public CIFP + NASR products (28-day AIRAC cycle) — see `ARCHITECTURE.md` §5.
Not affiliated with Octavi, Garmin, or Laminar Research.

`README.md` has the full user-facing reference (flags, keyboard bindings, IFR-1
protocol, wind/weather/charts subsystems). `ARCHITECTURE.md` is the living
module map — **read it first** when touching anything beyond a single file;
it documents cross-module data flow that isn't obvious from one file alone.
`WORKING.md` is the task tracker; `FINDINGS.md` is the GNS 530 validation
log against the Garmin Pilot's Guide (190-00181-00 Rev. H) — check it before
changing CDI/nav behavior, since prior fixes there cite specific manual
sections.

## Commands

```
pip install -e .[dev]              # editable install + pytest (assets resolve via __file__)
pytest                              # full suite (~800+ tests, headless-safe)
pytest tests/test_gpsnav.py         # single file — note: gpsnav core is tested via test_gns530.py's scenarios
pytest tests/test_ifr1.py -k name   # single test
python main.py --plan "KBOS PVD KJFK" --wind 300/25
python main.py --no-device          # hand-fly, keyboard only, no IFR-1 needed
python -m datasrc.faa update        # fetch/cache the current FAA CIFP+NASR cycle (required before first run)
```

No lint config is present in this repo (no ruff/flake8/mypy config file) —
don't invent one unprompted.

Nav data must be fetched once before the trainer will run (`navdata.load()`
raises `FileNotFoundError` with the exact fix-it command otherwise); it's
gitignored under `data/` and never fetched automatically.

## Architecture

Single Python process, one ~30 Hz loop, no threads except optional UDP
receivers (X-Plane feed, GDL90 discovery) and the opt-in weather auto-refresh
thread. **No network in the main loop** — all FAA/weather/chart fetching is a
separate offline CLI (`datasrc/`) that caches to disk with a manifest.

```
ifr1.py (HID in) --events--> gns530/gns430 (thin views over gpsnav.GpsNav, the avionics "brain")
                                    |
                     sim_model.py or xplane_feed.py (motion) --> instruments.py (CDI/HSI/DME math) --> render.py (pygame)
navdata/  (loaded nav DB, used by gpsnav + sim_model)      navmath.py (pure geometry, used by everyone)
datasrc/  (offline: fetch FAA CIFP+NASR / weather / charts, cache, manifest — never in the loop)
```

Key structural points to internalize before making changes:

- **`gpsnav.py` is the core.** All avionics state-machine logic (flight plan,
  Direct-To, sequencing/turn-anticipation, holds, OBS, CDI source/scale, PROC
  selector, VNAV, flight-plan catalog, MNU menu) lives here, variant-independent.
  `gns530.py`/`gns430.py` are *thin* `Variant` subclasses that differ only in
  screen height/row count/faceplate — **the state machine must never fork per
  variant**; if a change seems to need a 530/430 branch, it belongs in a
  `Variant` field, not an `if`.
- **Pure-function modules have no I/O and are the easiest to test in
  isolation:** `navmath.py`, `windsaloft.py`, `radios.py`, `autopilot.py`,
  `instruments.py`, `wmm.py`. Keep new geometry/avionics math in this style
  (stdlib only, no I/O) rather than folding it into a stateful module.
- **`datasrc/` vs `navdata/`:** `datasrc/` fetches and caches raw FAA/weather/
  chart files offline (CLI only); `navdata/` parses the cached FAA CIFP/NASR
  into the in-memory `NavDatabase` the trainer actually queries. Don't add
  network calls to `navdata/` or any module in the runtime loop.
- **Units/angle conventions are load-bearing** (see `ARCHITECTURE.md` §4):
  nm/kt/ft/degrees, true bearings unless a name ends `_mag`, cross-track `+`
  = ownship right of course, wind direction is meteorological (from-direction).
  Use `navmath.norm360`/`norm180`/`angle_diff`/`reciprocal` — never hand-roll
  angle math.
- **Magnetic variation** has two independent sources: station/airport magvar
  comes from nav data itself; ownship's own-position magvar comes from
  `wmm.py` (WMM 2025), falling back to nearest-navaid declination if the
  coefficient file is missing. Don't conflate the two.
- **The IFR-1 HID protocol is hardware-confirmed** (`ifr1.py`); frame offsets
  are pinned by captured-frame tests in `tests/test_ifr1.py`. If you suspect
  the protocol is wrong, verify with `python ifr1.py raw` against real
  hardware rather than guessing from the docs.
- **Time warp** re-runs the same fixed `dt` N times per frame rather than
  scaling `dt` — any new time-sensitive threshold in `sim_model`/`autopilot`/
  `gpsnav` must stay dt-based so it behaves identically at 1x and under warp.
- Changes to CDI/HSI/nav behavior should be cross-checked against
  `FINDINGS.md` (which cites exact Garmin Pilot's Guide sections) to avoid
  regressing an already-validated fix.
