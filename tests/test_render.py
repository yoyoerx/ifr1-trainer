"""Headless smoke tests for render.py + the main.py loop plumbing.

These don't check pixels - they check that a full frame draws without raising
and that one simulation tick produces a coherent panel. Real visual work is
eyeballed by running ``python main.py``.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from navmath import Point, destination, great_circle_nm, initial_bearing, norm360, reciprocal  # noqa: E402
from navdata.model import Airport, NavDatabase, Runway, VhfNavaid, Waypoint  # noqa: E402
import gns530 as gns530_mod  # noqa: E402
import gns430 as gns430_mod  # noqa: E402
import instruments as instr  # noqa: E402
import sim_model as simmod  # noqa: E402
from autopilot import Autopilot  # noqa: E402
from radios import RadioStack  # noqa: E402
import render  # noqa: E402
from render import (Renderer, Scene, _hold_track_points, _pt_symbol_points,  # noqa: E402
                    draw_ap_panel)
import main as main_mod  # noqa: E402
from gpsnav import PlanWaypoint  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _pygame():
    pygame.init()
    pygame.display.set_mode((1000, 640))
    yield
    pygame.quit()


@pytest.fixture
def db():
    d = NavDatabase(source="test")
    d.add_waypoint(Waypoint("ALFA", Point(40.0, -74.0)))
    d.add_waypoint(Waypoint("BRAVO", Point(40.5, -74.0)))
    d.add_waypoint(Waypoint("CHAR", Point(41.0, -74.0)))
    d.add_vhf(VhfNavaid("OMN", Point(40.3, -73.8), 113.0, magvar_deg=-13.0))
    return d


def _scene(db):
    g = gns530_mod.Gns530(db)
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    a, b = db.find("ALFA")[0].pos, db.find("BRAVO")[0].pos
    brg = initial_bearing(a, b)
    sim = simmod.SimModel(pos=destination(a, brg, 2.0), heading_deg=brg, tas_kt=130.0)
    sim.command(altitude=5000.0)
    nav, panel, st = main_mod.step_once(g, sim, 0.1, autopilot=True, magvar=-13.0)
    nearby = [(n.ident, n.pos, "navaid") for n in [db.find("OMN")[0]]]
    return Scene(own=st, nav=nav, panel=panel, gns=g, db=db, magvar=-13.0,
                 map_range_nm=20.0, autopilot=True, fps=30.0, nearby=nearby)


def test_full_frame_draws_without_error(db):
    surf = pygame.display.get_surface()
    Renderer(surf).draw(_scene(db))
    # a non-trivial number of non-background pixels got written
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000


def test_draw_shows_a_warp_annunciator_only_above_1x(db):
    import dataclasses
    surf = pygame.display.get_surface()
    sc1 = _scene(db)
    Renderer(surf).draw(sc1)
    arr1 = pygame.surfarray.array2d(surf).copy()

    sc5 = dataclasses.replace(_scene(db), time_warp=5)
    Renderer(surf).draw(sc5)
    arr5 = pygame.surfarray.array2d(surf)
    # the annunciator strip (top 24 px) differs once "WARP 5x" is drawn there
    assert not (arr1[:, :24] == arr5[:, :24]).all()


def test_draw_is_stable_over_many_frames(db):
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    sc = _scene(db)
    for _ in range(30):
        r.draw(sc)


def test_draw_handles_no_active_leg(db):
    g = gns530_mod.Gns530(db)            # empty flight plan
    sim = simmod.SimModel(pos=Point(40.0, -74.0), heading_deg=90.0)
    nav, panel, st = main_mod.step_once(g, sim, 0.1, autopilot=True, magvar=0.0)
    Renderer(pygame.display.get_surface()).draw(
        Scene(own=st, nav=nav, panel=panel, gns=g, db=db))


def test_hold_track_points_form_a_closed_racetrack():
    """The map's hold symbol - a real racetrack, not a bare ring (playtest
    round 3): starts/ends at the fix, and the outbound leg's far end and its
    parallel inbound-leg counterpart both sit ``leg_nm`` out / ~2x the turn
    radius apart, on the turn side. ``arc_steps=1`` collapses each turn to
    its two endpoints (no intermediate arc points) for a simple 5-point shape."""
    fix = Point(40.0, -74.0)
    pts = _hold_track_points(fix, inbound_true=0.0, turn="R", leg_nm=4.0, arc_steps=1)
    fix2, far, far_off, near_off, fix3 = pts
    assert fix2 == fix and fix3 == fix
    # both ~4 nm from the fix (along the racetrack's long axis)
    assert great_circle_nm(fix, far) == pytest.approx(4.0, abs=0.05)
    # far_off is offset both along (4 nm) and across (2 nm) from the fix
    assert great_circle_nm(fix, far_off) == pytest.approx((4.0 ** 2 + 2.0 ** 2) ** 0.5, abs=0.1)
    # turning right *at the fix* to reverse from inbound (000) to outbound
    # (180) sweeps through east (000 -> 090 -> 180), so a right-turn hold's
    # parallel leg sits east of the inbound course, offset by ~2x the
    # stylized turn radius
    assert far_off.lon > far.lon
    assert near_off.lon > fix.lon
    assert great_circle_nm(far, far_off) == pytest.approx(2.0, abs=0.1)


def test_hold_track_points_mirror_for_left_turns():
    fix = Point(40.0, -74.0)
    r_pts = _hold_track_points(fix, inbound_true=0.0, turn="R", leg_nm=4.0, arc_steps=1)
    l_pts = _hold_track_points(fix, inbound_true=0.0, turn="L", leg_nm=4.0, arc_steps=1)
    far_off_r, far_off_l = r_pts[2], l_pts[2]
    # R turns offset the parallel leg east, L turns offset it west
    assert far_off_r.lon > fix.lon
    assert far_off_l.lon < fix.lon


def test_pt_symbol_points_point_along_inbound_course():
    """The procedure-turn chevron (playtest round 3: "PT not drawn on the
    map") has its vertex at the fix and both arms trailing behind it -
    toward the outbound (reciprocal) course, not out ahead of it."""
    tip = Point(40.0, -74.0)
    left, vertex, right = _pt_symbol_points(tip, inbound_true=0.0)
    assert vertex == tip
    # inbound 0 (north) -> arms trail south (higher latitude = further north)
    assert left.lat < tip.lat and right.lat < tip.lat
    assert left.lon < tip.lon < right.lon           # symmetric either side


def test_map_draws_a_hold_racetrack_and_a_pt_chevron(db):
    g = gns530_mod.Gns530(db)
    hold_fix = PlanWaypoint("DALAC", Point(40.6, -74.0), "wpt", hold=True,
                             hold_inbound_true=0.0, hold_turn="R", hold_leg_nm=3.0)
    pt_fix = PlanWaypoint("PT", Point(40.8, -74.0), "wpt", synthetic=True,
                           hold_inbound_true=90.0, hold_turn="L")
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.fpl.insert(1, hold_fix)
    g.fpl.insert(2, pt_fix)
    a, b = db.find("ALFA")[0].pos, db.find("BRAVO")[0].pos
    sim = simmod.SimModel(pos=a, heading_deg=initial_bearing(a, b), tas_kt=130.0)
    nav, panel, st = main_mod.step_once(g, sim, 0.1, autopilot=True, magvar=0.0)
    sc = Scene(own=st, nav=nav, panel=panel, gns=g, db=db, map_range_nm=40.0)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)          # must not raise


def test_bundled_fonts_present_and_lcd_renders(db):
    from pathlib import Path
    import render as render_mod
    for f in ("B612Mono-Regular.ttf", "DSEG7Classic-Regular.ttf"):
        assert (render_mod._FONT_DIR / f).is_file(), f"missing bundled font {f}"
    r = Renderer(pygame.display.get_surface())
    rect = r.lcd("118.000", 10, 10, color=(0, 255, 0))
    assert rect.width > 0 and rect.height > 0


def test_draw_message_page_obs_and_turn_advisory(db):
    sc = _scene(db)
    sc.gns.toggle_obs()                       # OBS course shown on the 530 screen
    sc.gns.messages.append("NAV DATA EXPIRED 01-01-26 - VERIFY ALL DATA")
    sc.messages = sc.gns.peek_messages()
    sc.show_messages = True
    # force a turn advisory
    import dataclasses
    sc.nav = dataclasses.replace(sc.nav, wpt_alert=True, turn_now=True, next_dtk=95.0)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000


def test_draw_direct_to_page_and_shift_hint(db):
    from ifr1 import Event, Mode
    sc = _scene(db)
    sc.gns.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))     # opens the D-T page
    sc.gns._dto_dialog.chars[:] = list("CHAR  ")
    sc.shift_hint = "SHIFT CRS1"
    sc.selector_mode = "NAV1"
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000


def test_draw_direct_to_page_shows_activate_prompt_after_the_first_ent(db):
    from ifr1 import Event, Mode
    sc = _scene(db)
    sc.gns.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    sc.gns._dto_dialog.chars[:] = list("CHAR  ")
    sc.gns.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert sc.gns._dto_dialog.confirming
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000


def test_draw_wpt_and_nrst_pages(db):
    from ifr1 import Event, Mode
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    g = sc.gns
    # WPT / VOR page with an identifier typed
    g.cursor.group = list(PAGE_GROUPS).index("WPT")
    g.cursor.page = PAGE_GROUPS["WPT"].index("VOR")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.wpt_entry.chars[:] = list("OMN   ")
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000
    # NRST / APT page
    g.cursor.group = list(PAGE_GROUPS).index("NRST")
    g.cursor.page = 0
    Renderer(surf).draw(sc)


def test_draw_remove_waypoint_confirmation_page(db):
    from ifr1 import Event, Mode
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    g = sc.gns
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g._fpl_edit["row"] = 1
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g._remove_confirm is not None
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_draw_remove_approach_confirmation_and_restart_approach_pages(db):
    from ifr1 import Event, Mode
    from navdata.model import LegType, Procedure, ProcedureLeg
    from gns530 import PAGE_GROUPS
    db.add_airport(Airport("KEND", Point(41.5, -74.0)))
    db.add_procedure(Procedure(
        airport="KEND", ident="I05", kind="approach", route_type="I",
        transitions={"": (ProcedureLeg(10, LegType.IF, fix_ident="ALFA", is_iaf=True),
                          ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
                          ProcedureLeg(30, LegType.CF, fix_ident="CHAR", is_map=True))},
    ))
    sc = _scene(db)
    g = sc.gns
    g.load_procedure("KEND", "I05", None)
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)                          # procedure title row on the FPL page
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    g._fpl_menu.sel = g._fpl_menu.options.index("REMOVE APPROACH")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._remove_confirm is not None
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))          # cancel the removal
    assert g._remove_confirm is None
    iaf = next(i for i, w in enumerate(g.fpl.waypoints) if w.is_iaf)
    g.fpl.activate_leg(iaf)
    g.begin_proc_select()
    g._proc_dialog.sel = g._proc_dialog.options.index("ACTIVATE APPROACH")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._restart_confirm is not None
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_cdi_strip_service_label_does_not_overlap_the_source_label(db, monkeypatch):
    """A 530W's flight-phase annunciation (ENR/TERM/LPV/...) and the GPS/VLOC
    source label were both drawn at the left edge of the CDI strip, only 2px
    apart vertically - "TERM" visibly overlapped "GPS". They must now sit
    side by side with no overlap, for every string level_of_service() can
    actually produce. Captures the actual `_t()` calls `_cdi_strip` makes,
    rather than recomputing the same layout formula under test."""
    import dataclasses
    sc = _scene(db)
    r = Renderer(pygame.display.get_surface())
    strip = pygame.Rect(0, 0, 400, 40)
    calls = []
    orig_t = Renderer._t

    def recording_t(self, s, x, y, *args, **kwargs):
        calls.append((s, x, y, kwargs.get("font")))
        return orig_t(self, s, x, y, *args, **kwargs)

    monkeypatch.setattr(Renderer, "_t", recording_t)
    for svc in ("ENR", "TERM", "LPV", "L/VNAV", "LNAV+V", "LP+V", "LNAV", "MAPR"):
        calls.clear()
        cdi = dataclasses.replace(sc.panel.cdi, source="GPS", service=svc, valid=True)
        sc2 = dataclasses.replace(sc, panel=dataclasses.replace(sc.panel, cdi=cdi))
        r._cdi_strip(sc2, strip)
        by_text = {text: (x, y, font) for text, x, y, font in calls}
        assert "GPS" in by_text and svc in by_text
        sx, sy, sfont = by_text["GPS"]
        vx, vy, vfont = by_text[svc]
        src_rect = pygame.Rect(sx, sy, r.f_sm.size("GPS")[0], sfont.get_height())
        svc_rect = pygame.Rect(vx, vy, r.f_sm.size(svc)[0], vfont.get_height())
        assert not src_rect.colliderect(svc_rect), f"service={svc!r} overlaps the source label"


def test_draw_ground_status_banner(db):
    import dataclasses
    sc = _scene(db)
    surf = pygame.display.get_surface()
    for status in ("LANDED", "CRASHED"):
        sc2 = dataclasses.replace(sc, ground_status=status)
        Renderer(surf).draw(sc2)
        assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000
    sc3 = dataclasses.replace(sc, ground_status="")   # no banner, must not raise
    Renderer(surf).draw(sc3)


def test_draw_flight_plan_page(db):
    sc = _scene(db)
    sc.gns.cursor.group = list(__import__("render").PAGE_GROUPS).index("NAV") \
        if hasattr(__import__("render"), "PAGE_GROUPS") else 0
    # force the FPL sub-page via gns530's own page model
    from gns530 import PAGE_GROUPS
    sc.gns.cursor.group = list(PAGE_GROUPS).index("NAV")
    sc.gns.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    Renderer(pygame.display.get_surface()).draw(sc)


def test_nearby_declutters_small_airports_and_includes_ndb_vordme(db):
    """F78 (requested directly: "a lot of dinky airports" cluttering a PVD ->
    KJFK route). `main._nearby` used to pull the 6 nearest airports
    regardless of size and explicitly excluded NDBs (`ndb=False`) - now it
    classifies by `Airport.size_class` (Pilot's Guide p.36) and only shows
    Small airports within their own, tighter range, and includes NDBs and
    VOR-DME/VORTAC-tagged navaids too."""
    from navdata.model import NdbNavaid
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBIG", Point(40.0, -74.0), longest_runway_ft=10000))   # 0 nm, Large
    d.add_airport(Airport("KFAR", Point(40.0, -73.6), longest_runway_ft=2000))    # ~28 nm, Small, far
    d.add_airport(Airport("KNER", Point(40.02, -74.0), longest_runway_ft=2000))   # ~1.2 nm, Small, close
    d.add_vhf(VhfNavaid("VOR1", Point(40.1, -74.0), 113.0, nav_class="V LW"))     # plain VOR
    d.add_vhf(VhfNavaid("VDM1", Point(40.2, -74.0), 114.0, nav_class="VDHW"))     # VOR-DME
    d.add_ndb(NdbNavaid("NDB1", Point(40.15, -74.0), 350.0))

    nearby = main_mod._nearby(d, Point(40.0, -74.0), 20.0)
    by_ident = {ident: kind for ident, _, kind in nearby}
    assert by_ident["KBIG"] == "apt_large"
    assert by_ident["KNER"] == "apt_small"           # close enough - still shown
    assert "KFAR" not in by_ident                    # small and far - decluttered
    assert by_ident["VOR1"] == "vor"
    assert by_ident["VDM1"] == "vordme"
    assert by_ident["NDB1"] == "ndb"                 # NDBs no longer excluded outright


def test_map_draws_every_new_nearby_symbol_kind_without_error(db):
    """Smoke test for the new symbol-drawing dispatch (airport size classes,
    VOR/VOR-DME hexagons, NDB rings) - each kind must render without raising
    and actually put pixels on screen, not silently fall through to nothing."""
    import dataclasses
    sc = _scene(db)
    sc = dataclasses.replace(sc, nearby=[
        ("BIG", destination(sc.own.pos, 10.0, 3.0), "apt_large"),
        ("MED", destination(sc.own.pos, 30.0, 3.0), "apt_medium"),
        ("SML", destination(sc.own.pos, 50.0, 3.0), "apt_small"),
        ("VOR", destination(sc.own.pos, 70.0, 3.0), "vor"),
        ("VDM", destination(sc.own.pos, 90.0, 3.0), "vordme"),
        ("NDB", destination(sc.own.pos, 110.0, 3.0), "ndb"),
    ])
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_flight_plan_page_scrolls_to_keep_the_cursor_visible(db, monkeypatch):
    """Reported directly: a long flight plan (KBOS PVD KJFK plus the KJFK
    I22R approach's legs) never showed its later waypoints on the Flight
    Plan page, and scrolling the cursor down didn't reveal them either -
    `_draw_fpl` always drew from waypoint 0, with no scrolling at all (the
    NRST page had the same bug once, fixed the same way - see the comment
    at its own fix). Build a plan longer than the 530W's visible rows,
    scroll the cursor near the end, and confirm a late waypoint's ident
    actually gets drawn (not just present in the underlying flight plan)."""
    from ifr1 import Event, Mode
    d = NavDatabase(source="test")
    idents = [f"WP{i:02d}" for i in range(20)]
    for i, ident in enumerate(idents):
        d.add_waypoint(Waypoint(ident, Point(40.0 + i * 0.1, -74.0)))
    from gns530 import PAGE_GROUPS
    g = gns530_mod.Gns530(d)
    g.load_flight_plan(idents)
    assert len(g.fpl.waypoints) == 20 > 12          # longer than the 530W ever shows unscrolled
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))   # cursor on
    g._fpl_edit["row"] = 18                                     # scroll near the bottom

    a, b = d.find("WP00")[0].pos, d.find("WP01")[0].pos
    sim = simmod.SimModel(pos=a, heading_deg=initial_bearing(a, b), tas_kt=130.0)
    nav, panel, st = main_mod.step_once(g, sim, 0.1, autopilot=True, magvar=0.0)
    sc = Scene(own=st, nav=nav, panel=panel, gns=g, db=d, magvar=0.0,
              map_range_nm=20.0, autopilot=True, fps=30.0)

    r = Renderer(pygame.display.get_surface())
    calls = []
    orig_t = Renderer._t

    def recording_t(self, s, x, y, *args, **kwargs):
        calls.append(s)
        return orig_t(self, s, x, y, *args, **kwargs)

    monkeypatch.setattr(Renderer, "_t", recording_t)
    r._draw_fpl(sc, pygame.Rect(0, 0, 300, 200))     # the Flight Plan page itself, not the
    drawn = " ".join(calls)                          # always-from-top compact sidebar strip
    assert "WP18" in drawn                           # the cursor's own row is visible
    assert "WP00" not in drawn                        # scrolled well past the top


def test_draw_map_page_inside_the_screen(db):
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    sc.gns.cursor.group = list(PAGE_GROUPS).index("NAV")
    sc.gns.cursor.page = PAGE_GROUPS["NAV"].index("Map")
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_draw_aux_pages(db):
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    surf = pygame.display.get_surface()
    for sub in PAGE_GROUPS["AUX"]:
        sc.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
        sc.gns.cursor.page = PAGE_GROUPS["AUX"].index(sub)
        Renderer(surf).draw(sc)
        assert (pygame.surfarray.array2d(surf) != 0).sum() > 3000


def test_draw_aux_weather_page_with_no_cached_data(db):
    from gns530 import PAGE_GROUPS
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBOS", Point(42.36, -71.01)))
    d.add_waypoint(Waypoint("ALFA", Point(40.0, -74.0)))
    d.add_waypoint(Waypoint("BRAVO", Point(40.5, -74.0)))
    sc = _scene(db)
    g = gns530_mod.Gns530(d)
    g.load_flight_plan(["KBOS"])
    sc.gns = g
    sc.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    sc.gns.cursor.page = PAGE_GROUPS["AUX"].index("Weather")
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    r.draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 3000


def test_draw_aux_weather_page_shows_a_cached_metar_and_taf(db, tmp_path, monkeypatch):
    from gns530 import PAGE_GROUPS
    from datasrc import wx
    monkeypatch.setattr(wx, "default_data_root", lambda: tmp_path)
    wx._cache(tmp_path, "metar", "KBOS",
             {"stations": [{"station": "KBOS", "raw": "KBOS 111853Z 28012KT 10SM FEW250 22/09 A3005",
                            "obs_time": "", "wind_from_deg": 280.0, "wind_kt": 12.0,
                            "wind_gust_kt": None, "temp_c": 22.0, "dewpoint_c": 9.0,
                            "altim_inhg": 30.05, "flight_category": "VFR"}]})
    wx._cache(tmp_path, "taf", "KBOS",
             {"stations": [{"station": "KBOS", "raw": "TAF KBOS 111740Z 1118/1218 28010KT P6SM FEW250",
                            "issue_time": "", "valid_from": "", "valid_to": ""}]})
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBOS", Point(42.36, -71.01)))
    sc = _scene(db)
    g = gns530_mod.Gns530(d)
    g.load_flight_plan(["KBOS"])
    sc.gns = g
    sc.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    sc.gns.cursor.page = PAGE_GROUPS["AUX"].index("Weather")
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    r.draw(sc)
    metar_r = r._wx_read(sc, "KBOS", "metar")
    assert metar_r is not None
    assert "28012KT" in metar_r[0].raw
    taf_r = r._wx_read(sc, "KBOS", "taf")
    assert taf_r is not None
    assert "P6SM" in taf_r[0].raw


def test_wx_read_is_cached_until_the_ttl_elapses(tmp_path, monkeypatch):
    from datasrc import wx
    import render as render_mod
    monkeypatch.setattr(wx, "default_data_root", lambda: tmp_path)
    wx._cache(tmp_path, "metar", "KBOS", {"stations": [{
        "station": "KBOS", "raw": "FIRST", "obs_time": "", "wind_from_deg": None,
        "wind_kt": None, "wind_gust_kt": None, "temp_c": None, "dewpoint_c": None,
        "altim_inhg": None, "flight_category": ""}]})
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    sc = type("S", (), {"t": 0.0})()
    first = r._wx_read(sc, "KBOS", "metar")
    assert first[0].raw == "FIRST"
    wx._cache(tmp_path, "metar", "KBOS", {"stations": [{
        "station": "KBOS", "raw": "SECOND", "obs_time": "", "wind_from_deg": None,
        "wind_kt": None, "wind_gust_kt": None, "temp_c": None, "dewpoint_c": None,
        "altim_inhg": None, "flight_category": ""}]})
    sc.t = 1.0                                     # inside the TTL -> still cached
    assert r._wx_read(sc, "KBOS", "metar")[0].raw == "FIRST"
    sc.t = render_mod._WX_CACHE_TTL_S + 1.0         # past the TTL -> re-read
    assert r._wx_read(sc, "KBOS", "metar")[0].raw == "SECOND"


def test_wrap_splits_long_text_to_fit_the_given_width():
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    text = "TAF KBOS 111740Z 1118/1218 28010KT P6SM FEW250 FM120000 32015G25KT P6SM SCT200"
    lines = r._wrap(text, r.f_sm, 150)
    assert len(lines) > 1
    for line in lines:
        assert r.f_sm.size(line)[0] <= 150 or " " not in line


def test_draw_aux_charts_page_with_no_cached_index(db):
    from gns530 import PAGE_GROUPS
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBOS", Point(42.36, -71.01)))
    sc = _scene(db)
    g = gns530_mod.Gns530(d)
    g.load_flight_plan(["KBOS"])
    sc.gns = g
    sc.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    sc.gns.cursor.page = PAGE_GROUPS["AUX"].index("Charts")
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 3000


def test_draw_aux_charts_page_lists_a_cached_index(db, tmp_path, monkeypatch):
    from gns530 import PAGE_GROUPS
    from datasrc import dtpp
    from datasrc.airac import current_cycle
    monkeypatch.setattr(dtpp, "default_data_root", lambda: tmp_path)
    cycle = current_cycle()
    dtpp.fetch_and_cache_metafile(tmp_path, cycle, get=lambda url: b"""<?xml version="1.0"?>
<digital_tpp cycle="2609" from_edate="2026-08-13" to_edate="2026-09-10">
  <state_code ID="PA">
    <city_name volume="NE-3">LANCASTER
      <airport_name apt_ident="LNS" military="N">LANCASTER
        <record>
          <chartseq>1</chartseq><chart_code>IAP</chart_code>
          <chart_name>ILS OR LOC RWY 08</chart_name><useraction>C</useraction>
          <pdf_name>00775IL8.PDF</pdf_name>
        </record>
      </airport_name>
    </city_name>
  </state_code>
</digital_tpp>
""")
    d = NavDatabase(source="test")
    d.add_airport(Airport("KLNS", Point(40.12, -76.30)))
    sc = _scene(db)
    g = gns530_mod.Gns530(d)
    g.load_flight_plan(["KLNS"])
    sc.gns = g
    sc.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    sc.gns.cursor.page = PAGE_GROUPS["AUX"].index("Charts")
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    r.draw(sc)
    charts = r._dtpp_charts_for("KLNS")
    assert len(charts) == 1
    assert charts[0].chart_name == "ILS OR LOC RWY 08"


def test_dtpp_index_is_loaded_once_and_cached_on_the_renderer(tmp_path, monkeypatch):
    from datasrc import dtpp
    from datasrc.airac import current_cycle
    monkeypatch.setattr(dtpp, "default_data_root", lambda: tmp_path)
    calls = []
    real_load_index = dtpp.load_index

    def counting_load_index(root, cycle):
        calls.append(1)
        return real_load_index(root, cycle)

    monkeypatch.setattr(dtpp, "load_index", counting_load_index)
    dtpp.fetch_and_cache_metafile(
        tmp_path, current_cycle(),
        get=lambda url: b'<digital_tpp cycle="x" from_edate="x" to_edate="x"></digital_tpp>')
    surf = pygame.display.get_surface()
    r = Renderer(surf)
    r._dtpp_charts_for("KLNS")
    r._dtpp_charts_for("KBOS")
    r._dtpp_charts_for("KJFK")
    assert len(calls) == 1                # parsed once, not once per lookup


def test_open_selected_chart_reports_when_nothing_is_cached(db, monkeypatch):
    from gns530 import PAGE_GROUPS
    from datasrc import dtpp
    d = NavDatabase(source="test")
    d.add_airport(Airport("KLNS", Point(40.12, -76.30)))
    w = _bare_world(db)
    w.gns = gns530_mod.Gns530(d)
    w.gns.load_flight_plan(["KLNS"])
    w.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    w.gns.cursor.page = PAGE_GROUPS["AUX"].index("Charts")

    # run the worker inline instead of on a real thread, so the test doesn't
    # need to sleep/poll for a background thread to finish
    class SyncThread:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_mod.threading, "Thread", SyncThread)
    monkeypatch.setattr(dtpp, "load_index", lambda root, cycle: None)   # no index cached

    main_mod._open_selected_chart(w)
    msgs = w.gns.peek_messages()
    assert any("OPENING CHART" in m for m in msgs)
    assert any("NO CHART INDEX" in m for m in msgs)


def test_open_selected_chart_opens_the_selected_pdf(db, monkeypatch):
    from gns530 import PAGE_GROUPS
    from datasrc import dtpp
    d = NavDatabase(source="test")
    d.add_airport(Airport("KLNS", Point(40.12, -76.30)))
    w = _bare_world(db)
    w.gns = gns530_mod.Gns530(d)
    w.gns.load_flight_plan(["KLNS"])
    w.gns.cursor.group = list(PAGE_GROUPS).index("AUX")
    w.gns.cursor.page = PAGE_GROUPS["AUX"].index("Charts")
    w.gns.chart_sel = 0

    class SyncThread:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    chart = dtpp.ChartRecord(state="PA", city="LANCASTER", volume="NE-3",
                             airport_ident="LNS", airport_name="LANCASTER", military="N",
                             chart_seq="1", chart_code="IAP", chart_name="ILS OR LOC RWY 08",
                             useraction="", pdf_name="00775IL8.PDF")
    opened = []
    monkeypatch.setattr(main_mod.threading, "Thread", SyncThread)
    monkeypatch.setattr(dtpp, "load_index", lambda root, cycle: [chart])
    monkeypatch.setattr(dtpp, "fetch_and_cache_chart",
                        lambda root, cycle, pdf_name: f"/fake/{pdf_name}")
    monkeypatch.setattr(dtpp, "open_with_os_default", lambda path: opened.append(path))

    main_mod._open_selected_chart(w)
    assert opened == ["/fake/00775IL8.PDF"]
    assert any("OPENED" in m for m in w.gns.peek_messages())


def test_draw_vnav_page(db):
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    g = sc.gns
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(main_mod.Point(40.0, -74.0), 0.0, 120.0)
    assert g.vnav_set("CHAR", 2000.0, -500.0)
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("VNAV")
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_draw_flight_plan_catalog_page_and_mnu_popup(db):
    from ifr1 import Event, Mode
    from gns530 import PAGE_GROUPS
    sc = _scene(db)
    g = sc.gns
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(0)
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan Catalog")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000
    # the MNU pop-up over the Flight Plan page
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


# --------------------------------------------------------------------------- #
# main.py plumbing
# --------------------------------------------------------------------------- #
def test_parse_args_wind_and_plan():
    cfg = main_mod.parse_args(["--plan", "KBOS BOS PVD", "--wind", "300/25", "--no-device"])
    assert cfg.plan == ["KBOS", "BOS", "PVD"]
    assert cfg.wind_from == 300.0 and cfg.wind_kt == 25.0
    assert cfg.no_device


def test_parse_wind_tolerates_junk():
    assert main_mod._parse_wind("garbage") == (0.0, 0.0)
    assert main_mod._parse_wind("270@10") == (270.0, 10.0)


def test_step_once_advances_and_tracks(db):
    g = gns530_mod.Gns530(db)
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    a, b = db.find("ALFA")[0].pos, db.find("BRAVO")[0].pos
    brg = initial_bearing(a, b)
    sim = simmod.SimModel(pos=destination(a, brg, 2.0), heading_deg=brg, tas_kt=130.0)
    p0 = sim.state.pos
    for _ in range(50):
        nav, panel, st = main_mod.step_once(g, sim, 1.0, autopilot=True, magvar=0.0)
    assert st.pos != p0
    assert nav.valid and panel.cdi.source == "GPS"
    assert abs(nav.xtk_nm) < 1.0            # autopilot holds the centreline


def test_phase_tightens_near_the_fix():
    class N:
        dist_nm = 2.0
    assert main_mod._phase_for(N()) == instr.Phase.APPROACH
    N.dist_nm = 15.0
    assert main_mod._phase_for(N()) == instr.Phase.TERMINAL
    N.dist_nm = 80.0
    assert main_mod._phase_for(N()) == instr.Phase.ENROUTE


# --------------------------------------------------------------------------- #
# steam layout
# --------------------------------------------------------------------------- #
def _steam_scene(db):
    g = gns530_mod.Gns530(db)
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    sim = simmod.SimModel(pos=Point(40.1, -74.0), heading_deg=10.0, tas_kt=140.0)
    sim.command(altitude=5000.0)
    sim.step(1.0)
    st = sim.state
    nav = g.update(st.pos, st.track_deg, st.gs_kt)
    own_i = instr.Ownship(st.pos, st.track_deg, st.heading_deg, st.gs_kt, st.altitude_ft, -13.0)
    panel = instr.compute_panel(own_i, nav)
    rx = db.find("OMN")[0]

    class _R:  # minimal NavReceiver duck for nav_head
        tuned = True
        is_localizer = False
        station_pos = rx.pos
        station_magvar = -13.0
        station_ident = "OMN"
        obs_deg = 245.0
        course_deg = 245.0
        has_dme = True
        gs_ref = None
    n1 = instr.nav_head(_R(), st.pos, st.altitude_ft, st.gs_kt, -13.0)
    ap = Autopilot()
    ap.press_hdg()
    ap.set_heading_bug(90)
    return Scene(
        own=st, nav=nav, panel=panel, gns=g, db=db, magvar=-13.0, layout="steam",
        sixpack=instr.six_pack(st, -13.0), nav1_head=n1, nav2_head=instr.nav_head(None, st.pos, 0, 0, 0),
        ap=ap, radios=RadioStack(),
    )


def test_steam_layout_draws(db):
    surf = pygame.display.get_surface()
    Renderer(surf).draw(_steam_scene(db))
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000


def test_steam_layout_hsi_variant_draws(db):
    sc = _steam_scene(db)
    sc.nav1_hsi = True
    Renderer(pygame.display.get_surface()).draw(sc)


def test_steam_layout_draws_the_ias_setpoint_bug(db):
    sc = _steam_scene(db)
    sc.ias_target = 145.0
    sc.ias_managed = True
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


# --------------------------------------------------------------------------- #
# IFR-1 event routing
# --------------------------------------------------------------------------- #
def _bare_world(db, *, gns2=None):
    w = main_mod.World.__new__(main_mod.World)
    w.db = db
    w.ap = Autopilot()
    w.radios = RadioStack()
    w.gns = gns530_mod.Gns530(db)
    w.gns2 = gns2               # None unless a test opts into --dual
    w.show_msg = False
    w.baro_inhg = 29.92
    w.shift_latched = False
    w._last_mode = None
    w.ident_timer = 0.0
    w.gps_follow = True
    w._appr_tuned = False
    w._vloc_reminded = False
    w._last_approach_freq = None
    w.sim = simmod.SimModel(pos=Point(40.0, -74.0), heading_deg=90.0,
                            altitude_ft=6000.0, tas_kt=130.0)
    w.ias_target = 120.0
    w._ias_managed = False
    w.paused = False
    w.ground_status = ""
    w.feed = None
    w.feed_live = False
    w.magvar = 0.0
    w.t = 0.0
    import scoring
    w.score = scoring.ScoreTracker()
    return w


def _ground_test_world(db, *, pos, altitude_ft):
    thr = Point(40.0, -76.0)
    apt = Airport("KTST", thr, elev_ft=500)
    apt.runways["RW09"] = Runway("RW09", thr, 90.0, length_ft=6000.0, width_ft=150.0)
    db.add_airport(apt)
    w = _bare_world(db)
    w.sim = simmod.SimModel(pos=pos, heading_deg=90.0, altitude_ft=altitude_ft, tas_kt=100.0)
    return w, apt


def test_ground_contact_landed_on_the_runway(db):
    thr = Point(40.0, -76.0)
    mid_runway = destination(thr, 90.0, 3000.0 / 6076.115)
    w, apt = _ground_test_world(db, pos=mid_runway, altitude_ft=490.0)   # at/below field elev
    w._check_ground_contact()
    assert w.paused and w.ground_status == "LANDED"
    assert w.sim.altitude == pytest.approx(500.0)   # clamped to the field, not left below it


def test_ground_contact_crashed_off_the_runway(db):
    thr = Point(40.0, -76.0)
    off_runway = destination(thr, 0.0, 1.0)   # 1 nm north of the field, same low altitude
    w, apt = _ground_test_world(db, pos=off_runway, altitude_ft=495.0)
    w._check_ground_contact()
    assert w.paused and w.ground_status == "CRASHED"


def test_ground_contact_no_effect_while_still_above_field_elevation(db):
    thr = Point(40.0, -76.0)
    mid_runway = destination(thr, 90.0, 3000.0 / 6076.115)
    w, apt = _ground_test_world(db, pos=mid_runway, altitude_ft=2000.0)
    w._check_ground_contact()
    assert not w.paused and w.ground_status == ""


def test_ground_contact_no_effect_far_from_any_airport(db):
    w, apt = _ground_test_world(db, pos=Point(41.0, -76.0), altitude_ft=100.0)  # ~60nm away, well below any real field
    w._check_ground_contact()
    assert not w.paused and w.ground_status == ""


def test_tick_freezes_position_once_paused(db):
    """The sim must actually stop advancing once ground contact is
    declared - not just report LANDED/CRASHED while still moving."""
    w = _bare_world(db)
    w.gns.load_flight_plan(["OMN"])
    w.paused = True
    w.ground_status = "LANDED"
    before = w.sim.state.pos
    fr = w.tick(1.0)
    assert w.sim.state.pos == before          # never advanced
    assert fr.own.pos == before


def _key(key, *, shift=False):
    mod = pygame.KMOD_SHIFT if shift else 0
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod)


def test_on_key_number_keys_set_time_warp(db):
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    for key, level in ((pygame.K_1, 1), (pygame.K_2, 5), (pygame.K_3, 10), (pygame.K_4, 20)):
        main_mod._on_key(_key(key), w, ui)
        assert ui["time_warp"] == level


def test_on_key_plain_pageup_pagedown_changes_page_within_group(db):
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    grp0 = w.gns.cursor.group
    page0 = w.gns.cursor.page
    main_mod._on_key(_key(pygame.K_PAGEUP), w, ui)
    assert w.gns.cursor.group == grp0                # group unchanged
    assert w.gns.cursor.page == page0 + 1             # page within it advanced
    main_mod._on_key(_key(pygame.K_PAGEDOWN), w, ui)
    assert w.gns.cursor.page == page0


def test_on_key_c_does_not_cancel_direct_to(db):
    """F73: a bare CLR press does NOT cancel an active Direct-To - the
    Pilot's Guide's only documented way is DCT > MENU > "Cancel Direct-To
    NAV?" > ENT (sec.3 p.47-48). An earlier CLR shortcut here (F14) was a
    keyboard-convenience approximation, not the real unit's behavior, and
    has been dropped; `C` with no dialog open now just falls through to the
    ordinary CLR fallback (Default NAV)."""
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.update(Point(40.0, -74.0), 0.0, 120.0)
    assert w.gns.direct_to("CHAR")
    assert w.gns.dto is not None
    main_mod._on_key(_key(pygame.K_c), w, ui)
    assert w.gns.dto is not None                     # unaffected by a bare CLR


def test_menu_cancel_direct_to_nav_resumes_the_closest_leg(db):
    """F73: DCT > MENU > "Cancel Direct-To NAV?" > ENT is the Pilot's Guide's
    documented way to cancel an active Direct-To (sec.3 p.47-48) - it drops
    the Direct-To, closes the Select Direct-to Waypoint Page, and resumes
    the flight plan on the closest leg, same as the old CLR shortcut did."""
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.update(Point(40.0, -74.0), 0.0, 120.0)
    assert w.gns.direct_to("CHAR")
    assert w.gns.dto is not None

    main_mod._on_key(_key(pygame.K_d), w, ui)          # DCT re-opens the page
    assert w.gns._dto_dialog is not None
    main_mod._on_key(_key(pygame.K_x), w, ui)          # MNU -> Direct-to Options
    assert w.gns._dto_menu is not None
    assert w.gns._dto_menu.current == "CANCEL DIRECT-TO NAV?"
    main_mod._on_key(_key(pygame.K_RETURN), w, ui)     # ENT applies it
    assert w.gns._dto_menu is None
    assert w.gns._dto_dialog is None
    assert w.gns.dto is None
    assert w.gns.fpl.to_wp.ident == "BRAVO"            # resumed the nearest leg


def test_menu_on_dto_page_clr_backs_out_without_cancelling(db):
    """CLR (or a second MNU) on the Direct-to Options menu backs out to the
    Select Direct-to Waypoint Page without cancelling anything."""
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.update(Point(40.0, -74.0), 0.0, 120.0)
    assert w.gns.direct_to("CHAR")

    main_mod._on_key(_key(pygame.K_d), w, ui)
    main_mod._on_key(_key(pygame.K_x), w, ui)
    assert w.gns._dto_menu is not None
    main_mod._on_key(_key(pygame.K_ESCAPE), w, ui)     # CLR (the DTO menu's own keyboard route)
    assert w.gns._dto_menu is None
    assert w.gns._dto_dialog is not None               # still on the DCT page
    assert w.gns.dto is not None                       # not cancelled


def test_on_key_x_opens_and_drives_the_fpl_menu(db):
    """O1: "Menu button needs a key" - the MNU key had no keyboard route at
    all. `X` opens it; once open, Up/Down move the selection and Enter
    confirms, same as the PROC selector's keyboard handling."""
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.cursor.go_to_flight_plan()
    main_mod._on_key(_key(pygame.K_x), w, ui)
    assert w.gns._fpl_menu is not None
    sel0 = w.gns._fpl_menu.sel
    main_mod._on_key(_key(pygame.K_DOWN), w, ui)
    assert w.gns._fpl_menu.sel != sel0
    main_mod._on_key(_key(pygame.K_ESCAPE), w, ui)
    assert w.gns._fpl_menu is None


def test_on_key_shift_pageup_pagedown_changes_page_group(db):
    from gns530 import PAGE_GROUPS
    w = _bare_world(db)
    ui = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    grp0 = w.gns.cursor.group
    main_mod._on_key(_key(pygame.K_PAGEUP, shift=True), w, ui)
    assert w.gns.cursor.group == grp0 + 1             # group advanced (NAV -> WPT)
    assert w.gns.cursor.page == 0                     # PageCursor.on_outer resets the page
    main_mod._on_key(_key(pygame.K_PAGEDOWN, shift=True), w, ui)
    assert w.gns.cursor.group == grp0
    assert list(PAGE_GROUPS)[w.gns.cursor.group] == "NAV"


def test_reload_cached_winds_aloft_applies_a_fresher_profile_to_the_sim(db, monkeypatch):
    from windsaloft import WindsAloftProfile
    w = _bare_world(db)
    w._wx_region, w._wx_station = "BOS", "BOS"
    fresh = WindsAloftProfile.uniform(270.0, 40.0)
    monkeypatch.setattr(main_mod, "_load_cached_winds_aloft", lambda region, station: fresh)
    w._reload_cached_winds_aloft()
    assert w.sim.winds_aloft is fresh


def test_reload_cached_winds_aloft_is_a_noop_when_nothing_is_cached(db, monkeypatch):
    w = _bare_world(db)
    w._wx_region, w._wx_station = "BOS", "BOS"
    before = w.sim.winds_aloft
    monkeypatch.setattr(main_mod, "_load_cached_winds_aloft", lambda region, station: None)
    w._reload_cached_winds_aloft()
    assert w.sim.winds_aloft is before


# --------------------------------------------------------------------------- #
# ForeFlight discovery -> GDL90 unicast retargeting
# --------------------------------------------------------------------------- #
class _FakeGdl90:
    def __init__(self, addr):
        self.addr = addr


class _FakeListener:
    def __init__(self, primary=None):
        self.primary = primary


class _FakeDevice:
    def __init__(self, ip, port=4000, app="ForeFlight"):
        self.ip = ip
        self.gdl90_port = port
        self.app = app


def test_apply_ff_discovery_switches_to_unicast_once_a_device_is_seen():
    gdl90 = _FakeGdl90(("255.255.255.255", 4000))
    listener = _FakeListener(primary=_FakeDevice("192.168.1.23"))
    new_ip = main_mod._apply_ff_discovery(gdl90, listener, None, ("255.255.255.255", 4000))
    assert new_ip == "192.168.1.23"
    assert gdl90.addr == ("192.168.1.23", 4000)


def test_apply_ff_discovery_falls_back_to_broadcast_once_the_device_goes_stale():
    gdl90 = _FakeGdl90(("192.168.1.23", 4000))
    listener = _FakeListener(primary=None)
    new_ip = main_mod._apply_ff_discovery(gdl90, listener, "192.168.1.23",
                                          ("255.255.255.255", 4000))
    assert new_ip is None
    assert gdl90.addr == ("255.255.255.255", 4000)


def test_apply_ff_discovery_is_a_noop_while_target_is_unchanged():
    gdl90 = _FakeGdl90(("192.168.1.23", 4000))
    listener = _FakeListener(primary=_FakeDevice("192.168.1.23"))
    new_ip = main_mod._apply_ff_discovery(gdl90, listener, "192.168.1.23",
                                          ("255.255.255.255", 4000))
    assert new_ip == "192.168.1.23"
    assert gdl90.addr == ("192.168.1.23", 4000)     # untouched


def test_apply_ff_discovery_switches_between_two_different_devices():
    gdl90 = _FakeGdl90(("192.168.1.23", 4000))
    listener = _FakeListener(primary=_FakeDevice("192.168.1.99", port=4001))
    new_ip = main_mod._apply_ff_discovery(gdl90, listener, "192.168.1.23",
                                          ("255.255.255.255", 4000))
    assert new_ip == "192.168.1.99"
    assert gdl90.addr == ("192.168.1.99", 4001)


def test_apply_ff_discovery_stays_on_broadcast_when_nothing_ever_seen():
    gdl90 = _FakeGdl90(("255.255.255.255", 4000))
    listener = _FakeListener(primary=None)
    new_ip = main_mod._apply_ff_discovery(gdl90, listener, None, ("255.255.255.255", 4000))
    assert new_ip is None
    assert gdl90.addr == ("255.255.255.255", 4000)


def test_auto_vloc_stages_activates_and_switches_cdi_on_the_approach(db):
    from navdata.model import Airport, Runway, Procedure, ProcedureLeg, LegType
    thr = Point(41.0, -74.0)
    apt = Airport("KTST", thr, elev_ft=300)
    apt.runways["RW09"] = Runway("RW09", thr, 90.0, ils_ident="ITST")
    db.add_airport(apt)
    db.add_vhf(VhfNavaid("ITST", destination(thr, 90.0, 1.4), 110.30,
                         nav_class="ILSW", loc_bearing_deg=90.0,
                         runway_ident="RW09", airport_ident="KTST"))
    db.add_procedure(Procedure(
        airport="KTST", ident="I09", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="RW09", is_map=True),
        )},
    ))
    w = _bare_world(db)
    w.gns.load_procedure("KTST", "I09")
    # main's __init__ stages the approach frequency into VLOC standby
    w.radios.nav1.standby_mhz = w.gns.approach_freq
    assert w.gns.approach_freq == pytest.approx(110.30)

    # on the RW09 centreline, 6 nm west of the threshold, sequenced past the FAF
    ac = destination(thr, 270.0, 6.0)
    w.gns.fpl.activate_leg(2)                         # BRAVO(FAF) -> RW09
    nav = w.gns.update(ac, 90.0, 130.0)

    w._auto_vloc(nav)
    assert w.radios.nav1.active_mhz == pytest.approx(110.30)   # staged freq activated
    w.radios.resolve(db, ac, 3000.0)                  # bind the localizer
    w._auto_vloc(nav)
    assert w.radios.nav1.station_ident == "ITST"              # localizer locked
    assert w.gns.cdi_source == "VLOC"                         # CDI auto-switched GPS->VLOC


