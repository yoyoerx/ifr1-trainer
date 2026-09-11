"""wx_auto.py - background auto-refresh of live weather for the trainer.

`datasrc/wx.py` is a manual, offline CLI by design (same "no network in the
runtime loop" principle as the AIRAC nav-data cache in `datasrc/faa.py`) - a
pilot runs it, it fetches once, it caches. This module is the opt-in
exception: a slow background poller, started only with `--wx-auto-refresh`,
that periodically re-runs those same fetch-and-cache calls on a schedule
appropriate to each product and writes to the *exact same* on-disk cache -
`render._wx_read` and `_load_cached_winds_aloft` don't know or care whether
the file they're reading was written by a pilot's one-off CLI run or by this
poller.

It runs in its own daemon thread so a slow or hung HTTP request never stalls
the ~30 Hz render/physics loop. Station/region lookups are callables
(`stations`, `region`) rather than a fixed list, so the poller always fetches
for whatever the flight plan currently says, even if it changes mid-session.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from datasrc import wx as wxmod

__all__ = ["WxAutoUpdater", "METAR_REFRESH_S", "TAF_REFRESH_S", "WINDS_ALOFT_REFRESH_S"]

# How often each product is worth re-fetching, in seconds - not how often the
# source actually publishes a new one (a METAR can update anytime a SPECI is
# warranted; TAFs are scheduled ~4x/day plus amendments; the FD winds/temps
# aloft forecast is likewise issued ~4x/day for a 6/12/24 h window). These
# defaults trade "as live as practical" against not hammering a free public
# API from a training app running for hours.
METAR_REFRESH_S = 20 * 60        # 15-30 min asked for; 20 catches a SPECI reasonably promptly
TAF_REFRESH_S = 60 * 60          # hourly is generous against a ~4x/day + amendments cadence
WINDS_ALOFT_REFRESH_S = 60 * 60  # ditto - the FD table doesn't change on METAR timescales

_POLL_STEP_S = 5.0   # how often the thread wakes to check what's due (also its shutdown latency)


class WxAutoUpdater:
    """Background poller for METAR / TAF / winds-aloft.

    ``stations`` is called each cycle to get the current list of ICAO idents
    to fetch METAR/TAF for (typically ``GpsNav.wx_station_idents``).
    ``region`` (optional) returns the NWS FD region code for winds-aloft, or
    falsy to skip that product entirely - there's no lat/lon-to-region
    lookup table here, so this only runs when the pilot has told the trainer
    which region via ``--wx-region``. ``on_winds_aloft`` (optional) is called
    after each successful winds-aloft fetch, e.g. to re-apply the refreshed
    profile to the sim's wind model.

    All fetch/cache work reuses ``datasrc.wx.fetch_and_cache_*`` - this class
    only owns the scheduling and the thread.
    """

    def __init__(
        self,
        *,
        stations: Callable[[], list[str]],
        region: Callable[[], str] | None = None,
        on_winds_aloft: Callable[[], None] | None = None,
        root=None,
        metar_s: float = METAR_REFRESH_S,
        taf_s: float = TAF_REFRESH_S,
        winds_s: float = WINDS_ALOFT_REFRESH_S,
        poll_step_s: float = _POLL_STEP_S,
    ):
        self._stations = stations
        self._region = region
        self._on_winds_aloft = on_winds_aloft
        self._root = root or wxmod.default_data_root()
        self._metar_s = max(60.0, float(metar_s))     # floor: never hammer faster than 1/min
        self._taf_s = max(60.0, float(taf_s))
        self._winds_s = max(60.0, float(winds_s))
        self._poll_step_s = poll_step_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_metar_error = ""
        self.last_taf_error = ""
        self.last_winds_error = ""
        self.fetch_count = 0        # successful fetches, for tests / a status readout
        # due immediately on the first poll, so the Weather page has
        # something without waiting a full interval after start-up
        self._next_metar = 0.0
        self._next_taf = 0.0
        self._next_winds = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> "WxAutoUpdater":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                             name="wx-auto-updater")
            self._thread.start()
        return self

    def stop(self, *, join: bool = False, timeout: float = 2.0) -> None:
        self._stop.set()
        if join and self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- scheduling -----------------------------------------------------
    def poll_once(self, *, now: float | None = None) -> None:
        """Run one scheduling pass, fetching whatever product is due. Split
        out from :meth:`_run` (a plain loop calling this) so the schedule
        can be driven deterministically - by real callers on a timer, or by
        tests passing an explicit ``now``."""
        now = time.monotonic() if now is None else now
        idents = sorted(set(self._stations() or []))
        if idents and now >= self._next_metar:
            self._fetch_metar(idents)
            self._next_metar = now + self._metar_s
        if idents and now >= self._next_taf:
            self._fetch_taf(idents)
            self._next_taf = now + self._taf_s
        region = self._region() if self._region else None
        if region and now >= self._next_winds:
            self._fetch_winds(region)
            self._next_winds = now + self._winds_s

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._poll_step_s)

    def _fetch_metar(self, idents: list[str]) -> None:
        try:
            wxmod.fetch_and_cache_metar(self._root, idents)
            self.last_metar_error = ""
            self.fetch_count += 1
        except Exception as exc:                       # noqa: BLE001 - background, must not die
            self.last_metar_error = str(exc)

    def _fetch_taf(self, idents: list[str]) -> None:
        try:
            wxmod.fetch_and_cache_taf(self._root, idents)
            self.last_taf_error = ""
            self.fetch_count += 1
        except Exception as exc:                       # noqa: BLE001
            self.last_taf_error = str(exc)

    def _fetch_winds(self, region: str) -> None:
        try:
            wxmod.fetch_and_cache_winds_aloft(self._root, region)
            self.last_winds_error = ""
            self.fetch_count += 1
            if self._on_winds_aloft is not None:
                self._on_winds_aloft()
        except Exception as exc:                       # noqa: BLE001
            self.last_winds_error = str(exc)
