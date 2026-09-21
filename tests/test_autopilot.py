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
    for _ in range(1800):                # ample time for the (slow, POH-staged) trim to wind in
        cmd = ap.update(Nav(dtk=180.0, xtk=xtk), Own(), 0.0, dt=1.0)
        intercept = angle_diff(cmd.heading, 180.0)   # + = cutting toward the right
        # toy plant: flying right of track (+intercept) drifts xtk further
        # right over time; the simulated crosswind adds a constant push
        xtk += intercept * 0.02 + drift_per_s
    # 0.1 nm/s is a ~360 kt crosswind; the small residual trim (3 deg max - the measured
    # track-vs-heading drift does the real work) holds the offset well inside one dot
    assert abs(xtk) < 0.6 and abs(ap._xtk_i) > 2.5


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
    assert cmd.heading == pytest.approx(90.0 + 45.0)     # full-scale needle: POH sec.3.1.2 45 deg cut


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


def test_gps_leg_full_scale_offset_is_a_45_degree_cut():
    """S-TEC 55X POH (4th Ed.) sec.3.1.2: at full-scale CDI deflection 'the autopilot
    will establish the aircraft on a 45 degree intercept angle relative to the
    selected course' - a cut to the course, NOT a heading toward the next fix."""
    for gpss in (False, True):                          # sec.3.1.3.1: GPSS intercepts the same way
        ap = Autopilot(); ap.engage(); ap.lateral = Lat.NAV; ap.gpss = gpss
        right = ap.update(Nav(dtk=360.0, xtk=5.0), Own(), 0.0).heading    # 5 nm right = full scale
        assert right == pytest.approx(315.0)
        ap2 = Autopilot(); ap2.engage(); ap2.lateral = Lat.NAV; ap2.gpss = gpss
        assert ap2.update(Nav(dtk=360.0, xtk=-12.0), Own(), 0.0).heading == pytest.approx(45.0)


def test_intercept_angle_shrinks_to_zero_at_the_course_and_is_symmetric():
    from autopilot import nav_intercept_deg
    angles = [nav_intercept_deg(x, 5.0, 110.0) for x in (5.0, 2.0, 1.0, 0.5, 0.1, 0.0)]
    assert angles[0] == pytest.approx(-45.0) and angles[-1] == 0.0
    assert angles == sorted(angles)                     # -45 ... 0, monotonic
    assert nav_intercept_deg(-0.7, 5.0, 110.0) == pytest.approx(-nav_intercept_deg(0.7, 5.0, 110.0))


def test_turn_in_starts_between_20_and_100_percent_of_full_scale():
    """POH sec.3.1.2: 'the turn will always begin between 100% and 20% CDI needle deflection'."""
    from autopilot import turn_in_distance_nm
    for scale in (5.0, 1.0, 0.3):
        for gs in (60.0, 110.0, 250.0, 450.0):
            d = turn_in_distance_nm(gs, scale)
            assert 0.20 * scale - 1e-9 <= d <= scale + 1e-9
    assert turn_in_distance_nm(300.0, 5.0) >= turn_in_distance_nm(80.0, 5.0)   # faster -> earlier
    assert turn_in_distance_nm(110.0, 5.0) == pytest.approx(1.0)                # 20% floor at 5 nm scale


