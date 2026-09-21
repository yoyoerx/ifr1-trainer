"""radios.py - the COM / NAV radio stack and transponder.

Pure state: no I/O, no db held. `NavReceiver.resolve(db, pos)` is called each
loop (cheap, cached on frequency) to bind the active NAV frequency to a VOR or
localizer in the nav database, and - for a localizer - to pair it with a runway
so we get a front-course and a glideslope reference.

Frequencies are MHz floats. Tuning is split the way a real radio's dual knob
works: the outer knob steps the MHz digits (wrapping within the band), the inner
knob steps the kHz digits (wrapping within 1 MHz).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from navmath import Point, angle_diff, great_circle_nm, initial_bearing, norm360

__all__ = ["ComRadio", "NavReceiver", "Transponder", "RadioStack",
           "MORSE_CODE", "morse_pattern", "morse_is_keyed"]

COM_MIN, COM_MAX, COM_STEP_KHZ = 118.0, 136.990, 25
NAV_MIN, NAV_MAX, NAV_STEP_KHZ = 108.0, 117.950, 50
_XPDR_MODES = ("OFF", "SBY", "ON", "ALT", "GND")

_LOC_RANGE_NM = 25.0           # localizer beam usable range
_LOC_CONE_DEG = 35.0          # ... within this of the runway centreline (either end)

# FAA standard VOR/DME service volumes by facility class (AIM 1-1-8). The class
# is the 3rd char of the ARINC 424 facility field (T terminal / L low / H high);
# a High VOR is a stacked set of cylinders keyed on altitude.
_SSV_TERMINAL_NM = 25.0
_SSV_LOW_NM = 40.0
_SSV_HIGH = (                  # (min_alt_ft, radius_nm), highest floor first
    (45000, 100.0),
    (18000, 130.0),
    (14500, 100.0),
    (0, 40.0),
)
_RESOLVE_MOVE_NM = 0.5         # re-scan once the aircraft has moved this far ...
_RESOLVE_ALT_FT = 500.0       # ... or changed altitude this much
_SSV_HYSTERESIS = 1.15        # keep a locked station out to this * its SSV radius


def _navaid_range_nm(nav, ac_alt_ft: float) -> float:
    """Usable radius of a VOR/DME for the aircraft's altitude: the smaller of
    the facility's service volume and radio line of sight."""
    cls = getattr(nav, "nav_class", "") or ""
    r = cls[2] if len(cls) > 2 else ""
    if r == "T":
        ssv = _SSV_TERMINAL_NM
    elif r == "H":
        ssv = next(rad for floor, rad in _SSV_HIGH if ac_alt_ft >= floor)
    else:                                   # L, or an unspecified enroute VOR
        ssv = _SSV_LOW_NM
    elev = getattr(nav, "elev_ft", None) or 0
    los = 1.23 * (max(ac_alt_ft, 0.0) ** 0.5 + max(float(elev), 0.0) ** 0.5)
    return min(ssv, los)


def _round_khz(mhz: float) -> float:
    return round(mhz * 1000.0) / 1000.0


