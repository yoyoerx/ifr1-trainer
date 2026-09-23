"""TrainerSession — one flight, one GpsNav + one SimModel, ticked together.

Kotlin creates one `TrainerSession` per training session (via Chaquopy) and
calls `tick(dt)` once per frame, reading back a **plain dict** — floats,
strings, bools, lists — deliberately, since that's what's cheap to marshal
across the Chaquopy JNI boundary. Dataclass instances (`NavState`, `Ownship`,
`Point`) stay on the Python side; nothing here hands one across the bridge.

This is the Phase 0 proof that the existing avionics brain (`gpsnav.GpsNav`,
`sim_model.SimModel`, `navdata.NavDatabase`) imports and runs together
unmodified — see `docs/ANDROID_PORT_PLAN.md` §3.4/§9.1. It does not yet do
everything `main.py`'s loop does (input dispatch, message-queue draining
policy, the draw-command-list render contract from §3.6) — those are
follow-up work, not silently dropped; see the package docstring
(`androidbridge/__init__.py`) for what's intentionally out of scope here.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from gpsnav import GpsNav
from navdata.model import NavDatabase
from navmath import Point
from sim_model import SimModel


class TrainerSession:
    """Owns one `GpsNav` + one `SimModel` and steps them together each tick."""

    def __init__(
        self,
        db: NavDatabase,
        *,
        start_lat: float,
        start_lon: float,
        heading_deg: float,
        altitude_ft: float = 3000.0,
        tas_kt: float = 120.0,
        wind_from_deg: float = 0.0,
        wind_kt: float = 0.0,
    ) -> None:
        self.db = db
        self.gpsnav = GpsNav(db)
        self.sim = SimModel(
            pos=Point(start_lat, start_lon),
            heading_deg=heading_deg,
            altitude_ft=altitude_ft,
            tas_kt=tas_kt,
            wind_from_deg=wind_from_deg,
            wind_kt=wind_kt,
        )

    # -- flight plan / commands (thin pass-throughs, not a full bezel
    #    dispatch table — see the module docstring) -------------------
    def load_flight_plan(self, idents: list[str]) -> list[str]:
        """Returns the list of idents that failed to resolve, same as
        `GpsNav.load_flight_plan` — empty means every waypoint was found."""
        return self.gpsnav.load_flight_plan(idents)

    def command(
        self,
        *,
        heading_deg: float | None = None,
        altitude_ft: float | None = None,
        tas_kt: float | None = None,
        ias_kt: float | None = None,
    ) -> None:
        self.sim.command(heading=heading_deg, altitude=altitude_ft, tas=tas_kt, ias=ias_kt)

    # -- the tick ------------------------------------------------------
    def tick(self, dt_s: float) -> dict[str, Any]:
        ownship = self.sim.step(dt_s)
        nav = self.gpsnav.update(ownship.pos, ownship.track_deg, ownship.gs_kt, dt_s)

        # drain gpsnav's message queue rather than let it grow unbounded -
        # this session is the only consumer, so it owns "read = cleared"
        # (main.py's desktop loop has its own, separate policy for this;
        # not shared with the Android side)
        messages = list(self.gpsnav.messages)
        self.gpsnav.messages.clear()

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
        }
        nav_dict = asdict(nav)
        nav_dict["annunciators"] = list(nav_dict["annunciators"])
        snapshot["nav"] = nav_dict
        return snapshot