def test_closed_loop_intercept_rolls_onto_the_leg_without_overshoot():
    """Off a long leg: hold 45 deg to the course, then roll onto it - the heading stays
    45 deg from the course until the turn-in point (it does not curve toward the fix)."""
    import math
    ap = Autopilot(); ap.engage(); ap.lateral = Lat.NAV
    x, along, hdg, gs, dt = 6.0, 0.0, 360.0, 110.0, 0.5    # course 360; x = nm right of it
    fix_along, max_left, hdgs = 40.0, 0.0, []
    for _ in range(1200):
        cmd = ap.update(Nav(dtk=360.0, xtk=x), Own(heading=hdg, gs=gs), 0.0, dt=dt).heading
        err = (cmd - hdg + 180.0) % 360.0 - 180.0
        hdg = (hdg + max(-2.7 * dt, min(2.7 * dt, err))) % 360.0
        x += gs / 3600.0 * dt * math.sin(math.radians(hdg))
        along += gs / 3600.0 * dt * math.cos(math.radians(hdg))
        max_left = max(max_left, -x)
        hdgs.append((x, (hdg + 180.0) % 360.0 - 180.0))
        if along > fix_along:
            break
    assert max_left < 0.25                                   # crossed the course by well under 0.25 nm
    assert abs(x) < 0.05                                     # and is on it before the fix
    far = [h for xx, h in hdgs if xx > 1.5]
    assert far and all(-46.0 < h < -40.0 for h in far[40:])  # ~45 deg cut while far off (after the initial turn)


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
    # forward course ~090, reverse course ~270; intercepts go opposite ways (a 45 deg cut, POH 3.1.2)
    assert ((hf - 90) + 180) % 360 - 180 == pytest.approx(45.0)
    assert ((hr - 270) + 180) % 360 - 180 == pytest.approx(-45.0)


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
    """F38 (KLNS ILS 08 playtest): a steady needle offset (crosswind drift) must eventually
    get a stronger correction, or the AP parks off-centre. With the POH timeline the trim only
    learns from 30 s after capture (sec.3.1.2: the crosswind correction is established then),
    once the needle has stopped closing, and must be dt-based."""
    ap = Autopilot()
    ap.press_apr()
    kw = dict(vloc_course_deg=90.0, vloc_deflection=0.10, vloc_valid=True)
    early = ap.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0, **kw).heading
    for _ in range(90):
        cmd = ap.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0, **kw)
    assert cmd.heading > 90.0 + 30.0 * 0.10 + 0.5        # CAP SOFT gain x deflection, plus trim
    # a large (intercept-in-progress) deflection must not wind the trim up
    ap3 = Autopilot()
    ap3.press_apr()
    for _ in range(50):
        c3 = ap3.update(Nav(cdi_source="VLOC"), Own(heading=90.0), 0.0, dt=1.0,
                        vloc_course_deg=90.0, vloc_deflection=0.5, vloc_valid=True)
    assert c3.heading == pytest.approx(90.0 + 45.0) and ap3._xtk_i == 0.0
    assert ap3.stage == "INTERCEPT"


