"""gpsnav.py - the variant-independent GPS/RNAV navigator core.

This is the trainer's "brain": it consumes decoded IFR-1 events (see
``ifr1.Event``) and an ownship position feed, maintains the flight plan /
Direct-To / OBS / CDI-source state, and each loop exposes a :class:`NavState`
snapshot for ``instruments.py`` and ``render.py``.

The avionics logic is identical between the Garmin GNS 530 and GNS 430 - only
the display differs (screen size, number of visible data rows, bezel layout).
That view configuration lives in :class:`Variant`; ``gns530.py`` and
``gns430.py`` are thin wrappers that bind one to :class:`GpsNav`.

Scope of this module:

* flight plan: append / insert / delete / activate-leg, load a CIFP procedure
* Direct-To (present position -> a fix, incl. a fix already in the plan)
* automatic waypoint sequencing with turn anticipation; SUSP at the end
* OBS mode (course select through the active fix, no sequencing, TO/FROM)
* CDI source flag GPS <-> VLOC (auto-switch on approach is a later slice)
* nav-data-expiry annunciation
* a light NAV/WPT/AUX/NRST page + big/small-knob + CRSR cursor scaffold

Conventions (see ARCHITECTURE.md sec.4): angles are degrees 0..360 **true**
unless a name ends ``_mag``; cross-track ``+`` == ownship is right of course.
Magnetic display variation is applied downstream in ``instruments.py``.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum

from navmath import (
    along_track_nm,
    angle_diff,
    arc_length_nm,
    arc_progress_deg,
    arc_sweep_deg,
    arc_track,
    arc_xtk_nm,
    cross_track_nm,
    destination,
    great_circle_nm,
    hold_entry,
    initial_bearing,
    intersect_radials,
    norm360,
    reciprocal,
    turn_anticipation_nm,
)
from navdata.model import LegType, NavDatabase, Point

try:  # ifr1 imports `hid`; keep gpsnav importable (and testable) without it
    from ifr1 import Event, Mode
except Exception:  # pragma: no cover - exercised only when hidapi is absent
    Event = None  # type: ignore

    class Mode(int, Enum):  # minimal stand-in matching ifr1.Mode values
        COM1 = 0
        COM2 = 1
        NAV1 = 2
        NAV2 = 3
        FMS1 = 4
        FMS2 = 5
        AP = 6
        XPDR = 7


__all__ = [
    "PlanWaypoint",
    "FlightPlan",
    "DirectTo",
    "DirectToEntry",
    "ProcSelect",
    "FplMenu",
    "VnavProfile",
    "VnavStatus",
    "NavState",
    "PageCursor",
    "Variant",
    "VARIANT_530",
    "VARIANT_430",
    "GpsNav",
    "PAGE_GROUPS",
    "CATALOG_SIZE",
]

def _clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# distance (nm) inside which a fly-over / final fix is considered "reached"
_FIX_CAPTURE_NM = 0.30
# how far ahead of a sequence point the "WPT" alert starts (seconds of flight)
_WPT_ALERT_SEC = 10.0
# a hold's inbound leg (see _step_hold): once actual distance-to-fix has
# opened back up by this much past its closest point of approach, the
# aircraft is past the fix and moving away - treat the CPA as "reached"
# rather than waiting indefinitely for an exact _FIX_CAPTURE_NM hit that a
# wide entry (e.g. a teardrop's ~210 deg return turn) may never quite land.
_HOLD_TURN_DONE_DEG = 100.0   # inbound turn hands back to normal intercept steering inside this
_HOLD_CPA_MARGIN_NM = 1.0

# GPS CDI full-scale (each side), by phase of flight - GNS 530 Pilot's Guide
# 190-00181-00 sec.3.3 / 10.4: 5.0 nm enroute, 1.0 nm terminal, 0.30 nm approach.
_CDI_ENROUTE_NM = 5.0
_CDI_TERMINAL_NM = 1.0
_CDI_APPROACH_NM = 0.30
# arm terminal within this range of the destination; approach within this of the
# active fix once an approach procedure is loaded. Sec.10.4 also arms terminal
# scale within the same 30 nm of the *departure* airport (ramping 1.0 -> 5.0 nm
# outbound), symmetric with the arrival-side ramp.
_CDI_TERMINAL_ARM_NM = 30.0
_CDI_APPROACH_ARM_NM = 2.0
# AUX > Setup > CDI/Alarms: Auto (phase-of-flight, the default/only mode
# until now) or a fixed ceiling the scale never widens past - `None` is Auto.
_CDI_ALARM_CHOICES: tuple[float | None, ...] = (None, _CDI_ENROUTE_NM, _CDI_TERMINAL_NM, _CDI_APPROACH_NM)
# the scale slews toward its target rather than stepping (sec.6.2 "gradual ...
# transition"): 5.0 -> 1.0 nm in ~30 s.
_CDI_RAMP_NM_PER_S = (5.0 - 1.0) / 30.0

# Flight Plan Catalog: stored plans FPL 01-19 (FPL 00 is always the active
# plan, `GpsNav.fpl` itself) - Pilot's Guide sec.5.2.
CATALOG_SIZE = 19


# --------------------------------------------------------------------------- #
# flight-plan data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _RwyFix:
    """A runway-threshold 'fix' (``RW08``) for a procedure leg - not in the
    navaid stores, so `_resolve_proc_fix` builds one from the airport."""

    ident: str
    pos: Point


@dataclass(frozen=True, slots=True)
class PlanWaypoint:
    """One waypoint in a flight plan, already resolved to a position.

    Procedure metadata (all default off for a plain enroute fix) lets the
    sequencer stop at the right places and the renderer annotate the leg.
    """

    ident: str
    pos: Point
    kind: str = "wpt"            # wpt | apt | vor | ndb | ppos (Direct-To origin)
    is_iaf: bool = False
    is_faf: bool = False
    is_map: bool = False         # missed-approach point -> SUSP on arrival
    fly_over: bool = False
    hold: bool = False           # a holding pattern here - flown, not just SUSPed
    manual: bool = False         # manual-termination leg (FM/VM) -> SUSP on arrival
    synthetic: bool = False      # position synthesised from a non-fix leg
    # hold geometry (only meaningful when ``hold`` is set) - HF (hold-in-lieu of
    # a procedure turn) flies exactly one circuit then auto-continues; HM/HA
    # (e.g. a missed-approach hold) repeats until the pilot releases SUSP.
    hold_single_circuit: bool = False
    hold_inbound_true: float | None = None
    hold_turn: str = "R"
    hold_leg_min: float | None = None    # time-based outbound leg length
    hold_leg_nm: float | None = None     # DME-based outbound leg length (overrides time)
    # A DME / constant-radius arc (ARINC AF / RF) ENDING at this fix: the leg from
    # the previous fix is flown around ``arc_centre`` (``arc_turn`` R = clockwise),
    # not as a straight line. The radius is centre -> this fix.
    arc_centre: Point | None = None
    arc_turn: str = "R"

    @classmethod
    def from_entry(cls, entry, **flags) -> "PlanWaypoint":
        """Build from any nav-db entry that has ``.ident`` and ``.pos``."""
        kind = {
            "Airport": "apt",
            "VhfNavaid": "vor",
            "NdbNavaid": "ndb",
        }.get(type(entry).__name__, "wpt")
        return cls(entry.ident, entry.pos, kind, **flags)

    @property
    def stop_here(self) -> bool:
        """The sequencer suspends rather than auto-advancing past this fix."""
        return self.is_map or self.hold or self.manual


@dataclass
class FlightPlan:
    """An ordered list of waypoints. The active leg runs
    ``waypoints[active - 1] -> waypoints[active]``.
    """

    waypoints: list[PlanWaypoint] = field(default_factory=list)
    active: int = 1             # index of the TO waypoint
    comment: str = ""           # catalog label, e.g. "KBOS/KJFK" (Pilot's Guide sec.5.2)

    def __len__(self) -> int:
        return len(self.waypoints)

    def clear(self) -> None:
        self.waypoints.clear()
        self.active = 1

    def append(self, wp: PlanWaypoint) -> None:
        self.waypoints.append(wp)

    def insert(self, index: int, wp: PlanWaypoint) -> None:
        index = max(0, min(index, len(self.waypoints)))
        self.waypoints.insert(index, wp)
        if index < self.active:
            self.active += 1

    def delete(self, index: int) -> None:
        if not 0 <= index < len(self.waypoints):
            return
        self.waypoints.pop(index)
        if index < self.active:
            self.active -= 1
        self.active = max(1, min(self.active, max(1, len(self.waypoints) - 1)))

    def index_of(self, ident: str) -> int:
        ident = ident.strip().upper()
        for i, wp in enumerate(self.waypoints):
            if wp.ident == ident:
                return i
        return -1

    def activate_leg(self, to_index: int) -> None:
        if 1 <= to_index < len(self.waypoints):
            self.active = to_index

    @property
    def has_active_leg(self) -> bool:
        return 1 <= self.active < len(self.waypoints)

    @property
    def from_wp(self) -> PlanWaypoint | None:
        return self.waypoints[self.active - 1] if self.has_active_leg else None

    @property
    def to_wp(self) -> PlanWaypoint | None:
        return self.waypoints[self.active] if self.has_active_leg else None

    def wp_after_active(self) -> PlanWaypoint | None:
        i = self.active + 1
        return self.waypoints[i] if 0 <= i < len(self.waypoints) else None


@dataclass(frozen=True, slots=True)
class DirectTo:
    """An active Direct-To: a straight course from ``origin`` to ``target``.

    ``course`` is frozen at activation (a real GNS holds the original DTK, so
    off-course flight shows as cross-track, not a moving needle).
    """

    target: PlanWaypoint
    origin: Point
    course: float               # true, deg
    fpl_index: int = -1         # >=0 if the target is also in the flight plan


# --------------------------------------------------------------------------- #
# derived nav state (what instruments / render consume)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NavState:
    valid: bool = False                 # is there a navigable active leg?
    mode: str = "NOWPT"                 # LEG | DTO | OBS | SUSP | NOWPT
    cdi_source: str = "GPS"             # GPS | VLOC
    from_ident: str = ""
    to_ident: str = ""
    dtk: float | None = None           # desired track, true deg
    brg: float | None = None           # bearing to the TO fix, true deg
    xtk_nm: float | None = None        # + == right of course
    tke: float | None = None           # track angle error, signed deg
    dist_nm: float | None = None       # distance to the TO fix
    dtg_nm: float | None = None        # distance to go along the active leg
    to_from: str = ""                  # TO | FROM (OBS only; else TO)
    wpt_alert: bool = False            # "WPT" - sequence imminent ("NEXT DTK ...")
    turn_anticipation: bool = False
    turn_now: bool = False             # at the turn point ("TURN TO ...")
    next_dtk: float | None = None      # DTK of the leg after the sequence
    cdi_scale_nm: float | None = None  # GPS CDI full-scale each side (5.0/1.0/0.30)
    annunciators: tuple[str, ...] = ()  # SUSP / OBS / WPT / LOI ...


# --------------------------------------------------------------------------- #
# page / cursor scaffold
# --------------------------------------------------------------------------- #
PAGE_GROUPS: dict[str, list[str]] = {
    "NAV": ["Default NAV", "Map", "NAV/COM", "Position", "Flight Plan",
            "Flight Plan Catalog", "VNAV"],
    "WPT": ["Airport", "Intersection", "NDB", "VOR"],
    "AUX": ["Trip Planning", "Utility", "Setup", "Nav Data", "Weather", "Charts"],
    "NRST": ["Nearest APT", "Nearest VOR", "Nearest NDB", "Nearest INT"],
}
_GROUP_ORDER = list(PAGE_GROUPS)
# each WPT sub-page resolves only its own category (Pilot's Guide sec.4.2:
# the Airport/Intersection/NDB/VOR pages are separate lookups) - the value
# matches navdata.NavDatabase.find/nearest_fix's ``kind`` argument.
_WPT_PAGE_KIND = {"Airport": "airport", "Intersection": "waypoint",
                  "NDB": "ndb", "VOR": "vhf"}


@dataclass
class PageCursor:
    """Big-knob selects the page group / moves the cursor between fields;
    small-knob selects the page / edits the active field. CRSR toggles."""

    group: int = 0
    page: int = 0
    cursor_on: bool = False
    field: int = 0
    # last page shown in each OTHER group, keyed by group index - the real
    # 530 is "sticky": leaving a group and coming back returns to whichever
    # page you were last on there, it doesn't reset to page 1 every time.
    # (dataclasses.field, fully qualified: this class's own `field` attribute
    # above shadows the bare name by the time this line executes.)
    _last_page: dict = dataclasses.field(default_factory=dict)

    @property
    def group_name(self) -> str:
        return _GROUP_ORDER[self.group]

    @property
    def page_name(self) -> str:
        return PAGE_GROUPS[self.group_name][self.page]

    def on_outer(self, delta: int) -> None:
        """Big knob: page group / cursor field. The GNS knobs do NOT wrap -
        they stop at the ends of the list."""
        if not delta:
            return
        if self.cursor_on:
            self.field = max(0, self.field + (1 if delta > 0 else -1))
        else:
            self._last_page[self.group] = self.page
            self.group = max(0, min(len(_GROUP_ORDER) - 1, self.group + delta))
            n = len(PAGE_GROUPS[self.group_name])
            self.page = min(self._last_page.get(self.group, 0), n - 1)

    def on_inner(self, delta: int) -> None:
        """Small knob: page within the group (no wrap)."""
        if not delta:
            return
        if self.cursor_on:
            return  # field editing is render-tier; nothing to do yet
        pages = PAGE_GROUPS[self.group_name]
        self.page = max(0, min(len(pages) - 1, self.page + delta))

    def toggle_cursor(self) -> None:
        self.cursor_on = not self.cursor_on
        self.field = 0

    def go_to_default_nav(self) -> None:
        """Jump to NAV group / Default NAV page (GNS "hold CLR" shortcut)."""
        self.group = _GROUP_ORDER.index("NAV")
        self.page = 0
        self.cursor_on = False
        self.field = 0

    def go_to_flight_plan(self) -> None:
        """Jump to the NAV / Flight Plan page (FPL key)."""
        self.group = _GROUP_ORDER.index("NAV")
        self.page = PAGE_GROUPS["NAV"].index("Flight Plan")
        self.cursor_on = False
        self.field = 0

    def go_to_vnav(self) -> None:
        """Jump to the NAV / VNAV page (ALT bezel key)."""
        self.group = _GROUP_ORDER.index("NAV")
        self.page = PAGE_GROUPS["NAV"].index("VNAV")
        self.cursor_on = False
        self.field = 0


# --------------------------------------------------------------------------- #
# Direct-To identifier entry (Select Direct-To Waypoint page)
# --------------------------------------------------------------------------- #
_IDENT_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_IDENT_LEN = 6


@dataclass
class DirectToEntry:
    """The editable identifier buffer behind the Direct-To page: the big knob
    moves the cursor, the small knob spins the character under it."""

    chars: list[str] = field(default_factory=lambda: [" "] * _IDENT_LEN)
    cursor: int = 0
    confirming: bool = False   # True once ENT has confirmed the ident -> "Activate?"

    @classmethod
    def seeded(cls, ident: str = "") -> "DirectToEntry":
        buf = list(ident.strip().upper()[:_IDENT_LEN].ljust(_IDENT_LEN))
        cur = max(0, min(_IDENT_LEN - 1, len(ident.strip()) - 1)) if ident.strip() else 0
        return cls(buf, cur)

    def ident(self) -> str:
        return "".join(self.chars).strip()

    def move_cursor(self, delta: int) -> None:
        self.cursor = max(0, min(_IDENT_LEN - 1, self.cursor + delta))

    def scroll_char(self, delta: int) -> None:
        cur = self.chars[self.cursor]
        i = _IDENT_CHARS.find(cur if cur in _IDENT_CHARS else " ")
        self.chars[self.cursor] = _IDENT_CHARS[(i + delta) % len(_IDENT_CHARS)]


# --------------------------------------------------------------------------- #
# PROC key - approach / arrival / departure selector
# --------------------------------------------------------------------------- #
# Menu labels; the ACTIVATE choices only appear once an approach is loaded.
_PROC_APPROACH = "SELECT APPROACH"
_PROC_ARRIVAL = "SELECT ARRIVAL"
_PROC_DEPARTURE = "SELECT DEPARTURE"
_PROC_ACT_APPR = "ACTIVATE APPROACH"
_PROC_ACT_VTF = "ACTIVATE VECTORS-TO-FINAL"
_PROC_VECTORS = "VECTORS"
_PROC_MENU_KIND = {_PROC_APPROACH: "approach", _PROC_ARRIVAL: "star",
                   _PROC_DEPARTURE: "sid"}


@dataclass
class ProcSelect:
    """Modal state behind the PROC key: a wizard (menu -> pick procedure ->
    pick transition -> Load?/Activate?) that ends in
    :meth:`GpsNav.load_procedure` (and, for "Activate?", `_activate_approach`
    right after). Only display state lives here - the database lookups are
    :class:`GpsNav`'s job.

    The final Load?/Activate? step mirrors the Pilot's Guide (190-00181-00
    Rev. H) sec.5 verbatim, p.61 step 5: "Rotate the large right knob to
    highlight 'Load?' or 'Activate?' (approaches only) and press ENT.
    ('Load?' adds the procedure to the flight plan without immediately using
    it for navigation guidance... 'Activate?' adds the procedure to the
    flight plan and begins navigating [it].)" - SIDs/STARs only ever offer
    "Load?" at this step (arrivals/departures are activated later, the same
    way any other flight-plan leg is)."""

    airport: str = ""
    step: str = "MENU"                    # MENU | PROC | TRANS | LOADACT
    kind: str = ""                        # approach | star | sid  (step PROC on)
    proc_ident: str = ""                  # chosen procedure (step TRANS on)
    transition: str | None = None         # chosen transition (step LOADACT)
    has_trans_step: bool = True           # False if TRANS was skipped (no choices)
    options: list[str] = field(default_factory=list)
    sel: int = 0

    def move(self, delta: int) -> None:
        if delta and self.options:
            self.sel = max(0, min(len(self.options) - 1, self.sel + delta))

    @property
    def current(self) -> str:
        return self.options[self.sel] if 0 <= self.sel < len(self.options) else ""

    @property
    def title(self) -> str:
        if self.step == "MENU":
            return f"PROCEDURES  {self.airport}".rstrip()
        if self.step == "PROC":
            return f"{self.airport}  {self.kind.upper()}"
        if self.step == "TRANS":
            return f"{self.proc_ident}  TRANSITION"
        return f"{self.proc_ident}  LOAD/ACTIVATE"


# --------------------------------------------------------------------------- #
# MNU key - page-context menu (Flight Plan / Flight Plan Catalog)
# --------------------------------------------------------------------------- #
_FPL_MENU = ["INVERT FLT PLAN", "COPY FLT PLAN", "DELETE FLT PLAN"]
_CATALOG_MENU = ["COPY FLT PLAN", "SORT CATALOG", "DELETE FLT PLAN"]


@dataclass
class FplMenu:
    """The small pop-up the MNU key opens on the Flight Plan / Flight Plan
    Catalog pages (Pilot's Guide sec.5.2): big knob picks a row, ENT applies
    it, CLR or a second MNU press cancels without doing anything."""

    options: list[str] = field(default_factory=lambda: list(_FPL_MENU))
    sel: int = 0

    def move(self, delta: int) -> None:
        if delta and self.options:
            self.sel = max(0, min(len(self.options) - 1, self.sel + delta))

    @property
    def current(self) -> str:
        return self.options[self.sel] if 0 <= self.sel < len(self.options) else ""


# --------------------------------------------------------------------------- #
# VNAV - a straight-line descent/climb profile to a flight-plan fix
# --------------------------------------------------------------------------- #
# The real GNS 530's VNAV page (Pilot's Guide sec.10/11, "Vertical
# Navigation") programs a vertical SPEED ("VS Profile" / "Vertical Speed
# Desired", ft/min - default a 400 fpm descent rate), not a flight-path
# angle - there is no angle field on the real unit at all. The live "VSR"
# (Vertical Speed Required) readout is what tells the pilot the rate needed
# right now to stay on that profile; this trainer's earlier angle_deg field
# was a misremembered invention, not the real GNS 530 behavior - corrected
# per playtest feedback (round 5), cross-checked against the Pilot's Guide's
# actual field labels.
_VNAV_MIN_VS_FPM = -2000.0
_VNAV_MAX_VS_FPM = 2000.0
_VNAV_DEFAULT_VS_FPM = -400.0
_VNAV_MIN_GS_KT = 35.0          # Pilot's Guide: VNAV needs > 35 kt groundspeed
_FT_PER_NM = 6076.115949


@dataclass(frozen=True, slots=True)
class VnavProfile:
    """The pilot-entered VNAV target: cross ``target_ident`` at
    ``target_alt_ft``, descending/climbing at ``vs_fpm`` (+ = climb,
    - = descend - Pilot's Guide's "VS Profile"/"Vertical Speed Desired"
    field; this trainer's version has no "distance before" offset field)."""

    target_ident: str = ""
    target_alt_ft: float = 0.0
    vs_fpm: float = _VNAV_DEFAULT_VS_FPM
    armed: bool = False


@dataclass(frozen=True, slots=True)
class VnavStatus:
    """What `GpsNav.vnav_status()` reports each tick - deliberately decoupled
    from `update()`/`NavState` because altitude isn't otherwise part of this
    core's state (`update()` only ever sees pos/track/gs)."""

    valid: bool = False                      # armed, target resolved, ahead of us, GS usable
    target_ident: str = ""
    target_alt_ft: float = 0.0
    vs_fpm: float = _VNAV_DEFAULT_VS_FPM
    distance_to_target_nm: float | None = None
    required_alt_ft: float | None = None     # ideal altitude at the present position
    tod_distance_nm: float | None = None     # distance-to-go needed to fly the profile
    distance_to_tod_nm: float | None = None  # + = TOD ahead; - = past TOD, should descend
    deviation_ft: float | None = None        # actual - required; + = above the path
    alert: bool = False                      # within 1 nm of the top of descent
    required_vs_fpm: float | None = None     # VSR - live rate needed right now, independent of vs_fpm
    time_to_tod_min: float | None = None     # ETE to the top of descent at current GS


# --------------------------------------------------------------------------- #
# unit view configuration (530 vs 430)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Variant:
    """How one physical unit displays the shared :class:`GpsNav` state.

    The state machine does not branch on this - only ``render.py`` and the
    thin ``gns530`` / ``gns430`` wrappers read it.
    """

    name: str                       # "GNS 530" / "GNS 430"
    short: str                      # "530" / "430" (CLI / labels)
    screen_rows: int                # data / flight-plan rows the screen shows
    screen_px: tuple[int, int]      # nominal screen pixel size (render scales)
    bezel_dir: str                  # assets/instruments/<bezel_dir>/faceplate.svg
    bezel_aspect: float             # faceplate width / height
    screen_frac: tuple[float, float, float, float]  # screen cutout (x0,y0,x1,y1)
    pages: dict[str, list[str]] = field(default_factory=lambda: PAGE_GROUPS)


# The real GNS 530 faceplate is 165 x 120 mm; the 430 is 165 x 69 mm (same
# width, ~half the height -> the moving-map / data screen shows far fewer rows).
VARIANT_530 = Variant(
    name="GNS 530",
    short="530",
    screen_rows=12,
    screen_px=(320, 234),
    bezel_dir="garmin-gns-530",
    bezel_aspect=165.0 / 120.0,
    screen_frac=(0.163, 0.050, 0.838, 0.765),
)

VARIANT_430 = Variant(
    name="GNS 430",
    short="430",
    screen_rows=5,
    screen_px=(240, 128),
    bezel_dir="garmin-gns-430",
    bezel_aspect=165.0 / 69.0,
    # measured off the faceplate raster: the 430 screen is a big central rect
    screen_frac=(0.235, 0.048, 0.793, 0.884),
)


# --------------------------------------------------------------------------- #
# the state machine
# --------------------------------------------------------------------------- #
class GpsNav:
    def __init__(self, db: NavDatabase, *, today: date | None = None,
                 variant: Variant = VARIANT_530):
        self.db = db
        self.today = today
        self.variant = variant
        self.fpl = FlightPlan()
        self.dto: DirectTo | None = None
        self.suspended = False
        self.obs_active = False
        self.obs_course = 0.0           # true deg
        self.cdi_source = "GPS"
        self.cursor = PageCursor()
        self.messages: list[str] = []
        self._dto_dialog: DirectToEntry | None = None
        self._proc_dialog: ProcSelect | None = None   # PROC key selector overlay
        self._proc_airport = ""                        # airport of the last-loaded procedure
        self.wpt_entry = DirectToEntry.seeded("")   # WPT-page identifier lookup
        self.nrst_sel = 0                            # selection row on a NRST page
        self._fpl_edit: dict | None = None           # {"row": int, "buf": DirectToEntry?}
        self._pending_menu = False       # MNU pressed; menu content is render-tier
        self._fpl_menu: FplMenu | None = None    # MNU pop-up: Flight Plan / Catalog page
        self.fpl_catalog: list[FlightPlan | None] = [None] * CATALOG_SIZE  # FPL 01-19
        self.cat_sel = 0                             # selected row on the Catalog page
        self.vnav = VnavProfile()                    # the VNAV page's pilot-entered target
        self.vnav_field = 0                          # 0=target 1=altitude 2=angle
        self.wx_sel = 0                               # selected station on the Weather page
        self.wx_scroll = 0                            # line offset into that station's METAR/TAF text
        self.chart_airport_sel = 0                    # selected airport on the Charts page
        self.chart_sel = 0                            # selected chart within that airport
        self._expiry_warned = False
        self._approach_active = False    # an approach procedure is loaded
        self.approach_freq: float | None = None   # VLOC freq the GNS loads to standby
        self.approach_ref: str = ""               # ... its ident (ILS / VOR)
        self._auto_vloc_done = False              # one-shot GPS->VLOC CDI switch
        self._susp_at: tuple | None = None  # (active, ident) we auto-suspended at
        self._hold_state: dict | None = None  # in-progress holding-pattern circuit
        self.cdi_alarm_max_nm: float | None = None  # AUX>Setup>CDI/Alarms override; None = Auto
        self._cdi_scale = _CDI_ENROUTE_NM  # live (slewed) GPS CDI full-scale, nm
        self._cdi_scale_init = False       # snap once instead of slewing from the
                                            # enroute default seeded above (a fresh
                                            # start near the departure airport should
                                            # read 1.0 nm immediately, not visibly
                                            # ramp 5.0 -> 1.0 over the first few seconds)
        self._nav = NavState()
        # last ownship sample (set by update())
        self._pos: Point | None = None
        self._track = 0.0
        self._gs = 0.0

    # -- flight-plan editing ---------------------------------------------
    def load_flight_plan(self, idents: list[str], *, ref: Point | None = None) -> list[str]:
        """Replace the flight plan with these fix idents. Returns the idents that
        could not be resolved (kept out of the plan)."""
        self.fpl.clear()
        self.dto = None
        self.suspended = False
        self._approach_active = False
        self.approach_freq = None
        self.approach_ref = ""
        self._auto_vloc_done = False
        self._susp_at = None
        self._hold_state = None
        self._proc_airport = ""
        self.vnav = VnavProfile()
        missing: list[str] = []
        anchor = ref
        for ident in idents:
            entry = self._resolve(ident, anchor)
            if entry is None:
                missing.append(ident)
                continue
            self.fpl.append(PlanWaypoint.from_entry(entry))
            anchor = entry.pos
        self.fpl.active = 1 if len(self.fpl) >= 2 else 1
        return missing

    def load_procedure(self, airport: str, ident: str, transition: str | None = None,
                       *, append: bool = True) -> int:
        """Append a CIFP procedure to the flight plan.

        Fix-terminating legs resolve directly; non-fix legs (course/heading to
        altitude / DME / radial / intercept, DME arcs, procedure turns, holds,
        manual terminations) get a **synthesised** fix so the whole procedure
        loads. The missed-approach point, holds and manual legs are flagged so
        the sequencer suspends there instead of running on.
        Returns the number of waypoints added.
        """
        proc = self.db.procedure(airport, ident)
        if proc is None:
            return 0
        self._proc_airport = airport            # remember it for the PROC menu
        if not append:
            self.fpl.clear()
            self._approach_active = False
        is_appr = getattr(proc, "kind", "") == "approach"
        if is_appr:
            self._approach_active = True
            self._auto_vloc_done = False
        apt = self.db.airport(airport)
        anchor = (self.fpl.waypoints[-1].pos if self.fpl.waypoints
                  else (apt.pos if apt is not None else None))
        prev_true = None
        added = 0
        legs = proc.assemble(transition)
        for leg in legs:
            for wp, brg in self._expand_leg(leg, anchor, prev_true, apt):
                last = self.fpl.waypoints[-1] if self.fpl.waypoints else None
                if last is not None and last.ident == wp.ident and not wp.synthetic:
                    self.fpl.waypoints[-1] = replace(   # IF then HF at the same fix
                        last, is_iaf=last.is_iaf or wp.is_iaf,
                        is_faf=last.is_faf or wp.is_faf, is_map=last.is_map or wp.is_map,
                        fly_over=last.fly_over or wp.fly_over, hold=last.hold or wp.hold,
                        manual=last.manual or wp.manual,
                        hold_single_circuit=last.hold_single_circuit or wp.hold_single_circuit,
                        hold_inbound_true=(wp.hold_inbound_true if wp.hold
                                           else last.hold_inbound_true),
                        hold_turn=(wp.hold_turn if wp.hold else last.hold_turn),
                        hold_leg_min=(wp.hold_leg_min if wp.hold else last.hold_leg_min),
                        hold_leg_nm=(wp.hold_leg_nm if wp.hold else last.hold_leg_nm))
                    anchor = last.pos
                    continue
                self.fpl.append(wp)
                anchor = wp.pos
                if brg is not None:
                    prev_true = brg
                added += 1
        if not self.fpl.has_active_leg and len(self.fpl) >= 2:
            self.fpl.active = 1
        if is_appr:
            self.approach_freq, self.approach_ref = self._approach_vloc_freq(legs, apt)
        return added

    def _approach_vloc_freq(self, legs, apt) -> tuple[float | None, str]:
        """The VLOC frequency the GNS auto-loads to standby for a VOR/ILS
        approach: the runway's ILS, else a VOR/LOC referenced by the final legs."""
        # 1) a runway leg -> that runway's ILS localizer
        if apt is not None:
            for leg in legs:
                rwy = apt.runways.get((leg.fix_ident or "").strip().upper())
                if rwy is not None and rwy.ils_ident:
                    for n in self.db.vhf.get(rwy.ils_ident, []):
                        return n.freq_mhz, n.ident
        # 2) a recommended navaid on the final segment (FAF onward)
        seen_faf = False
        for leg in legs:
            seen_faf = seen_faf or leg.is_faf
            if seen_faf and leg.recnav_ident:
                e = self._resolve(leg.recnav_ident, apt.pos if apt is not None else self._pos)
                if e is not None and getattr(e, "freq_mhz", None):
                    return e.freq_mhz, e.ident
        return None, ""

    # -- procedure-leg synthesis --------------------------------------
    def _magvar_at(self, pos: Point | None) -> float:
        if pos is None:
            return 0.0
        try:
            navs = self.db.nearest_navaids(pos, 1, max_nm=250.0)
            return float(getattr(navs[0], "magvar_deg", 0.0)) if navs else 0.0
        except Exception:                          # pragma: no cover - defensive
            return 0.0

    def _leg_course_true(self, leg, anchor: Point | None, prev_true: float | None) -> float:
        if leg.course_mag is not None and anchor is not None:
            return norm360(leg.course_mag + self._magvar_at(anchor))
        return prev_true if prev_true is not None else 0.0

    def _recnav_pos(self, leg) -> Point | None:
        if not leg.recnav_ident:
            return None
        ref = self._pos or (self.fpl.waypoints[-1].pos if self.fpl.waypoints else None)
        e = self._resolve(leg.recnav_ident, ref)
        return e.pos if e is not None else None

    def _resolve_proc_fix(self, ident: str, anchor, apt):
        """Resolve a procedure-leg fix ident. A runway fix (``RW08``) isn't in
        the navaid stores - take its threshold from the airport."""
        entry = self._resolve(ident, anchor)
        if entry is None and apt is not None and ident.strip().upper().startswith("RW"):
            rwy = apt.runways.get(ident.strip().upper())
            if rwy is not None:
                return _RwyFix(ident.strip().upper(), rwy.threshold)
        return entry

    def _expand_leg(self, leg, anchor: Point | None, prev_true: float | None, apt=None):
        """Yield ``(PlanWaypoint, outbound_true | None)`` for one procedure leg."""
        lt = leg.leg_type
        flags = dict(is_iaf=leg.is_iaf, is_faf=leg.is_faf, is_map=leg.is_map,
                     fly_over=leg.is_flyover)
        crs = self._leg_course_true(leg, anchor, prev_true)

        # --- legs that already end on a real fix -------------------------
        if leg.terminates_at_fix and leg.fix_ident:
            entry = self._resolve_proc_fix(leg.fix_ident, anchor, apt)
            if entry is None:
                return
            hold = lt in (LegType.HF,)
            wp = PlanWaypoint.from_entry(
                entry, hold=hold, hold_single_circuit=hold,
                hold_inbound_true=(crs if hold else None),
                hold_turn=(leg.turn or "R").strip().upper() or "R",
                hold_leg_min=(leg.time_min if hold and leg.time_min is not None else None),
                hold_leg_nm=(leg.distance_nm if hold and leg.distance_nm is not None else None),
                **flags)
            if lt in (LegType.AF, LegType.RF) and anchor is not None:
                centre = self._recnav_pos(leg)
                if centre is not None:
                    wp = replace(wp, arc_centre=centre,
                                 arc_turn=(leg.turn or "R").strip().upper() or "R")
            if wp.arc_centre is not None:      # the course leaving an arc is its tangent
                yield wp, arc_track(wp.arc_centre, wp.arc_turn, entry.pos)
                return
            yield wp, (initial_bearing(anchor, entry.pos) if anchor is not None else None)
            return

        if anchor is None:
            return

        # --- holds / manual terminations: keep the fix, flag it ---------
        if lt in (LegType.HM, LegType.HA, LegType.FM, LegType.VM):
            if leg.fix_ident:
                entry = self._resolve(leg.fix_ident, anchor)
                if entry is not None:
                    is_hold = lt in (LegType.HM, LegType.HA)
                    yield PlanWaypoint.from_entry(
                        entry, hold=is_hold, hold_single_circuit=False,
                        hold_inbound_true=(crs if is_hold else None),
                        hold_turn=(leg.turn or "R").strip().upper() or "R",
                        hold_leg_min=(leg.time_min if is_hold and leg.time_min is not None else None),
                        hold_leg_nm=(leg.distance_nm if is_hold and leg.distance_nm is not None else None),
                        manual=lt in (LegType.FM, LegType.VM), **flags), None
                    return
            p = destination(anchor, crs, 3.0)
            yield PlanWaypoint("(MANUAL)", p, "wpt", manual=True, synthetic=True, **flags), crs
            return

        # --- to-altitude: a point ahead on course/heading -------------
        if lt in (LegType.CA, LegType.VA, LegType.FA):
            alt = leg.alt1_ft or 0
            dist = _clampf(alt / 320.0 if alt else 3.0, 1.5, 10.0)
            start = anchor
            if lt is LegType.FA and leg.fix_ident:
                e = self._resolve(leg.fix_ident, anchor)
                if e is not None:
                    yield PlanWaypoint.from_entry(e, **flags), None
                    start = e.pos
            label = f"({alt // 100:02d}00)" if alt else "(ALT)"
            yield PlanWaypoint(label, destination(start, crs, dist), "wpt",
                               synthetic=True, **flags), crs
            return

        # --- to-DME-distance: endpoint at rho from the recnav ---------
        if lt in (LegType.CD, LegType.VD, LegType.FD, LegType.FC):
            start = anchor
            if lt is LegType.FC and leg.fix_ident:
                e = self._resolve(leg.fix_ident, anchor)
                if e is not None:
                    yield PlanWaypoint.from_entry(e, **flags), None
                    start = e.pos
            if lt is LegType.FC:
                dist = leg.distance_nm or 4.0
            else:
                rec = self._recnav_pos(leg)
                dist = leg.rho or leg.distance_nm or 5.0
                if rec is not None and leg.rho:
                    # march along the course until DME(rec) == rho
                    d, p = 0.0, start
                    for _ in range(40):
                        p = destination(start, crs, d)
                        if great_circle_nm(p, rec) >= leg.rho:
                            break
                        d += 0.5
                    yield PlanWaypoint(f"D{leg.rho:04.1f}", p, "wpt",
                                       synthetic=True, **flags), crs
                    return
            yield PlanWaypoint(f"D{dist:04.1f}", destination(start, crs, dist), "wpt",
                               synthetic=True, **flags), crs
            return

        # --- to-radial: intersect the course with the radial --------
        if lt in (LegType.CR, LegType.VR):
            rec = self._recnav_pos(leg)
            if rec is not None and leg.theta is not None:
                rad_true = norm360(leg.theta + self._magvar_at(rec))
                hit = intersect_radials(anchor, crs, rec, rad_true)
                if hit is not None:
                    yield PlanWaypoint(f"R{leg.theta:03.0f}", hit, "wpt",
                                       synthetic=True, **flags), crs
                    return
            yield PlanWaypoint("(RADIAL)", destination(anchor, crs, 5.0), "wpt",
                               synthetic=True, **flags), crs
            return

        # --- to-intercept: a short lead on course/heading ----------
        if lt in (LegType.CI, LegType.VI):
            yield PlanWaypoint("(INTC)", destination(anchor, crs, 3.0), "wpt",
                               synthetic=True, **flags), crs
            return

        # --- procedure turn: a single outbound extremity ----------
        if lt is LegType.PI:
            base = anchor
            if leg.fix_ident:
                e = self._resolve(leg.fix_ident, anchor)
                if e is not None:
                    base = e.pos
            out = leg.course_mag
            out_true = norm360((out if out is not None else crs) + self._magvar_at(base))
            far = destination(base, out_true, leg.distance_nm or 4.0)
            side = -45.0 if leg.turn == "L" else 45.0
            tip = destination(far, norm360(out_true + 180.0 + side), 1.8)
            # hold_turn/hold_inbound_true aren't otherwise meaningful with
            # hold=False (no HILPT is actually flown for a PI leg) - stashed
            # here purely so render.py's map can draw a proper procedure-turn
            # barb oriented the same way the real course reversal is flown,
            # instead of an undifferentiated dot.
            yield PlanWaypoint("PT", tip, "wpt", synthetic=True,
                               hold_turn=(leg.turn or "R").strip().upper() or "R",
                               hold_inbound_true=norm360(out_true + 180.0),
                               **flags), \
                norm360(out_true + 180.0)
            return

        # unknown / VI-with-no-data: skip quietly

    def direct_to(self, target: str | PlanWaypoint, *, from_pos: Point | None = None) -> bool:
        """Activate Direct-To. ``target`` is a fix ident or a PlanWaypoint.

        If the target is already in the flight plan, the plan is kept and
        sequencing resumes into it after the fix is reached.
        """
        origin = from_pos or self._pos
        if origin is None:
            return False
        if isinstance(target, PlanWaypoint):
            wp = target
            fpl_index = self.fpl.index_of(wp.ident)
        else:
            fpl_index = self.fpl.index_of(target)
            if fpl_index >= 0:
                wp = self.fpl.waypoints[fpl_index]
            else:
                entry = self._resolve(target, origin)
                if entry is None:
                    return False
                wp = PlanWaypoint.from_entry(entry)
        self.dto = DirectTo(wp, origin, initial_bearing(origin, wp.pos), fpl_index)
        self.suspended = False
        self.obs_active = False
        self._hold_state = None           # Direct-To abandons any hold in progress
        return True

    def cancel_direct_to(self) -> None:
        """Drop the Direct-To. If a flight plan is still loaded, resume it along
        the leg closest to the present position (GNS 530 Pilot's Guide sec.4)."""
        self.dto = None
        self._hold_state = None
        if self._pos is not None and len(self.fpl) >= 2:
            self.fpl.active = self._closest_leg(self._pos)

    def _closest_leg(self, pos: Point) -> int:
        """Index of the TO waypoint of the flight-plan leg nearest ``pos``."""
        best_i, best_d = self.fpl.active, float("inf")
        for i in range(1, len(self.fpl)):
            a = self.fpl.waypoints[i - 1].pos
            wb = self.fpl.waypoints[i]
            b = wb.pos
            if wb.arc_centre is not None:
                r = great_circle_nm(wb.arc_centre, b)
                sweep = arc_sweep_deg(wb.arc_centre, a, b, wb.arc_turn)
                prog = arc_progress_deg(wb.arc_centre, a, wb.arc_turn, pos)
                if prog < 0.0:
                    d = great_circle_nm(pos, a)
                elif prog > sweep:
                    d = great_circle_nm(pos, b)
                else:
                    d = abs(arc_xtk_nm(wb.arc_centre, wb.arc_turn, r, pos))
                if d < best_d:
                    best_i, best_d = i, d
                continue
            seg = great_circle_nm(a, b)
            along = along_track_nm(a, b, pos)
            if along < 0.0:
                d = great_circle_nm(pos, a)
            elif along > seg:
                d = great_circle_nm(pos, b)
            else:
                d = abs(cross_track_nm(a, b, pos))
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    # -- modes ---------------------------------------------------------
    def set_obs(self, course_deg: float, *, on: bool = True) -> None:
        self.obs_course = norm360(course_deg)
        self.obs_active = on
        if on:
            self.suspended = False
            self.dto = None
            self._hold_state = None       # OBS overrides any hold in progress

    def clear_obs(self) -> None:
        self.obs_active = False

    def toggle_obs(self) -> None:
        """OBS key: enable OBS (course seeded from the current DTK / track), or
        cancel it and resume automatic sequencing."""
        if self.obs_active:
            self.clear_obs()
        else:
            seed = self._nav.dtk if self._nav.dtk is not None else self._track
            self.set_obs(seed)

    def nudge_obs(self, delta_deg: float) -> None:
        """Step the selected OBS course (no-op unless OBS is active)."""
        if self.obs_active:
            self.set_obs(self.obs_course + delta_deg)

    def toggle_suspend(self) -> None:
        """SUSP key. While a hold is actively being flown this arms/disarms an
        exit at the next inbound crossing (the GNS doesn't rip you out mid-turn);
        otherwise it's the plain SUSP/resume toggle used at a MAP or manual leg."""
        if self._hold_state is not None:
            self._hold_state["exit_requested"] = not self._hold_state["exit_requested"]
            return
        self.suspended = not self.suspended

    def toggle_cdi_source(self) -> None:
        self.cdi_source = "VLOC" if self.cdi_source == "GPS" else "GPS"

    def ack_messages(self) -> list[str]:
        msgs, self.messages = self.messages, []
        return msgs

    def peek_messages(self) -> list[str]:
        """Read the message queue without clearing it (for the Message page)."""
        return list(self.messages)

    # -- IFR-1 events -------------------------------------------------
    def handle_event(self, ev) -> None:
        """Apply one ``ifr1.Event``. Only FMS modes drive the GPS pages here;
        COM/NAV/XPDR tuning and the AP row are ``main.py``'s job."""
        if getattr(ev, "mode", None) not in (Mode.FMS1, Mode.FMS2):
            return
        pressed = tuple(getattr(ev, "pressed", ()))
        outer = getattr(ev, "outer", 0)
        inner = getattr(ev, "inner", 0)

        # the PROC selector is a modal overlay - it owns every input
        if self._proc_dialog is not None:
            self._proc_event(pressed, outer, inner)
            return

        # the Direct-To dialog is a modal overlay - it owns every input
        if self._dto_dialog is not None:
            for btn in pressed:
                if btn == "ENT":
                    self._confirm()
                elif btn in ("CLR", "DCT"):
                    self._cancel()
            # once "Activate?" is highlighted the identifier is locked in -
            # the knobs no longer edit it (Pilot's Guide sec.4.1)
            if self._dto_dialog is not None and not self._dto_dialog.confirming:
                self._dto_dialog.move_cursor(outer)
                self._dto_dialog.scroll_char(inner)
            return

        # the MNU pop-up (Flight Plan / Flight Plan Catalog) is a modal overlay
        if self._fpl_menu is not None:
            for btn in pressed:
                if btn == "ENT":
                    self._apply_fpl_menu()
                elif btn in ("CLR", "MNU"):
                    self._fpl_menu = None
            if self._fpl_menu is not None:
                self._fpl_menu.move(outer)
            return

        page = self.cursor.page_name
        for btn in pressed:
            if btn == "SWAP":
                self.toggle_cdi_source()
            elif btn == "DCT":
                self._begin_direct_to()
            elif btn == "MNU":
                if page == "Flight Plan":
                    self._fpl_menu = FplMenu(list(_FPL_MENU))
                elif page == "Flight Plan Catalog":
                    self._fpl_menu = FplMenu(list(_CATALOG_MENU))
                else:
                    self._pending_menu = True
            elif btn == "KNOB":
                self.cursor.toggle_cursor()
                self._on_cursor_toggle(page)
            elif btn == "ENT":
                self._page_ent(page)
            elif btn == "CLR":
                self._page_clr(page)

        if not self.cursor.cursor_on:
            self.cursor.on_outer(outer)
            self.cursor.on_inner(inner)
        elif outer or inner:
            self._page_edit(page, outer, inner)

    # -- page-tier cursor editing ----------------------------------
    def _on_cursor_toggle(self, page: str) -> None:
        if self.cursor.cursor_on:
            if page == "Flight Plan":
                self._fpl_edit = {"row": max(1, self.fpl.active)}
            elif page == "Flight Plan Catalog":
                self.cat_sel = 0
            elif page == "VNAV":
                self.vnav_field = 0
            elif page == "Weather":
                self.wx_sel = 0
                self.wx_scroll = 0
            elif page == "Charts":
                self.chart_airport_sel = 0
                self.chart_sel = 0
            elif page.startswith("Nearest"):
                self.nrst_sel = 0
        else:
            self._fpl_edit = None

    def _page_edit(self, page: str, outer: int, inner: int) -> None:
        if page == "Flight Plan":
            ed = self._fpl_edit or {"row": 1}
            self._fpl_edit = ed
            buf = ed.get("buf")
            if buf is not None:
                buf.move_cursor(outer)
                buf.scroll_char(inner)
            elif outer:
                n = len(self.fpl.waypoints)
                ed["row"] = max(0, min(n, ed["row"] + outer))
        elif page in ("Airport", "Intersection", "NDB", "VOR"):
            self.wpt_entry.move_cursor(outer)
            self.wpt_entry.scroll_char(inner)
        elif page.startswith("Nearest"):
            if outer:
                hits = self.nearest_for_page(page)
                self.nrst_sel = max(0, min(max(0, len(hits) - 1), self.nrst_sel + outer))
        elif page == "Flight Plan Catalog":
            if outer:
                self.cat_sel = max(0, min(len(self.fpl_catalog) - 1, self.cat_sel + outer))
        elif page == "VNAV":
            if outer:
                self.vnav_field = max(0, min(2, self.vnav_field + outer))
            if inner:
                self._vnav_edit(inner)
        elif page == "Setup":
            if inner:
                self._cycle_cdi_alarm(inner)
        elif page == "Weather":
            if outer:
                n = len(self.wx_station_idents())
                self.wx_sel = max(0, min(max(0, n - 1), self.wx_sel + outer))
                self.wx_scroll = 0                     # new station - back to the top
            if inner:
                # scrolls the METAR/TAF text itself, which routinely runs
                # longer than the screen (H1/I1: "screen too small to show
                # complete TAF... CRSR should scroll") - render.py clamps
                # the upper bound against the actual line count each frame.
                self.wx_scroll = max(0, self.wx_scroll + inner)
        elif page == "Charts":
            # outer (large knob) picks the airport, resetting the chart index
            # each time; inner (small knob) scrolls the chart list. GpsNav
            # doesn't know how many charts an airport has (that's datasrc.dtpp's
            # cached index, read by render.py / main.py, not this pure core),
            # so chart_sel is left unclamped here and clamped defensively
            # wherever it's actually used - same reasoning as the Weather page
            # not owning METAR/TAF data.
            if outer:
                n = len(self.wx_station_idents())
                self.chart_airport_sel = max(0, min(max(0, n - 1), self.chart_airport_sel + outer))
                self.chart_sel = 0
            if inner:
                self.chart_sel += inner
        else:                                     # pages with no field editor yet
            self.cursor.on_outer(outer)

    def _page_ent(self, page: str) -> None:
        if page == "Flight Plan":
            ed = self._fpl_edit
            if ed is None:
                return
            buf = ed.get("buf")
            if buf is None:
                # Pilot's Guide sec.5.1: "turn the large right knob to select
                # the point to add the new waypoint - if an existing waypoint
                # is highlighted, the new waypoint is placed directly in
                # front of this waypoint." So the field always opens blank -
                # entering an identifier here INSERTS, it never overwrites
                # the highlighted waypoint (delete-then-CLR is how an
                # existing one is actually changed).
                ed["buf"] = DirectToEntry.seeded("")
                return
            ident = buf.ident()
            ed["buf"] = None
            if not ident:
                return
            ref = self.fpl.waypoints[-1].pos if self.fpl.waypoints else self._pos
            entry = self._resolve(ident, ref)
            if entry is None:
                self.messages.append(f"NO WAYPOINT: {ident}")
                return
            row = ed["row"]
            wp = PlanWaypoint.from_entry(entry)
            self.fpl.insert(row, wp)
            ed["row"] = row + 1     # cursor follows onto the (now shifted-down) old row
        elif page in ("Airport", "Intersection", "NDB", "VOR"):
            entry = self.lookup(self.wpt_entry.ident(), page)
            if entry is not None:                  # DCT-from-a-WPT-page shortcut
                self.direct_to(PlanWaypoint.from_entry(entry))
                self.cursor.go_to_default_nav()
        elif page.startswith("Nearest"):
            hits = self.nearest_for_page(page)
            if hits and 0 <= self.nrst_sel < len(hits):
                self.direct_to(PlanWaypoint.from_entry(hits[self.nrst_sel]))
                self.cursor.go_to_default_nav()
        elif page == "Flight Plan Catalog":
            if self.catalog_load(self.cat_sel):
                self.cursor.go_to_flight_plan()
        elif page == "VNAV":
            if self.vnav.target_ident:
                self.vnav = replace(self.vnav, armed=not self.vnav.armed)

    def _page_clr(self, page: str) -> None:
        if page == "Flight Plan" and self._fpl_edit is not None:
            ed = self._fpl_edit
            if ed.get("buf") is not None:
                ed["buf"] = None
                return
            row = ed["row"]
            if 0 <= row < len(self.fpl.waypoints):
                self.fpl.delete(row)
                ed["row"] = max(0, min(row, len(self.fpl.waypoints)))
            return
        if page == "Flight Plan Catalog":
            self.catalog_delete(self.cat_sel)
            return
        if page == "VNAV":
            self.vnav = VnavProfile()
            return
        self._cancel()

    def _vnav_edit(self, delta: int) -> None:
        """VNAV page, cursor on: outer picks the field (`vnav_field`), inner
        edits it - target fix (stepped through the remaining flight plan),
        target altitude (+-100 ft), or VS profile (+-100 fpm)."""
        if self.vnav_field == 0:
            ahead = ([w.ident for w in self.fpl.waypoints[self.fpl.active:]]
                     if self.fpl.has_active_leg else [])
            if not ahead:
                return
            cur = ahead.index(self.vnav.target_ident) if self.vnav.target_ident in ahead else -1
            cur = max(0, min(len(ahead) - 1, cur + delta))
            self.vnav = replace(self.vnav, target_ident=ahead[cur], armed=False)
        elif self.vnav_field == 1:
            alt = max(0.0, self.vnav.target_alt_ft + delta * 100.0)
            self.vnav = replace(self.vnav, target_alt_ft=alt)
        else:
            vs = _clampf(self.vnav.vs_fpm + delta * 100.0, _VNAV_MIN_VS_FPM, _VNAV_MAX_VS_FPM)
            self.vnav = replace(self.vnav, vs_fpm=vs)

    def _cycle_cdi_alarm(self, delta: int) -> None:
        """Setup page, cursor on: inner knob steps through CDI/Alarms'
        Auto / 5.0 / 1.0 / 0.30 nm choices."""
        cur = (_CDI_ALARM_CHOICES.index(self.cdi_alarm_max_nm)
               if self.cdi_alarm_max_nm in _CDI_ALARM_CHOICES else 0)
        cur = max(0, min(len(_CDI_ALARM_CHOICES) - 1, cur + (1 if delta > 0 else -1)))
        self.cdi_alarm_max_nm = _CDI_ALARM_CHOICES[cur]

    # -- Flight Plan Catalog (Pilot's Guide sec.5.2): store / recall / -----
    # invert / delete whole plans. FPL 00 is always the active plan (`self.
    # fpl`); slots 0..CATALOG_SIZE-1 here are shown to the pilot as 01-19.
    def _apply_fpl_menu(self) -> None:
        menu = self._fpl_menu
        if menu is None:
            return
        choice = menu.current
        self._fpl_menu = None
        page = self.cursor.page_name
        if page == "Flight Plan":
            if choice == "INVERT FLT PLAN":
                self.invert_flight_plan()
            elif choice == "COPY FLT PLAN":
                if not self.catalog_store_first_empty():
                    self.messages.append("FLIGHT PLAN CATALOG FULL")
            elif choice == "DELETE FLT PLAN":
                self.fpl.clear()
                self.dto = None
                self._hold_state = None
                self.suspended = False
        elif page == "Flight Plan Catalog":
            if choice == "COPY FLT PLAN":
                stored = self.fpl_catalog[self.cat_sel] if 0 <= self.cat_sel < len(
                    self.fpl_catalog) else None
                if stored is not None:
                    for i, slot in enumerate(self.fpl_catalog):
                        if slot is None:
                            self.fpl_catalog[i] = FlightPlan(
                                list(stored.waypoints), 1, comment=stored.comment)
                            break
                    else:
                        self.messages.append("FLIGHT PLAN CATALOG FULL")
            elif choice == "SORT CATALOG":
                self.catalog_sort()
            elif choice == "DELETE FLT PLAN":
                self.catalog_delete(self.cat_sel)

    def invert_flight_plan(self) -> None:
        """MNU > Invert Flight Plan: reverse waypoint order and fly it
        back-to-front. Any Direct-To or hold in progress is abandoned - the
        plan geometry underneath just changed."""
        if len(self.fpl.waypoints) < 2:
            return
        self.fpl.waypoints.reverse()
        self.fpl.active = 1
        self.dto = None
        self._hold_state = None
        self.suspended = False

    def catalog_store(self, slot: int, comment: str | None = None) -> bool:
        """Copy the active flight plan into catalog slot ``slot`` (0-based;
        shown to the pilot as FPL 01-19). ``comment`` overrides the
        auto-generated "ORIGIN/DEST" label (Pilot's Guide sec.5.2 comment
        line) - pass None to keep the plan's own comment, or auto-generate one."""
        if not 0 <= slot < len(self.fpl_catalog) or not self.fpl.waypoints:
            return False
        wps = self.fpl.waypoints
        label = comment if comment is not None else (
            self.fpl.comment or f"{wps[0].ident}/{wps[-1].ident}")
        self.fpl_catalog[slot] = FlightPlan(list(wps), 1, comment=label)
        return True

    def catalog_store_first_empty(self) -> bool:
        """Store the active plan into the first free catalog slot."""
        for i, slot in enumerate(self.fpl_catalog):
            if slot is None:
                return self.catalog_store(i)
        return False

    def catalog_load(self, slot: int) -> bool:
        """Recall stored plan ``slot`` as the new active flight plan (FPL 00).
        No-op (returns False) on an empty slot, same as the real unit."""
        if not 0 <= slot < len(self.fpl_catalog):
            return False
        stored = self.fpl_catalog[slot]
        if stored is None:
            return False
        self.fpl = FlightPlan(list(stored.waypoints), 1, comment=stored.comment)
        self.dto = None
        self._hold_state = None
        self.suspended = False
        return True

    def catalog_delete(self, slot: int) -> bool:
        if not 0 <= slot < len(self.fpl_catalog):
            return False
        self.fpl_catalog[slot] = None
        return True

    def catalog_set_comment(self, slot: int, text: str) -> bool:
        """Rename a stored plan's comment line (a free-text on-screen editor
        for this isn't wired to the bezel - see WORKING.md - but the field and
        the API are real)."""
        if not 0 <= slot < len(self.fpl_catalog) or self.fpl_catalog[slot] is None:
            return False
        self.fpl_catalog[slot].comment = text.strip().upper()[:16]
        return True

    def catalog_sort(self) -> None:
        """MNU > Sort Catalog: alphabetize stored plans by comment, packing
        empty slots to the end."""
        filled = sorted((p for p in self.fpl_catalog if p is not None),
                        key=lambda p: (p.comment or "").upper())
        self.fpl_catalog = filled + [None] * (len(self.fpl_catalog) - len(filled))

    def crossfill(self, other: "GpsNav") -> bool:
        """MNU > Crossfill: copy the active flight plan to another GNS unit.
        This trainer models one physical FMS per running session, so ``other``
        is whatever GpsNav the caller wires up (e.g. a scripted FMS2) - there
        is no live second unit in the default `main.World` yet (a dedicated
        FMS2 is tracked separately as an M8 follow-up in WORKING.md)."""
        if not self.fpl.waypoints:
            return False
        other.fpl = FlightPlan(list(self.fpl.waypoints), 1, comment=self.fpl.comment)
        other.dto = None
        other._hold_state = None
        other.suspended = False
        return True

    # -- VNAV: a straight-line descent profile to a flight-plan fix -------
    # Deliberately decoupled from `update()` - altitude isn't otherwise part
    # of this core's state, and this keeps `NavState`/`update()`'s signature
    # untouched for every existing caller. `main`/`render` call this each
    # tick with the ownship altitude.
    def _distance_to_index(self, i: int) -> float | None:
        """Route distance from the present position to `fpl.waypoints[i]`,
        or None if that fix isn't ahead of the active leg (already passed, or
        there's no active leg / position yet)."""
        wps = self.fpl.waypoints
        if not (0 <= i < len(wps)) or self._pos is None or not self.fpl.has_active_leg:
            return None
        if i < self.fpl.active:
            return None
        act = wps[self.fpl.active]
        if act.arc_centre is not None:
            r = great_circle_nm(act.arc_centre, act.pos)
            flown = math.radians(arc_progress_deg(
                act.arc_centre, wps[self.fpl.active - 1].pos, act.arc_turn, self._pos)) * r
            dist = max(0.0, self._leg_nm(wps[self.fpl.active - 1], act) - flown)
        else:
            dist = great_circle_nm(self._pos, act.pos)
        for k in range(self.fpl.active, i):
            dist += self._leg_nm(wps[k], wps[k + 1])
        return dist

    @staticmethod
    def _leg_nm(a: PlanWaypoint, b: PlanWaypoint) -> float:
        """Length of the leg a -> b: the arc for a DME arc, else the great circle."""
        if b.arc_centre is not None:
            return arc_length_nm(b.arc_centre, a.pos, b.pos, b.arc_turn)
        return great_circle_nm(a.pos, b.pos)

    def vnav_set(self, target_ident: str, target_alt_ft: float,
                 vs_fpm: float = _VNAV_DEFAULT_VS_FPM) -> bool:
        """Program + arm a VNAV profile directly (the page does the same
        thing one field at a time). False (no-op) if the fix isn't in the
        active flight plan."""
        ident = target_ident.strip().upper()
        if self.fpl.index_of(ident) < 0:
            return False
        vs = _clampf(vs_fpm, _VNAV_MIN_VS_FPM, _VNAV_MAX_VS_FPM)
        self.vnav = VnavProfile(ident, max(0.0, target_alt_ft), vs, armed=True)
        return True

    def vnav_status(self, alt_ft: float | None = None, gs_kt: float | None = None) -> VnavStatus:
        prof = self.vnav
        base = VnavStatus(target_ident=prof.target_ident, target_alt_ft=prof.target_alt_ft,
                           vs_fpm=prof.vs_fpm)
        if not prof.armed or not prof.target_ident:
            return base
        i = self.fpl.index_of(prof.target_ident)
        dist = self._distance_to_index(i) if i >= 0 else None
        if dist is None:
            return base
        base = replace(base, distance_to_target_nm=dist)
        # Pilot's Guide: VNAV needs > 35 kt groundspeed - converting a ft/min
        # profile into a ground-track altitude profile is undefined below
        # that (same reason the real unit requires it).
        if gs_kt is None or gs_kt <= _VNAV_MIN_GS_KT or alt_ft is None or prof.vs_fpm == 0:
            return base
        ft_per_nm = prof.vs_fpm * 60.0 / gs_kt        # signed: - = descending, + = climbing
        required_alt = prof.target_alt_ft - ft_per_nm * dist
        to_lose = alt_ft - prof.target_alt_ft
        tod_nm = max(0.0, to_lose / -ft_per_nm) if ft_per_nm else None
        to_tod = dist - tod_nm if tod_nm is not None else None
        dev = alt_ft - required_alt
        time_min = dist / gs_kt * 60.0
        # VSR (Vertical Speed Required): the live rate needed RIGHT NOW to
        # reach target_alt_ft at the target, at the current groundspeed -
        # independent of the pilot's chosen vs_fpm profile, same as the real
        # unit's VSR readout (it's a "how am I doing" gauge, not an echo).
        req_vs = -to_lose / time_min if time_min > 0 else None
        time_to_tod = max(0.0, to_tod) / gs_kt * 60.0 if to_tod is not None else None
        return VnavStatus(
            valid=True, target_ident=prof.target_ident, target_alt_ft=prof.target_alt_ft,
            vs_fpm=prof.vs_fpm, distance_to_target_nm=dist, required_alt_ft=required_alt,
            tod_distance_nm=tod_nm, distance_to_tod_nm=to_tod, deviation_ft=dev,
            alert=to_tod is not None and abs(to_tod) <= 1.0,
            required_vs_fpm=req_vs, time_to_tod_min=time_to_tod,
        )

    def nearest_for_page(self, page: str) -> list:
        """The nearest-N entries a NRST page shows (closest first)."""
        if self._pos is None:
            return []
        if page == "Nearest APT":
            return list(self.db.nearest_airports(self._pos, 9, max_nm=200.0))
        if page == "Nearest VOR":
            return list(self.db.nearest_navaids(self._pos, 9, max_nm=200.0, ndb=False))
        if page == "Nearest NDB":
            return list(self.db.nearest_navaids(self._pos, 9, max_nm=200.0, vhf=False))
        if page == "Nearest INT":
            return list(self.db.nearest_waypoints(self._pos, 9, max_nm=100.0))
        return []

    def wx_station_idents(self, *, max_n: int = 6) -> list[str]:
        """Airport idents the Weather page shows: every airport in the active
        flight plan (departure, enroute stops, destination), route order,
        deduplicated - falling back to the nearest airport if no plan is
        loaded. Capped at ``max_n`` so a long plan doesn't run off the page."""
        idents: list[str] = []
        for wp in self.fpl.waypoints:
            ident = wp.ident.upper()
            if ident in idents:
                continue
            if self.db.airport(ident) is not None:
                idents.append(ident)
        if not idents and self._pos is not None:
            near = self.db.nearest_airports(self._pos, 1, max_nm=200.0)
            if near:
                idents.append(near[0].ident)
        return idents[:max_n]

    def _begin_direct_to(self) -> None:
        """Open the Select Direct-To Waypoint page. The GNS pre-fills the active
        TO waypoint (or the current Direct-To target); the knobs then edit the
        identifier and ENT resolves + activates it."""
        seed = ""
        if self.dto is not None:
            seed = self.dto.target.ident
        elif (self.fpl.has_active_leg and not self.suspended
              and self.fpl.to_wp is not None):
            seed = self.fpl.to_wp.ident
        self._dto_dialog = DirectToEntry.seeded(seed)

    def _confirm(self) -> None:
        """ENT on the Select Direct-To Waypoint page. Per the Pilot's Guide
        sec.4.1 this is always two presses - the first confirms the entered
        identifier and highlights "Activate?", the second actually activates
        - with no shortcut even when re-centring on the already-active
        waypoint ("press the Direct-to Key, followed by the ENT Key twice")."""
        dlg = self._dto_dialog
        if dlg is None:
            return
        ident = dlg.ident()
        if not ident:
            return                                     # empty buffer: keep editing
        if not dlg.confirming:
            if self.lookup(ident) is None:
                self.messages.append(f"NO WAYPOINT: {ident}")
                return
            dlg.confirming = True                       # -> "Activate?" highlighted
            return
        if self.direct_to(ident):
            self._dto_dialog = None
        else:
            self.messages.append(f"NO WAYPOINT: {ident}")
            dlg.confirming = False                      # back to the identifier field

    def lookup(self, ident: str, page: str | None = None):
        """Resolve an identifier against the nav database near present
        position (for the Direct-To page's live preview, or a WPT sub-page's
        own category - e.g. the VOR page must only ever resolve a VHF
        navaid, not an airport or intersection that happens to share the
        identifier). Returns the entry or None."""
        if not ident or not ident.strip():
            return None
        return self._resolve(ident, self._pos, kind=_WPT_PAGE_KIND.get(page))

    def _cancel(self) -> None:
        if self._dto_dialog is not None and self._dto_dialog.confirming:
            self._dto_dialog.confirming = False    # back out of "Activate?" to editing
        elif self._dto_dialog is not None:
            self._dto_dialog = None
        elif self.dto is not None:
            # CLR with no dialog open and a Direct-To active cancels it and
            # resumes the flight plan on the nearest leg (Pilot's Guide sec.4).
            self.cancel_direct_to()
        elif self.cursor.cursor_on:
            self.cursor.toggle_cursor()
        else:                                   # nothing to cancel -> Default NAV
            self.cursor.go_to_default_nav()

    # -- PROC key: approach / arrival / departure selection --------
    def _proc_target_airport(self) -> str:
        """Which airport the PROC menu works on: the plan's destination airport
        (last fix, or any airport fix in the plan), then the airport of a
        procedure already loaded, then the nearest airport to present position."""
        wps = self.fpl.waypoints

        def _is_apt(w) -> bool:
            return w.kind == "apt" or self.db.airport(w.ident) is not None

        if wps and _is_apt(wps[-1]):
            return wps[-1].ident
        if self._proc_airport and self.db.procs(self._proc_airport):
            return self._proc_airport
        for w in wps:
            if _is_apt(w):
                return w.ident
        if self._pos is not None:
            near = self.db.nearest_airports(self._pos, 1, max_nm=200.0)
            if near:
                return near[0].ident
        return ""

    def begin_proc_select(self) -> bool:
        """Open the PROC selector for the destination airport. Returns False
        (and posts a message) when there is nothing to select."""
        apt = self._proc_target_airport()
        if not apt:
            self.messages.append("NO DESTINATION FOR PROC")
            return False
        menu: list[str] = []
        if self.db.approaches(apt):
            menu.append(_PROC_APPROACH)
        if self.db.stars(apt):
            menu.append(_PROC_ARRIVAL)
        if self.db.sids(apt):
            menu.append(_PROC_DEPARTURE)
        if self._approach_active:
            menu += [_PROC_ACT_VTF, _PROC_ACT_APPR]
        if not menu:
            self.messages.append(f"NO PROCEDURES: {apt}")
            return False
        self._proc_dialog = ProcSelect(airport=apt, step="MENU", options=menu)
        return True

    def _proc_event(self, pressed, outer: int, inner: int) -> None:
        dlg = self._proc_dialog
        if dlg is None:
            return
        for btn in pressed:
            if btn == "ENT":
                self._proc_advance()
                if self._proc_dialog is None:
                    return
            elif btn in ("CLR", "DCT", "MNU"):
                self._proc_back()
                if self._proc_dialog is None:
                    return
        if self._proc_dialog is not None:
            self._proc_dialog.move((outer or 0) + (inner or 0))

    def _proc_back(self) -> None:
        dlg = self._proc_dialog
        if dlg is None or dlg.step == "MENU":
            self._proc_dialog = None
            return
        if dlg.step == "LOADACT":
            if dlg.has_trans_step:
                dlg.step = "TRANS"
                proc = self.db.procedure(dlg.airport, dlg.proc_ident)
                names = list(proc.transition_names()) if proc is not None else []
                if dlg.kind == "approach":
                    names = [_PROC_VECTORS, *names]
                dlg.options = names
                dlg.sel = 0
            else:                                   # TRANS itself was skipped
                dlg.step = "PROC"
                dlg.options = sorted(p.ident for p in self.db.procs(dlg.airport, dlg.kind))
                dlg.sel = 0
                dlg.proc_ident = ""
        elif dlg.step == "TRANS":
            dlg.step = "PROC"
            dlg.options = sorted(p.ident for p in self.db.procs(dlg.airport, dlg.kind))
            dlg.sel = 0
            dlg.proc_ident = ""
        else:                                       # PROC -> MENU
            self.begin_proc_select()

    def _proc_advance(self) -> None:
        dlg = self._proc_dialog
        if dlg is None or not dlg.current:
            return
        if dlg.step == "MENU":
            choice = dlg.current
            if choice in _PROC_MENU_KIND:
                dlg.kind = _PROC_MENU_KIND[choice]
                dlg.step = "PROC"
                dlg.options = sorted(p.ident for p in self.db.procs(dlg.airport, dlg.kind))
                dlg.sel = 0
            elif choice == _PROC_ACT_APPR:
                self._activate_approach(vtf=False)
                self._proc_dialog = None
            elif choice == _PROC_ACT_VTF:
                self._activate_approach(vtf=True)
                self._proc_dialog = None
            return
        if dlg.step == "PROC":
            dlg.proc_ident = dlg.current
            proc = self.db.procedure(dlg.airport, dlg.proc_ident)
            names = list(proc.transition_names()) if proc is not None else []
            if dlg.kind == "approach":
                # VECTORS = load the final segment only (radar vectors to it)
                names = [_PROC_VECTORS, *names]
            if not names:                           # nothing to choose -> Load?/Activate?
                dlg.transition = None
                dlg.has_trans_step = False
                self._proc_begin_loadact()
                return
            dlg.step = "TRANS"
            dlg.has_trans_step = True
            dlg.options = names
            dlg.sel = 0
            return
        if dlg.step == "TRANS":
            # VECTORS is not a real transition key, so Procedure.assemble()
            # falls through to the common segment alone.
            dlg.transition = dlg.current
            self._proc_begin_loadact()
            return
        # step == "LOADACT" - Pilot's Guide p.61 step 5: "Load?" (add to the
        # flight plan without activating) or, approaches only, "Activate?"
        # (add it AND immediately fly it - the same as loading, then an
        # Activate Approach/Vectors-to-Final from the PROC menu, in one step).
        if dlg.current == "Activate?":
            self._proc_load(dlg.transition, activate=True)
        else:
            self._proc_load(dlg.transition, activate=False)

    def _proc_begin_loadact(self) -> None:
        """Enter the final PROC-wizard step: Pilot's Guide p.61 step 5,
        "Rotate the large right knob to highlight 'Load?' or 'Activate?'
        (approaches only) and press ENT." SIDs/STARs only ever get "Load?"
        here - they're activated later the same way any other flight-plan
        leg is, not through this step (p.62: "To later activate a departure
        or arrival, follow the steps on page 60")."""
        dlg = self._proc_dialog
        if dlg is None:
            return
        dlg.step = "LOADACT"
        dlg.options = ["Load?", "Activate?"] if dlg.kind == "approach" else ["Load?"]
        dlg.sel = 0                                 # "Load?" is the default highlight

    def _proc_load(self, transition: str | None, *, activate: bool = False) -> None:
        dlg = self._proc_dialog
        if dlg is None:
            return
        n = self.load_procedure(dlg.airport, dlg.proc_ident, transition, append=True)
        kind, proc_ident = dlg.kind, dlg.proc_ident
        self._proc_dialog = None
        if not n:
            self.messages.append(f"PROC LOAD FAILED: {proc_ident}")
            return
        tag = f" {transition}" if transition else ""
        if activate:
            # p.62's "Activate Approach?" (or, when the loaded transition was
            # VECTORS and so has no IAF, this naturally reduces to "Activate
            # Vectors-to-Final?" instead - `_activate_approach` already picks
            # whichever fix is actually present, and posts its own message).
            self._activate_approach(vtf=False)
        else:
            self.messages.append(f"{kind.upper()} LOADED: {proc_ident}{tag}")
            self.cursor.go_to_flight_plan()

    def _activate_approach(self, *, vtf: bool) -> None:
        """PROC > Activate Approach / Vectors-To-Final: drop SUSP and steer to
        the first approach fix (the FAF for VTF, else the first IAF)."""
        wps = self.fpl.waypoints
        faf = next((i for i, w in enumerate(wps) if w.is_faf), None)
        iaf = next((i for i, w in enumerate(wps) if w.is_iaf), None)
        target = faf if (vtf and faf is not None) else (iaf if iaf is not None else faf)
        if target is None or target < 1:
            self.messages.append("NO APPROACH LOADED")
            return
        self.dto = None
        self.suspended = False
        self._susp_at = None
        self.fpl.activate_leg(target)
        self.messages.append(
            "VECTORS-TO-FINAL" if vtf else f"APPROACH ACTIVE: {wps[target].ident}")

    # -- the per-loop update ----------------------------------------
    def update(self, pos: Point, track_deg: float, gs_kt: float,
               dt: float | None = None) -> NavState:
        self._pos, self._track, self._gs = pos, norm360(track_deg), max(0.0, gs_kt)
        self._check_expiry()

        if self._hold_state is not None:
            return self._step_hold(pos, dt)

        leg = self._active_leg()          # (from_pt, to_pt, from_id, to_id, is_dto) or None
        if leg is None:
            scale = self._step_cdi_scale(
                self._target_cdi_scale(self._dist_to_destination(pos),
                                        self._dist_to_faf(pos),
                                        self._dist_to_departure(pos)), dt)
            self._nav = NavState(cdi_source=self.cdi_source,
                                 cdi_scale_nm=scale,
                                 annunciators=self._annunciators(False))
            return self._nav

        a, b, from_id, to_id, is_dto = leg

        dist = great_circle_nm(pos, b)
        scale = self._step_cdi_scale(
            self._target_cdi_scale(self._dist_to_destination(pos),
                                    self._dist_to_faf(pos) if self._approach_active else dist,
                                    self._dist_to_departure(pos)), dt)

        if self.obs_active:
            self._nav = self._obs_state(b, from_id, to_id, scale)
            return self._nav

        arc = None if is_dto else self._active_arc()     # (centre, turn) | None
        brg = initial_bearing(pos, b)
        if arc is not None:
            # DME arc (AF/RF): the path is the circle about the navaid - DTK is its
            # tangent at ownship, XTK the distance off the circle, DTG the arc left.
            centre, aturn = arc
            radius = great_circle_nm(centre, b)
            dtk = arc_track(centre, aturn, pos)
            xtk = arc_xtk_nm(centre, aturn, radius, pos)
            leg_len = arc_length_nm(centre, a, b, aturn)
            along = math.radians(arc_progress_deg(centre, a, aturn, pos)) * radius
            dtg = max(0.0, leg_len - along)
            end_dtk = arc_track(centre, aturn, b)
        else:
            dtk = self.dto.course if is_dto else initial_bearing(a, b)
            xtk = cross_track_nm(a, b, pos)
            leg_len = great_circle_nm(a, b)
            along = along_track_nm(a, b, pos)
            dtg = max(0.0, leg_len - along)
            end_dtk = dtk
        tke = angle_diff(self._track, dtk)

        nxt = self._next_after_active(is_dto)
        nxt_course = self._course_out_of(b, is_dto)
        course_change = abs(angle_diff(nxt_course, end_dtk)) if nxt_course is not None else 0.0
        # turn_anticipation_nm blows up (tan) as course_change -> 180 deg (a near
        # reversal, e.g. into a hold): cap it at a sane lead distance rather than
        # cutting the corner tens of miles early. Also cap it to at most half the
        # CURRENT leg's own length - an unusually short leg (e.g. two waypoints
        # under a mile apart) must not anticipate a turn before ownship has had
        # any chance to actually get established on the leg in the first place.
        anticip = min(turn_anticipation_nm(self._gs, course_change), 5.0, leg_len * 0.5) \
            if nxt else 0.0
        alert_dist = anticip + self._gs / 3600.0 * _WPT_ALERT_SEC

        # a MAP / hold / manual-termination fix: stop here (GNS 530 Pilot's Guide
        # sec.6.2 - 'SUSP' at the MAP / after a hold circuit) instead of running on.
        # A hold is actually flown (see _start_hold); MAP/manual legs just SUSP.
        # Uses the same (capped) anticip as _should_sequence below so this check
        # always wins the race and short-circuits it via self.suspended.
        # The (active, ident) latch means un-suspending doesn't immediately re-trip.
        to_wp = self.fpl.to_wp if (not is_dto and self.fpl.has_active_leg) else None
        if to_wp is not None and to_wp.stop_here:
            # a fly-TO fix by definition - no turn-anticipation corner cut (a
            # hold's "next leg" is a ~180 deg reversal that would otherwise cut
            # it many miles early). Overrun still sequences once un-suspended
            # (pilot commits to the miss) or after a hold's circuit completes.
            if dtg <= _FIX_CAPTURE_NM:
                key = (self.fpl.active, to_wp.ident)
                if not self.suspended and self._susp_at != key:
                    self._susp_at = key
                    if to_wp.hold:
                        self._start_hold(to_wp, dtk, self._gs)
                    else:
                        self.suspended = True
            seq_now = not self.suspended and self._should_sequence(along, leg_len, dtg, 0.0, None)
        else:
            seq_now = not self.suspended and self._should_sequence(along, leg_len, dtg, anticip, nxt)
        if seq_now:
            self._sequence(is_dto)
            return self.update(pos, track_deg, gs_kt, dt)  # recompute on the new leg

        turning = bool(nxt) and course_change > 5.0
        # "NEXT DTK ..." over the wide alert band; "TURN TO ..." once inside ~1.5x
        # the turn-anticipation distance, just before the leg sequences.
        turn_now = turning and anticip > 0.0 and dtg <= anticip * 1.5
        self._nav = NavState(
            valid=True,
            mode="DTO" if is_dto else ("SUSP" if self.suspended else "LEG"),
            cdi_source=self.cdi_source,
            from_ident=from_id,
            to_ident=to_id,
            dtk=dtk,
            brg=brg,
            xtk_nm=xtk,
            tke=tke,
            dist_nm=dist,
            dtg_nm=dtg,
            to_from="TO",
            wpt_alert=bool(nxt) and dtg <= alert_dist,
            turn_anticipation=turning and dtg <= alert_dist,
            turn_now=turn_now,
            next_dtk=nxt_course,
            cdi_scale_nm=scale,
            annunciators=self._annunciators(True),
        )
        return self._nav

    # -- holding pattern: fly it, don't just sit there (GNS 530W behaviour) --
    def _start_hold(self, wp: PlanWaypoint, arrival_true: float, gs_kt: float) -> None:
        """Enter the hold at ``wp``: pick the AIM 5-3-9 entry (direct / teardrop /
        parallel) from the arrival course and fly it. A single-circuit
        hold-in-lieu-of-PT (ARINC ``HF``) auto-continues once re-established
        inbound; ``HM``/``HA`` keep circling until the pilot releases SUSP."""
        inbound = wp.hold_inbound_true if wp.hold_inbound_true is not None else arrival_true
        turn = (wp.hold_turn or "R").strip().upper() or "R"
        sign = 1.0 if turn != "L" else -1.0
        outbound = reciprocal(inbound)
        entry = hold_entry(inbound, arrival_true, turn)   # navmath: AIM 5-3-9 sectors
        if entry == "teardrop":
            leg_hdg = norm360(outbound - 30.0 * sign)      # offset inbound-ward
        else:                                              # direct / parallel entry
            leg_hdg = outbound
        if wp.hold_leg_nm:
            leg_nm = wp.hold_leg_nm
        else:
            leg_min = wp.hold_leg_min if wp.hold_leg_min is not None else 1.0
            leg_nm = max(0.5, (gs_kt if gs_kt > 20.0 else 90.0) / 60.0 * leg_min)
        self._hold_state = {
            "fix": wp.pos, "ident": wp.ident, "inbound": inbound, "outbound": outbound,
            "sign": sign, "leg_nm": leg_nm, "phase": "OUTBOUND", "leg_hdg": leg_hdg,
            "single_circuit": wp.hold_single_circuit, "lap": 1, "exit_requested": False,
            "min_dist_to_fix": None,   # closest approach seen this INBOUND leg - see _step_hold
            "entry": entry,
            "turn_dir": sign, "turn_done": True,   # set per lap when the INBOUND leg begins
        }
        self.suspended = True

    def _complete_hold_lap(self) -> None:
        """Recrossed the fix established inbound. A single-circuit hold (or a
        repeating one the pilot has asked to leave) resumes normal sequencing;
        otherwise fly one more standard circuit (outbound - turn - inbound)."""
        hs = self._hold_state
        if hs is None:
            return
        if hs["single_circuit"] or hs["exit_requested"]:
            self._hold_state = None
            self.suspended = False
            self._sequence(False)
        else:
            hs["phase"] = "OUTBOUND"
            hs["leg_hdg"] = hs["outbound"]       # subsequent laps fly the standard leg
            hs["lap"] += 1
            hs["min_dist_to_fix"] = None

    def _step_hold(self, pos: Point, dt: float | None) -> NavState:
        """NavState while actively flying a hold - a synthetic outbound or
        inbound leg, so `follow_leg` / a coupled autopilot fly it exactly like
        any other leg (dtk/xtk-driven), no special-casing needed downstream."""
        hs = self._hold_state
        fix = hs["fix"]
        if hs["phase"] == "OUTBOUND":
            a, b, dtk = fix, destination(fix, hs["leg_hdg"], hs["leg_nm"]), hs["leg_hdg"]
        else:                                       # INBOUND: intercept the course to the fix
            a, b, dtk = destination(fix, hs["outbound"], 60.0), fix, hs["inbound"]
        xtk = cross_track_nm(a, b, pos)
        leg_len = great_circle_nm(a, b)
        along = along_track_nm(a, b, pos)
        if hs["phase"] == "INBOUND" and not hs["turn_done"]:
            if abs(angle_diff(self._track, hs["inbound"])) > _HOLD_TURN_DONE_DEG:
                # still swinging round to the inbound course: steer a course
                # 80 deg ahead of the track IN the hold's turn direction, so
                # the (shortest-turn) follower keeps turning that way
                dtk = norm360(self._track + hs["turn_dir"] * 80.0)
                xtk = 0.0
            else:
                hs["turn_done"] = True
        tke = angle_diff(self._track, dtk)

        if hs["phase"] == "OUTBOUND":
            dtg = max(0.0, leg_len - along)
            if along >= leg_len - 0.05:
                hs["phase"] = "INBOUND"
                # The turn back onto the inbound course goes in the hold's
                # own direction (a teardrop's ~210 deg return turn, and every
                # racetrack lap) - except a parallel entry's first turn, which
                # AIM 5-3-8 flies the OPPOSITE way (toward the non-holding
                # side). The sim only ever takes the shortest turn, which is
                # the wrong way round for exactly these cases.
                hs["turn_dir"] = -hs["sign"] if (hs["entry"] == "parallel" and hs["lap"] == 1)                     else hs["sign"]
                hs["turn_done"] = False
        else:
            # Capture (and the DTG/DIS readout) on the INBOUND leg uses
            # straight-line distance to the fix itself, not along-track
            # progress on this 60 nm reference line: with a real cross-track
            # offset (e.g. still mid-turn out of the entry), along-track
            # alone reads near-zero while the aircraft is still miles off
            # the inbound centerline, so the hold "completed" and sequenced
            # onto the next leg - pointed straight at it, cutting a corner -
            # well before the aircraft ever got near the fix.
            #
            # A wide entry (a teardrop's ~210 deg return turn especially)
            # can leave the intercept still converging as the aircraft
            # passes abeam the fix, so an exact _FIX_CAPTURE_NM hit isn't
            # guaranteed - waiting for one can fly the aircraft past the fix
            # and away forever. Track the closest approach instead: capture
            # on a clean close pass (real hit), or once distance has opened
            # back up past that closest point by _HOLD_CPA_MARGIN_NM (the
            # aircraft is now moving away - this is as close as it's getting).
            dtg = great_circle_nm(pos, fix)
            if hs["min_dist_to_fix"] is None or dtg < hs["min_dist_to_fix"]:
                hs["min_dist_to_fix"] = dtg
            past_cpa = dtg > hs["min_dist_to_fix"] + _HOLD_CPA_MARGIN_NM
            if dtg <= _FIX_CAPTURE_NM or past_cpa:
                self._complete_hold_lap()
                return self.update(pos, self._track, self._gs, dt)   # new lap, or resumed leg

        scale = self._step_cdi_scale(
            self._target_cdi_scale(self._dist_to_destination(pos),
                                    self._dist_to_faf(pos) if self._approach_active
                                    else great_circle_nm(pos, fix),
                                    self._dist_to_departure(pos)), dt)
        to_wp = self.fpl.to_wp
        self._nav = NavState(
            valid=True, mode="HOLD", cdi_source=self.cdi_source,
            from_ident="", to_ident=to_wp.ident if to_wp is not None else hs["ident"],
            dtk=dtk, brg=initial_bearing(pos, b), xtk_nm=xtk, tke=tke,
            dist_nm=great_circle_nm(pos, fix), dtg_nm=dtg, to_from="TO",
            cdi_scale_nm=scale, annunciators=self._annunciators(True),
        )
        return self._nav

    # -- CDI full-scale (phase of flight) -------------------------
    def _dist_to_destination(self, pos: Point) -> float | None:
        """Great-circle nm to the last plan waypoint (or an off-plan DTO fix)."""
        if self.dto is not None and self.dto.fpl_index < 0:
            return great_circle_nm(pos, self.dto.target.pos)
        if self.fpl.waypoints:
            return great_circle_nm(pos, self.fpl.waypoints[-1].pos)
        return None

    def _dist_to_departure(self, pos: Point) -> float | None:
        """Great-circle nm to the first plan waypoint (the departure airport).

        A stand-alone Direct-To (no flight plan) has no departure point.
        """
        if self.dto is not None and self.dto.fpl_index < 0:
            return None
        if self.fpl.waypoints:
            return great_circle_nm(pos, self.fpl.waypoints[0].pos)
        return None

    def _dist_to_faf(self, pos: Point) -> float | None:
        """Great-circle nm to the loaded approach's FAF while actually flying
        the leg that ends there, or ``0.0`` once it has already been
        sequenced past (so the 0.30 nm approach scale, once armed, stays
        armed for the rest of the approach - through the FAF->MAP leg -
        instead of widening again just because that leg's own endpoint, the
        MAP, is farther away than the arm distance).

        Deliberately ``None`` (no approach-scale evaluation at all) before
        the FAF's own leg is active: an earlier version used the FAF's
        straight-line distance regardless of which leg was active, and an
        earlier leg's flight path can pass within the 2 nm arm distance of
        the FAF's coordinates by pure incidental geometry - e.g. on KLNS
        I08, LRP->DALAC happens to track within ~0.3 nm of POLCU (the FAF)
        well before DALAC's hold has even been reached - which armed and
        then un-armed the 0.30 nm scale on a leg that has nothing to do with
        the final approach segment. ``None`` also when no FAF is in the plan."""
        faf_i = next((i for i, w in enumerate(self.fpl.waypoints) if w.is_faf), None)
        if faf_i is None or self.fpl.active < faf_i:
            return None
        if self.fpl.active > faf_i:
            return 0.0
        return great_circle_nm(pos, self.fpl.waypoints[faf_i].pos)

    def _target_cdi_scale(self, dist_to_dest: float | None,
                          dist_to_fix: float | None,
                          dist_to_dep: float | None = None) -> float:
        if (self._approach_active and dist_to_fix is not None
                and dist_to_fix <= _CDI_APPROACH_ARM_NM):
            auto = _CDI_APPROACH_NM
        else:
            near_dest = dist_to_dest is not None and dist_to_dest <= _CDI_TERMINAL_ARM_NM
            near_dep = dist_to_dep is not None and dist_to_dep <= _CDI_TERMINAL_ARM_NM
            auto = _CDI_TERMINAL_NM if (near_dest or near_dep) else _CDI_ENROUTE_NM
        # AUX>Setup>CDI/Alarms: a fixed ceiling never widens past, but a
        # tighter phase (e.g. genuinely on an armed approach) still tightens
        # further than it - the selected value caps the scale, it doesn't
        # override a legitimately tighter one.
        if self.cdi_alarm_max_nm is not None:
            return min(auto, self.cdi_alarm_max_nm)
        return auto

    def _step_cdi_scale(self, target: float, dt: float | None) -> float:
        """Slew the live scale toward ``target``; snap when ``dt`` is unknown
        or this is the first sample (a fresh start already inside a scale
        arm - e.g. near the departure airport - should read that scale
        immediately rather than visibly ramping down from the enroute
        default seeded in ``__init__``)."""
        if not self._cdi_scale_init:
            self._cdi_scale = target
            self._cdi_scale_init = True
            return self._cdi_scale
        if not dt or dt <= 0.0:
            self._cdi_scale = target
            return self._cdi_scale
        step = _CDI_RAMP_NM_PER_S * dt
        if abs(target - self._cdi_scale) <= step:
            self._cdi_scale = target
        else:
            self._cdi_scale += step if target > self._cdi_scale else -step
        return self._cdi_scale

    @property
    def nav(self) -> NavState:
        return self._nav

    # -- internals -------------------------------------------------
    def _resolve(self, ident: str, ref: Point | None, *, kind: str | None = None):
        ident = ident.strip().upper()
        if ref is not None:
            return self.db.nearest_fix(ident, ref, kind=kind)
        hits = self.db.find(ident, kind=kind)
        return hits[0] if hits else None

    def _active_leg(self):
        """Return (from_pt, to_pt, from_id, to_id, is_dto) or None."""
        if self.dto is not None:
            return (self.dto.origin, self.dto.target.pos, "", self.dto.target.ident, True)
        if self.fpl.has_active_leg:
            f, t = self.fpl.from_wp, self.fpl.to_wp
            return (f.pos, t.pos, f.ident, t.ident, False)
        return None

    def _active_arc(self):
        """``(centre, turn)`` when the active flight-plan leg is a DME arc."""
        if self.dto is not None or not self.fpl.has_active_leg:
            return None
        w = self.fpl.to_wp
        return (w.arc_centre, w.arc_turn) if w.arc_centre is not None else None

    def _course_out_of(self, b: Point, is_dto: bool) -> float | None:
        """True course of the leg after the active one, at its start ``b`` (the
        tangent when that next leg is itself an arc), or None if there is none."""
        if is_dto:
            nxt = self._next_after_active(True)
            return initial_bearing(b, nxt) if nxt is not None else None
        w = self.fpl.wp_after_active()
        if w is None:
            return None
        if w.arc_centre is not None:
            return arc_track(w.arc_centre, w.arc_turn, b)
        return initial_bearing(b, w.pos)

    def _next_after_active(self, is_dto: bool) -> Point | None:
        if is_dto:
            if self.dto and self.dto.fpl_index >= 0:
                nxt = self.dto.fpl_index + 1
                if nxt < len(self.fpl):
                    return self.fpl.waypoints[nxt].pos
            return None
        w = self.fpl.wp_after_active()
        return w.pos if w else None

    @staticmethod
    def _should_sequence(along: float, leg_len: float, dtg: float,
                         anticip: float, nxt: Point | None) -> bool:
        if along >= leg_len + 0.01:            # overflew the fix
            return True
        if nxt is not None:
            return dtg <= max(anticip, _FIX_CAPTURE_NM)
        return dtg <= _FIX_CAPTURE_NM           # last leg: sequence -> SUSP

    def _sequence(self, is_dto: bool) -> None:
        if is_dto:
            idx = self.dto.fpl_index if self.dto else -1
            self.dto = None
            if idx >= 0 and idx + 1 < len(self.fpl):
                self.fpl.active = idx + 1
            else:
                self.suspended = True
            return
        if self.fpl.active + 1 < len(self.fpl):
            self.fpl.active += 1
        else:
            self.suspended = True

    def _obs_state(self, fix: Point, from_id: str, to_id: str,
                   scale: float | None = None) -> NavState:
        far = destination(fix, norm360(self.obs_course + 180.0), 100.0)
        xtk = cross_track_nm(far, fix, self._pos)
        along = along_track_nm(far, fix, self._pos)
        dist = great_circle_nm(self._pos, fix)
        return NavState(
            valid=True,
            mode="OBS",
            cdi_source=self.cdi_source,
            from_ident=from_id,
            to_ident=to_id,
            dtk=norm360(self.obs_course),
            brg=initial_bearing(self._pos, fix),
            xtk_nm=xtk,
            tke=angle_diff(self._track, self.obs_course),
            dist_nm=dist,
            dtg_nm=max(0.0, 100.0 - along),
            to_from="TO" if along < 100.0 else "FROM",
            cdi_scale_nm=scale,
            annunciators=self._annunciators(True),
        )

    def _annunciators(self, have_leg: bool) -> tuple[str, ...]:
        ann: list[str] = []
        if self.obs_active:
            ann.append("OBS")
        if self.suspended:
            ann.append("SUSP")
        if self.today and self.db.expires and self.today >= self.db.expires:
            ann.append("NAV DATA EXPIRED")
        return tuple(ann)

    def _check_expiry(self) -> None:
        if self._expiry_warned or not (self.today and self.db.expires):
            return
        if self.today >= self.db.expires:
            self.messages.append(
                f"NAV DATA EXPIRED {self.db.expires:%m-%d-%y} - VERIFY ALL DATA"
            )
            self._expiry_warned = True