def test_auto_vloc_reminds_rather_than_auto_activates_when_a_pilot_is_flying(db):
    # activating VLOC (the flip-flop key) is always a pilot action on the real
    # 530W - only reach in and press it when nobody is at the controls at all
    # (GPS-follow with the autopilot OFF). With the autopilot engaged (a very
    # normal way to fly an ILS) the pilot must press SWAP themselves; leaving
    # the frequency silently in standby would strand APR/GS capture forever,
    # so post a one-shot reminder instead.
    from navdata.model import Airport, Runway, Procedure, ProcedureLeg, LegType
    thr = Point(41.0, -74.0)
    apt = Airport("KTST", thr, elev_ft=300)
    apt.runways["RW09"] = Runway("RW09", thr, 90.0, ils_ident="ITST")
    db.add_airport(apt)
    db.add_vhf(VhfNavaid("ITST", destination(thr, 90.0, 1.4), 110.30,
                         nav_class="ILSW", loc_bearing_deg=90.0,
                         runway_ident="RW09", airport_ident="KTST"))
    db.add_procedure(Procedure(
        airport="KTST", ident="I09", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="RW09", is_map=True),
        )},
    ))
    w = _bare_world(db)
    w.gns.load_procedure("KTST", "I09")
    w.radios.nav1.standby_mhz = w.gns.approach_freq
    w.ap.press_ap()
    w.ap.press_apr()                                  # the pilot is flying it via the AP

    ac = destination(thr, 270.0, 6.0)
    w.gns.fpl.activate_leg(2)
    nav = w.gns.update(ac, 90.0, 130.0)
    w._auto_vloc(nav)

    assert w.radios.nav1.active_mhz != pytest.approx(110.30)   # NOT auto-activated
    assert any("TUNE VLOC" in m for m in w.gns.peek_messages())
    # pressing SWAP themselves still works exactly as on the real unit
    w.radios.nav1.swap()
    assert w.radios.nav1.active_mhz == pytest.approx(110.30)


