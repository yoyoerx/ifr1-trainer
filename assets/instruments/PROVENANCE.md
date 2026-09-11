# Instrument art provenance

Every file here is tracked so it can be swapped or removed cleanly later. Nothing
in this directory is original to this project.

## garmin-gns-530/faceplate.svg

| | |
|---|---|
| **Source** | `allanglen/c172-flight-sim-panel`, `parts/avionics/garmin-gns-530/faceplate.svg` |
| **URL** | https://github.com/allanglen/c172-flight-sim-panel |
| **Author** | Allan Glen |
| **License** | **Apache-2.0** (permissive; full text in `garmin-gns-530/LICENSE`) |
| **Pulled** | 2026-09-09, from `master` |
| **What it is** | A laser-cut + engrave vector of a real GNS 530 front panel — 165 x 120 mm, screen cutout, all button/knob cutouts, engraved labels (GARMIN, GNS 530, COM/VLOC, GPS, VOL/ID, DEFAULT NAV). |
| **How we use it** | `render.py` loads it via `pygame.image.load_sized_svg`, recolours the red cut-strokes to a bezel grey, and draws it as the GPS-layout bezel with the text screen positioned inside the cutout. Falls back to a hand-drawn panel if the file is absent. |
| **To replace** | Drop a new `faceplate.svg` in `garmin-gns-530/` (or delete it for the fallback). Only `render.Renderer._load_bezel` and the `_SCREEN_FRAC` rect touch it. |

## garmin-gns-430/faceplate.svg

| | |
|---|---|
| **Source** | `allanglen/c172-flight-sim-panel`, `parts/avionics/garmin-gns-430/faceplate.svg` |
| **URL** | https://github.com/allanglen/c172-flight-sim-panel |
| **Author** | Allan Glen |
| **License** | **Apache-2.0** (permissive; full text in `garmin-gns-430/LICENSE`) |
| **Pulled** | 2026-09-09, from `master` |
| **What it is** | Same family as the 530 vector, for the shorter GNS 430 — 165 x 69 mm, same width, ~half the height. Screen cutout, button/knob cutouts, engraved labels (GARMIN, GNS 430, COM/VLOC, GPS, VOL/SQ, VOL/ID, DEFAULT NAV). |
| **How we use it** | `render.py` selects it via `gpsnav.VARIANT_430.bezel_dir` when `--unit 430` is active; same recolour + screen-inside-cutout path as the 530. `screen_frac=(0.235, 0.048, 0.793, 0.884)` was measured off a raster of this SVG. |
| **To replace** | Drop a new `faceplate.svg` in `garmin-gns-430/` (or delete it for the hand-drawn fallback). `gpsnav.VARIANT_430.screen_frac` / `bezel_aspect` and `render.Renderer._load_bezel` are the only touch points. |

## Not vendored

* **`sebmatton/jQuery-Flight-Indicators`** six-pack SVGs — repo README says MIT but
  the `LICENSE` file is GPL-3.0. Six-pack gauges are drawn as vectors instead.
* **FlightGear** instrument art — GPL-2.0; visual reference only.
