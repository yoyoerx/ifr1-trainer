"""render_commands.py is the Android/Chaquopy draw-command-list producer
(§3.6, docs/ANDROID_PORT_PLAN.md) - pure stdlib + math, no pygame, exercised
here without any Android/Kotlin involved, exactly like every other pure
module in this repo.
"""

from autopilot import Autopilot
from gns530 import Gns530
from gpsnav import PAGE_GROUPS, DirectToEntry, FplMenu, NavState, ProcSelect, VnavProfile
from instruments import CDI, DME, BearingPointer, Markers, NavHead, Ownship, Panel
from navdata.model import Airport, Airspace, NavDatabase, VhfNavaid, Waypoint
from navmath import Point
from render_commands import ap_panel_commands, gns_commands, hsi_commands, map_commands


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
    d.add_waypoint(Waypoint("CHAR", Point(41.0, -74.0)))
    d.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))
    d.add_airport(Airport("KTST", Point(40.0, -74.0), longest_runway_ft=5000,
                            comms={"TWR": [118.5]}))
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


def _to_flight_plan_page(gns) -> None:
    gns.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")


def test_gns_commands_flight_plan_page_lists_waypoints_within_bounds():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    _to_flight_plan_page(gns)
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    x, y, w, h = 10.0, 10.0, 480.0, 280.0
    s = gns_commands(x, y, w, h, gns, nav, _own(), _empty_panel(), 0.0)
    assert "Flight Plan" in s
    assert "ALFA" in s and "BRAVO" in s and "CHAR" in s
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    max_y = 0.0
    for line in s.splitlines():
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]
        if op == "T":
            max_y = max(max_y, float(fields[1]))
        elif op == "R":
            max_y = max(max_y, float(fields[1]) + float(fields[3]))
    assert max_y <= y + h, "a draw command escaped the passed h bound"


def test_gns_commands_flight_plan_active_leg_marker():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    _to_flight_plan_page(gns)
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "-> BRAVO" in s


def test_gns_commands_flight_plan_empty_shows_message():
    gns = Gns530(_db())
    _to_flight_plan_page(gns)
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "no flight plan" in s


def _to_vnav_page(gns) -> None:
    gns.cursor.page = PAGE_GROUPS["NAV"].index("VNAV")


def _to_navcom_page(gns) -> None:
    gns.cursor.page = PAGE_GROUPS["NAV"].index("NAV/COM")


def test_gns_commands_vnav_page_shows_armed_target_and_status():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    own = _own()
    gns.update(own.pos, own.track_deg, own.gs_kt)  # establishes gns._pos + the active leg VnavStatus needs
    gns.vnav = VnavProfile(target_ident="BRAVO", target_alt_ft=1000.0, vs_fpm=-500.0, armed=True)
    _to_vnav_page(gns)
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    x, y, w, h = 10.0, 10.0, 480.0, 280.0
    s = gns_commands(x, y, w, h, gns, nav, _own(), _empty_panel(), 0.0)
    assert "BRAVO" in s
    assert "VNV ARMED" in s
    assert "DIS " in s
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    max_y = 0.0
    for line in s.splitlines():
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]
        if op == "T":
            max_y = max(max_y, float(fields[1]))
    assert max_y <= y + h, "a draw command escaped the passed h bound"


def test_gns_commands_vnav_page_unarmed_shows_no_target_message():
    gns = Gns530(_db())
    _to_vnav_page(gns)
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "no active VNAV target" in s


def test_gns_commands_navcom_page_no_airport_shows_message():
    """The loaded flight plan (ALFA/BRAVO, both plain waypoints) has no
    airport along it - `navcom_airport()` only considers airports actually
    in the route, so this exercises the "nothing to tune" branch even
    though `_db()`'s KTST airport exists elsewhere in the database."""
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO"])
    _to_navcom_page(gns)
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "no airport in flight plan" in s


def _to_wpt_page(gns, page: str) -> None:
    group = list(PAGE_GROUPS).index("WPT")
    gns.cursor.group = group
    gns.cursor.page = PAGE_GROUPS["WPT"].index(page)