def test_auto_vloc_never_auto_switches_cdi_on_a_vor_approach(db):
    """500W Pilot's Guide p.117-118: the GPS->VLOC auto-switch is described
    only for an activated ILS approach ("the correct ILS frequency is active
    in the VLOC window"). A VOR-referenced approach has no such feature - a
    localizer's course is fixed (OBS is moot), but a VOR radial's is not, so
    the pilot must always set the external CDI's OBS/course themselves and
    press CDI to switch manually. Frequency auto-staging/auto-tuning (the
    general, non-WAAS-specific feature) still applies to both."""
    from navdata.model import Airport, Procedure, ProcedureLeg, LegType
    thr = Point(41.0, -74.0)
    apt = Airport("KTST", thr, elev_ft=300)
    db.add_airport(apt)
    vor_pos = destination(thr, 270.0, 10.0)
    db.add_vhf(VhfNavaid("TVOR", vor_pos, 113.5, nav_class="VOR"))
    db.add_procedure(Procedure(
        airport="KTST", ident="D09", kind="approach", route_type="D",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True, recnav_ident="TVOR"),
            ProcedureLeg(30, LegType.CF, fix_ident="RW09", is_map=True, recnav_ident="TVOR"),
        )},
    ))
    w = _bare_world(db)
    w.gns.load_procedure("KTST", "D09")
    assert w.gns.approach_freq == pytest.approx(113.5)      # frequency staging still happens
    assert w.gns.approach_is_localizer is False
    w.radios.nav1.standby_mhz = w.gns.approach_freq
    w.radios.nav1.swap()                                     # pilot flip-flops it active themselves

    ac = destination(thr, 270.0, 1.0)                        # essentially on top of the MAP
    w.gns.fpl.activate_leg(2)
    nav = w.gns.update(ac, 90.0, 130.0)
    w.radios.resolve(db, ac, 3000.0)
    w._auto_vloc(nav)
    assert w.gns.cdi_source == "GPS"                          # never auto-switched


