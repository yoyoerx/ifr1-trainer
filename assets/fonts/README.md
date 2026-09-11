# Bundled fonts

Both families are under the **SIL Open Font License 1.1** (permissive; may be
bundled and redistributed). Full licence text is alongside the files.

| File(s) | Family | Upstream | Licence |
|---|---|---|---|
| `B612Mono-Regular.ttf`, `B612Mono-Bold.ttf`, `B612-Regular.ttf`, `B612-Bold.ttf` | **B612 / B612 Mono** — cockpit-legibility typeface by Airbus + ENAC | <https://github.com/polarsys/b612> | OFL-1.1 (`B612-OFL.txt`) |
| `DSEG7Classic-Regular.ttf`, `DSEG7Classic-Bold.ttf`, `DSEG14Classic-Regular.ttf` | **DSEG** — 7-/14-segment LCD segment font by keshikan | <https://github.com/keshikan/DSEG> | OFL-1.1 (`DSEG-LICENSE.txt`) |

`render.py` loads these from here and falls back to Consolas / a system mono if a
file is missing. **DSEG7 only has digits, `.`, `:`, `-`** — feed it numeric
strings only (frequencies, squawk, altitude/heading readouts).

## Not bundled — instrument face SVGs

`sebmatton/jQuery-Flight-Indicators` (the obvious six-pack SVG set) is
**GPL-3.0**, which would relicense this repo. It is kept as a visual reference
only; the gauges in `render.py` are drawn as vectors instead. If a permissively
licensed face set turns up (or the GPL is acceptable for a private build), wire
it via `pygame.image.load_sized_svg`.