def _to_nrst_page(gns, page: str) -> None:
    group = list(PAGE_GROUPS).index("NRST")
    gns.cursor.group = group
    gns.cursor.page = PAGE_GROUPS["NRST"].index(page)


def _to_aux_page(gns, page: str) -> None:
    group = list(PAGE_GROUPS).index("AUX")
    gns.cursor.group = group
    gns.cursor.page = PAGE_GROUPS["AUX"].index(page)


def test_gns_commands_fpl_catalog_page_lists_slots():
    gns = Gns530(_db())
    gns.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan Catalog")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "-- empty --" in s


def test_gns_commands_wpt_airport_page_shows_looked_up_airport():
    """The looked-up ident is drawn as one T command per character cell
    (matching render.py's per-cell underline-cursor layout), not as one
    contiguous "KTST" string - check the cells and the looked-up record's
    own fields instead."""
    gns = Gns530(_db())
    gns.wpt_entry.chars = list("KTST")
    _to_wpt_page(gns, "Airport")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    cells = [ln.split("|")[-1] for ln in s.splitlines() if ln.startswith("T|") and ln.split("|")[-1] in "KTST"]
    assert cells[:4] == ["K", "T", "S", "T"]
    assert "RWY  5000 ft" in s
    assert "TWR  118.5" in s


def test_gns_commands_wpt_airport_page_no_match_shows_hint():
    gns = Gns530(_db())
    _to_wpt_page(gns, "Airport")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "knob: enter identifier" in s


def test_gns_commands_nrst_apt_page_shows_nearest_airport():
    gns = Gns530(_db())
    gns.update(Point(40.0, -74.0), 0.0, 100.0)  # establishes gns._pos for nearest_for_page
    _to_nrst_page(gns, "Nearest APT")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "KTST" in s


def test_gns_commands_nrst_apt_page_none_within_range_shows_message():
    gns = Gns530(NavDatabase(source="test"))  # no airports at all
    gns.update(Point(40.0, -74.0), 0.0, 100.0)
    _to_nrst_page(gns, "Nearest APT")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "none within range" in s


def test_gns_commands_aux_navdata_page_shows_db_counts():
    gns = Gns530(_db())
    _to_aux_page(gns, "Nav Data")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "SOURCE  test" in s
    assert "APT  1" in s


def test_gns_commands_aux_trip_page_shows_totals():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    _to_aux_page(gns, "Trip Planning")
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "ALFA -> CHAR" in s
    assert "TOTAL DIS" in s


def test_gns_commands_aux_utility_page_shows_flight_timer():
    gns = Gns530(_db())
    _to_aux_page(gns, "Utility")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0, t=3725.0)
    assert "FLIGHT TIMER" in s
    assert "01:02:05" in s


def test_gns_commands_aux_setup_page_shows_unit_and_baro():
    gns = Gns530(_db())
    _to_aux_page(gns, "Setup")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0, baro_inhg=29.87)
    assert "GNS 530" in s
    assert "29.87 in" in s


def test_gns_commands_proc_dialog_shows_title_and_options():
    gns = Gns530(_db())
    gns._proc_dialog = ProcSelect(airport="KTST", step="MENU", options=["Select Approach", "Select Arrival"])
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "PROCEDURES  KTST" in s
    assert "Select Approach" in s


def test_gns_commands_activate_leg_dialog_shows_leg():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    gns._leg_confirm = {"row": 2}
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "ACTIVATE LEG" in s
    assert "BRAVO -> CHAR" in s


def test_gns_commands_remove_confirm_dialog_shows_waypoint():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    gns._remove_confirm = {"kind": "waypoint", "row": 1}
    nav = NavState(valid=True, mode="LEG", from_ident="ALFA", to_ident="BRAVO")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "REMOVE WAYPOINT" in s
    assert "Yes?" in s


def test_gns_commands_restart_confirm_dialog_shows_prompt():
    gns = Gns530(_db())
    gns._restart_confirm = {"vtf": False}
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "RESTART APPROACH" in s
    assert "ENT=restart  CLR=cancel" in s


def test_gns_commands_dto_menu_dialog_shows_options():
    gns = Gns530(_db())
    gns._dto_menu = FplMenu(options=["CANCEL DIRECT-TO NAV?"])
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "CANCEL DIRECT-TO NAV?" in s