def test_world_tick_threads_the_expected_station_through_to_radios_resolve(db):
    """F75 (validation sweep, KIAD ILS 19L: a ~172 deg autopilot heading
    swing): the most common real-world frequency-sharing case is one
    frequency shared between the two ENDS of the same runway (real KIAD
    110.1 numbers below - ISGC/RW19L and IIAD/RW01R, exact reciprocals,
    almost exactly collinear). Beam-alignment error alone can't reliably
    tell them apart, and ordinary track noise can make the *wrong* one read
    marginally better. This is an end-to-end check (through `World.tick`,
    not `radios.py` directly) that main.py actually threads
    `GpsNav.approach_ref` into `RadioStack.resolve` every tick, so the
    loaded approach's own station wins regardless."""
    from navdata.model import Airport, Runway, Procedure, ProcedureLeg, LegType
    thr19l = Point(38.955331, -77.435975)
    thr01r = Point(38.923756, -77.436447)
    apt = Airport("KIAD", Point(38.94, -77.44), elev_ft=313, magvar_deg=-10.0)
    apt.runways["RW19L"] = Runway("RW19L", thr19l, 191.0, length_ft=11500, ils_ident="ISGC")
    apt.runways["RW01R"] = Runway("RW01R", thr01r, 11.0, length_ft=11500, ils_ident="IIAD")
    db.add_airport(apt)
    db.add_vhf(VhfNavaid("ISGC", Point(38.919947, -77.436506), 110.1, nav_class="ILSW",
                         loc_bearing_deg=190.7, runway_ident="RW19L", airport_ident="KIAD"))
    db.add_vhf(VhfNavaid("IIAD", Point(38.958575, -77.435925), 110.1, nav_class="ILSW",
                         loc_bearing_deg=10.7, runway_ident="RW01R", airport_ident="KIAD"))
    db.add_procedure(Procedure(
        airport="KIAD", ident="I19L", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="RW19L", is_map=True),
        )},
    ))

    w = _bare_world(db)
    w.gns.load_procedure("KIAD", "I19L")
    assert w.gns.approach_ref == "ISGC"
    w.radios.nav1.standby_mhz = w.gns.approach_freq
    w.radios.nav1.swap()                                       # pilot activates it

    # same off-centreline point that flips a geometry-only pick to IIAD
    # (test_resolve_prefers_the_gns_expected_station_on_a_reciprocal_runway_pair)
    true_course = norm360(191.0 - 10.0)
    base = destination(thr19l, reciprocal(true_course), 9.0)
    ac = destination(base, norm360(true_course + 90.0), 0.05)
    w.sim.pos = ac
    w.sim.heading = true_course
    w.gns.fpl.activate_leg(2)                                  # BRAVO (FAF) -> RW19L

    w.tick(0.1)
    assert w.radios.nav1.station_ident == "ISGC"               # not IIAD


