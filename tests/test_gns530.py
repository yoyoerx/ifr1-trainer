"""Scenario + unit tests for the GNS 530 state machine (no hardware, no real data).

Geometry is hand-placed on the -74 deg meridian so desired tracks are ~0 deg
(north) and cross-track signs are easy to reason about: north-bound, "right of
course" == east == +xtk.
"""

import math
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point, angle_diff, destination, great_circle_nm  # noqa: E402
from navdata.model import (  # noqa: E402
    Airport,
    LegType,
    NavDatabase,
    Procedure,
    ProcedureLeg,
    VhfNavaid,
    Waypoint,
)
from ifr1 import Event, Mode  # noqa: E402
from gns530 import FlightPlan, Gns530, PlanWaypoint, PAGE_GROUPS  # noqa: E402

# four fixes: ALFA -> BRAVO -> CHAR northbound, then DELT to the east
ALFA = Point(40.0, -74.0)
BRAVO = Point(40.5, -74.0)
CHAR = Point(41.0, -74.0)
DELT = Point(41.0, -73.0)


@pytest.fixture
def db():
    d = NavDatabase(source="test")
    d.add_waypoint(Waypoint("ALFA", ALFA))
    d.add_waypoint(Waypoint("BRAVO", BRAVO))
    d.add_waypoint(Waypoint("CHAR", CHAR))
    d.add_waypoint(Waypoint("DELT", DELT))
    d.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))
    return d


@pytest.fixture
def g(db):
    return Gns530(db)


def _near(a, b, tol=1.0):
    return min(abs(a - b), 360 - abs(a - b)) <= tol


# --------------------------------------------------------------------------- #
# flight-plan editing
# --------------------------------------------------------------------------- #
def test_load_flight_plan_resolves_and_sets_active(g):
    missing = g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    assert missing == []
    assert len(g.fpl) == 4
    assert g.fpl.active == 1
    assert g.fpl.from_wp.ident == "ALFA" and g.fpl.to_wp.ident == "BRAVO"


def test_load_flight_plan_reports_unresolved(g):
    missing = g.load_flight_plan(["ALFA", "NOPE", "CHAR"])
    assert missing == ["NOPE"]
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "CHAR"]


def test_insert_and_delete_shift_active():
    fpl = FlightPlan([PlanWaypoint("A", ALFA), PlanWaypoint("C", CHAR)], active=1)
    fpl.insert(1, PlanWaypoint("B", BRAVO))          # at the active slot -> fly to B next
    assert [w.ident for w in fpl.waypoints] == ["A", "B", "C"]
    assert fpl.active == 1 and fpl.to_wp.ident == "B"
    fpl.delete(0)                                     # remove behind active
    assert [w.ident for w in fpl.waypoints] == ["B", "C"]
    assert fpl.active == 1


def test_activate_leg(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.fpl.activate_leg(3)
    assert g.fpl.from_wp.ident == "CHAR" and g.fpl.to_wp.ident == "DELT"


def test_load_procedure_fix_legs_dedupe_and_merge_hold_flag(db):
    db.add_procedure(Procedure(
        airport="KTST", ident="RNV-Z", kind="approach", route_type="R",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA", is_iaf=True),
            ProcedureLeg(20, LegType.HF, fix_ident="ALFA"),   # hold at the IAF -> merge
            ProcedureLeg(30, LegType.TF, fix_ident="BRAVO"),
            ProcedureLeg(50, LegType.DF, fix_ident="CHAR"),
        )},
    ))
    g = Gns530(db)
    added = g.load_procedure("KTST", "RNV-Z")
    assert added == 3
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO", "CHAR"]
    assert g.fpl.waypoints[0].is_iaf and g.fpl.waypoints[0].hold   # HF merged onto ALFA


def test_load_procedure_synthesises_non_fix_legs(db):
    db.add_procedure(Procedure(
        airport="KTST", ident="RNV-Y", kind="approach", route_type="R",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="CHAR", is_map=True),   # MAP
            ProcedureLeg(40, LegType.CA, course_mag=10.0, alt1_ft=2000),   # climb to 2000
            ProcedureLeg(50, LegType.DF, fix_ident="DELT"),               # MAHP-ish
        )},
    ))
    g = Gns530(db)
    added = g.load_procedure("KTST", "RNV-Y")
    idents = [w.ident for w in g.fpl.waypoints]
    assert idents[:3] == ["ALFA", "BRAVO", "CHAR"]
    assert added == 5 and len(idents) == 5           # CA leg was synthesised, not skipped
    assert g.fpl.waypoints[3].synthetic              # the "(2000)" altitude fix
    assert g.fpl.waypoints[1].is_faf and g.fpl.waypoints[2].is_map


def test_approach_resolves_runway_fix_and_stages_the_ils_frequency(db):
    from navdata.model import Airport, Runway
    apt = Airport("KTST", Point(41.0, -74.05), elev_ft=300)
    apt.runways["RW08"] = Runway("RW08", Point(41.0, -74.05), 80.0, ils_ident="ITST")
    db.add_airport(apt)
    db.add_vhf(VhfNavaid("ITST", Point(41.0, -74.02), 110.30, nav_class="ILSW"))
    db.add_procedure(Procedure(
        airport="KTST", ident="I08", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True, recnav_ident="ITST"),
            ProcedureLeg(30, LegType.CF, fix_ident="RW08", is_map=True, recnav_ident="ITST"),
            ProcedureLeg(40, LegType.CA, course_mag=80.0, alt1_ft=2000),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "I08")
    idents = [w.ident for w in g.fpl.waypoints]
    assert "RW08" in idents                            # runway MAP fix resolved
    rw = g.fpl.waypoints[idents.index("RW08")]
    assert rw.is_map and rw.pos == apt.runways["RW08"].threshold
    assert g.approach_freq == pytest.approx(110.30)    # staged from the runway ILS
    assert g.approach_ref == "ITST"
    assert g.fpl.waypoints[idents.index("BRAVO")].is_faf


def test_suspends_at_the_missed_approach_point(db):
    # ALFA -> BRAVO(FAF) -> CHAR(MAP) -> DELT ; the plan should stop at CHAR
    db.add_procedure(Procedure(
        airport="KTST", ident="ILS", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.CF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.CF, fix_ident="CHAR", is_map=True),
            ProcedureLeg(40, LegType.DF, fix_ident="DELT"),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "ILS")
    g.fpl.activate_leg(2)                             # BRAVO -> CHAR
    g.update(Point(40.9, -74.0), 0.0, 120.0)
    ns = g.update(Point(41.001, -74.0), 0.0, 120.0)   # over CHAR (the MAP)
    assert g.suspended and ns.mode == "SUSP"
    assert g.fpl.to_wp.ident == "CHAR"               # did NOT run on to DELT
    g.toggle_suspend()                                # pilot commits to the miss
    g.update(Point(41.01, -74.0), 0.0, 120.0)
    assert g.fpl.to_wp.ident == "DELT"


# --------------------------------------------------------------------------- #
# holding patterns - GNS 530W actually flies the racetrack, not just SUSP
# --------------------------------------------------------------------------- #
def _fly_needle(g, pos, gs_kt=130.0, max_steps=2000, dt=1.0):
    """Drive ``g.update()`` with a simple xtk-correcting kinematic step -
    enough to exercise the hold-flying state machine deterministically
    without a full SimModel. Returns the list of ``NavState`` seen."""
    trk = 0.0
    seen = []
    for _ in range(max_steps):
        ns = g.update(pos, trk, gs_kt, dt)
        seen.append(ns)
        if ns.dtk is None:
            break
        hdg = ns.dtk
        if ns.xtk_nm is not None:
            hdg = (hdg - max(-30.0, min(30.0, ns.xtk_nm * 15.0))) % 360.0
        trk = hdg
        pos = destination(pos, hdg, gs_kt * dt / 3600.0)
    return seen


def test_hold_in_lieu_of_pt_is_flown_then_auto_continues(db):
    # BRAVO -> CHAR(HF hold, inbound 000/R) -> DELT, with DELT roughly due
    # south of CHAR - a near-180 reversal, exactly the DALAC-style geometry
    # a hold-in-lieu-of-PT exists to resolve.
    db.add_waypoint(Waypoint("DELT2", Point(39.9, -74.0)))
    db.add_procedure(Procedure(
        airport="KTST", ident="I36", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="BRAVO"),
            ProcedureLeg(30, LegType.HF, fix_ident="CHAR", turn="R",
                        course_mag=0.0, time_min=1.0, is_iaf=True),
            ProcedureLeg(40, LegType.TF, fix_ident="DELT2"),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "I36")
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    assert char.hold and char.hold_single_circuit
    assert char.hold_turn == "R" and char.hold_inbound_true == pytest.approx(0.0)

    g.fpl.activate_leg(g.fpl.index_of("CHAR"))
    seen = _fly_needle(g, Point(40.9, -74.0))           # arriving from the south, heading ~000
    modes = {ns.mode for ns in seen}
    assert "HOLD" in modes                              # it actually flew the pattern...
    assert seen[-1].mode == "LEG"                        # ...then auto-continued
    assert g.fpl.to_wp.ident == "DELT2"                  # past the hold, no pilot action needed
    assert not g.suspended and g._hold_state is None


