# IFR-1 Playtest Report

40 / 40 scenarios answered.

## A. Setup & first flight

- **A1 First flight renders the flight plan (re-test)** — PASS: all correct
- **A2 GNS 430 variant (re-test)** — PASS: all correct
- **A3 Steam-gauge layout — no overlapping text (re-test)** — PASS: All correct.

## B. Flight plan & Direct-To

- **B1 Turn anticipation before a waypoint (re-test)** — PASS: all correct
- **B2 Direct-To requires two ENTs to activate** — PASS: functions as expected
- **B3 Cancelling Direct-To resumes the nearest leg (re-test)** — PASS: Works as described.
- **B4 In-flight flight plan edit (re-test)** — FAIL: appears correct, input character should have underline hint to know which character in the identifier is being changed.
- **B5 Flight Plan Catalog: store, recall, delete (re-test)** — PASS: No keyboard binding to press the MNU key equivalent. Works correctly with device.

## C. CDI scale & phase of flight

- **C1 Enroute CDI scale is 5.0 nm** — PASS: Works as expected
- **C2 Gradual transition to 1.0 nm near the destination** — PASS: works as expected
- **C3 Approach scale tightens to 0.3 nm near the FAF, only on the FAF leg (re-test)** — PASS: works correctly
- **C4 Terminal scale near the departure airport too (re-test)** — PASS: works correctly

## D. OBS, SUSP, CDI source

- **D1 OBS mode holds a manually-selected course (re-test)** — PASS: fully working
- **D2 SUSP auto-arms at a manual-termination leg / MAP** — PASS: works as expected
- **D3 CDI source auto-switches GPS to VLOC on an ILS (re-test)** — PASS: works as expected.

## E. Procedures (PROC, approaches, holds)

- **E1 On-screen PROC selector** — PASS: JFK shows and loads approaches, arrivals, and departures
- **E2 A full approach loads, including non-fix legs** — PASS: loads correctly
- **E3 A holding pattern is actually flown, not just suspended (re-test)** — PASS: 1.5 nm from DALAC - consider improving the feedback loop.
- **E4 Missed-approach hold repeats until released** — PASS
- **E5 A second real approach loads cleanly** — PASS: Loads and flies correctly

## F. VNAV

- **F1 VNAV profile reports distance, deviation, required VS and time-to-TOD (re-test)** — FAIL: vnav SETTING should be in ft/min not an angle - vnav decent programmed in ft/min - please validate why I think this - older version? improperly implemented in other nav computer sims?

## G. WPT & NRST pages

- **G1 WPT page identifier lookup is filtered per page (re-test)** — FAIL: word "intersection" overlaps identifier on page. identifier input should have underline hint for active character.
- **G2 NRST nearest list** — FAIL: should be able to scroll the lists like the metar/taf page to reach NRST POIs that are further away. 

## H. AUX pages

- **H1 Setup page: CDI scale mode (re-test)** — FAIL: option now appears on the menu and is changeable.  when close to terminal area, will go from 1nm to 0.3, but will not change the display to 5nm when asked. not overriding terminal area limit.
- **H2 Nav Data page shows AIRAC cycle and expiry** — PASS: 2609, EXP 2026-10-01
- **H3 Trip Planning / Utility pages render** — PASS

## I. Weather (METAR / TAF / winds aloft)

- **I1 METAR/TAF on the Weather page, and CRSR scrolls a long TAF (re-test)** — FAIL: works but if you overshoot the bottom of the list and scroll further, the sim makes you scroll back that number of steps before returning to scroll behavior.  should just cap and then reverse. 
- **I2 Winds aloft affects the flight** — PASS
- **I3 Background weather auto-refresh** — PASS

## J. Approach charts (d-TPP)

- **J1 Charts page opens a real plate** — PASS

## K. Messages & long-press actions

- **K1 Message queue and MSG annunciator** — PASS
- **K2 Long-press: Default NAV and COM emergency** — PASS

## L. Autopilot & radios

- **L1 Autopilot lateral/vertical modes** — PASS
- **L2 GPSS coupling** — PASS
- **L3 Radio tuning and flip-flop** — FAIL: NAV tuning and OBS rotation work but NAV source for the AP does not change to the NAV VOR when CDI is pressed.  Expected behavior is to change the AP NAV source depending on the CDI state.

## M. Time warp

- **M1 1x/5x/10x/20x speeds behave correctly** — PASS

## N. Dual 530/430 (--dual)

- **N1 Dual layout draws both units cleanly** — PASS
- **N2 FMS1 and FMS2 are genuinely independent** — PASS
- **N3 A plain (non-dual) run is unaffected** — PASS

## O. Keyboard-only flying

- **O1 Complete keyboard-only session** — FAIL: Menu button needs a key. 