def test_route_event_ap_row_drives_autopilot_and_knobs(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    main_mod.route_event(Event(mode=Mode.AP, pressed=("AP",)), w)
    assert w.ap.engaged
    main_mod.route_event(Event(mode=Mode.AP, pressed=("HDG",)), w)
    assert w.ap.lateral.value == "HDG"
    base = w.ap.alt_preselect
    main_mod.route_event(Event(mode=Mode.AP, outer=2), w)              # ALT select, 100 ft
    assert w.ap.alt_preselect == pytest.approx(base + 200)
    main_mod.route_event(Event(mode=Mode.AP, inner=-3), w)            # VS, 100 fpm
    assert w.ap.vs_target == pytest.approx(-300)


def test_route_event_ap_shift_inner_trims_the_ias_setpoint(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    assert not w._ias_managed
    main_mod.route_event(Event(mode=Mode.AP, pressed=("KNOB",)), w)    # latch the shift
    assert w.shift_latched
    vs0 = w.ap.vs_target
    main_mod.route_event(Event(mode=Mode.AP, inner=3), w)             # shifted -> IAS +15 kt
    assert w.ias_target == pytest.approx(135.0)
    assert w._ias_managed and w.sim.target_ias == pytest.approx(135.0)
    assert w.ap.vs_target == pytest.approx(vs0)                       # VS untouched while shifted
    # the held IAS drives the sim's TAS as a function of altitude
    for _ in range(60):
        w.sim.step(1.0)
    assert w.sim.state.tas_kt > 135.0                                # 6000 ft -> TAS > IAS
    # un-shift: the inner knob is back to VS
    main_mod.route_event(Event(mode=Mode.AP, pressed=("KNOB",)), w)
    main_mod.route_event(Event(mode=Mode.AP, inner=-2), w)
    assert w.ap.vs_target == pytest.approx(vs0 - 200)


def test_route_event_fms_ap_row_becomes_bezel_keys(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("AP",)), w)     # CDI
    assert w.gns.cdi_source == "VLOC"
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.update(main_mod.Point(40.1, -74.0), 0.0, 120.0)
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("HDG",)), w)    # OBS
    assert w.gns.obs_active
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("APR",)), w)    # FPL
    assert w.gns.cursor.page_name == "Flight Plan"
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("NAV",)), w)    # MSG
    assert w.show_msg
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("ALT",)), w)    # VNAV
    assert w.gns.cursor.page_name == "VNAV"
    assert not w.ap.engaged                                            # AP row did NOT arm


def test_fms_obs_bezel_key_releases_suspend_instead_of_toggling_obs(db):
    # OBS/SUSP is one physical key on the real 530 - while suspended (a MAP,
    # or mid-hold) it must release the suspend, not drop into OBS mode too.
    from ifr1 import Event, Mode
    from navdata.model import Procedure, ProcedureLeg, LegType
    db.add_procedure(Procedure(
        airport="KTST", ident="ILS", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="CHAR", is_map=True),
            ProcedureLeg(40, LegType.DF, fix_ident="DELT"),
        )},
    ))
    w = _bare_world(db)
    w.gns.load_procedure("KTST", "ILS")
    w.gns.fpl.activate_leg(2)                          # BRAVO -> CHAR (the MAP)
    w.gns.update(main_mod.Point(41.001, -74.0), 0.0, 120.0)   # over CHAR -> SUSP
    assert w.gns.suspended and not w.gns.obs_active

    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("HDG",)), w)      # OBS/SUSP key
    assert not w.gns.suspended                          # released...
    assert not w.gns.obs_active                          # ...and did NOT enter OBS mode

    # not suspended any more: the same key now toggles OBS as usual
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("HDG",)), w)
    assert w.gns.obs_active


def test_fms_obs_bezel_key_arms_a_hold_exit_instead_of_obs(db):
    from ifr1 import Event, Mode
    from navdata.model import Procedure, ProcedureLeg, LegType
    db.add_procedure(Procedure(
        airport="KTST", ident="I18", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.HM, fix_ident="BRAVO", turn="R",
                        course_mag=0.0, time_min=1.0),
        )},
    ))
    w = _bare_world(db)
    w.gns.load_procedure("KTST", "I18")
    w.gns.fpl.activate_leg(w.gns.fpl.index_of("BRAVO"))
    w.gns.update(main_mod.Point(40.499, -74.0), 0.0, 130.0, 1.0)   # trips the hold
    assert w.gns._hold_state is not None and not w.gns._hold_state["exit_requested"]

    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("HDG",)), w)   # OBS/SUSP key
    assert w.gns._hold_state["exit_requested"]           # armed the release...
    assert not w.gns.obs_active                          # ...not OBS mode


def test_route_event_fms_proc_key_opens_and_owns_the_selector(db):
    from ifr1 import Event, Mode
    from navdata.model import Airport, Procedure, ProcedureLeg, LegType
    db.add_airport(Airport("KEND", Point(41.0, -74.0)))
    db.add_procedure(Procedure(
        airport="KEND", ident="I09", kind="approach", route_type="I",
        transitions={"": (ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
                          ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True))},
    ))
    w = _bare_world(db)
    w.gns.load_flight_plan(["ALFA", "KEND"])
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("VS",)), w)     # VS == PROC bezel key
    assert w.gns._proc_dialog is not None and w.gns._proc_dialog.step == "MENU"
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("NAV",)), w)    # would be MSG - swallowed
    assert not w.show_msg
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("ENT",)), w)    # SELECT APPROACH
    assert w.gns._proc_dialog.step == "PROC"
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("ENT",)), w)    # pick I09
    assert w.gns._proc_dialog.step == "TRANS" and w.gns._proc_dialog.options == ["VECTORS"]
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("ENT",)), w)    # VECTORS -> Load?/Activate?
    assert w.gns._proc_dialog.step == "LOADACT"
    main_mod.route_event(Event(mode=Mode.FMS1, pressed=("ENT",)), w)    # confirm Load?
    assert w.gns._proc_dialog is None
    assert [wp.ident for wp in w.gns.fpl.waypoints][-2:] == ["ALFA", "BRAVO"]


def test_draw_proc_page(db):
    from gpsnav import ProcSelect
    sc = _scene(db)
    sc.gns._proc_dialog = ProcSelect(
        airport="KEND", step="PROC", kind="approach",
        options=["I05", "I23", "RNAV (GPS) Y 05"], sel=1)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_draw_proc_page_loadact_step(db):
    """The Load?/Activate? step (Pilot's Guide p.61 step 5) renders through
    the same generic `_proc_page` as every other step - `_proc_page` reads
    `dlg.title`/`dlg.options` generically, no special-casing needed."""
    from gpsnav import ProcSelect
    sc = _scene(db)
    sc.gns._proc_dialog = ProcSelect(
        airport="KEND", step="LOADACT", kind="approach", proc_ident="I05",
        options=["Load?", "Activate?"], sel=1)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)
    assert (pygame.surfarray.array2d(surf) != 0).sum() > 5000