def test_gns_commands_direct_to_dialog_shows_resolved_match():
    gns = Gns530(_db())
    gns._dto_dialog = DirectToEntry.seeded("BRAVO")
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "DIRECT TO  ->" in s
    assert "BRAVO  Waypoint" in s
    assert "knob=char/cursor  ENT=confirm  CLR=x" in s


def test_gns_commands_message_dialog_shows_pending_messages():
    gns = Gns530(_db())
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0,
                       messages=["ARRIVING AT WAYPOINT"], show_messages=True)
    assert "MESSAGES" in s
    assert "ARRIVING AT WAYPOINT" in s


def test_gns_commands_message_dialog_no_messages_shows_placeholder():
    gns = Gns530(_db())
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0, show_messages=True)
    assert "no messages" in s


def test_gns_commands_fpl_menu_dialog_shows_options():
    gns = Gns530(_db())
    gns._fpl_menu = FplMenu()
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "INVERT FLT PLAN" in s


def test_gns_commands_airspace_info_dialog_shows_class_and_altitudes():
    gns = Gns530(_db())
    ring = (Point(39.9, -74.1), Point(39.9, -73.9), Point(40.1, -73.9), Point(40.1, -74.1))
    aw = Airspace(ident="KTST", name="Test Airspace", cls="D", floor_ft=0, ceiling_ft=2500, rings=(ring,))
    from gpsnav import AirspaceInfo

    gns._airspace_info = AirspaceInfo(airspace=aw)
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "AIRSPACE INFORMATION" in s
    assert "CLASS D" in s
    assert "SFC - 2500ft" in s


def test_map_commands_returns_well_formed_commands():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    s = map_commands(10.0, 10.0, 480.0, 400.0, gns, _own(), _db(), map_range_nm=20.0)
    assert s
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    for line in s.splitlines():
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]


def test_map_commands_shows_flight_plan_waypoint_idents():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    s = map_commands(0.0, 0.0, 480.0, 400.0, gns, _own(), _db(), map_range_nm=80.0)
    assert "ALFA" in s
    assert "BRAVO" in s
    assert "CHAR" in s


def test_map_commands_shows_nearby_vor_symbol():
    """_db()'s "OOO" VOR sits right on top of ownship's start position -
    always within range regardless of map_range_nm, so it's a reliable
    "is the nearby-fixes layer wired up at all" check."""
    gns = Gns530(_db())
    s = map_commands(0.0, 0.0, 480.0, 400.0, gns, _own(), _db(), map_range_nm=20.0)
    assert "OOO" in s


def test_map_commands_handles_no_db():
    gns = Gns530(_db())
    s = map_commands(0.0, 0.0, 480.0, 400.0, gns, _own(), None, map_range_nm=20.0)
    assert s   # ownship/range rings still draw with no nearby-fixes layer


def test_map_commands_shows_dto_course():
    gns = Gns530(_db())
    gns.load_flight_plan(["ALFA", "BRAVO"])
    gns.direct_to("BRAVO")
    s = map_commands(0.0, 0.0, 480.0, 400.0, gns, _own(), _db(), map_range_nm=80.0)
    assert "P|" in s or "L|" in s   # the magenta DTO course line, plus ownship's polygon


def _to_aux_charts_page(gns) -> None:
    gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    gns.cursor.page = PAGE_GROUPS["AUX"].index("Charts")


def test_gns_commands_aux_charts_page_no_flight_plan_shows_message():
    """No flight-plan airports means _aux_charts_body returns before ever
    touching datasrc.dtpp's on-disk index/cache - the one AUX Charts case
    this pure-stdlib test can exercise without a real chart index fetched
    (androidbridge/charts.py, §3.4/§3.5) on disk somewhere."""
    gns = Gns530(_db())
    _to_aux_charts_page(gns)
    nav = NavState(valid=False, mode="NOWPT")
    s = gns_commands(0, 0, 480, 280, gns, nav, _own(), _empty_panel(), 0.0)
    assert "CHARTS" in s
    assert "no flight-plan airports" in s
