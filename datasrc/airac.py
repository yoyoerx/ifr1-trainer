"""AIRAC cycle <-> date arithmetic. Pure, stdlib only.

An AIRAC cycle is a 28-day period on a globally fixed calendar. Each cycle has:

* an **ident** ``YYNN`` -- two-digit year, two-digit ordinal within that year,
  ``1``-based (e.g. ``"2610"`` = the 10th cycle effective in 2026). The FAA's
  d-TPP numbering matches this.
* an **effective** date (00:00 UTC that day) and an **expiration** date, which is
  simply the next cycle's effective date. Validity is the half-open interval
  ``[effective, expiration)``.

Most years hold 13 cycles; a year occasionally holds 14 (e.g. 2020). This module
derives the count structurally from the 28-day grid, so both are handled without
a table.

Anchor: AIRAC ``2601`` was effective ``2026-01-22``. Every cycle is
``anchor + k * 28 days`` for integer ``k``.

Limitation: idents are assumed to be 21st-century (``"YY"`` -> ``20YY``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

CYCLE_DAYS = 28
ANCHOR_IDENT = "2601"
ANCHOR_DATE = date(2026, 1, 22)

_IDENT_RE = re.compile(r"^(\d{2})(\d{2})$")
_ONE_CYCLE = timedelta(days=CYCLE_DAYS)

__all__ = [
    "CYCLE_DAYS",
    "ANCHOR_IDENT",
    "ANCHOR_DATE",
    "Cycle",
    "current_cycle",
    "cycle_from_date",
    "cycle_from_ident",
]


def _first_effective_of_year(year: int) -> date:
    """The earliest AIRAC effective date whose calendar year is ``year``."""
    guess_k = round((date(year, 1, 1) - ANCHOR_DATE).days / CYCLE_DAYS)
    d = ANCHOR_DATE + timedelta(days=guess_k * CYCLE_DAYS)
    # walk back while an earlier grid date is still within `year`
    while d.year > year or (d.year == year and (d - _ONE_CYCLE).year == year):
        d -= _ONE_CYCLE
    # walk forward if we landed in the previous year
    while d.year < year:
        d += _ONE_CYCLE
    return d


def _ident_for_effective(eff: date) -> str:
    first = _first_effective_of_year(eff.year)
    ordinal = (eff - first).days // CYCLE_DAYS + 1
    return f"{eff.year % 100:02d}{ordinal:02d}"


@dataclass(frozen=True, order=True)
class Cycle:
    """One 28-day AIRAC cycle.

    Ordering / equality are by ``effective`` date (``ident`` and ``expiration``
    are derived, so this stays consistent).
    """

    effective: date
    ident: str
    expiration: date

    # -- construction ---------------------------------------------------
    @classmethod
    def from_effective(cls, eff: date) -> "Cycle":
        return cls(
            effective=eff,
            ident=_ident_for_effective(eff),
            expiration=eff + _ONE_CYCLE,
        )

    # -- navigation ---------------------------------------------------------
    def next(self) -> "Cycle":
        return Cycle.from_effective(self.effective + _ONE_CYCLE)

    def prev(self) -> "Cycle":
        return Cycle.from_effective(self.effective - _ONE_CYCLE)

    # -- queries ------------------------------------------------------------
    def contains(self, on: date) -> bool:
        return self.effective <= on < self.expiration

    def days_until_expiry(self, on: date | None = None) -> int:
        """Whole days from ``on`` (default today) until expiration.

        ``0`` on the expiration date itself; negative once expired.
        """
        on = on or date.today()
        return (self.expiration - on).days

    def is_expired(self, on: date | None = None) -> bool:
        on = on or date.today()
        return on >= self.expiration

    def is_current(self, on: date | None = None) -> bool:
        return self.contains(on or date.today())

    # -- display ------------------------------------------------------------
    def label(self) -> str:
        """e.g. ``2610  effective 2026-10-01  expires 2026-10-29``."""
        return (
            f"{self.ident}  effective {self.effective.isoformat()}  "
            f"expires {self.expiration.isoformat()}"
        )

    def __str__(self) -> str:
        return self.ident


def cycle_from_date(on: date) -> Cycle:
    """The cycle whose validity interval contains ``on``."""
    k = (on - ANCHOR_DATE).days // CYCLE_DAYS  # floor division -> effective <= on
    return Cycle.from_effective(ANCHOR_DATE + timedelta(days=k * CYCLE_DAYS))


def current_cycle(on: date | None = None) -> Cycle:
    """The cycle in effect on ``on`` (default: today)."""
    return cycle_from_date(on or date.today())


def cycle_from_ident(ident: str) -> Cycle:
    """Parse a ``YYNN`` ident into a :class:`Cycle`.

    Raises ``ValueError`` for a malformed ident, an out-of-range ordinal, or an
    ordinal that does not exist in that year (e.g. a 14th cycle in a 13-cycle
    year).
    """
    m = _IDENT_RE.match(ident.strip())
    if not m:
        raise ValueError(f"not a YYNN AIRAC ident: {ident!r}")
    yy, nn = int(m.group(1)), int(m.group(2))
    if not 1 <= nn <= 14:
        raise ValueError(f"AIRAC ordinal out of range in {ident!r}")
    year = 2000 + yy
    eff = _first_effective_of_year(year) + timedelta(days=(nn - 1) * CYCLE_DAYS)
    cycle = Cycle.from_effective(eff)
    if cycle.ident != f"{yy:02d}{nn:02d}":
        raise ValueError(
            f"AIRAC {ident!r} does not exist (that year has fewer cycles); "
            f"nearest is {cycle.ident}"
        )
    return cycle
