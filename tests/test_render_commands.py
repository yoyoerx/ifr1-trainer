"""render_commands.py is the Android/Chaquopy draw-command-list producer
(§3.6, docs/ANDROID_PORT_PLAN.md) - pure stdlib + math, no pygame, exercised
here without any Android/Kotlin involved, exactly like every other pure
module in this repo.
"""

from autopilot import Autopilot
from gns530 import Gns530
from gpsnav import NavState
from instruments import CDI, DME, BearingPointer, Markers, NavHead, Ownship, Panel
from navdata.model import NavDatabase, VhfNavaid, Waypoint
from navmath import Point
from render_commands import ap_panel_commands, gns_commands, hsi_commands


def _panel(hdg=45.0):
    return Panel(
        cdi=CDI(valid=True, deflection=0.3, to_from="TO", full_scale_deg=2.0, source="GPS"),
        hsi_heading_deg=hdg,
        bearing1=BearingPointer(valid=False), bearing2=BearingPointer(valid=False),
        dme=DME(), markers=Markers(),
    )


def test_hsi_commands_returns_nonempty_delimited_string():
    nav1 = NavHead(valid=True, ident="OOO", course_deg=90.0, deflection=0.4, to_from="TO")
    s = hsi_commands(10, 10, 300, 300, nav1, _panel(), 1.0)
    assert s, "expected a non-empty command string"
    lines = s.splitlines()
    assert len(lines) > 10
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    for line in lines:
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]


def test_hsi_commands_handles_no_receiver():
    """nav1=None (no VOR/LOC tuned) must not raise - render.py's draw_hsi_head
    draws the card/heading readout regardless, no receiver info column."""
    s = hsi_commands(0, 0, 200, 200, None, _panel(), 0.0)
    assert s
    assert "VOR" not in s and "LOC" not in s


def test_hsi_commands_heading_readout_reflects_panel():
    s = hsi_commands(0, 0, 200, 200, None, _panel(hdg=270.0), 0.0)
    assert "|270" in s


def test_ap_panel_commands_returns_nonempty_delimited_string():
    ap = Autopilot()
    s = ap_panel_commands(10, 10, 400, 90, ap, 1.0)
    assert s
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    for line in s.splitlines():
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]


def test_ap_panel_commands_hdg_key_lights_when_engaged():
    ap = Autopilot()
    ap.engage()
    ap.press_hdg()
    s = ap_panel_commands(0, 0, 400, 90, ap, 0.0)
    assert "|HDG" in s


def test_ap_panel_commands_handles_no_autopilot():
    s = ap_panel_commands(0, 0, 400, 90, None, 0.0)
    assert "no autopilot" in s


def _db():
    d = NavDatabase(source="test")
    d.add_waypoint(Waypoint("ALFA", Point(40.0, -74.0)))
    d.add_waypoint(Waypoint("BRAVO", Point(40.5, -74.0)))
    d.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))
    return d


def _own():
    return Ownship(Point(40.0, -74.0), 90.0, 88.0, 120.0, 3000.0, 0.0)


def _empty_panel():
    return Panel(
        cdi=CDI(), hsi_heading_deg=0.0,
        bearing1=BearingPointer(valid=False), bearing2=BearingPointer(valid=False),
        dme=DME(), markers=Markers(),
    )


def test_gns_commands_all_commands_stay_within_the_box():
    gns = Gns530(_db())
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO",
                    dtk=90.0, dist_nm=12.3, xtk_nm=0.5, next_dtk=100.0, wpt_alert=True)
    panel = _empty_panel()
    x, y, w, h = 10.0, 10.0, 480.0, 280.0
    s = gns_commands(x, y, w, h, gns, nav, _own(), panel, 0.0)
    assert s
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    max_y = 0.0
    for line in s.splitlines():
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]
        if op == "T":
            max_y = max(max_y, float(fields[1]))
        elif op == "L":
            max_y = max(max_y, float(fields[1]), float(fields[3]))
        elif op == "R":
            max_y = max(max_y, float(fields[1]) + float(fields[3]))
        elif op == "C":
            max_y = max(max_y, float(fields[1]))
        elif op == "P":
            pts = fields[2].split(";")
            max_y = max(max_y, *(float(p.split(",")[1]) for p in pts))
    assert max_y <= y + h, "a draw command escaped the passed h bound"


def test_gns_commands_shows_to_from_idents():
    gns = Gns530(_db())
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "ALFA" in s
    assert "BRAVO" in s


def test_gns_commands_no_active_leg_shows_dashes():
    gns = Gns530(_db())
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "----" in s


def test_gns_commands_obs_annunciation_when_active():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO"])
    gns.set_obs(45.0)
    nav = NavState(valid=True, mode="OBS")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "OBS 045" in s
