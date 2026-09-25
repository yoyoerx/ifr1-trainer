"""TrainerSession — owns a real `main.World` and steps/routes it exactly like
desktop's loop does.

Kotlin creates one `TrainerSession` per training session (via Chaquopy),
calls `tick(dt)` once per frame and `dispatch_event(...)` for every real
IFR-1 event, reading back **plain dicts** - floats, strings, bools, lists -
deliberately, since that's what's cheap to marshal across the Chaquopy JNI
boundary. Dataclass instances (`NavState`, `Ownship`, `Point`) and the
`World`/radio/autopilot objects themselves stay on the Python side; nothing
here hands one across the bridge.

Phase 1 (docs/ANDROID_PORT_PLAN.md §6): this wraps `main.World` and
`main.route_event` directly rather than reimplementing the mode-routing
table (COM/NAV tuning, shift-latch semantics, AP-row buttons, XPDR) that
`route_event` already owns - see `androidbridge/__init__.py` for why that's
safe to reuse here (module-level imports are stdlib-only; pygame/render/hid
are only ever imported lazily, inside functions `androidbridge` never
calls).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import config as config_mod
import render_commands
from ifr1 import Event as Ifr1Event
from ifr1 import Mode as Ifr1Mode
from main import Config, World, route_event
from navdata.model import NavDatabase
from navmath import Point, norm360


class TrainerSession:
    """Owns one `World` (gns/sim/radios/ap/baro/shift-latch state) and steps
    + routes IFR-1 events through it, mirroring desktop's loop exactly."""

    def __init__(
        self,
        db: NavDatabase,
        *,
        start_lat: float | None = None,
        start_lon: float | None = None,
        heading_deg: float | None = None,
        altitude_ft: float | None = None,
        tas_kt: float | None = None,
        wind_from_deg: float | None = None,
        wind_kt: float | None = None,
    ) -> None:
        cfg_dict = dict(config_mod.DEFAULTS)
        if altitude_ft is not None:
            cfg_dict["altitude"] = altitude_ft
        if tas_kt is not None:
            cfg_dict["tas"] = tas_kt
        if wind_from_deg is not None or wind_kt is not None:
            cfg_dict["wind"] = f"{wind_from_deg or 0.0:.0f}/{wind_kt or 0.0:.0f}"
        self.world = World(Config(cfg_dict), db=db)

        # World derives its own start position from the loaded flight plan
        # (or a KBOS/first-airport fallback) via `_initial_position` - only
        # override it when the caller explicitly asked for a specific start,
        # e.g. the selftest's small synthetic db with no airports in it.
        if start_lat is not None and start_lon is not None:
            self.world.sim.pos = Point(start_lat, start_lon)
        if heading_deg is not None:
            self.world.sim.heading = norm360(heading_deg)

        self._last_frame = None   # set by tick(); render_hsi() reads it

    # -- flight plan / commands (thin pass-throughs) --------------------
    def load_flight_plan(self, idents: list[str]) -> list[str]:
        """Returns the list of idents that failed to resolve, same as
        `GpsNav.load_flight_plan` - empty means every waypoint was found."""
        return self.world.gns.load_flight_plan(idents)

    def command(
        self,
        *,
        heading_deg: float | None = None,
        altitude_ft: float | None = None,
        tas_kt: float | None = None,
        ias_kt: float | None = None,
    ) -> None:
        self.world.sim.command(heading=heading_deg, altitude=altitude_ft, tas=tas_kt, ias=ias_kt)

    # -- real IFR-1 input, one event at a time ---------------------------
    def dispatch_event(
        self,
        mode: str,
        pressed: str,
        released: str,
        outer: int,
        inner: int,
        mode_changed: bool,
        long_press: str,
    ) -> None:
        """Builds an `ifr1.Event` from Kotlin's `Ifr1Event` fields (already
        confirmed field-for-field identical, see §3.1) and routes it through
        `main.route_event` - the exact function desktop's loop calls.

        `pressed`/`released`/`long_press` are comma-joined strings, not
        lists - a real crash on-device (2026-09-24) found that Chaquopy's
        Java List -> Python marshaling for a `callAttr` argument produces an
        object `tuple()`/`list()` can't consume ("TypeError: 'ArrayList'
        object is not iterable", same failure for `EmptyList` too - not a
        Kotlin-collection-type-specific issue). A comma-joined string
        sidesteps that class of bug entirely; button names never contain
        commas so this loses nothing.
        """
        ev = Ifr1Event(
            mode=Ifr1Mode[mode],
            mode_changed=mode_changed,
            pressed=tuple(pressed.split(",")) if pressed else (),
            released=tuple(released.split(",")) if released else (),
            outer=outer,
            inner=inner,
            long_press=tuple(long_press.split(",")) if long_press else (),
        )
        route_event(ev, self.world)

    # -- the tick ------------------------------------------------------
    def tick(self, dt_s: float) -> dict[str, Any]:
        frame = self.world.tick(dt_s)
        self._last_frame = frame
        ownship = frame.own

        # drain gpsnav's message queue rather than let it grow unbounded -
        # this session is the only consumer, so it owns "read = cleared"
        # (main.py's desktop loop has its own, separate policy for this;
        # not shared with the Android side)
        messages = list(self.world.gns.messages)
        self.world.gns.messages.clear()

        nav_dict = asdict(frame.nav)
        nav_dict["annunciators"] = list(nav_dict["annunciators"])

        ap = self.world.ap
        com1, com2 = self.world.radios.com1, self.world.radios.com2
        nav1, nav2 = self.world.radios.nav1, self.world.radios.nav2
        xpdr = self.world.radios.xpdr

        snapshot: dict[str, Any] = {
            "pos_lat": ownship.pos.lat,
            "pos_lon": ownship.pos.lon,
            "heading_deg": ownship.heading_deg,
            "track_deg": ownship.track_deg,
            "gs_kt": ownship.gs_kt,
            "tas_kt": ownship.tas_kt,
            "ias_kt": ownship.ias_kt,
            "altitude_ft": ownship.altitude_ft,
            "vs_fpm": ownship.vs_fpm,
            "messages": messages,
            "nav": nav_dict,
            "baro_inhg": self.world.baro_inhg,
            "shift_latched": self.world.shift_latched,
            "mode": self.world._last_mode.name if self.world._last_mode is not None else "",
            "com1_active_mhz": com1.active_mhz,
            "com1_standby_mhz": com1.standby_mhz,
            "com2_active_mhz": com2.active_mhz,
            "com2_standby_mhz": com2.standby_mhz,
            "nav1_active_mhz": nav1.active_mhz,
            "nav1_standby_mhz": nav1.standby_mhz,
            "nav1_obs_deg": nav1.obs_deg,
            "nav2_active_mhz": nav2.active_mhz,
            "nav2_standby_mhz": nav2.standby_mhz,
            "nav2_obs_deg": nav2.obs_deg,
            "xpdr_squawk": xpdr.code,
            "xpdr_mode": xpdr.mode,
            "ap_engaged": ap.engaged,
            "ap_lateral": ap.lateral.name if hasattr(ap.lateral, "name") else str(ap.lateral),
            "ap_vertical": ap.vertical.name if hasattr(ap.vertical, "name") else str(ap.vertical),
            "ap_heading_bug": ap.heading_bug,
            "ap_alt_preselect": ap.alt_preselect,
        }
        return snapshot

    # -- rendering (§3.6) --------------------------------------------------
    def render_hsi(self, x: float, y: float, w: float, h: float) -> str:
        """Draw-command-list string for the HSI head, using the same
        `Frame.nav1`/`Frame.panel` `tick()` already computed this frame -
        call after `tick()`, not instead of it. See render_commands.py's
        module docstring for the encoding and why it's a string."""
        frame = self._last_frame
        if frame is None:
            return ""
        return render_commands.hsi_commands(x, y, w, h, frame.nav1, frame.panel, self.world.t)

    def render_ap_panel(self, x: float, y: float, w: float, h: float) -> str:
        """Draw-command-list string for the S-TEC 55X AP programmer panel -
        `self.world.ap` directly, no `tick()`-computed data needed (unlike
        `render_hsi`), but still call after `tick()` for a consistent `t`."""
        return render_commands.ap_panel_commands(
            x, y, w, h, self.world.ap, self.world.t, ias_bug=self.world.ias_target,
        )
