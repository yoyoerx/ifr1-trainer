"""Pure navigation geometry. No I/O, stdlib only.

Everything else in the trainer builds on this module, so it is deliberately
self-contained and heavily unit-tested.

Conventions (see ARCHITECTURE.md sec. 4):

* Distances in nautical miles, speeds in knots, angles in degrees ``0..360``.
* Spherical earth, radius ``EARTH_RADIUS_NM``. Sub-knot error at GA ranges.
* ``Point(lat, lon)`` in decimal degrees, north/east positive.
* Bearings are TRUE unless the name carries ``_mag``.
* Cross-track sign: ``+`` = the point/ownship is to the RIGHT of the A->B course,
  ``-`` = to the LEFT.
* Wind direction is meteorological: the direction wind blows FROM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_NM = 3440.065  # mean earth radius, 6371.0088 km / 1.852

__all__ = [
    "EARTH_RADIUS_NM",
    "Point",
    "norm360",
    "norm180",
    "angle_diff",
    "reciprocal",
    "great_circle_nm",
    "initial_bearing",
    "final_bearing",
    "destination",
    "cross_track_nm",
    "along_track_nm",
    "radial_dme",
    "intersect_radials",
    "wind_triangle",
    "wind_components",
    "turn_radius_nm",
    "standard_rate_turn_radius_nm",
    "turn_anticipation_nm",
    "hold_entry",
    "point_in_polygon",
    "distance_to_polygon_nm",
]


@dataclass(frozen=True)
class Point:
    """A geographic position in decimal degrees (north/east positive)."""

    lat: float
    lon: float

    def __iter__(self):  # allows: lat, lon = point
        yield self.lat
        yield self.lon


# --------------------------------------------------------------------------- #
# angle helpers                                                              #
# --------------------------------------------------------------------------- #
def norm360(deg: float) -> float:
    """Wrap an angle to ``[0, 360)``."""
    return deg % 360.0


def norm180(deg: float) -> float:
    """Wrap an angle to ``(-180, 180]``."""
    d = (deg + 180.0) % 360.0 - 180.0
    return d + 360.0 if d <= -180.0 else d


def angle_diff(a: float, b: float) -> float:
    """Signed smallest rotation from ``b`` to ``a``, in ``(-180, 180]``.

    Positive means ``a`` is clockwise of ``b``.
    """
    return norm180(a - b)


def reciprocal(deg: float) -> float:
    """The opposite direction, wrapped to ``[0, 360)``."""
    return norm360(deg + 180.0)


# --------------------------------------------------------------------------- #
# great-circle primitives                                                    #
# --------------------------------------------------------------------------- #
def great_circle_nm(a: Point, b: Point) -> float:
    """Great-circle distance between two points, in nautical miles."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlam = math.radians(b.lon - a.lon)
    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_NM * math.asin(math.sqrt(h))


def initial_bearing(a: Point, b: Point) -> float:
    """True course at ``a`` for the great circle to ``b``, ``[0, 360)``."""
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dlam = math.radians(b.lon - a.lon)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return norm360(math.degrees(math.atan2(y, x)))


def final_bearing(a: Point, b: Point) -> float:
    """True course when arriving at ``b`` from ``a``, ``[0, 360)``."""
    return reciprocal(initial_bearing(b, a))


def destination(origin: Point, bearing_deg: float, dist_nm: float) -> Point:
    """Point reached by travelling ``dist_nm`` from ``origin`` on ``bearing_deg``."""
    delta = dist_nm / EARTH_RADIUS_NM
    theta = math.radians(bearing_deg)
    phi1 = math.radians(origin.lat)
    lam1 = math.radians(origin.lon)

    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * sin_phi2
    lam2 = lam1 + math.atan2(y, x)
    return Point(math.degrees(phi2), norm180(math.degrees(lam2)))


def cross_track_nm(a: Point, b: Point, p: Point) -> float:
    """Signed distance of ``p`` from the great circle ``a -> b``.

    ``+`` = ``p`` lies to the RIGHT of the course, ``-`` = to the LEFT.
    """
    delta13 = great_circle_nm(a, p) / EARTH_RADIUS_NM
    theta13 = math.radians(initial_bearing(a, p))
    theta12 = math.radians(initial_bearing(a, b))
    xt = math.asin(
        max(-1.0, min(1.0, math.sin(delta13) * math.sin(theta13 - theta12)))
    )
    return xt * EARTH_RADIUS_NM