def _hold_procedure(db):
    """A hold-in-lieu-of-PT at CHAR (inbound 000/R, single circuit), same
    shape as `test_hold_in_lieu_of_pt_is_flown_then_auto_continues`, for the
    INBOUND-capture tests below."""
    db.add_waypoint(Waypoint("DELT2", Point(39.9, -74.0)))
    db.add_procedure(Procedure(
        airport="KTST", ident="I36", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="BRAVO"),
            ProcedureLeg(30, LegType.HF, fix_ident="CHAR", turn="R",
                        course_mag=0.0, time_min=1.0, is_iaf=True),
            ProcedureLeg(40, LegType.TF, fix_ident="DELT2"),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "I36")
    g.fpl.activate_leg(g.fpl.index_of("CHAR"))
    return g


# --------------------------------------------------------------------------- #
# FINDINGS F17: the hold's INBOUND leg used to "capture" (and report DTG) off
# along-track progress on a 60 nm reference line, which reads near-zero while
# the aircraft is still well off the inbound centerline (e.g. mid-turn out of
# a wide entry) - sequencing onto the next leg, cutting a corner toward it,
# well before the aircraft was ever near the fix.
# --------------------------------------------------------------------------- #
def test_hold_inbound_does_not_capture_early_with_a_large_cross_track_offset(db):
    g = _hold_procedure(db)
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    g._start_hold(char, arrival_true=0.0, gs_kt=130.0)
    hs = g._hold_state
    hs["phase"] = "INBOUND"
    # along-track on the 60 nm reference line is ~59.9 nm (i.e. "nearly
    # captured" under the old along-track-only check) but the aircraft is
    # genuinely 2 nm east of the actual fix.
    near_along = destination(char.pos, hs["outbound"], 0.1)
    pos = destination(near_along, 90.0, 2.0)
    ns = g.update(pos, 0.0, 130.0, 1.0)
    assert ns.mode == "HOLD" and g._hold_state is not None       # not captured yet
    assert ns.dtg_nm == pytest.approx(great_circle_nm(pos, char.pos), abs=0.01)
    assert ns.dtg_nm > 1.5                                       # honest distance, not ~0


def test_hold_inbound_captures_cleanly_once_genuinely_near_the_fix(db):
    g = _hold_procedure(db)
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    g._start_hold(char, arrival_true=0.0, gs_kt=130.0)
    hs = g._hold_state
    hs["phase"] = "INBOUND"
    pos = destination(char.pos, hs["outbound"], 0.1)             # 0.1 nm short, on course
    ns = g.update(pos, 0.0, 130.0, 1.0)
    assert ns.mode == "LEG" and g._hold_state is None            # single-circuit auto-continued
    assert g.fpl.to_wp.ident == "DELT2"


def test_hold_inbound_eventually_completes_at_the_closest_approach(db):
    """A wide entry that never quite reaches `_FIX_CAPTURE_NM` must still
    complete - at its closest approach - rather than fly past the fix and
    away forever chasing an exact hit it will never land."""
    g = _hold_procedure(db)
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    g._start_hold(char, arrival_true=0.0, gs_kt=130.0)
    hs = g._hold_state
    hs["phase"] = "INBOUND"
    far = destination(char.pos, hs["outbound"], 60.0)   # "a" - the 60 nm reference point
    # walk inbound along-track from ~5 nm short of the fix to ~5 nm past it,
    # holding a constant 1.5 nm cross-track offset throughout (never closes
    # inside `_FIX_CAPTURE_NM`) - closest real approach is exactly at the fix
    ns = None
    for dist_from_far in range(55, 66):
        center = destination(far, hs["inbound"], float(dist_from_far))
        pos = destination(center, 90.0, 1.5)
        ns = g.update(pos, 0.0, 130.0, 1.0)
        if g._hold_state is None:
            break
    assert g._hold_state is None                                 # did complete, didn't hang
    assert ns.mode == "LEG" and g.fpl.to_wp.ident == "DELT2"


def test_hold_stays_indefinitely_until_pilot_releases_suspend(db):
    # a plain HM (e.g. a missed-approach hold) repeats until released.
    db.add_procedure(Procedure(
        airport="KTST", ident="I18", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="BRAVO"),
            ProcedureLeg(30, LegType.HM, fix_ident="CHAR", turn="R",
                        course_mag=0.0, time_min=1.0),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "I18")
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    assert char.hold and not char.hold_single_circuit

    g.fpl.activate_leg(g.fpl.index_of("CHAR"))
    pos = Point(40.9, -74.0)
    trk = 0.0
    # phase 1: fly up to and into the hold
    for _ in range(400):
        ns = g.update(pos, trk, 130.0, 1.0)
        if ns.mode == "HOLD":
            break
        hdg = ns.dtk - max(-30.0, min(30.0, (ns.xtk_nm or 0.0) * 15.0)) if ns.dtk is not None else trk
        trk = hdg % 360.0
        pos = destination(pos, trk, 130.0 / 3600.0)
    assert ns.mode == "HOLD"
    # phase 2: keep flying well past one lap - it must NOT auto-exit (HM, no release yet)
    for _ in range(600):
        hdg = ns.dtk - max(-30.0, min(30.0, (ns.xtk_nm or 0.0) * 15.0))
        trk = hdg % 360.0
        pos = destination(pos, trk, 130.0 / 3600.0)
        ns = g.update(pos, trk, 130.0, 1.0)
        if g._hold_state and g._hold_state["lap"] >= 2:
            break
    assert ns.mode == "HOLD" and g._hold_state is not None   # still circling
    assert g._hold_state["lap"] >= 2                          # started a second lap
    g.toggle_suspend()                                        # pilot releases it
    seen = _fly_needle(g, pos)
    assert seen[-1].mode in ("SUSP", "LEG")                   # finished the lap and stopped
    assert g._hold_state is None


def test_toggle_suspend_arms_and_disarms_the_hold_exit(db):
    db.add_procedure(Procedure(
        airport="KTST", ident="I18", kind="approach", route_type="I",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.HM, fix_ident="BRAVO", turn="R",
                        course_mag=0.0, time_min=1.0),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "I18")
    g.fpl.activate_leg(g.fpl.index_of("BRAVO"))
    g.update(Point(40.499, -74.0), 0.0, 130.0, 1.0)   # within capture range - trips the hold
    assert g._hold_state is not None
    g.toggle_suspend()
    assert g._hold_state["exit_requested"]
    g.toggle_suspend()                              # pressed again -> cancels the release
    assert not g._hold_state["exit_requested"]


# --------------------------------------------------------------------------- #
# PROC key - approach / arrival / departure selector
# --------------------------------------------------------------------------- #
def _fms(**kw):
    return Event(mode=Mode.FMS1, **kw)


@pytest.fixture
def gp(db):
    """A 530 whose plan ends at KEND, which has one ILS approach (two
    transitions: 'ALFA' plus a common final) and one SID."""
    db.add_airport(Airport("KEND", CHAR))
    db.add_procedure(Procedure(
        airport="KEND", ident="I05", kind="approach", route_type="I",
        transitions={
            "ALFA": (ProcedureLeg(10, LegType.IF, fix_ident="ALFA", is_iaf=True),
                     ProcedureLeg(20, LegType.TF, fix_ident="BRAVO", is_faf=True)),
            "": (ProcedureLeg(40, LegType.CF, fix_ident="CHAR", is_map=True),),
        },
    ))
    db.add_procedure(Procedure(
        airport="KEND", ident="EXITT1", kind="sid", route_type="D",
        transitions={"": (ProcedureLeg(10, LegType.IF, fix_ident="DELT"),)},
    ))
    g = Gns530(db)
    g.load_flight_plan(["ALFA", "KEND"])
    return g


def test_proc_menu_lists_only_the_available_kinds(gp):
    assert gp.begin_proc_select()
    d = gp._proc_dialog
    assert d.step == "MENU" and d.airport == "KEND"
    assert d.options == ["SELECT APPROACH", "SELECT DEPARTURE"]   # no STAR in the db


def test_proc_key_with_no_destination_airport_posts_a_message(g):
    g.load_flight_plan(["ALFA", "BRAVO"])            # ends at a plain fix
    assert g.begin_proc_select() is False
    assert g._proc_dialog is None
    assert any("NO DESTINATION" in m for m in g.messages)


def test_proc_select_loads_an_approach_with_its_transition(gp):
    """Pilot's Guide p.61 step 5: after picking the transition, the wizard
    stops at a "Load?"/"Activate?" choice before actually loading - it
    doesn't load immediately."""
    gp.begin_proc_select()
    gp.handle_event(_fms(pressed=("ENT",)))          # SELECT APPROACH
    assert gp._proc_dialog.step == "PROC" and gp._proc_dialog.options == ["I05"]
    gp.handle_event(_fms(pressed=("ENT",)))          # pick I05
    assert gp._proc_dialog.step == "TRANS"
    assert gp._proc_dialog.options == ["VECTORS", "ALFA"]
    gp.handle_event(_fms(inner=1))                   # scroll to the ALFA transition
    gp.handle_event(_fms(pressed=("ENT",)))          # -> Load?/Activate?
    assert gp._proc_dialog.step == "LOADACT"
    assert gp._proc_dialog.options == ["Load?", "Activate?"]
    assert gp._proc_dialog.current == "Load?"        # "Load?" is the default highlight
    gp.handle_event(_fms(pressed=("ENT",)))          # confirm Load?
    assert gp._proc_dialog is None
    idents = [w.ident for w in gp.fpl.waypoints]
    assert idents[-3:] == ["ALFA", "BRAVO", "CHAR"]  # IAF transition + final + MAP
    assert gp.fpl.waypoints[-1].is_map
    assert gp.cursor.page_name == "Flight Plan"      # jumps to the plan to review
    assert gp.fpl.to_wp.ident != "ALFA"              # loaded only - not activated


