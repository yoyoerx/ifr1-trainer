"""autopilot.py - an S-TEC Fifty Five X style autopilot.

The S-TEC 55X is **rate based** (it steers through the turn coordinator, not an
attitude reference), so the base engaged roll state is simply "wings level"
(`Lat.LVL`) and the pilot layers a mode on top with the programmer buttons:

    HDG   NAV   APR   REV   ALT   VS        + a VS/ALT knob and TRIM arrows

* NAV / APR *arm* and then *capture* when the course needle comes alive.
* **GPSS** (GPS roll steering) is a modifier on NAV/APR: when on, the AP flies
  the GPS's digital steering command instead of chasing the analog CDI.
* **REV** is APR with reversed localizer sensing (back course) and no glideslope.
* Vertical axis: **ALT** holds the present altitude, **VS** holds the knob-set
  vertical speed; **GS** captures from APR on an ILS. An altitude-selector
  (preselect) capture out of VS is modelled as an installed option.

`update(...)` returns a :class:`Commands` bundle for ``SimModel.command(...)``.
Pure - only primitives cross the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from navmath import norm360

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


_NAV_GAIN = 8.0        # deg intercept / nm XTK  (NAV, analog CDI)
_APR_GAIN = 13.0       # deg intercept / nm XTK  (APR, tighter)
_GPSS_GAIN = 12.0      # deg / nm  (digital roll steering, crisp)
_VLOC_GAIN = 22.0      # deg / unit-deflection   (localizer)
_MAX_INTERCEPT = 30.0
_CAPTURE_XTK_NM = 1.2
_CAPTURE_DEFLECTION = 0.75
_VS_KNOB_STEP = 100.0


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


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

    # -- master (yoke AP/disconnect) --------------------------------
    def press_ap(self) -> None:
        self.disengage() if self.engaged else self.engage()

    def engage(self) -> None:
        self.engaged = True
        if self.lateral is Lat.OFF:
            self.lateral = Lat.LVL

    def disengage(self) -> None:
        self.engaged = False
        self.lateral = Lat.OFF
        self.vertical = Vert.OFF
        self.armed_lat = self.armed_vert = None
        self.alt_hold_ft = None
        self.trim = 0

    # -- programmer buttons ---------------------------------------
    def press_hdg(self) -> None:
        self.engage()
        self.lateral = Lat.LVL if self.lateral is Lat.HDG else Lat.HDG
        self.armed_lat = None

    def press_nav(self) -> None:
        self.engage()
        if self.lateral is Lat.NAV or self.armed_lat is Lat.NAV:
            self.lateral, self.armed_lat = Lat.LVL, None
        else:
            self.armed_lat, self.armed_vert = Lat.NAV, None

    def press_apr(self) -> None:
        self.engage()
        if self.lateral is Lat.APR or self.armed_lat is Lat.APR:
            self.armed_lat = self.armed_vert = None
            self.lateral = Lat.LVL
            if self.vertical is Vert.GS:
                self.vertical = Vert.OFF
        else:
            self.armed_lat, self.armed_vert = Lat.APR, Vert.GS

    def press_rev(self) -> None:
        self.engage()
        if self.lateral is Lat.REV or self.armed_lat is Lat.REV:
            self.lateral, self.armed_lat = Lat.LVL, None
        else:
            self.armed_lat, self.armed_vert = Lat.REV, None

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
        if self.armed_lat:
            lat += f"/{self.armed_lat.value}→"
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

        cmd_heading = self._lateral_command(nav_state, own, magvar,
                                            vloc_course_deg, vloc_deflection, vloc_valid)
        cmd_alt, cmd_vs, clear_vs = self._vertical_command(alt, gs_kt, gs_deflection)

        if self.vertical is Vert.VS:
            band = max(60.0, abs(getattr(own, "vs_fpm", 0.0)) / 9.0)
            if abs(alt - self.alt_preselect) <= band:
                self.vertical = Vert.ALT
                self.alt_hold_ft = self.alt_preselect
                cmd_alt, cmd_vs, clear_vs = self.alt_preselect, None, True

        self.trim = 1 if (cmd_vs or 0) > 200 else -1 if (cmd_vs or 0) < -200 else 0
        return Commands(heading=cmd_heading, altitude=cmd_alt, vs=cmd_vs, clear_vs=clear_vs)

    # -- lateral --------------------------------------------
    def _maybe_capture_lateral(self, nav_state, vloc_deflection, vloc_valid) -> None:
        if self.armed_lat not in (Lat.NAV, Lat.APR, Lat.REV):
            return
        if self.armed_lat is Lat.NAV:
            xtk = getattr(nav_state, "xtk_nm", None) if nav_state else None
            live = xtk is not None and abs(xtk) <= _CAPTURE_XTK_NM
        else:  # APR / REV track the localizer
            live = (vloc_valid and vloc_deflection is not None
                    and abs(vloc_deflection) <= _CAPTURE_DEFLECTION)
        if live:
            self.lateral, self.armed_lat = self.armed_lat, None

    def _lateral_command(self, nav_state, own, magvar,
                         vloc_course_deg, vloc_deflection, vloc_valid) -> float:
        lat = self.lateral
        hdg = getattr(own, "heading_deg", 0.0)

        if lat is Lat.HDG:
            return norm360(self.heading_bug + magvar)

        if lat is Lat.NAV and nav_state is not None and getattr(nav_state, "dtk", None) is not None:
            xtk = getattr(nav_state, "xtk_nm", 0.0) or 0.0
            gain = _GPSS_GAIN if self.gpss else _NAV_GAIN
            intercept = _clamp(-xtk * gain, -_MAX_INTERCEPT, _MAX_INTERCEPT)
            return norm360(nav_state.dtk + intercept)

        if lat in (Lat.APR, Lat.REV) and vloc_valid and vloc_course_deg is not None:
            dev = (vloc_deflection or 0.0) * (-1.0 if lat is Lat.REV else 1.0)
            if self.gpss and lat is Lat.APR and nav_state is not None \
                    and getattr(nav_state, "dtk", None) is not None:
                xtk = getattr(nav_state, "xtk_nm", 0.0) or 0.0
                return norm360(nav_state.dtk + _clamp(-xtk * _GPSS_GAIN,
                                                     -_MAX_INTERCEPT, _MAX_INTERCEPT))
            intercept = _clamp(dev * _VLOC_GAIN, -_MAX_INTERCEPT, _MAX_INTERCEPT)
            crs = vloc_course_deg + (180.0 if lat is Lat.REV else 0.0)
            return norm360(crs + magvar + intercept)

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
