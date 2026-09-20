"""Unit tests for autopilot.py - the S-TEC Fifty Five X style state machine."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from autopilot import Autopilot, Commands, Lat, Vert  # noqa: E402


class Own:
    def __init__(self, heading=90.0, altitude=5000.0, vs=0.0, gs=120.0):
        self.heading_deg = heading
        self.altitude_ft = altitude
        self.vs_fpm = vs
        self.gs_kt = gs


class Nav:
    def __init__(self, dtk=None, xtk=None, cdi_source="GPS"):
        self.dtk = dtk
        self.xtk_nm = xtk
        self.cdi_source = cdi_source


# --------------------------------------------------------------------------- #
# master + base modes
# --------------------------------------------------------------------------- #
def test_master_engages_wings_level_no_vertical():
    ap = Autopilot()
    ap.press_ap()
    assert ap.engaged and ap.lateral is Lat.LVL and ap.vertical is Vert.OFF


def test_disengaged_issues_no_commands():
    ap = Autopilot()
    assert ap.update(Nav(), Own(), 0.0) == Commands()


def test_wings_level_holds_present_heading():
    ap = Autopilot()
    ap.engage()
    assert ap.update(Nav(), Own(heading=123.0), 0.0).heading == pytest.approx(123.0)


def test_hdg_button_engages_and_flies_the_bug_true():
    ap = Autopilot()
    ap.press_hdg()                      # also auto-engages the roll axis
    assert ap.engaged and ap.lateral is Lat.HDG
    ap.set_heading_bug(90.0)
    assert ap.update(Nav(), Own(), magvar=-13.0).heading == pytest.approx(77.0)
    ap.press_hdg()
    assert ap.lateral is Lat.LVL


# --------------------------------------------------------------------------- #
# NAV / GPSS
# --------------------------------------------------------------------------- #
def test_nav_engages_and_intercepts_immediately():
    """S-TEC 55X POH sec.4.2.2: 'the turn will always begin between 100%
    (full-scale) needle deflection and 20% of full-scale' - NAV steers an
    active intercept from the moment it's pressed, not after waiting for
    the needle to already be centered (no wings-level dead zone)."""
    ap = Autopilot()
    ap.press_nav()
    assert ap.lateral is Lat.NAV and ap.armed_lat is Lat.NAV   # engaged, not yet captured
    cmd = ap.update(Nav(dtk=360.0, xtk=6.0), Own(heading=90.0), 0.0)
    assert cmd.heading != pytest.approx(90.0)                  # actively cutting toward course
    assert ap.armed_lat is Lat.NAV                              # needle still not alive
    ap.update(Nav(dtk=360.0, xtk=0.5), Own(), 0.0)
    assert ap.lateral is Lat.NAV and ap.armed_lat is None       # captured


def test_nav_trims_out_steady_crosswind_offset():
    """A pure proportional xtk loop settles at a nonzero cross-track offset
    under any steady crosswind (the intercept angle has to equal the wind
    correction angle to hold track, which only happens once xtk itself is
    offset by -WCA/gain) - the AP would visibly never settle onto the
    course. The integral trim should walk xtk back toward zero over time
    even against a constant simulated drift that keeps pushing it off."""
    from navmath import angle_diff
    ap = Autopilot()
    ap.press_nav()
    xtk = 0.0
    drift_per_s = 0.1                    # a stand-in constant crosswind drift
    for _ in range(900):                 # ample time for the trim to wind in
        cmd = ap.update(Nav(dtk=180.0, xtk=xtk), Own(), 0.0, dt=1.0)
        intercept = angle_diff(cmd.heading, 180.0)   # + = cutting toward the right
        # toy plant: flying right of track (+intercept) drifts xtk further
        # right over time; the simulated crosswind adds a constant push
        xtk += intercept * 0.02 + drift_per_s
    assert abs(xtk) < 0.3


def test_nav_tracks_vloc_once_cdi_source_switches():
    """FINDINGS: NAV couples to whatever the GNS CDI is actually showing
    (S-TEC 55X POH: NAV tracks the selected nav source) - once the pilot
    switches CDI source to VLOC (the SWAP/CDI key), NAV must fly the VOR/LOC
    needle, not keep silently steering off the GPS course underneath it."""
    ap = Autopilot()
    ap.press_nav()
    # still on GPS: flies the GPS course, ignores a live VLOC deflection
    cmd = ap.update(Nav(dtk=360.0, xtk=0.0, cdi_source="GPS"), Own(heading=90.0), 0.0,
                     vloc_course_deg=90.0, vloc_deflection=1.0, vloc_valid=True)
    assert cmd.heading == pytest.approx(0.0)          # dtk 360 normalizes to 0
    # CDI source switches to VLOC: NAV now tracks the VOR/LOC course+deflection,
    # not the (still centered) GPS xtk
    cmd = ap.update(Nav(dtk=360.0, xtk=0.0, cdi_source="VLOC"), Own(heading=90.0), 0.0,
                     vloc_course_deg=90.0, vloc_deflection=1.0, vloc_valid=True)
    assert cmd.heading != pytest.approx(360.0)
    assert cmd.heading == pytest.approx(90.0 + 1.0 * 22.0)     # _VLOC_GAIN


def test_nav_press_again_engages_gpss():
    """S-TEC 55X POH sec.4.2.5: 'To enter the GPSS Mode, push the NAV button
    twice... To delete the GPSS function, push the NAV button again' - NAV
    mode itself stays engaged through both presses."""
    ap = Autopilot()
    ap.press_nav()
    assert not ap.gpss
    ap.press_nav()
    assert ap.lateral is Lat.NAV and ap.gpss
    ap.press_nav()
    assert ap.lateral is Lat.NAV and not ap.gpss


def test_gpss_tightens_nav_tracking():
    soft = Autopilot(); soft.engage(); soft.lateral = Lat.NAV
    hard = Autopilot(); hard.engage(); hard.lateral = Lat.NAV; hard.gpss = True
    n = Nav(dtk=360.0, xtk=1.0)                        # 1 nm right of course
    h_soft = soft.update(n, Own(), 0.0).heading
    h_hard = hard.update(n, Own(), 0.0).heading
    # both cut left of 360; GPSS cuts harder
    assert (360 - h_hard) % 360 > (360 - h_soft) % 360


# --------------------------------------------------------------------------- #
# APR / REV / glideslope
# --------------------------------------------------------------------------- #
def test_apr_arms_lateral_and_gs():
    ap = Autopilot()
    ap.press_apr()
    assert ap.armed_lat is Lat.APR and ap.armed_vert is Vert.GS


def test_apr_captures_loc_then_gs_from_below():
    ap = Autopilot()
    ap.press_apr()
    ap.update(Nav(), Own(altitude=3000.0), 0.0,
              vloc_course_deg=40.0, vloc_deflection=0.2, vloc_valid=True,
              gs_deflection=0.95, gs_valid=True)
    assert ap.lateral is Lat.APR and ap.vertical is not Vert.GS
    cmd = ap.update(Nav(), Own(altitude=3000.0, gs=120.0), 0.0,
                    vloc_course_deg=40.0, vloc_deflection=0.1, vloc_valid=True,
                    gs_deflection=0.3, gs_valid=True)
    assert ap.vertical is Vert.GS and cmd.vs is not None and cmd.vs < 0


def test_apr_cancel_drops_gs():
    ap = Autopilot()
    ap.press_apr()
    ap.lateral, ap.vertical, ap.armed_lat = Lat.APR, Vert.GS, None
    ap.press_apr()
    assert ap.lateral is Lat.LVL and ap.vertical is Vert.OFF


def test_rev_reverses_localizer_sensing():
    fwd = Autopilot(); fwd.engage(); fwd.lateral = Lat.APR
    rev = Autopilot(); rev.engage(); rev.lateral = Lat.REV
    kw = dict(vloc_course_deg=90.0, vloc_deflection=0.4, vloc_valid=True)
    hf = fwd.update(Nav(), Own(), 0.0, **kw).heading
    hr = rev.update(Nav(), Own(), 0.0, **kw).heading
    # forward course ~090, reverse course ~270; intercepts go opposite ways
    assert abs(((hf - 90) + 180) % 360 - 180) < _MAX
    assert abs(((hr - 270) + 180) % 360 - 180) < _MAX


_MAX = 31.0


# --------------------------------------------------------------------------- #
# vertical: VS knob, ALT hold, preselect
# --------------------------------------------------------------------------- #
def test_vs_knob_sets_target_and_is_commanded():
    ap = Autopilot()
    ap.press_vs()
    ap.turn_vs_knob(-5)                                 # -500 fpm
    assert ap.vs_target == pytest.approx(-500.0)
    assert ap.update(Nav(), Own(altitude=6000.0), 0.0).vs == pytest.approx(-500.0)


def test_alt_hold_grabs_present_altitude_once():
    ap = Autopilot()
    ap.press_alt()
    c1 = ap.update(Nav(), Own(altitude=5432.0), 0.0)
    assert c1.altitude == pytest.approx(5430.0) and c1.clear_vs
    c2 = ap.update(Nav(), Own(altitude=5480.0), 0.0)
    assert c2.altitude == pytest.approx(5430.0)


def test_vs_knob_nudges_alt_hold_when_in_alt():
    ap = Autopilot()
    ap.press_alt()
    ap.update(Nav(), Own(altitude=4000.0), 0.0)         # captures 4000
    ap.turn_vs_knob(2)                                  # +200 ft
    assert ap.update(Nav(), Own(altitude=4000.0), 0.0).altitude == pytest.approx(4200.0)


def test_preselect_captures_out_of_vs():
    ap = Autopilot()
    ap.press_vs()
    ap.set_vs_target(700.0)
    ap.set_preselect(6000.0)
    ap.update(Nav(), Own(altitude=5000.0, vs=700.0), 0.0)
    assert ap.vertical is Vert.VS
    cmd = ap.update(Nav(), Own(altitude=5960.0, vs=700.0), 0.0)
    assert ap.vertical is Vert.ALT and cmd.altitude == pytest.approx(6000.0)


def test_trim_annunciator_follows_commanded_vs():
    ap = Autopilot()
    ap.press_vs()
    ap.set_preselect(20000.0)                           # keep the preselect out of the way
    ap.set_vs_target(600.0)
    ap.update(Nav(), Own(), 0.0)
    assert ap.trim == 1
    ap.set_vs_target(-600.0)
    ap.update(Nav(), Own(), 0.0)
    assert ap.trim == -1


def test_disengage_clears_everything():
    ap = Autopilot()
    ap.press_hdg()
    ap.press_alt()
    ap.disengage()
    assert not ap.engaged and ap.lateral is Lat.OFF and ap.vertical is Vert.OFF
    assert ap.armed_lat is None and ap.alt_hold_ft is None and ap.trim == 0


def test_led_bitmask_maps_modes_to_ap_row():
    ap = Autopilot()
    assert ap.led_bitmask() == 0                        # off
    ap.press_ap()
    assert ap.led_bitmask() == 0x01                     # AP only
    ap.press_hdg()
    assert ap.led_bitmask() == 0x01 | 0x02              # AP + HDG
    ap.press_apr()                                      # arms APR (+GS)
    assert ap.led_bitmask() & 0x08                      # APR lamp lit while armed
    ap.press_vs()
    assert ap.led_bitmask() & 0x20                      # VS lamp
    ap.disengage()
    assert ap.led_bitmask() == 0


def test_mode_line_smoke():
    ap = Autopilot()
    assert ap.mode_line() == "AP OFF"
    ap.press_nav()
    ap.gpss = True
    ap.lateral = Lat.NAV
    ap.armed_lat = None
    assert "NAV" in ap.mode_line() and "GPSS" in ap.mode_line()


def test_apr_builds_wind_drift_trim_for_a_steady_localizer_offset():
    """F38 (KLNS ILS 08 playtest): APR was proportional-only, so a steady
    needle offset (crosswind drift) never got a stronger correction - the AP
    parked off-centre. A captured, off-centre needle must integrate trim
    toward the course side, and the trim must be dt-based."""
    ap = Autopilot()
    ap.press_apr()
    base = ap.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0,
                     vloc_course_deg=90.0, vloc_deflection=0.15, vloc_valid=True).heading
    for _ in range(20):
        cmd = ap.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0,
                        vloc_course_deg=90.0, vloc_deflection=0.15, vloc_valid=True)
    assert cmd.heading > base                    # turning harder toward the needle
    # a large (intercept-in-progress) deflection must not wind the trim up
    ap3 = Autopilot()
    ap3.press_apr()
    for _ in range(50):
        c3 = ap3.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0,
                        vloc_course_deg=90.0, vloc_deflection=0.5, vloc_valid=True)
    assert c3.heading == pytest.approx(90.0 + 0.5 * 22.0)
    # a pegged (uncaptured) needle must not wind the trim up
    ap2 = Autopilot()
    ap2.press_apr()
    for _ in range(50):
        c2 = ap2.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0,
                        vloc_course_deg=90.0, vloc_deflection=1.0, vloc_valid=True)
    assert c2.heading == pytest.approx(90.0 + 22.0)


def test_nav_crabs_into_the_wind_from_track_vs_heading():
    """F44: NAV/GPSS/APR steer a desired *track*; the heading command must be
    that track minus the measured drift (track - heading), so a crosswind is
    crabbed out at once instead of being learned slowly by the integral trim
    (slow, wide oscillation about the line)."""
    ap = Autopilot()
    ap.press_nav()
    own = Own(heading=30.0)
    own.track_deg = 40.0                 # drifting 10 deg right of heading
    cmd = ap.update(Nav(dtk=40.0, xtk=0.0, cdi_source="GPS"), own, 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(30.0)      # DTK 40 - drift 10
    own.track_deg = 20.0                 # drifting left instead
    cmd = ap.update(Nav(dtk=40.0, xtk=0.0, cdi_source="GPS"), own, 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(60.0)      # DTK 40 + 20
    # no track available (or no drift): unchanged from before
    cmd = ap.update(Nav(dtk=40.0, xtk=0.0, cdi_source="GPS"), Own(heading=30.0), 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(40.0)