def test_proc_select_activate_loads_and_activates_in_one_pass(gp):
    """The actual playtest complaint: activating a just-selected procedure
    took a separate PROC-menu round trip after loading. Per the Pilot's
    Guide (p.61 step 5), "Activate?" at the Load?/Activate? step loads AND
    starts flying it immediately - no second PROC key press needed."""
    gp.begin_proc_select()
    gp.handle_event(_fms(pressed=("ENT",)))          # SELECT APPROACH
    gp.handle_event(_fms(pressed=("ENT",)))          # pick I05
    gp.handle_event(_fms(inner=1))                   # scroll to the ALFA transition
    gp.handle_event(_fms(pressed=("ENT",)))          # -> Load?/Activate?
    gp.handle_event(_fms(outer=1))                   # highlight Activate?
    assert gp._proc_dialog.current == "Activate?"
    gp.handle_event(_fms(pressed=("ENT",)))          # confirm Activate?
    assert gp._proc_dialog is None
    idents = [w.ident for w in gp.fpl.waypoints]
    assert idents[-3:] == ["ALFA", "BRAVO", "CHAR"]
    assert gp.fpl.to_wp.ident == "ALFA"              # already flying the IAF
    assert not gp.suspended


def test_proc_select_vectors_loads_only_the_final_segment(gp):
    gp.begin_proc_select()
    gp.handle_event(_fms(pressed=("ENT",)))          # SELECT APPROACH
    gp.handle_event(_fms(pressed=("ENT",)))          # pick I05
    gp.handle_event(_fms(pressed=("ENT",)))          # sel 0 == VECTORS -> Load?/Activate?
    assert gp._proc_dialog.step == "LOADACT"
    gp.handle_event(_fms(pressed=("ENT",)))          # confirm Load? (no transition)
    assert gp._proc_dialog is None
    idents = [w.ident for w in gp.fpl.waypoints]
    assert idents == ["ALFA", "KEND", "CHAR"]        # no ALFA/BRAVO transition spliced in


def test_proc_select_clr_steps_back_one_level_then_closes(gp):
    gp.begin_proc_select()
    gp.handle_event(_fms(pressed=("ENT",)))          # -> PROC
    gp.handle_event(_fms(pressed=("ENT",)))          # -> TRANS
    assert gp._proc_dialog.step == "TRANS"
    gp.handle_event(_fms(pressed=("ENT",)))          # -> LOADACT
    assert gp._proc_dialog.step == "LOADACT"
    gp.handle_event(_fms(pressed=("CLR",)))
    assert gp._proc_dialog.step == "TRANS"
    gp.handle_event(_fms(pressed=("CLR",)))
    assert gp._proc_dialog.step == "PROC"
    gp.handle_event(_fms(pressed=("CLR",)))
    assert gp._proc_dialog.step == "MENU"
    gp.handle_event(_fms(pressed=("CLR",)))
    assert gp._proc_dialog is None


def test_proc_select_loadact_back_skips_to_proc_when_trans_had_no_choices(gp):
    """A SID has no named transition here, so TRANS is skipped straight to
    LOADACT - CLR from there must go back to PROC, not a TRANS step that was
    never actually shown."""
    gp.begin_proc_select()
    gp.handle_event(_fms(outer=1))                   # highlight SELECT DEPARTURE
    gp.handle_event(_fms(pressed=("ENT",)))
    gp.handle_event(_fms(pressed=("ENT",)))          # pick EXITT1 -> straight to LOADACT
    assert gp._proc_dialog.step == "LOADACT"
    assert gp._proc_dialog.options == ["Load?"]      # SIDs never offer Activate? here
    gp.handle_event(_fms(pressed=("CLR",)))
    assert gp._proc_dialog.step == "PROC"


def test_proc_menu_activate_vtf_appears_after_an_approach_is_loaded(gp):
    gp.load_procedure("KEND", "I05", "ALFA")
    assert gp._approach_active
    gp.suspended = True
    gp.begin_proc_select()
    opts = gp._proc_dialog.options
    assert "ACTIVATE APPROACH" in opts and "ACTIVATE VECTORS-TO-FINAL" in opts
    gp._proc_dialog.sel = opts.index("ACTIVATE VECTORS-TO-FINAL")
    gp.handle_event(_fms(pressed=("ENT",)))
    assert gp._proc_dialog is None
    assert not gp.suspended
    assert gp.fpl.to_wp.ident == "BRAVO"             # steered straight to the FAF


