"""xplane_feed.py - optional live ownship position from X-Plane over UDP.

X-Plane's ``RREF`` protocol: you send it a datagram naming a dataref, a refresh
rate, and an integer index; it then streams ``(index, float)`` records back at
that rate. This module subscribes to the handful of position datarefs the
trainer needs and presents them as a :class:`FeedState` that is drop-in
compatible with ``sim_model.Ownship`` (same attribute names / units), so
``main.World`` can swap motion sources without the rest of the pipeline caring.

When no sim is answering, :attr:`XPlaneFeed.alive` goes ``False`` and the caller
falls back to ``sim_model``.

Protocol refs: the ``RREF`` request is a fixed 413-byte datagram to UDP 49000;
responses are ``b"RREF"`` + one version byte + repeating little-endian
``<int index><float value>`` 8-byte records. See the X-Plane "UDP" docs.

Units in / out: X-Plane gives metres, m/s and true degrees; the converters in
:data:`DATAREFS` turn those into the project's feet / knots / degrees. All
angles stay TRUE (see ARCHITECTURE sec.4).
"""

from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass

from navmath import Point, norm360

__all__ = [
    "DATAREFS",
    "FeedState",
    "XPlaneFeed",
    "build_rref_request",
    "parse_rref_response",
    "feed_state_from_values",
]

_XPLANE_UDP_PORT = 49000
_M_TO_FT = 3.280839895013123
_MS_TO_KT = 1.9438444924406046
_HDR = b"RREF"                     # response header is this + one version byte

# index -> (dataref path, raw -> project-unit converter)
DATAREFS: dict[int, tuple[str, "callable[[float], float]"]] = {
    0: ("sim/flightmodel/position/latitude", float),
    1: ("sim/flightmodel/position/longitude", float),
    2: ("sim/flightmodel/position/elevation", lambda m: m * _M_TO_FT),          # m MSL -> ft
    3: ("sim/flightmodel/position/groundspeed", lambda ms: ms * _MS_TO_KT),      # m/s -> kt
    4: ("sim/flightmodel/position/hpath", float),                               # true ground track, deg
    5: ("sim/flightmodel/position/psi", float),                                 # true heading, deg
    6: ("sim/flightmodel/position/magnetic_variation", float),                  # deg (see note in _state)
    7: ("sim/flightmodel/position/vh_ind_fpm", float),                          # vertical speed, fpm
    8: ("sim/flightmodel/position/true_airspeed", lambda ms: ms * _MS_TO_KT),    # m/s -> kt
    9: ("sim/flightmodel/position/phi", float),                                # roll, deg (+ right)
    10: ("sim/flightmodel/position/theta", float),                              # pitch, deg (+ up)
}
_CORE = (0, 1, 2)                  # lat / lon / elevation - required for a usable fix


@dataclass(frozen=True, slots=True)
class FeedState:
    """Ownship snapshot from the sim. Mirrors ``sim_model.Ownship``."""

    pos: Point
    heading_deg: float
    track_deg: float
    gs_kt: float
    tas_kt: float
    altitude_ft: float
    vs_fpm: float = 0.0
    bank_deg: float = 0.0
    pitch_deg: float = 0.0
    turn_rate_dps: float = 0.0
    slip_skid: float = 0.0


# --------------------------------------------------------------------------- #
# pure protocol
# --------------------------------------------------------------------------- #
def build_rref_request(freq_hz: int, index: int, dataref: str) -> bytes:
    """One 413-byte ``RREF`` subscription datagram. ``freq_hz`` 0 cancels."""
    path = dataref.encode("ascii")
    if len(path) >= 400:
        raise ValueError("dataref path too long")
    pkt = struct.pack("<5sii400s", b"RREF\x00", int(freq_hz), int(index), path)
    assert len(pkt) == 413
    return pkt


def parse_rref_response(data: bytes) -> dict[int, float]:
    """Decode a datagram from X-Plane into ``{index: raw_value}``.

    Tolerates the 4- or 5-byte header form and any trailing partial record.
    Returns ``{}`` for anything that is not an ``RREF`` response.
    """
    if len(data) < 5 or data[:4] != _HDR:
        return {}
    out: dict[int, float] = {}
    body = data[5:]
    for off in range(0, len(body) - 7, 8):
        idx, val = struct.unpack_from("<if", body, off)
        out[idx] = val
    return out


