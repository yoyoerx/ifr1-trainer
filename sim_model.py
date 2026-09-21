"""sim_model.py - a minimal kinematic ownship.

Used when no X-Plane feed is present. It flies a commanded heading / altitude /
speed, or (``follow_leg``) intercepts and tracks the GPS active leg. Point-mass,
coordinated turns, first-order lags - enough to exercise ``gns530`` /
``instruments`` and to hand ``render`` something that moves.

All headings/courses are TRUE degrees; wind is meteorological (direction it
blows FROM). No vertical dynamics beyond a capped VS toward a target altitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from autopilot import nav_intercept_deg
from navmath import Point, angle_diff, destination, norm360, wind_triangle
from windsaloft import WindsAloftProfile, isa_temp_c

__all__ = ["Ownship", "SimModel", "tas_from_ias", "ias_from_tas", "density_ratio"]

_STD_RATE_DEG_S = 3.0
_MAX_BANK_DEG = 25.0
_ACCEL_KT_S = 2.0
_MAX_VS_FPM = 1000.0
_MAX_INTERCEPT_DEG = 45.0
_INTERCEPT_GAIN_DEG_PER_NM = 12.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def density_ratio(altitude_ft: float, oat_c: float | None = None) -> float:
    """Air-density ratio sigma = rho / rho_sl at a pressure altitude.

    With ``oat_c`` omitted this is the plain ISA-troposphere approximation
    (valid to ~36 000 ft, floored so the airspeed conversions stay finite
    higher up) - the trainer's stand-in for a real air-data computer, there
    is no throttle / power model. When ``oat_c`` is given (e.g. from a
    :class:`windsaloft.WindsAloftProfile`), sigma is scaled by the ratio of
    ISA-standard to actual absolute temperature at that altitude - the same
    first-order correction a flight computer's true-altitude/TAS window
    applies for a non-standard day.
    """
    sigma = (1.0 - 6.8755856e-6 * max(altitude_ft, 0.0)) ** 4.2558797
    if oat_c is not None:
        t_isa_k = isa_temp_c(altitude_ft) + 273.15
        t_act_k = max(oat_c + 273.15, 1.0)
        sigma *= t_isa_k / t_act_k
    return max(sigma, 0.10)


def tas_from_ias(ias_kt: float, altitude_ft: float, oat_c: float | None = None) -> float:
    """True airspeed for an indicated airspeed at altitude (TAS = IAS / sqrt(sigma)).

    Compressibility and instrument error are ignored - fine for a light-GA
    procedures trainer (~+2%/1000 ft near sea level, more with height)."""
    return ias_kt / math.sqrt(density_ratio(altitude_ft, oat_c))


def ias_from_tas(tas_kt: float, altitude_ft: float, oat_c: float | None = None) -> float:
    """Inverse of :func:`tas_from_ias` - the indicated airspeed the ASI shows."""
    return tas_kt * math.sqrt(density_ratio(altitude_ft, oat_c))


@dataclass(frozen=True, slots=True)
class Ownship:
    pos: Point
    heading_deg: float           # true
    track_deg: float             # true (heading + drift)
    gs_kt: float
    tas_kt: float
    altitude_ft: float
    vs_fpm: float = 0.0
    bank_deg: float = 0.0         # + = right wing down
    pitch_deg: float = 0.0        # + = nose up (derived from VS + TAS)
    turn_rate_dps: float = 0.0    # + = turning right
    slip_skid: float = 0.0        # inclinometer ball, -1..+1 (0 = coordinated)
    ias_kt: float = 0.0           # indicated airspeed (what the ASI shows)


class SimModel:
    def __init__(
        self,
        *,
        pos: Point,
        heading_deg: float,
        altitude_ft: float = 3000.0,
        tas_kt: float = 120.0,
        wind_from_deg: float = 0.0,
        wind_kt: float = 0.0,
        winds_aloft: WindsAloftProfile | None = None,
    ):
        self.pos = pos
        self.heading = norm360(heading_deg)
        self.altitude = float(altitude_ft)
        self.tas = float(tas_kt)
        self.wind_from = norm360(wind_from_deg)
        self.wind_kt = float(wind_kt)
        # a multi-altitude forecast (--winds-aloft / a fetched FD report) takes
        # over from the uniform wind_from/wind_kt above once set - see
        # windsaloft.WindsAloftProfile and _wind_solve/_step_speed below.
        self.winds_aloft = winds_aloft

        self.target_heading: float | None = None
        self.target_altitude: float | None = None
        self.target_tas: float | None = None
        self.target_ias: float | None = None     # if set, target_tas tracks it vs altitude
        self.target_vs: float | None = None      # fpm; overrides target_altitude while set
        self.bank = 0.0
        self._turn_rate = 0.0

        self._track, self._gs = self._wind_solve(self.heading)

    # -- commands ---------------------------------------------------
    def set_wind(self, from_deg: float, kt: float) -> None:
        """Set the uniform wind. Cleared to this alone if a winds-aloft profile
        was active - matches the "one knob wins" convention elsewhere (e.g.
        target_tas vs target_ias)."""
        self.wind_from, self.wind_kt = norm360(from_deg), float(kt)
        self.winds_aloft = None

    def set_winds_aloft(self, profile: WindsAloftProfile | None) -> None:
        self.winds_aloft = profile

    def current_wind(self) -> tuple[float, float]:
        """(from_deg, kt) actually acting on the aircraft right now - the
        winds-aloft profile at the current altitude if one is loaded, else the
        uniform wind. What a wind readout should show."""
        return self._wind_here()

    def _wind_here(self) -> tuple[float, float]:
        """(from_deg, kt) at the current altitude - profile if set, else uniform."""
        if self.winds_aloft is not None:
            return self.winds_aloft.wind_at(self.altitude)
        return self.wind_from, self.wind_kt

    def _oat_here(self) -> float | None:
        return self.winds_aloft.temp_at(self.altitude) if self.winds_aloft is not None else None

    def command(self, *, heading: float | None = None, altitude: float | None = None,
                tas: float | None = None, ias: float | None = None,
                vs: float | None = None, clear_vs: bool = False) -> None:
        if heading is not None:
            self.target_heading = norm360(heading)
        if altitude is not None:
            self.target_altitude = float(altitude)
        if tas is not None:                      # explicit TAS wins over the IAS hold
            self.target_tas = float(tas)
            self.target_ias = None
        if ias is not None:                      # hold an indicated airspeed instead
            self.target_ias = max(0.0, float(ias))
        if vs is not None:
            self.target_vs = float(vs)
        if clear_vs:
            self.target_vs = None

    def follow_leg(self, nav) -> None:
        """Aim to intercept and hold the GPS active leg described by ``nav``
        (a ``gns530.NavState``). No-op if there is no valid lateral guidance."""
        if nav is None or not getattr(nav, "valid", False) or nav.dtk is None:
            return
        # same S-TEC intercept the coupled autopilot flies (POH sec.3.1.2): a 45 deg cut
        # back toward the course, rolling onto it before the CDI centres
        intercept = nav_intercept_deg(nav.xtk_nm or 0.0,
                                      getattr(nav, "cdi_scale_nm", None), self._gs)
        wind_from, wind_kt = self._wind_here()
        wind_hdg, _, _ = wind_triangle(self.tas, nav.dtk, wind_from, wind_kt)
        self.target_heading = norm360(wind_hdg + intercept)

    # -- integration ---------------------------------------------
    def step(self, dt_s: float) -> Ownship:
        self._step_heading(dt_s)
        self._step_speed(dt_s)
        self._step_altitude(dt_s)
        self._track, self._gs = self._wind_solve(self.heading)
        if self._gs > 0.0:
            self.pos = destination(self.pos, self._track, self._gs * dt_s / 3600.0)
        return self.state

    @property
    def state(self) -> Ownship:
        tas = max(self.tas, 1.0)
        pitch = math.degrees(math.atan2(self._vs_fpm / 60.0, tas * 6076.115 / 3600.0))
        return Ownship(
            pos=self.pos,
            heading_deg=self.heading,
            track_deg=self._track,
            gs_kt=self._gs,
            tas_kt=self.tas,
            altitude_ft=self.altitude,
            vs_fpm=self._vs_fpm,
            bank_deg=self.bank,
            pitch_deg=pitch,
            turn_rate_dps=self._turn_rate,
            slip_skid=0.0,          # the model always flies coordinated
            ias_kt=ias_from_tas(self.tas, self.altitude, self._oat_here()),
        )

    # -- internals ----------------------------------------------
    def _step_heading(self, dt: float) -> None:
        if self.target_heading is None:
            self.bank = 0.0
            self._turn_rate = 0.0
            return
        err = angle_diff(self.target_heading, self.heading)   # + = target is CW
        max_step = _STD_RATE_DEG_S * dt
        step = _clamp(err, -max_step, max_step)
        self.heading = norm360(self.heading + step)
        rate = step / dt if dt else 0.0
        self._turn_rate = rate
        # bank for a rate-one-ish turn, capped
        self.bank = _clamp(math.copysign(rate / _STD_RATE_DEG_S, rate) * _MAX_BANK_DEG,
                           -_MAX_BANK_DEG, _MAX_BANK_DEG)
        if abs(err) <= max_step:
            self.heading = self.target_heading
            self.bank = 0.0
            self._turn_rate = 0.0

    def _step_speed(self, dt: float) -> None:
        # a held IAS set-point drives TAS as a function of the current altitude,
        # so a constant-IAS climb reads an increasing TAS / GS
        if self.target_ias is not None:
            self.target_tas = tas_from_ias(self.target_ias, self.altitude, self._oat_here())
        if self.target_tas is None:
            return
        d = _clamp(self.target_tas - self.tas, -_ACCEL_KT_S * dt, _ACCEL_KT_S * dt)
        self.tas = max(0.0, self.tas + d)

    def _step_altitude(self, dt: float) -> None:
        self._vs_fpm = 0.0
        # explicit vertical-speed hold (autopilot VS / GS mode) wins
        if self.target_vs is not None:
            self._vs_fpm = _clamp(self.target_vs, -3000.0, 3000.0)
            self.altitude += self._vs_fpm * dt / 60.0
            return
        if self.target_altitude is None:
            return
        err_ft = self.target_altitude - self.altitude
        if abs(err_ft) < 0.5:
            self.altitude = self.target_altitude
            self._vs_fpm = 0.0
            return
        # ease in over the last ~1000 ft
        vs = _clamp(err_ft * 6.0, -_MAX_VS_FPM, _MAX_VS_FPM)
        self._vs_fpm = vs
        self.altitude += vs * dt / 60.0
        if (err_ft >= 0) != (self.target_altitude - self.altitude >= 0):
            self.altitude = self.target_altitude
            self._vs_fpm = 0.0

    def _wind_solve(self, heading: float) -> tuple[float, float]:
        """Ground track & speed for an air vector flown at ``heading``, using
        the winds-aloft profile at the current altitude if one is set."""
        wind_from, wind_kt = self._wind_here()
        hx = self.tas * math.sin(math.radians(heading))
        hy = self.tas * math.cos(math.radians(heading))
        blow = math.radians(wind_from + 180.0)
        wx = wind_kt * math.sin(blow)
        wy = wind_kt * math.cos(blow)
        gx, gy = hx + wx, hy + wy
        gs = math.hypot(gx, gy)
        track = norm360(math.degrees(math.atan2(gx, gy))) if gs > 1e-9 else heading
        return track, gs

    _vs_fpm = 0.0
