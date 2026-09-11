"""Unit tests for datasrc.airac.

Reference effective dates are published ICAO/FAA AIRAC dates; the 28-day grid and
the 13-vs-14 cycle-count logic are checked against 2020 (a 14-cycle year) and
2026 (a 13-cycle year).
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc.airac import (  # noqa: E402
    Cycle,
    current_cycle,
    cycle_from_date,
    cycle_from_ident,
)

# ident -> published effective date
KNOWN = {
    "2501": date(2025, 1, 23),
    "2513": date(2025, 12, 25),
    "2601": date(2026, 1, 22),
    "2602": date(2026, 2, 19),
    "2603": date(2026, 3, 19),
    "2604": date(2026, 4, 16),
    "2605": date(2026, 5, 14),
    "2608": date(2026, 8, 6),
    "2609": date(2026, 9, 3),
    # 2020 held 14 cycles
    "2001": date(2020, 1, 2),
    "2014": date(2020, 12, 31),
    "2101": date(2021, 1, 28),
}


@pytest.mark.parametrize("ident, eff", KNOWN.items())
def test_cycle_from_ident_effective(ident, eff):
    c = cycle_from_ident(ident)
    assert c.effective == eff
    assert c.ident == ident
    assert (c.expiration - c.effective).days == 28


@pytest.mark.parametrize("ident, eff", KNOWN.items())
def test_cycle_from_date_roundtrips(ident, eff):
    assert cycle_from_date(eff).ident == ident
    # a day inside the window resolves to the same cycle
    assert cycle_from_date(eff.fromordinal(eff.toordinal() + 13)).ident == ident
    # the day before is the previous cycle
    assert cycle_from_date(eff.fromordinal(eff.toordinal() - 1)).ident != ident


@pytest.mark.parametrize("ident", KNOWN)
def test_ident_roundtrip(ident):
    assert cycle_from_ident(ident).ident == ident


@pytest.mark.parametrize("ident", KNOWN)
def test_next_prev_inverse(ident):
    c = cycle_from_ident(ident)
    assert c.next().prev() == c
    assert c.prev().next() == c
    assert (c.next().effective - c.effective).days == 28


def test_chain_across_year_boundary_13_cycle_year():
    assert cycle_from_ident("2513").next().ident == "2601"
    assert cycle_from_ident("2601").prev().ident == "2513"


def test_chain_across_year_boundary_14_cycle_year():
    assert cycle_from_ident("2013").next().ident == "2014"
    assert cycle_from_ident("2014").next().ident == "2101"
    assert cycle_from_ident("2101").prev().ident == "2014"


def test_2026_has_13_cycles_2020_has_14():
    c = cycle_from_ident("2601")
    n = 1
    while c.next().effective.year == 2026:
        c = c.next()
        n += 1
    assert n == 13

    c = cycle_from_ident("2001")
    n = 1
    while c.next().effective.year == 2020:
        c = c.next()
        n += 1
    assert n == 14


# --------------------------------------------------------------------------- #
# current_cycle / validity queries                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "on, expected",
    [
        (date(2026, 9, 3), "2609"),   # effective date is inclusive
        (date(2026, 9, 20), "2609"),
        (date(2026, 9, 2), "2608"),
        (date(2026, 10, 1), "2610"),  # 2609 expires this day
    ],
)
def test_current_cycle(on, expected):
    assert current_cycle(on).ident == expected


def test_contains_is_half_open():
    c = cycle_from_ident("2609")
    assert c.contains(date(2026, 9, 3))
    assert c.contains(date(2026, 9, 30))
    assert not c.contains(date(2026, 10, 1))
    assert not c.contains(date(2026, 9, 2))


def test_expiry_helpers():
    c = cycle_from_ident("2609")  # expires 2026-10-01
    assert c.days_until_expiry(date(2026, 9, 3)) == 28
    assert c.days_until_expiry(date(2026, 10, 1)) == 0
    assert c.days_until_expiry(date(2026, 10, 6)) == -5
    assert not c.is_expired(date(2026, 9, 30))
    assert c.is_expired(date(2026, 10, 1))
    assert c.is_current(date(2026, 9, 15))
    assert not c.is_current(date(2026, 10, 2))


def test_label_shape():
    c = cycle_from_ident("2609")
    assert c.label() == "2609  effective 2026-09-03  expires 2026-10-01"


# --------------------------------------------------------------------------- #
# bad input                                                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["2699", "2600", "26", "abcd", "260a", "2614", ""])
def test_cycle_from_ident_rejects_bad(bad):
    with pytest.raises(ValueError):
        cycle_from_ident(bad)


def test_2614_rejected_but_2014_ok():
    # 2026 has 13 cycles -> "2614" is invalid; 2020 has 14 -> "2014" is valid
    with pytest.raises(ValueError):
        cycle_from_ident("2614")
    assert cycle_from_ident("2014").effective == date(2020, 12, 31)


def test_cycle_ordering():
    assert cycle_from_ident("2608") < cycle_from_ident("2609")
    assert max(cycle_from_ident(i) for i in ("2601", "2609", "2605")).ident == "2609"
