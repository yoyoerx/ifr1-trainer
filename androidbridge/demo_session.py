"""Phase 1 core-loop entry points BrainBridge.kt calls (see
docs/ANDROID_PORT_PLAN.md §6). `new_session()` builds a `TrainerSession` on
a small synthetic nav database - the same one `selftest.py` already uses -
a placeholder flight for testing without real FAA data cached yet.
`new_real_session()` (§3.5, `androidbridge/nav_update.py` fetches the data
this loads) is the real-nav-data equivalent, once available. Every other
function here (`render_*`/`dispatch`/`tick_line`) operates on whichever
`TrainerSession` Kotlin created - real or synthetic doesn't matter past
construction, `World`/`GpsNav`/`render_commands` are nav-data-agnostic.

Returns a single formatted string per tick rather than a dict, deliberately
- Chaquopy's string marshaling back to Kotlin is already proven (`selfTest()`
via `androidbridge.selftest.run()`), so this pass's live-state verification
screen (a plain text panel, not real instrument graphics - see the plan)
reuses that exact pattern instead of introducing dict/PyObject marshaling
as a second, unproven code path.
"""

from __future__ import annotations

from navdata.model import NavDatabase, VhfNavaid, Waypoint
from navmath import Point

from . import TrainerSession


def new_session() -> TrainerSession:
    db = NavDatabase(source="android-phase1-demo")
    db.add_waypoint(Waypoint("ALFA", Point(40.0, -74.0)))
    db.add_waypoint(Waypoint("BRAVO", Point(40.5, -74.0)))
    db.add_waypoint(Waypoint("CHAR", Point(41.0, -74.0)))
    db.add_vhf(VhfNavaid("OOO", Point(40.25, -74.0), 113.0))
    session = TrainerSession(db, start_lat=39.9, start_lon=-74.0, heading_deg=0.0)
    session.load_flight_plan(["ALFA", "BRAVO", "CHAR"])
    return session


def new_real_session(root: str) -> TrainerSession:
    """The §3.5 real-nav-data equivalent of `new_session()` - `root` is the
    same writable directory `androidbridge.nav_update.fetch_kind` was
    called against (Android's `context.filesDir`-derived path, not
    `datasrc.faa.default_data_root()`'s repo-relative default, which
    resolves nowhere useful on Chaquopy's staged asset filesystem). No
    `start_lat`/`start_lon` override, unlike `new_session()`'s hand-picked
    demo position - `World`'s own `_initial_position` already derives a
    sane start (the loaded flight plan, or a first-airport fallback) from
    a real database, the same way desktop's `main.py` does with no
    special-casing."""
    import navdata

    db = navdata.load(data_dir=root)
    return TrainerSession(db)


def dispatch(
    session: TrainerSession,
    mode: str,
    pressed: str,
    released: str,
    outer: int,
    inner: int,
    mode_changed: bool,
    long_press: str,
) -> None:
    """pressed/released/long_press are comma-joined strings, not lists - see
    TrainerSession.dispatch_event's docstring for why."""
    session.dispatch_event(mode, pressed, released, outer, inner, mode_changed, long_press)


def render_hsi(session: TrainerSession, x: float, y: float, w: float, h: float) -> str:
    """Call after tick_line() each frame, not instead of it - see
    TrainerSession.render_hsi's docstring. The synthetic db's "OOO" VHF
    navaid (113.0 MHz) is there to exercise this: tune NAV1 to it to see
    the CDI/GS respond."""
    return session.render_hsi(x, y, w, h)


def render_ap_panel(session: TrainerSession, x: float, y: float, w: float, h: float) -> str:
    """Call after tick_line() each frame - see TrainerSession
    .render_ap_panel's docstring. AP-row buttons (any non-FMS mode) and the
    AP-mode shifted inner knob (IAS set-point) already dispatch through the
    same route_event path the HSI slice proved - see this panel react."""
    return session.render_ap_panel(x, y, w, h)


def render_gns(session: TrainerSession, x: float, y: float, w: float, h: float) -> str:
    """Call after tick_line() each frame - see TrainerSession.render_gns's
    docstring and render_commands.gns_commands's docstring for exactly
    which pages render. FMS-mode bezel input already moves
    gns.cursor.page_name/group_name correctly (proven by the core-loop
    milestone), the screen just doesn't have a body for AUX Weather/Charts
    yet (needs live datasrc.wx/PDF data) - it falls back to the default
    NAV page's content instead. The Map page has its own render_map()
    call, not this one - see render_commands.gns_commands's docstring."""
    return session.render_gns(x, y, w, h)


def adjust_map_range(session: TrainerSession, factor: float) -> None:
    """Call from the RNG touch key (§3.2) - see TrainerSession
    .adjust_map_range's docstring for why this isn't a dispatch_event call."""
    session.adjust_map_range(factor)


def adjust_vs(session: TrainerSession, detents: int) -> None:
    """Call from the touch VS knob (§3.2) - see TrainerSession.adjust_vs's
    docstring for why this isn't a dispatch_event call."""
    session.adjust_vs(detents)


def adjust_ias_target(session: TrainerSession, detents: int) -> None:
    """Call from the touch IAS knob (§3.2) - see TrainerSession
    .adjust_ias_target's docstring for why this isn't a dispatch_event call."""
    session.adjust_ias_target(detents)


def render_map(session: TrainerSession, x: float, y: float, w: float, h: float) -> str:
    """Call after tick_line() each frame - see TrainerSession.render_map's
    docstring. The demo flight plan (ALFA->BRAVO->CHAR) and the synthetic
    "OOO" VOR are there to exercise this: the flight-plan legs, waypoint
    dots, and the OOO VOR's hexagon symbol should all be visible."""
    return session.render_map(x, y, w, h)


def tick_line(session: TrainerSession, dt_s: float) -> str:
    s = session.tick(dt_s)
    return (
        f"mode={s['mode']:<4} shift={'Y' if s['shift_latched'] else 'n'}  "
        f"hdg={s['heading_deg']:05.1f} alt={s['altitude_ft']:.0f} "
        f"ias={s['ias_kt']:.0f} vs={s['vs_fpm']:+.0f}\n"
        f"COM1 {s['com1_active_mhz']:.3f}/{s['com1_standby_mhz']:.3f}  "
        f"COM2 {s['com2_active_mhz']:.3f}/{s['com2_standby_mhz']:.3f}\n"
        f"NAV1 {s['nav1_active_mhz']:.2f}/{s['nav1_standby_mhz']:.2f} "
        f"OBS={s['nav1_obs_deg']:05.1f}  "
        f"NAV2 {s['nav2_active_mhz']:.2f}/{s['nav2_standby_mhz']:.2f} "
        f"OBS={s['nav2_obs_deg']:05.1f}\n"
        f"XPDR {s['xpdr_squawk']:04d} {s['xpdr_mode']}  "
        f"AP eng={'Y' if s['ap_engaged'] else 'n'} "
        f"lat={s['ap_lateral']} vert={s['ap_vertical']} "
        f"hdgbug={s['ap_heading_bug']:.0f} altsel={s['ap_alt_preselect']:.0f}\n"
        f"CDI to={s['nav']['to_ident']!r} xtk={(s['nav'].get('xtk_nm') or 0.0):+.2f}nm  "
        f"baro={s['baro_inhg']:.2f}"
    )
