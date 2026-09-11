"""M8 - the GPS core is variant-independent; 530 / 430 are thin views.

Re-runs a slice of the navigator scenario suite against ``GpsNav`` directly
(no unit binding), checks the two ``Variant`` configs, and asserts the 430
view truncates a long flight-plan list to its smaller screen.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from navmath import Point  # noqa: E402
from navdata.model import NavDatabase, VhfNavaid, Waypoint  # noqa: E402
from gpsnav import GpsNav, Variant, VARIANT_430, VARIANT_530  # noqa: E402
from gns530 import Gns530  # noqa: E402
from gns430 import Gns430  # noqa: E402
import render as render_mod  # noqa: E402

ALFA = Point(40.0, -74.0)
BRAVO = Point(40.5, -74.0)
CHAR = Point(41.0, -74.0)


@pytest.fixture
def db():
    d = NavDatabase(source="test")
    d.add_waypoint(Waypoint("ALFA", ALFA))
    d.add_waypoint(Waypoint("BRAVO", BRAVO))
    d.add_waypoint(Waypoint("CHAR", CHAR))
    d.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))
    return d


# --------------------------------------------------------------------------- #
# the core does not fork
# --------------------------------------------------------------------------- #
def test_gpsnav_runs_the_scenario_without_a_unit_binding(db):
    g = GpsNav(db)                       # bare core, default variant
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.1, -74.0), 0.0, 120.0)
    assert g.fpl.to_wp.ident == "BRAVO"
    g.update(Point(40.55, -74.0), 0.0, 120.0)          # past BRAVO -> sequences
    assert g.fpl.to_wp.ident == "CHAR"
    ns = g.update(Point(41.05, -74.0), 0.0, 120.0)     # past CHAR -> SUSP at the end
    assert g.suspended and "SUSP" in ns.annunciators


def test_530_and_430_are_thin_views_over_the_same_core(db):
    assert issubclass(Gns530, GpsNav) and issubclass(Gns430, GpsNav)
    g530, g430 = Gns530(db), Gns430(db)
    assert g530.variant is VARIANT_530
    assert g430.variant is VARIANT_430
    # identical avionics: same NavState from the same inputs
    for g in (g530, g430):
        g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    a = g530.update(Point(40.2, -74.05), 10.0, 130.0)
    b = g430.update(Point(40.2, -74.05), 10.0, 130.0)
    assert (a.dtk, a.xtk_nm, a.to_ident, a.mode) == (b.dtk, b.xtk_nm, b.to_ident, b.mode)


# --------------------------------------------------------------------------- #
# variant view config
# --------------------------------------------------------------------------- #
def test_430_screen_is_shorter_than_the_530():
    assert VARIANT_430.screen_rows < VARIANT_530.screen_rows
    assert VARIANT_430.bezel_aspect > VARIANT_530.bezel_aspect   # wider / shorter
    assert VARIANT_430.short == "430" and VARIANT_530.short == "530"


def test_both_faceplate_svgs_are_vendored():
    assert render_mod._bezel_svg(VARIANT_530).is_file()
    assert render_mod._bezel_svg(VARIANT_430).is_file()


def test_visible_fpl_rows_truncates_for_the_430():
    n = 18
    assert render_mod.visible_fpl_rows(n, VARIANT_530.screen_rows) == VARIANT_530.screen_rows
    assert render_mod.visible_fpl_rows(n, VARIANT_430.screen_rows) == VARIANT_430.screen_rows
    # a short plan is shown in full on either unit
    assert render_mod.visible_fpl_rows(3, VARIANT_430.screen_rows) == 3
    # a tighter pixel budget wins over screen_rows
    assert render_mod.visible_fpl_rows(n, VARIANT_530.screen_rows, space_rows=4) == 4


def test_custom_variant_flows_through_unchanged(db):
    tiny = Variant(name="TINY", short="t", screen_rows=2, screen_px=(80, 40),
                   bezel_dir="garmin-gns-430", bezel_aspect=2.0,
                   screen_frac=(0.1, 0.1, 0.6, 0.8))
    g = GpsNav(db, variant=tiny)
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    assert g.variant.screen_rows == 2
    assert render_mod.visible_fpl_rows(len(g.fpl), g.variant.screen_rows) == 2


# --------------------------------------------------------------------------- #
# the 430 view renders headless
# --------------------------------------------------------------------------- #
def test_430_gps_layout_draws_and_truncates(db):
    import pygame
    import sim_model as simmod
    import main as main_mod

    pygame.init()
    surf = pygame.display.set_mode((1000, 640))
    try:
        g = Gns430(db)
        g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
        # pad the plan well past the 430's row count
        from gpsnav import PlanWaypoint
        for i in range(15):
            g.fpl.append(PlanWaypoint(f"X{i:02d}", Point(41.0 + i * 0.1, -74.0)))
        sim = simmod.SimModel(pos=Point(40.1, -74.0), heading_deg=0.0, tas_kt=120.0)
        nav, panel, st = main_mod.step_once(g, sim, 0.1, autopilot=False, magvar=0.0)
        sc = render_mod.Scene(own=st, nav=nav, panel=panel, gns=g, db=db,
                              variant=g.variant, map_range_nm=20.0)
        render_mod.Renderer(surf).draw(sc)              # must not raise
        assert render_mod.visible_fpl_rows(len(g.fpl), g.variant.screen_rows) == 5
    finally:
        pygame.quit()