def test_route_event_knob_latches_shift_and_mode_change_clears_it(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    f0 = w.radios.nav1.standby_mhz
    main_mod.route_event(Event(mode=Mode.NAV1, inner=1), w)             # kHz step (no shift)
    tuned = w.radios.nav1.standby_mhz
    assert tuned == pytest.approx(f0 + 0.05)
    main_mod.route_event(Event(mode=Mode.NAV1, pressed=("KNOB",)), w)   # latch shift
    assert w.shift_latched
    main_mod.route_event(Event(mode=Mode.NAV1, inner=2), w)            # now turns the OBS card
    assert w.radios.nav1.obs_deg == 2.0
    assert w.radios.nav1.standby_mhz == pytest.approx(tuned)          # freq untouched
    main_mod.route_event(Event(mode=Mode.NAV2, inner=1), w)           # selector moved -> clears
    assert not w.shift_latched
    main_mod.route_event(Event(mode=Mode.NAV1, pressed=("SWAP",)), w)  # SWAP is pure flip-flop
    assert w.radios.nav1.active_mhz == pytest.approx(tuned)


def test_panel_uses_nav1_only_when_cdi_source_is_vloc(db):
    import instruments as instr
    from navmath import destination
    w = _bare_world(db)
    w.magvar = 0.0
    # tune NAV1 onto the OMN VOR
    w.radios.nav1.active_mhz = 113.00
    w.radios.resolve(db, main_mod.Point(40.2, -74.0))
    assert w.radios.nav1.tuned
    w.radios.nav1.set_obs(90.0)
    w.gns.load_flight_plan(["ALFA", "BRAVO"])
    ac = destination(w.radios.nav1.station_pos, 90.0, 8.0)
    nav = w.gns.update(ac, 0.0, 120.0)
    p_gps = w._panel(instr.Ownship(ac, 0.0, 0.0, 120.0, 5000.0, 0.0), nav)
    assert p_gps.cdi.source == "GPS"                     # default: GPS guidance
    w.gns.toggle_cdi_source()
    nav = w.gns.update(ac, 0.0, 120.0)
    p_vloc = w._panel(instr.Ownship(ac, 0.0, 0.0, 120.0, 5000.0, 0.0), nav)
    assert p_vloc.cdi.source == "VLOC" and p_vloc.cdi.valid
    assert p_vloc.cdi.course_deg == pytest.approx(90.0, abs=1.0)   # the NAV1 OBS


def test_route_event_com_shift_drives_heading_and_baro(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    main_mod.route_event(Event(mode=Mode.COM1, pressed=("KNOB",)), w)   # latch
    main_mod.route_event(Event(mode=Mode.COM1, outer=3), w)
    assert w.ap.heading_bug == 30.0
    main_mod.route_event(Event(mode=Mode.COM2, pressed=("KNOB",)), w)   # mode change cleared it, re-latch
    main_mod.route_event(Event(mode=Mode.COM2, inner=5), w)
    assert w.baro_inhg == pytest.approx(29.97)


def test_route_event_xpdr_digit_cursor_ident_and_mode(db):
    from ifr1 import Event, Mode
    w = _bare_world(db)
    assert w.radios.xpdr.cursor == 3
    main_mod.route_event(Event(mode=Mode.XPDR, inner=1), w)            # bump LSD 0 -> 1
    assert w.radios.xpdr.squawk == "1201"
    main_mod.route_event(Event(mode=Mode.XPDR, outer=-1), w)          # cursor left
    assert w.radios.xpdr.cursor == 2
    main_mod.route_event(Event(mode=Mode.XPDR, pressed=("SWAP",)), w)  # SWAP -> IDENT
    assert w.radios.xpdr.identing and w.ident_timer > 0
    m0 = w.radios.xpdr.mode
    main_mod.route_event(Event(mode=Mode.XPDR, pressed=("KNOB",)), w)  # latch shift
    main_mod.route_event(Event(mode=Mode.XPDR, inner=1), w)           # shift -> mode
    assert w.radios.xpdr.mode != m0


# --------------------------------------------------------------------------- #
# dual FMS units (--dual: FMS1/FMS2 drive two independent GNS units)
# --------------------------------------------------------------------------- #
def _dual_world(db):
    return _bare_world(db, gns2=gns430_mod.Gns430(db))


def test_config_dual_defaults_off_and_layout_stays_gps():
    c = main_mod.parse_args(["--no-device"])
    assert c.dual is False
    assert c.layout == "gps"


def test_config_dual_defaults_layout_to_dual():
    c = main_mod.parse_args(["--no-device", "--dual"])
    assert c.dual is True
    assert c.layout == "dual"


def test_config_dual_explicit_layout_overrides_the_dual_default():
    c = main_mod.parse_args(["--no-device", "--dual", "--layout", "steam"])
    assert c.dual is True
    assert c.layout == "steam"


def test_config_dual_layout_from_file_is_not_overridden_by_dual(tmp_path, monkeypatch):
    (tmp_path / "octavi.toml").write_text('layout = "steam"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    c = main_mod.parse_args(["--no-device", "--dual"])
    assert c.layout == "steam"


def test_world_dual_builds_the_other_unit_type(db):
    w = main_mod.World.__new__(main_mod.World)   # just exercise the unit-selection logic
    unit_cls = gns530_mod.Gns530
    unit2_cls = gns530_mod.Gns530 if unit_cls is gns430_mod.Gns430 else gns430_mod.Gns430
    assert unit2_cls is gns430_mod.Gns430          # --unit 530 --dual -> 430 on FMS2
    unit_cls = gns430_mod.Gns430
    unit2_cls = gns530_mod.Gns530 if unit_cls is gns430_mod.Gns430 else gns430_mod.Gns430
    assert unit2_cls is gns530_mod.Gns530          # --unit 430 --dual -> 530 on FMS2


def test_route_event_fms1_and_fms2_drive_independent_units(db):
    from ifr1 import Event, Mode
    w = _dual_world(db)
    p1_before, p2_before = w.gns.cursor.page_name, w.gns2.cursor.page_name
    main_mod.route_event(Event(mode=Mode.FMS1, inner=1), w)
    assert w.gns.cursor.page_name != p1_before
    assert w.gns2.cursor.page_name == p2_before        # FMS1 never touches FMS2

    main_mod.route_event(Event(mode=Mode.FMS2, inner=1), w)
    assert w.gns2.cursor.page_name != p2_before
    # FMS1's own page (moved above) is untouched by the FMS2 press
    assert w.gns.cursor.page_name != p1_before


def test_route_event_fms2_bezel_keys_and_dto_hit_only_gns2(db):
    from ifr1 import Event, Mode
    w = _dual_world(db)
    main_mod.route_event(Event(mode=Mode.FMS2, pressed=("AP",)), w)     # CDI bezel key
    assert w.gns2.cdi_source == "VLOC"
    assert w.gns.cdi_source == "GPS"

    main_mod.route_event(Event(mode=Mode.FMS2, pressed=("DCT",)), w)
    assert w.gns2._dto_dialog is not None
    assert w.gns._dto_dialog is None


def test_route_event_fms2_msg_key_acks_gns2_without_opening_the_shared_box(db):
    from ifr1 import Event, Mode
    w = _dual_world(db)
    w.gns2.messages.append("TEST MSG")
    main_mod.route_event(Event(mode=Mode.FMS2, pressed=("NAV",)), w)    # MSG bezel key
    assert w.gns2.peek_messages() == []                # acked
    assert w.show_msg is False                          # the on-screen box stays FMS1's


def test_route_event_single_unit_fms2_still_drives_gns_when_not_dual(db):
    """No --dual (`w.gns2 is None`): FMS1 and FMS2 both fall back to the one
    unit, so a plain 530/430 session with the mode selector on FMS2 keeps
    working exactly as before this feature existed."""
    from ifr1 import Event, Mode
    w = _bare_world(db)                                 # gns2=None
    before = w.gns.cursor.page_name
    main_mod.route_event(Event(mode=Mode.FMS2, inner=1), w)
    assert w.gns.cursor.page_name != before


def test_on_key_u_toggles_keyboard_fms_unit_only_when_dual(db):
    w = _dual_world(db)
    ui = {"layout": "dual", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    main_mod._on_key(_key(pygame.K_u), w, ui)
    assert ui["kbd_fms2"] is True
    main_mod._on_key(_key(pygame.K_u), w, ui)
    assert ui["kbd_fms2"] is False

    w_single = _bare_world(db)                          # gns2=None: U is a no-op
    ui2 = {"layout": "gps", "map_range": 20.0, "nav1_hsi": False, "running": True,
           "time_warp": 1}
    main_mod._on_key(_key(pygame.K_u), w_single, ui2)
    assert "kbd_fms2" not in ui2


def test_on_key_page_keys_follow_the_toggled_keyboard_fms_unit(db):
    w = _dual_world(db)
    ui = {"layout": "dual", "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": 1}
    p1_before, p2_before = w.gns.cursor.page_name, w.gns2.cursor.page_name
    main_mod._on_key(_key(pygame.K_PAGEUP), w, ui)       # still FMS1 by default
    assert w.gns.cursor.page_name != p1_before
    assert w.gns2.cursor.page_name == p2_before

    main_mod._on_key(_key(pygame.K_u), w, ui)            # toggle keyboard to FMS2
    p1_mid = w.gns.cursor.page_name
    main_mod._on_key(_key(pygame.K_PAGEUP), w, ui)
    assert w.gns2.cursor.page_name != p2_before
    assert w.gns.cursor.page_name == p1_mid              # FMS1 untouched this time


def test_next_layout_cycles_and_resets_from_unknown():
    assert main_mod._next_layout("gps", dual=False) == "steam"
    assert main_mod._next_layout("steam", dual=False) == "stack"
    assert main_mod._next_layout("stack", dual=False) == "gps"
    assert main_mod._next_layout("dual", dual=False) == "gps"      # not offered -> reset
    assert main_mod._next_layout("gps", dual=True) == "steam"
    assert main_mod._next_layout("steam", dual=True) == "stack"
    assert main_mod._next_layout("stack", dual=True) == "dual"
    assert main_mod._next_layout("dual", dual=True) == "gps"


def test_dual_layout_draws_both_units(db):
    w = _dual_world(db)
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns2.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    nav1 = w.gns.update(Point(40.1, -74.0), 0.0, 120.0)
    w.gns2.update(Point(40.1, -74.0), 0.0, 120.0)
    own_i = instr.Ownship(Point(40.1, -74.0), 0.0, 0.0, 120.0, 5000.0, -13.0)
    panel = instr.compute_panel(own_i, nav1)
    sc = Scene(own=w.sim.state, nav=nav1, panel=panel, gns=w.gns, db=db,
              magvar=-13.0, layout="dual", gns2=w.gns2)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)                              # must not raise
    arr = pygame.surfarray.array2d(surf)
    assert arr.any()                                     # something was actually drawn


def test_dual_layout_without_a_second_unit_falls_back_to_single(db):
    """`--layout dual` without `--dual` (gns2 stays None): render() must not
    crash reaching for a unit that doesn't exist - it falls back to the
    ordinary single-unit "gps" placement."""
    w = _bare_world(db)                                   # gns2=None
    nav = w.gns.update(Point(40.1, -74.0), 0.0, 120.0)
    own_i = instr.Ownship(Point(40.1, -74.0), 0.0, 0.0, 120.0, 5000.0, -13.0)
    panel = instr.compute_panel(own_i, nav)
    sc = Scene(own=w.sim.state, nav=nav, panel=panel, gns=w.gns, db=db,
              magvar=-13.0, layout="dual", gns2=None)
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)                                # must not raise


# --------------------------------------------------------------------------- #
# "stack" layout - single-page IFR panel (see WORKING.md/ARCHITECTURE.md sec.8)
# --------------------------------------------------------------------------- #
def _stack_scene(w, db, *, gns2=None, stack_tab="WX"):
    nav = w.gns.update(Point(40.1, -74.0), 0.0, 120.0)
    own_i = instr.Ownship(Point(40.1, -74.0), 0.0, 0.0, 120.0, 5000.0, -13.0)
    panel = instr.compute_panel(own_i, nav)
    sp = instr.six_pack(own_i, -13.0)
    n1 = instr.nav_head(w.radios.nav1, own_i.pos, own_i.altitude_ft, own_i.gs_kt, -13.0)
    n2 = instr.nav_head(w.radios.nav2, own_i.pos, own_i.altitude_ft, own_i.gs_kt, -13.0)
    return Scene(own=w.sim.state, nav=nav, panel=panel, gns=w.gns, db=db, magvar=-13.0,
                layout="stack", gns2=gns2, sixpack=sp, nav1_head=n1, nav2_head=n2,
                ap=w.ap, radios=w.radios, ias_target=w.ias_target,
                ias_managed=w._ias_managed, stack_tab=stack_tab,
                wind_from_deg=w.sim.wind_from, wind_kt=w.sim.wind_kt)


def test_stack_layout_draws_all_columns_single_unit(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    sc = _stack_scene(w, db)
    r = Renderer(surf)
    r.draw(sc)                                             # must not raise
    arr = pygame.surfarray.array2d(surf)
    assert arr.any()
    # tab bar + AP bug boxes registered their click rects
    assert {"tab:WX", "tab:MAP", "tab:PLATE", "tab:SETTINGS"} <= r._stack_hit.keys()
    assert "bug:hdg:+" in r._stack_hit and "bug:alt:-" in r._stack_hit


def test_stack_hdg_actuals_shows_baro(db, monkeypatch):
    """Playtest: "Baro read out does not display in stacked" - it was
    missing from the layout entirely (no altimeter dial on "stack" the way
    "steam" has one). Added next to the heading indicator, alongside the
    existing actual HDG/IAS/ALT readout."""
    import dataclasses
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    w.baro_inhg = 30.11
    sc = _stack_scene(w, db)
    sc = dataclasses.replace(sc, baro_inhg=w.baro_inhg)
    r = Renderer(surf)
    calls = []
    orig_t, orig_lcd = Renderer._t, Renderer.lcd

    def recording_t(self, s, x, y, *args, **kwargs):
        calls.append(s)
        return orig_t(self, s, x, y, *args, **kwargs)

    def recording_lcd(self, value, x, y, *args, **kwargs):
        calls.append(value)
        return orig_lcd(self, value, x, y, *args, **kwargs)

    monkeypatch.setattr(Renderer, "_t", recording_t)
    monkeypatch.setattr(Renderer, "lcd", recording_lcd)
    r.draw(sc)
    assert "BARO" in calls
    assert f"{w.baro_inhg:.2f}" in calls


def test_stack_middle_column_is_fixed_width_not_window_width(db):
    """NAV1/NAV2 don't need the full remaining window width (playtest:
    "boxes are way too wide... allow the tabs... to get bigger") - the
    middle column is a fixed size regardless of window width, and the left
    tab column gets whatever room that leaves."""
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db))
    tab_w = r._stack_hit["tab:WX"].width
    # each of the 4 tabs is a quarter of the left column (minus a small
    # gap) - comfortably wider than the old fixed 340px-wide left column
    # produced (~81px/tab); this only holds if the middle column shrank
    assert tab_w > 100


def test_stack_layout_draws_with_a_second_unit(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _dual_world(db)
    sc = _stack_scene(w, db, gns2=w.gns2)
    Renderer(surf).draw(sc)                                # must not raise


@pytest.mark.parametrize("tab", ["WX", "MAP", "PLATE", "SETTINGS"])
def test_stack_layout_draws_every_tab(db, tab):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    sc = _stack_scene(w, db, stack_tab=tab)
    Renderer(surf).draw(sc)                                # must not raise, any tab selected


def _click(x, y):
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, y))


