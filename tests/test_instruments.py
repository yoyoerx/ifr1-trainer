"""Unit tests for instruments.py - CDI / VOR / DME / marker math + the assembler.

Geometry is hand-placed: a station at (40, -74), a north (360 deg) selected
course, so "right of course" == east and the signs are easy to check.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point, destination  # noqa: E402
from gns530 import NavState  # noqa: E402
from instruments import (  # noqa: E402
    LOC_FULL_SCALE_DEG,
    Ownship,
    Phase,
    TunedAdf,
    TunedNav,
    MarkerBeacon,
    bearing_to_station,
    compute_panel,
    dme_slant,
    glideslope_deviation,
    gps_cdi_deflection,
    gps_nav_head,
    marker_state,
    nav_head,
    six_pack,
    vor_cdi,
)

STN = Point(40.0, -74.0)


# --------------------------------------------------------------------------- #
# GPS linear CDI
# --------------------------------------------------------------------------- #
def test_gps_cdi_sign_and_scale():
    assert gps_cdi_deflection(1.0, 2.0) == pytest.approx(-0.5)   # right of course -> fly left
    assert gps_cdi_deflection(-1.0, 2.0) == pytest.approx(0.5)
    assert gps_cdi_deflection(0.0, 2.0) == 0.0


def test_gps_cdi_clamps_full_scale():
    assert gps_cdi_deflection(50.0, 2.0) == -1.0
    assert gps_cdi_deflection(-50.0, 2.0) == 1.0


# --------------------------------------------------------------------------- #
# VOR angular CDI
# --------------------------------------------------------------------------- #
def test_vor_from_quadrant_right_of_course_deflects_left():
    ac = destination(STN, 10.0, 10.0)          # radial 010, east of the 360 course
    dfl, tf, ok = vor_cdi(STN, 0.0, ac, 360.0)
    assert ok and tf == "FROM"
    assert dfl < 0


def test_vor_to_quadrant_left_of_course_deflects_right():
    ac = destination(STN, 190.0, 10.0)         # south of the station, west of course
    dfl, tf, ok = vor_cdi(STN, 0.0, ac, 360.0)
    assert ok and tf == "TO"
    assert dfl > 0


def test_vor_on_course_is_centered_both_senses():
    dfl_f, tf_f, _ = vor_cdi(STN, 0.0, destination(STN, 360.0, 10.0), 360.0)
    dfl_t, tf_t, _ = vor_cdi(STN, 0.0, destination(STN, 180.0, 10.0), 360.0)
    assert tf_f == "FROM" and dfl_f == pytest.approx(0.0, abs=1e-6)
    assert tf_t == "TO" and dfl_t == pytest.approx(0.0, abs=1e-6)


def test_vor_two_degree_offset_is_one_fifth_scale():
    on_course = destination(STN, 360.0, 10.0)
    ac = destination(on_course, 90.0, 0.34920)      # 10 * tan(2 deg) nm east
    dfl, _, _ = vor_cdi(STN, 0.0, ac, 360.0)
    assert dfl == pytest.approx(-0.2, abs=0.02)     # ~2 deg / 10 deg full scale


def test_localizer_is_four_times_more_sensitive():
    on_course = destination(STN, 360.0, 10.0)
    ac = destination(on_course, 90.0, 0.34920)      # same ~2 deg offset
    dfl, _, _ = vor_cdi(STN, 0.0, ac, 360.0, full_scale_deg=LOC_FULL_SCALE_DEG)
    assert dfl == pytest.approx(-0.8, abs=0.05)


def test_vor_station_passage_flags_invalid():
    ac = destination(STN, 45.0, 0.1)
    dfl, tf, ok = vor_cdi(STN, 0.0, ac, 360.0)
    assert not ok and tf == "OFF" and dfl == 0.0


def test_vor_station_declination_rotates_the_course():
    # course 360 mag with a station 13 W declination -> the course line is 347 T
    ac_on_true_347 = destination(STN, 347.0, 10.0)
    dfl, _, ok = vor_cdi(STN, -13.0, ac_on_true_347, 360.0)
    assert ok and dfl == pytest.approx(0.0, abs=0.02)


# --------------------------------------------------------------------------- #
# bearing pointer / DME / markers
# --------------------------------------------------------------------------- #
def test_bearing_to_station_applies_local_variation():
    ac = destination(STN, 180.0, 20.0)             # due south of the station
    b0 = bearing_to_station(ac, STN, 0.0)
    assert min(b0, 360.0 - b0) == pytest.approx(0.0, abs=0.1)
    assert bearing_to_station(ac, STN, 10.0) == pytest.approx(350.0, abs=0.1)


def test_dme_slant_range_and_time():
    ac = destination(STN, 90.0, 6.0)
    slant, t = dme_slant(ac, 6076.115, STN, 0.0, 180.0)   # 1 nm up, 6 nm along
    assert slant == pytest.approx(6.0827, abs=0.01)
    assert t == pytest.approx(2.028, abs=0.02)


def test_dme_time_none_when_slow():
    ac = destination(STN, 90.0, 6.0)
    _, t = dme_slant(ac, 0.0, STN, 0.0, 10.0)
    assert t is None


def test_marker_lamp_lights_inside_footprint_and_low():
    om = MarkerBeacon("OM", destination(STN, 0.0, 0.0))
    near = destination(STN, 0.0, 0.3)
    assert marker_state(near, 1500.0, [om]).outer is True
    assert marker_state(near, 6000.0, [om]).outer is False     # altitude gate
    far = destination(STN, 0.0, 0.8)
    assert marker_state(far, 1500.0, [om]).outer is False


# --------------------------------------------------------------------------- #
# compute_panel assembler
# --------------------------------------------------------------------------- #
def _own(**kw):
    base = dict(pos=Point(40.0, -74.0), track_deg=90.0, heading_deg=90.0,
               gs_kt=120.0, altitude_ft=4000.0, magvar_deg=13.0)
    base.update(kw)
    return Ownship(**base)


def test_panel_gps_source_uses_navstate_xtk_and_phase_scale():
    nav = NavState(valid=True, cdi_source="GPS", xtk_nm=0.5, dtk=90.0, brg=92.0,
                   dist_nm=12.0, to_from="TO", annunciators=("SUSP",))
    p = compute_panel(_own(), nav, phase=Phase.TERMINAL)
    assert p.cdi.source == "GPS"
    assert p.cdi.full_scale_nm == 1.0
    assert p.cdi.deflection == pytest.approx(-0.5)
    assert p.cdi.to_from == "TO" and p.cdi.valid
    assert p.hsi_heading_deg == pytest.approx(77.0, abs=0.1)     # 90 T - 13 E
    assert p.bearing2.source == "GPS"
    assert p.bearing2.bearing_deg == pytest.approx(79.0, abs=0.1)
    assert p.dme.distance_nm == 12.0 and p.dme.time_min == pytest.approx(6.0, abs=0.1)
    assert p.annunciators == ("SUSP",)


def test_enroute_gps_full_scale_is_five_nm():
    from instruments import GPS_FULL_SCALE_NM
    assert GPS_FULL_SCALE_NM[Phase.ENROUTE] == 5.0          # GNS 530, non-WAAS
    assert GPS_FULL_SCALE_NM[Phase.TERMINAL] == 1.0
    assert GPS_FULL_SCALE_NM[Phase.APPROACH] == 0.30


def test_navstate_cdi_scale_overrides_phase_table():
    nav = NavState(valid=True, cdi_source="GPS", xtk_nm=0.5, dtk=90.0,
                   dist_nm=12.0, to_from="TO", cdi_scale_nm=0.30)
    p = compute_panel(_own(), nav, phase=Phase.ENROUTE)     # phase says 5.0 ...
    assert p.cdi.full_scale_nm == 0.30                       # ... NavState wins
    assert p.cdi.deflection == pytest.approx(-1.0)           # 0.5 nm on a 0.30 scale


def test_panel_vloc_source_uses_tuned_nav():
    nav = NavState(valid=True, cdi_source="VLOC", xtk_nm=0.0, dtk=0.0)
    ac = destination(STN, 10.0, 12.0)
    p = compute_panel(
        _own(pos=ac),
        nav,
        nav1=TunedNav(ident="ABC", pos=STN, course_deg=360.0, has_dme=True, elev_ft=100.0),
    )
    assert p.cdi.source == "VLOC"
    assert p.cdi.full_scale_deg is not None
    assert p.cdi.to_from == "FROM" and p.cdi.deflection < 0
    assert p.dme.valid and p.dme.ident == "ABC"
    assert p.bearing1.source == "VOR"


def test_panel_adf_takes_bearing2_over_gps():
    nav = NavState(valid=True, cdi_source="GPS", xtk_nm=0.0, dtk=90.0, brg=90.0, dist_nm=5.0)
    p = compute_panel(_own(), nav, adf=TunedAdf(ident="LOM", pos=destination(STN, 180.0, 8.0)))
    assert p.bearing2.source == "ADF"


def test_panel_without_navstate_is_invalid_cdi():
    p = compute_panel(_own(), None)
    assert not p.cdi.valid and p.dme.valid is False
    assert p.bearing1.source == "OFF"


# --------------------------------------------------------------------------- #
# glideslope
# --------------------------------------------------------------------------- #
def test_glideslope_on_path_is_centered():
    thr = Point(40.0, -74.0)
    # 6 nm out, on a 3 deg path: height = 6 * tan(3) * 6076 ~ 1911 ft
    ac = destination(thr, 180.0, 6.0)
    dfl, ok = glideslope_deviation(thr, 0.0, 3.0, ac, 6.0 * 6076.115 * 0.052407779)
    assert ok and dfl == pytest.approx(0.0, abs=0.05)


def test_glideslope_below_path_says_fly_up():
    thr = Point(40.0, -74.0)
    ac = destination(thr, 180.0, 6.0)
    dfl, ok = glideslope_deviation(thr, 0.0, 3.0, ac, 1400.0)   # low
    assert ok and dfl > 0.2                                     # + = fly up


def test_glideslope_way_high_flags_invalid():
    thr = Point(40.0, -74.0)
    ac = destination(thr, 180.0, 6.0)
    dfl, ok = glideslope_deviation(thr, 0.0, 3.0, ac, 9000.0)
    assert not ok and dfl < 0


# --------------------------------------------------------------------------- #
# nav_head (from a radios.NavReceiver)
# --------------------------------------------------------------------------- #
class _Rx:
    def __init__(self, **kw):
        self.tuned = True
        self.is_localizer = False
        self.station_pos = STN
        self.station_magvar = 0.0
        self.station_ident = "ABC"
        self.obs_deg = 360.0
        self.course_deg = 360.0
        self.has_dme = False
        self.gs_ref = None
        self.__dict__.update(kw)


def test_nav_head_vor_deviation_and_tofrom():
    rx = _Rx()
    ac = destination(STN, 10.0, 12.0)          # radial 010, right of the 360 course
    nh = nav_head(rx, ac, 5000.0, 120.0, 0.0)
    assert nh.valid and not nh.is_localizer
    assert nh.to_from == "FROM" and nh.deflection < 0
    assert nh.bearing_deg == pytest.approx(190.0, abs=1.0)


def test_nav_head_localizer_has_no_tofrom_and_carries_gs():
    thr = Point(40.0, -74.0)
    rx = _Rx(is_localizer=True, course_deg=40.0,
             gs_ref=(thr, 0.0, 3.0), station_pos=destination(thr, 40.0, 1.4))
    ac = destination(thr, 220.0, 6.0)         # 6 nm out on the approach side
    nh = nav_head(rx, ac, 1400.0, 130.0, 0.0)
    assert nh.is_localizer and nh.to_from == "OFF"
    assert nh.gs_valid and nh.gs_deflection > 0        # below path -> fly up
    assert nh.full_scale_deg == LOC_FULL_SCALE_DEG


def test_nav_head_untuned_is_invalid():
    class Off:
        tuned = False
        obs_deg = 123.0
    nh = nav_head(Off(), STN, 0.0, 0.0, 0.0)
    assert not nh.valid and nh.obs_deg == 123.0


# --------------------------------------------------------------------------- #
# gps_nav_head - the NAV1 round head slaved to the GNS's own CDI/VLOC switch
# --------------------------------------------------------------------------- #
def test_gps_nav_head_repackages_panel_cdi():
    """`gps_nav_head` must show exactly what `panel.cdi` (the GNS's own CDI
    strip, and what the autopilot's NAV mode flies - F27) already computed,
    not a second, independently-derived reading."""
    nav = NavState(valid=True, cdi_source="GPS", dtk=90.0, xtk_nm=0.5,
                   to_ident="alfa", dist_nm=12.3, to_from="TO", mode="LEG")
    own = Ownship(STN, magvar_deg=-10.0)
    panel = compute_panel(own, nav)
    nh = gps_nav_head(nav, panel)
    assert nh.valid and not nh.is_localizer
    assert nh.ident == "ALFA"                       # upper-cased for display
    assert nh.dme_nm == pytest.approx(12.3)
    assert nh.course_deg == pytest.approx(panel.cdi.course_deg)
    assert nh.deflection == pytest.approx(panel.cdi.deflection)
    assert nh.to_from == "TO"


def test_gps_nav_head_invalid_with_no_active_leg():
    nav = NavState(valid=False)
    own = Ownship(STN)
    panel = compute_panel(own, nav)
    assert not gps_nav_head(nav, panel).valid


# --------------------------------------------------------------------------- #
# six_pack
# --------------------------------------------------------------------------- #
def test_six_pack_reads_ownship_and_applies_magvar():
    class O:
        tas_kt = 155.0
        pitch_deg = 2.5
        bank_deg = -12.0
        altitude_ft = 4500.0
        vs_fpm = -320.0
        heading_deg = 100.0
        turn_rate_dps = -1.5
        slip_skid = 0.0
    sp = six_pack(O(), magvar_deg=-13.0)
    assert sp.airspeed_kt == 155.0 and sp.bank_deg == -12.0
    assert sp.heading_deg == pytest.approx(113.0)      # 100 T - (-13) = 113 mag
    assert sp.vsi_fpm == -320.0 and sp.turn_rate_dps == -1.5
