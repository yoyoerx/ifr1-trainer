"""Unit tests for radios.py - COM/NAV tuning, transponder, station resolve."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point, destination, initial_bearing, norm360, reciprocal  # noqa: E402
from navdata.model import Airport, NavDatabase, Runway, VhfNavaid  # noqa: E402
from radios import (  # noqa: E402
    ComRadio,
    NavReceiver,
    RadioStack,
    Transponder,
    morse_is_keyed,
    VOR_IDENT_WPM,
    morse_pattern,
)


# --------------------------------------------------------------------------- #
# COM tuning
# --------------------------------------------------------------------------- #
def test_com_swap():
    c = ComRadio(118.000, 121.500)
    c.swap()
    assert (c.active_mhz, c.standby_mhz) == (121.500, 118.000)


def test_com_set_emergency():
    c = ComRadio(118.000, 126.700)
    c.set_emergency()
    assert c.active_mhz == 121.500 and c.standby_mhz == 118.000
    c.set_emergency()                       # already on 121.5 -> standby untouched
    assert c.active_mhz == 121.500 and c.standby_mhz == 118.000


def test_com_khz_knob_wraps_within_1mhz():
    c = ComRadio(standby_mhz=121.500)
    c.tune(khz=1)
    assert c.standby_mhz == pytest.approx(121.525)
    c = ComRadio(standby_mhz=121.975)
    c.tune(khz=1)
    assert c.standby_mhz == pytest.approx(121.000)     # wraps, MHz digit unchanged


def test_com_mhz_knob_wraps_within_band():
    c = ComRadio(standby_mhz=136.900)
    c.tune(mhz=1)
    assert int(c.standby_mhz) == 118                    # 136 -> 118, kHz digits kept
    assert c.standby_mhz == pytest.approx(118.900)
    c = ComRadio(standby_mhz=118.400)
    c.tune(mhz=-1)
    assert int(c.standby_mhz) == 136


# --------------------------------------------------------------------------- #
# NAV tuning / OBS
# --------------------------------------------------------------------------- #
def test_nav_khz_step_is_50():
    n = NavReceiver(standby_mhz=110.00)
    n.tune(khz=1)
    assert n.standby_mhz == pytest.approx(110.05)


def test_obs_wraps():
    n = NavReceiver()
    n.set_obs(350)
    n.turn_obs(20)
    assert n.obs_deg == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# station resolution
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    d = NavDatabase(source="test")
    # a VOR-DME on 113.00 near (40, -74)
    d.add_vhf(VhfNavaid("ABE", Point(40.0, -74.0), 113.00, magvar_deg=-11.0,
                        name="ALLENTOWN", nav_class="VDHW"))
    # a far VOR sharing the frequency - should not win
    d.add_vhf(VhfNavaid("FAR", Point(10.0, -74.0), 113.00, nav_class="VDHW"))
    # a localizer on 110.30 off the departure end of RW04 at KTST
    rwy_thr = Point(40.00, -74.20)
    brg = 40.0
    loc_antenna = destination(rwy_thr, brg, 1.4)       # ~1.4 nm past the threshold
    d.add_vhf(VhfNavaid("ITST", loc_antenna, 110.30, nav_class="ITW", name="ILS RWY 04"))
    apt = Airport("KTST", Point(40.0, -74.19), elev_ft=200)
    apt.runways["RW04"] = Runway("RW04", rwy_thr, brg, length_ft=7000)
    d.add_airport(apt)
    return d


def test_resolve_picks_nearest_cofrequency_vor(db):
    n = NavReceiver(active_mhz=113.00)
    n.resolve(db, Point(40.1, -74.0))
    assert n.tuned and n.station_ident == "ABE"
    assert not n.is_localizer
    assert n.station_magvar == pytest.approx(-11.0)
    assert n.has_dme
    assert n.course_deg == n.obs_deg                    # VOR course follows the OBS


def test_resolve_localizer_pairs_runway_for_course_and_gs(db):
    n = NavReceiver(active_mhz=110.30)
    n.resolve(db, Point(40.05, -74.15))
    assert n.tuned and n.is_localizer
    assert n.loc_course_deg == pytest.approx(40.0, abs=0.5)
    assert n.course_deg == pytest.approx(40.0, abs=0.5)  # ignores OBS for a localizer
    assert n.has_gs
    thr, elev, ang = n.gs_ref
    assert elev == pytest.approx(200.0) and ang == pytest.approx(3.0)


def test_resolve_is_cached_until_frequency_changes(db):
    n = NavReceiver(active_mhz=113.00)
    n.resolve(db, Point(40.1, -74.0))
    n._resolved_freq = 999.0                            # force a re-resolve marker
    n.active_mhz = 110.30
    n.resolve(db, Point(40.05, -74.15))
    assert n.station_ident == "ITST"


def test_unresolved_frequency_clears_station(db):
    n = NavReceiver(active_mhz=116.55)
    n.resolve(db, Point(40.0, -74.0))
    assert not n.tuned and n.station_ident == ""


def test_nav_swap_forces_reresolve(db):
    n = NavReceiver(active_mhz=113.00, standby_mhz=110.30)
    n.resolve(db, Point(40.1, -74.0))
    assert n.station_ident == "ABE"
    n.swap()
    n.resolve(db, Point(40.05, -74.15))
    assert n.station_ident == "ITST"


def test_resolve_pi_localizer_uses_the_airport_station_declination(db):
    # A CIFP section P.I localizer (loc_bearing_deg + airport/runway_ident,
    # no magvar_deg of its own - real published courses are magnetic and need
    # the station declination, NOT zero, or the CDI reads persistently off-
    # centre even when the aircraft is right on the true extended centreline.
    rwy_thr = Point(40.12031388888889, -76.30285555555555)
    brg_mag = 77.0
    apt = Airport("KTST2", Point(40.12, -76.30), elev_ft=400, magvar_deg=-9.0)
    apt.runways["RW08"] = Runway("RW08", rwy_thr, brg_mag, length_ft=6900)
    d = NavDatabase(source="test")
    d.add_airport(apt)
    antenna = destination(rwy_thr, reciprocal(norm360(brg_mag - 9.0)), 1.2)
    d.add_vhf(VhfNavaid("ILNS", antenna, 108.70, nav_class="ILSW",
                        loc_bearing_deg=brg_mag, runway_ident="RW08", airport_ident="KTST2"))

    # a point on the TRUE extended centreline (course = published mag + magvar)
    on_course = destination(rwy_thr, reciprocal(norm360(brg_mag - 9.0)), 6.0)
    n = NavReceiver(active_mhz=108.70)
    n.resolve(d, on_course)
    assert n.is_localizer and n.loc_course_deg == pytest.approx(77.0)
    assert n.station_magvar == pytest.approx(-9.0)      # the airport's declination, not 0

    import instruments as instr
    dfl = instr.nav_head(n, on_course, 3000.0, 130.0, -9.0).deflection
    assert dfl == pytest.approx(0.0, abs=0.02)           # centred, not skewed by ~9 deg


def test_navaid_range_scales_with_class_and_altitude():
    from radios import _navaid_range_nm

    class N:
        def __init__(self, cls):
            self.nav_class = cls
            self.elev_ft = 0

    assert _navaid_range_nm(N("VTTW"), 5000) == pytest.approx(25.0)   # terminal
    assert _navaid_range_nm(N("VDLW"), 5000) == pytest.approx(40.0)   # low
    assert _navaid_range_nm(N("VDHW"), 3000) == pytest.approx(40.0)   # high, low tier
    assert _navaid_range_nm(N("VDHW"), 20000) == pytest.approx(130.0)  # high, FL200
    assert _navaid_range_nm(N("VDHW"), 50000) == pytest.approx(100.0)  # high, FL500
    # very low altitude -> radio line of sight, not the full service volume
    assert _navaid_range_nm(N("VDHW"), 900) < 40.0


def test_vor_times_out_when_leaving_its_service_volume():
    d = NavDatabase(source="test")
    d.add_vhf(VhfNavaid("LOW", Point(40.0, -74.0), 112.00, nav_class="VDLW"))  # 40 nm
    n = NavReceiver(active_mhz=112.00)
    n.resolve(d, destination(Point(40.0, -74.0), 90.0, 20.0), 6000.0)
    assert n.station_ident == "LOW"
    n.resolve(d, destination(Point(40.0, -74.0), 90.0, 60.0), 6000.0)   # now 60 nm out
    assert not n.tuned                                                  # dropped


def test_receiver_hands_off_between_cofrequency_vors():
    d = NavDatabase(source="test")
    a = Point(40.0, -74.0)
    b = destination(a, 90.0, 150.0)                       # 150 nm east, same freq
    d.add_vhf(VhfNavaid("AAA", a, 113.50, nav_class="VDLW"))
    d.add_vhf(VhfNavaid("BBB", b, 113.50, nav_class="VDLW"))
    n = NavReceiver(active_mhz=113.50)
    n.resolve(d, destination(a, 90.0, 15.0), 8000.0)
    assert n.station_ident == "AAA"
    n.resolve(d, destination(a, 90.0, 75.0), 8000.0)      # gap between the two SSVs
    assert not n.tuned
    n.resolve(d, destination(b, 270.0, 15.0), 8000.0)     # into BBB's area
    assert n.station_ident == "BBB"


def test_locked_station_has_hysteresis_at_the_edge():
    d = NavDatabase(source="test")
    d.add_vhf(VhfNavaid("EDG", Point(40.0, -74.0), 111.00, nav_class="VDLW"))  # 40 nm
    n = NavReceiver(active_mhz=111.00)
    n.resolve(d, destination(Point(40.0, -74.0), 90.0, 20.0), 6000.0)
    n.resolve(d, destination(Point(40.0, -74.0), 90.0, 43.0), 6000.0)  # just past 40 nm
    assert n.station_ident == "EDG"                                    # held (hysteresis)
    n.resolve(d, destination(Point(40.0, -74.0), 90.0, 50.0), 6000.0)  # well past
    assert not n.tuned


def test_localizer_only_received_inside_its_beam():
    """Two ILS on the same freq: you receive the one whose centreline you are on,
    not the geographically closer field."""
    d = NavDatabase(source="test")
    # localizer A: RW09 at (40.00, -75.00), antenna 1.4 nm east of the threshold
    thrA = Point(40.00, -75.00)
    d.add_vhf(VhfNavaid("IAAA", destination(thrA, 90.0, 1.4), 109.30, nav_class="ITW"))
    aA = Airport("KAAA", Point(40.0, -75.0), elev_ft=100)
    aA.runways["RW09"] = Runway("RW09", thrA, 90.0, length_ft=7000)
    d.add_airport(aA)
    # localizer B on the same freq, ~35 nm north, RW36
    thrB = Point(40.60, -75.00)
    d.add_vhf(VhfNavaid("IBBB", destination(thrB, 360.0, 1.4), 109.30, nav_class="ITW"))
    aB = Airport("KBBB", Point(40.6, -75.0), elev_ft=100)
    aB.runways["RW36"] = Runway("RW36", thrB, 360.0, length_ft=7000)
    d.add_airport(aB)

    n = NavReceiver(active_mhz=109.30)
    # on the RW09 final ~8 nm west of the threshold -> receives A
    n.resolve(d, destination(thrA, 270.0, 8.0))
    assert n.station_ident == "IAAA"
    # abeam KAAA but well off its centreline, 20 nm south of everything -> nothing
    n._resolved_freq = None
    n.resolve(d, Point(39.70, -75.00))
    assert not n.tuned


def test_resolve_prefers_the_full_ils_record_over_a_courseless_duplicate():
    """F74 (playtest, KJFK ILS/LOC 22R): an airport's ILS can legitimately
    appear twice under the same ident+frequency - once as the real Section
    P.I precision-approach record (course + runway), once as a plain
    Section D navaid-directory duplicate of the same transmitter with no
    course data at all. Picking by raw nearest-distance let the course-less
    duplicate win purely because it happened to sit closer; `resolve` then
    had to guess a runway pairing from the duplicate's own (not the real
    antenna's) position, which resolved to the wrong runway's course
    entirely - the autopilot then steered onto a nonsensical course the
    instant the CDI auto-switched to VLOC near the FAF. The complete record
    must always win, even when the duplicate is closer."""
    rwy_thr = Point(40.00, -74.00)
    brg = 220.0
    real_antenna = destination(rwy_thr, brg, 1.4)
    d = NavDatabase(source="test")
    apt = Airport("KDUP", Point(40.0, -73.98), elev_ft=100, magvar_deg=-12.0)
    apt.runways["RW22"] = Runway("RW22", rwy_thr, brg, length_ft=7000)
    d.add_airport(apt)
    # the real Section P.I record - correct course, but placed FARTHER away
    d.add_vhf(VhfNavaid("IDUP", real_antenna, 109.90, nav_class="ILSW",
                        loc_bearing_deg=brg, runway_ident="RW22", airport_ident="KDUP"))
    # a course-less Section D duplicate of the same ident+frequency, placed
    # CLOSER to the test point than the real antenna - nothing to disambiguate
    # it from the real one except the missing course data
    stub_pos = destination(rwy_thr, reciprocal(brg), 5.5)   # near the aircraft below
    d.add_vhf(VhfNavaid("IDUP", stub_pos, 109.90, nav_class="ITW"))

    n = NavReceiver(active_mhz=109.90)
    on_course = destination(rwy_thr, reciprocal(brg), 6.0)   # established on final
    n.resolve(d, on_course)
    assert n.is_localizer
    assert n.loc_course_deg == pytest.approx(220.0)         # the real record's course
    assert n._station.runway_ident == "RW22"


def test_resolve_disambiguates_parallel_runways_sharing_a_frequency():
    """F74: real airports legitimately reuse one ILS frequency between two
    runways whose beams point in different directions (never simultaneously
    receivable in reality, since the beams don't overlap where it matters -
    e.g. KJFK's 109.5 serves both 22R's course 221 and 04R's course 44). The
    front-or-back beam cone used to decide *receivability* is deliberately
    generous per station (it has to admit a genuine back-course approach),
    so both can independently pass it - `resolve` must still prefer whichever
    one the aircraft is actually established on, not whichever antenna is a
    few tenths of a mile closer."""
    d = NavDatabase(source="test")
    apt = Airport("KPAR", Point(40.0, -74.0), elev_ft=100, magvar_deg=0.0)
    # runway A: course 220, antenna near the aircraft's position below
    thrA = Point(40.00, -74.00)
    antA = destination(thrA, 220.0, 1.4)
    apt.runways["RW22"] = Runway("RW22", thrA, 220.0, length_ft=7000)
    # runway B: a parallel/crossing runway, course ~40 (near A's reciprocal),
    # antenna placed CLOSER to the test aircraft than A's real antenna
    thrB = Point(40.02, -74.05)
    antB = destination(thrB, 40.0, 0.3)
    apt.runways["RW04"] = Runway("RW04", thrB, 40.0, length_ft=7000)
    d.add_airport(apt)
    d.add_vhf(VhfNavaid("IPAR", antA, 109.70, nav_class="ILSW",
                        loc_bearing_deg=220.0, runway_ident="RW22", airport_ident="KPAR"))
    d.add_vhf(VhfNavaid("IPAR", antB, 109.70, nav_class="ILSW",
                        loc_bearing_deg=40.0, runway_ident="RW04", airport_ident="KPAR"))

    n = NavReceiver(active_mhz=109.70)
    # established on RW22's final, well aligned with A's front course
    on_a = destination(thrA, reciprocal(220.0), 6.0)
    n.resolve(d, on_a)
    assert n.is_localizer and n.loc_course_deg == pytest.approx(220.0)
    assert n._station.runway_ident == "RW22"


# --------------------------------------------------------------------------- #
# transponder
# --------------------------------------------------------------------------- #
def test_transponder_octal_digit_edit_wraps():
    t = Transponder(code=1200)
    t.edit_digit(3, 5)
    assert t.squawk == "1205"
    t.edit_digit(0, 7)
    assert t.squawk == "0205"                           # 1 -> (1+7)%8 = 0


def test_transponder_mode_cycle():
    t = Transponder(mode="ALT")
    t.cycle_mode(1)
    assert t.mode == "GND"
    t.cycle_mode(1)
    assert t.mode == "OFF"


def test_radiostack_resolves_both_navs(db):
    rs = RadioStack()
    rs.nav1.active_mhz = 113.00
    rs.nav2.active_mhz = 110.30
    rs.resolve(db, Point(40.05, -74.1))
    assert rs.nav1.station_ident == "ABE"
    assert rs.nav2.station_ident == "ITST" and rs.nav2.is_localizer


# --------------------------------------------------------------------------- #
# VLOC Morse ident (FINDINGS D8 remainder)                                    #
# --------------------------------------------------------------------------- #
def test_morse_pattern_known_letters():
    assert morse_pattern("A") == ".-"
    assert morse_pattern("SOS") == "... --- ..."
    assert morse_pattern("i08") == ".. ----- ---.."      # non A-Z0-9 dropped, none here


def test_morse_pattern_ignores_unknown_characters():
    assert morse_pattern("I-N") == ".. -."               # "-" isn't in the table


def test_morse_is_keyed_follows_the_dot_dash_timeline():
    # "A" -> ".-": dot(1u) gap(1u) dash(3u); dit = 1.2/7 ~= 0.17 s (FAA ~7 wpm)
    dit = 1.2 / VOR_IDENT_WPM
    assert morse_is_keyed("A", 0.0)                       # inside the dot
    assert morse_is_keyed("A", dit * 0.5)
    assert not morse_is_keyed("A", dit * 1.5)              # inside the inter-symbol gap
    assert morse_is_keyed("A", dit * 2.5)                  # inside the dash (starts at 2u)


def test_morse_is_keyed_silent_during_the_repeat_gap():
    dit = 1.2 / VOR_IDENT_WPM
    total = (1 + 1 + 3) * dit                              # "A" pattern length
    assert not morse_is_keyed("A", total + 0.1)            # into the trailing silence


def test_morse_is_keyed_false_for_an_empty_or_unresolved_ident():
    assert not morse_is_keyed("", 0.0)
    assert not morse_is_keyed("---", 0.0)


def test_ident_morse_rate_is_the_faa_seven_wpm():
    """F40: AIM 1-1-3 puts VOR ident at ~7 wpm; it was 20 wpm (3x too fast)."""
    assert VOR_IDENT_WPM == 7.0
    assert 1.2 / VOR_IDENT_WPM == pytest.approx(0.171, abs=0.002)


def test_ident_repeats_every_seven_and_a_half_seconds():
    """F41: the ident loop used a ~1.5 s gap; a real station starts its ident
    about every 7.5 s (four per 30 s)."""
    from radios import MORSE_REPEAT_PERIOD_S
    assert morse_is_keyed("A", 0.0)
    assert not morse_is_keyed("A", 5.0)                    # long silence, not 1.5 s
    assert morse_is_keyed("A", MORSE_REPEAT_PERIOD_S)      # next ident starts on the period
    # an ident longer than the period still gets a gap before it repeats
    long_ident = "0123456789"
    assert morse_is_keyed(long_ident, 0.0)