def test_stack_click_switches_tabs(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db, stack_tab="WX"))
    ui = {"layout": "stack", "stack_tab": "WX"}
    plate_rect = r._stack_hit["tab:PLATE"]
    main_mod._on_stack_click(_click(*plate_rect.center), w, ui, r)
    assert ui["stack_tab"] == "PLATE"


def test_stack_click_adjusts_heading_bug_and_alt_preselect(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db))
    ui = {"layout": "stack", "stack_tab": "WX"}
    hdg0 = w.ap.heading_bug
    alt0 = w.ap.alt_preselect
    main_mod._on_stack_click(_click(*r._stack_hit["bug:hdg:+"].center), w, ui, r)
    main_mod._on_stack_click(_click(*r._stack_hit["bug:alt:-"].center), w, ui, r)
    assert w.ap.heading_bug == pytest.approx((hdg0 + 5) % 360)
    assert w.ap.alt_preselect == alt0 - 100.0


def test_stack_click_adjusts_wind_and_time_warp(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db, stack_tab="SETTINGS"))
    ui = {"layout": "stack", "stack_tab": "SETTINGS", "time_warp": 1}
    wind0 = w.sim.wind_kt
    main_mod._on_stack_click(_click(*r._stack_hit["wind_kt:+"].center), w, ui, r)
    assert w.sim.wind_kt == wind0 + 5
    main_mod._on_stack_click(_click(*r._stack_hit["warp:10"].center), w, ui, r)
    assert ui["time_warp"] == 10


def _two_airport_world():
    """A world whose flight plan has two real airports (`db`'s own fixture
    only has enroute waypoints), for WX/PLATE airport-switching tests."""
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBOS", Point(42.36, -71.01)))
    d.add_airport(Airport("KLNS", Point(40.12, -76.30)))
    w = _bare_world(d)
    w.gns.load_flight_plan(["KBOS", "KLNS"])
    return w, d


def test_stack_wx_click_switches_which_airport_is_shown():
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w, d = _two_airport_world()
    r = Renderer(surf)
    r.draw(_stack_scene(w, d, stack_tab="WX"))
    assert w.gns.wx_sel == 0                               # departure by default
    assert "wx:airport:1" in r._stack_hit
    w.gns.wx_scroll = 5                                     # non-zero, to prove it resets
    ui = {"layout": "stack", "stack_tab": "WX"}
    main_mod._on_stack_click(_click(*r._stack_hit["wx:airport:1"].center), w, ui, r)
    assert w.gns.wx_sel == 1                                # now the destination
    assert w.gns.wx_scroll == 0


def test_stack_plate_click_switches_airport_and_resets_chart():
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w, d = _two_airport_world()
    r = Renderer(surf)
    r.draw(_stack_scene(w, d, stack_tab="PLATE"))
    assert "plate:airport:0" in r._stack_hit
    assert "plate:airport:1" in r._stack_hit
    w.gns.chart_sel = 3                                     # non-zero, to prove it resets
    ui = {"layout": "stack", "stack_tab": "PLATE"}
    main_mod._on_stack_click(_click(*r._stack_hit["plate:airport:1"].center), w, ui, r)
    assert w.gns.chart_airport_sel == 1
    assert w.gns.chart_sel == 0


def _fake_charts(codes):
    """A synthetic AUX>Charts index for one airport - `chart_code` values in
    the order given, each with a distinct `chart_name` so the fixture can
    tell rows apart."""
    from datasrc.dtpp import ChartRecord
    return [ChartRecord("PA", "TESTVILLE", "NE-3", "TST", "TEST FIELD", "N", str(i),
                        code, f"{code} PROCEDURE {i}", "", f"chart{i}.pdf")
            for i, code in enumerate(codes)]


def test_stack_plate_filter_chips_condense_a_busy_procedure_list(db, monkeypatch):
    """Playtest: "list of procedures is redundant/overflows with STR STR STR
    IAP IAP DP DP DP DP DP" - a bare row of chart_code was both meaningless
    (every approach showed "IAP") and could overflow. Filter chips (one per
    code actually present) plus a real chart-name list fix both."""
    from render import STACK_W, STACK_H
    w, d = _two_airport_world()
    charts = _fake_charts(["STAR", "STAR", "IAP", "IAP", "DP", "DP", "DP"])
    monkeypatch.setattr(Renderer, "_dtpp_charts_for", lambda self, ident: charts)
    surf = pygame.Surface((STACK_W, STACK_H))
    r = Renderer(surf)
    r.draw(_stack_scene(w, d, stack_tab="PLATE"))
    # one filter chip per distinct code, plus ALL
    assert {"plate:filter:ALL", "plate:filter:STAR", "plate:filter:IAP",
           "plate:filter:DP"} <= r._stack_hit.keys()
    # rows are keyed by index into the *unfiltered* list and only the
    # visible window (room=4) is registered - distinct chart names, not a
    # wall of repeated codes
    assert any(k.startswith("plate:chart:") for k in r._stack_hit)


def test_stack_plate_filter_narrows_the_chart_list_and_snaps_selection(db, monkeypatch):
    import dataclasses
    from render import STACK_W, STACK_H
    w, d = _two_airport_world()
    charts = _fake_charts(["STAR", "IAP", "IAP", "DP"])
    monkeypatch.setattr(Renderer, "_dtpp_charts_for", lambda self, ident: charts)
    w.gns.chart_sel = 0                                    # currently on the STAR
    surf = pygame.Surface((STACK_W, STACK_H))
    r = Renderer(surf)
    sc = dataclasses.replace(_stack_scene(w, d, stack_tab="PLATE"), plate_filter="IAP")
    r.draw(sc)
    # STAR (index 0) is hidden by the IAP filter - selection snaps onto the
    # first IAP instead of staying pointed at a now-invisible chart
    assert w.gns.chart_sel in (1, 2)
    assert charts[w.gns.chart_sel].chart_code == "IAP"
    # only the two IAP rows are click targets, not the STAR/DP ones
    assert "plate:chart:1" in r._stack_hit and "plate:chart:2" in r._stack_hit
    assert "plate:chart:0" not in r._stack_hit and "plate:chart:3" not in r._stack_hit


def test_stack_plate_filter_click_updates_ui_state():
    from render import STACK_W, STACK_H
    w, d = _two_airport_world()
    charts = _fake_charts(["STAR", "IAP", "DP"])
    surf = pygame.Surface((STACK_W, STACK_H))
    r = Renderer(surf)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Renderer, "_dtpp_charts_for", lambda self, ident: charts)
        r.draw(_stack_scene(w, d, stack_tab="PLATE"))
        ui = {"layout": "stack", "stack_tab": "PLATE", "plate_filter": "ALL"}
        main_mod._on_stack_click(_click(*r._stack_hit["plate:filter:DP"].center), w, ui, r)
    assert ui["plate_filter"] == "DP"


def test_draw_ap_panel_show_info_false_wraps_the_vs_window_not_overflow():
    """`stack`'s AP panel (draw_ap_panel(..., show_info=False)) drops the
    side HDG BUG/ALT SEL info box - that readout now lives next to the HDG
    indicator instead (`Renderer._stack_hdg_info`), showing it twice was
    redundant - and, at this narrower width, the VS readout's column
    position (`vcol`, computed assuming a wide box like `steam`'s) used to
    run off the box's own right edge into whatever sits next to it.
    Confirms the wrap-onto-its-own-line fallback keeps everything inside."""
    from autopilot import Autopilot
    surf = pygame.Surface((600, 150))
    r = Renderer(surf)
    rect = pygame.Rect(50, 10, 380, 112)
    draw_ap_panel(surf, rect, Autopilot(), 0.0, r, show_info=False)
    arr = pygame.surfarray.array2d(surf)
    bg = arr[0, 0]
    assert not (arr[rect.right + 2:, :] != bg).any()


def test_draw_hdg_indicator_left_biases_the_dial():
    """The dial used to be centered in its box while the info column next
    to it (`Renderer._stack_hdg_actuals`) assumed a left-biased position
    (`_card_geometry`, the same one `draw_nav_head` uses) - mismatched, so
    the two overlapped (playtest: "heading indicator should be left aligned
    in its box so that it stops overlapping the ALT, IAS, HDG displays")."""
    surf = pygame.Surface((400, 300))
    r = Renderer(surf)
    rect = pygame.Rect(0, 0, 400, 300)
    render.draw_hdg_indicator(surf, rect, instr.SixPack(heading_deg=90.0), r)
    rad, cx, cy = render._card_geometry(rect, None)
    assert cx < rect.centerx          # left-biased, not centered


def test_stack_hdg_actuals_are_actuals_not_ap_setpoints(db):
    """F32: HDG/IAS/ALT next to the heading dial must read the aircraft's
    actual state, while the editable autopilot bugs stay under the tab
    column - a follow-up clarifying an earlier round had conflated the two
    (a single display that was both "the readout" and "the editable set
    point" next to the dial)."""
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    w.ap.heading_bug = 55.0            # deliberately different from actual heading
    w.ap.alt_preselect = 9000.0        # deliberately different from actual altitude
    sc = _stack_scene(w, db)
    assert sc.sixpack.heading_deg != w.ap.heading_bug
    assert sc.sixpack.altitude_ft != w.ap.alt_preselect
    r = Renderer(surf)
    r.draw(sc)                         # must not raise
    # the editable set-point boxes are still under the tab column
    assert "bug:hdg:+" in r._stack_hit and "bug:alt:-" in r._stack_hit


