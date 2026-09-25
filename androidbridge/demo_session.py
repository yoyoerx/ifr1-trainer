"""Phase 1 core-loop entry points BrainBridge.kt calls (see
docs/ANDROID_PORT_PLAN.md §6). Builds a `TrainerSession` on a small
synthetic nav database - the same one `selftest.py` already uses - since
real FAA CIFP+NASR acquisition on-device is §3.5, not yet built. This is a
placeholder flight, not the eventual real-nav-data experience.

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
    docstring. Only the default NAV and Flight Plan pages render; FMS-mode
    bezel input already moves gns.cursor.page_name correctly (proven by the
    core-loop milestone), the screen just doesn't have a body for any other
    page yet - it falls back to the default NAV page's content instead."""
    return session.render_gns(x, y, w, h)


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
        f"CDI to={s['nav']['to_ident']!r} xtk={s['nav'].get('xtk_nm', 0.0):+.2f}nm  "
        f"baro={s['baro_inhg']:.2f}"
    )
