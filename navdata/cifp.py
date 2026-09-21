"""Parse the FAA CIFP (``FAACIFP18``, ARINC 424-18) into a :class:`NavDatabase`.

Fixed 132-char records. Handled so far:

* section ``D``    -- VHF NAVAIDs (VOR / VOR-DME / VORTAC / DME)
* section ``DB``   -- NDB NAVAIDs
* section ``EA``   -- enroute waypoints
* section ``P``/``A`` -- airport reference points
* section ``P``/``C`` -- terminal waypoints (same field layout as ``EA``)
* section ``P``/``G`` -- runways (attached to their airport after the pass)

* section ``P``/``D`` -- SIDs        (grouped into `Procedure` objects)
* section ``P``/``E`` -- STARs
* section ``P``/``F`` -- instrument approaches
* section ``ER``      -- enroute airways (grouped into `Airway` objects)

Unknown record types are counted and skipped.

Column offsets below are 0-indexed slices verified against real cycle-2609
records (see ``tests/test_cifp.py`` for the fixture lines).
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Iterator

from navmath import Point, great_circle_nm

from .arinc424 import (
    opt_int,
    parse_lat,
    parse_latlon,
    parse_lon,
    parse_magvar,
    parse_ndb_freq_khz,
    parse_vhf_freq_mhz,
)
from .model import (
    Airport,
    Airway,
    AirwayPoint,
    LegType,
    NavDatabase,
    NdbNavaid,
    PathPoint,
    Procedure,
    ProcedureLeg,
    Runway,
    VhfNavaid,
    Waypoint,
)

_PROC_KIND = {"D": "sid", "E": "star", "F": "approach"}

__all__ = ["parse_cifp", "iter_lines"]

RECORD_LEN = 132


def iter_lines(source) -> Iterator[str]:
    """Yield lines from a path, an open file, or any iterable of strings."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "r", encoding="latin-1") as fh:
            for line in fh:
                yield line.rstrip("\r\n")
        return
    if hasattr(source, "read"):  # file-like
        for line in source:
            yield line.rstrip("\r\n")
        return
    for line in source:  # iterable of strings
        yield line.rstrip("\r\n")


# --------------------------------------------------------------------------- #
# per-record-type field extraction                                           #
# --------------------------------------------------------------------------- #
def _navaid_pos(ln: str) -> Point:
    """Navaid position.

    Section D has two coordinate slots: the VOR position at [32:51] and the
    DME/TACAN position at [55:74]. A VOR-with-DME fills both (near-identical); a
    DME-/TACAN-only record leaves [32:51] blank and fills only the second slot.
    """
    lat, lon = ln[32:41].strip(), ln[41:51].strip()
    if not lat or not lon:
        lat, lon = ln[55:64].strip(), ln[64:74].strip()
    return Point(parse_lat(lat), parse_lon(lon))


def _vhf_from_line(ln: str) -> VhfNavaid:
    return VhfNavaid(
        ident=ln[13:17].strip(),
        region=ln[19:21].strip(),
        freq_mhz=parse_vhf_freq_mhz(ln[22:27]),
        nav_class=ln[27:31].strip(),
        pos=_navaid_pos(ln),
        magvar_deg=parse_magvar(ln[74:79]),
        elev_ft=opt_int(ln[79:84]),
        name=ln[93:123].strip(),
    )


def _localizer_from_pi_line(ln: str) -> VhfNavaid:
    """CIFP section P·I -- airport localizer & glide-slope.

        SUSAP KLNSK6IILNS1   010870RW08 N40073972W0761644650770N40071355...
             ^apt ^sub ^ident^cat  ^freq^rwy  ^localizer pos     ^brg (mag*10)
    """
    return VhfNavaid(
        ident=ln[13:17].strip(),
        region=ln[10:12].strip(),
        freq_mhz=parse_vhf_freq_mhz(ln[22:27]),
        nav_class="ILSW",
        pos=parse_latlon(ln[32:41], ln[41:51]),
        loc_bearing_deg=(int(ln[51:55]) / 10.0) if ln[51:55].strip().isdigit() else None,
        runway_ident=ln[27:32].strip(),
        airport_ident=ln[6:10].strip(),
        ils_category=int(ln[17:18]) if ln[17:18].isdigit() else 0,  # letter = LOC-only (LDA/SDF/...)
        name=f"ILS {ln[27:32].strip()} {ln[6:10].strip()}",
    )