# --------------------------------------------------------------------------- #
# NAV1 round head slaved to the GNS's own CDI/VLOC switch (F33)
# --------------------------------------------------------------------------- #
def test_nav1_view_uses_gps_cdi_when_source_is_gps(db):
    """The round NAV1 head is wired like a real GPS-slaved analog CDI - GPS
    course deviation while the GNS's own CDI source is GPS, the tuned NAV1
    VOR/LOC receiver only once the CDI key selects VLOC."""
    sc = _scene(db)
    assert sc.nav.cdi_source == "GPS"
    r = Renderer(pygame.display.get_surface())
    nh, kind = r._nav1_view(sc)
    assert kind == "GPS"
    assert nh.valid
    assert nh.course_deg == pytest.approx(sc.panel.cdi.course_deg)
    assert nh.deflection == pytest.approx(sc.panel.cdi.deflection)


def test_nav1_view_falls_back_to_the_tuned_receiver_on_vloc(db):
    import dataclasses
    sc = _scene(db)
    sc = dataclasses.replace(sc, nav=dataclasses.replace(sc.nav, cdi_source="VLOC"))
    r = Renderer(pygame.display.get_surface())
    nh, kind = r._nav1_view(sc)
    assert kind is None
    assert nh is sc.nav1_head                # unchanged - the tuned NAV1 receiver


def test_draw_nav_head_gps_source_kind_labels_dtk_and_dis_not_obs_and_dme():
    """`source_kind="GPS"` swaps the VOR/LOC labels ("OBS"/"CRS" -> "DTK",
    "DME" -> "DIS") - a GPS "distance" isn't a DME reading, and there's no
    pilot-set OBS course to show, just the desired track."""
    from instruments import NavHead
    surf = pygame.Surface((300, 300))
    r = Renderer(surf)
    nh = NavHead(valid=True, ident="ALFA", course_deg=90.0, obs_deg=90.0,
                deflection=0.2, to_from="TO", dme_nm=12.3)
    render.draw_nav_head(surf, pygame.Rect(0, 0, 300, 300), nh, "NAV1", r,
                         source_kind="GPS")
    arr = pygame.surfarray.array2d(surf)
    assert arr.any()                          # must not raise, something drawn


def test_stack_layout_nav1_draws_without_raising_in_gps_and_vloc(db):
    """Integration smoke test for the full draw path - `Renderer._nav1_view`
    feeding `draw_nav_head` in the `stack` layout, both CDI-source branches."""
    import dataclasses
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    w.gns.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    w.gns.update(Point(40.0, -74.0), 0.0, 120.0)
    sc = _stack_scene(w, db)
    Renderer(surf).draw(sc)                   # GPS source - must not raise
    sc2 = dataclasses.replace(sc, nav=dataclasses.replace(sc.nav, cdi_source="VLOC"))
    Renderer(surf).draw(sc2)                  # VLOC source - must not raise


# --------------------------------------------------------------------------- #
# COM2/NAV2 tuning panel (single-unit stack) and FMS2 -> NAV2 (--dual) (F35)
# --------------------------------------------------------------------------- #
def test_stack_single_unit_has_a_com2_nav2_tuning_panel_as_tall_as_the_ap(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db))
    for key in ("com2", "nav2"):
        for act in ("swap", "mhz-", "mhz+", "khz-", "khz+"):
            assert f"radio:{key}:{act}" in r._stack_hit


def test_stack_dual_has_no_com2_nav2_panel(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _dual_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db, gns2=w.gns2))
    assert not any(k.startswith("radio:") for k in r._stack_hit)


def test_stack_radio_clicks_tune_com2_and_nav2(db):
    from render import STACK_W, STACK_H
    surf = pygame.Surface((STACK_W, STACK_H))
    w = _bare_world(db)
    r = Renderer(surf)
    r.draw(_stack_scene(w, db))
    ui = {"layout": "stack", "stack_tab": "WX"}
    c0, n0 = w.radios.com2.standby_mhz, w.radios.nav2.standby_mhz
    main_mod._on_stack_click(_click(*r._stack_hit["radio:com2:khz+"].center), w, ui, r)
    main_mod._on_stack_click(_click(*r._stack_hit["radio:nav2:mhz+"].center), w, ui, r)
    assert w.radios.com2.standby_mhz == pytest.approx(c0 + 0.025)
    assert w.radios.nav2.standby_mhz == pytest.approx(n0 + 1.0)
    act = w.radios.nav2.active_mhz
    main_mod._on_stack_click(_click(*r._stack_hit["radio:nav2:swap"].center), w, ui, r)
    assert w.radios.nav2.active_mhz == pytest.approx(n0 + 1.0)
    assert w.radios.nav2.standby_mhz == pytest.approx(act)


def test_nav2_view_follows_fms2_when_dual(db):
    import dataclasses
    w = _dual_world(db)
    w.gns.load_flight_plan(["ALFA", "BRAVO"])
    w.gns2.load_flight_plan(["ALFA", "BRAVO"])
    sc = _stack_scene(w, db, gns2=w.gns2)
    own_i = instr.Ownship(Point(40.1, -74.0), 0.0, 0.0, 120.0, 5000.0, -13.0)
    n2 = w.gns2.update(Point(40.1, -74.0), 0.0, 120.0)
    sc = dataclasses.replace(sc, panel2=instr.compute_panel(own_i, n2))
    r = Renderer(pygame.display.get_surface())
    nh, kind = r._nav2_view(sc)
    assert kind == "GPS" and nh.valid                    # FMS2 on GPS -> NAV2 shows GPS
    assert nh.course_deg == pytest.approx(sc.panel2.cdi.course_deg)
    w.gns2.toggle_cdi_source()                           # FMS2 CDI key -> VLOC
    n2v = w.gns2.update(Point(40.1, -74.0), 0.0, 120.0)  # next tick picks it up
    sc_v = dataclasses.replace(sc, panel2=instr.compute_panel(own_i, n2v))
    nh2, kind2 = r._nav2_view(sc_v)
    assert kind2 is None and nh2 is sc_v.nav2_head       # the tuned NAV2 receiver
    # FMS1's CDI source has no say over NAV2 in --dual
    w.gns2.toggle_cdi_source()                           # FMS2 back to GPS
    w.gns2.update(Point(40.1, -74.0), 0.0, 120.0)
    w.gns.toggle_cdi_source()                            # FMS1 -> VLOC
    w.gns.update(Point(40.1, -74.0), 0.0, 120.0)
    assert r._nav2_view(sc)[1] == "GPS"


def test_nav2_view_single_unit_is_always_the_raw_receiver(db):
    w = _bare_world(db)
    sc = _stack_scene(w, db)
    nh, kind = Renderer(pygame.display.get_surface())._nav2_view(sc)
    assert kind is None and nh is sc.nav2_head


def test_settings_wind_shows_the_loaded_winds_aloft_not_zero(db):
    """F37: the SETTINGS wind read the uniform wind (0 when a winds-aloft
    profile is what's actually loaded)."""
    import windsaloft
    w = _bare_world(db)
    w.sim.set_winds_aloft(windsaloft.parse_cli("3000:280/20 9000:300/35"))
    d, k = w.sim.current_wind()
    assert k > 0 and w.sim.wind_kt == 0
    assert (d, k) == w.sim._wind_here()
    w.sim.set_wind(90, 10)
    assert w.sim.current_wind() == (90.0, 10.0)


def test_gs_scale_draws_a_horizontal_needle_across_the_face_and_a_flag_when_invalid():
    """The glideslope is a horizontal needle sweeping ACROSS the head's face (dots
    down its left/right edges, as on a GA OBS/ILS indicator), replaced by a red
    "GS" flag when the glideslope isn't valid."""
    def px(valid, dfl):
        surf = pygame.Surface((240, 240))
        r = Renderer(surf)
        render._gs_scale(surf, 120, 120, 80, dfl, valid, r)
        return surf
    up = px(True, 0.5)                  # needle above centre (fly up)
    row = lambda s, y: [s.get_at((x, y))[:3] for x in range(60, 180)]
    ys = [y for y in range(240) if row(up, y).count(render.GPS_GREEN) >= 100]
    assert ys and max(ys) < 120         # a bar spanning the face, above centre
    off = px(False, 0.0)
    assert not any(row(off, y).count(render.GPS_GREEN) >= 100 for y in range(240))
    assert any(c == (66 * 1, 40, 40) for y in range(240) for c in row(off, y))   # the GS flag box


def test_manual_ap_course_shows_the_nav1_pointer_on_a_gps_cdi_and_auto_slaves_it_to_dtk(db):
    import instruments as instr
    from navmath import destination
    w = _bare_world(db)
    w.magvar = 0.0
    w.gns.load_flight_plan(["ALFA", "BRAVO"])
    ac = destination(w.gns.fpl.waypoints[0].pos, 45.0, 3.0)
    nav = w.gns.update(ac, 0.0, 120.0)
    own = instr.Ownship(ac, 0.0, 0.0, 120.0, 5000.0, 0.0)
    w.radios.nav1.set_obs(300.0)
    w.ap_course = "auto"
    assert w._gps_pointer() is None
    assert w._panel(own, nav).cdi.course_deg == pytest.approx(nav.dtk, abs=1.0)
    w.ap_course = "manual"
    assert w._gps_pointer() == pytest.approx(300.0)
    assert w._panel(own, nav).cdi.course_deg == pytest.approx(300.0)   # the pilot's pointer, not the DTK


def test_ap_course_defaults_to_manual_and_is_a_cli_flag():
    assert main_mod.parse_args(["--headless", "--no-device"]).ap_course == "manual"
    assert main_mod.parse_args(["--headless", "--no-device", "--ap-course", "auto"]).ap_course == "auto"


# --------------------------------------------------------------------------- #
# Class B/C/D airspace overlay on the moving map (F62)                       #
# --------------------------------------------------------------------------- #
def test_map_draws_class_airspace_rings_near_ownship(db):
    from navdata.model import Airspace

    near = Airspace(ident="NEAR", name="", cls="D", floor_ft=0, ceiling_ft=2500,
                    rings=((Point(39.9, -74.1), Point(39.9, -73.9), Point(40.1, -73.9),
                            Point(40.1, -74.1)),))
    far = Airspace(ident="FAR", name="", cls="B", floor_ft=0, ceiling_ft=10000,
                   rings=((Point(10.0, 10.0), Point(10.0, 10.1), Point(10.1, 10.1), Point(10.1, 10.0)),))
    db.add_airspace(near)
    db.add_airspace(far)
    surf = pygame.display.get_surface()
    surf.fill((0, 0, 0))
    Renderer(surf).draw(_scene(db))
    arr = pygame.surfarray.array2d(surf)
    assert (arr != arr[0, 0]).sum() > 5000   # still draws fine with airspace present


def test_map_with_no_airspaces_does_not_crash(db):
    surf = pygame.display.get_surface()
    Renderer(surf).draw(_scene(db))          # db.airspaces == [] by default - must be a no-op, not an error


def test_nrst_airspace_page_shows_alert_wording_when_ahead(db):
    """The Nearest Airspace page's status column should read the guide's own wording ("Ahead", not a bare
    distance) once the alert condition applies - F63."""
    from navdata.model import Airspace
    from ifr1 import Event, Mode
    from gpsnav import PAGE_GROUPS

    a, b = db.find("ALFA")[0].pos, db.find("BRAVO")[0].pos
    square = (Point(a.lat + 0.3, a.lon - 0.05), Point(a.lat + 0.3, a.lon + 0.05),
              Point(a.lat + 0.4, a.lon + 0.05), Point(a.lat + 0.4, a.lon - 0.05))
    db.add_airspace(Airspace(ident="ZZZ", name="", cls="D", floor_ft=0, ceiling_ft=2500, rings=(square,)))
    sc = _scene(db)
    g = sc.gns
    g.update(sc.own.pos, 0.0, 150.0)                      # heading north (toward the box) at high speed
    g.cursor.group = list(PAGE_GROUPS).index("NRST")
    g.cursor.page = PAGE_GROUPS["NRST"].index("Nearest Airspace")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    surf = pygame.display.get_surface()
    Renderer(surf).draw(sc)                               # must not raise; the category lookup runs at draw time
