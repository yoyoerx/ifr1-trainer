"""Unit tests for navmath.

Reference values are either analytic (1 deg of latitude = ~60 nm, equator/meridian
bearings) or round-trip properties (project then measure back), so the suite needs
no external navigation library.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import (  # noqa: E402
    EARTH_RADIUS_NM,
    Point,
    along_track_nm,
    angle_diff,
    cross_track_nm,
    destination,
    distance_to_polygon_nm,
    final_bearing,
    great_circle_nm,
    hold_entry,
    initial_bearing,
    intersect_radials,
    norm180,
    point_in_polygon,
    norm360,
    radial_dme,
    reciprocal,
    standard_rate_turn_radius_nm,
    turn_anticipation_nm,
    turn_radius_nm,
    wind_components,
    wind_triangle,
)

NM_PER_DEG = math.radians(1.0) * EARTH_RADIUS_NM  # ~60.04


# --------------------------------------------------------------------------- #
# angle helpers                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [(370, 10), (-10, 350), (360, 0), (0, 0), (720.5, 0.5)],
)
def test_norm360(value, expected):
    assert norm360(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value, expected",
    [(190, -170), (-190, 170), (180, 180), (-180, 180), (0, 0), (45, 45)],
)
def test_norm180(value, expected):
    assert norm180(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "a, b, expected",
    [(10, 350, 20), (350, 10, -20), (0, 180, 180), (90, 90, 0)],
)
def test_angle_diff(a, b, expected):
    assert angle_diff(a, b) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value, expected", [(90, 270), (270, 90), (0, 180), (360, 180), (200, 20)]
)
def test_reciprocal(value, expected):
    assert reciprocal(value) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# great-circle primitives                                                    #
# --------------------------------------------------------------------------- #
def test_distance_one_degree_latitude():
    assert great_circle_nm(Point(0, 0), Point(1, 0)) == pytest.approx(NM_PER_DEG, abs=0.05)


def test_distance_one_degree_longitude_at_equator():
    assert great_circle_nm(Point(0, 0), Point(0, 1)) == pytest.approx(NM_PER_DEG, abs=0.05)


def test_distance_zero():
    assert great_circle_nm(Point(51.47, -0.45), Point(51.47, -0.45)) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (Point(0, 0), Point(0, 1), 90.0),   # east along the equator
        (Point(0, 0), Point(1, 0), 0.0),    # north along a meridian
        (Point(0, 0), Point(0, -1), 270.0), # west
        (Point(0, 0), Point(-1, 0), 180.0), # south
    ],
)
def test_initial_bearing_cardinal(a, b, expected):
    assert initial_bearing(a, b) == pytest.approx(expected, abs=1e-6)


def test_final_bearing_along_meridian():
    # a meridian is a great circle; bearing stays 0 the whole way
    assert final_bearing(Point(0, 0), Point(80, 0)) == pytest.approx(0.0, abs=1e-6)


def test_final_bearing_along_equator():
    assert final_bearing(Point(0, 0), Point(0, 40)) == pytest.approx(90.0, abs=1e-6)


@pytest.mark.parametrize(
    "origin, bearing, dist",
    [
        (Point(0, 0), 90.0, 60.0),
        (Point(0, 0), 0.0, 60.0),
        (Point(40.64, -73.78), 66.0, 1800.0),
        (Point(-33.95, 151.18), 300.0, 450.0),
    ],
)
def test_destination_roundtrip(origin, bearing, dist):
    p = destination(origin, bearing, dist)
    assert great_circle_nm(origin, p) == pytest.approx(dist, rel=1e-6)
    assert angle_diff(initial_bearing(origin, p), bearing) == pytest.approx(0.0, abs=1e-4)


def test_destination_east_of_equator():
    p = destination(Point(0, 0), 90.0, NM_PER_DEG)
    assert p.lat == pytest.approx(0.0, abs=1e-6)
    assert p.lon == pytest.approx(1.0, abs=1e-4)


# --------------------------------------------------------------------------- #
# cross-track / along-track                                                   #
# --------------------------------------------------------------------------- #
def test_cross_track_sign_and_magnitude():
    a, b = Point(0, 0), Point(0, 10)  # eastbound along the equator
    left = cross_track_nm(a, b, Point(1, 5))    # north of an eastbound course = left
    right = cross_track_nm(a, b, Point(-1, 5))
    assert left == pytest.approx(-NM_PER_DEG, abs=0.5)
    assert right == pytest.approx(NM_PER_DEG, abs=0.5)


def test_cross_track_on_course_is_zero():
    a, b = Point(0, 0), Point(0, 10)
    assert cross_track_nm(a, b, Point(0, 4)) == pytest.approx(0.0, abs=1e-6)


def test_along_track_forward():
    a, b = Point(0, 0), Point(0, 10)
    assert along_track_nm(a, b, Point(0.5, 5)) == pytest.approx(5 * NM_PER_DEG, abs=1.0)


def test_along_track_behind_start_is_negative():
    a, b = Point(0, 0), Point(0, 10)
    dat = along_track_nm(a, b, Point(0, -1))
    assert dat == pytest.approx(-NM_PER_DEG, abs=0.5)


# --------------------------------------------------------------------------- #
# fixes from navaids                                                         #
# --------------------------------------------------------------------------- #
def test_radial_dme_cardinal():
    station = Point(0, 0)
    east = radial_dme(station, 90.0, NM_PER_DEG)
    north = radial_dme(station, 0.0, NM_PER_DEG)
    assert (east.lat, east.lon) == pytest.approx((0.0, 1.0), abs=1e-3)
    assert (north.lat, north.lon) == pytest.approx((1.0, 0.0), abs=1e-3)


def test_radial_dme_applies_magvar():
    station = Point(0, 0)
    # radial 080 magnetic + 10 deg variation = 090 true
    p = radial_dme(station, 80.0, NM_PER_DEG, station_magvar_deg=10.0)
    assert (p.lat, p.lon) == pytest.approx((0.0, 1.0), abs=1e-3)


def test_intersect_radials_symmetric():
    p = intersect_radials(Point(0, -1), 45.0, Point(0, 1), 315.0)
    assert p is not None
    assert (p.lat, p.lon) == pytest.approx((1.0, 0.0), abs=0.05)


def test_intersect_radials_parallel_returns_none():
    assert intersect_radials(Point(0, 0), 90.0, Point(0, 5), 90.0) is None


# --------------------------------------------------------------------------- #
# wind triangle                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "course, wind_from, wind_kt, exp_head, exp_cross",
    [
        (360, 360, 20, 20, 0),    # straight headwind
        (360, 180, 20, -20, 0),   # straight tailwind
        (360, 90, 20, 0, 20),     # wind from the right
        (360, 270, 20, 0, -20),   # wind from the left
    ],
)
def test_wind_components(course, wind_from, wind_kt, exp_head, exp_cross):
    head, cross = wind_components(course, wind_from, wind_kt)
    assert head == pytest.approx(exp_head, abs=1e-6)
    assert cross == pytest.approx(exp_cross, abs=1e-6)


def test_wind_triangle_direct_headwind():
    hdg, gs, wca = wind_triangle(100, 360, 360, 20)
    assert wca == pytest.approx(0.0, abs=1e-6)
    assert hdg == pytest.approx(0.0, abs=1e-6)
    assert gs == pytest.approx(80.0, abs=1e-6)


def test_wind_triangle_crosswind_crab_and_gs():
    hdg, gs, wca = wind_triangle(100, 360, 90, 20)
    assert wca == pytest.approx(math.degrees(math.asin(0.2)), abs=1e-6)
    assert hdg == pytest.approx(wca, abs=1e-6)  # course 360 -> heading == wca
    assert gs == pytest.approx(100 * math.cos(math.radians(wca)), abs=1e-6)


def test_wind_triangle_tailwind_increases_gs():
    _, gs, _ = wind_triangle(100, 360, 180, 20)
    assert gs == pytest.approx(120.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# turn geometry                                                              #
# --------------------------------------------------------------------------- #
def test_turn_radius_25_bank_120kt():
    assert turn_radius_nm(120, 25) == pytest.approx(0.4514, abs=0.005)


def test_turn_radius_zero_speed():
    assert turn_radius_nm(0) == 0.0


def test_standard_rate_turn_radius_120kt():
    assert standard_rate_turn_radius_nm(120) == pytest.approx(0.6366, abs=0.002)


def test_turn_anticipation_90_deg():
    # 90 deg turn: anticipation distance == turn radius
    assert turn_anticipation_nm(120, 90, 25) == pytest.approx(turn_radius_nm(120, 25), abs=1e-6)


def test_turn_anticipation_no_course_change():
    assert turn_anticipation_nm(120, 0) == 0.0


def test_turn_anticipation_uses_shortest_angle():
    # a 350 deg "change" is really 10 deg
    assert turn_anticipation_nm(120, 350, 25) == pytest.approx(
        turn_anticipation_nm(120, 10, 25), abs=1e-9
    )


# --------------------------------------------------------------------------- #
# holding entry                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "inbound, heading, turn, expected",
    [
        (360, 90, "R", "direct"),
        (360, 0, "R", "direct"),
        (360, 175, "R", "direct"),
        (360, 200, "R", "teardrop"),
        (360, 180, "R", "teardrop"),
        (360, 300, "R", "parallel"),
        (360, 340, "R", "parallel"),
        # left-hand pattern mirrors the right-hand one
        (360, 270, "L", "direct"),
        (360, 360, "L", "direct"),
        (360, 160, "L", "teardrop"),
        (360, 60, "L", "parallel"),
    ],
)
def test_hold_entry(inbound, heading, turn, expected):
    assert hold_entry(inbound, heading, turn) == expected


def test_hold_entry_rejects_bad_turn():
    with pytest.raises(ValueError):
        hold_entry(360, 90, "X")


# --------------------------------------------------------------------------- #
# constant-radius arcs                                                        #
# --------------------------------------------------------------------------- #
def _arc_fixture():
    from navmath import Point, destination
    centre = Point(40.0, -74.0)
    start = destination(centre, 0.0, 15.0)      # north of the navaid
    end = destination(centre, 90.0, 15.0)       # east of it
    return centre, start, end


def test_arc_sweep_and_length_follow_the_direction_of_travel():
    from navmath import arc_length_nm, arc_sweep_deg
    c, s, e = _arc_fixture()
    assert arc_sweep_deg(c, s, e, "R") == pytest.approx(90.0, abs=0.1)     # clockwise N -> E
    assert arc_sweep_deg(c, s, e, "L") == pytest.approx(270.0, abs=0.1)    # the long way round
    assert arc_length_nm(c, s, e, "R") == pytest.approx(math.radians(90.0) * 15.0, abs=0.05)


def test_arc_track_is_the_tangent_and_xtk_is_off_the_circle():
    from navmath import arc_track, arc_xtk_nm, destination
    c, s, _ = _arc_fixture()
    assert arc_track(c, "R", s) == pytest.approx(90.0, abs=0.1)     # N of centre, clockwise -> east
    assert arc_track(c, "L", s) == pytest.approx(270.0, abs=0.1)
    inside = destination(c, 0.0, 14.0)                              # nearer the navaid
    assert arc_xtk_nm(c, "R", 15.0, inside) == pytest.approx(1.0, abs=0.01)    # right of a CW arc
    assert arc_xtk_nm(c, "L", 15.0, inside) == pytest.approx(-1.0, abs=0.01)   # left of a CCW arc


def test_arc_points_lie_on_the_circle():
    from navmath import arc_points, great_circle_nm
    c, s, e = _arc_fixture()
    pts = arc_points(c, s, e, "R")
    assert pts[0] == s and pts[-1] == e and len(pts) > 20
    assert all(abs(great_circle_nm(c, p) - 15.0) < 0.05 for p in pts)


# --------------------------------------------------------------------------- #
# polygon geometry (airspace boundaries, F62)                                #
# --------------------------------------------------------------------------- #
def test_point_in_polygon_simple_square():
    square = [Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0)]
    assert point_in_polygon(Point(0.5, 0.5), [square])
    assert not point_in_polygon(Point(2.0, 2.0), [square])
    assert not point_in_polygon(Point(0.5, 1.5), [square])


def test_point_in_polygon_with_a_hole():
    outer = [Point(0.0, 0.0), Point(4.0, 0.0), Point(4.0, 4.0), Point(0.0, 4.0)]
    hole = [Point(1.0, 1.0), Point(3.0, 1.0), Point(3.0, 3.0), Point(1.0, 3.0)]
    assert point_in_polygon(Point(0.5, 0.5), [outer, hole])       # in the outer ring, outside the hole
    assert not point_in_polygon(Point(2.0, 2.0), [outer, hole])   # inside the hole -> not "in" the shape


def test_distance_to_polygon_zero_when_inside():
    square = [Point(39.0, -77.0), Point(39.0, -76.0), Point(40.0, -76.0), Point(40.0, -77.0)]
    assert distance_to_polygon_nm(Point(39.5, -76.5), [square]) == 0.0


def test_distance_to_polygon_positive_outside_and_roughly_right():
    # a 1x1 degree square near 39N; 0.5 deg east of its edge is ~26 nm (60nm/deg * cos(39))
    square = [Point(39.0, -77.0), Point(39.0, -76.0), Point(40.0, -76.0), Point(40.0, -77.0)]
    d = distance_to_polygon_nm(Point(39.5, -75.5), [square])
    assert 20.0 < d < 30.0


def test_distance_to_polygon_empty_rings_is_infinite_and_not_inside():
    assert distance_to_polygon_nm(Point(0.0, 0.0), []) == float("inf")