def _ndb_from_line(ln: str) -> NdbNavaid:
    return NdbNavaid(
        ident=ln[13:17].strip(),
        region=ln[19:21].strip(),
        freq_khz=parse_ndb_freq_khz(ln[22:27]),
        pos=_navaid_pos(ln),
        magvar_deg=parse_magvar(ln[74:79]),
        name=ln[93:123].strip(),
    )


def _wpt_from_line(ln: str, *, kind: str = "WPT") -> Waypoint:
    # EA (enroute) and P/C (terminal) share this field layout
    return Waypoint(
        ident=ln[13:18].strip(),
        region=ln[19:21].strip(),
        pos=Point(parse_lat(ln[32:41]), parse_lon(ln[41:51])),
        name=ln[98:123].strip(),
        kind=kind,
    )


def _airport_from_line(ln: str) -> Airport:
    return Airport(
        ident=ln[6:10].strip(),
        region=ln[10:12].strip(),
        iata=ln[13:16].strip(),
        pos=Point(parse_lat(ln[32:41]), parse_lon(ln[41:51])),
        magvar_deg=parse_magvar(ln[51:56]),
        elev_ft=opt_int(ln[56:61]),
        longest_runway_ft=(lambda v: v * 100 if v is not None else None)(opt_int(ln[27:30])),
        transition_alt_ft=opt_int(ln[70:75]),
        name=ln[93:123].strip(),
    )


def _tenths(field: str) -> float | None:
    field = field.strip()
    return int(field) / 10.0 if field.isdigit() else None


def _alt(field: str) -> int | None:
    field = field.strip().upper()
    if not field:
        return None
    if field.startswith("FL") and field[2:].isdigit():
        return int(field[2:]) * 100
    return int(field) if field.isdigit() else None


def build_procedure(airport: str, kind: str, ident: str, lines: list[str]) -> Procedure:
    """Turn a procedure's raw CIFP record lines into a :class:`Procedure`.

    Called lazily by :class:`NavDatabase` -- see the note there on why procedure
    legs are not parsed during the main pass.
    """
    by_trans: dict[str, list[ProcedureLeg]] = {}
    rt_by_trans: dict[str, str] = {}
    for ln in lines:
        try:
            leg = _leg_from_line(ln)
        except ValueError:
            continue
        transition = ln[20:25].strip()
        by_trans.setdefault(transition, []).append(leg)
        rt_by_trans.setdefault(transition, ln[19:20].strip())
    route_type = rt_by_trans.get("") or next(iter(rt_by_trans.values()), "")
    transitions = {
        t: tuple(sorted(ls, key=lambda leg: leg.seq)) for t, ls in by_trans.items()
    }
    return Procedure(airport, ident, kind, route_type, transitions)


def _leg_from_line(ln: str) -> ProcedureLeg:
    desc = ln[39:43]
    d4 = desc[3:4]
    dist_time = ln[74:78]
    is_time = dist_time[:1].upper() == "T"
    tail = dist_time[1:].strip()
    spd = ln[99:102].strip()
    return ProcedureLeg(
        seq=int(ln[26:29]),
        leg_type=LegType.parse(ln[47:49]),
        fix_ident=ln[29:34].strip(),
        fix_region=ln[34:36].strip(),
        fix_section=ln[36:37].strip(),
        turn=ln[43:44].strip(),
        recnav_ident=ln[50:54].strip(),
        recnav_region=ln[54:56].strip(),
        theta=_tenths(ln[62:66]),
        rho=_tenths(ln[66:70]),
        course_mag=_tenths(ln[70:74]),
        distance_nm=None if is_time else _tenths(dist_time),
        time_min=(int(tail) / 10.0 if is_time and tail.isdigit() else None),
        alt_desc=ln[82:83].strip(),
        alt1_ft=_alt(ln[84:89]),
        alt2_ft=_alt(ln[89:94]),
        speed_kt=int(spd) if spd.isdigit() else None,
        is_iaf=d4 in ("A", "B", "C"),
        is_faf=d4 == "F",
        is_map=d4 == "M",
        is_flyover=desc[1:2] == "Y",
    )


