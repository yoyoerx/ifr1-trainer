# WORKING.md

Task tracker for **octavi-ifr-trainer**. Newest status at the top of each list.
`[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped.

Last updated: 2026-09-18 (stack layout: Phase 0-5 complete + WX/PLATE
airport-switching follow-up, 839 tests green)

Prior task history (M0–M16, the initial build through the playtest-fix rounds)
is archived in `archive/WORKING-2026-09-11.md`, refreshed here to start a clean
slate for the next project. `FINDINGS.md` remains the permanent log of every
bug fixed against the Pilot's Guide/playtest feedback (F1–F28 so far) and is
**not** archived — keep adding to it as before.

---

## Current status (2026-09-18, 839 tests green)

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

## Side project — "faa2xp": back-port FAA data into X-Plane (separate repo)

**Goal:** give a *running X-Plane 12 install* current US nav data (frequencies,
navaids, approaches) from the free FAA CIFP + NASR, without a Navigraph
subscription. X-Plane's bundled data is stuck at AIRAC 2406. Not on this
trainer's milestone path — see `archive/WORKING-2026-09-11.md` for the notes
if this gets picked up.

## Done log

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
