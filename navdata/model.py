"""In-memory nav database types, shared by every loader (FAA CIFP, NASR, X-Plane).

Kept deliberately small and loader-agnostic: a loader's job is to fill these in.
Positions are ``navmath.Point`` (decimal degrees). Magnetic variation is degrees,
**east positive** -- so ``true = magnetic + magvar`` (matches ``navmath``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from navmath import Point, great_circle_nm

__all__ = [
    "Waypoint",
    "VhfNavaid",
    "NdbNavaid",
    "Runway",
    "Airport",
    "LegType",
    "ProcedureLeg",
    "Procedure",
    "AirwayPoint",
    "Airway",
    "NavDatabase",
]


@dataclass(frozen=True, slots=True)
class Waypoint:
    """An enroute or terminal fix (no transmitter)."""

    ident: str
    pos: Point
    region: str = ""          # ICAO region code, e.g. "K6"
    name: str = ""
    kind: str = "WPT"         # WPT | terminal | etc. (raw ARINC usage later)

    def distance_nm(self, other: Point) -> float:
        return great_circle_nm(self.pos, other)


@dataclass(frozen=True, slots=True)
class VhfNavaid:
    """VOR / VOR-DME / VORTAC / DME -- and, from CIFP section P·I, ILS/LOC."""

    ident: str
    pos: Point
    freq_mhz: float
    region: str = ""
    name: str = ""
    magvar_deg: float = 0.0   # east positive
    elev_ft: int | None = None
    nav_class: str = ""       # raw 4-char ARINC facility class (`I...` = localizer)
    # localizer-only (CIFP section P·I), 0/blank for a VOR:
    loc_bearing_deg: float | None = None   # localizer course, MAGNETIC
    runway_ident: str = ""                 # runway it serves, e.g. "RW08"
    airport_ident: str = ""                # ICAO of that airport
    ils_category: int = 0                  # 0 LOC-only, 1/2/3 CAT I/II/III

    @property
    def is_localizer(self) -> bool:
        return (self.nav_class or "")[:1] == "I" or self.loc_bearing_deg is not None

    @property
    def has_dme(self) -> bool:
        return "D" in self.nav_class or "T" in self.nav_class

    def distance_nm(self, other: Point) -> float:
        return great_circle_nm(self.pos, other)


@dataclass(frozen=True, slots=True)
class NdbNavaid:
    ident: str
    pos: Point
    freq_khz: float
    region: str = ""
    name: str = ""
    magvar_deg: float = 0.0

    def distance_nm(self, other: Point) -> float:
        return great_circle_nm(self.pos, other)


@dataclass(frozen=True, slots=True)
class Runway:
    """One runway end."""

    ident: str                # "RW04L"
    threshold: Point
    bearing_deg: float        # magnetic
    length_ft: int | None = None
    width_ft: int | None = None
    ils_ident: str = ""       # e.g. "IHIQ" (blank if none)
    ils_category: int = 0     # 0 none/LOC, 1 CAT I, 2 CAT II, 3 CAT III

    @property
    def number(self) -> str:
        """"04L" from "RW04L"."""
        return self.ident[2:] if self.ident.startswith("RW") else self.ident

    @property
    def has_ils(self) -> bool:
        return bool(self.ils_ident)


@dataclass(slots=True)
class Airport:
    ident: str                # ICAO, e.g. "KJFK"
    pos: Point                # airport reference point
    region: str = ""
    name: str = ""
    iata: str = ""
    elev_ft: int | None = None
    magvar_deg: float = 0.0   # east positive
    longest_runway_ft: int | None = None
    transition_alt_ft: int | None = None
    runways: dict[str, Runway] = field(default_factory=dict)
    comms: dict[str, list[float]] = field(default_factory=dict)  # "TWR"/"GND"/"ATIS"/... -> MHz

    def distance_nm(self, other: Point) -> float:
        return great_circle_nm(self.pos, other)

    def comm(self, *uses: str) -> float | None:
        """First frequency for any of the given comm uses (e.g. ``comm("TWR")``)."""
        for u in uses:
            if self.comms.get(u):
                return self.comms[u][0]
        return None


class LegType(str, Enum):
    """ARINC 424 path/terminator codes for procedure legs.

    Defined now so `navdata` and `gns530` can reference them; procedure parsing
    (CIFP section P subsections D/E/F) lands in a later M3 slice.
    """

    IF = "IF"  # initial fix
    TF = "TF"  # track to fix
    CF = "CF"  # course to fix
    DF = "DF"  # direct to fix
    FA = "FA"  # fix to altitude
    FC = "FC"  # fix to distance on course
    FD = "FD"  # fix to DME distance
    FM = "FM"  # fix to manual termination
    VA = "VA"  # heading to altitude
    VI = "VI"  # heading to intercept
    VM = "VM"  # heading to manual termination
    VD = "VD"  # heading to DME distance
    VR = "VR"  # heading to radial
    CA = "CA"  # course to altitude
    CD = "CD"  # course to DME distance
    CI = "CI"  # course to intercept
    CR = "CR"  # course to radial
    RF = "RF"  # constant-radius arc
    AF = "AF"  # arc to fix (DME arc)
    PI = "PI"  # procedure turn
    HA = "HA"  # hold to altitude
    HF = "HF"  # hold, single circuit to fix
    HM = "HM"  # hold, manual termination

    @classmethod
    def parse(cls, code: str) -> "LegType | str":
        """Coerce a 2-char ARINC code to a ``LegType``; unknown -> the raw string."""
        code = (code or "").strip().upper()
        try:
            return cls(code)
        except ValueError:
            return code


@dataclass(frozen=True, slots=True)
class ProcedureLeg:
    """One leg of a SID / STAR / approach, from a CIFP procedure record."""

    seq: int
    leg_type: "LegType | str"
    fix_ident: str = ""
    fix_region: str = ""
    fix_section: str = ""       # 'P' terminal, 'D' navaid, 'E' enroute
    turn: str = ""              # 'L' | 'R' | 'E' (either) | ''
    recnav_ident: str = ""      # recommended navaid (course/arc legs)
    recnav_region: str = ""
    theta: float | None = None  # deg, bearing from recnav
    rho: float | None = None    # nm, distance from recnav
    course_mag: float | None = None
    distance_nm: float | None = None
    time_min: float | None = None
    alt_desc: str = ""          # '+', '-', 'B', 'H', '' (at) ...
    alt1_ft: int | None = None
    alt2_ft: int | None = None
    speed_kt: int | None = None
    is_iaf: bool = False
    is_faf: bool = False
    is_map: bool = False        # missed approach point
    is_flyover: bool = False

    @property
    def terminates_at_fix(self) -> bool:
        lt = self.leg_type
        return isinstance(lt, LegType) and lt in (
            LegType.IF, LegType.TF, LegType.CF, LegType.DF, LegType.RF, LegType.AF, LegType.HF
        )


@dataclass(frozen=True, slots=True)
class Procedure:
    """A SID, STAR, or instrument approach for one airport.

    ``transitions`` maps a transition identifier to its ordered legs. The key
    ``""`` is the segment common to every transition (an approach's final +
    missed-approach; a SID's common route). ``assemble()`` splices a chosen
    transition onto the common segment.
    """

    airport: str
    ident: str                 # "I04R", "DEEZZ6", "CAMRN5"
    kind: str                  # 'approach' | 'sid' | 'star'
    route_type: str            # raw ARINC route-type char
    transitions: dict[str, tuple[ProcedureLeg, ...]] = field(default_factory=dict)

    def transition_names(self) -> list[str]:
        return [k for k in self.transitions if k]

    def assemble(self, transition: str | None = None) -> list[ProcedureLeg]:
        legs: list[ProcedureLeg] = []
        if transition and transition in self.transitions:
            legs += self.transitions[transition]
        elif transition is None and self.transition_names():
            legs += self.transitions[self.transition_names()[0]]
        legs += self.transitions.get("", ())
        return legs


@dataclass(frozen=True, slots=True)
class AirwayPoint:
    seq: int
    fix_ident: str
    fix_region: str = ""
    fix_section: str = ""          # 'D' navaid, 'E' enroute waypoint
    course_mag: float | None = None    # outbound, toward the next point
    distance_nm: float | None = None   # to the next point
    min_alt_ft: int | None = None      # MEA
    max_alt_ft: int | None = None


@dataclass(frozen=True, slots=True)
class Airway:
    ident: str                    # "V16", "J121", "T263"
    points: tuple[AirwayPoint, ...] = ()

    def fix_idents(self) -> list[str]:
        return [p.fix_ident for p in self.points]

    def segment(self, from_ident: str, to_ident: str) -> list[AirwayPoint]:
        """Points between two fixes, inclusive, oriented from -> to.

        Empty if either fix is not on the airway.
        """
        idents = self.fix_idents()
        try:
            i = idents.index(from_ident.strip().upper())
            j = idents.index(to_ident.strip().upper())
        except ValueError:
            return []
        return list(self.points[i : j + 1]) if i <= j else list(reversed(self.points[j : i + 1]))


@dataclass
class NavDatabase:
    """Everything a loader produced, plus the cycle it came from.

    Idents are not globally unique (e.g. NDB "AA" exists in several regions), so
    every store maps ``ident -> list``.
    """

    cycle: str | None = None
    effective: date | None = None
    expires: date | None = None
    source: str = ""          # "FAA CIFP", "X-Plane", ...

    waypoints: dict[str, list[Waypoint]] = field(default_factory=dict)
    vhf: dict[str, list[VhfNavaid]] = field(default_factory=dict)
    ndb: dict[str, list[NdbNavaid]] = field(default_factory=dict)
    airports: dict[str, Airport] = field(default_factory=dict)  # ICAO idents are unique
    airways: dict[str, Airway] = field(default_factory=dict)  # ident -> Airway

    # Procedures are parsed lazily: raw CIFP record lines are kept here, keyed by
    # (airport, kind, ident), and turned into `Procedure` objects on first
    # request (a session touches only a handful, and eager parsing of every US
    # procedure leg is ~20 s). See `add_procedure_lines` / `procedure`.
    _proc_lines: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    _proc_cache: dict[tuple[str, str, str], Procedure] = field(default_factory=dict)

    notes: list[str] = field(default_factory=list)  # parser warnings, skip counts

    # -- population helpers ------------------------------------------------
    def add_waypoint(self, wp: Waypoint) -> None:
        self.waypoints.setdefault(wp.ident, []).append(wp)

    def add_vhf(self, nav: VhfNavaid) -> None:
        self.vhf.setdefault(nav.ident, []).append(nav)

    def add_ndb(self, nav: NdbNavaid) -> None:
        self.ndb.setdefault(nav.ident, []).append(nav)

    def add_airport(self, apt: Airport) -> None:
        self.airports[apt.ident] = apt

    def add_procedure_lines(self, airport: str, kind: str, ident: str, lines: list[str]) -> None:
        self._proc_lines[(airport, kind, ident)] = lines

    def add_procedure(self, proc: Procedure) -> None:
        """Insert an already-built procedure (used by non-CIFP loaders / tests)."""
        self._proc_cache[(proc.airport, proc.kind, proc.ident)] = proc
        self._proc_lines.setdefault((proc.airport, proc.kind, proc.ident), [])

    def add_airway(self, awy: Airway) -> None:
        self.airways[awy.ident] = awy

    # -- counts -------------------------------------------------------
    def __len__(self) -> int:
        return (
            sum(len(v) for v in self.waypoints.values())
            + sum(len(v) for v in self.vhf.values())
            + sum(len(v) for v in self.ndb.values())
            + len(self.airports)
        )

    def counts(self) -> dict[str, int]:
        return {
            "waypoints": sum(len(v) for v in self.waypoints.values()),
            "vhf": sum(len(v) for v in self.vhf.values()),
            "ndb": sum(len(v) for v in self.ndb.values()),
            "airports": len(self.airports),
            "runways": sum(len(a.runways) for a in self.airports.values()),
            "procedures": len(self._proc_lines),
            "airways": len(self.airways),
            "airport_comms": sum(1 for a in self.airports.values() if a.comms),
        }

    # -- lookup ---------------------------------------------------------
    def airport(self, ident: str) -> Airport | None:
        return self.airports.get(ident.strip().upper())

    def _build_procedure(self, key: tuple[str, str, str]) -> Procedure:
        cached = self._proc_cache.get(key)
        if cached is not None:
            return cached
        from .cifp import build_procedure  # lazy: breaks the model<->cifp cycle

        proc = build_procedure(*key, self._proc_lines[key])
        self._proc_cache[key] = proc
        return proc

    def procs(self, airport: str, kind: str | None = None) -> list[Procedure]:
        apt = airport.strip().upper()
        keys = [k for k in self._proc_lines if k[0] == apt and (kind is None or k[1] == kind)]
        return [self._build_procedure(k) for k in keys]

    def approaches(self, airport: str) -> list[Procedure]:
        return self.procs(airport, "approach")

    def sids(self, airport: str) -> list[Procedure]:
        return self.procs(airport, "sid")

    def stars(self, airport: str) -> list[Procedure]:
        return self.procs(airport, "star")

    def procedure(self, airport: str, ident: str) -> Procedure | None:
        apt, ident = airport.strip().upper(), ident.strip().upper()
        for k in self._proc_lines:
            if k[0] == apt and k[2] == ident:
                return self._build_procedure(k)
        return None

    def airway(self, ident: str) -> Airway | None:
        return self.airways.get(ident.strip().upper())

    # -- nearest-N -----------------------------------------------------
    @staticmethod
    def _nearest(items, ref: Point, n: int, max_nm: float | None):
        if max_nm is not None:
            # cheap bounding-box prefilter before the haversine sort
            dlat = max_nm / 60.0 * 1.3
            clat = max(math.cos(math.radians(ref.lat)), 0.02)
            dlon = dlat / clat
            items = [
                it for it in items
                if abs(it.pos.lat - ref.lat) <= dlat
                and abs((it.pos.lon - ref.lon + 180.0) % 360.0 - 180.0) <= dlon
            ]
        scored = sorted(((great_circle_nm(it.pos, ref), it) for it in items), key=lambda t: t[0])
        if max_nm is not None:
            scored = [t for t in scored if t[0] <= max_nm]
        return [it for _, it in scored[:n]]

    def nearest_airports(self, ref: Point, n: int = 9, *, max_nm: float | None = None):
        return self._nearest(self.airports.values(), ref, n, max_nm)

    def nearest_navaids(self, ref: Point, n: int = 9, *, max_nm: float | None = None,
                        vhf: bool = True, ndb: bool = True, loc: bool = False):
        """Nearest navaids to ``ref``. Localizers are **excluded** unless
        ``loc=True`` (they're not a Direct-To / NRST-VOR target)."""
        pool: list = []
        if vhf:
            pool += (nav for lst in self.vhf.values() for nav in lst
                     if loc or not nav.is_localizer)
        if ndb:
            pool += (nav for lst in self.ndb.values() for nav in lst)
        return self._nearest(pool, ref, n, max_nm)

    def nearest_waypoints(self, ref: Point, n: int = 9, *, max_nm: float | None = None):
        pool = [wp for lst in self.waypoints.values() for wp in lst]
        return self._nearest(pool, ref, n, max_nm)

    def find(self, ident: str, *, near: Point | None = None, kind: str | None = None):
        """All navaids / waypoints / airports matching ``ident``
        (VHF, then NDB, then fix, then airport).

        With ``near`` given, results are sorted by distance from that point.
        ``kind`` (one of "vhf" / "ndb" / "waypoint" / "airport") restricts
        the search to that single category - e.g. the GNS WPT group's VOR
        page should only ever resolve a VHF navaid, not any airport or
        intersection that happens to share the identifier. ``None`` (the
        default) searches every category, as before.
        """
        ident = ident.strip().upper()
        hits: list = []
        if kind in (None, "vhf"):
            hits += self.vhf.get(ident, [])
        if kind in (None, "ndb"):
            hits += self.ndb.get(ident, [])
        if kind in (None, "waypoint"):
            hits += self.waypoints.get(ident, [])
        if kind in (None, "airport") and ident in self.airports:
            hits.append(self.airports[ident])
        if near is not None:
            hits.sort(key=lambda e: great_circle_nm(e.pos, near))
        return hits

    def nearest_fix(self, ident: str, ref: Point, *, kind: str | None = None):
        """The single closest entry matching ``ident`` to ``ref``, or ``None``.

        This is how ARINC procedure legs resolve a bare fix ident.
        """
        hits = self.find(ident, near=ref, kind=kind)
        return hits[0] if hits else None
