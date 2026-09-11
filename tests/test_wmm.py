"""M7 - WMM declination, checked against NOAA's official WMM2025 test values.

``assets/wmm/WMM2025_TestValues.txt`` is NOAA's published table
(WMM2025COF.zip). Columns: year, alt_km, lat, lon, D, I, H, X, Y, Z, F, ...
We verify D / X / Y / Z for every row.
"""

import math
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import wmm  # noqa: E402

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "wmm")
_TESTVALS = os.path.join(_ASSETS, "WMM2025_TestValues.txt")


def _rows():
    with open(_TESTVALS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            f = [float(x) for x in line.split()]
            yield {"year": f[0], "alt": f[1], "lat": f[2], "lon": f[3],
                   "D": f[4], "X": f[7], "Y": f[8], "Z": f[9]}


@pytest.fixture(scope="module")
def model():
    return wmm.WmmModel.from_cof()


def test_cof_file_is_vendored_and_wmm2025(model):
    assert model.epoch == 2025.0
    assert "2025" in model.name


@pytest.mark.parametrize("row", list(_rows()))
def test_field_matches_noaa_reference(model, row):
    bx, by, bz = model.field(row["lat"], row["lon"], row["alt"], row["year"])
    d = math.degrees(math.atan2(by, bx))
    assert bx == pytest.approx(row["X"], abs=2.0)      # nT
    assert by == pytest.approx(row["Y"], abs=2.0)
    assert bz == pytest.approx(row["Z"], abs=2.0)
    # NOAA prints D to 0.01 deg; allow a hair more for high-latitude rows
    assert d == pytest.approx(row["D"], abs=0.02)


def test_declination_convenience_wrapper_is_east_positive():
    # Boston area: variation is about 14 W in 2025 -> declination ~ -14 deg
    d = wmm.declination(42.36, -71.01, year=2025.5)
    assert d is not None
    assert -16.5 < d < -12.5


def test_decimal_year_is_sane():
    assert wmm.decimal_year(date(2025, 1, 1)) == pytest.approx(2025.0)
    assert wmm.decimal_year(date(2025, 7, 2)) == pytest.approx(2025.5, abs=0.01)
