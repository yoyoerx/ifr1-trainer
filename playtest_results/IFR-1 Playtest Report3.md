# IFR-1 Playtest Report

15 / 40 scenarios answered.

## A. Setup & first flight

- **A1 First flight renders the flight plan (re-test)** — FAIL: button labels are more correct but missing VNAV and  none are  centered in their button cutout. when loading in, the heading is to the left of the  dtk and the plane executes a right turn. this is a wind thing?
- **A2 GNS 430 variant (re-test)** — FAIL: labels are present but incorrect. CDI OBS FPL and PROC should be the Direct To, ENT, CLR, MENU buttons.
- **A3 Steam-gauge layout — no overlapping text (re-test)** — PASS: All correct.

## B. Flight plan & Direct-To

- **B1 Turn anticipation before a waypoint (re-test)** — PASS
- **B2 Direct-To requires two ENTs to activate** — PASS: functions as expected
- **B3 Cancelling Direct-To resumes the nearest leg (re-test)** — FAIL: Direct To JFK when entered works. Does not resume navigation as expected. Remains on Direct To JFK
- **B4 In-flight flight plan edit (re-test)** — PASS: appears correct
- **B5 Flight Plan Catalog: store, recall, delete (re-test)** — PASS: No keyboard binding to press the MNU key equivalent. Works correctly with device.

## C. CDI scale & phase of flight

- **C1 Enroute CDI scale is 5.0 nm** — PASS: Works as expected
- **C2 Gradual transition to 1.0 nm near the destination** — PASS: works as expected
- **C3 Approach scale tightens to 0.3 nm near the FAF** — FAIL: the scale adjusts to 0.3 nm and then then expands back to 1.0 after passing POLCU. also noted that the procedure turn is not drawn on the map, 530 behavior draws the on map.
- **C4 Terminal scale near the departure airport too** — FAIL: starts at 5nm, then ramps down to 1nm, before ramping back up after 30 nm. since we are starting in the KBOS area, I would expect it to start at 1nm rather than ramp down and then up

## D. OBS, SUSP, CDI source

- **D1 OBS mode holds a manually-selected course (re-test)** — PASS: fully working
- **D2 SUSP auto-arms at a manual-termination leg / MAP** — PASS: works as expected
- **D3 CDI source auto-switches GPS to VLOC on an ILS** — FAIL: define more specifically when APR should be pressed. after the procedure turn? did not automatically switch over at POLCU in APR mode. APR mode skips the procedure turn. if NAV to PT and then arm APR, does not auto switch the freq in NAV1, ILS freq remains STBY. 

## Not yet answered

E1, E2, E3, E4, E5, F1, G1, G2, H1, H2, H3, I1, I2, I3, J1, K1, K2, L1, L2, L3, M1, N1, N2, N3, O1