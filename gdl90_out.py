"""gdl90_out.py - broadcast ownship state as GDL90 so a tablet EFB tracks it.

ForeFlight, Garmin Pilot and FltPlan Go all listen for **GDL90** on UDP port
4000. This module encodes the trainer's ownship into the three messages an EFB
needs to show a moving aeroplane - Heartbeat (0x00), Ownship Report (0x0A) and
Ownship Geometric Altitude (0x0B) - plus the ForeFlight ID (0x65/0x00) that
labels the sender. Pure ``struct`` + ``socket``; no third-party deps.

The encoders are pure functions with byte-exact behaviour (CRC-16-CCITT per the
GDL90 spec appendix, then ``0x7E`` framing with ``0x7D`` escape). :class:`GDL90Sender`
is the thin UDP wrapper ``main`` calls once per loop.

Note: X-Plane can already emit this natively (Settings -> Network -> "Send to
ForeFlight"); this module is for the standalone ``sim_model`` case. When both are
active, prefer the sim's own output and leave ``--gdl90`` off.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

__all__ = [
    "crc16",
    "frame_message",
    "heartbeat",
    "ownship_report",
    "ownship_geo_altitude",
    "foreflight_id",
    "GDL90Sender",
]

_FLAG = 0x7E
_ESC = 0x7D
_SEMI = 0x800000 / 180.0          # semicircle scale: 2^23 counts / 180 deg


def _crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = (i << 8) & 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) & 0xFFFF) ^ (0x1021 if crc & 0x8000 else 0)
        table.append(crc)
    return table


_TABLE = _crc16_table()


def crc16(data: bytes) -> int:
    """GDL90 CRC-16-CCITT over the un-stuffed message (id + payload)."""
    crc = 0
    for b in data:
        crc = (_TABLE[(crc >> 8) & 0xFF] ^ ((crc << 8) & 0xFFFF) ^ b) & 0xFFFF
    return crc


def _stuff(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b in (_FLAG, _ESC):
            out.append(_ESC)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


def frame_message(payload: bytes) -> bytes:
    """Wrap a raw message (id + data) with CRC and ``0x7E`` framing + stuffing."""
    crc = crc16(payload)
    body = payload + bytes((crc & 0xFF, (crc >> 8) & 0xFF))   # CRC little-endian
    return bytes((_FLAG,)) + _stuff(body) + bytes((_FLAG,))


# --------------------------------------------------------------------------- #
# messages
# --------------------------------------------------------------------------- #
def _seconds_since_midnight_utc(t: float | None = None) -> int:
    t = time.time() if t is None else t
    return int(t) % 86400


def heartbeat(*, gps_valid: bool = True, utc_ok: bool = True,
              timestamp: int | None = None) -> bytes:
    """Message 0x00. One per second keeps the EFB's connection status green."""
    ts = _seconds_since_midnight_utc() if timestamp is None else int(timestamp)
    st1 = 0x01                                   # bit0: UAT initialised
    if gps_valid:
        st1 |= 0x80                              # bit7: GPS position valid
    st2 = 0x00
    if utc_ok:
        st2 |= 0x01                              # bit0: UTC timing valid
    if ts & 0x10000:
        st2 |= 0x80                              # bit7: timestamp bit 16
    payload = bytes((0x00, st1, st2, ts & 0xFF, (ts >> 8) & 0xFF, 0x00, 0x00))
    return frame_message(payload)


def _pack_latlon(deg: float) -> bytes:
    counts = int(round(deg * _SEMI))
    counts = max(-0x800000, min(0x7FFFFF, counts)) & 0xFFFFFF
    return bytes((counts >> 16 & 0xFF, counts >> 8 & 0xFF, counts & 0xFF))


def _position_report(msg_id: int, *, lat: float, lon: float, alt_ft: float,
                     track_deg: float, gs_kt: float, vs_fpm: float,
                     callsign: str, misc: int, nic: int, nacp: int,
                     emitter: int, address: int, airborne: bool) -> bytes:
    b = bytearray()
    b.append(msg_id)
    b.append(0x00)                                             # alert(hi)=0, addr type(lo)=0 (ADS-B ICAO)
    b += bytes((address >> 16 & 0xFF, address >> 8 & 0xFF, address & 0xFF))
    b += _pack_latlon(lat)
    b += _pack_latlon(lon)

    if alt_ft <= -1000 or alt_ft is None:
        alt = 0xFFF
    else:
        alt = int(round((alt_ft + 1000) / 25.0))
        alt = max(0, min(0xFFE, alt))
    misc_nib = misc | (0x08 if airborne else 0x00)             # bit3 = airborne
    b.append(alt >> 4 & 0xFF)
    b.append(((alt & 0x0F) << 4) | (misc_nib & 0x0F))

    b.append(((nic & 0x0F) << 4) | (nacp & 0x0F))

    hvel = 0xFFF if gs_kt < 0 else min(0xFFE, int(round(gs_kt)))
    vv = int(round(vs_fpm / 64.0))
    vv = max(-0x1FF, min(0x1FF, vv)) & 0xFFF                   # 12-bit signed, 64 fpm units
    b.append(hvel >> 4 & 0xFF)
    b.append(((hvel & 0x0F) << 4) | (vv >> 8 & 0x0F))
    b.append(vv & 0xFF)

    b.append(int(round((track_deg % 360.0) * 256.0 / 360.0)) & 0xFF)
    b.append(emitter & 0xFF)

    cs = (callsign or "").upper()[:8].ljust(8)
    b += cs.encode("ascii", "replace")
    b.append(0x00)                                             # emergency/priority code + spare
    return bytes(b)


