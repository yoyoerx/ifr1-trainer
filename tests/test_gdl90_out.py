"""M7 - GDL90 encoders. CRC + framing are checked against the spec worked example."""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import gdl90_out as g  # noqa: E402

# GDL90 spec appendix worked example: this Heartbeat message ...
_HB_MSG = bytes([0x00, 0x81, 0x41, 0xDB, 0xD0, 0x08, 0x02])
# ... has this CRC (sent little-endian) and frames to this exact datagram.
_HB_CRC = 0x8BB3
_HB_FRAMED = bytes.fromhex("7e 00 81 41 db d0 08 02 b3 8b 7e".replace(" ", ""))


def test_crc16_matches_spec_example():
    assert g.crc16(_HB_MSG) == _HB_CRC


def test_frame_message_matches_spec_example():
    assert g.frame_message(_HB_MSG) == _HB_FRAMED


def test_frame_applies_byte_stuffing():
    # a payload byte of 0x7E must become 0x7D 0x5E inside the frame
    framed = g.frame_message(b"\x00\x7e\x7d")
    inner = framed[1:-1]
    assert b"\x7d\x5e" in inner and b"\x7d\x5d" in inner
    assert framed[0] == 0x7E and framed[-1] == 0x7E
    assert inner.count(b"\x7e") == 0                      # no raw flag survived


def test_heartbeat_shape():
    hb = g.heartbeat(timestamp=0)
    assert hb[0] == 0x7E and hb[-1] == 0x7E
    assert hb[1] == 0x00                                  # message id
    assert hb[2] & 0x80                                   # GPS position valid bit


def test_ownship_report_is_28_byte_payload_and_encodes_position():
    pkt = g.ownship_report(lat=44.0, lon=-93.0, alt_ft=5000.0,
                           track_deg=270.0, gs_kt=150.0, vs_fpm=640.0,
                           callsign="N123AB")
    assert pkt[0] == 0x7E and pkt[-1] == 0x7E
    payload = pkt[1:-3]                                   # strip flags + CRC (no stuffing here)
    assert len(payload) == 28
    assert payload[0] == 0x0A

    lat_counts = int.from_bytes(payload[5:8], "big", signed=True)
    lon_counts = int.from_bytes(payload[8:11], "big", signed=True)
    assert lat_counts / (0x800000 / 180.0) == pytest.approx(44.0, abs=1e-3)
    assert lon_counts / (0x800000 / 180.0) == pytest.approx(-93.0, abs=1e-3)

    track = payload[17] * 360.0 / 256.0
    assert track == pytest.approx(270.0, abs=1.5)
    assert payload[19:27] == b"N123AB  "                  # 8 chars, space padded


def test_ownship_report_altitude_encoding():
    payload = g.ownship_report(lat=0, lon=0, alt_ft=0.0, track_deg=0, gs_kt=0)[1:-3]
    alt = (payload[11] << 4) | (payload[12] >> 4)
    assert alt * 25 - 1000 == pytest.approx(0.0, abs=25)


def test_geo_altitude_message():
    pkt = g.ownship_geo_altitude(geo_alt_ft=5280.0)
    payload = pkt[1:-3]
    assert payload[0] == 0x0B
    counts = int.from_bytes(payload[1:3], "big", signed=True)
    assert counts * 5 == pytest.approx(5280.0, abs=5)


def test_foreflight_id_message():
    pkt = g.foreflight_id()
    payload = pkt[1:-3]
    assert payload[0] == 0x65 and payload[1] == 0x00
    assert b"Octavi" in payload


def test_sender_rate_limits(monkeypatch):
    sent = []
    s = g.GDL90Sender()
    monkeypatch.setattr(s, "_emit", lambda pkt: sent.append(pkt))
    clock = {"t": 0.0}
    monkeypatch.setattr(s, "_clock", lambda: clock["t"])

    class _Own:
        class pos:
            lat, lon = 40.0, -74.0
        altitude_ft = 3000.0
        track_deg = 90.0
        gs_kt = 120.0
        vs_fpm = 0.0

    s.send(_Own())
    first = len(sent)
    assert first >= 3                                     # heartbeat + id + ownship + geo
    s.send(_Own())                                        # same instant -> nothing new
    assert len(sent) == first
    clock["t"] = 1.5
    s.send(_Own())                                        # heartbeat + reports due again
    assert len(sent) > first
