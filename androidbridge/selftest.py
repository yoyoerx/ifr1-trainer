"""Phase 0 self-test entry point.

Called from Kotlin (`BrainBridge.selfTest()`) to prove the brain modules
import and tick together on-device — the Python-side half of the §3.4 spike
in `docs/ANDROID_PORT_PLAN.md` (the USB-HID half is §3.1, separate). It's
also run right now, with no Android involved, by `tests/test_androidbridge.py`
— the exact code path Kotlin will eventually call is already exercised by
the desktop suite.
"""

from __future__ import annotations

from navdata.model import NavDatabase, VhfNavaid, Waypoint
from navmath import Point

from . import TrainerSession


def run() -> str:
    db = NavDatabase(source="android-selftest")
    db.add_waypoint(Waypoint("ALFA", Point(40.0, -74.0)))
    db.add_waypoint(Waypoint("BRAVO", Point(40.5, -74.0)))
    db.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))

    session = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    failed = session.load_flight_plan(["ALFA", "BRAVO"])
    if failed:
        return f"FAIL: unresolved waypoints {failed}"

    snap = session.tick(1.0)
    return (
        "OK: navmath/navdata/gpsnav/sim_model/androidbridge imported and "
        f"ticked - to={snap['nav']['to_ident']!r} "
        f"pos=({snap['pos_lat']:.4f},{snap['pos_lon']:.4f})"
    )
