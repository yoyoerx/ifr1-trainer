"""NavDatabase lookup / nearest-N behaviour, built from hand-placed fixtures."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point  # noqa: E402
from navdata.model import (  # noqa: E402
    Airport,
    NavDatabase,
    NdbNavaid,
    Procedure,
    VhfNavaid,
    Waypoint,
)

REF = Point(40.0, -74.0)


@pytest.fixture
def db():
    d = NavDatabase(source="test")
    d.add_vhf(VhfNavaid("AAA", Point(40.0, -74.0), 113.0))          # 0 nm
    d.add_vhf(VhfNavaid("BBB", Point(41.0, -74.0), 114.0))          # ~60 nm N
    d.add_vhf(VhfNavaid("CCC", Point(45.0, -74.0), 115.0))          # ~300 nm N
    d.add_ndb(NdbNavaid("NNN", Point(40.1, -74.0), 350.0))          # ~6 nm N
    d.add_waypoint(Waypoint("WPT1", Point(40.05, -74.0)))          # ~3 nm N
    d.add_airport(Airport("K001", Point(40.0, -74.0)))              # 0 nm
    d.add_airport(Airport("K002", Point(40.0, -75.0)))              # ~46 nm W
    d.add_airport(Airport("K003", Point(50.0, -74.0)))              # ~600 nm N
    return d


def _idents(items):
    return [it.ident for it in items]


def test_nearest_navaids_orders_by_distance(db):
    assert _idents(db.nearest_navaids(REF, n=3)) == ["AAA", "NNN", "BBB"]


def test_nearest_navaids_respects_n(db):
    assert len(db.nearest_navaids(REF, n=2)) == 2


def test_nearest_navaids_max_nm_filter(db):
    got = db.nearest_navaids(REF, n=10, max_nm=100)
    assert _idents(got) == ["AAA", "NNN", "BBB"]      # CCC at ~300 nm excluded


def test_nearest_navaids_kind_toggle(db):
    assert _idents(db.nearest_navaids(REF, n=10, ndb=False)) == ["AAA", "BBB", "CCC"]
    assert _idents(db.nearest_navaids(REF, n=10, vhf=False)) == ["NNN"]


def test_nearest_airports(db):
    assert _idents(db.nearest_airports(REF, n=2)) == ["K001", "K002"]
    assert _idents(db.nearest_airports(REF, max_nm=100)) == ["K001", "K002"]  # K003 far


def test_nearest_waypoints(db):
    assert _idents(db.nearest_waypoints(REF, n=1)) == ["WPT1"]


def test_bbox_prefilter_matches_full_scan(db):
    # same result whether or not the cheap prefilter runs
    assert _idents(db.nearest_navaids(REF, n=10)) == _idents(
        db.nearest_navaids(REF, n=10, max_nm=100_000)
    )


# -- find / nearest_fix ---------------------------------------------------- #
def test_find_across_types_sorted_by_distance():
    d = NavDatabase()
    d.add_vhf(VhfNavaid("ABC", Point(41.0, -74.0), 113.0))
    d.add_waypoint(Waypoint("ABC", Point(40.0, -74.0)))
    hits = d.find("ABC", near=REF)
    assert [type(h).__name__ for h in hits] == ["Waypoint", "VhfNavaid"]


def test_nearest_fix_picks_closest_same_ident():
    d = NavDatabase()
    d.add_waypoint(Waypoint("DUPE", Point(10.0, 10.0)))
    d.add_waypoint(Waypoint("DUPE", Point(40.1, -74.0)))
    got = d.nearest_fix("DUPE", REF)
    assert got.pos.lat == pytest.approx(40.1)


# -- pickle round-trip (the load() DB cache relies on this) --------------- #
def test_navdatabase_survives_a_pickle_round_trip(db):
    import pickle
    db.add_procedure(Procedure(airport="K001", ident="RNV-Z", kind="approach",
                               route_type="R"))
    back = pickle.loads(pickle.dumps(db, protocol=pickle.HIGHEST_PROTOCOL))
    assert back.counts() == db.counts()
    assert _idents(back.nearest_navaids(REF, n=3)) == _idents(db.nearest_navaids(REF, n=3))
    assert back.procedure("K001", "RNV-Z").kind == "approach"


def test_load_cache_path_is_deterministic_and_option_aware(tmp_path):
    import navdata as nd
    cifp = tmp_path / "FAACIFP18"
    cifp.write_bytes(b"x" * 100)
    a = nd._cache_path(tmp_path, cifp, areas=None, comms=True)
    b = nd._cache_path(tmp_path, cifp, areas=None, comms=True)
    c = nd._cache_path(tmp_path, cifp, areas={"USA"}, comms=True)
    d2 = nd._cache_path(tmp_path, cifp, areas=None, comms=False)
    assert a == b and a != c and a != d2
    assert a.parent == tmp_path and a.name.startswith("navdb-") and a.suffix == ".pkl"


# --------------------------------------------------------------------------- #
# Nearest Center / FSS / Airspace (F62)                                      #
# --------------------------------------------------------------------------- #
def test_nearest_centers_and_fss_sorted_by_distance():
    from navdata.model import CenterSite, Fss

    d = NavDatabase()
    d.add_center(CenterSite(artcc="ZDC", ident="FAR", pos=Point(41.0, -74.0), freqs=((132.0, "HIGH"),)))
    d.add_center(CenterSite(artcc="ZDC", ident="NEAR", pos=Point(40.05, -74.0), freqs=((133.0, "LOW"),)))
    assert [c.ident for c in d.nearest_centers(REF, 5)] == ["NEAR", "FAR"]

    d.add_fss(Fss(fss_id="DCA", voice_call="LEESBURG", ident="FAR", pos=Point(41.0, -74.0)))
    d.add_fss(Fss(fss_id="DCA", voice_call="LEESBURG", ident="NEAR", pos=Point(40.05, -74.0)))
    assert [f.ident for f in d.nearest_fss(REF, 5)] == ["NEAR", "FAR"]


def test_nearest_airspaces_by_boundary_distance_not_centroid():
    from navdata.model import Airspace

    d = NavDatabase()
    # a large area whose centroid is far but whose near edge is close, vs. a small area whose centroid is closer
    big = Airspace(ident="BIG", name="", cls="B", floor_ft=0, ceiling_ft=10000,
                   rings=((Point(40.02, -74.0), Point(40.02, -70.0), Point(45.0, -70.0), Point(45.0, -74.0)),))
    small_far_centroid = Airspace(ident="SMALL", name="", cls="D", floor_ft=0, ceiling_ft=3000,
                                  rings=((Point(41.0, -74.0), Point(41.0, -73.9), Point(41.1, -73.9),
                                          Point(41.1, -74.0)),))
    d.add_airspace(big)
    d.add_airspace(small_far_centroid)
    ordered = d.nearest_airspaces(REF, 2)
    assert ordered[0].ident == "BIG" and ordered[0].distance_nm(REF) < 5.0


def test_nearest_airspaces_zero_distance_when_inside_and_respects_max_nm():
    from navdata.model import Airspace

    d = NavDatabase()
    inside = Airspace(ident="HOME", name="", cls="D", floor_ft=0, ceiling_ft=3000,
                      rings=((Point(39.9, -74.1), Point(39.9, -73.9), Point(40.1, -73.9),
                              Point(40.1, -74.1)),))
    d.add_airspace(inside)
    assert d.nearest_airspaces(REF, 4)[0].distance_nm(REF) == 0.0
    assert d.nearest_airspaces(REF, 4, max_nm=0.001) == [inside]
    far = Airspace(ident="FAR", name="", cls="C", floor_ft=0, ceiling_ft=4000,
                   rings=((Point(0.0, 0.0), Point(0.0, 0.1), Point(0.1, 0.1), Point(0.1, 0.0)),))
    d.add_airspace(far)
    assert d.nearest_airspaces(REF, 4, max_nm=1.0) == [inside]
