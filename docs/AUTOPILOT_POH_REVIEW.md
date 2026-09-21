# Autopilot: S-TEC System Fifty Five X POH vs. the trainer

The trainer's autopilot (`autopilot.py`, driven from `main.World.tick`, flown by
`sim_model.SimModel`) is meant to behave like the S-TEC 55X. This is a line-by-line
comparison with the manual, written so gaps are fixed against the text and not against memory.

**Source.** *S-TEC System Fifty Five X Pilot's Operating Handbook*, 4th Ed., Nov 30 2007
(1st Rev. Mar 01 2008; software rev 5+ described alongside rev 4-). Fetched from
`http://www.flying20club.org/documents/Sys_55_X_POH_(4th_Ed).pdf`; local copy
`docs/reference/STEC_System55X_POH_4thEd.pdf` (80 pages, SHA-256
`d53d9b1e1fd7ebf62a9bc2d306861e78a299875a1b8bc49e190481ba5e062a60`). Like the Garmin guide the PDF is
gitignored (`/docs/reference/*.pdf`, third-party copyright), so a fresh clone must re-download it.
Page numbers below are the POH's printed ones ("3-4"), section numbers are its own.

**Other references saved beside it** (all gitignored): `GNS500_Installation_Manual_190-00181-02_RevJ.pdf`
(Garmin 500 Series Installation Manual: OBS/course-select input, GPSS roll-steering label 121, CDI switching;
SHA-256 `7c4b5ad94a5b9d2820e4f8f6dca56f3ccda227d7121f3bfd6858ed4eca5770e2`, from
`aeroelectric.com/Installation_Data/Garmin/GNS530_IM.pdf`) and `STEC_55X_Cirrus_Transition_Training.pdf`
(36-slide S-TEC 55X training deck, NAV vs GPSS, roll-before-pitch; SHA-256
`4a2a3e1d562c09fa11621c9bb9e68c02d45444c6d7309d9c424748bf814ce78e`, from `befa.org`).

**The GNS 530 Pilot's Guide is silent on autopilot behaviour** - it only says the autopilot follows the
course selected on the external CDI/HSI (pp.80, 94, 189). Everything about *how* the autopilot flies is
this POH.

Status: **OK** matches the text - **PARTIAL** right idea, wrong numbers or missing conditions -
**GAP** behaves differently - **N/M** not modelled.

## 1. Roll axis