def along_track_nm(a: Point, b: Point, p: Point) -> float:
    """Distance from ``a`` to the foot of ``p``'s perpendicular onto ``a -> b``.

    Negative if that foot lies *behind* ``a`` (i.e. ``p`` is abeam or short of
    the start of the leg).
    """
    delta13 = great_circle_nm(a, p) / EARTH_RADIUS_NM
    dxt = cross_track_nm(a, b, p) / EARTH_RADIUS_NM
    ratio = math.cos(delta13) / math.cos(dxt)
    dat = math.acos(max(-1.0, min(1.0, ratio))) * EARTH_RADIUS_NM
    if abs(angle_diff(initial_bearing(a, p), initial_bearing(a, b))) > 90.0:
        return -dat
    return dat


# --------------------------------------------------------------------------- #
# constant-radius arcs (DME arcs, ARINC AF / RF legs)                          #
# --------------------------------------------------------------------------- #
# An arc leg is flown around ``centre`` at ``radius`` nm. ``turn`` is the
# direction of travel: "R" = clockwise (centre on the right), "L" = counter-
# clockwise. Cross-track keeps the module-wide sign: + == ownship RIGHT of the
# path, so for a clockwise arc, being inside the arc (nearer the centre) is +.
def arc_sweep_deg(centre: Point, start: Point, end: Point, turn: str) -> float:
    """Unsigned angle (0..360) swept from ``start`` to ``end`` about ``centre``
    in the direction of travel."""
    b0 = initial_bearing(centre, start)
    b1 = initial_bearing(centre, end)
    return norm360(b1 - b0) if turn != "L" else norm360(b0 - b1)


def arc_length_nm(centre: Point, start: Point, end: Point, turn: str) -> float:
    r = great_circle_nm(centre, end)
    return math.radians(arc_sweep_deg(centre, start, end, turn)) * r


def arc_track(centre: Point, turn: str, p: Point) -> float:
    """True course of the arc at ``p`` (tangent, in the direction of travel)."""
    return norm360(initial_bearing(centre, p) + (-90.0 if turn == "L" else 90.0))


def arc_xtk_nm(centre: Point, turn: str, radius_nm: float, p: Point) -> float:
    """Cross-track from the arc: + == ownship right of the path."""
    inside = radius_nm - great_circle_nm(centre, p)
    return -inside if turn == "L" else inside


def arc_progress_deg(centre: Point, start: Point, turn: str, p: Point) -> float:
    """Angle flown along the arc from ``start`` to the foot of ``p``, -180..180
    (negative == ``p`` is still short of the start)."""
    b0 = initial_bearing(centre, start)
    b = initial_bearing(centre, p)
    return norm180(b - b0) if turn != "L" else norm180(b0 - b)


def arc_points(centre: Point, start: Point, end: Point, turn: str,
               step_deg: float = 4.0) -> list[Point]:
    """Points along the arc from ``start`` to ``end`` inclusive (for drawing)."""
    r = great_circle_nm(centre, end)
    b0 = initial_bearing(centre, start)
    sweep = arc_sweep_deg(centre, start, end, turn)
    n = max(1, int(math.ceil(sweep / step_deg)))
    sgn = -1.0 if turn == "L" else 1.0
    return [start] + [destination(centre, norm360(b0 + sgn * sweep * k / n), r)
                      for k in range(1, n)] + [end]


# --------------------------------------------------------------------------- #
# fixes from navaids                                                         #
# --------------------------------------------------------------------------- #
def radial_dme(
    station: Point, radial_mag_deg: float, dme_nm: float, station_magvar_deg: float = 0.0
) -> Point:
    """Position on a VOR ``radial`` (magnetic, FROM the station) at ``dme_nm``.

    ``station_magvar_deg`` is added to the magnetic radial to get true bearing;
    pass it with the sign used by ``earth_nav.dat``.
    """
    true_brg = norm360(radial_mag_deg + station_magvar_deg)
    return destination(station, true_brg, dme_nm)