def _tune(mhz: float, lo: float, hi: float, *, mhz_delta: int = 0, khz_delta_steps: int = 0,
          step_khz: int = 25) -> float:
    """Step the MHz part and the kHz part independently, each wrapping in place."""
    mhz = _round_khz(mhz)
    whole = int(mhz)                       # truncate - never round the MHz digits up
    frac_khz = round((mhz - whole) * 1000.0)
    lo_w, hi_w = int(lo), int(hi)
    if mhz_delta:
        whole = lo_w + (whole - lo_w + mhz_delta) % (hi_w - lo_w + 1)
    if khz_delta_steps:
        n = 1000 // step_khz
        frac_khz = ((frac_khz // step_khz + khz_delta_steps) % n) * step_khz
    return _round_khz(whole + frac_khz / 1000.0)


@dataclass
class ComRadio:
    active_mhz: float = 118.000
    standby_mhz: float = 121.500

    def swap(self) -> None:
        self.active_mhz, self.standby_mhz = self.standby_mhz, self.active_mhz

    def set_emergency(self) -> None:
        """COM flip-flop held: 121.500 MHz active, previous active -> standby
        (GNS 530 Pilot's Guide sec.1.2)."""
        if self.active_mhz != 121.500:
            self.standby_mhz = self.active_mhz
        self.active_mhz = 121.500

    def tune(self, *, mhz: int = 0, khz: int = 0) -> None:
        self.standby_mhz = _tune(self.standby_mhz, COM_MIN, COM_MAX,
                                 mhz_delta=mhz, khz_delta_steps=khz, step_khz=COM_STEP_KHZ)


@dataclass
class NavReceiver:
    active_mhz: float = 110.00
    standby_mhz: float = 108.00
    obs_deg: float = 0.0                 # selected course, magnetic

    # resolved (filled by resolve()):
    station_ident: str = ""
    station_name: str = ""
    station_pos: Point | None = None
    station_magvar: float = 0.0          # 0 for a localizer
    is_localizer: bool = False
    has_dme: bool = False
    loc_course_deg: float | None = None  # localizer front course (from the paired runway)
    gs_ref: tuple | None = None          # (threshold Point, threshold_elev_ft, gs_angle_deg)
    _resolved_freq: float | None = field(default=None, repr=False)
    _resolved_pos: Point | None = field(default=None, repr=False)
    _resolved_alt: float | None = field(default=None, repr=False)
    _station: object = field(default=None, repr=False)   # the bound VhfNavaid

    # -- tuning -----------------------------------------------------
    def swap(self) -> None:
        self.active_mhz, self.standby_mhz = self.standby_mhz, self.active_mhz
        self._resolved_freq = None
        self._resolved_pos = None

    def tune(self, *, mhz: int = 0, khz: int = 0) -> None:
        self.standby_mhz = _tune(self.standby_mhz, NAV_MIN, NAV_MAX,
                                 mhz_delta=mhz, khz_delta_steps=khz, step_khz=NAV_STEP_KHZ)

    def set_obs(self, deg: float) -> None:
        self.obs_deg = norm360(deg)

    def turn_obs(self, delta: float) -> None:
        self.obs_deg = norm360(self.obs_deg + delta)

    @property
    def tuned(self) -> bool:
        return self.station_pos is not None

    @property
    def course_deg(self) -> float:
        """The course the CDI/HSI is deviating from: the localizer front course
        if we have it, otherwise the OBS setting. (A localizer's needle is the beam itself; it
        does not move with the OBS card - only a VOR's does.)"""
        return self.loc_course_deg if self.loc_course_deg is not None else self.obs_deg

    manual_course: bool = False     # True: the HSI card is only what the pilot sets (see `card_deg`)

    @property
    def card_deg(self) -> float:
        """The course pointer (the HSI/CDI card) - what the autopilot's NAV/APR mode flies.
        With `manual_course` it is always the pilot's OBS setting: S-TEC POH sec.3.3.3 has the pilot
        "Set Course Pointer to FRONT INBOUND LOC course". Otherwise it follows the beam."""
        return self.obs_deg if self.manual_course else self.course_deg

    @property
    def has_gs(self) -> bool:
        return self.gs_ref is not None

    # -- binding to nav data -------------------------------------
    def resolve(self, db, ac_pos: Point, ac_alt_ft: float = 1500.0) -> None:
        """Bind the active frequency to a *receivable* station, re-evaluated as
        the aircraft moves.

        * A VOR/DME is usable inside its altitude-dependent service volume
          (terminal 25, low 40, high 40/100/130 nm) and radio line of sight.
        * A localizer is directional - usable only inside its beam.

        The currently-locked station is kept (with hysteresis) until it drops
        out of range or a decisively closer co-frequency station appears, so the
        receiver does not flicker at a service-volume edge but *does* pick up a
        new emitter when you fly into its area and time out when you leave.
        """
        f = _round_khz(self.active_mhz)
        moved = (self._resolved_pos is None
                 or great_circle_nm(ac_pos, self._resolved_pos) > _RESOLVE_MOVE_NM)
        alt_changed = (self._resolved_alt is None
                       or abs(ac_alt_ft - self._resolved_alt) > _RESOLVE_ALT_FT)
        if f == self._resolved_freq and not moved and not alt_changed:
            return
        self._resolved_freq, self._resolved_pos, self._resolved_alt = f, ac_pos, ac_alt_ft

        best, best_d = None, 1e9
        for lst in db.vhf.values():
            for n in lst:
                if abs(n.freq_mhz - f) >= 0.005:
                    continue
                if not _receivable(db, n, ac_pos, ac_alt_ft):
                    continue
                d = great_circle_nm(n.pos, ac_pos)
                if d < best_d:
                    best, best_d = n, d

        cur = self._station
        if (cur is not None and abs(cur.freq_mhz - f) < 0.005
                and _receivable(db, cur, ac_pos, ac_alt_ft, hysteresis=True)):
            cur_d = great_circle_nm(cur.pos, ac_pos)
            if best is None or best_d >= cur_d * 0.7:   # stay locked to the current
                best, best_d = cur, cur_d

        self._station = best
        self._bind(db, best)

    def _bind(self, db, station) -> None:
        self.loc_course_deg = None
        self.gs_ref = None
        if station is None:
            self.station_ident = self.station_name = ""
            self.station_pos = None
            self.station_magvar = 0.0
            self.is_localizer = self.has_dme = False
            return
        self.station_ident = station.ident
        self.station_name = getattr(station, "name", "")
        self.station_pos = station.pos
        self.is_localizer = bool(getattr(station, "is_localizer", None)) or \
            (getattr(station, "nav_class", "") or "")[:1] == "I"
        self.has_dme = getattr(station, "has_dme", False)
        self.station_magvar = getattr(station, "magvar_deg", 0.0)
        if self.is_localizer:
            # CIFP section P·I gives the course + runway directly; fall back to
            # the geometric pairing for section-D localizers.
            brg = getattr(station, "loc_bearing_deg", None)
            apt = db.airport(getattr(station, "airport_ident", "") or "")
            rwy = (apt.runways.get(getattr(station, "runway_ident", ""))
                   if apt is not None else None)
            if brg is None or rwy is None:
                rwy, apt = _pair_runway(db, station.pos)
                brg = rwy.bearing_deg if rwy is not None else brg
            if not self.station_magvar and apt is not None:
                # loc_bearing_deg (P.I) is magnetic like any other ARINC course
                # field, but doesn't carry its own magvar - use the airport's
                # published station declination (how the course was actually
                # encoded), not a smooth WMM model, which can be off by several
                # degrees and put the CDI persistently off-centre.
                self.station_magvar = apt.magvar_deg
            if brg is not None:
                self.loc_course_deg = norm360(brg)
            if rwy is not None:
                elev = (apt.elev_ft if apt and apt.elev_ft is not None else 0)
                self.gs_ref = (rwy.threshold, float(elev), 3.0)


@dataclass
class Transponder:
    code: int = 1200            # four octal digits
    mode: str = "ALT"
    identing: bool = False
    cursor: int = 3             # digit under edit, 0 (MSD) .. 3 (LSD)

    def set_mode(self, mode: str) -> None:
        if mode in _XPDR_MODES:
            self.mode = mode

    def cycle_mode(self, delta: int = 1) -> None:
        self.mode = _XPDR_MODES[(_XPDR_MODES.index(self.mode) + delta) % len(_XPDR_MODES)]

    def move_cursor(self, delta: int) -> None:
        self.cursor = max(0, min(3, self.cursor + (1 if delta > 0 else -1)))

    def ident(self) -> None:
        """Flip-flop key in XPDR mode: fire the IDENT pulse."""
        self.identing = True

    def edit_digit(self, index: int, delta: int) -> None:
        """index 0..3, most-significant first; each digit is octal 0-7."""
        digs = [(self.code // 1000) % 10, (self.code // 100) % 10,
                (self.code // 10) % 10, self.code % 10]
        if not 0 <= index < 4:
            return
        digs[index] = (digs[index] + delta) % 8
        self.code = digs[0] * 1000 + digs[1] * 100 + digs[2] * 10 + digs[3]

    @property
    def squawk(self) -> str:
        return f"{self.code:04d}"


@dataclass
class RadioStack:
    com1: ComRadio = field(default_factory=lambda: ComRadio(118.000, 119.100))
    com2: ComRadio = field(default_factory=lambda: ComRadio(121.900, 121.700))
    nav1: NavReceiver = field(default_factory=lambda: NavReceiver(110.00, 111.60))
    nav2: NavReceiver = field(default_factory=lambda: NavReceiver(108.00, 113.20))
    xpdr: Transponder = field(default_factory=Transponder)

    def resolve(self, db, ac_pos: Point, ac_alt_ft: float = 1500.0) -> None:
        self.nav1.resolve(db, ac_pos, ac_alt_ft)
        self.nav2.resolve(db, ac_pos, ac_alt_ft)


# --------------------------------------------------------------------------- #
# localizer <-> runway pairing + reception geometry
# --------------------------------------------------------------------------- #
def _is_localizer(n) -> bool:
    return getattr(n, "is_localizer", (getattr(n, "nav_class", "") or "")[:1] == "I")


def _receivable(db, n, ac_pos: Point, ac_alt_ft: float, *, hysteresis: bool = False) -> bool:
    """Can this navaid be received from here?  Localizer -> inside the beam;
    VOR/DME -> inside its service volume + line of sight (a locked station gets
    a ``hysteresis`` margin so it fades out rather than blinking)."""
    if _is_localizer(n):
        return _localizer_receivable(db, n, ac_pos)
    rng = _navaid_range_nm(n, ac_alt_ft)
    if hysteresis:
        rng *= _SSV_HYSTERESIS
    return great_circle_nm(n.pos, ac_pos) <= rng


def _localizer_receivable(db, loc, ac_pos: Point) -> bool:
    """True when the aircraft is inside a localizer's beam: within
    :data:`_LOC_RANGE_NM` of the antenna and within :data:`_LOC_CONE_DEG` of the
    runway centreline (front *or* back course)."""
    d = great_circle_nm(loc.pos, ac_pos)
    if d > _LOC_RANGE_NM:
        return False
    # centreline bearing (from the antenna, looking back down the approach)
    brg = getattr(loc, "loc_bearing_deg", None)
    if brg is not None:
        apt = db.airport(getattr(loc, "airport_ident", "") or "")
        on_course = norm360(brg + (apt.magvar_deg if apt is not None else 0.0))
    else:
        rwy, _apt = _pair_runway(db, loc.pos)
        if rwy is None:
            return d <= 8.0                   # no geometry - only very close in
        on_course = initial_bearing(loc.pos, rwy.threshold)
    to_ac = initial_bearing(loc.pos, ac_pos)
    off = abs(angle_diff(to_ac, on_course))
    return min(off, 180.0 - off) <= _LOC_CONE_DEG


def _pair_runway(db, loc_pos: Point, *, max_nm: float = 3.0, align_tol_deg: float = 6.0):
    """Find the runway a localizer antenna belongs to.

    The antenna sits just off the *stop* end, on the extended centreline, so the
    landing runway's magnetic bearing points from its threshold toward the
    antenna. Returns ``(Runway, Airport)`` or ``(None, None)``.
    """
    best = (None, None)
    best_d = 1e9
    for apt in db.nearest_airports(loc_pos, 4, max_nm=max_nm + 2.0):
        for rwy in apt.runways.values():
            d = great_circle_nm(rwy.threshold, loc_pos)
            if d > max_nm or d < 0.15:
                continue
            brg = initial_bearing(rwy.threshold, loc_pos)
            if abs(angle_diff(brg, rwy.bearing_deg)) <= align_tol_deg and d < best_d:
                best, best_d = (rwy, apt), d
    return best


# --------------------------------------------------------------------------- #
# VLOC Morse ident (FINDINGS D8 remainder) - pure timing, no audio backend.
# A real receiver keys the station's 2-3 letter ident in Morse every ~7-10 s so
# the pilot can positively identify the station before trusting it; render.py
# uses `morse_is_keyed` to light a small dot beside the ident text in sync.
# --------------------------------------------------------------------------- #
MORSE_CODE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
}
# FAA AIM 1-1-3: VOR/localizer ident is Morse at approximately 7 words per
# minute (PARIS standard: dit = 1.2 / wpm ~= 0.17 s). The original 20 wpm was
# nearly 3x too fast (F40).
VOR_IDENT_WPM = 7.0
# A real VOR/localizer sends its ident about four times in a 30 s cycle (FAA
# requires at least once per 30 s), i.e. one start every ~7.5 s. The loop
# period is that interval; the silence is whatever is left after the ident
# (never less than _MORSE_MIN_GAP_S, for a long ident). F41.
MORSE_REPEAT_PERIOD_S = 7.5
_MORSE_MIN_GAP_S = 1.5


def morse_pattern(ident: str) -> str:
    """Space-separated dot/dash pattern, e.g. ``morse_pattern("ILS")`` ->
    ``".. .-.. ..."``. Unrecognised characters are dropped."""
    return " ".join(MORSE_CODE[c] for c in ident.strip().upper() if c in MORSE_CODE)


def _morse_timeline(pattern: str) -> list[tuple[bool, int]]:
    """Expand a dot/dash pattern into ``(keyed, unit_count)`` segments: a dot
    is 1 unit, a dash 3; a 1-unit gap separates symbols within a character, a
    3-unit gap separates characters (ARRL/ITU standard timing)."""
    segs: list[tuple[bool, int]] = []
    chars = pattern.split(" ") if pattern else []
    for ci, ch in enumerate(chars):
        for si, sym in enumerate(ch):
            segs.append((True, 1 if sym == "." else 3))
            if si < len(ch) - 1:
                segs.append((False, 1))
        if ci < len(chars) - 1:
            segs.append((False, 3))
    return segs


def morse_is_keyed(ident: str, t: float, *, wpm: float = VOR_IDENT_WPM) -> bool:
    """True if the ident tone is "on" at time ``t`` seconds, looping the
    pattern plus a trailing silence gap. Pure function of ``t`` so the
    animation is deterministic and needs no per-receiver timer state."""
    pattern = morse_pattern(ident)
    segs = _morse_timeline(pattern)
    if not segs:
        return False
    dit = 1.2 / wpm
    ident_s = sum(u for _, u in segs) * dit
    period = max(MORSE_REPEAT_PERIOD_S, ident_s + _MORSE_MIN_GAP_S)
    tt = t % period
    acc = 0.0
    for keyed, units in segs:
        dur = units * dit
        if tt < acc + dur:
            return keyed
        acc += dur
    return False