def feed_state_from_values(values: dict[int, float], *,
                           prev: FeedState | None = None,
                           dt_s: float | None = None) -> FeedState | None:
    """Build a :class:`FeedState` from converted dataref values (index-keyed).

    ``None`` if lat/lon/elevation are missing. Turn rate is differentiated from
    the previous track when ``prev`` / ``dt_s`` are supplied.
    """
    if any(i not in values for i in _CORE):
        return None
    lat, lon = values[0], values[1]
    track = norm360(values.get(4, values.get(5, 0.0)))
    heading = norm360(values.get(5, track))
    turn_rate = 0.0
    if prev is not None and dt_s and dt_s > 1e-6:
        d = ((track - prev.track_deg + 180.0) % 360.0) - 180.0
        turn_rate = d / dt_s
    return FeedState(
        pos=Point(lat, lon),
        heading_deg=heading,
        track_deg=track,
        gs_kt=max(0.0, values.get(3, 0.0)),
        tas_kt=max(0.0, values.get(8, values.get(3, 0.0))),
        altitude_ft=values[2],
        vs_fpm=values.get(7, 0.0),
        bank_deg=values.get(9, 0.0),
        pitch_deg=values.get(10, 0.0),
        turn_rate_dps=turn_rate,
        slip_skid=0.0,
    )


# --------------------------------------------------------------------------- #
# the socket wrapper
# --------------------------------------------------------------------------- #
class XPlaneFeed:
    def __init__(self, *, host: str = "127.0.0.1", xplane_port: int = _XPLANE_UDP_PORT,
                 rate_hz: int = 10, stale_after_s: float = 2.0):
        self.host = host
        self.xplane_port = int(xplane_port)
        self.rate_hz = int(rate_hz)
        self.stale_after_s = float(stale_after_s)
        self.sock: socket.socket | None = None
        self._raw: dict[int, float] = {}       # index -> converted value
        self._last_rx = 0.0                     # time.monotonic() of last good packet
        self._state: FeedState | None = None
        self._clock = time.monotonic

    # -- lifecycle ------------------------------------------------------
    def open(self) -> "XPlaneFeed":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        s.setblocking(False)
        self.sock = s
        self.request_all()
        return self

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.request_all(freq_hz=0)     # ask X-Plane to stop streaming
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def __enter__(self) -> "XPlaneFeed":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- io -----------------------------------------------------------
    def request_all(self, *, freq_hz: int | None = None) -> None:
        if self.sock is None:
            return
        freq = self.rate_hz if freq_hz is None else freq_hz
        dst = (self.host, self.xplane_port)
        for index, (path, _conv) in DATAREFS.items():
            self.sock.sendto(build_rref_request(freq, index, path), dst)

    def poll(self) -> int:
        """Drain pending datagrams into the value cache. Returns packet count."""
        if self.sock is None:
            return 0
        n = 0
        while True:
            try:
                data, _addr = self.sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                break
            decoded = parse_rref_response(data)
            if not decoded:
                continue
            for idx, raw in decoded.items():
                conv = DATAREFS.get(idx, (None, float))[1]
                try:
                    self._raw[idx] = conv(raw)
                except (TypeError, ValueError):
                    self._raw[idx] = raw
            n += 1
        if n and all(i in self._raw for i in _CORE):
            now = self._clock()
            dt = now - self._last_rx if self._last_rx else None
            self._state = feed_state_from_values(self._raw, prev=self._state, dt_s=dt)
            self._last_rx = now
        return n

    # -- consumer view ---------------------------------------------
    @property
    def alive(self) -> bool:
        return (self._state is not None
                and (self._clock() - self._last_rx) < self.stale_after_s)

    @property
    def state(self) -> FeedState | None:
        return self._state if self.alive else None

    @property
    def magvar_deg(self) -> float | None:
        """Local magnetic variation, EAST-POSITIVE (project convention).

        X-Plane reports ``magnetic_variation`` west-positive, so it is negated
        here. Sign has drifted across X-Plane versions; ``main`` treats this as
        an optional override and keeps the nav-data (nearest-navaid) value as
        the trustworthy default.
        """
        v = self._raw.get(6)
        return None if v is None else -float(v)