# --------------------------------------------------------------------------- #
# derived nav state
# --------------------------------------------------------------------------- #
def test_update_reports_leg_dtk_and_zero_xtk_on_course(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    ns = g.update(Point(40.1, -74.0), track_deg=0.0, gs_kt=120.0)
    assert ns.valid and ns.mode == "LEG"
    assert ns.from_ident == "ALFA" and ns.to_ident == "BRAVO"
    assert _near(ns.dtk, 0.0)
    assert ns.xtk_nm == pytest.approx(0.0, abs=0.05)
    assert ns.dist_nm == pytest.approx(24.0, abs=1.0)


def test_xtk_positive_when_right_of_course(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    ns = g.update(Point(40.25, -73.9), track_deg=0.0, gs_kt=120.0)   # east of line
    assert ns.xtk_nm > 1.0


def test_xtk_negative_when_left_of_course(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    ns = g.update(Point(40.25, -74.1), track_deg=0.0, gs_kt=120.0)   # west of line
    assert ns.xtk_nm < -1.0


def test_no_active_leg_is_nowpt(g):
    ns = g.update(ALFA, 0.0, 120.0)
    assert not ns.valid and ns.mode == "NOWPT"


# --------------------------------------------------------------------------- #
# sequencing -- fly the 3-leg plan
# --------------------------------------------------------------------------- #
def test_sequences_through_plan_then_suspends(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])

    g.update(Point(40.2, -74.0), 0.0, 120.0)
    assert g.fpl.to_wp.ident == "BRAVO"

    # overfly BRAVO
    g.update(Point(40.55, -74.0), 0.0, 120.0)
    assert g.fpl.to_wp.ident == "CHAR"

    # overfly CHAR
    g.update(Point(41.02, -74.0), 90.0, 120.0)
    assert g.fpl.to_wp.ident == "DELT"

    # overfly DELT (last leg) -> SUSP, active leg unchanged
    ns = g.update(Point(41.0, -72.9), 90.0, 120.0)
    assert g.suspended and ns.mode == "SUSP"
    assert "SUSP" in ns.annunciators


def test_wpt_alert_and_turn_anticipation_before_a_turn(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.fpl.activate_leg(3)                              # CHAR -> DELT, turn at DELT? no
    g.fpl.activate_leg(2)                              # BRAVO -> CHAR, then turn to DELT
    ns = g.update(Point(40.989, -74.0), 0.0, 120.0)    # ~0.65 nm short of CHAR
    assert ns.to_ident == "CHAR"
    assert ns.wpt_alert
    assert ns.turn_anticipation
    assert ns.next_dtk is not None and _near(ns.next_dtk, 90.0, tol=2.0)


def test_turn_anticipation_is_capped_for_a_near_reversal(db):
    # ALFA -> BRAVO -> CHAR, then CHAR -> a fix almost back where we came from
    # (~180 deg reversal, e.g. into a hold): turn_anticipation_nm's tan(x/2)
    # blows up near 180 deg: this must not sequence/alert from many miles out.
    db.add_waypoint(Waypoint("SOUTH", Point(39.9, -74.0)))
    g = Gns530(db)
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "SOUTH"])
    g.fpl.activate_leg(2)                              # BRAVO -> CHAR
    ns = g.update(Point(40.9, -74.0), 0.0, 130.0)      # ~6 nm short of CHAR
    assert ns.to_ident == "CHAR"                        # did not skip straight to SOUTH
    assert not ns.wpt_alert and not ns.turn_anticipation


def test_suspend_blocks_sequencing(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.toggle_suspend()
    g.update(Point(40.6, -74.0), 0.0, 120.0)           # past BRAVO
    assert g.fpl.to_wp.ident == "BRAVO"                # did not sequence


# --------------------------------------------------------------------------- #
# Direct-To
# --------------------------------------------------------------------------- #
def test_direct_to_fix_in_plan_keeps_plan_and_resumes(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(ALFA, 0.0, 120.0)
    assert g.direct_to("CHAR")
    ns = g.update(Point(40.3, -74.0), 0.0, 120.0)
    assert ns.mode == "DTO" and ns.to_ident == "CHAR"
    assert g.dto.fpl_index == 2
    # reach CHAR -> DTO clears, plan resumes on CHAR->DELT
    g.update(Point(41.02, -74.0), 0.0, 120.0)
    assert g.dto is None
    assert g.fpl.from_wp.ident == "CHAR" and g.fpl.to_wp.ident == "DELT"


def test_direct_to_off_plan_fix_then_suspends_at_fix(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(ALFA, 0.0, 120.0)
    assert g.direct_to("OOO")                          # the VOR, not in the plan
    ns = g.update(Point(40.1, -74.0), 0.0, 120.0)
    assert ns.mode == "DTO" and ns.to_ident == "OOO"
    g.update(Point(40.26, -74.0), 0.0, 120.0)          # overfly OOO
    assert g.dto is None and g.suspended


def test_direct_to_course_is_frozen_at_activation(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.0, -74.2), 0.0, 120.0)
    g.direct_to("BRAVO")
    course0 = g.dto.course
    ns = g.update(Point(40.2, -74.0), 0.0, 120.0)      # now east of the DTO line
    assert ns.dtk == pytest.approx(course0)
    assert ns.xtk_nm > 0.5                              # shows as cross-track


def test_direct_to_needs_a_position(db):
    g = Gns530(db)
    assert g.direct_to("BRAVO") is False               # no position yet


def test_clr_cancels_an_active_direct_to_and_resumes_the_nearest_leg(g):
    """Pilot's Guide sec.4: cancelling a Direct-To resumes the flight plan on
    whichever leg is nearest the present position. Previously ``CLR`` (via
    ``handle_event``, the actual IFR-1 input path) did nothing here - the
    Direct-To just stayed active forever, with no way to back out of it."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(ALFA, 0.0, 120.0)
    assert g.direct_to("DELT")
    g.update(Point(40.7, -74.0), 0.0, 120.0)           # off toward DELT, nowhere near it
    assert g.dto is not None
    g.handle_event(_fms(pressed=("CLR",)))
    assert g.dto is None
    ns = g.update(Point(40.7, -74.0), 0.0, 120.0)
    assert ns.mode == "LEG"                            # back on the plan, not DTO


# --------------------------------------------------------------------------- #
# OBS mode
# --------------------------------------------------------------------------- #
def test_obs_sets_dtk_and_disables_sequencing(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.set_obs(360.0)
    ns = g.update(Point(40.6, -74.0), 0.0, 120.0)      # past BRAVO
    assert ns.mode == "OBS" and "OBS" in ns.annunciators
    assert _near(ns.dtk, 360.0)
    assert g.fpl.to_wp.ident == "BRAVO"                # no sequencing in OBS
    assert ns.to_from == "FROM"                        # north of the fix, course north


def test_obs_to_from_before_fix(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.set_obs(360.0)
    ns = g.update(Point(40.2, -74.0), 0.0, 120.0)      # south of BRAVO
    assert ns.to_from == "TO"


def test_clear_obs_allows_sequencing_again(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.set_obs(360.0)
    g.update(Point(40.2, -74.0), 0.0, 120.0)
    g.clear_obs()
    g.update(Point(40.55, -74.0), 0.0, 120.0)
    assert g.fpl.to_wp.ident == "CHAR"


# --------------------------------------------------------------------------- #
# CDI source + events + expiry
# --------------------------------------------------------------------------- #
def test_swap_event_toggles_cdi_source(g):
    assert g.cdi_source == "GPS"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("SWAP",)))
    assert g.cdi_source == "VLOC"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("SWAP",)))
    assert g.cdi_source == "GPS"


def test_events_ignored_outside_fms_modes(g):
    g.handle_event(Event(mode=Mode.COM1, pressed=("SWAP",)))
    assert g.cdi_source == "GPS"


def test_dct_then_ent_ent_goes_direct_to_active_waypoint(g):
    """Pilot's Guide sec.4.1: ENT confirms the identifier ("Activate?"
    highlighted), a second ENT actually activates - always two presses, even
    to re-centre on the already-active waypoint ("Press the Direct-to Key,
    followed by the ENT Key twice")."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.0, -74.2), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog is not None and g._dto_dialog.ident() == "BRAVO"  # pre-filled
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # confirm ident
    assert g._dto_dialog is not None and g._dto_dialog.confirming
    assert g.dto is None                                          # not yet activated
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # activate
    assert g._dto_dialog is None
    ns = g.update(Point(40.1, -74.1), 0.0, 120.0)
    assert ns.mode == "DTO" and ns.to_ident == "BRAVO"


def test_dct_ent_ent_re_centres_on_the_same_already_active_waypoint(g):
    """The manual has no single-ENT shortcut, even when the destination is
    already the active Direct-To target - it's explicitly still "ENT twice"."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.0, -74.2), 0.0, 120.0)
    assert g.direct_to("BRAVO")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog.ident() == "BRAVO"                       # pre-filled w/ current DTO
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._dto_dialog is not None and g._dto_dialog.confirming  # still needs a 2nd ENT
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._dto_dialog is None
    assert g.dto is not None and g.dto.target.ident == "BRAVO"


def test_dto_confirming_ignores_knob_edits(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # -> confirming
    before = g._dto_dialog.ident()
    g.handle_event(Event(mode=Mode.FMS1, outer=2, inner=3))       # should be a no-op
    assert g._dto_dialog.ident() == before


def test_dto_clr_while_confirming_backs_out_to_editing_not_full_cancel(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # -> confirming
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g._dto_dialog is not None and not g._dto_dialog.confirming
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))       # 2nd CLR now closes it
    assert g._dto_dialog is None


def test_direct_to_seed_is_blank_once_the_plan_is_flown(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    for p in (Point(40.3, -74.0), Point(40.7, -74.0), Point(41.05, -74.0)):
        g.update(p, 0.0, 120.0)                          # fly past the end -> SUSP
    assert g.suspended
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog.ident() == ""                   # not the last waypoint


def test_direct_to_page_types_a_new_identifier(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    dlg = g._dto_dialog
    dlg.chars[:] = list("      ")                    # clear the pre-fill
    dlg.cursor = 0
    for want in "CHAR":                              # spin each character in
        steps = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".index(want)
        g.handle_event(Event(mode=Mode.FMS1, inner=steps))
        g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert dlg.ident() == "CHAR"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))     # confirm
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))     # activate
    assert g._dto_dialog is None
    ns = g.update(Point(40.2, -74.0), 0.0, 120.0)
    assert ns.mode == "DTO" and ns.to_ident == "CHAR"


def test_direct_to_page_rejects_an_unknown_identifier(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g._dto_dialog.chars[:] = list("ZZZZ  ")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._dto_dialog is not None                 # stays open
    assert any("ZZZZ" in m for m in g.messages)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g._dto_dialog is None


def test_direct_to_page_knob_does_not_move_the_page_cursor(g):
    grp0 = g.cursor.group
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, outer=2))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    assert g.cursor.group == grp0 and not g.cursor.cursor_on


def test_knob_selects_group_page_and_toggles_cursor(g):
    assert g.cursor.group_name == "NAV" and not g.cursor.cursor_on
    g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert g.cursor.group_name == "WPT"
    g.handle_event(Event(mode=Mode.FMS1, inner=2))
    assert g.cursor.page == 2                          # WPT / NDB
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    assert g.cursor.cursor_on
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    assert not g.cursor.cursor_on


def test_group_switch_remembers_the_page_it_was_on(g):
    """The real 530 is sticky per group: leaving WPT/NDB for another group
    and coming back returns to NDB, it doesn't reset to page 1."""
    g.handle_event(Event(mode=Mode.FMS1, outer=1))      # -> WPT
    g.handle_event(Event(mode=Mode.FMS1, inner=2))      # -> WPT / NDB
    assert g.cursor.group_name == "WPT" and g.cursor.page == 2
    g.handle_event(Event(mode=Mode.FMS1, outer=1))      # -> AUX
    assert g.cursor.group_name == "AUX" and g.cursor.page == 0
    g.handle_event(Event(mode=Mode.FMS1, outer=-1))     # back to WPT
    assert g.cursor.group_name == "WPT" and g.cursor.page == 2


def test_wpt_page_knob_types_an_identifier(g):
    g.cursor.group = list(PAGE_GROUPS).index("WPT")
    g.cursor.page = PAGE_GROUPS["WPT"].index("VOR")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))      # cursor on
    for want in "OOO":
        steps = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".index(want)
        g.handle_event(Event(mode=Mode.FMS1, inner=steps))
        g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert g.wpt_entry.ident() == "OOO"
    assert g.lookup(g.wpt_entry.ident()).ident == "OOO"          # resolves the VOR


def test_wpt_page_lookup_is_filtered_by_its_own_category(db):
    """A WPT sub-page must only ever resolve its own category (Pilot's Guide
    sec.4.2) - e.g. the VOR page shouldn't return an airport or intersection
    that happens to share the identifier. "ALFA" here is both a plain
    waypoint (from the shared fixture) and, added here, a VHF navaid and an
    airport."""
    db.add_vhf(VhfNavaid("ALFA", Point(40.01, -74.0), 114.0))
    db.add_airport(Airport("ALFA", Point(40.02, -74.0)))
    g = Gns530(db)
    assert g.lookup("ALFA", "Intersection").__class__.__name__ == "Waypoint"
    assert g.lookup("ALFA", "VOR").__class__.__name__ == "VhfNavaid"
    assert g.lookup("ALFA", "Airport").__class__.__name__ == "Airport"
    assert g.lookup("ALFA", "NDB") is None                       # no NDB by that ident
    assert g.lookup("ALFA") is not None                           # unfiltered (e.g. DTO) still resolves


