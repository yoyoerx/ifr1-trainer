"""Unit tests for navdata.arinc424 field parsers.

Reference values: JFK VOR is at N40 37 58.38 / W073 46 17.01 per cycle-2609
CIFP; that works out to 40.632883 / -73.771392 deg.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navdata.arinc424 import (  # noqa: E402
    opt_int,
    parse_lat,
    parse_latlon,
    parse_lon,
    parse_magvar,
    parse_ndb_freq_khz,
    parse_vhf_freq_mhz,
)


# --------------------------------------------------------------------------- #
# coordinates                                                                #
# --------------------------------------------------------------------------- #
def test_parse_lat_north():
    assert parse_lat("N40375838") == pytest.approx(40.632883, abs=1e-6)


def test_parse_lat_south_is_negative():
    assert parse_lat("S14195733") == pytest.approx(-(14 + 19 / 60 + 57.33 / 3600), abs=1e-9)


def test_parse_lon_west_is_negative():
    assert parse_lon("W073461701") == pytest.approx(-73.771392, abs=1e-6)


def test_parse_lon_east_is_positive():
    assert parse_lon("E166373835") == pytest.approx(166 + 37 / 60 + 38.35 / 3600, abs=1e-9)


def test_parse_latlon_returns_point():
    p = parse_latlon("N40375838", "W073461701")
    assert (p.lat, p.lon) == pytest.approx((40.632883, -73.771392), abs=1e-6)


@pytest.mark.parametrize("bad", ["", "X40375838", "N4037583", "N403758380", "NABCDEFGH"])
def test_parse_lat_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_lat(bad)


@pytest.mark.parametrize("bad", ["W07346170", "Q073461701", "W07346170X"])
def test_parse_lon_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_lon(bad)


# --------------------------------------------------------------------------- #
# magnetic variation (east positive)                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field, expected",
    [("W0120", -12.0), ("E0141", 14.1), ("T0000", 0.0), ("E0000", 0.0),
     ("W0005", -0.5), ("", 0.0), ("   ", 0.0)],
)
def test_parse_magvar(field, expected):
    assert parse_magvar(field) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["W12A4", "Z0120", "12345"])
def test_parse_magvar_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_magvar(bad)


# --------------------------------------------------------------------------- #
# frequencies                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field, mhz", [("11590", 115.90), ("11770", 117.70), ("11360", 113.60), ("10820", 108.20)]
)
def test_parse_vhf_freq(field, mhz):
    assert parse_vhf_freq_mhz(field) == pytest.approx(mhz)


@pytest.mark.parametrize("field, khz", [("03650", 365.0), ("04100", 410.0), ("02000", 200.0)])
def test_parse_ndb_freq(field, khz):
    assert parse_ndb_freq_khz(field) == pytest.approx(khz)


def test_freq_parsers_reject_nondigit():
    with pytest.raises(ValueError):
        parse_vhf_freq_mhz("11A90")
    with pytest.raises(ValueError):
        parse_ndb_freq_khz("")


# --------------------------------------------------------------------------- #
# opt_int                                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field, expected", [("00117", 117), (" 42 ", 42), ("", None), ("   ", None)])
def test_opt_int(field, expected):
    assert opt_int(field) == expected
