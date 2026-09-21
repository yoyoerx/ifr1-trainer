"""autopilot.py - an S-TEC Fifty Five X style autopilot.

The S-TEC 55X is **rate based** (it steers through the turn coordinator, not an
attitude reference), so the base engaged roll state is simply "wings level"
(`Lat.LVL`) and the pilot layers a mode on top with the programmer buttons:

    HDG   NAV   APR   REV   ALT   VS        + a VS/ALT knob and TRIM arrows

* NAV / APR / REV steer an active intercept (up to a 45 deg cut) the instant
  they're pressed - S-TEC 55X POH (4th Ed.) sec.3.1.2: "If the CDI is at full
  scale (100%) needle deflection from center, then the autopilot will
  establish the aircraft on a 45 degree intercept angle relative to the
  selected course ... the turn will always begin between 100% and 20% CDI
  needle deflection ... When the aircraft arrives at 15% CDI needle deflection,
  the course is captured" - then *capture* (tighten onto the centered needle)
  once alive. On a GPS leg that is `nav_intercept_deg`: a flat 45 deg cut until
  the turn-in point, then a shrinking angle so the aircraft rolls onto the leg's
  course line and tracks it (rather than curving asymptotically toward the fix). There is no wings-level
  dead zone while "armed" waiting for the needle; `armed_lat` is only the
  not-yet-captured annunciator flag.
* **GPSS** (GPS roll steering) is a modifier on NAV/APR: when on, the AP flies
  the GPS's digital steering command instead of chasing the analog CDI.
  POH sec.4.2.5: pressing NAV a second time (while already in NAV mode)
  enters GPSS; a further press deletes it - NAV mode itself stays engaged.
* **REV** is APR with reversed localizer sensing (back course) and no glideslope.
* Vertical axis: **ALT** holds the present altitude, **VS** holds the knob-set
  vertical speed; **GS** captures from APR on an ILS. An altitude-selector
  (preselect) capture out of VS is modelled as an installed option.

`update(...)` returns a :class:`Commands` bundle for ``SimModel.command(...)``.
Pure - only primitives cross the boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from navmath import norm180, norm360

__all__ = ["Lat", "Vert", "Commands", "Autopilot"]


class Lat(str, Enum):
    OFF = "OFF"
    LVL = "LVL"     # engaged, wings level (turn-coordinator hold) - the S-TEC base
    HDG = "HDG"
    NAV = "NAV"
    APR = "APR"
    REV = "REV"     # back-course localizer


class Vert(str, Enum):
    OFF = "OFF"
    VS = "VS"
    ALT = "ALT"
    GS = "GS"


@dataclass(frozen=True, slots=True)
class Commands:
    heading: float | None = None
    altitude: float | None = None
    vs: float | None = None
    clear_vs: bool = False
    turn_rate_dps: float | None = None      # roll-axis turn-rate limit (POH sec.4.1); None = pilot's


_NAV_GAIN = 8.0        # deg intercept / nm XTK  (NAV, analog CDI)
_APR_GAIN = 13.0       # deg intercept / nm XTK  (APR, tighter)
_GPSS_GAIN = 12.0      # deg / nm  (digital roll steering, crisp)
_MAX_DRIFT_DEG = 30.0  # cap on the wind-drift correction applied to a track command
_VLOC_GAIN = 22.0      # deg / unit-deflection   (localizer)
_VLOC_I_BAND = 0.2     # only integrate while |deflection| is this small
_VLOC_I_MAX = 8.0      # deg - drift trim authority
_VLOC_I_GAIN = 3.0     # deg of trim per (unit-deflection . s) - wind-drift trim on VOR/LOC
_MAX_INTERCEPT = 30.0
_CAPTURE_XTK_NM = 1.2   # fallback capture band when the CDI scale is unknown

# S-TEC 55X POH sec.3.1.2 (4th Ed.): 45 deg intercept at full-scale deflection; the
# turn onto the course "will always begin between 100% and 20% CDI needle
# deflection" (variable with closure rate); "at 15% CDI needle deflection, the
# course is captured"; the intercept turn is limited to 90% of a standard-rate turn.
_NAV_INTERCEPT_DEG = 45.0
_TURN_IN_MIN_FRAC = 0.20        # of full-scale CDI deflection
_TURN_IN_MAX_FRAC = 1.00
_CAPTURE_FRAC = 0.15
STD_RATE_DPS = 3.0
_INTERCEPT_TURN_RATE_DPS = STD_RATE_DPS * 0.9
_DEFAULT_CDI_SCALE_NM = 5.0

# The NAV / APR / REV coupler's timeline (POH sec.3.1.2, p.3-4/3-5). After the 15% capture the
# turn-rate authority steps down: CAP (90% of standard rate) -> +15 s CAP SOFT (45%) -> +30 s the
# crosswind correction is established -> +75 s SOFT (15%, NAV only: APR is the higher-authority
# CAP SOFT tracking, p.3-5). In SOFT short-term needle excursions are ignored, and >50% deflection
# for 60 s falls back to CAP SOFT.
_RATE_FRAC = {"INTERCEPT": 0.90, "CAP": 0.90, "CAP SOFT": 0.45, "SOFT": 0.15}
# deg of course cut per unit deflection by stage: "maximum gain" in CAP, stepping down (POH p.3-4)
_STAGE_GAIN = {"CAP": 50.0, "CAP SOFT": 30.0, "SOFT": _VLOC_GAIN}
_HDG_RATE_FRAC = 0.90           # sec.4.1: HDG, NAV, APR, REV, CWS
_T_CAP_SOFT = 15.0
_T_WIND_CORR = 30.0
_T_SOFT = 75.0
_SOFT_FILTER_S = 15.0
_SOFT_REVERT_DEV = 0.50
_SOFT_REVERT_S = 60.0
_IMMEDIATE_SOFT_DEV = 0.10      # engaged nearly on course: skip straight to tracking (p.3-4)
_COURSE_CHANGE_DEG = 10.0       # a new course this different reverts to CAP (p.3-5)
# The POH gives only bounds for where the turn onto the course begins (100%..20% of full scale,
# earlier at higher closure rate). The trainer's model: begin when the time to reach the course at
# the closure rate held during the 45 deg cut falls to the time a 90%-rate roll-out needs
# (~8.8 s of closure, +30% margin), inside those bounds.
_T_TURN_IN_S = 11.5
_CLOSURE_FILTER_S = 3.0
_TRIM_MAX_CLOSURE = 0.01         # deflection-fraction / s
# the drift correction is measured (track vs heading); this trim only mops up the residual, and
# is kept small so the slow SOFT/CAP SOFT authority doesn't limit-cycle around the course
_COUPLER_I_GAIN = 0.6
_COUPLER_I_MAX = 3.0
_CAPTURE_DEFLECTION = 0.75
_VS_KNOB_STEP = 100.0

# A pure proportional xtk->intercept loop settles at a nonzero steady-state
# offset in any crosswind: the intercept angle has to equal the wind
# correction angle for the track to hold, and with intercept = -xtk * gain
# that only happens once xtk itself sits at -WCA/gain (e.g. ~1.25 nm left of
# course in a stiff crosswind at the default gain) - the AP visibly never
# settles onto the magenta line. A slow integral trim on xtk removes that
# offset over time, same as a real coupler's long-term trim: it accumulates
# just enough extra correction to hold zero xtk once the transient settles.
_XTK_I_GAIN = 0.5      # deg of trim per (nm . s) of accumulated xtk
_XTK_I_MAX = 15.0      # deg - trim authority is modest, capture still leads


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def turn_in_distance_nm(gs_kt: float, scale_nm: float) -> float:
    """Distance from the course (nm) at which the turn onto it begins: the lead a
    90%-standard-rate turn from a 45 deg cut needs (with margin), held within the
    POH's 100%..20% of full-scale CDI deflection."""
    scale = scale_nm if scale_nm and scale_nm > 0 else _DEFAULT_CDI_SCALE_NM
    radius = max(gs_kt, 0.0) / 3600.0 / math.radians(_INTERCEPT_TURN_RATE_DPS)
    lead = 1.5 * radius * (1.0 - math.cos(math.radians(_NAV_INTERCEPT_DEG)))
    return _clamp(lead, _TURN_IN_MIN_FRAC * scale, _TURN_IN_MAX_FRAC * scale)


