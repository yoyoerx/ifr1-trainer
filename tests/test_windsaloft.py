"""Unit tests for windsaloft.py - the multi-altitude wind/temperature profile."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from windsaloft import WindLevel, WindsAloftProfile, isa_temp_c, parse_cli  # noqa: E402


def test_isa_temp_c_sea_level_and_altitude():
    assert isa_temp_c(0) == pytest.approx(15.0)
    assert isa_temp_c(10000) == pytest.approx(-4.8)


def test_single_level_profile_holds_constant_at_any_altitude():
    p = WindsAloftProfile.uniform(270.0, 20.0, temp_c=10.0)
    assert p.wind_at(0) == pytest.approx((270.0, 20.0))
    assert p.wind_at(18000) == pytest.approx((270.0, 20.0))
    assert p.temp_at(18000) == pytest.approx(10.0)


def test_wind_at_interpolates_between_bracketing_levels():
    p = WindsAloftProfile((
        WindLevel(3000, 270.0, 10.0, 10.0),
        WindLevel(9000, 270.0, 30.0, -5.0),
    ))
    from_deg, kt = p.wind_at(6000)
    assert from_deg == pytest.approx(270.0)
    assert kt == pytest.approx(20.0, abs=0.5)
    assert p.temp_at(6000) == pytest.approx(2.5, abs=0.1)


def test_wind_at_holds_the_nearest_level_outside_the_table():
    p = WindsAloftProfile((
        WindLevel(3000, 300.0, 20.0),
        WindLevel(9000, 320.0, 40.0),
    ))
    assert p.wind_at(0) == pytest.approx((300.0, 20.0))
    assert p.wind_at(30000) == pytest.approx((320.0, 40.0))


def test_direction_interpolates_through_the_short_way_across_north():
    p = WindsAloftProfile((
        WindLevel(0, 350.0, 20.0),
        WindLevel(10000, 10.0, 20.0),
    ))
    from_deg, kt = p.wind_at(5000)
    # should pass through 000, not backwards through 180
    assert from_deg < 10.0 or from_deg > 350.0
    assert kt == pytest.approx(20.0, abs=1.0)


def test_temp_at_is_none_when_no_level_carries_a_temperature():
    p = WindsAloftProfile((WindLevel(3000, 270.0, 10.0), WindLevel(9000, 280.0, 20.0)))
    assert p.temp_at(6000) is None
    assert not p.has_temps


def test_isa_dev_c_at_reports_deviation_from_standard():
    p = WindsAloftProfile.uniform(0.0, 0.0, temp_c=isa_temp_c(9000) + 8.0)
    assert p.isa_dev_c_at(9000) == pytest.approx(8.0)


def test_from_fd_levels_drops_light_and_variable_entries():
    p = WindsAloftProfile.from_fd_levels([
        (3000, None, None, None),          # light & variable / not forecast
        (9000, 280.0, 35.0, -5.0),
        (18000, 300.0, 55.0, -20.0),
    ])
    assert p is not None
    assert [w.alt_ft for w in p.levels] == [9000, 18000]


def test_from_fd_levels_returns_none_when_nothing_usable():
    assert WindsAloftProfile.from_fd_levels([(3000, None, None, None)]) is None


# --------------------------------------------------------------------------- #
# CLI parsing
# --------------------------------------------------------------------------- #
def test_parse_cli_multiple_levels_with_temperature():
    p = parse_cli("3000:280/20/-05 9000:300/35/-15 18000:310/55/-30")
    assert len(p.levels) == 3
    assert p.levels[0] == WindLevel(3000.0, 280.0, 20.0, -5.0)
    assert p.levels[-1] == WindLevel(18000.0, 310.0, 55.0, -30.0)


def test_parse_cli_temperature_is_optional():
    p = parse_cli("6000:250/15")
    assert p.levels[0].temp_c is None


def test_parse_cli_accepts_comma_separation_and_positive_temp():
    p = parse_cli("3000:280/20/+05,9000:300/35/+15")
    assert p.levels[0].temp_c == pytest.approx(5.0)
    assert p.levels[1].temp_c == pytest.approx(15.0)


def test_parse_cli_rejects_a_malformed_entry():
    with pytest.raises(ValueError):
        parse_cli("not-a-level")


def test_parse_cli_rejects_empty_string():
    with pytest.raises(ValueError):
        parse_cli("   ")
