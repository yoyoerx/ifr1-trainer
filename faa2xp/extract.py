"""Read FAACIFP18 into X-Plane-shaped records (pure; no X-Plane files touched)."""

from __future__ import annotations

from dataclasses import dataclass, field

from navdata.arinc424 import opt_int, parse_latlon, parse_magvar
from navdata.cifp import (
    _airway_point_from_line, _leg_from_line, _localizer_from_pi_line,
    _ndb_from_line, _vhf_from_line, iter_lines,
)
from navmath import norm360

ENRT_FIX_CODE = 4606275     # opaque per-fix code X-Plane's files carry; reused per usage
TERM_FIX_CODE = 4530263


@dataclass
class NavRow:
    type: int
    lat: float
    lon: float
    elev: int
    freq: int
    range_nm: int
    bearing: float
    ident: str
    area: str
    region: str
    name: str


@dataclass
class FixRow:
    lat: float
    lon: float
    ident: str
    area: str      # "ENRT" or the airport ICAO
    region: str
    code: int
    name: str


@dataclass
class AwyRow:
    a_ident: str
    a_region: str
    a_type: int
    b_ident: str
    b_region: str
    b_type: int
    direction: str
    level: int
    base_fl: int
    top_fl: int
    name: str


@dataclass
class HoldRow:
    ident: str
    region: str
    area: str
    type: int
    course: float
    time_min: float
    length_nm: float
    turn: str
    min_alt: int
    max_alt: int
    speed: int