def ownship_report(*, lat: float, lon: float, alt_ft: float, track_deg: float,
                   gs_kt: float, vs_fpm: float = 0.0, callsign: str = "OCTAVI",
                   nic: int = 10, nacp: int = 10, emitter: int = 1,
                   address: int = 0, airborne: bool = True) -> bytes:
    """Message 0x0A - the aircraft's own position/velocity (28-byte payload)."""
    payload = _position_report(0x0A, lat=lat, lon=lon, alt_ft=alt_ft,
                               track_deg=track_deg, gs_kt=gs_kt, vs_fpm=vs_fpm,
                               callsign=callsign, misc=0x01, nic=nic, nacp=nacp,
                               emitter=emitter, address=address, airborne=airborne)
    return frame_message(payload)


def ownship_geo_altitude(*, geo_alt_ft: float, vpl_m: int | None = None) -> bytes:
    """Message 0x0B - geometric (GPS) altitude in 5-ft units, plus VFOM."""
    counts = int(round(geo_alt_ft / 5.0))
    counts = max(-0x8000, min(0x7FFF, counts)) & 0xFFFF
    metrics = 0x7FFF if vpl_m is None else (vpl_m & 0x7FFF)
    payload = bytes((0x0B, counts >> 8 & 0xFF, counts & 0xFF,
                     metrics >> 8 & 0xFF, metrics & 0xFF))
    return frame_message(payload)


def foreflight_id(*, name: str = "Octavi", long_name: str = "Octavi IFR Trainer",
                  serial: int = 0xFFFFFFFFFFFFFFFF, capabilities: int = 0) -> bytes:
    """Message 0x65 sub-id 0x00 - names the sender in ForeFlight's device list."""
    payload = struct.pack(
        ">BBBQ8s16sI",
        0x65, 0x00, 0x01, serial & 0xFFFFFFFFFFFFFFFF,
        name.encode("ascii", "replace")[:8].ljust(8, b"\x00"),
        long_name.encode("ascii", "replace")[:16].ljust(16, b"\x00"),
        capabilities & 0xFFFFFFFF,
    )
    return frame_message(payload)


# --------------------------------------------------------------------------- #
# UDP sender
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Ownship:
    lat: float
    lon: float
    alt_ft: float
    track_deg: float
    gs_kt: float
    vs_fpm: float = 0.0


class GDL90Sender:
    """Fire-and-forget GDL90 broadcaster. Call :meth:`send` each loop; it rate-
    limits the heartbeat to 1 Hz and the reports to ``report_hz`` internally."""

    def __init__(self, *, host: str = "255.255.255.255", port: int = 4000,
                 callsign: str = "OCTAVI", report_hz: float = 2.0):
        self.addr = (host, int(port))
        self.callsign = callsign
        self.report_period = 1.0 / max(0.1, report_hz)
        self.sock: socket.socket | None = None
        self._t_hb = -1e9                 # force everything out on the first send()
        self._t_rep = -1e9
        self._t_id = -1e9
        self._clock = time.monotonic

    def open(self) -> "GDL90Sender":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setblocking(False)
        self.sock = s
        return self

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __enter__(self) -> "GDL90Sender":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _emit(self, pkt: bytes) -> None:
        if self.sock is not None:
            try:
                self.sock.sendto(pkt, self.addr)
            except OSError:
                pass

    def send(self, own) -> None:
        """``own`` is any object with pos/track_deg/gs_kt/altitude_ft/vs_fpm
        (``sim_model.Ownship`` or ``xplane_feed.FeedState``)."""
        now = self._clock()
        if now - self._t_hb >= 1.0:
            self._emit(heartbeat())
            self._t_hb = now
        if now - self._t_id >= 5.0:
            self._emit(foreflight_id())
            self._t_id = now
        if now - self._t_rep >= self.report_period:
            lat = own.pos.lat
            lon = own.pos.lon
            self._emit(ownship_report(
                lat=lat, lon=lon, alt_ft=own.altitude_ft,
                track_deg=own.track_deg, gs_kt=own.gs_kt,
                vs_fpm=getattr(own, "vs_fpm", 0.0), callsign=self.callsign,
            ))
            self._emit(ownship_geo_altitude(geo_alt_ft=own.altitude_ft))
            self._t_rep = now
