"""instruments.py - CDI / HSI / bearing-pointer / DME / marker computation.

Pure functions plus one assembler (:func:`compute_panel`). Given ownship state,
the GPS/RNAV :class:`gns530.NavState`, and whatever is tuned in the VLOC / ADF
receivers, produce a :class:`Panel` snapshot that ``render.py`` draws verbatim.

Sign conventions (ARCHITECTURE.md sec.4):

* cross-track ``+`` == ownship is RIGHT of the desired course.
* CDI ``deflection`` is ``-1..+1`` as a fraction of full scale, ``+`` == the
  needle is deflected RIGHT, i.e. the course is to the right / "fly right".
  Being right of course therefore gives a negative (left) deflection.
* All displayed bearings/courses/headings are **magnetic**
  (``magnetic = true - magvar``; magvar east-positive).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from navmath import (
    Point,
    along_track_nm,
    cross_track_nm,
    destination,
    great_circle_nm,
    initial_bearing,
    norm360,
    reciprocal,
)

__all__ = [
    "Phase",
    "GPS_FULL_SCALE_NM",
    "VOR_FULL_SCALE_DEG",
    "LOC_FULL_SCALE_DEG",
    "GS_FULL_SCALE_DEG",
    "Ownship",
    "TunedNav",
    "TunedAdf",
    "MarkerBeacon",
    "CDI",
    "BearingPointer",
    "DME",
    "Markers",
    "Panel",
    "NavHead",
    "SixPack",
    "gps_cdi_deflection",
    "vor_cdi",
    "glideslope_deviation",
    "bearing_to_station",
    "dme_slant",
    "nav_head",
    "six_pack",
    "compute_panel",
]

GS_FULL_SCALE_DEG = 0.7          # +/- 0.7 deg = full deflection (0.35 deg/dot)

FEET_PER_NM = 6076.115

# GPS course-deviation full-scale (each side), by flight phase
class Phase(str, Enum):
    ENROUTE = "ENR"
    TERMINAL = "TERM"
    APPROACH = "APPR"


# GNS 530 Pilot's Guide 190-00181-00 sec.3.3 / 10.4: 5.0 nm enroute, 1.0 nm
# terminal, 0.30 nm approach. gpsnav.NavState.cdi_scale_nm (a slewed value) wins
# when present; this table is the fallback for callers that pass a bare Phase.
GPS_FULL_SCALE_NM = {Phase.ENROUTE: 5.0, Phase.TERMINAL: 1.0, Phase.APPROACH: 0.30}
VOR_FULL_SCALE_DEG = 10.0     # 5 dots, 2 deg / dot
LOC_FULL_SCALE_DEG = 2.5      # localizer is ~4x more sensitive


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Ownship:
    pos: Point
    track_deg: float = 0.0        # true
    heading_deg: float = 0.0      # true
    gs_kt: float = 0.0
    altitude_ft: float = 0.0
    magvar_deg: float = 0.0       # local variation, east positive

    def mag(self, true_deg: float) -> float:
        return norm360(true_deg - self.magvar_deg)


@dataclass(frozen=True, slots=True)
class TunedNav:
    """State of one VLOC receiver."""

    ident: str = ""
    pos: Point | None = None
    station_magvar_deg: float = 0.0   # station declination (VOR); 0 for a localizer
    course_deg: float = 0.0           # course the needle deviates from, MAGNETIC (OBS, or the ILS front course)
    card_deg: float | None = None     # the HSI card as set by the pilot, if it can differ from `course_deg`
    is_localizer: bool = False
    has_dme: bool = False
    elev_ft: float = 0.0

    @property
    def tuned(self) -> bool:
        return self.pos is not None


@dataclass(frozen=True, slots=True)
class TunedAdf:
    ident: str = ""
    pos: Point | None = None

    @property
    def tuned(self) -> bool:
        return self.pos is not None


@dataclass(frozen=True, slots=True)
class MarkerBeacon:
    kind: str            # "OM" | "MM" | "IM"
    pos: Point


# --------------------------------------------------------------------------- #
# outputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CDI:
    source: str = "GPS"              # GPS | VLOC
    deflection: float = 0.0          # -1..+1 fraction of full scale; + = fly right
    full_scale_nm: float | None = None    # set for GPS (linear)
    full_scale_deg: float | None = None   # set for VOR / LOC (angular)
    to_from: str = "OFF"            # TO | FROM | OFF
    valid: bool = False
    course_deg: float = 0.0         # selected course / DTK, magnetic (HSI pointer)


@dataclass(frozen=True, slots=True)
class BearingPointer:
    source: str = "OFF"             # VOR | ADF | GPS | OFF
    bearing_deg: float | None = None   # magnetic, TO the station / active waypoint
    valid: bool = False


@dataclass(frozen=True, slots=True)
class DME:
    distance_nm: float | None = None
    speed_kt: float | None = None
    time_min: float | None = None
    ident: str = ""
    valid: bool = False


@dataclass(frozen=True, slots=True)
class Markers:
    outer: bool = False
    middle: bool = False
    inner: bool = False


@dataclass(frozen=True, slots=True)
class Panel:
    cdi: CDI
    hsi_heading_deg: float
    bearing1: BearingPointer
    bearing2: BearingPointer
    dme: DME
    markers: Markers
    annunciators: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NavHead:
    """One VOR/LOC indicator (CDI or HSI) fed from a `radios.NavReceiver`."""

    valid: bool = False
    is_localizer: bool = False
    ident: str = ""
    course_deg: float = 0.0          # displayed course (LOC front course, else OBS), magnetic
    obs_deg: float = 0.0
    deflection: float = 0.0         # -1..+1, + = fly right
    to_from: str = "OFF"
    full_scale_deg: float = VOR_FULL_SCALE_DEG
    gs_deflection: float = 0.0      # -1..+1, + = fly up
    gs_valid: bool = False
    dme_nm: float | None = None
    dme_time_min: float | None = None
    bearing_deg: float | None = None   # magnetic bearing TO the station


@dataclass(frozen=True, slots=True)
class SixPack:
    airspeed_kt: float = 0.0
    pitch_deg: float = 0.0
    bank_deg: float = 0.0
    altitude_ft: float = 0.0
    vsi_fpm: float = 0.0
    heading_deg: float = 0.0        # magnetic
    turn_rate_dps: float = 0.0
    slip_skid: float = 0.0


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def gps_cdi_deflection(xtk_nm: float, full_scale_nm: float) -> float:
    """Linear GPS deviation. ``xtk_nm`` +ve (right of course) -> -ve (fly left)."""
    if full_scale_nm <= 0:
        return 0.0
    return _clamp(-xtk_nm / full_scale_nm, -1.0, 1.0)


def bearing_to_station(ac_pos: Point, station_pos: Point, ac_magvar_deg: float) -> float:
    """Magnetic bearing from the aircraft to the station."""
    return norm360(initial_bearing(ac_pos, station_pos) - ac_magvar_deg)


def vor_cdi(
    station_pos: Point,
    station_magvar_deg: float,
    ac_pos: Point,
    course_mag_deg: float,
    *,
    full_scale_deg: float = VOR_FULL_SCALE_DEG,
    passage_nm: float = 0.25,
) -> tuple[float, str, bool]:
    """Angular CDI deviation for a VOR / localizer.

    Returns ``(deflection, to_from, valid)``. Built from the aircraft's position
    relative to the selected course *line through the station* (unambiguous in
    every quadrant), then expressed as an angle.
    """
    course_true = norm360(course_mag_deg + station_magvar_deg)
    behind = destination(station_pos, reciprocal(course_true), 100.0)
    xtk = cross_track_nm(behind, station_pos, ac_pos)          # + = right of course
    along = along_track_nm(behind, station_pos, ac_pos)         # ~100 at the station
    dist = great_circle_nm(ac_pos, station_pos)
    if dist < passage_nm:
        return 0.0, "OFF", False
    dev_deg = math.degrees(math.atan2(xtk, max(dist, 0.1)))
    deflection = _clamp(-dev_deg / full_scale_deg, -1.0, 1.0)
    to_from = "TO" if along < 100.0 else "FROM"
    return deflection, to_from, True


def glideslope_deviation(
    threshold: Point,
    threshold_elev_ft: float,
    gs_angle_deg: float,
    ac_pos: Point,
    ac_alt_ft: float,
    *,
    full_scale_deg: float = GS_FULL_SCALE_DEG,
) -> tuple[float, bool]:
    """``(deflection, valid)`` for an ILS glideslope needle.

    ``+`` deflection == the needle is **above** centre == the aircraft is
    **below** the glidepath == "fly up". Invalid within ~0.1 nm of the
    threshold (in the flare) or when above a sane intercept angle.
    """
    d_nm = great_circle_nm(ac_pos, threshold)
    if d_nm < 0.10:
        return 0.0, False
    height_ft = ac_alt_ft - threshold_elev_ft
    actual = math.degrees(math.atan2(height_ft / FEET_PER_NM, d_nm))
    if actual > gs_angle_deg + 3.0 or actual < -1.0:
        return _clamp(-(actual - gs_angle_deg) / full_scale_deg, -1.0, 1.0), False
    err = actual - gs_angle_deg               # + == above path
    return _clamp(-err / full_scale_deg, -1.0, 1.0), True


def dme_slant(
    ac_pos: Point, ac_alt_ft: float, station_pos: Point, station_elev_ft: float, gs_kt: float
) -> tuple[float, float | None]:
    """``(slant_range_nm, time_min)``; time is ``None`` below 30 kt closure proxy."""
    ground = great_circle_nm(ac_pos, station_pos)
    dalt_nm = (ac_alt_ft - station_elev_ft) / FEET_PER_NM
    slant = math.hypot(ground, dalt_nm)
    time_min = slant / gs_kt * 60.0 if gs_kt > 30.0 else None
    return slant, time_min


def marker_state(ac_pos: Point, ac_alt_ft: float, beacons) -> Markers:
    """Light a lamp when within the (approximate) footprint of a beacon.

    Footprints are elliptical in reality; a circular radius is close enough for
    a 2D trainer. No lamp above 3000 ft AGL-ish (we only have MSL - use a
    generous 5000 ft gate)."""
    radius = {"OM": 0.40, "MM": 0.22, "IM": 0.15}
    o = m = i = False
    if ac_alt_ft <= 5000.0:
        for b in beacons:
            if great_circle_nm(ac_pos, b.pos) <= radius.get(b.kind, 0.25):
                o |= b.kind == "OM"
                m |= b.kind == "MM"
                i |= b.kind == "IM"
    return Markers(o, m, i)


# --------------------------------------------------------------------------- #
# assembler
# --------------------------------------------------------------------------- #
def compute_panel(
    own: Ownship,
    nav,                       # gns530.NavState (duck-typed) or None
    *,
    nav1: TunedNav | None = None,
    nav2: TunedNav | None = None,
    adf: TunedAdf | None = None,
    phase: Phase = Phase.ENROUTE,
    markers=(),
    gps_course_deg: float | None = None,     # HSI course pointer (mag) shown on a GPS CDI; None = the DTK
) -> Panel:
    nav1 = nav1 or TunedNav()
    source = getattr(nav, "cdi_source", "GPS") if nav is not None else "GPS"

    # -- CDI ----------------------------------------------------------
    if source == "VLOC" and nav1.tuned:
        fs = LOC_FULL_SCALE_DEG if nav1.is_localizer else VOR_FULL_SCALE_DEG
        dfl, tf, ok = vor_cdi(
            nav1.pos, nav1.station_magvar_deg, own.pos, nav1.course_deg, full_scale_deg=fs
        )
        cdi = CDI(
            source="VLOC", deflection=dfl, full_scale_deg=fs, to_from=tf, valid=ok,
            course_deg=norm360(nav1.course_deg if nav1.card_deg is None else nav1.card_deg),
        )
    elif nav is not None and getattr(nav, "valid", False) and nav.xtk_nm is not None:
        fs_nm = getattr(nav, "cdi_scale_nm", None) or GPS_FULL_SCALE_NM[phase]
        cdi = CDI(
            source="GPS",
            deflection=gps_cdi_deflection(nav.xtk_nm, fs_nm),
            full_scale_nm=fs_nm,
            to_from=getattr(nav, "to_from", "TO") or "TO",
            valid=True,
            course_deg=(norm360(gps_course_deg) if gps_course_deg is not None
                        else own.mag(nav.dtk) if nav.dtk is not None else 0.0),
        )
    else:
        cdi = CDI(source=source, valid=False)

    # -- bearing pointers ------------------------------------------
    if nav1.tuned:
        b1 = BearingPointer("VOR", bearing_to_station(own.pos, nav1.pos, own.magvar_deg), True)
    else:
        b1 = BearingPointer()
    if adf is not None and adf.tuned:
        b2 = BearingPointer("ADF", bearing_to_station(own.pos, adf.pos, own.magvar_deg), True)
    elif nav2 is not None and nav2.tuned:
        b2 = BearingPointer("VOR", bearing_to_station(own.pos, nav2.pos, own.magvar_deg), True)
    elif nav is not None and getattr(nav, "valid", False) and getattr(nav, "brg", None) is not None:
        b2 = BearingPointer("GPS", own.mag(nav.brg), True)
    else:
        b2 = BearingPointer()

    # -- DME -------------------------------------------------------
    if nav1.tuned and nav1.has_dme:
        d_nm, t_min = dme_slant(own.pos, own.altitude_ft, nav1.pos, nav1.elev_ft, own.gs_kt)
        dme = DME(d_nm, own.gs_kt if own.gs_kt > 30 else None, t_min, nav1.ident, True)
    elif nav is not None and getattr(nav, "dist_nm", None) is not None:
        t_min = nav.dist_nm / own.gs_kt * 60.0 if own.gs_kt > 30 else None
        dme = DME(nav.dist_nm, own.gs_kt if own.gs_kt > 30 else None, t_min,
                  getattr(nav, "to_ident", "") or "", True)
    else:
        dme = DME()

    ann = tuple(getattr(nav, "annunciators", ()) or ()) if nav is not None else ()
    return Panel(
        cdi=cdi,
        hsi_heading_deg=own.mag(own.heading_deg),
        bearing1=b1,
        bearing2=b2,
        dme=dme,
        markers=marker_state(own.pos, own.altitude_ft, markers),
        annunciators=ann,
    )


# --------------------------------------------------------------------------- #
# steam panel: one NAV head, and the six-pack
# --------------------------------------------------------------------------- #
def nav_head(receiver, ac_pos: Point, ac_alt_ft: float, gs_kt: float,
             ac_magvar_deg: float) -> NavHead:
    """Build a :class:`NavHead` from a ``radios.NavReceiver`` (duck-typed)."""
    if receiver is None or not getattr(receiver, "tuned", False):
        # untuned/off: the card still shows the pilot's OBS setting (a real
        # CDI's card follows the OBS knob whether or not a station is received)
        obs = norm360(getattr(receiver, "obs_deg", 0.0))
        return NavHead(valid=False, obs_deg=obs, course_deg=obs)

    is_loc = bool(getattr(receiver, "is_localizer", False))
    fs = LOC_FULL_SCALE_DEG if is_loc else VOR_FULL_SCALE_DEG
    course = float(getattr(receiver, "course_deg", receiver.obs_deg))
    magvar = float(getattr(receiver, "station_magvar", 0.0))
    dfl, tf, ok = vor_cdi(receiver.station_pos, magvar, ac_pos, course, full_scale_deg=fs)

    gs_dfl, gs_ok = 0.0, False
    ref = getattr(receiver, "gs_ref", None)
    if ref is not None:
        thr, elev, ang = ref
        gs_dfl, gs_ok = glideslope_deviation(thr, elev, ang, ac_pos, ac_alt_ft)

    dme_nm = dme_t = None
    if getattr(receiver, "has_dme", False):
        dme_nm, dme_t = dme_slant(ac_pos, ac_alt_ft, receiver.station_pos, 0.0, gs_kt)

    return NavHead(
        valid=ok,
        is_localizer=is_loc,
        ident=getattr(receiver, "station_ident", ""),
        course_deg=norm360(getattr(receiver, "card_deg", course)),   # the card; the needle used `course`
        obs_deg=norm360(getattr(receiver, "obs_deg", 0.0)),
        deflection=dfl,
        to_from="OFF" if is_loc else tf,          # a localizer has no TO/FROM
        full_scale_deg=fs,
        gs_deflection=gs_dfl,
        gs_valid=gs_ok,
        dme_nm=dme_nm,
        dme_time_min=dme_t,
        bearing_deg=bearing_to_station(ac_pos, receiver.station_pos, ac_magvar_deg),
    )


def gps_nav_head(nav, panel) -> NavHead:
    """A :class:`NavHead`-shaped view of the GPS CDI, for a NAV1 round head
    that's slaved to the GNS's own CDI/VLOC switch - the same real-world
    wiring as a GPS-slaved analog CDI: the needle shows GPS course deviation
    while the GNS's CDI source is GPS, and only the tuned NAV1 VOR/LOC
    receiver (the plain `nav_head()` builder above) once the CDI key selects
    VLOC. ``panel.cdi`` already carries this exact switch (`compute_panel`
    picks GPS or VLOC deflection there); this just repackages it as a
    NavHead so `render.draw_nav_head`/`draw_hsi_head` don't need a second
    drawing path. ``nav`` supplies the to-waypoint ident/distance (GPS has
    no VOR-style DME, but the "distance to the active fix" fills the same
    display slot); ``panel`` supplies the already-computed CDI deflection."""
    cdi = getattr(panel, "cdi", None)
    if cdi is None or not getattr(cdi, "valid", False):
        return NavHead(valid=False)
    course = getattr(cdi, "course_deg", 0.0)
    return NavHead(
        valid=True,
        is_localizer=False,
        ident=(getattr(nav, "to_ident", "") or "").upper(),
        course_deg=course,
        obs_deg=course,
        deflection=getattr(cdi, "deflection", 0.0),
        to_from=getattr(cdi, "to_from", "TO") or "TO",
        full_scale_deg=VOR_FULL_SCALE_DEG,
        dme_nm=getattr(nav, "dist_nm", None),
    )


def six_pack(own, magvar_deg: float = 0.0) -> SixPack:
    """Snapshot the six primary flight instruments from a `sim_model.Ownship`."""
    hdg = norm360(getattr(own, "heading_deg", 0.0) - magvar_deg)
    return SixPack(
        # the ASI reads indicated airspeed; fall back to TAS if a feed omits it
        airspeed_kt=getattr(own, "ias_kt", None) or getattr(own, "tas_kt", 0.0),
        pitch_deg=getattr(own, "pitch_deg", 0.0),
        bank_deg=getattr(own, "bank_deg", 0.0),
        altitude_ft=getattr(own, "altitude_ft", 0.0),
        vsi_fpm=getattr(own, "vs_fpm", 0.0),
        heading_deg=hdg,
        turn_rate_dps=getattr(own, "turn_rate_dps", 0.0),
        slip_skid=getattr(own, "slip_skid", 0.0),
    )
