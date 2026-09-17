# IFR-1 Playtest Report

4 / 40 scenarios answered.

## A. Setup & first flight

- **A1 First flight renders the flight plan** — FAIL: Actually starts on BOS -> PVD and ownship starts to the east of BOS, not at KBOS. visuals otherwise correct. HSI heading shows 232 despite DTK being 221 once on course to PVD.
- **A2 GNS 430 variant** — PASS: Same navigation issue as A1, but 430 graphics appear correct.
- **A3 Steam-gauge layout — no overlapping text** — FAIL: IAS on the ASI is overlapped by the needle., same for the VSI. T/C should show the 2 min turn marks, HDG should have ownship marking. VS label on the AP is not in alignment with the other text (HDG or ALT SEL) ALT SEL overlaps the bottom of the AP box. so does IAS SET. only VS should show in the AP box, other set point numbers should show in their own box to the right as they are not part of the 55X AP display. No other text overlaps.

## B. Flight plan & Direct-To

- **B1 Turn anticipation before a waypoint** — FAIL: Ownship starts in the wrong location. AP toggle appears inverted. only tracks to the magenta line when AP is deselected. Request to engage heading and the NAV does not seem correct.  Only one should function at a time. HDG to track the heading bug and then NAV to track NAV source - GPS or NAV1 -  Pressing NAV twice should switch the 55x to GPSS mode (verify this behavior with 55X manual - find on web if needed). unable to full verify this test. 

## Not yet answered

B2, B3, B4, B5, C1, C2, C3, C4, D1, D2, D3, E1, E2, E3, E4, E5, F1, G1, G2, H1, H2, H3, I1, I2, I3, J1, K1, K2, L1, L2, L3, M1, N1, N2, N3, O1