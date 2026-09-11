"""Unit tests for navdata.nasr (NASR CSV comm-frequency merge)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point  # noqa: E402
from navdata.model import Airport, NavDatabase  # noqa: E402
from navdata.nasr import (  # noqa: E402
    _category,
    _vhf_voice,
    faa_icao_crosswalk,
    load_frequencies,
    merge_comms,
)

APT_BASE = """EFF_DATE,ARPT_ID,ICAO_ID,ARPT_NAME
2026/09/03,TST,KTST,TEST TOWERED
2026/09/03,0J0,,ABBEVILLE MUNI
"""

FRQ = """EFF_DATE,SERVICED_FACILITY,FREQ_USE,FREQ
2026/09/03,TST,LCL/P,119.1
2026/09/03,TST,LCL/P,119.1
2026/09/03,TST,GND/P,121.9
2026/09/03,TST,D-ATIS,128.7
2026/09/03,TST,CLASS B,125.0
2026/09/03,TST,EMERG,121.5
2026/09/03,TST,APCH/P DEP/P,124.35
2026/09/03,TST,JFK VOR/DME,115.9/106X
2026/09/03,0J0,CTAF,122.8
2026/09/03,0J0,UNICOM,122.8
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "APT_BASE.csv").write_text(APT_BASE, encoding="utf-8")
    (tmp_path / "FRQ.csv").write_text(FRQ, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# field parsers                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("121.9", 121.9),
        ("119.100", 119.1),
        ("115.9/106X", None),   # nav band -> rejected
        ("108.5", None),        # nav band
        ("243.0", None),        # UHF
        ("", None),
        ("n/a", None),
        ("136.975", 136.975),
    ],
)
def test_vhf_voice(raw, expected):
    assert _vhf_voice(raw) == expected


@pytest.mark.parametrize(
    "use, cat",
    [
        ("LCL/P", "TWR"), ("LCL/S", "TWR"),
        ("GND/P", "GND"),
        ("D-ATIS", "ATIS"), ("ATIS", "ATIS"),
        ("CD/P", "CLNC"), ("CLNC DEL", "CLNC"),
        ("APCH/P DEP/P", "APP"), ("DEP/P", "DEP"),
        ("CTAF", "CTAF"), ("UNICOM", "UNICOM"),
        ("CLASS B", None), ("EMERG", None), ("RAMP CTL", None),
    ],
)
def test_category(use, cat):
    assert _category(use) == cat


# --------------------------------------------------------------------------- #
# csv readers                                                                #
# --------------------------------------------------------------------------- #
def test_crosswalk_only_when_icao_present(data_dir):
    assert faa_icao_crosswalk(data_dir) == {"TST": "KTST"}


def test_load_frequencies_filters_and_dedups(data_dir):
    freqs = load_frequencies(data_dir)
    assert freqs["TST"] == {
        "TWR": [119.1],        # duplicate collapsed
        "GND": [121.9],
        "ATIS": [128.7],
        "APP": [124.35],
    }
    assert freqs["0J0"] == {"CTAF": [122.8], "UNICOM": [122.8]}


# --------------------------------------------------------------------------- #
# merge                                                                      #
# --------------------------------------------------------------------------- #
def test_merge_comms_matches_icao_and_faa_keyed_airports(data_dir):
    db = NavDatabase()
    db.add_airport(Airport("KTST", Point(40, -74)))   # towered -> match via crosswalk
    db.add_airport(Airport("0J0", Point(31, -85)))    # small field -> match directly
    db.add_airport(Airport("KXXX", Point(0, 0)))      # no NASR data

    n = merge_comms(db, data_dir)
    assert n == 2

    ktst = db.airport("KTST")
    assert ktst.comm("TWR") == 119.1
    assert ktst.comm("ATIS") == 128.7
    assert ktst.comm("APP") == 124.35
    assert db.airport("0J0").comm("CTAF") == 122.8
    assert db.airport("KXXX").comms == {}
    assert db.counts()["airport_comms"] == 2


def test_merge_comms_missing_csvs_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        merge_comms(NavDatabase(), tmp_path)


def test_airport_comm_helper_prefers_first_listed():
    a = Airport("K1", Point(0, 0), comms={"TWR": [120.0, 121.0]})
    assert a.comm("TWR") == 120.0
    assert a.comm("GND") is None
    assert a.comm("GND", "TWR") == 120.0   # falls through to the next use
