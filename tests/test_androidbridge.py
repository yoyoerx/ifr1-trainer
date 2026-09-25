"""androidbridge is Phase 0 scaffolding for the Android port (see
docs/ANDROID_PORT_PLAN.md) - it's plain stdlib Python with no Android/
Chaquopy dependency of its own, so it's exercised here exactly like any
other pure module, without needing an Android device or emulator.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navdata.model import NavDatabase, VhfNavaid, Waypoint  # noqa: E402
from androidbridge import TrainerSession  # noqa: E402

ALFA = (40.0, -74.0)
BRAVO = (40.5, -74.0)
CHAR = (41.0, -74.0)


@pytest.fixture
def db():
    d = NavDatabase(source="test")
    d.add_waypoint(Waypoint("ALFA", _pt(ALFA)))
    d.add_waypoint(Waypoint("BRAVO", _pt(BRAVO)))
    d.add_waypoint(Waypoint("CHAR", _pt(CHAR)))
    d.add_vhf(VhfNavaid("OOO", _pt((40.25, -74.0)), 113.0))
    return d


def _pt(latlon):
    from navmath import Point

    return Point(*latlon)


def test_session_ticks_and_returns_plain_data(db):
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    snap = s.tick(1.0)
    # every value must be a plain type - this is the whole point of the
    # bridge: nothing here should be a dataclass/Point instance
    for key in ("pos_lat", "pos_lon", "heading_deg", "track_deg", "gs_kt",
                "altitude_ft"):
        assert isinstance(snap[key], float)
    assert isinstance(snap["messages"], list)
    assert isinstance(snap["nav"], dict)
    assert isinstance(snap["nav"]["annunciators"], list)


def test_session_flies_a_loaded_plan_like_the_desktop_core_does(db):
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    failed = s.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    assert failed == []

    # command a heading toward the plan and step until it starts sequencing -
    # mirrors test_gpsnav.py's scenario shape, just through the bridge
    s.command(heading_deg=0.0)
    snap = None
    for _ in range(200):
        snap = s.tick(5.0)
    assert snap["nav"]["to_ident"] in ("BRAVO", "CHAR")


def test_session_drains_the_message_queue_each_tick(db):
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    s.world.gns.messages.append("TEST MESSAGE")
    snap = s.tick(1.0)
    assert "TEST MESSAGE" in snap["messages"]
    snap2 = s.tick(1.0)
    assert snap2["messages"] == []   # drained, not re-delivered


def test_dispatch_event_tunes_com1_standby_like_route_event_does(db):
    """Phase 1: dispatch_event feeds main.route_event directly - this is the
    exact mode-routing table docs/ANDROID_PORT_PLAN.md §3.1 documents, now
    reachable from Kotlin's Ifr1Event fields instead of only from a real
    ifr1.IFR1 device on desktop."""
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    before = s.world.radios.com1.standby_mhz
    s.dispatch_event("COM1", "", "", 1, 0, True, "")
    after = s.world.radios.com1.standby_mhz
    assert after != before


def test_dispatch_event_shift_latch_then_nav1_obs(db):
    """KNOB press in a shift-capable mode latches shift (main.py's
    `_SHIFT_FN`); NAV1's shifted knob then turns the OBS/CRS card, not the
    tuning knob - same behavior as desktop's route_event."""
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    s.dispatch_event("NAV1", "", "", 0, 0, True, "")       # mode selector lands on NAV1
    s.dispatch_event("NAV1", "KNOB", "", 0, 0, False, "")  # latch shift
    assert s.world.shift_latched is True

    before_obs = s.world.radios.nav1.obs_deg
    before_freq = s.world.radios.nav1.standby_mhz
    s.dispatch_event("NAV1", "", "", 1, 0, False, "")      # shifted -> OBS, not tuning
    assert s.world.radios.nav1.obs_deg != before_obs
    assert s.world.radios.nav1.standby_mhz == before_freq


def test_dispatch_event_ap_row_button_drives_autopilot(db):
    """AP-row buttons drive the autopilot in every non-FMS mode - here COM1,
    same as `route_event`'s documented behavior."""
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    assert s.world.ap.lateral.name != "HDG"
    s.dispatch_event("COM1", "HDG", "", 0, 0, False, "")
    assert s.world.ap.lateral.name == "HDG"


def test_dispatch_event_xpdr_ident_pulse(db):
    s = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    assert s.world.ident_timer == 0.0
    s.dispatch_event("XPDR", "SWAP", "", 0, 0, True, "")
    assert s.world.ident_timer > 0.0


def test_selftest_entry_point_is_what_kotlin_will_call():
    """This is the exact function BrainBridge.kt calls via Chaquopy
    (android/app/src/main/java/.../bridge/BrainBridge.kt) - exercised here
    with no Android involved at all."""
    from androidbridge.selftest import run

    result = run()
    assert result.startswith("OK:")
    assert "to='BRAVO'" in result
