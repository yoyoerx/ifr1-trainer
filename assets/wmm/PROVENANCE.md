# WMM coefficient data provenance

## WMM2025.COF

| | |
|---|---|
| **What it is** | World Magnetic Model 2025 spherical-harmonic coefficients (degree/order 12), epoch 2025.0, valid to 2029.x. Plain-text NOAA `.COF` format: header line, then `n m g h dg dh` rows. |
| **Origin** | **NOAA NCEI / NGA**, the official WMM producers. Canonical download: <https://www.ncei.noaa.gov/products/world-magnetic-model/wmm-coefficients> (`WMM2025COF.zip`). |
| **Pulled** | 2026-09-10, via a byte-identical mirror (`github.com/Navigraph/wmm2025`, `src/wmm2025/WMM.COF`) - the NCEI page blocks scripted download. Verify against NCEI's `WMM.COF` if in doubt. |
| **License** | **None / public domain.** A U.S. Government work (NOAA + NGA); the coefficients are facts, not a copyrightable work. Freely redistributable. |
| **How we use it** | `wmm.py` parses it and does the standard (public-domain, NOAA-published) field synthesis to get ownship magnetic declination. `main._local_magvar` prefers this over the nearest-navaid declination. |
| **To replace / update** | Drop the next epoch's file in here as `WMM2025.COF` (or edit `wmm._COF_PATH`). If the model name changes, `wmm.WmmModel.from_cof` reads the epoch from the header - no code change needed for a straight coefficient refresh. |

## WMM2025_TestValues.txt

NOAA's official validation table from the same `WMM2025COF.zip` (mirrored from
`github.com/boxpet/pygeomag`, MIT-licensed wrapper - the table itself is NOAA's).
Public domain. `tests/test_wmm.py` checks every row (D / X / Y / Z) against
`wmm.py`; agreement is < 0.01 deg and < 2 nT.
