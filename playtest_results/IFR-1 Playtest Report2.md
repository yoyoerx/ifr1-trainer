# IFR-1 Playtest Report

14 / 40 scenarios answered.

## A. Setup & first flight

- **A1 First flight renders the flight plan** — PASS: Bezel labels are displaced from correct button locations.
- **A2 GNS 430 variant** — PASS: 430 present. bezel is unlabeled.
- **A3 Steam-gauge layout — no overlapping text (re-test)** — PASS: top edge of the numbers inside the boxes on the gauges touch the top edge of the box. IAS SET touches the bottom edge of the box. Too much space between the VS label and the numbers, too much space between the VS numeric display and the VS buttons, we can condense a small amount. 

## B. Flight plan & Direct-To

- **B1 Turn anticipation before a waypoint (re-test)** — FAIL: Flying left of course on leg to PVD. DTK not tracking TRK 220 vs 217. not tracking the magenta line. NAV function works.  no indication that a "NEXT DTK" appeared.
- **B2 Direct-To requires two ENTs to activate** — PASS: functions as expected
- **B3 Cancelling Direct-To resumes the nearest leg** — FAIL: Unclear on instructions. Direct To JFK when entered works. Does not resume navigation as expected. Remains on Direct To
- **B4 In-flight flight plan edit** — FAIL: Able to reach the Flight Plan page and press TAB to get CRSR and move through the list but not able to input waypoint. What is the press? enter? arrows? PgDn/PgUp or Shift+PgUp/Shift+PgDn don't do that. unable to check step 3 with keyboard. With the device enabled, ENT works, but needs a hint like an underline on the character that is being edited. Able to insert waypoint at the end. No obvious way to insert a waypoint in the middle, only change them. Changing and adding does update DTK and DIS.
- **B5 Flight Plan Catalog: store, recall, delete** — FAIL: no keyboard MNU, performed test with device. appears to function with ifr1 device. only tested 4 saves, did not validate all 19 slots. Discovered that the knob behavior for switching pages does not match the 530. Should remember the page its on when changing groups. (Verify this is the correct behavior)

## C. CDI scale & phase of flight

- **C1 Enroute CDI scale is 5.0 nm** — PASS: Works as expected
- **C2 Gradual transition to 1.0 nm near the destination** — PASS: works as expected
- **C3 Approach scale tightens to 0.3 nm near the FAF** — PASS: scale adjusts as expected or slightly early, adjusted on the outbound leg near DALAC. did note that the green triangle on the CDI on the 530 is to the right of the horizontal line. also noted that the procedure turn is not drawn on the map.
- **C4 Terminal scale near the departure airport too** — FAIL: starts at 5nm, then ramps down to 1nm, before ramping back up after 30 nm.

## D. OBS, SUSP, CDI source

- **D1 OBS mode holds a manually-selected course** — FAIL: works on keyboard. not clear what the IFR1 button bind is. I would expect to have CRS1 (Shift+NAV1 mode) to adjust the OBS when in OBS. What is the knob behavior in the 530? we should attempt to duplicate.  Separately, the triangle and the line on the CDI refuse to overlap. Maybe related to the issues reported in C3.
- **D2 SUSP auto-arms at a manual-termination leg / MAP** — PASS: works as expected

## Not yet answered

D3, E1, E2, E3, E4, E5, F1, G1, G2, H1, H2, H3, I1, I2, I3, J1, K1, K2, L1, L2, L3, M1, N1, N2, N3, O1