# IFR-1 Playtest Report

27 / 40 scenarios answered.

## A. Setup & first flight

- **A1 First flight renders the flight plan (re-test)** — PASS: all correct
- **A2 GNS 430 variant (re-test)** — PASS: all correct
- **A3 Steam-gauge layout — no overlapping text (re-test)** — PASS: All correct.

## B. Flight plan & Direct-To

- **B1 Turn anticipation before a waypoint (re-test)** — PASS: all correct
- **B2 Direct-To requires two ENTs to activate** — PASS: functions as expected
- **B3 Cancelling Direct-To resumes the nearest leg (re-test)** — PASS: Works as described.
- **B4 In-flight flight plan edit (re-test)** — PASS: appears correct
- **B5 Flight Plan Catalog: store, recall, delete (re-test)** — PASS: No keyboard binding to press the MNU key equivalent. Works correctly with device.

## C. CDI scale & phase of flight

- **C1 Enroute CDI scale is 5.0 nm** — PASS: Works as expected
- **C2 Gradual transition to 1.0 nm near the destination** — PASS: works as expected
- **C3 Approach scale tightens to 0.3 nm near the FAF (re-test)** — FAIL: the scale adjusts to 0.3 nm and then jumps back to 1.0 after passing POLCU outbound, works, but should be POLCU inbound only. 
- **C4 Terminal scale near the departure airport too (re-test)** — FAIL: starts at 5nm, then ramps down to 1nm, before ramping back up after 30 nm. since we are starting in the KBOS area, I would expect it to start at 1nm rather than ramp down and then up.

## D. OBS, SUSP, CDI source

- **D1 OBS mode holds a manually-selected course (re-test)** — PASS: fully working
- **D2 SUSP auto-arms at a manual-termination leg / MAP** — PASS: works as expected
- **D3 CDI source auto-switches GPS to VLOC on an ILS (re-test)** — PASS: works as expected.

## E. Procedures (PROC, approaches, holds)

- **E1 On-screen PROC selector** — PASS: only shows approaches, no arrivals or departures for KLNS
- **E2 A full approach loads, including non-fix legs** — PASS: loads correctly
- **E3 A holding pattern is actually flown, not just suspended (re-test)** — PASS: 1.5 nm from DALAC - consider improving the feedback loop.
- **E4 Missed-approach hold repeats until released** — PASS
- **E5 A second real approach loads cleanly** — PASS: Loads and flies correctly

## F. VNAV

- **F1 VNAV profile reports distance and deviation** — FAIL: vnav should be in ft/min and yield time until starting decent. angle not typical metric. please confirm. 

## G. WPT & NRST pages

- **G1 WPT page identifier lookup** — FAIL: WPT will show Airport, navaid, or intersection in any of the pages. not flitered per page.
- **G2 NRST nearest list** — PASS

## H. AUX pages

- **H1 Setup page: CDI scale mode** — FAIL: setup shows UNIT, CDI SRC, and BARO and NAV UNITS. nothing is configurable. does not show CDI Alarms.
- **H2 Nav Data page shows AIRAC cycle and expiry** — PASS: 2609, EXP 2026-10-01
- **H3 Trip Planning / Utility pages render** — PASS

## I. Weather (METAR / TAF / winds aloft)

- **I1 METAR/TAF on the Weather page** — PASS: Loads data. shows data. screen in 530 too small to show complete TAF and cuts of data. CRSR should scroll?

## Not yet answered

I2, I3, J1, K1, K2, L1, L2, L3, M1, N1, N2, N3, O1