| # | POH says | Trainer does | Status |
|---|---|---|---|
| R1 | Sec.3.1.2 p.3-4: at full-scale CDI deflection the AP establishes a **45 deg** intercept to the selected course; less than 45 only when closure rate is high. | GPS legs: 45 deg cut (`nav_intercept_deg`, F49). **VOR/LOC (VLOC) path: proportional, 22 deg per unit deflection capped at 30 deg** (`_VLOC_GAIN`, `_MAX_INTERCEPT`) - at full scale that is a 22 deg cut. | **OK** (F49 GPS, F50 VLOC/NAV/APR/REV) |
| R2 | p.3-4: turn onto the course "will always begin between 100% and 20% CDI needle deflection", earlier at faster closure rate. | GPS legs: turn-in distance = lead of a 90%-rate turn x1.5, clamped to 20-100% of full scale (constant in closure rate). VLOC: none - a linear ramp. | **PARTIAL** (F50: closure-rate based, inside the 20-100% bound) |
| R3 | p.3-4: **capture at 15%** deflection: gain steps down. | F50: capture at 15% for GPS and VLOC (annunciator and stage), with a gain step (50 -> 30 -> 22 deg/unit). | **OK** |
| R4 | p.3-4: staged authority after capture - **CAP** (90% std-rate limit), **CAP SOFT** at +15 s (45% std rate), crosswind correction established at +30 s, **SOFT** at +75 s (15% std rate); "at 115 kts, 2.4 nm is travelled in 75 s". | F50: `Autopilot.stage` INTERCEPT -> CAP -> CAP SOFT (+15 s) -> SOFT (+75 s, NAV only), turn-rate limits 90/45/15%, drift correction from +30 s (also on the roll-out ramp, so wind cannot park the aircraft outside the capture band). | **OK** |
| R5 | p.3-5: in SOFT the AP "ignores short term CDI needle deflections" (no scalloping over station passage); >50% for 60 s reverts to CAP SOFT. | F50: SOFT filters the needle (15 s), >50% for 60 s -> CAP SOFT. | **OK** |
| R6 | p.3-5: **NAV flashes** when deflection >50% or flag in view (FAIL also on flag). | F53: `Autopilot.flashing` - NAV/APR/REV at >50% deflection or no valid needle; NAV+GPSS with no course; the S-TEC panel blinks the lamp. | **OK** |
| R7 | p.3-4: engaging with course pointer within 5 deg of the course and <10% deflection goes **straight to SOFT**. | F50: <10% deflection and heading within 5 deg of the course engages straight into SOFT (CAP SOFT for APR/REV). | **OK** |
| R8 | p.3-5: pressing **APR while tracking in NAV** (within 50%) raises authority to CAP SOFT; a **new course >=10 deg** different reverts to CAP. | F50: a course change >= 10 deg reverts to CAP; `press_apr` while in NAV swaps the mode and restarts the coupler (it does not keep the captured state). | **PARTIAL** |
| R9 | Sec.3.1.2.1 / 3.1.3.1 p.3-6: **pilot-selectable intercept angle** - set the heading bug to the intercept heading, hold HDG, press NAV (twice for GPSS); AP flies that heading until it must turn to avoid overshoot. | Not implemented. | **N/M** |
| R10 | Sec.4.1 p.4-3: turn-rate limit **90% of standard rate** (HDG, NAV, APR, REV, CWS; piston), **GPSS 90/110/130%** by hardware mod code. | F50: `Commands.turn_rate_dps` -> `SimModel.turn_rate_limit`: HDG 90%, NAV/APR/REV 90/45/15% by stage. GPSS 110% (the AR-and-above hardware code; POH offers 130/90/110% by code). | **OK** |
| R11 | Sec.3.1.2 p.3-3: **NAV mode uses the course selected on the HSI/OBS** and the CDI needle; the pilot must keep it set. GPSS (sec.3.1.3) follows the GPS's own course and ignores the course pointer. GNS 530 Pilot's Guide p.175: 'Set course to [###]' when the selected course is >10 deg off the DTK. | F51: NAV on GPS flies the NAV1 CRS pointer (`--ap-course manual`, the default) against the GPS needle; GPSS ignores it; the 530 posts 'Set course to ###' (`GpsNav.check_course_select`). `--ap-course auto` keeps the old DTK-slaved behaviour. | **OK** |
| R12 | Sec.3.1.3 p.3-7: NAV GPSS with no programmed course: NAV and GPSS **flash**, AP holds wings level. | F53: NAV and GPSS flash and the AP holds heading when no course is programmed. | **OK** |
| R13 | Sec.3.1.3 p.3-7: the AP will not accept course-error input from the HSI in GPSS. | Consistent (GPSS ignores OBS). | **OK** |
| R14 | Sec.3.1.3, 3.5.2 p.3-34: GPSS + a GPS with the missed approach loaded flies it, including the hold; sec.3.3.7 the whole lateral approach incl. procedure turn. | Works through the `gpsnav` legs (flown-hold, arcs, missed approach SUSP). | **OK** |
| R15 | Sec.3.1.1 p.3-3: HDG mode turns to the bug and holds it. | `heading_bug + magvar`. Pressing HDG a second time drops to **LVL** (wings level) - the POH describes no such toggle. | **OK / PARTIAL** |
| R16 | The POH's *roll modes* are the mode buttons themselves - **HDG, NAV, NAV APR, REV, REV APR, NAV GPSS** (sec.3.1.4: "a roll mode (HDG, NAV, NAV APR, REV, REV APR, NAV GPSS)"). There is **no separate roll button**. AP master on -> display reads **RDY** (no roll servo engaged, pilot flying, p.2-3); pressing HDG/NAV/APR/REV engages the roll axis; ALT/VS/CWS then "can only be engaged" with one of those already engaged (sec.3.1.4, 3.1.5, 4.2). | Master press engages an invented **LVL** roll base; ALT/VS press engages the AP by themselves (`press_alt` -> `engage()`). Trainer should show RDY, fly nothing until HDG/NAV/APR/REV, and ignore ALT/VS before that. | **OK** (F52: `Lat.RDY`, `Autopilot.roll_engaged`, ALT/VS ignored before a roll mode) |
| R17 | Sec.3.1.2 note: the DG/HSI heading system differences (bug set to course on DG). | Trainer assumes an HSI. | **N/M** (acceptable) |