def nav_intercept_deg(xtk_nm: float, scale_nm: float, gs_kt: float) -> float:
    """Angle (deg, + = right) to add to the leg's DTK for a GPS leg ``xtk_nm`` off
    it (+ = ownship right of course): a flat 45 deg cut back toward the course
    until the turn-in distance, then shrinking to zero at the course line."""
    k = _NAV_INTERCEPT_DEG / turn_in_distance_nm(gs_kt, scale_nm)
    return -_clamp(xtk_nm * k, -_NAV_INTERCEPT_DEG, _NAV_INTERCEPT_DEG)


@dataclass
class Autopilot:
    engaged: bool = False
    lateral: Lat = Lat.OFF
    vertical: Vert = Vert.OFF
    armed_lat: Lat | None = None
    armed_vert: Vert | None = None
    gpss: bool = False

    heading_bug: float = 0.0          # magnetic, from the DG bug
    alt_preselect: float = 5000.0     # altitude selector option
    vs_target: float = 0.0            # the VS window on the programmer
    alt_hold_ft: float | None = None  # captured on ALT engage
    trim: int = 0                     # -1 / 0 / +1 - TRIM annunciator arrow
    _xtk_i: float = 0.0               # lateral integral trim (deg), see _XTK_I_GAIN
    # NAV/APR/REV coupler state (see `_couple`)
    stage: str = "INTERCEPT"          # INTERCEPT | CAP | CAP SOFT | SOFT (POH sec.3.1.2)
    _cap_t: float = 0.0               # seconds since course capture
    _closure: float = 0.0             # filtered closure rate, deflection-fraction / s
    _closure_peak: float = 0.0
    _dev_prev: float | None = None
    _dev_soft: float = 0.0            # low-passed deflection used in SOFT
    _over50_t: float = 0.0
    _course_ref: float | None = None
    _rate_frac: float | None = None   # turn-rate limit (fraction of standard rate) this frame
    _src: str = ""                    # what the coupler is flying: mode + needle source

    # -- master (yoke AP/disconnect) --------------------------------
    def press_ap(self) -> None:
        self.disengage() if self.engaged else self.engage()

    def engage(self) -> None:
        self.engaged = True
        if self.lateral is Lat.OFF:
            self.lateral = Lat.LVL

    def _reset_coupler(self) -> None:
        self.stage = "INTERCEPT"
        self._cap_t = self._closure = self._closure_peak = self._dev_soft = self._over50_t = 0.0
        self._dev_prev = self._course_ref = None
        self._xtk_i = 0.0

    def disengage(self) -> None:
        self.engaged = False
        self.lateral = Lat.OFF
        self.vertical = Vert.OFF
        self.armed_lat = self.armed_vert = None
        self.alt_hold_ft = None
        self.trim = 0
        self._xtk_i = 0.0

    # -- programmer buttons ---------------------------------------
    # NAV/APR/REV engage - and start actively intercepting - the moment
    # they're pressed, exactly like the real S-TEC 55X: "the turn will
    # always begin between 100% (full-scale) needle deflection and 20% of
    # full-scale" (POH sec.4.2.2) - there is no wings-level dead zone while
    # "armed" waiting for the needle to already be centered. `armed_lat`
    # still tracks not-yet-captured (needle not yet alive/centered) purely
    # for the annunciator - `_lateral_command` steers on `self.lateral`
    # itself throughout, armed or captured.
    def press_hdg(self) -> None:
        self.engage()
        self.lateral = Lat.LVL if self.lateral is Lat.HDG else Lat.HDG
        self.armed_lat = None

    def press_nav(self) -> None:
        self.engage()
        if self.lateral is Lat.NAV:
            # POH sec.4.2.5: "push the NAV button twice" enters GPSS mode;
            # "push the NAV button again" (a further press) deletes it -
            # NAV mode itself stays engaged either way. To fully leave NAV,
            # select HDG (or disconnect the AP), same as the real unit.
            self.gpss = not self.gpss
            return
        self.lateral = Lat.NAV
        self.armed_lat = Lat.NAV
        self.armed_vert = None
        self.gpss = False
        self._reset_coupler()

    def press_apr(self) -> None:
        self.engage()
        if self.lateral is Lat.APR:
            self.armed_lat = self.armed_vert = None
            self.lateral = Lat.LVL
            if self.vertical is Vert.GS:
                self.vertical = Vert.OFF
            return
        self.lateral = Lat.APR
        self.armed_lat = Lat.APR
        self.armed_vert = Vert.GS
        self._reset_coupler()

    def press_rev(self) -> None:
        self.engage()
        if self.lateral is Lat.REV:
            self.lateral, self.armed_lat = Lat.LVL, None
            return
        self.lateral = Lat.REV
        self.armed_lat = Lat.REV
        self._reset_coupler()

    def press_alt(self) -> None:
        self.engage()
        self.vertical = Vert.ALT
        self.alt_hold_ft = None          # grab present altitude next update

    def press_vs(self) -> None:
        self.engage()
        self.vertical = Vert.VS

    def toggle_gpss(self) -> None:
        self.gpss = not self.gpss

    # -- knobs ---------------------------------------------------
    def turn_vs_knob(self, detents: int) -> None:
        if self.vertical is Vert.ALT and self.alt_hold_ft is not None:
            self.alt_hold_ft += detents * 100.0
        else:
            self.vs_target = _clamp(self.vs_target + detents * _VS_KNOB_STEP, -2000.0, 2000.0)

    def set_heading_bug(self, deg: float) -> None:
        self.heading_bug = norm360(deg)

    def turn_heading_bug(self, delta: float) -> None:
        self.heading_bug = norm360(self.heading_bug + delta)

    def set_preselect(self, ft: float) -> None:
        self.alt_preselect = float(ft)

    def set_vs_target(self, fpm: float) -> None:
        self.vs_target = float(fpm)

    # -- annunciator text --------------------------------------
    @property
    def ready(self) -> bool:
        return self.engaged and self.lateral not in (Lat.OFF,)

    def led_bitmask(self) -> int:
        """IFR-1 AP-row LEDs: bit0 AP, bit1 HDG, bit2 NAV, bit3 APR, bit4 ALT, bit5 VS.

        A mode's LED lights when it is active *or* armed (the IFR-1 lamps are
        plain on/off, so armed and captured read the same).
        """
        if not self.engaged:
            return 0
        lat = (self.lateral, self.armed_lat)
        vert = (self.vertical, self.armed_vert)
        bits = 0x01                                    # AP engaged
        if Lat.HDG in lat:
            bits |= 0x02
        if Lat.NAV in lat:
            bits |= 0x04
        if Lat.APR in lat or Lat.REV in lat:
            bits |= 0x08
        if Vert.ALT in vert:
            bits |= 0x10
        if Vert.VS in vert or Vert.GS in vert:
            bits |= 0x20
        return bits

    def mode_line(self) -> str:
        if not self.engaged:
            return "AP OFF"
        lat = self.lateral.value
        if self.armed_lat is not None and self.armed_lat is not self.lateral:
            lat += f"/{self.armed_lat.value}→"
        elif self.armed_lat is not None:
            lat += "→"          # engaged, still intercepting (needle not yet alive/centered)
        if self.gpss and self.lateral in (Lat.NAV, Lat.APR):
            lat += "+GPSS"
        vert = self.vertical.value if self.vertical is not Vert.OFF else "--"
        if self.armed_vert:
            vert += f"/{self.armed_vert.value}→"
        return f"AP  {lat}   {vert}"

    # -- the per-loop update --------------------------------
    def update(
        self,
        nav_state,
        own,
        magvar: float,
        *,
        dt: float = 1.0,
        vloc_course_deg: float | None = None,
        vloc_deflection: float | None = None,
        vloc_valid: bool = False,
        gs_deflection: float = 0.0,
        gs_valid: bool = False,
    ) -> Commands:
        if not self.engaged:
            self.trim = 0
            return Commands()

        alt = getattr(own, "altitude_ft", 0.0)
        gs_kt = max(getattr(own, "gs_kt", 0.0), 1.0)

        self._maybe_capture_lateral(nav_state, vloc_deflection, vloc_valid)
        self._maybe_capture_gs(gs_deflection, gs_valid)

        self._rate_frac = None
        cmd_heading = self._lateral_command(nav_state, own, magvar, dt,
                                            vloc_course_deg, vloc_deflection, vloc_valid)
        cmd_alt, cmd_vs, clear_vs = self._vertical_command(alt, gs_kt, gs_deflection)

        if self.vertical is Vert.VS:
            band = max(60.0, abs(getattr(own, "vs_fpm", 0.0)) / 9.0)
            if abs(alt - self.alt_preselect) <= band:
                self.vertical = Vert.ALT
                self.alt_hold_ft = self.alt_preselect
                cmd_alt, cmd_vs, clear_vs = self.alt_preselect, None, True

        self.trim = 1 if (cmd_vs or 0) > 200 else -1 if (cmd_vs or 0) < -200 else 0
        rate = None if self._rate_frac is None else self._rate_frac * STD_RATE_DPS
        return Commands(heading=cmd_heading, altitude=cmd_alt, vs=cmd_vs, clear_vs=clear_vs,
                        turn_rate_dps=rate)

    # -- lateral --------------------------------------------
    def _maybe_capture_lateral(self, nav_state, vloc_deflection, vloc_valid) -> None:
        """Clear `armed_lat` once the needle comes alive/centers - `self.lateral`
        is already the engaged mode from the button press (see press_nav/apr/
        rev), so this only retires the "still intercepting" annunciator flag,
        it does not gate whether the AP steers."""
        if self.armed_lat not in (Lat.NAV, Lat.APR, Lat.REV):
            return
        cdi_source = getattr(nav_state, "cdi_source", "GPS") if nav_state is not None else "GPS"
        if self.armed_lat is Lat.NAV and cdi_source != "VLOC":
            xtk = getattr(nav_state, "xtk_nm", None) if nav_state else None
            scale = getattr(nav_state, "cdi_scale_nm", None)
            band = _CAPTURE_FRAC * scale if scale else _CAPTURE_XTK_NM
            live = xtk is not None and abs(xtk) <= band
        else:  # APR / REV, or NAV tracking VLOC: track the localizer/VOR needle
            live = (vloc_valid and vloc_deflection is not None
                    and abs(vloc_deflection) <= _CAPTURE_FRAC)     # POH: captured at 15%
        if live:
            self.armed_lat = None

    @staticmethod
    def _drift(own) -> float:
        """Wind-drift angle (deg, + = track is right of heading) measured from
        the aircraft's own track vs heading. NAV/APR steer a desired *track*;
        the heading command is that track minus this drift, so a crosswind is
        crabbed out immediately instead of being learned slowly by the integral
        trim (which left slow, wide oscillations - "not a perfect line")."""
        trk = getattr(own, "track_deg", None)
        hdg = getattr(own, "heading_deg", None)
        if trk is None or hdg is None:
            return 0.0
        return _clamp(norm180(trk - hdg), -_MAX_DRIFT_DEG, _MAX_DRIFT_DEG)

    def _vloc_intercept(self, dev: float, dt: float, integrate: bool = True,
                        gain: float = _VLOC_GAIN, i_gain: float = _VLOC_I_GAIN,
                        i_max: float = _VLOC_I_MAX) -> float:
        """Intercept angle (deg, + = turn right of the course) for a VOR/LOC
        needle deflection ``dev`` (+ = fly right): proportional, plus a small
        integral wind-drift trim (`_xtk_i`, shared with the GPS paths).
        The trim only accumulates while the needle is nearly centred
        (|dev| <= `_VLOC_I_BAND`) - a steady small offset is drift; a large
        one is just the intercept in progress, and integrating through it
        winds the trim up and overshoots the course (the first cut of this
        did exactly that, even in still air). Outside the band it bleeds off.
        Without any trim, a steady crosswind leaves a permanent offset (KLNS
        ILS 08 playtest)."""
        if dt:
            if integrate and abs(dev) <= _VLOC_I_BAND:
                self._xtk_i = _clamp(self._xtk_i + dev * i_gain * dt, -i_max, i_max)
            else:
                self._xtk_i *= max(0.0, 1.0 - 0.3 * dt)     # bleed off during an intercept
        return _clamp(dev * gain + self._xtk_i, -_MAX_INTERCEPT, _MAX_INTERCEPT)

    def _couple(self, dev: float, course_deg: float, own, dt: float, *, soft_ok: bool,
                src: str = "") -> float:
        """The S-TEC NAV / APR / REV coupler (POH sec.3.1.2). ``dev`` is the needle
        deflection as a fraction of full scale, + = fly right; ``course_deg`` the true
        course being flown. Returns the desired *heading* and sets `_rate_frac`.

        INTERCEPT: a flat 45 deg cut toward the course, turning onto it once the time to
        reach it (at the closure rate held during the cut) drops below a roll-out's worth,
        always inside 100%..20% of full scale. At 15% deflection the course is captured
        (CAP), then authority steps down on the POH's timeline (`_RATE_FRAC`)."""
        a = abs(dev)
        if src != self._src:                                # a different mode/needle: start over
            self._reset_coupler()
            self._src = src
        if (self._course_ref is not None and self.stage != "INTERCEPT"
                and abs(norm180(course_deg - self._course_ref)) >= _COURSE_CHANGE_DEG):
            self.stage, self._cap_t = "CAP", 0.0            # p.3-5: new course >= 10 deg -> CAP
        self._course_ref = course_deg
        if self._dev_prev is None:                          # first frame after engaging
            self._dev_soft = dev
            hdg_err = abs(norm180(getattr(own, "heading_deg", course_deg) - course_deg))
            if a < _IMMEDIATE_SOFT_DEV and hdg_err <= 5.0:
                self.stage, self._cap_t = ("SOFT" if soft_ok else "CAP SOFT"), _T_SOFT
        elif dt > 0.0:
            closing = (abs(self._dev_prev) - a) / dt
            self._closure += (closing - self._closure) * min(1.0, dt / _CLOSURE_FILTER_S)
        self._dev_prev = dev

        if self.stage == "INTERCEPT":
            self._closure_peak = max(self._closure_peak, self._closure)
            if a <= _CAPTURE_FRAC:
                self.stage, self._cap_t = "CAP", 0.0
        if self.stage != "INTERCEPT":
            self._cap_t += dt
            if self.stage == "CAP" and self._cap_t >= _T_CAP_SOFT:
                self.stage = "CAP SOFT"
            if self.stage == "CAP SOFT" and soft_ok and self._cap_t >= _T_SOFT:
                self.stage = "SOFT"
            if self.stage != "SOFT":
                self._dev_soft = dev                    # tracks the needle until SOFT starts filtering it
            else:
                self._dev_soft += (dev - self._dev_soft) * min(1.0, dt / _SOFT_FILTER_S)
                self._over50_t = self._over50_t + dt if a > _SOFT_REVERT_DEV else 0.0
                if self._over50_t >= _SOFT_REVERT_S:
                    self.stage, self._cap_t, self._over50_t = "CAP SOFT", _T_CAP_SOFT, 0.0

        turn_in = 1.0
        if self.stage == "INTERCEPT":
            # roll out from 45 deg between the turn-in deflection and the 15% capture band, so
            # the heading is already near the course when capture steps the gain down
            turn_in = _clamp(_CAPTURE_FRAC + self._closure_peak * _T_TURN_IN_S,
                             _TURN_IN_MIN_FRAC, _TURN_IN_MAX_FRAC)
            at_capture = _STAGE_GAIN["CAP"] * _CAPTURE_FRAC
            ramp = _clamp((a - _CAPTURE_FRAC) / max(turn_in - _CAPTURE_FRAC, 1e-6), 0.0, 1.0)
            angle = math.copysign(at_capture + (_NAV_INTERCEPT_DEG - at_capture) * ramp, dev)
        else:
            d = self._dev_soft if self.stage == "SOFT" else dev
            # the wind-drift trim only learns from 30 s after capture (the POH's "establishes the
            # crosswind correction") and once the needle has stopped closing - integrating through
            # an intercept winds it up and overshoots
            settled = self._cap_t >= _T_WIND_CORR and abs(self._closure) < _TRIM_MAX_CLOSURE
            angle = self._vloc_intercept(d, dt, integrate=settled, gain=_STAGE_GAIN[self.stage],
                                          i_gain=_COUPLER_I_GAIN, i_max=_COUPLER_I_MAX)
        self._rate_frac = _RATE_FRAC[self.stage]
        # the crosswind correction is only established 30 s after capture (p.3-4)
        # (on the roll-out ramp it applies too: without it a steady wind holds the aircraft just outside
        # the capture band forever, on a heading that only balances the drift)
        drift = 0.0
        if (self.stage == "INTERCEPT" and a < turn_in) or (self.stage != "INTERCEPT" and self._cap_t >= _T_WIND_CORR):
            drift = self._drift(own)
        return norm360(course_deg + angle - drift)

    def _gps_track_command(self, nav_state, own, dt) -> float:
        """Desired track for a GPS leg: DTK plus the S-TEC intercept angle, plus a
        slow wind-drift trim that only accumulates once inside the capture band
        (integrating through the 45 deg cut would wind it up and overshoot)."""
        xtk = getattr(nav_state, "xtk_nm", 0.0) or 0.0
        scale = getattr(nav_state, "cdi_scale_nm", None) or _DEFAULT_CDI_SCALE_NM
        gs = getattr(own, "gs_kt", 0.0) or 0.0
        if dt:
            if abs(xtk) <= _CAPTURE_FRAC * scale:
                self._xtk_i = _clamp(self._xtk_i - xtk * _XTK_I_GAIN * dt, -_XTK_I_MAX, _XTK_I_MAX)
            else:
                self._xtk_i *= max(0.0, 1.0 - 0.3 * dt)
        angle = _clamp(nav_intercept_deg(xtk, scale, gs) + self._xtk_i,
                       -_NAV_INTERCEPT_DEG, _NAV_INTERCEPT_DEG)
        return norm360(nav_state.dtk + angle - self._drift(own))

    def _lateral_command(self, nav_state, own, magvar, dt,
                         vloc_course_deg, vloc_deflection, vloc_valid) -> float:
        lat = self.lateral
        hdg = getattr(own, "heading_deg", 0.0)

        if lat is Lat.HDG:
            self._rate_frac = _HDG_RATE_FRAC
            return norm360(self.heading_bug + magvar)

        if lat is Lat.NAV:
            # NAV tracks whatever the GNS CDI is actually showing (S-TEC 55X
            # POH: NAV couples to the selected nav source) - once the pilot
            # swaps CDI source to VLOC (the SWAP/CDI key), NAV should track
            # the VOR/LOC needle, not keep silently flying the GPS course
            # underneath a CDI that no longer displays it.
            cdi_source = getattr(nav_state, "cdi_source", "GPS") if nav_state is not None else "GPS"
            if cdi_source == "VLOC":
                if vloc_valid and vloc_course_deg is not None:
                    return self._couple(vloc_deflection or 0.0, vloc_course_deg + magvar,
                                        own, dt, soft_ok=True, src="NAV/VLOC")
            elif nav_state is not None and getattr(nav_state, "dtk", None) is not None:
                if self.gpss:
                    return self._gps_track_command(nav_state, own, dt)
                scale = getattr(nav_state, "cdi_scale_nm", None) or _DEFAULT_CDI_SCALE_NM
                xtk = getattr(nav_state, "xtk_nm", 0.0) or 0.0
                return self._couple(-xtk / scale, nav_state.dtk, own, dt, soft_ok=True, src="NAV/GPS")

        if lat in (Lat.APR, Lat.REV) and vloc_valid and vloc_course_deg is not None:
            dev = (vloc_deflection or 0.0) * (-1.0 if lat is Lat.REV else 1.0)
            if self.gpss and lat is Lat.APR and nav_state is not None \
                    and getattr(nav_state, "dtk", None) is not None:
                return self._gps_track_command(nav_state, own, dt)
            crs = vloc_course_deg + (180.0 if lat is Lat.REV else 0.0)
            return self._couple(dev, crs + magvar, own, dt, soft_ok=False, src=lat.value)

        return norm360(hdg)          # LVL / OFF / armed-not-captured: wings level

    # -- vertical -----------------------------------------
    def _maybe_capture_gs(self, gs_deflection, gs_valid) -> None:
        if self.armed_vert is Vert.GS and self.lateral is Lat.APR \
                and gs_valid and abs(gs_deflection) <= _CAPTURE_DEFLECTION:
            self.vertical, self.armed_vert = Vert.GS, None

    def _vertical_command(self, alt, gs_kt, gs_deflection):
        vert = self.vertical
        if vert is Vert.ALT:
            if self.alt_hold_ft is None:
                self.alt_hold_ft = round(alt / 10.0) * 10.0
            return self.alt_hold_ft, None, True
        if vert is Vert.VS:
            return None, self.vs_target, False
        if vert is Vert.GS:
            nominal = -gs_kt * 5.0
            corr = _clamp(gs_deflection * 400.0, -400.0, 400.0)
            return None, _clamp(nominal + corr, -1200.0, 200.0), False
        return None, None, False       # vertical axis off - pilot flies pitch
