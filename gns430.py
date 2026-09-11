"""gns430.py - the Garmin GNS 430 view over the shared :class:`gpsnav.GpsNav`.

Identical avionics to the GNS 530 (see ``gpsnav.py`` / ``gns530.py``); the 430
is the shorter-height unit - same width, roughly half the screen, so it shows
far fewer data / flight-plan rows and uses its own (narrower) faceplate art.
Only :data:`gpsnav.VARIANT_430` differs; the state machine does not fork.
"""

from __future__ import annotations

from datetime import date

from gpsnav import (
    DirectTo,
    FlightPlan,
    GpsNav,
    NavState,
    PageCursor,
    PlanWaypoint,
    VARIANT_430,
    Variant,
    PAGE_GROUPS,
)

__all__ = [
    "PlanWaypoint",
    "FlightPlan",
    "DirectTo",
    "NavState",
    "PageCursor",
    "Variant",
    "Gns430",
    "PAGE_GROUPS",
]


class Gns430(GpsNav):
    """GpsNav bound to the GNS 430 faceplate / screen layout."""

    def __init__(self, db, *, today: date | None = None):
        super().__init__(db, today=today, variant=VARIANT_430)