## 2. Approach modes

| # | POH says | Trainer does | Status |
|---|---|---|---|
| A1 | Sec.3.3.3, 3.3.4: LOC/VOR **front course** inbound: **APR** button = NAV APR, intercepts and tracks. | `press_apr` through the same coupler as NAV, tracking in CAP SOFT (never SOFT). | **OK** |
| A2 | Sec.3.3.1: REV = back course (HSI: course pointer stays on the front course, AP reverses sensing). | `press_rev` reverses the deflection sign. | **OK** |
| A3 | Sec.3.2.1.1 p.3-12: **GS arms automatically** after 1 s (rev 5+; 10 s rev 4-) when: NAV APR + **ALT engaged** + no flags + LOC freq + within **50%** of LOC + **>10%** GDI below the glideslope. | F53: GS arms itself after 1 s of: APR + ALT engaged, LOC tuned on VLOC, no flags, within 50% of the localizer, >10% GDI below the beam (`_track_glideslope`). Rev 4 (10 s / 60%) is not modelled. | **OK** |
| A4 | p.3-12: GS **captures at 5% GDI** below centerline; ALT annunciation drops. | F53: engages at 5% GDI below the beam (the ALT annunciation goes out); nominal descent uses the 3.00 deg rate (5.31 fpm/kt). Headless ILS 08: |GDI| <= 0.05 through the approach in 0/270@25/090@30 wind. | **OK** |
| A5 | p.3-12: pressing **APR** with GS armed disables it (GS flashes); again re-arms (1 s / 10 s). Pressing **ALT** above the beam engages GS manually; caution >20% above - "aggressive". | F53: APR press with GS armed disarms it (GS flashes), the next press re-arms it (back after 1 s); ALT press with APR+ALT and a usable beam engages GS at once, ALT again leaves it. The >20%-above caution is not enforced (the descent is just rate-clamped). | **OK** |
| A6 | p.3-13: GS annunciation **flashes** at >50% GDI or flag (with FAIL). | F53: the GS annunciation flashes at >50% GDI or a flag (`flashing`, blinking in the panel). | **OK** |
| A7 | p.3-12: at DH disconnect - the AP does not level off (sec.3.5.1 caution for WAAS at DH/MDA). | No automatic level-off at DH/MDA - the pilot disconnects (POH sec.3.2.1, 3.5.1 caution); the trainer does the same. | **OK** |
| A8 | Sec.3.5.1 p.3-34: with a WAAS GPS, GPSS flies the lateral approach; **NAV APR on the FAC** captures the GPS glideslope (LPV, LNAV/VNAV, LNAV+V). | No GPS-vertical GS coupling (GS only from a tuned ILS). | **N/M** |
| A9 | Sec.3.2.1: GS holds via closure rate on the beam. | F53: 3.00 deg nominal descent (5.31 fpm/kt) plus a 400 fpm/unit correction; flown headless to 1 nm in 3 winds within 0.05 GDI. | **OK** |

## 3. Pitch axis