def intersect_radials(
    p1: Point, bearing1_deg: float, p2: Point, bearing2_deg: float
) -> Point | None:
    """Intersection of the great circle leaving ``p1`` on ``bearing1_deg`` with
    the one leaving ``p2`` on ``bearing2_deg``.

    Returns ``None`` when the paths are colinear or the geometry is ambiguous
    (the intersection would be on the reciprocal of one of the bearings).
    """
    phi1, lam1 = math.radians(p1.lat), math.radians(p1.lon)
    phi2, lam2 = math.radians(p2.lat), math.radians(p2.lon)
    dphi = phi2 - phi1
    dlam = lam2 - lam1

    delta12 = 2.0 * math.asin(
        math.sqrt(
            math.sin(dphi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
        )
    )
    if delta12 == 0.0:
        return None

    cos_theta_a = (math.sin(phi2) - math.sin(phi1) * math.cos(delta12)) / (
        math.sin(delta12) * math.cos(phi1)
    )
    cos_theta_b = (math.sin(phi1) - math.sin(phi2) * math.cos(delta12)) / (
        math.sin(delta12) * math.cos(phi2)
    )
    theta_a = math.acos(max(-1.0, min(1.0, cos_theta_a)))
    theta_b = math.acos(max(-1.0, min(1.0, cos_theta_b)))

    if math.sin(dlam) > 0.0:
        theta12 = theta_a
        theta21 = 2.0 * math.pi - theta_b
    else:
        theta12 = 2.0 * math.pi - theta_a
        theta21 = theta_b

    alpha1 = math.radians(bearing1_deg) - theta12
    alpha2 = theta21 - math.radians(bearing2_deg)
    sin_a1, sin_a2 = math.sin(alpha1), math.sin(alpha2)

    if abs(sin_a1) < 1e-9 and abs(sin_a2) < 1e-9:
        return None  # colinear: infinitely many intersections
    if sin_a1 * sin_a2 < 0.0:
        return None  # intersection would fall behind one of the radials

    alpha3 = math.acos(
        max(
            -1.0,
            min(
                1.0,
                -math.cos(alpha1) * math.cos(alpha2)
                + math.sin(alpha1) * math.sin(alpha2) * math.cos(delta12),
            ),
        )
    )
    delta13 = math.atan2(
        math.sin(delta12) * math.sin(alpha1) * math.sin(alpha2),
        math.cos(alpha2) + math.cos(alpha1) * math.cos(alpha3),
    )
    phi3 = math.asin(
        max(
            -1.0,
            min(
                1.0,
                math.sin(phi1) * math.cos(delta13)
                + math.cos(phi1) * math.sin(delta13) * math.cos(math.radians(bearing1_deg)),
            ),
        )
    )
    dlam13 = math.atan2(
        math.sin(math.radians(bearing1_deg)) * math.sin(delta13) * math.cos(phi1),
        math.cos(delta13) - math.sin(phi1) * math.sin(phi3),
    )
    lam3 = lam1 + dlam13
    return Point(math.degrees(phi3), norm180(math.degrees(lam3)))


# --------------------------------------------------------------------------- #
# wind triangle                                                              #
# --------------------------------------------------------------------------- #
def wind_components(course_deg: float, wind_from_deg: float, wind_kt: float) -> tuple[float, float]:
    """Head/tail and cross components of the wind relative to ``course_deg``.

    Returns ``(headwind, crosswind)``:
      * ``headwind``  ``+`` on the nose, ``-`` a tailwind.
      * ``crosswind`` ``+`` from the right, ``-`` from the left.
    """
    angle = math.radians(wind_from_deg - course_deg)
    return wind_kt * math.cos(angle), wind_kt * math.sin(angle)


def wind_triangle(
    tas_kt: float, course_deg: float, wind_from_deg: float, wind_kt: float
) -> tuple[float, float, float]:
    """Solve the wind triangle for a desired ground track.

    Returns ``(heading_deg, ground_speed_kt, wca_deg)`` where ``wca`` is the
    wind-correction angle (``+`` = crab right). ``ground_speed`` can be negative
    only in the degenerate case of wind faster than TAS from ahead — callers
    flying real profiles never hit that.
    """
    _, crosswind = wind_components(course_deg, wind_from_deg, wind_kt)
    sin_wca = max(-1.0, min(1.0, crosswind / tas_kt)) if tas_kt else 0.0
    wca = math.degrees(math.asin(sin_wca))
    heading = norm360(course_deg + wca)
    headwind, _ = wind_components(course_deg, wind_from_deg, wind_kt)
    gs = tas_kt * math.cos(math.radians(wca)) - headwind
    return heading, gs, wca


# --------------------------------------------------------------------------- #
# turn geometry                                                              #
# --------------------------------------------------------------------------- #
def turn_radius_nm(gs_kt: float, bank_deg: float = 25.0) -> float:
    """Radius of a level, constant-bank turn."""
    if gs_kt <= 0.0:
        return 0.0
    radius_ft = gs_kt**2 / (11.26 * math.tan(math.radians(bank_deg)))
    return radius_ft / 6076.12


def standard_rate_turn_radius_nm(gs_kt: float) -> float:
    """Radius of a 3 deg/sec (standard rate) turn."""
    if gs_kt <= 0.0:
        return 0.0
    # v (nm/s) / omega (rad/s), omega = 3 deg/s
    return (gs_kt / 3600.0) / math.radians(3.0)


def turn_anticipation_nm(gs_kt: float, course_change_deg: float, bank_deg: float = 25.0) -> float:
    """Distance before a waypoint to start the turn so as to roll out on the
    next course, for a ``course_change`` of the given magnitude.
    """
    change = abs(norm180(course_change_deg))
    if change < 1e-6:
        return 0.0
    return turn_radius_nm(gs_kt, bank_deg) * math.tan(math.radians(change / 2.0))


# --------------------------------------------------------------------------- #
# holding entry                                                              #
# --------------------------------------------------------------------------- #
def hold_entry(inbound_course_deg: float, heading_deg: float, turn: str = "R") -> str:
    """Recommended holding-pattern entry.

    ``inbound_course_deg`` is the course TO the holding fix on the inbound leg.
    ``turn`` is ``"R"`` for a standard (right-turn) pattern, ``"L"`` for
    non-standard. Returns ``"direct"``, ``"teardrop"`` or ``"parallel"``.

    Sector model (right-hand pattern): the 180 deg arc from the inbound course
    clockwise to its reciprocal is DIRECT; of the remaining half, the 70 deg
    adjacent to the outbound direction is TEARDROP and the other 110 deg is
    PARALLEL. Left-hand patterns mirror this. Boundaries resolve toward the
    entry that keeps the aircraft on the holding side. Advisory only.
    """
    t = turn.strip().upper()
    if t not in ("R", "L"):
        raise ValueError("turn must be 'R' or 'L'")

    recip = reciprocal(inbound_course_deg)
    if t == "R":
        from_course = norm360(heading_deg - inbound_course_deg)  # CW from inbound
        from_recip = norm360(heading_deg - recip)                # CW from outbound
        if from_course < 180.0:
            return "direct"
        return "teardrop" if from_recip < 70.0 else "parallel"
    else:
        to_course = norm360(inbound_course_deg - heading_deg)  # CCW from inbound
        to_recip = norm360(recip - heading_deg)                # CCW from outbound
        if to_course < 180.0:
            return "direct"
        return "teardrop" if to_recip < 70.0 else "parallel"


# --------------------------------------------------------------------------- #
# polygon geometry (airspace boundaries: simple lat/lon rings, GA-scale areas
# small enough that an equirectangular projection local to the polygon is
# plenty accurate - no need for great-circle-correct polygon math here)
# --------------------------------------------------------------------------- #
def point_in_polygon(pt: Point, rings: list[list[Point]]) -> bool:
    """True if ``pt`` is inside the (possibly multi-ring) polygon, even-odd
    rule - correct for a simple outer ring plus disjoint holes, which is all
    FAA Class Airspace shapes are."""
    inside = False
    x, y = pt.lon, pt.lat
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        for i in range(n):
            x1, y1 = ring[i].lon, ring[i].lat
            x2, y2 = ring[(i + 1) % n].lon, ring[(i + 1) % n].lat
            if (y1 > y) != (y2 > y):
                x_at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < x_at:
                    inside = not inside
    return inside


def _dist_to_segment_nm(pt: Point, a: Point, b: Point) -> float:
    clat = max(math.cos(math.radians(pt.lat)), 0.01)
    px, py = pt.lon * clat, pt.lat
    ax, ay = a.lon * clat, a.lat
    bx, by = b.lon * clat, b.lat
    dx, dy = bx - ax, by - ay
    if dx == dy == 0.0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy) * 60.0   # 1 deg lat = 60 nm


def distance_to_polygon_nm(pt: Point, rings: list[list[Point]]) -> float:
    """Distance from ``pt`` to the nearest edge of the polygon, ``0.0`` when
    ``pt`` is inside it. A flat local-equirectangular approximation (see
    ``_dist_to_segment_nm``) - fine at the size of a Class B/C/D boundary."""
    if point_in_polygon(pt, rings):
        return 0.0
    best = math.inf
    for ring in rings:
        n = len(ring)
        for i in range(n):
            best = min(best, _dist_to_segment_nm(pt, ring[i], ring[(i + 1) % n]))
    return best