def test_nav_crabs_into_the_wind_from_track_vs_heading():
    """F44: NAV/GPSS/APR steer a desired *track*; the heading command must be
    that track minus the measured drift (track - heading), so a crosswind is
    crabbed out at once instead of being learned slowly by the integral trim
    (slow, wide oscillation about the line)."""
    ap = Autopilot()
    ap.press_nav()
    own = Own(heading=30.0)
    own.track_deg = 40.0                 # drifting 10 deg right of heading
    n = Nav(dtk=40.0, xtk=0.0, cdi_source="GPS")
    cmd = ap.update(n, own, 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(40.0)      # POH 3.1.2: no crosswind correction until 30 s after capture
    for _ in range(320):
        cmd = ap.update(n, own, 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(30.0)      # DTK 40 - drift 10
    own.track_deg = 20.0                 # 10 deg LEFT of heading (drift -10)
    cmd = ap.update(n, own, 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(50.0)      # DTK 40 + 10
    # no track available (or no drift): unchanged from before
    cmd = ap.update(n, Own(heading=30.0), 0.0, dt=0.1)
    assert cmd.heading == pytest.approx(40.0)


# --------------------------------------------------------------------------- #
# S-TEC 55X POH sec.3.1.2 coupler timeline: 45 deg cut, 15% capture, CAP -> CAP SOFT (+15 s)
# -> SOFT (+75 s, NAV only), turn-rate limits 90% / 45% / 15% of standard rate
# --------------------------------------------------------------------------- #
def _vloc(ap, dev, *, heading=90.0, dt=1.0, n=1, course=90.0):
    cmd = None
    for _ in range(n):
        cmd = ap.update(Nav(cdi_source="VLOC"), Own(heading=heading), 0.0, dt=dt,
                        vloc_course_deg=course, vloc_deflection=dev, vloc_valid=True)
    return cmd


def test_coupler_stages_and_turn_rate_limits_follow_the_pohs_timeline():
    ap = Autopilot(); ap.press_nav()
    c = _vloc(ap, 1.0)
    assert ap.stage == "INTERCEPT" and c.turn_rate_dps == pytest.approx(2.7)      # 90% of 3 deg/s
    c = _vloc(ap, 0.14)                                                            # inside 15%: captured
    assert ap.stage == "CAP" and c.turn_rate_dps == pytest.approx(2.7)
    c = _vloc(ap, 0.14, n=15)
    assert ap.stage == "CAP SOFT" and c.turn_rate_dps == pytest.approx(1.35)      # 45%
    c = _vloc(ap, 0.02, n=60)
    assert ap.stage == "SOFT" and c.turn_rate_dps == pytest.approx(0.45)          # 15%


def test_apr_tracks_in_cap_soft_never_soft():
    """POH p.3-5: APR is the way to track in the higher-authority CAP SOFT instead of SOFT."""
    ap = Autopilot(); ap.press_apr()
    _vloc(ap, 1.0)
    _vloc(ap, 0.05, n=200)
    assert ap.stage == "CAP SOFT"


def test_engaging_on_the_course_goes_straight_to_soft():
    ap = Autopilot(); ap.press_nav()
    c = _vloc(ap, 0.05, heading=92.0)          # <10% and heading within 5 deg of the course
    assert ap.stage == "SOFT" and c.turn_rate_dps == pytest.approx(0.45)


def test_a_new_course_10_degrees_off_reverts_to_cap():
    ap = Autopilot(); ap.press_nav()
    _vloc(ap, 0.05, heading=92.0)
    assert ap.stage == "SOFT"
    c = _vloc(ap, 0.05, heading=92.0, course=105.0)
    assert ap.stage == "CAP" and c.turn_rate_dps == pytest.approx(2.7)


def test_soft_falls_back_to_cap_soft_after_60_s_beyond_50_percent():
    ap = Autopilot(); ap.press_nav()
    _vloc(ap, 0.05, heading=92.0)
    _vloc(ap, 0.6, n=59)
    assert ap.stage == "SOFT"
    _vloc(ap, 0.6, n=3)
    assert ap.stage == "CAP SOFT"


def test_crosswind_correction_waits_30_s_after_capture():
    ap = Autopilot(); ap.press_nav()
    own = Own(heading=80.0); own.track_deg = 90.0            # 10 deg of right drift
    first = ap.update(Nav(cdi_source="VLOC"), own, 0.0, dt=1.0, vloc_course_deg=90.0,
                      vloc_deflection=0.5, vloc_valid=True).heading
    ap.update(Nav(cdi_source="VLOC"), own, 0.0, dt=1.0, vloc_course_deg=90.0,
              vloc_deflection=0.10, vloc_valid=True)          # captured
    early = ap.update(Nav(cdi_source="VLOC"), own, 0.0, dt=1.0, vloc_course_deg=90.0,
                      vloc_deflection=0.0, vloc_valid=True).heading
    for _ in range(31):
        late = ap.update(Nav(cdi_source="VLOC"), own, 0.0, dt=1.0, vloc_course_deg=90.0,
                         vloc_deflection=0.0, vloc_valid=True).heading
    assert early == pytest.approx(90.0) and late == pytest.approx(80.0)   # 30 s later: - drift


def test_hdg_and_wings_level_turn_rates():
    ap = Autopilot(); ap.press_hdg(); ap.set_heading_bug(120.0)
    assert ap.update(Nav(), Own(), 0.0).turn_rate_dps == pytest.approx(2.7)     # POH sec.4.1: 90%
    ap.press_hdg()                                                              # -> wings level
    assert ap.update(Nav(), Own(), 0.0).turn_rate_dps is None


def test_gpss_turn_rate_limit_is_110_percent_of_standard_rate():
    """POH sec.3.1.3 / 4.1: GPSS 130% / 90% / 110% by programmer hardware mod code; the
    trainer models the newest (AR and above) = 110%, vs 90% for NAV/APR/REV/HDG."""
    gp = Autopilot(); gp.press_nav(); gp.press_nav()
    assert gp.gpss
    c = gp.update(Nav(dtk=90.0, xtk=0.0), Own(), 0.0)
    assert c.turn_rate_dps == pytest.approx(3.3)
    nv = Autopilot(); nv.press_nav()
    assert nv.update(Nav(dtk=90.0, xtk=0.0), Own(heading=200.0), 0.0).turn_rate_dps == pytest.approx(2.7)
    ap = Autopilot(); ap.press_apr(); ap.gpss = True
    c = ap.update(Nav(dtk=90.0, xtk=0.0), Own(), 0.0, vloc_course_deg=90.0,
                  vloc_deflection=0.0, vloc_valid=True)
    assert c.turn_rate_dps == pytest.approx(3.3)         # GPSS APR flies the GPS course too


# --------------------------------------------------------------------------- #
# NAV flies the HSI course pointer, GPSS flies the GPS (POH sec.3.1.2 / 3.1.3)
# --------------------------------------------------------------------------- #
def _gps_nav_run(pointer_offset, gpss=False, n=1500):
    """Closed loop on a 360-degree leg (magvar 0): the needle is the GPS's own deviation."""
    import math
    ap = Autopilot(); ap.press_nav()
    if gpss:
        ap.press_nav()
    x, hdg, gs, dt = 0.0, 360.0, 110.0, 0.5             # x = nm right of the leg
    for _ in range(n):
        nav = Nav(dtk=360.0, xtk=x)
        ptr = None if pointer_offset is None else (360.0 + pointer_offset) % 360.0
        cmd = ap.update(nav, Own(heading=hdg, gs=gs), 0.0, dt=dt, gps_course_deg=ptr).heading
        err = (cmd - hdg + 180.0) % 360.0 - 180.0
        rate = ap._rate_frac and ap._rate_frac * 3.0 or 3.0
        hdg = (hdg + max(-rate * dt, min(rate * dt, err))) % 360.0
        x += gs / 3600.0 * dt * math.sin(math.radians(hdg))
    return x


def test_nav_on_gps_flies_the_selected_course_pointer():
    assert abs(_gps_nav_run(0.0)) < 0.05                         # pointer on the DTK: tracks the leg
    assert abs(_gps_nav_run(None)) < 0.05                        # slaved to the DTK: same thing
    # a pointer left 20 deg off parks the aircraft off the leg (5 nm CDI scale here)
    assert abs(_gps_nav_run(20.0)) > 1.0
    assert abs(_gps_nav_run(-20.0)) > 1.0


def test_gpss_ignores_the_course_pointer():
    """POH sec.3.1.3: 'the autopilot will not accept any course error input from the Course
    Pointer (HSI)'."""
    assert abs(_gps_nav_run(20.0, gpss=True)) < 0.05


def test_pointer_only_matters_to_nav_on_a_gps_leg():
    ap = Autopilot(); ap.press_nav()
    c = ap.update(Nav(dtk=90.0, xtk=0.0), Own(heading=200.0), 0.0, gps_course_deg=45.0)
    assert c.heading == pytest.approx(45.0)                      # pointer + 0 deflection
    ap2 = Autopilot(); ap2.press_nav()
    c2 = ap2.update(Nav(dtk=90.0, xtk=0.0), Own(heading=200.0), 0.0)
    assert c2.heading == pytest.approx(90.0)                     # no pointer given: the DTK
