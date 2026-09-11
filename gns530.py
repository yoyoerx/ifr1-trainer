"""gns530.py - the Garmin GNS 530 view over the shared :class:`gpsnav.GpsNav`.

The avionics state machine lives in ``gpsnav.py`` and is identical between the
GNS 530 and the GNS 430 (see ``gns430.py``). This module is a thin wrapper that
binds :data:`gpsnav.VARIANT_530` (screen size, visible-row count, bezel art) to
the core, and re-exports the shared types so existing ``from gns530 import ...``
call sites keep working.
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
    VARIANT_530,
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
    "Gns530",
    "PAGE_GROUPS",
]


class Gns530(GpsNav):
    """GpsNav bound to the GNS 530 faceplate / screen layout."""

    def __init__(self, db, *, today: date | None = None):
        super().__init__(db, today=today, variant=VARIANT_530)
