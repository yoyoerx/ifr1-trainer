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
    RDY = "RDY"     # master on, NO roll mode engaged: the pilot is still flying (POH p.2-3)
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
# sec.3.1.3 / 4.1: GPSS limits the turn rate to 130% (Prog/Comp hardware mod code AM and below),
# 90% (AN, AP) or 110% (AR and above) of standard rate. The trainer models the newest, AR+.
_GPSS_RATE_FRAC = 1.10
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
# Glideslope (POH sec.3.2.1.1, software rev 5 and above, p.3-12): the GS annunciation arms once, for one
# second, NAV APR + ALT are engaged, no NAV/GS flag, LOC frequency selected, within 50% of the localizer
# and MORE than 10% GDI below the glideslope; the mode engages at 5% GDI below centreline. The GS
# annunciation flashes when the GDI exceeds 50% or the GS flag is in view. (Rev 4 and below: 10 s, 60%.)
_GS_ARM_S = 1.0
_GS_ARM_LOC_DEV = 0.50
_GS_ARM_MIN_BELOW = 0.10
_GS_CAPTURE_DEFL = 0.05
_GS_FPM_PER_KT = 5.31           # 3.00 deg: tan(3 deg) x 6076 ft/nm / 60 min
_FLASH_DEV = 0.50               # NAV / GS annunciation flashes beyond 50% deflection (p.3-5, p.3-13)
_VS_KNOB_STEP = 100.0
# POH sec.3.1.4 / 3.1.5 / 4.2 (pp.3-8, 4-3): the modifier knob moves the held altitude 20 ft per detent,
# +/-360 ft from the captured altitude, and the held vertical speed 100 fpm per detent, +/-1600 fpm from
# the captured rate; 1600 fpm is the absolute limit. In a climb the VS annunciation flashes when the
# aircraft cannot hold the rate for 15 s. TRIM UP/DN appears after 3 s of servo loading and flashes 4 s
# later (sec.3.1.7.1). After a disconnect RDY flashes for 5 s (sec.3.7, pre-flight test step 50).
_ALT_KNOB_FT = 20.0
_ALT_KNOB_RANGE_FT = 360.0
_VS_MAX_FPM = 1600.0
_VS_LAG_S = 15.0
_VS_LAG_FPM = 200.0             # "unable to hold" tolerance - the POH gives none (trainer's choice)
_TRIM_ANNUNCIATE_S = 3.0
_TRIM_FLASH_AFTER_S = 4.0
_TRIM_LOAD_FPM = 200.0          # commanded rate that loads the pitch servo enough to need trim (trainer's proxy)
_DISC_RDY_FLASH_S = 5.0

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
    # glideslope arming (see `_track_glideslope`)
    gs_disabled: bool = False         # the pilot disarmed it with APR (the GS annunciation flashes)
    _gs_arm_t: float = 0.0
    _gs_defl: float = 0.0
    _gs_ok: bool = False              # NAV APR could couple a glideslope right now
    _gs_angle: float = 3.0            # the path angle being flown (feed-forward descent rate)
    _flash_nav: bool = False
    _fail: bool = False
    _flash_gpss: bool = False
    _gs_needle_flash: bool = False
    # pitch-axis bookkeeping
    _alt_captured: float | None = None    # altitude captured on ALT engage (the knob's +/-360 ft datum)
    _vs_captured: float = 0.0             # vertical speed captured on VS engage (the +/-1600 fpm datum)
    _own_vs: float = 0.0
    _vs_lag_t: float = 0.0
    _trim_t: float = 0.0
    _trim_dir: int = 0
    trim_flash: bool = False
    _rdy_flash_t: float = 0.0

    # -- master (yoke AP/disconnect) --------------------------------
    def press_ap(self) -> None:
        """The IFR-1 has a single AP key, so it stands in for both of the POH's controls: with a
        roll mode engaged it is the yoke AP DISC switch (back to a flashing RDY, sec.3.7); from RDY it
        is the master switch going off; from off it switches the unit on (RDY, p.2-3)."""
        if self.roll_engaged:
            self.disconnect()
        elif self.engaged:
            self.disengage()
        else:
            self.engage()

    def disconnect(self) -> None:
        """AP DISC: every mode drops; RDY flashes for 5 s (pre-flight test step 50). The POH's audible
        tone is not modelled."""
        self._release_roll()
        self._reset_coupler()
        self._rdy_flash_t = _DISC_RDY_FLASH_S

    def engage(self) -> None:
        self.engaged = True
        if self.lateral is Lat.OFF:
            self.lateral = Lat.RDY

    @property
    def roll_engaged(self) -> bool:
        """A roll mode (HDG, NAV, NAV APR, REV, NAV GPSS) is engaged - the only state in which the
        autopilot steers, and the precondition for a pitch mode (POH sec.3.1.4, 3.1.5, 4.2)."""
        return self.engaged and self.lateral not in (Lat.OFF, Lat.RDY)

    def _release_roll(self) -> None:
        """Back to RDY. A pitch mode cannot outlive its roll mode."""
        self.lateral, self.armed_lat, self.gpss = Lat.RDY, None, False
        self.vertical, self.armed_vert, self.alt_hold_ft, self.trim = Vert.OFF, None, None, 0
        self.gs_disabled, self._gs_arm_t = False, 0.0

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
        self._rdy_flash_t = 0.0

    # -- programmer buttons ---------------------------------------
    # NAV/APR/REV engage - and start actively intercepting - the moment
    # they're pressed, exactly like the real S-TEC 55X: "the turn will
    # always begin between 100% (full-scale) needle deflection and 20% of
    # full-scale" (POH sec.4.2.2) - there is no wings-level dead zone while
    # "armed" waiting for the needle to already be centered. `armed_lat`
    # still tracks not-yet-captured (needle not yet alive/centered) purely
    # for the annunciator - `_lateral_command` steers on `self.lateral`
    # itself throughout, armed or captured.
    # Pressing the button of the mode that is already engaged releases it (back to RDY). The POH
    # does not say what a repeat press does; this is the trainer's choice (see the review file).
    def press_hdg(self) -> None:
        self.engage()
        if self.lateral is Lat.HDG:
            self._release_roll()
            return
        self.lateral, self.armed_lat, self.gpss = Lat.HDG, None, False

    def press_nav(self) -> None:
        self.engage()
        if self.lateral is Lat.NAV:
            # POH sec.3.1.3: "Press the NAV mode selector switch twice to engage the [GPSS] mode,
            # unless the navigation mode is already engaged. In the latter event, only press the
            # NAV mode selector switch once." Nothing in the POH says what a further press does;
            # the trainer toggles GPSS back off (NAV stays engaged).
            self.gpss = not self.gpss
            self._reset_coupler()
            return
        self.lateral = Lat.NAV
        self.armed_lat = Lat.NAV
        self.armed_vert = None
        self.gpss = False
        self._reset_coupler()

    def press_apr(self) -> None:
        self.engage()
        if self.lateral is Lat.APR:
            # POH p.3-12: "The armed glideslope mode can be subsequently disabled by pressing the APR
            # mode selector switch. The GS annunciation will flash to acknowledge this. To then re-arm
            # the glideslope mode, press the APR mode selector switch again."
            if self.armed_vert is Vert.GS:
                self.armed_vert, self.gs_disabled, self._gs_arm_t = None, True, 0.0
                return
            if self.gs_disabled:
                self.gs_disabled, self._gs_arm_t = False, 0.0
                return
            self._release_roll()            # (trainer's choice: the POH is silent on a plain repeat press)
            return
        # POH p.3-5: "While tracking in the SOFT condition and within 50% CDI needle deflection, should it be
        # desired to track in the higher authority CAP SOFT condition instead, press the APR mode selector switch
        # to engage the navigation approach (NAV APR) mode": the captured course is kept, only the authority
        # rises - the intercept does not start over.
        carry = (self.lateral is Lat.NAV and not self.gpss and self.stage != "INTERCEPT"
                 and self._dev_prev is not None and abs(self._dev_prev) <= _FLASH_DEV
                 and self._src.startswith("NAV/"))
        self.lateral = Lat.APR
        self.armed_lat = Lat.APR
        self.armed_vert = None              # GS arms itself when the conditions hold (`_track_glideslope`)
        self.gs_disabled, self._gs_arm_t = False, 0.0
        self.gpss = False                   # NAV APR replaces NAV GPSS (POH sec.3.2.2)
        if carry:
            self._src = "APR/" + self._src[4:]
            if self.stage == "SOFT":
                self.stage = "CAP SOFT"
        else:
            self._reset_coupler()

    def press_rev(self) -> None:
        self.engage()
        if self.lateral is Lat.REV:
            self._release_roll()
            return
        self.lateral = Lat.REV
        self.armed_lat = Lat.REV
        self.gpss = False
        self._reset_coupler()

    # ALT / VS "can only be engaged if a roll mode (HDG, NAV, NAV APR, REV, REV APR, NAV GPSS) is
    # already engaged" (POH sec.3.1.4, 3.1.5; sec.4.2). Before that the press is ignored, and it
    # does not switch the autopilot on.
    def press_alt(self) -> None:
        if not self.roll_engaged:
            return
        if self.lateral is Lat.APR and self.vertical is Vert.ALT and self._gs_ok:
            # POH p.3-12 note: "If the approach positions the aircraft slightly above the GS centerline,
            # then manual engagement of the glideslope mode can be instantly achieved by pressing the ALT
            # mode selector switch." (Caution: not if more than 20% above the centreline - it moves
            # aggressively toward it.)
            self.vertical, self.armed_vert = Vert.GS, None
            return
        self.vertical = Vert.ALT
        self.armed_vert = None
        self.alt_hold_ft = None          # grab present altitude next update
        self._alt_captured = None

    def press_vs(self) -> None:
        if not self.roll_engaged:
            return
        # POH sec.3.1.5: "The autopilot will hold the aircraft at its current (captured) vertical speed."
        self.vs_target = _clamp(round(self._own_vs / 100.0) * 100.0, -_VS_MAX_FPM, _VS_MAX_FPM)
        self._vs_captured = self.vs_target
        self._vs_lag_t = 0.0
        self.vertical = Vert.VS

    def toggle_gpss(self) -> None:
        self.gpss = not self.gpss

    # -- knobs ---------------------------------------------------
    def turn_vs_knob(self, detents: int) -> None:
        """The modifier knob: 20 ft per detent (+/-360 ft) on ALT, 100 fpm per detent (+/-1600 fpm from the
        captured rate, 1600 fpm absolute) on VS."""
        if self.vertical is Vert.ALT and self.alt_hold_ft is not None:
            cap = self.alt_hold_ft if self._alt_captured is None else self._alt_captured
            self.alt_hold_ft = _clamp(self.alt_hold_ft + detents * _ALT_KNOB_FT,
                                      cap - _ALT_KNOB_RANGE_FT, cap + _ALT_KNOB_RANGE_FT)
        else:
            lo = max(-_VS_MAX_FPM, self._vs_captured - _VS_MAX_FPM)
            hi = min(_VS_MAX_FPM, self._vs_captured + _VS_MAX_FPM)
            self.vs_target = _clamp(self.vs_target + detents * _VS_KNOB_STEP, lo, hi)

    def set_heading_bug(self, deg: float) -> None:
        self.heading_bug = norm360(deg)

    def turn_heading_bug(self, delta: float) -> None:
        self.heading_bug = norm360(self.heading_bug + delta)

    def set_preselect(self, ft: float) -> None:
        self.alt_preselect = float(ft)

    def set_vs_target(self, fpm: float) -> None:
        self.vs_target = _clamp(float(fpm), -_VS_MAX_FPM, _VS_MAX_FPM)
        self._vs_captured = self.vs_target

    # -- annunciator text --------------------------------------
    @property
    def ready(self) -> bool:
        """The RDY annunciation state: master on, no roll mode yet."""
        return self.engaged and self.lateral is Lat.RDY

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
        gps_course_deg: float | None = None,
        vloc_is_loc: bool = True,
        gs_angle_deg: float = 3.0,
    ) -> Commands:
        self._rdy_flash_t = max(0.0, self._rdy_flash_t - dt) if self.engaged else 0.0
        self._own_vs = getattr(own, "vs_fpm", 0.0)
        self._gs_angle = gs_angle_deg
        if not self.roll_engaged:            # off, or RDY: nothing steers and no pitch mode can run
            self.trim = 0
            self._trim_t, self._trim_dir, self.trim_flash = 0.0, 0, False
            return Commands()

        alt = getattr(own, "altitude_ft", 0.0)
        gs_kt = max(getattr(own, "gs_kt", 0.0), 1.0)

        self._maybe_capture_lateral(nav_state, vloc_deflection, vloc_valid)
        self._track_glideslope(nav_state, dt, vloc_deflection, vloc_valid, vloc_is_loc,
                               gs_deflection, gs_valid)
        self._track_flashes(nav_state, vloc_deflection, vloc_valid)

        self._rate_frac = None
        cmd_heading = self._lateral_command(nav_state, own, magvar, dt,
                                            vloc_course_deg, vloc_deflection, vloc_valid,
                                            gps_course_deg)
        cmd_alt, cmd_vs, clear_vs = self._vertical_command(alt, gs_kt, gs_deflection)

        if self.vertical is Vert.VS:
            band = max(60.0, abs(getattr(own, "vs_fpm", 0.0)) / 9.0)
            if abs(alt - self.alt_preselect) <= band:
                self.vertical = Vert.ALT
                self.alt_hold_ft = self.alt_preselect
                self._alt_captured = self.alt_preselect
                cmd_alt, cmd_vs, clear_vs = self.alt_preselect, None, True

        self._track_trim(cmd_vs, dt)
        self._track_vs_lag(dt)
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
        if self.armed_lat in (Lat.NAV, Lat.APR) and cdi_source != "VLOC":
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
        self._rate_frac = _GPSS_RATE_FRAC
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
                         vloc_course_deg, vloc_deflection, vloc_valid,
                         gps_course_deg=None) -> float:
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
                # POH sec.3.1.2: NAV flies the course *selected on the HSI* against the CDI needle.
                # The needle is the GPS's deviation from its own leg; the course is the pointer, so a
                # pointer left off the DTK leaves the aircraft parked off the leg. `None` = the pointer
                # is slaved to the DTK (the trainer's `--ap-course auto`).
                course = nav_state.dtk if gps_course_deg is None else norm360(gps_course_deg + magvar)
                return self._couple(-xtk / scale, course, own, dt, soft_ok=True, src="NAV/GPS")

        if lat in (Lat.APR, Lat.REV):
            cdi_source = getattr(nav_state, "cdi_source", "GPS") if nav_state is not None else "GPS"
            if cdi_source == "VLOC":
                if vloc_valid and vloc_course_deg is not None:
                    dev = (vloc_deflection or 0.0) * (-1.0 if lat is Lat.REV else 1.0)
                    crs = vloc_course_deg + (180.0 if lat is Lat.REV else 0.0)
                    return self._couple(dev, crs + magvar, own, dt, soft_ok=False, src=lat.value + "/VLOC")
            elif lat is Lat.APR and nav_state is not None and getattr(nav_state, "dtk", None) is not None:
                # NAV APR on a GPS source (a GPS approach, POH sec.3.5.1): the same coupler, the GPS
                # needle against the HSI pointer - APR is the higher-authority tracking, never SOFT
                scale = getattr(nav_state, "cdi_scale_nm", None) or _DEFAULT_CDI_SCALE_NM
                xtk = getattr(nav_state, "xtk_nm", 0.0) or 0.0
                course = nav_state.dtk if gps_course_deg is None else norm360(gps_course_deg + magvar)
                return self._couple(-xtk / scale, course, own, dt, soft_ok=False, src="APR/GPS")

        return norm360(hdg)          # RDY / OFF / no valid needle: no steering (holds heading)

    # -- vertical -----------------------------------------
    def _track_glideslope(self, nav_state, dt, vloc_deflection, vloc_valid, vloc_is_loc,
                          gs_deflection, gs_valid) -> None:
        """Glideslope auto-arm and capture (POH sec.3.2.1.1). `gs_deflection` is + = fly up, so the
        aircraft is *below* the beam while it is positive."""
        cdi_source = getattr(nav_state, "cdi_source", "GPS") if nav_state is not None else "GPS"
        loc_ok = (cdi_source == "VLOC" and vloc_valid and vloc_is_loc and vloc_deflection is not None)
        loc_dev = vloc_deflection
        if cdi_source == "GPS" and gs_valid and nav_state is not None:
            # a WAAS GPS glidepath (POH sec.3.5.1): the lateral needle is the GPS deviation on its own scale
            scale = getattr(nav_state, "cdi_scale_nm", None) or _DEFAULT_CDI_SCALE_NM
            xtk = getattr(nav_state, "xtk_nm", None)
            loc_ok = getattr(nav_state, "dtk", None) is not None and xtk is not None
            loc_dev = (xtk or 0.0) / scale
        self._gs_defl = gs_deflection
        self._gs_ok = bool(loc_ok and gs_valid)
        apr = self.lateral is Lat.APR
        if self.armed_vert is Vert.GS and not (apr and self.vertical is Vert.ALT):
            self.armed_vert = None                  # arming needs NAV APR + ALT engaged
        if apr and self.vertical is Vert.ALT and self.armed_vert is None and not self.gs_disabled:
            ready = (self._gs_ok and abs(loc_dev) <= _GS_ARM_LOC_DEV
                     and gs_deflection > _GS_ARM_MIN_BELOW)
            self._gs_arm_t = self._gs_arm_t + dt if ready else 0.0
            if self._gs_arm_t >= _GS_ARM_S:
                self.armed_vert, self._gs_arm_t = Vert.GS, 0.0
        if self.armed_vert is Vert.GS and apr and self._gs_ok and gs_deflection <= _GS_CAPTURE_DEFL:
            self.vertical, self.armed_vert = Vert.GS, None      # the ALT annunciation goes out

    def _track_flashes(self, nav_state, vloc_deflection, vloc_valid) -> None:
        """Annunciations that flash: NAV / APR / REV at >50% needle deflection or a flag (POH p.3-5,
        p.3-13), GS at >50% GDI or its flag, NAV+GPSS with no course programmed (p.3-7)."""
        cdi_source = getattr(nav_state, "cdi_source", "GPS") if nav_state is not None else "GPS"
        self._flash_nav = self._flash_gpss = self._fail = False
        if self.lateral in (Lat.NAV, Lat.APR, Lat.REV):
            if cdi_source == "VLOC":
                self._flash_nav = (not vloc_valid) or abs(vloc_deflection or 0.0) > _FLASH_DEV
                self._fail = not vloc_valid                   # NAV flag in view: "the FAIL annunciation will also appear"
            elif nav_state is not None:
                dtk = getattr(nav_state, "dtk", None)
                scale = getattr(nav_state, "cdi_scale_nm", None) or _DEFAULT_CDI_SCALE_NM
                xtk = getattr(nav_state, "xtk_nm", None)
                self._flash_nav = dtk is None or (xtk is not None and abs(xtk) / scale > _FLASH_DEV)
                self._flash_gpss = self.gpss and self.lateral is Lat.NAV and dtk is None
                self._fail = dtk is None                      # no course programmed (p.3-7 / Cirrus POH 3.1.3)
        if self.vertical is Vert.GS or self.armed_vert is Vert.GS:
            self._gs_needle_flash = (not self._gs_ok) or abs(self._gs_defl) > _FLASH_DEV
        else:
            self._gs_needle_flash = False

    def _track_trim(self, cmd_vs, dt) -> None:
        """TRIM UP/DN (POH sec.3.1.7): the pitch servo loads while a vertical rate is being held; after 3 s the
        annunciation appears, and flashes 4 s later. The trainer has no servo, so the commanded rate stands
        in for the loading."""
        direction = 1 if (cmd_vs or 0) > _TRIM_LOAD_FPM else -1 if (cmd_vs or 0) < -_TRIM_LOAD_FPM else 0
        if direction != self._trim_dir:
            self._trim_dir, self._trim_t = direction, (dt if direction else 0.0)
        elif direction:
            self._trim_t += dt
        self.trim = direction if (direction and self._trim_t >= _TRIM_ANNUNCIATE_S) else 0
        self.trim_flash = bool(self.trim) and self._trim_t >= _TRIM_ANNUNCIATE_S + _TRIM_FLASH_AFTER_S

    def _track_vs_lag(self, dt) -> None:
        """POH sec.3.1.5: 'During a climb, should the aircraft become unable to hold the captured vertical
        speed for a period of fifteen seconds, the VS annunciation will flash'."""
        lagging = (self.vertical is Vert.VS and self.vs_target > _VS_LAG_FPM
                   and self._own_vs < self.vs_target - _VS_LAG_FPM)
        self._vs_lag_t = self._vs_lag_t + dt if lagging else 0.0

    @property
    def fail(self) -> bool:
        """The FAIL annunciation: a NAV flag in view, or NAV GPSS with no course programmed (POH p.3-5, p.3-7)."""
        return self.engaged and self._fail

    @property
    def flashing(self) -> frozenset:
        """Annunciations that should blink right now."""
        out = set()
        if self._flash_nav and self.lateral in (Lat.NAV, Lat.APR, Lat.REV):
            out.add(self.lateral.value)
        if self._flash_gpss:
            out.add("GPSS")
        if self.gs_disabled or self._gs_needle_flash:
            out.add("GS")
        if self._vs_lag_t >= _VS_LAG_S:
            out.add("VS")
        if self.trim_flash:
            out.add("TRIM")
        if self._rdy_flash_t > 0.0 and self.lateral is Lat.RDY:
            out.add("RDY")
        return frozenset(out)

    def _vertical_command(self, alt, gs_kt, gs_deflection):
        vert = self.vertical
        if vert is Vert.ALT:
            if self.alt_hold_ft is None:
                self.alt_hold_ft = round(alt / 10.0) * 10.0
                self._alt_captured = self.alt_hold_ft
            return self.alt_hold_ft, None, True
        if vert is Vert.VS:
            return None, self.vs_target, False
        if vert is Vert.GS:
            # the descent rate that holds this path's angle at this groundspeed (3.00 deg: 5.31 fpm/kt)
            nominal = -gs_kt * _GS_FPM_PER_KT * math.tan(math.radians(self._gs_angle)) / math.tan(math.radians(3.0))
            corr = _clamp(gs_deflection * 400.0, -400.0, 400.0)
            return None, _clamp(nominal + corr, -1200.0, 200.0), False
        return None, None, False       # vertical axis off - pilot flies pitch