def _airway_point_from_line(ln: str) -> tuple[str, AirwayPoint]:
    """Returns ``(airway_ident, AirwayPoint)``."""
    pt = AirwayPoint(
        seq=int(ln[25:29]),
        fix_ident=ln[29:34].strip(),
        fix_region=ln[34:36].strip(),
        fix_section=ln[36:37].strip(),
        course_mag=_tenths(ln[70:74]),
        distance_nm=_tenths(ln[74:78]),
        min_alt_ft=_alt(ln[83:88]),
        max_alt_ft=_alt(ln[93:98]),
    )
    return ln[13:18].strip(), pt


def _runway_from_line(ln: str) -> tuple[str, Runway]:
    """Returns ``(airport_ident, Runway)``; the runway is attached after the pass."""
    bearing = opt_int(ln[27:31])
    ils_ident = ln[81:85].strip() if ln[80:81] == "I" else ""
    # [85] is an ARINC category code: 0-3 for ILS CAT, but also letters for
    # LDA / IGS / SDF etc. -- treat any non-digit as "not a numbered CAT".
    cat_char = ln[85:86]
    ils_cat = int(cat_char) if cat_char.isdigit() else 0
    rwy = Runway(
        ident=ln[13:18].strip(),
        threshold=Point(parse_lat(ln[32:41]), parse_lon(ln[41:51])),
        bearing_deg=(bearing / 10.0) if bearing is not None else 0.0,
        length_ft=opt_int(ln[22:27]),
        width_ft=opt_int(ln[77:80]),
        ils_ident=ils_ident,
        ils_category=ils_cat,
        elev_ft=opt_int(ln[66:71]),
    )
    return ln[6:10].strip(), rwy


def _path_point_from_line(ln: str) -> PathPoint:
    """CIFP section P.P primary record (``ln[24:27] == "001"``): an SBAS / GBAS final-approach path
    point. Offsets verified against 2609 records and, offline, against X-Plane's own LTP/FPAP rows
    (see ``tests/test_cifp.py``): LTP lat/lon at [37:48]/[48:60] (4-decimal seconds), GPA [66:70]
    (hundredths of a degree), FPAP [70:81]/[81:93], course width [93:98] (hundredths of a metre),
    TCH [103:108] (tenths, unit flag at [108]: F feet / M metres)."""
    tch = int(ln[103:108]) / 10.0
    if ln[108:109] == "M":
        tch *= 3.28084

    def lat(f: str) -> float:              # N DD MM SS ssss
        v = int(f[1:3]) + int(f[3:5]) / 60.0 + int(f[5:11]) / 10000.0 / 3600.0
        return -v if f[0] == "S" else v

    def lon(f: str) -> float:              # W DDD MM SS ssss
        v = int(f[1:4]) + int(f[4:6]) / 60.0 + int(f[6:12]) / 10000.0 / 3600.0
        return -v if f[0] == "W" else v

    return PathPoint(
        airport=ln[6:10].strip(),
        approach=ln[13:19].strip(),
        runway=ln[19:24].strip(),
        ref_path_id=ln[32:36].strip(),
        ltp=Point(lat(ln[37:48]), lon(ln[48:60])),
        gpa_deg=int(ln[66:70]) / 100.0,
        tch_ft=tch,
        fpap=Point(lat(ln[70:81]), lon(ln[81:93])),
        course_width_m=int(ln[93:98]) / 100.0,
        ellipsoid_height_m=int(ln[60:66]) / 10.0,
    )


_SERVICE_RE = re.compile(r"A(LPV|LP\+V|LP(?![V+])|LNAV/VNAV|LNAV\+V|LNAV(?![/+]))")


def _service_from_continuation(ln: str) -> frozenset:
    """Levels of service an RNAV approach publishes, from the "W" continuation record on its FAF leg
    (e.g. ``ALPV       ALNAV/VNAV ALNAV``; an ``N`` slot means not available)."""
    return frozenset(_SERVICE_RE.findall(ln[40:]))