@dataclass
class Extracted:
    nav: list[NavRow] = field(default_factory=list)
    fix: list[FixRow] = field(default_factory=list)
    awy: list[AwyRow] = field(default_factory=list)
    hold: list[HoldRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_CAT = {1: "ILS-cat-I", 2: "ILS-cat-II", 3: "ILS-cat-III"}


def _range_for(cls_char: str) -> int:
    return {"H": 130, "L": 40, "T": 25}.get(cls_char, 40)


def vhf_rows(n) -> list[NavRow]:
    """Rows for one D-section navaid: VOR/VORTAC/TACAN -> 3 (+12 for VOR/DME), DME -> 13."""
    c = (n.nav_class or "").ljust(4)
    common = (n.pos.lat, n.pos.lon, n.elev_ft or 0)
    f100 = round(n.freq_mhz * 100)
    rows: list[NavRow] = []
    if c[0] == "V":
        rng = _range_for(c[2])
        label = "VOR/DME" if c[1] == "D" else "VORTAC" if c[1] == "T" else "VOR"
        rows.append(NavRow(3, *common, f100, rng, n.magvar_deg, n.ident, "ENRT", n.region, f"{n.name} {label}"))
        if c[1] in ("D", "T"):   # X-Plane also lists the DME half of a VORTAC (name suffix "VORTAC DME")
            rows.append(NavRow(12, *common, f100, rng, 0.0, n.ident, "ENRT", n.region,
                               f"{n.name} {label}" + (" DME" if c[1] == "T" else "")))
    elif c[0] in ("T", "M"):
        rows.append(NavRow(3, *common, f100, _range_for(c[1]), n.magvar_deg, n.ident, "ENRT", n.region, f"{n.name} TACAN"))
    elif c[0] == "D":
        rows.append(NavRow(13, *common, f100, _range_for(c[1]), 0.0, n.ident, "ENRT", n.region, f"{n.name} DME"))
    return rows


def ils_rows(ln: str, apt_elev: dict[str, int]) -> list[NavRow]:
    """LOC (4/5) and, when the record carries a glide slope, GS (6) rows from a P.I record."""
    loc = _localizer_from_pi_line(ln)
    if not loc.ident or loc.loc_bearing_deg is None:
        return []
    var = parse_magvar(ln[90:95]) if ln[90:95].strip() else 0.0
    true_brg = norm360(loc.loc_bearing_deg + var)
    apt = loc.airport_ident
    rwy = loc.runway_ident[2:] if loc.runway_ident.startswith("RW") else loc.runway_ident
    elev = apt_elev.get(apt, 0)
    f100 = round(loc.freq_mhz * 100)
    label = _CAT.get(loc.ils_category, "LOC")
    # X-Plane packs the rounded magnetic bearing into the LOC field as N*360 + true bearing
    packed = round(loc.loc_bearing_deg) * 360 + true_brg
    rows = [NavRow(4 if loc.ils_category else 5, loc.pos.lat, loc.pos.lon, elev, f100, 18,
                   packed, loc.ident, apt, loc.region, f"{rwy} {label}")]
    gs_lat, gs_lon, ang = ln[55:64].strip(), ln[64:74].strip(), ln[87:90].strip()
    if gs_lat and gs_lon and ang.isdigit():
        gp = parse_latlon(gs_lat, gs_lon)
        # GS field: glide angle * 100000 + true bearing (3.00 deg -> 300000)
        rows.append(NavRow(6, gp.lat, gp.lon, elev, f100, 18, int(ang) * 1000.0 + true_brg,
                           loc.ident, apt, loc.region, f"{rwy} GS"))
    return rows


def _awy_type(sec: str, sub: str) -> int:
    return 3 if (sec, sub) == ("D", " ") else 2 if sec == "D" else 11


def awy_rows(ident: str, pts: list) -> list[AwyRow]:
    """Consecutive-pair segments of one airway; ``pts`` = [(AirwayPoint, (section, subsection))]."""
    high = ident[:1] in ("J", "Q")
    out = []
    for (pa, sa), (pb, sb) in zip(pts, pts[1:]):
        base = pa.min_alt_ft // 100 if pa.min_alt_ft else (180 if high else 20)
        top = pa.max_alt_ft // 100 if pa.max_alt_ft else (450 if high else 180)
        out.append(AwyRow(pa.fix_ident, pa.fix_region, _awy_type(*sa), pb.fix_ident, pb.fix_region,
                          _awy_type(*sb), "N", 2 if high or base >= 180 else 1, base, top, ident))
    return out


def hold_row(leg, area: str, vhf: set, ndb: set) -> HoldRow | None:
    if leg.course_mag is None or not leg.fix_ident:
        return None
    key = (leg.fix_ident, leg.fix_region)
    typ = 3 if key in vhf else 2 if key in ndb else 11
    lo = hi = 0
    if leg.alt1_ft is not None:
        if leg.alt_desc == "-":
            hi = leg.alt1_ft
        elif leg.alt_desc == "B":
            hi, lo = leg.alt1_ft, leg.alt2_ft or 0
        else:
            lo = leg.alt1_ft
    time_min = leg.time_min or 0.0
    length = leg.distance_nm or 0.0
    if not time_min and not length:
        time_min = 1.0
    return HoldRow(leg.fix_ident, leg.fix_region, area, typ, leg.course_mag, time_min, length,
                   "L" if leg.turn == "L" else "R", lo, hi, leg.speed_kt or 0)


def extract(source) -> Extracted:
    """One pass over a CIFP file (path or iterable of lines)."""
    lines = list(iter_lines(source))
    out = Extracted()
    apt_elev: dict[str, int] = {}
    for ln in lines:
        if len(ln) > 61 and ln[0] == "S" and ln[4] == "P" and ln[12] == "A":
            apt_elev[ln[6:10].strip()] = opt_int(ln[56:61]) or 0
    vhf_keys: set = set()
    ndb_keys: set = set()
    awy_acc: dict[str, list] = {}
    hold_legs: list[tuple] = []
    seen_fix: set = set()
    for ln in lines:
        if len(ln) < 14 or ln[0] != "S":
            continue
        sec, sub_de, sub_p = ln[4], ln[5], ln[12]
        try:
            if sec == "D" and sub_de == " ":
                if ln[27:28] == "I":
                    continue
                n = _vhf_from_line(ln)
                vhf_keys.add((n.ident, n.region))
                out.nav += vhf_rows(n)
            elif sec == "D" and sub_de == "B":
                n = _ndb_from_line(ln)
                ndb_keys.add((n.ident, n.region))
                out.nav.append(NavRow(2, n.pos.lat, n.pos.lon, 0, round(n.freq_khz), 25, 0.0,
                                      n.ident, "ENRT", n.region, f"{n.name} NDB"))
            elif sec == "P" and sub_p == "I":
                out.nav += ils_rows(ln, apt_elev)
            elif (sec == "E" and sub_de == "A") or (sec == "P" and sub_p == "C"):
                ident = ln[13:18].strip()
                area = "ENRT" if sec == "E" else ln[6:10].strip()
                region = ln[19:21].strip()
                k = (ident, region, area)
                if k in seen_fix:
                    continue
                seen_fix.add(k)
                pos = parse_latlon(ln[32:41], ln[41:51])
                out.fix.append(FixRow(pos.lat, pos.lon, ident, area, region,
                                      ENRT_FIX_CODE if sec == "E" else TERM_FIX_CODE, ident))
            elif sec == "E" and sub_de == "R":
                ident, pt = _airway_point_from_line(ln)
                awy_acc.setdefault(ident, []).append((pt, (ln[36:37], ln[37:38])))
            elif sec == "P" and sub_p in ("D", "E", "F") and ln[47:49] in ("HA", "HF", "HM"):
                hold_legs.append((ln[6:10].strip(), _leg_from_line(ln)))
        except ValueError:
            out.notes.append(f"skipped malformed record {ln[:30]!r}")
    for ident, pts in awy_acc.items():
        pts.sort(key=lambda p: p[0].seq)
        out.awy += awy_rows(ident, pts)
    seen_hold: set = set()
    for apt, leg in hold_legs:
        row = hold_row(leg, apt, vhf_keys, ndb_keys)
        if row is None:
            continue
        k = (row.ident, row.region, row.area, row.course, row.turn, row.time_min, row.length_nm)
        if k not in seen_hold:
            seen_hold.add(k)
            out.hold.append(row)
    return out
