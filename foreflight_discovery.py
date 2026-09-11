"""foreflight_discovery.py - listen for ForeFlight's own UDP discovery broadcast.

ForeFlight broadcasts a small JSON message on UDP port 63093 every ~5 s
while it is running in the foreground::

    {"App": "ForeFlight", "GDL90": {"port": 4000}}

(see https://www.foreflight.com/connect/spec/, "ForeFlight Broadcast"). This
is the *opposite* direction from `gdl90_out.py`'s own traffic: ForeFlight is
advertising itself so a GDL90 source on the same network can learn its IP
address and send GDL90 as a direct UDP unicast instead of broadcasting
blindly - useful on Wi-Fi networks that drop or block broadcast traffic
(client/AP isolation is common on hotel, FBO, and some home-router guest
networks).

Pure decode (`parse_discovery`) + a small non-blocking socket wrapper
(`ForeFlightListener`), same shape as `xplane_feed.py`'s RREF listener.
Nothing here sends anything - `main.py` retargets its own `gdl90_out.
GDL90Sender` once a device is discovered.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass

__all__ = ["DISCOVERY_PORT", "ForeFlightDevice", "ForeFlightListener", "parse_discovery"]

DISCOVERY_PORT = 63093
_STALE_AFTER_S = 15.0     # ForeFlight re-broadcasts every ~5 s; a few misses is fine


@dataclass(frozen=True, slots=True)
class ForeFlightDevice:
    """One ForeFlight instance seen on the network."""

    ip: str
    gdl90_port: int
    app: str = "ForeFlight"


def parse_discovery(data: bytes) -> tuple[str, int] | None:
    """Decode one discovery datagram's body -> ``(app_name, gdl90_port)``, or
    ``None`` if it isn't a recognizable discovery message. Tolerant of any
    app name (not just literally "ForeFlight") in case another EFB adopts
    the same wire format, since the port is all that actually matters here."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    gdl90 = obj.get("GDL90")
    if not isinstance(gdl90, dict) or "port" not in gdl90:
        return None
    try:
        port = int(gdl90["port"])
    except (TypeError, ValueError):
        return None
    return str(obj.get("App", "ForeFlight")), port


class ForeFlightListener:
    """Non-blocking listener for ForeFlight's discovery broadcast.

    Tracks every device currently broadcasting (there can be more than one
    tablet on the network); :attr:`primary` is the simplest single target -
    the most recently heard-from device that hasn't gone stale.
    """

    def __init__(self, *, port: int = DISCOVERY_PORT, stale_after_s: float = _STALE_AFTER_S):
        self.port = int(port)
        self.stale_after_s = float(stale_after_s)
        self.sock: socket.socket | None = None
        self._seen: dict[str, tuple[ForeFlightDevice, float]] = {}   # ip -> (device, last_rx)
        self._clock = time.monotonic

    # -- lifecycle ------------------------------------------------------
    def open(self) -> "ForeFlightListener":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):    # not available on Windows; best-effort
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", self.port))
        s.setblocking(False)
        self.sock = s
        return self

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __enter__(self) -> "ForeFlightListener":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- io ---------------------------------------------------------
    def poll(self) -> int:
        """Drain pending discovery datagrams. Returns the count processed."""
        if self.sock is None:
            return 0
        n = 0
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                break
            decoded = parse_discovery(data)
            if decoded is None:
                continue
            app, port = decoded
            ip = addr[0]
            self._seen[ip] = (ForeFlightDevice(ip=ip, gdl90_port=port, app=app), self._clock())
            n += 1
        return n

    # -- consumer view ---------------------------------------------
    @property
    def devices(self) -> list[ForeFlightDevice]:
        """Currently-fresh devices, most recently seen first."""
        now = self._clock()
        fresh = [(dev, t) for dev, t in self._seen.values() if now - t < self.stale_after_s]
        fresh.sort(key=lambda pair: pair[1], reverse=True)
        return [dev for dev, _ in fresh]

    @property
    def primary(self) -> ForeFlightDevice | None:
        """The most recently seen fresh device, or ``None``."""
        devs = self.devices
        return devs[0] if devs else None