| # | POH says | Trainer does | Status |
|---|---|---|---|
| P1 | Sec.3.1.4 p.3-8: ALT hold holds the captured altitude; **modifier knob 20 ft per detent, +/-360 ft** from capture. | F54: `turn_vs_knob` on ALT: 20 ft per detent, +/-360 ft from the captured altitude (also from a preselect capture). | **OK** |
| P2 | Sec.3.1.5 p.3-8: VS holds the captured rate; knob **100 fpm/detent, +/-1600 fpm** from the captured value; **VS flashes** if it cannot hold for 15 s in a climb. | F54: VS engage captures the present rate (rounded to 100 fpm); knob 100 fpm/detent, +/-1600 fpm from the captured rate, 1600 absolute; VS flashes after 15 s of a climb that cannot be held (>200 fpm short - the POH gives no tolerance). | **OK** |
| P3 | Sec.4.2 p.4-3: max **1600 fpm**, 0.6 g, 32,000 ft. | F54: 1600 fpm limit enforced (was 2000). | **OK** |
| P4 | Sec.3.1.5: VS shows the captured vertical speed in fpm x100 in the display. | Shown via `vs_target`. | **OK** |
| P5 | Sec.3.1.7 p.3-9: TRIM UP / TRIM DN annunciations after 3 s of servo loading, flash after 4 more s, audible tone. | F54: TRIM UP/DN appears after 3 s of held vertical rate and flashes 4 s later; the commanded rate stands in for servo loading and the audible tone is not modelled. | **PARTIAL** (visual only) |
| P6 | Altitude preselect capture out of VS is not in this POH (it is the 55X-with-altitude-selector option). | `alt_preselect` capture modelled (documented as an installed option). | **N/A** |

## 4. Other

| # | POH says | Trainer does | Status |
|---|---|---|---|
| O1 | Sec.3.1.6: **CWS** (control-wheel steering) holds turn rate and VS. | Not modelled. | **N/M** |
| O2 | Sec.3.7: disconnect by yoke switch/master/trim switch; RDY flashes 5 s with an audible alert. | F54: the single IFR-1 AP key = yoke AP DISC with a roll mode engaged (-> RDY flashing 5 s), master off from RDY, on from off. Audible alert not modelled. | **PARTIAL** (trainer mapping of one key onto two POH controls) |
| O3 | Sec.2.1/2.2: power-up test annunciations, FAIL states. | Not modelled. | **N/M** (out of scope) |
| O4 | Sec.3.1.3: press NAV twice for GPSS, "only press it once" if NAV is already engaged. | First press NAV, second press GPSS, third press deletes it. The "deletes" step is not in **this** POH; an earlier docstring cites "sec.4.2.5" of a different edition. | **UNVERIFIED** (see below) |
| O5 | Sec.3.6: yaw damper. | Not modelled. | **N/M** |

Docstring/comment citations in `autopilot.py` to "POH sec.4.2.2" and "sec.4.2.5" do not correspond to this
edition (its intercept text is sec.3.1.2, GPSS is sec.3.1.3; sec.4.2 is pitch limits). They should be re-pointed
or the other edition sourced and saved beside this one.

## 5. Recommended order (highest training impact first)

1. **VLOC intercept + capture, staged (R1-R5, R7, R8, A1).** One shared "CDI coupler" with the POH's 45 deg
   cut, 100->20% turn-in, 15% capture, and the CAP -> CAP SOFT -> SOFT timeline (90/45/15% std-rate limits,
   wind correction at +30 s, ignore excursions in SOFT). This is the bulk of what a pilot sees on every
   VOR/LOC/ILS.
2. **Per-mode turn-rate limits (R10)** in `SimModel`/`Commands` (90% NAV/APR/HDG/REV; GPSS 90-130%).
3. **NAV vs GPSS (R11).** NAV follows the selected course pointer / CDI deflection (GPS or VLOC); GPSS follows
   the GPS. Add the 530 "Set course to ###" advisory (Pilot's Guide p.80, >10 deg difference).
4. **RDY / roll-mode interlock (R16)** and disconnect alert (O2).
5. **Glideslope auto-arm, 5% capture, APR re-arm (A3-A5).**
6. **Knob ranges and flashes (P1-P3, R6, A6).**
7. **Pilot-selectable intercept angle (R9)**, GPS glideslope (A8), CWS (O1).

Decisions to make before starting: the S-TEC turn-in point and the timed stages are not published as
formulas - they are "variable"/closure-rate driven. The trainer would pick and document its own model (F49
already does for the GPS leg); tests should assert the POH's bounds (45 deg, 100-20%, 15%, 15/30/75 s,
90/45/15%), not a specific curve. And R11 changes the pilot workflow (keeping the course pointer on DTK),
so it should be a setting, not a silent change.
