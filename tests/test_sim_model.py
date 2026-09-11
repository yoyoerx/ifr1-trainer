"""Unit tests for sim_model.py - the kinematic ownship."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point, angle_diff, great_circle_nm  # noqa: E402
from gns530 import NavState  # noqa: E402
from sim_model import SimModel, density_ratio, ias_from_tas, tas_from_ias  # noqa: E402
from windsaloft import WindLevel, WindsAloftProfile  # noqa: E402

ORIGIN = Point(40.0, -74.0)


def _run(sim, secs, dt=1.0):
    n = int(secs / dt)
    st = sim.state
    for _ in range(n):
        st = sim.step(dt)
    return st


# --------------------------------------------------------------------------- #
# steady state / wind
# --------------------------------------------------------------------------- #
def test_no_wind_track_equals_heading():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, tas_kt=120.0)
    st = sim.step(1.0)
    assert st.track_deg == pytest.approx(90.0)
    assert st.gs_kt == pytest.approx(120.0)


def test_wind_solve_drift_and_groundspeed():
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, tas_kt=100.0,
                   wind_from_deg=270.0, wind_kt=20.0)
    st = sim.step(1.0)
    assert st.track_deg == pytest.approx(11.31, abs=0.2)     # atan2(20, 100)
    assert st.gs_kt == pytest.approx(101.98, abs=0.1)


# --------------------------------------------------------------------------- #
# commanded heading / altitude / speed
# --------------------------------------------------------------------------- #
def test_turns_the_short_way_to_target_heading():
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, tas_kt=120.0)
    sim.command(heading=350.0)
    sim.step(1.0)
    assert sim.state.heading_deg == pytest.approx(357.0, abs=0.01)   # turned LEFT
    _run(sim, 30)
    assert sim.state.heading_deg == pytest.approx(350.0, abs=0.5)


def test_reaches_far_target_heading_at_standard_rate():
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, tas_kt=120.0)
    sim.command(heading=90.0)
    st = sim.step(1.0)
    assert st.bank_deg > 10.0                                 # banking into the turn
    _run(sim, 40)
    assert sim.state.heading_deg == pytest.approx(90.0, abs=0.5)
    assert sim.state.bank_deg == pytest.approx(0.0)


def test_climbs_to_target_altitude_and_levels_off():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=3000.0, tas_kt=120.0)
    sim.command(altitude=5000.0)
    mid = sim.step(10.0)
    assert 0.0 < mid.vs_fpm <= 1000.0
    _run(sim, 400)
    assert sim.state.altitude_ft == pytest.approx(5000.0, abs=1.0)
    assert sim.state.vs_fpm == pytest.approx(0.0)


def test_accelerates_toward_target_speed():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, tas_kt=120.0)
    sim.command(tas=90.0)
    _run(sim, 30)
    assert sim.state.tas_kt == pytest.approx(90.0, abs=1.0)


# --------------------------------------------------------------------------- #
# IAS set-point (pseudo speed manager - no throttle model)
# --------------------------------------------------------------------------- #
def test_ias_tas_conversion_is_identity_at_sea_level_and_grows_with_height():
    assert tas_from_ias(120.0, 0.0) == pytest.approx(120.0, abs=0.1)
    assert ias_from_tas(120.0, 0.0) == pytest.approx(120.0, abs=0.1)
    # ~2 %/1000 ft near the surface, more with altitude; 120 KIAS -> ~140 KTAS @ 10k
    assert tas_from_ias(120.0, 10000.0) == pytest.approx(139.6, abs=1.0)
    assert tas_from_ias(120.0, 10000.0) > 120.0
    assert ias_from_tas(tas_from_ias(157.0, 8000.0), 8000.0) == pytest.approx(157.0, abs=0.01)


def test_held_ias_drives_tas_and_feeds_groundspeed():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=8000.0, tas_kt=100.0)
    sim.command(ias=130.0)
    st = _run(sim, 60)
    assert st.ias_kt == pytest.approx(130.0, abs=1.0)
    assert st.tas_kt == pytest.approx(tas_from_ias(130.0, 8000.0), abs=1.0)
    assert st.tas_kt > 130.0                      # TAS above IAS at altitude
    assert st.gs_kt == pytest.approx(st.tas_kt, abs=0.1)   # no wind -> GS == TAS


def test_constant_ias_climb_reads_increasing_tas():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=2000.0, tas_kt=120.0)
    sim.command(ias=120.0)
    sim.command(altitude=12000.0)
    low = _run(sim, 20)
    high = _run(sim, 1200)
    assert high.altitude_ft == pytest.approx(12000.0, abs=10.0)
    assert high.tas_kt > low.tas_kt + 10.0        # same IAS, higher -> faster TAS
    assert high.ias_kt == pytest.approx(120.0, abs=1.5)


def test_commanding_tas_cancels_the_ias_hold():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=9000.0, tas_kt=140.0)
    sim.command(ias=120.0)
    assert sim.target_ias == 120.0
    sim.command(tas=155.0)
    assert sim.target_ias is None
    _run(sim, 60)
    assert sim.state.tas_kt == pytest.approx(155.0, abs=1.0)


# --------------------------------------------------------------------------- #
# position integration
# --------------------------------------------------------------------------- #
def test_position_advances_along_track():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, tas_kt=120.0)
    sim.command(heading=90.0)
    st = _run(sim, 3600, dt=10.0)
    assert great_circle_nm(ORIGIN, st.pos) == pytest.approx(120.0, abs=1.0)


# --------------------------------------------------------------------------- #
# leg following
# --------------------------------------------------------------------------- #
def test_follow_leg_sets_intercept_heading_toward_course():
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, tas_kt=120.0)
    sim.follow_leg(NavState(valid=True, dtk=360.0, xtk_nm=2.0))    # 2 nm right of course
    assert sim.target_heading == pytest.approx(336.0, abs=0.01)    # cut left to intercept
    sim.follow_leg(NavState(valid=True, dtk=360.0, xtk_nm=-2.0))
    assert sim.target_heading == pytest.approx(24.0, abs=0.01)


def test_follow_leg_includes_wind_correction():
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, tas_kt=120.0,
                   wind_from_deg=90.0, wind_kt=30.0)
    sim.follow_leg(NavState(valid=True, dtk=360.0, xtk_nm=0.0))
    assert sim.target_heading == pytest.approx(14.48, abs=0.2)     # crab into the wind


def test_follow_leg_noop_without_guidance():
    sim = SimModel(pos=ORIGIN, heading_deg=45.0, tas_kt=120.0)
    sim.follow_leg(None)
    sim.follow_leg(NavState(valid=False))
    assert sim.target_heading is None


# --------------------------------------------------------------------------- #
# winds-aloft profile (multi-altitude wind + OAT)
# --------------------------------------------------------------------------- #
def test_winds_aloft_profile_overrides_the_uniform_wind_by_altitude():
    profile = WindsAloftProfile((
        WindLevel(3000, 0.0, 0.0),        # calm low
        WindLevel(9000, 270.0, 40.0),     # strong westerly up high
    ))
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=3000.0, tas_kt=120.0,
                   wind_from_deg=180.0, wind_kt=99.0,   # uniform wind should be ignored
                   winds_aloft=profile)
    st = sim.step(1.0)
    assert st.gs_kt == pytest.approx(120.0, abs=0.5)   # calm at 3000 ft
    sim.altitude = 9000.0
    st = sim.step(1.0)
    assert st.gs_kt != pytest.approx(120.0, abs=0.5)   # windy at 9000 ft now


def test_set_wind_clears_an_active_winds_aloft_profile():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, tas_kt=120.0,
                   winds_aloft=WindsAloftProfile.uniform(270.0, 40.0))
    sim.set_wind(0.0, 0.0)
    assert sim.winds_aloft is None
    st = sim.step(1.0)
    assert st.gs_kt == pytest.approx(120.0, abs=0.5)


def test_set_winds_aloft_setter():
    sim = SimModel(pos=ORIGIN, heading_deg=90.0, tas_kt=120.0)
    sim.set_winds_aloft(WindsAloftProfile.uniform(270.0, 20.0))
    assert sim.winds_aloft is not None


def test_follow_leg_uses_the_winds_aloft_profile_at_current_altitude():
    profile = WindsAloftProfile.uniform(90.0, 30.0)
    sim = SimModel(pos=ORIGIN, heading_deg=360.0, altitude_ft=5000.0, tas_kt=120.0,
                   winds_aloft=profile)
    sim.follow_leg(NavState(valid=True, dtk=360.0, xtk_nm=0.0))
    assert sim.target_heading == pytest.approx(14.48, abs=0.2)     # same crab as the uniform case


def test_temperature_aloft_changes_tas_for_a_held_ias():
    warm = WindsAloftProfile.uniform(0.0, 0.0, temp_c=40.0)     # much warmer than ISA
    cold = WindsAloftProfile.uniform(0.0, 0.0, temp_c=-40.0)    # much colder than ISA
    sim_warm = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=8000.0, tas_kt=100.0,
                        winds_aloft=warm)
    sim_warm.command(ias=130.0)
    st_warm = _run(sim_warm, 60)
    sim_cold = SimModel(pos=ORIGIN, heading_deg=90.0, altitude_ft=8000.0, tas_kt=100.0,
                        winds_aloft=cold)
    sim_cold.command(ias=130.0)
    st_cold = _run(sim_cold, 60)
    assert st_warm.ias_kt == pytest.approx(130.0, abs=1.0)
    assert st_cold.ias_kt == pytest.approx(130.0, abs=1.0)
    # same IAS, but a warmer OAT means thinner air -> higher TAS for the same IAS
    assert st_warm.tas_kt > st_cold.tas_kt


def test_density_ratio_with_oat_matches_isa_when_temp_is_standard():
    from windsaloft import isa_temp_c
    assert density_ratio(8000.0, isa_temp_c(8000.0)) == pytest.approx(density_ratio(8000.0), abs=1e-6)



def test_follow_leg_then_fly_reduces_cross_track():
    # place the leg as a north line through ORIGIN; start 3 nm east of it
    from navmath import destination
    start = destination(ORIGIN, 90.0, 3.0)
    sim = SimModel(pos=start, heading_deg=360.0, tas_kt=120.0)
    a, b = ORIGIN, destination(ORIGIN, 360.0, 50.0)
    from navmath import cross_track_nm
    xtk0 = cross_track_nm(a, b, sim.state.pos)
    for _ in range(120):
        xtk = cross_track_nm(a, b, sim.state.pos)
        sim.follow_leg(NavState(valid=True, dtk=360.0, xtk_nm=xtk))
        sim.step(1.0)
    end = abs(cross_track_nm(a, b, sim.state.pos))
    assert end < 1.5 and end < abs(xtk0)