def test_flight_plan_page_ent_inserts_before_the_selected_row(g):
    """Pilot's Guide sec.5.1: 'turn the large right knob to select the point
    to add the new waypoint - if an existing waypoint is highlighted, the
    new waypoint is placed directly in front of this waypoint.' ENT never
    overwrites the highlighted waypoint - it always inserts ahead of it."""
    g.load_flight_plan(["ALFA", "BRAVO", "DELT"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on -> row edit
    g._fpl_edit["row"] = 2                                       # DELT
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))      # open a blank insert field
    assert g._fpl_edit["buf"].ident() == ""                      # not pre-filled with DELT
    g._fpl_edit["buf"].chars[:] = list("CHAR  ")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))      # apply
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO", "CHAR", "DELT"]


def test_flight_plan_page_clr_deletes_the_selected_waypoint(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g._fpl_edit["row"] = 1
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))      # delete BRAVO
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "CHAR"]


def _to_fpl_page(g):
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan")


def _to_catalog_page(g):
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("Flight Plan Catalog")


def test_mnu_invert_flight_plan_reverses_and_reactivates(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.fpl.activate_leg(2)
    _to_fpl_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    assert g._fpl_menu is not None and g._fpl_menu.current == "INVERT FLT PLAN"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._fpl_menu is None
    assert [w.ident for w in g.fpl.waypoints] == ["DELT", "CHAR", "BRAVO", "ALFA"]
    assert g.fpl.active == 1


def test_mnu_delete_flight_plan_clears_it(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    _to_fpl_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    g._fpl_menu.sel = g._fpl_menu.options.index("DELETE FLT PLAN")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert len(g.fpl.waypoints) == 0


def test_mnu_cancels_without_acting(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    _to_fpl_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g._fpl_menu is None
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO"]


def test_mnu_copy_flight_plan_stores_it_in_the_first_empty_catalog_slot(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    _to_fpl_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    g._fpl_menu.sel = g._fpl_menu.options.index("COPY FLT PLAN")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g.fpl_catalog[0] is not None
    assert [w.ident for w in g.fpl_catalog[0].waypoints] == ["ALFA", "BRAVO", "CHAR"]
    # the active plan is untouched
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO", "CHAR"]


def test_catalog_page_recalls_a_stored_plan_as_the_active_plan(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    assert g.catalog_store(0)
    g.load_flight_plan(["ALFA", "DELT"])              # a different active plan now
    _to_catalog_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on -> row select
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))      # recall slot 0
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO", "CHAR"]
    assert g.fpl.active == 1
    assert g.cursor.page_name == "Flight Plan"                   # jumps to review it


def test_catalog_page_ent_on_an_empty_slot_is_a_no_op(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    _to_catalog_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert [w.ident for w in g.fpl.waypoints] == ["ALFA", "BRAVO"]  # unchanged
    assert g.cursor.page_name == "Flight Plan Catalog"              # did not jump


def test_catalog_page_clr_deletes_the_selected_slot(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(0)
    _to_catalog_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g.fpl_catalog[0] is None


def test_catalog_store_first_empty_skips_filled_slots(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(0)
    g.load_flight_plan(["CHAR", "DELT"])
    assert g.catalog_store_first_empty()
    assert [w.ident for w in g.fpl_catalog[1].waypoints] == ["CHAR", "DELT"]


def test_catalog_store_auto_generates_an_origin_dest_comment(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    assert g.catalog_store(0)
    assert g.fpl_catalog[0].comment == "ALFA/CHAR"


def test_catalog_store_accepts_an_explicit_comment(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    assert g.catalog_store(0, comment="MORNING RUN")
    assert g.fpl_catalog[0].comment == "MORNING RUN"


def test_catalog_set_comment_renames_a_stored_plan(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(0)
    assert g.catalog_set_comment(0, "practice ils")
    assert g.fpl_catalog[0].comment == "PRACTICE ILS"
    assert not g.catalog_set_comment(5, "X")            # empty slot -> no-op


def test_catalog_load_carries_the_comment_to_the_active_plan(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(0, comment="LEG 1")
    g.load_flight_plan(["CHAR", "DELT"])
    assert g.catalog_load(0)
    assert g.fpl.comment == "LEG 1"


def test_catalog_sort_alphabetizes_by_comment_and_packs_empties_last(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(2, comment="ZULU")
    g.catalog_store(0, comment="ALPHA")
    g.catalog_store(5, comment="MIKE")
    g.catalog_sort()
    comments = [p.comment if p else None for p in g.fpl_catalog]
    assert comments[:3] == ["ALPHA", "MIKE", "ZULU"]
    assert all(c is None for c in comments[3:])


def test_mnu_sort_catalog_from_the_catalog_page(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.catalog_store(1, comment="BRAVO PLAN")
    g.catalog_store(0, comment="ALPHA PLAN")
    _to_catalog_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    g._fpl_menu.sel = g._fpl_menu.options.index("SORT CATALOG")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g.fpl_catalog[0].comment == "ALPHA PLAN"
    assert g.fpl_catalog[1].comment == "BRAVO PLAN"


def test_crossfill_copies_the_active_plan_to_another_gns_unit(db):
    g1 = Gns530(db)
    g2 = Gns530(db)
    g1.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    assert g1.crossfill(g2)
    assert [w.ident for w in g2.fpl.waypoints] == ["ALFA", "BRAVO", "CHAR"]
    assert g2.fpl.active == 1
    assert g2.fpl.comment == g1.fpl.comment


def test_crossfill_is_a_no_op_with_no_active_plan(db):
    g1 = Gns530(db)
    g2 = Gns530(db)
    g2.load_flight_plan(["ALFA", "BRAVO"])
    assert not g1.crossfill(g2)
    assert [w.ident for w in g2.fpl.waypoints] == ["ALFA", "BRAVO"]   # untouched


def _to_vnav_page(g):
    g.cursor.group = list(PAGE_GROUPS).index("NAV")
    g.cursor.page = PAGE_GROUPS["NAV"].index("VNAV")


def test_vnav_field_and_target_selection(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(ALFA, 0.0, 120.0)                    # active leg ALFA->BRAVO
    _to_vnav_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on
    assert g.vnav_field == 0
    g.handle_event(Event(mode=Mode.FMS1, inner=1))                # step target fwd
    assert g.vnav.target_ident == "BRAVO"                          # first fix ahead
    g.handle_event(Event(mode=Mode.FMS1, inner=1))
    assert g.vnav.target_ident == "CHAR"
    g.handle_event(Event(mode=Mode.FMS1, outer=1))                # -> altitude field
    assert g.vnav_field == 1
    g.handle_event(Event(mode=Mode.FMS1, inner=5))                # +500 ft
    assert g.vnav.target_alt_ft == 500.0
    g.handle_event(Event(mode=Mode.FMS1, outer=1))                # -> VS profile field
    assert g.vnav_field == 2
    g.handle_event(Event(mode=Mode.FMS1, inner=5))                # +500 fpm (default -400)
    assert g.vnav.vs_fpm == pytest.approx(100.0)


def test_vnav_ent_arms_only_once_a_target_is_picked_and_clr_clears(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(ALFA, 0.0, 120.0)
    _to_vnav_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # no target yet
    assert not g.vnav.armed
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, inner=1))                 # target = BRAVO
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g.vnav.armed
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))        # ENT toggles off too
    assert not g.vnav.armed
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g.vnav.armed
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g.vnav.target_ident == "" and not g.vnav.armed


def test_vnav_status_computes_distance_tod_and_deviation(g):
    """FINDINGS F23: the real GNS 530's VNAV input is a vertical SPEED
    ("VS Profile"/"Vertical Speed Desired", Pilot's Guide sec.10/11 -
    default 400 fpm descent), not a flight-path angle - there is no angle
    field on the real unit. TOD/required-altitude/deviation are derived from
    that rate at the current groundspeed (the manual requires > 35 kt GS for
    exactly this reason)."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(ALFA, 0.0, 120.0)                     # active leg ALFA->BRAVO
    assert g.vnav_set("CHAR", 2000.0, -500.0)       # descend at 500 fpm
    st = g.vnav_status(alt_ft=5000.0, gs_kt=120.0)
    assert st.valid
    expected_dist = 60.0                           # ALFA->CHAR is 1 deg lat, ~60 nm
    assert st.distance_to_target_nm == pytest.approx(expected_dist, rel=0.02)
    ft_per_nm = 500.0 * 60.0 / 120.0                # 250 ft/nm at 500 fpm, 120 kt GS
    assert st.required_alt_ft == pytest.approx(2000.0 + expected_dist * ft_per_nm, rel=0.02)
    assert st.tod_distance_nm == pytest.approx((5000.0 - 2000.0) / ft_per_nm, rel=0.02)
    assert st.deviation_ft == pytest.approx(5000.0 - st.required_alt_ft, rel=0.02)


def test_vnav_status_reports_required_vs_and_time_to_tod(g):
    """VSR (Vertical Speed Required) is the live rate needed right now to
    make the target at the target altitude - independent of the pilot's
    chosen VS profile, same as the real unit's VSR readout."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(ALFA, 0.0, 120.0)
    assert g.vnav_set("CHAR", 2000.0, -500.0)
    st = g.vnav_status(alt_ft=5000.0, gs_kt=120.0)
    to_lose, dist, gs = 5000.0 - 2000.0, 60.0, 120.0
    time_min = dist / gs * 60.0
    assert st.required_vs_fpm == pytest.approx(-to_lose / time_min, rel=0.02)
    assert st.required_vs_fpm < 0                       # descending
    assert st.time_to_tod_min == pytest.approx(st.distance_to_tod_nm / 120.0 * 60.0, rel=0.02)


def test_vnav_status_invalid_below_35kt_groundspeed(g):
    """Pilot's Guide: VNAV requires > 35 kt groundspeed."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(ALFA, 0.0, 120.0)
    assert g.vnav_set("CHAR", 2000.0, -500.0)
    st = g.vnav_status(alt_ft=5000.0, gs_kt=20.0)
    assert not st.valid
    assert st.distance_to_target_nm is not None         # DIS still shown, just no profile math


def test_vnav_status_invalid_when_target_already_passed(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(ALFA, 0.0, 120.0)
    assert g.vnav_set("BRAVO", 1000.0)
    g.fpl.activate_leg(2)                          # now flying BRAVO -> CHAR
    g.update(BRAVO, 0.0, 120.0)
    st = g.vnav_status(alt_ft=3000.0, gs_kt=120.0)
    assert not st.valid


def test_vnav_set_rejects_a_target_not_in_the_plan(g):
    g.load_flight_plan(["ALFA", "BRAVO"])
    assert not g.vnav_set("NOPE", 1000.0)
    assert g.vnav.target_ident == ""


def test_nearest_page_ent_goes_direct_to(g):
    g.update(Point(40.24, -74.0), 0.0, 120.0)                   # ~near the OOO VOR (40.25)
    g.cursor.group = list(PAGE_GROUPS).index("NRST")
    g.cursor.page = PAGE_GROUPS["NRST"].index("Nearest VOR")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))    # cursor on
    hits = g.nearest_for_page("Nearest VOR")
    assert hits and hits[0].ident == "OOO"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))     # DTO the nearest
    assert g.dto is not None and g.dto.target.ident == "OOO"


def test_page_group_knob_does_not_wrap(g):
    assert g.cursor.group == 0
    g.handle_event(Event(mode=Mode.FMS1, outer=-1))         # already at the first
    assert g.cursor.group == 0                              # clamped, no wrap
    g.handle_event(Event(mode=Mode.FMS1, outer=99))         # past the last
    assert g.cursor.group == len(list(PAGE_GROUPS)) - 1     # clamped at the end


def test_page_knob_does_not_wrap(g):
    g.handle_event(Event(mode=Mode.FMS1, inner=-1))
    assert g.cursor.page == 0
    g.handle_event(Event(mode=Mode.FMS1, inner=99))
    assert g.cursor.page == len(PAGE_GROUPS[g.cursor.group_name]) - 1


def test_nav_data_expiry_annunciation_and_message():
    d = NavDatabase(source="test")
    d.expires = date(2026, 1, 1)
    d.add_waypoint(Waypoint("ALFA", ALFA))
    d.add_waypoint(Waypoint("BRAVO", BRAVO))
    g = Gns530(d, today=date(2026, 3, 1))
    g.load_flight_plan(["ALFA", "BRAVO"])
    ns = g.update(Point(40.1, -74.0), 0.0, 120.0)
    assert "NAV DATA EXPIRED" in ns.annunciators
    msgs = g.ack_messages()
    assert msgs and "EXPIRED" in msgs[0]
    # warned only once
    g.update(Point(40.2, -74.0), 0.0, 120.0)
    assert g.ack_messages() == []


# --------------------------------------------------------------------------- #
# CDI full-scale (phase of flight) -- FINDINGS F1 / F2
# --------------------------------------------------------------------------- #
def test_cdi_scale_is_five_nm_enroute(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    # >30 nm from both ALFA (departure) and DELT (destination) - sec.10.4 arms
    # terminal scale within 30 nm of *either* end, so a true enroute test must
    # clear both arms.
    ns = g.update(Point(40.75, -74.0), 0.0, 120.0)
    assert ns.cdi_scale_nm == pytest.approx(5.0)


def test_cdi_scale_tightens_to_terminal_near_destination(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.fpl.activate_leg(3)
    ns = g.update(Point(41.0, -73.5), 90.0, 120.0)          # ~23 nm from DELT
    assert ns.cdi_scale_nm == pytest.approx(1.0)


def test_cdi_scale_is_terminal_near_departure_airport(g):
    """Sec.10.4: '...when leaving the departure airport the CDI scale is set
    to 1.0 nm and gradually ramps UP to 5 nm beyond 30 nm (from the departure
    airport)' - symmetric with the arrival-side ramp, and independent of
    distance remaining to the destination."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    ns = g.update(Point(40.05, -74.0), 0.0, 120.0)          # ~3 nm from ALFA
    assert ns.cdi_scale_nm == pytest.approx(1.0)


def test_cdi_scale_tightens_to_approach_when_procedure_active(db):
    db.add_procedure(Procedure(
        airport="KTST", ident="RNV-Z", kind="approach", route_type="R",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="BRAVO", is_faf=True),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "RNV-Z")
    ns = g.update(Point(40.48, -74.0), 0.0, 120.0)          # ~1.3 nm short of BRAVO (the FAF)
    assert ns.cdi_scale_nm == pytest.approx(0.30)


def test_cdi_scale_stays_approach_past_the_faf(db):
    """FINDINGS: the 0.30 nm approach scale, once armed within 2 nm of the
    FAF, must not widen again on the FAF -> MAP leg just because that leg's
    own endpoint is farther than the arm distance (a real MAP fix is often
    several miles past the FAF)."""
    db.add_procedure(Procedure(
        airport="KTST", ident="RNV-Z", kind="approach", route_type="R",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(30, LegType.TF, fix_ident="CHAR", is_map=True),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "RNV-Z")
    g.update(Point(40.48, -74.0), 0.0, 120.0)               # short of BRAVO (the FAF): armed
    g.fpl.activate_leg(g.fpl.index_of("CHAR"))               # now past the FAF, on the MAP leg
    ns = g.update(Point(40.6, -74.0), 0.0, 120.0)            # miles from CHAR - would un-arm before the fix
    assert ns.cdi_scale_nm == pytest.approx(0.30)


def test_cdi_scale_does_not_arm_early_from_an_unrelated_leg_passing_near_the_faf(db):
    """FINDINGS F18: an *earlier* leg's own flight path can pass within 2 nm
    of the FAF's coordinates by pure incidental geometry (KLNS I08:
    LRP->DALAC happens to track within ~0.3 nm of POLCU well before DALAC's
    hold is even reached) - that must not arm the 0.30 nm approach scale.
    Here ALFA->CHAR runs straight through BRAVO (the FAF of a later leg)."""
    db.add_procedure(Procedure(
        airport="KTST", ident="RNV-Z", kind="approach", route_type="R",
        transitions={"": (
            ProcedureLeg(10, LegType.IF, fix_ident="ALFA"),
            ProcedureLeg(20, LegType.TF, fix_ident="CHAR"),
            ProcedureLeg(30, LegType.TF, fix_ident="BRAVO", is_faf=True),
            ProcedureLeg(40, LegType.TF, fix_ident="DELT", is_map=True),
        )},
    ))
    g = Gns530(db)
    g.load_procedure("KTST", "RNV-Z")
    # flying leg 1 (ALFA -> CHAR): passes exactly through BRAVO's position,
    # but BRAVO (the FAF) isn't the active leg yet - must not tighten
    ns = g.update(BRAVO, 0.0, 120.0)
    assert ns.cdi_scale_nm != pytest.approx(0.30)
    # now actually on the FAF-bound leg, close to BRAVO: correctly arms
    g.fpl.activate_leg(g.fpl.index_of("BRAVO"))
    ns = g.update(Point(40.49, -74.0), 0.0, 120.0)
    assert ns.cdi_scale_nm == pytest.approx(0.30)


def test_cdi_alarms_fixed_scale_caps_the_auto_value(g):
    """AUX > Setup > CDI/Alarms: a fixed selection is a ceiling the scale
    never widens past, but a genuinely tighter auto phase still tightens
    further than it (H1: "does not show CDI Alarms... nothing configurable")."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.cdi_alarm_max_nm = 1.0
    ns = g.update(Point(40.2, -74.0), 0.0, 120.0)         # far enroute - would be 5.0 nm Auto
    assert ns.cdi_scale_nm == pytest.approx(1.0)


def test_cdi_alarms_cycles_through_auto_and_fixed_choices(g):
    assert g.cdi_alarm_max_nm is None                     # Auto by default
    g.cursor.group = list(PAGE_GROUPS).index("AUX")
    g.cursor.page = PAGE_GROUPS["AUX"].index("Setup")
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on
    g.handle_event(Event(mode=Mode.FMS1, inner=1))
    assert g.cdi_alarm_max_nm == pytest.approx(5.0)
    g.handle_event(Event(mode=Mode.FMS1, inner=1))
    assert g.cdi_alarm_max_nm == pytest.approx(1.0)
    g.handle_event(Event(mode=Mode.FMS1, inner=1))
    assert g.cdi_alarm_max_nm == pytest.approx(0.30)
    g.handle_event(Event(mode=Mode.FMS1, inner=1))         # clamped at the last choice
    assert g.cdi_alarm_max_nm == pytest.approx(0.30)
    g.handle_event(Event(mode=Mode.FMS1, inner=-1))
    assert g.cdi_alarm_max_nm == pytest.approx(1.0)


def test_cdi_scale_ramps_gradually_when_dt_given(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(Point(40.75, -74.0), 0.0, 120.0)               # settle at 5.0 enroute
    assert g._cdi_scale == pytest.approx(5.0)
    g.fpl.activate_leg(3)
    ns = g.update(Point(41.0, -73.5), 90.0, 120.0, dt=1.0)  # terminal target now
    assert 4.5 < ns.cdi_scale_nm < 5.0                       # slewing, not snapped


# --------------------------------------------------------------------------- #
# turn advisory stages -- FINDINGS F5
# --------------------------------------------------------------------------- #
def test_turn_now_flag_at_the_turn_point(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.fpl.activate_leg(2)                                   # BRAVO -> CHAR, turn at CHAR
    far = g.update(Point(40.7, -74.0), 0.0, 120.0)          # well before CHAR
    assert far.wpt_alert is False and not far.turn_now
    near = g.update(Point(40.9915, -74.0), 0.0, 120.0)      # inside turn anticipation
    assert near.turn_now and near.next_dtk is not None
    assert g.fpl.to_wp.ident == "CHAR"                      # not sequenced yet


# --------------------------------------------------------------------------- #
# OBS reachability -- FINDINGS F6
# --------------------------------------------------------------------------- #
def test_toggle_obs_seeds_course_from_current_dtk(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.1, -74.0), 0.0, 120.0)                # DTK ~ 360
    g.toggle_obs()
    assert g.obs_active and _near(g.obs_course, 360.0, tol=2.0)
    g.toggle_obs()
    assert not g.obs_active


def test_nudge_obs_steps_course_only_while_active(g):
    g.nudge_obs(10)
    assert g.obs_course == 0.0                              # inactive -> no-op
    g.set_obs(90.0)
    g.nudge_obs(-5)
    assert _near(g.obs_course, 85.0)


# --------------------------------------------------------------------------- #
# Direct-To cancel resumes nearest leg -- FINDINGS F10
# --------------------------------------------------------------------------- #
def test_cancel_direct_to_resumes_on_the_closest_leg(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(ALFA, 0.0, 120.0)
    g.direct_to("DELT")                                     # skip ahead
    g.update(Point(40.95, -74.0), 0.0, 120.0)              # now near the CHAR->? area
    g.cancel_direct_to()
    # closest leg to (40.95,-74) is BRAVO(40.5)->CHAR(41.0) i.e. active index 2
    assert g.fpl.active == 2
    assert g.fpl.from_wp.ident == "BRAVO" and g.fpl.to_wp.ident == "CHAR"


# --------------------------------------------------------------------------- #
# CLR-when-idle -> Default NAV, and message peek -- FINDINGS F7 / F8
# --------------------------------------------------------------------------- #
def test_clr_when_idle_jumps_to_default_nav(g):
    g.handle_event(Event(mode=Mode.FMS1, outer=1))          # move off NAV group
    assert g.cursor.group_name != "NAV"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    assert g.cursor.group_name == "NAV" and g.cursor.page == 0


def test_peek_messages_does_not_clear_the_queue():
    d = NavDatabase(source="test")
    d.expires = date(2026, 1, 1)
    d.add_waypoint(Waypoint("ALFA", ALFA))
    d.add_waypoint(Waypoint("BRAVO", BRAVO))
    g = Gns530(d, today=date(2026, 3, 1))
    g.load_flight_plan(["ALFA", "BRAVO"])
    g.update(Point(40.1, -74.0), 0.0, 120.0)
    assert g.peek_messages() and g.peek_messages() == g.peek_messages()
    assert g.ack_messages()                                 # still there to ack
    assert g.peek_messages() == []


def test_nav_data_not_expired_is_quiet():
    d = NavDatabase(source="test")
    d.expires = date(2026, 12, 1)
    d.add_waypoint(Waypoint("ALFA", ALFA))
    d.add_waypoint(Waypoint("BRAVO", BRAVO))
    g = Gns530(d, today=date(2026, 3, 1))
    g.load_flight_plan(["ALFA", "BRAVO"])
    ns = g.update(Point(40.1, -74.0), 0.0, 120.0)
    assert "NAV DATA EXPIRED" not in ns.annunciators
    assert g.ack_messages() == []


# --------------------------------------------------------------------------- #
# Weather page (AUX group)
# --------------------------------------------------------------------------- #
@pytest.fixture
def wx_db():
    d = NavDatabase(source="test")
    d.add_airport(Airport("KBOS", Point(42.36, -71.01)))
    d.add_airport(Airport("KJFK", Point(40.64, -73.78)))
    d.add_waypoint(Waypoint("MID1", Point(41.5, -72.4)))   # enroute fix, not an airport
    return d


def _to_weather_page(g):
    g.cursor.group = list(PAGE_GROUPS).index("AUX")
    g.cursor.page = PAGE_GROUPS["AUX"].index("Weather")


def test_wx_station_idents_lists_flight_plan_airports_in_route_order(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    assert g.wx_station_idents() == ["KBOS", "KJFK"]


def test_wx_station_idents_dedups_a_repeated_airport(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "KJFK", "KBOS"])
    assert g.wx_station_idents() == ["KBOS", "KJFK"]


def test_wx_station_idents_falls_back_to_nearest_airport_with_no_plan(wx_db):
    g = Gns530(wx_db)
    g.update(Point(42.3, -71.0), 0.0, 0.0)          # sets _pos near KBOS
    assert g.wx_station_idents() == ["KBOS"]


def test_wx_station_idents_empty_with_no_plan_and_no_position(wx_db):
    g = Gns530(wx_db)
    assert g.wx_station_idents() == []


def test_wx_sel_scrolls_with_outer_knob_and_clamps(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    _to_weather_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on
    assert g.wx_sel == 0
    g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert g.wx_sel == 1
    g.handle_event(Event(mode=Mode.FMS1, outer=5))                # clamps at the end
    assert g.wx_sel == 1
    g.handle_event(Event(mode=Mode.FMS1, outer=-9))               # clamps at the start
    assert g.wx_sel == 0


def test_wx_sel_resets_when_cursor_re_enters_the_page(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    _to_weather_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert g.wx_sel == 1
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))      # cursor off
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))      # cursor on again
    assert g.wx_sel == 0


# --------------------------------------------------------------------------- #
# Charts page (AUX group)
# --------------------------------------------------------------------------- #
def _to_charts_page(g):
    g.cursor.group = list(PAGE_GROUPS).index("AUX")
    g.cursor.page = PAGE_GROUPS["AUX"].index("Charts")


def test_chart_airport_sel_scrolls_with_outer_knob_and_clamps(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    _to_charts_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))     # cursor on
    assert g.chart_airport_sel == 0
    g.handle_event(Event(mode=Mode.FMS1, outer=1))
    assert g.chart_airport_sel == 1
    g.handle_event(Event(mode=Mode.FMS1, outer=5))                # clamps at the end
    assert g.chart_airport_sel == 1
    g.handle_event(Event(mode=Mode.FMS1, outer=-9))               # clamps at the start
    assert g.chart_airport_sel == 0


def test_chart_sel_scrolls_with_inner_knob_unclamped(wx_db):
    """gpsnav doesn't know how many charts an airport has (that's datasrc.dtpp's
    job) so chart_sel is left unclamped here - render/main clamp it defensively."""
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS"])
    _to_charts_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, inner=3))
    assert g.chart_sel == 3
    g.handle_event(Event(mode=Mode.FMS1, inner=100))               # not clamped here
    assert g.chart_sel == 103


def test_chart_sel_resets_when_the_airport_selection_changes(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    _to_charts_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, inner=5))
    assert g.chart_sel == 5
    g.handle_event(Event(mode=Mode.FMS1, outer=1))                 # new airport
    assert g.chart_airport_sel == 1
    assert g.chart_sel == 0


def test_chart_sel_resets_when_cursor_re_enters_the_page(wx_db):
    g = Gns530(wx_db)
    g.load_flight_plan(["KBOS", "MID1", "KJFK"])
    _to_charts_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    g.handle_event(Event(mode=Mode.FMS1, outer=1, inner=2))
    assert g.chart_airport_sel == 1 and g.chart_sel == 2
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))      # cursor off
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))      # cursor on again
    assert g.chart_airport_sel == 0 and g.chart_sel == 0


def test_ent_on_the_charts_page_does_not_raise_gpsnav_has_no_io():
    """_page_ent has no branch for Charts on purpose - the real fetch+open
    action is intercepted in main.route_event, not gpsnav.py."""
    d = NavDatabase(source="test")
    g = Gns530(d)
    _to_charts_page(g)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))       # must not raise


def _left_hold_state(g, turn, entry_arrival):
    import dataclasses
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    char = dataclasses.replace(char, hold_turn=turn)      # PlanWaypoint is frozen
    g._start_hold(char, arrival_true=entry_arrival, gs_kt=130.0)
    return g._hold_state


def test_hold_inbound_turn_follows_the_hold_direction_not_the_shortest_way(db):
    """F43 (KLNS ILS 08 missed, KUPPS left-turn hold): the turn from the
    outbound/teardrop leg back onto the inbound course took the shortest way,
    not the hold's direction - a ~150 deg RIGHT turn in a LEFT hold, leaving
    the aircraft on the far side of the fix flying away from it. The INBOUND
    phase must steer in the hold's own turn direction."""
    g = _hold_procedure(db)
    for turn, want in (("L", -1.0), ("R", 1.0)):
        hs = _left_hold_state(g, turn, 0.0)
        assert hs["sign"] == want
        hs["phase"] = "OUTBOUND"
        char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
        # at the end of the outbound leg, still pointed outbound
        end = destination(char.pos, hs["leg_hdg"], hs["leg_nm"])
        ns = g.update(end, hs["leg_hdg"], 130.0, 1.0)
        assert hs["phase"] == "INBOUND"
        ns = g.update(end, hs["leg_hdg"], 130.0, 1.0)
        # commanded course is ahead of the track on the hold's turn side
        assert angle_diff(ns.dtk, hs["leg_hdg"]) * want > 0


def test_parallel_entry_first_inbound_turn_is_opposite_the_hold_direction(db):
    """AIM 5-3-8: after a parallel entry's outbound leg the first turn is
    toward the non-holding side (opposite the hold's own direction)."""
    g = _hold_procedure(db)
    # right-hand hold, inbound == 0 deg true (see _hold_procedure); arriving
    # heading 250 is in the parallel sector
    hs = _left_hold_state(g, "R", 250.0)
    if hs["entry"] != "parallel":
        pytest.skip("fixture geometry didn't produce a parallel entry")
    hs["phase"] = "OUTBOUND"
    char = next(w for w in g.fpl.waypoints if w.ident == "CHAR")
    end = destination(char.pos, hs["leg_hdg"], hs["leg_nm"])
    g.update(end, hs["leg_hdg"], 130.0, 1.0)
    assert hs["turn_dir"] == -hs["sign"]


# --------------------------------------------------------------------------- #
# DME arcs (ARINC AF legs) are flown as a circle about the navaid             #
# --------------------------------------------------------------------------- #
def _arc_plan(radius_nm=16.0, sweep_deg=178.0, turn="R"):
    """A plan whose second leg is a DME arc about a navaid, then a fix beyond it."""
    from navmath import norm360
    centre = Point(41.0, -74.0)
    sgn = 1.0 if turn == "R" else -1.0
    start = destination(centre, 0.0, radius_nm)
    end = destination(centre, norm360(sgn * sweep_deg), radius_nm)
    out = destination(end, norm360(sgn * sweep_deg + sgn * 90.0), 5.0)
    return centre, [PlanWaypoint("STRT", start),
                    PlanWaypoint("ARCE", end, arc_centre=centre, arc_turn=turn),
                    PlanWaypoint("OUTF", out)]


def _fly_arc(g, pos, gs=110.0, dt=1.0, steps=6000):
    trk, seen = 0.0, []
    for _ in range(steps):
        ns = g.update(pos, trk, gs, dt)
        seen.append((ns, pos))
        if g.fpl.active >= 2 or ns.dtk is None:
            break
        hdg = ns.dtk - max(-30.0, min(30.0, (ns.xtk_nm or 0.0) * 15.0))
        trk = hdg % 360.0
        pos = destination(pos, trk, gs * dt / 3600.0)
    return seen


@pytest.mark.parametrize("turn", ["R", "L"])
def test_a_long_dme_arc_is_flown_on_the_circle_not_the_chord(db, turn):
    centre, wps = _arc_plan(turn=turn)
    g = Gns530(db)
    g.fpl.waypoints = list(wps)
    g.fpl.activate_leg(1)
    seen = _fly_arc(g, wps[0].pos)
    settled = [pos for ns, pos in seen[len(seen) // 10:-len(seen) // 10]]
    err = [abs(great_circle_nm(centre, p) - 16.0) for p in settled]
    assert max(err) < 0.15               # a 178-degree chord approximation was ~0.8 nm off
    assert g.fpl.active == 2             # ...and it sequenced off the end of the arc


def test_arc_leg_reports_the_tangent_dtk_and_arc_dtg(db):
    centre, wps = _arc_plan(sweep_deg=90.0)
    g = Gns530(db)
    g.fpl.waypoints = list(wps)
    g.fpl.activate_leg(1)
    ns = g.update(wps[0].pos, 90.0, 110.0, 1.0)
    assert ns.dtk == pytest.approx(90.0, abs=0.5)                      # tangent at the start
    assert ns.xtk_nm == pytest.approx(0.0, abs=0.01)
    assert ns.dtg_nm == pytest.approx(math.radians(90.0) * 16.0, abs=0.1)   # arc length, not chord
    mid = destination(centre, 45.0, 15.0)                              # inside the circle
    ns = g.update(mid, 135.0, 110.0, 1.0)
    assert ns.xtk_nm == pytest.approx(1.0, abs=0.05)                   # right of a clockwise arc
    assert ns.dtk == pytest.approx(135.0, abs=0.5)


# --------------------------------------------------------------------------- #
# Flight Plan page: DCT on a highlighted waypoint (Pilot's Guide p.47 / p.60)  #
# --------------------------------------------------------------------------- #
def _fpl_cursor_on(g, row):
    """Flight Plan page, small-knob cursor on, big knob moved to ``row``."""
    g.cursor.go_to_flight_plan()
    g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    assert g._fpl_edit is not None
    g._fpl_edit["row"] = row


def test_dct_on_a_highlighted_fpl_waypoint_prefills_that_waypoint(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    _fpl_cursor_on(g, 2)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog is not None and g._dto_dialog.ident() == "CHAR"   # not the active TO


def test_dct_twice_on_a_fpl_waypoint_activates_that_leg_after_ent(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    assert g.fpl.active == 1
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    _fpl_cursor_on(g, 3)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog is None and g._leg_confirm == {"row": 3}      # "Activate Leg?"
    assert g.fpl.active == 1                                          # nothing happens yet
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g._leg_confirm is None and g.dto is None
    assert g.fpl.active == 3                                          # CHAR -> DELT leg
    ns = g.update(Point(40.0, -74.0), 0.0, 120.0)
    assert ns.mode == "LEG" and ns.from_ident == "CHAR" and ns.to_ident == "DELT"


def test_activate_leg_can_be_cancelled_and_clears_a_direct_to(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.direct_to("BRAVO")
    _fpl_cursor_on(g, 3)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))           # cancel
    assert g._leg_confirm is None and g.fpl.active == 1 and g.dto is not None
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
    assert g.dto is None and g.fpl.active == 3                        # the leg replaces the D->


def test_dct_on_the_first_waypoint_row_is_a_plain_direct_to(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    _fpl_cursor_on(g, 0)                                              # the departure: no leg ends there
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))
    assert g._dto_dialog is not None and g._dto_dialog.leg_row is None
    g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))           # 2nd press just cancels
    assert g._dto_dialog is None and g._leg_confirm is None


def test_mnu_activate_leg_option_on_a_highlighted_fpl_waypoint(g):
    """Pilot's Guide sec.4 (manual p.55): highlight the destination waypoint, press
    MENU, select "Activate Leg?", ENT, then ENT again on the confirmation window."""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR", "DELT"])
    g.update(Point(40.0, -74.0), 0.0, 120.0)
    _fpl_cursor_on(g, 3)
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    assert g._fpl_menu is not None and g._fpl_menu.current == "ACTIVATE LEG"
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))           # picks the option
    assert g._leg_confirm == {"row": 3} and g.fpl.active == 1         # confirmation window
    g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))           # "Activate?" -> ENT
    assert g.fpl.active == 3


def test_mnu_has_no_activate_leg_without_a_highlighted_waypoint(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    g.cursor.go_to_flight_plan()
    g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    assert g._fpl_menu is not None and "ACTIVATE LEG" not in g._fpl_menu.options


# --------------------------------------------------------------------------- #
# "Set course to ###" (Pilot's Guide p.175)
# --------------------------------------------------------------------------- #
def test_set_course_message_only_when_the_selected_course_is_over_10_deg_off(g):
    """p.175: 'Set course to [###] - The course select for the external CDI (or HSI) should be
    set to the specified course. The message only occurs when the current selected course is
    greater than 10 degrees different from the desired track.'"""
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    ns = g.update(Point(40.0, -74.0), 0.0, 120.0)
    dtk = ns.dtk
    g.check_course_select(dtk, 0.0)                            # on the DTK
    assert not any(m.startswith("Set course to") for m in g.peek_messages())
    g.check_course_select((dtk + 10.0) % 360.0, 0.0)           # exactly 10 deg: not "greater than"
    assert not any(m.startswith("Set course to") for m in g.peek_messages())
    g.check_course_select((dtk + 11.0) % 360.0, 0.0)
    msgs = [m for m in g.peek_messages() if m.startswith("Set course to")]
    assert msgs == [f"Set course to {dtk:03.0f}°"]
    g.check_course_select((dtk + 12.0) % 360.0, 0.0)           # still off: no duplicate
    assert len([m for m in g.peek_messages() if m.startswith("Set course to")]) == 1
    g.check_course_select(dtk, 0.0)                            # pilot dials it in: withdrawn
    assert not any(m.startswith("Set course to") for m in g.peek_messages())


def test_set_course_message_uses_the_magnetic_desired_track_and_is_off_when_slaved(g):
    g.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    ns = g.update(Point(40.0, -74.0), 0.0, 120.0)
    g.check_course_select(0.0 if ns.dtk > 60 else 180.0, 10.0)      # magvar 10 E: DTK mag = true - 10
    msg = [m for m in g.peek_messages() if m.startswith("Set course to")][0]
    assert msg == f"Set course to {(ns.dtk - 10.0) % 360.0:03.0f}°"
    g.check_course_select(None, 10.0)                          # pointer slaved / unread
    assert not any(m.startswith("Set course to") for m in g.peek_messages())
