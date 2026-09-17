# WORKING.md

Task tracker for **octavi-ifr-trainer**. Newest status at the top of each list.
`[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped.

Last updated: 2026-09-17

Prior task history (M0–M16, the initial build through the playtest-fix rounds)
is archived in `archive/WORKING-2026-09-11.md`, refreshed here to start a clean
slate for the next project. `FINDINGS.md` remains the permanent log of every
bug fixed against the Pilot's Guide/playtest feedback (F1–F28 so far) and is
**not** archived — keep adding to it as before.

---

## Current status (2026-09-17, 825 tests green)

The trainer runs end to end on three layouts (`gps`, `steam`, `dual`), driven
by the IFR-1 or keyboard, validated against the GNS 530(A) Pilot's Guide
190-00181-00 Rev. H. Five rounds of structured playtesting are complete —
round 5 was a clean 40/40 pass. See `FINDINGS.md` F1–F28 for the full list of
what was found and fixed.

---

## Active project: `stack` layout — a single-page IFR panel

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
- [ ] Add `pypdfium2` to `requirements.txt` / `pyproject.toml`
- [ ] `--layout stack` plumbed through `config.py` / `main.parse_args` /
      the `L`-key cycle (`main._next_layout`)
- [ ] Window resizes for `stack` (other layouts unaffected)
- [ ] Mouse input: `pygame.MOUSEBUTTONDOWN` handling in `main.run()`'s event
      loop, routed only when `stack` is active; a hit-testing helper in
      `render.py` (or a small new module) tabs/fields can register rects
      against

**Phase 1 — right column: the avionics stack**
- [ ] GNS 530/430 drawn as clean vector panels (no bezel SVG) for `stack`
- [ ] COM/NAV active+standby frequency annunciation on the GNS screen
- [ ] Transponder + S-TEC AP boxes restyled to sit directly under the GNS
      pair as one continuous column
- [ ] Confirm FPL/DTO/PROC/MSG/etc. still route correctly through the
      existing bezel-key handling with the new visual skin

**Phase 2 — middle column: instrument cluster**
- [ ] NAV1/NAV2 round CDI+GS+OBS heads, reusing `draw_nav_head`/
      `_vor_cdi_face`
- [ ] Standalone HDG indicator with a heading bug (adapt the six-pack's HDG
      dial rather than duplicating it)

**Phase 3 — left column: tabbed panel**
- [ ] Tab framework: click to switch WX/MAP/PLATE/SETTINGS
- [ ] WX tab (reuse `_draw_aux_weather`'s data path)
- [ ] MAP tab (reuse `_map`)
- [ ] PLATE tab: `pypdfium2` rasterization of the selected `datasrc.dtpp`
      chart, rendered inline
- [ ] SETTINGS tab: wind/winds-aloft editor + time-warp control, click-driven

**Phase 4 — AP bug boxes**
- [ ] HDG/IAS/ALT boxes wired to `Autopilot.heading_bug` / `World.ias_target`
      / `Autopilot.alt_preselect`, click+scroll editable

**Phase 5 — tests + docs**
- [ ] Headless render smoke tests for `stack` (same pattern as the other
      layouts in `tests/test_render.py`)
- [ ] Mouse hit-testing unit tests (synthetic `MOUSEBUTTONDOWN` events)
- [ ] `README.md`: document the new layout, its mouse controls, and the new
      dependency
- [ ] `ARCHITECTURE.md`: move the §8 "planned" write-up (added 2026-09-17) to
      the normal module-map/decisions sections once real, matching how every
      other layout is documented

---

## Backlog / ideas (unscheduled)

- Non-US coverage via a Navigraph FMS Data API backend (paid; no redistribution)
- Autopilot mode logic driven by the IFR-1 AP row (HDG/NAV/APR/ALT/VS arm+capture)
- GNS 430 / GTN 650 screen variants beyond what `stack` adds
- Failure injection (VOR out, GPS LOI) for partial-panel practice
- Record/replay of an IFR-1 session for regression tests of `gns530`

## Side project — "faa2xp": back-port FAA data into X-Plane (separate repo)

**Goal:** give a *running X-Plane 12 install* current US nav data (frequencies,
navaids, approaches) from the free FAA CIFP + NASR, without a Navigraph
subscription. X-Plane's bundled data is stuck at AIRAC 2406. Not on this
trainer's milestone path — see `archive/WORKING-2026-09-11.md` for the notes
if this gets picked up.

## Done log

See `archive/WORKING-2026-09-11.md` for the full dated log through M0–M16 and
the "gps"/"steam"/"dual" layouts' initial build, and `FINDINGS.md` for every
playtest-round fix (F1–F28) since. New entries for the `stack` layout project
go here once phases above start closing out.