# --------------------------------------------------------------------------- #
# top-level parse                                                            #
# --------------------------------------------------------------------------- #
def parse_cifp(
    source,
    *,
    areas: Iterable[str] | None = None,
    strict: bool = False,
) -> NavDatabase:
    """Build a :class:`NavDatabase` from a CIFP source.

    ``areas`` -- optional set of 3-letter area codes to keep (``"USA"``,
    ``"CAN"``, ...). ``None`` keeps everything.
    ``strict`` -- re-raise the first malformed-record error instead of skipping.
    """
    area_filter = {a.upper() for a in areas} if areas is not None else None
    db = NavDatabase(source="FAA CIFP")
    skipped = 0
    pending_runways: list[tuple[str, Runway]] = []
    orphan_runways = 0
    # (airport, kind, ident) -> raw procedure record lines (parsed lazily later)
    proc_acc: dict[tuple[str, str, str], list[str]] = {}
    airway_acc: dict[str, list[AirwayPoint]] = {}

    for ln in iter_lines(source):
        if len(ln) < 14 or ln[0] != "S":
            continue  # HDR*, blanks, trailer
        if area_filter is not None and ln[1:4] not in area_filter:
            continue

        section = ln[4]
        sub_de = ln[5]     # subsection for D / E sections
        sub_p = ln[12]     # subsection for P (airport) section
        try:
            if section == "D" and sub_de == " ":
                db.add_vhf(_vhf_from_line(ln))
            elif section == "P" and sub_p == "I":
                loc = _localizer_from_pi_line(ln)
                if loc.ident:
                    # a localizer may also appear in section D - prefer the
                    # richer P·I record (it carries the course + runway)
                    same = db.vhf.get(loc.ident, [])
                    same[:] = [n for n in same
                               if great_circle_nm(n.pos, loc.pos) > 1.0]
                    db.add_vhf(loc)
            elif section == "D" and sub_de == "B":
                db.add_ndb(_ndb_from_line(ln))
            elif section == "E" and sub_de == "A":
                db.add_waypoint(_wpt_from_line(ln))
            elif section == "P" and sub_p == "A":
                db.add_airport(_airport_from_line(ln))
            elif section == "P" and sub_p == "C":
                db.add_waypoint(_wpt_from_line(ln, kind="terminal"))
            elif section == "P" and sub_p == "G":
                pending_runways.append(_runway_from_line(ln))
            elif section == "P" and sub_p == "P":
                if ln[24:27] == "001":          # primary; 002 is the HAL/VAL/channel continuation
                    pp = _path_point_from_line(ln)
                    db.path_points[(pp.airport, pp.approach)] = pp
            elif section == "P" and sub_p in _PROC_KIND:
                if ln[47:49].strip():  # skip continuation records (no path terminator)
                    key = (ln[6:10].strip(), _PROC_KIND[sub_p], ln[13:19].strip())
                    proc_acc.setdefault(key, []).append(ln)
                elif sub_p == "F" and ln[39:40] == "W" and ln[38:39] in "23456789":
                    svc = _service_from_continuation(ln)
                    if svc:
                        akey = (ln[6:10].strip(), ln[13:19].strip())
                        db.approach_service[akey] = db.approach_service.get(akey, frozenset()) | svc
            elif section == "E" and sub_de == "R":
                ident, pt = _airway_point_from_line(ln)
                airway_acc.setdefault(ident, []).append(pt)
            # everything else: not in this slice
        except ValueError:
            if strict:
                raise
            skipped += 1

    for apt_ident, rwy in pending_runways:
        apt = db.airports.get(apt_ident)
        if apt is None:
            orphan_runways += 1
            continue
        apt.runways[rwy.ident] = rwy

    for (apt_ident, kind, ident), raw_lines in proc_acc.items():
        db.add_procedure_lines(apt_ident, kind, ident, raw_lines)

    for ident, pts in airway_acc.items():
        pts.sort(key=lambda p: p.seq)
        db.add_airway(Airway(ident, tuple(pts)))

    if skipped:
        db.notes.append(f"{skipped} record(s) skipped (parse errors)")
    if orphan_runways:
        db.notes.append(f"{orphan_runways} runway(s) for unknown airports")
    return db
