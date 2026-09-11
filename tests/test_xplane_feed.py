"""M7 - the X-Plane RREF position feed: pure protocol + a loopback integration."""

import os
import socket
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navmath import Point  # noqa: E402
import xplane_feed as xf  # noqa: E402


# --------------------------------------------------------------------------- #
# pure protocol
# --------------------------------------------------------------------------- #
def test_rref_request_is_413_bytes_and_round_trips():
    pkt = xf.build_rref_request(10, 3, "sim/flightmodel/position/groundspeed")
    assert len(pkt) == 413
    assert pkt[:5] == b"RREF\x00"
    freq, index = struct.unpack_from("<ii", pkt, 5)
    path = pkt[13:].split(b"\x00", 1)[0].decode()
    assert (freq, index, path) == (10, 3, "sim/flightmodel/position/groundspeed")


def test_rref_request_freq_zero_cancels():
    assert struct.unpack_from("<i", xf.build_rref_request(0, 1, "x"), 5)[0] == 0


def test_parse_rref_response_decodes_records():
    data = b"RREF," + struct.pack("<if", 0, 42.36) + struct.pack("<if", 1, -71.01)
    out = xf.parse_rref_response(data)
    assert out[0] == pytest.approx(42.36, abs=1e-4)
    assert out[1] == pytest.approx(-71.01, abs=1e-4)


def test_parse_rref_response_tolerates_short_header_and_trailing_junk():
    good = b"RREF\x00" + struct.pack("<if", 7, 500.0) + b"\x01\x02\x03"   # 3 dangling bytes
    assert xf.parse_rref_response(good) == {7: pytest.approx(500.0)}
    assert xf.parse_rref_response(b"XPLANEDATA....") == {}
    assert xf.parse_rref_response(b"RRE") == {}


def test_feed_state_from_values_builds_ownship_shape():
    vals = {0: 40.0, 1: -74.0, 2: 3500.0, 3: 120.0, 4: 91.0, 5: 88.0,
            7: -250.0, 8: 135.0, 9: -4.0, 10: 2.5}
    st = xf.feed_state_from_values(vals)
    assert isinstance(st.pos, Point)
    assert (st.pos.lat, st.pos.lon) == (40.0, -74.0)
    assert st.altitude_ft == 3500.0
    assert st.gs_kt == 120.0 and st.tas_kt == 135.0
    assert st.track_deg == 91.0 and st.heading_deg == 88.0
    assert st.vs_fpm == -250.0 and st.bank_deg == -4.0 and st.pitch_deg == 2.5


def test_feed_state_needs_a_position_fix():
    assert xf.feed_state_from_values({3: 120.0, 5: 90.0}) is None


def test_feed_state_differentiates_turn_rate():
    a = xf.feed_state_from_values({0: 0.0, 1: 0.0, 2: 0.0, 4: 10.0})
    b = xf.feed_state_from_values({0: 0.0, 1: 0.0, 2: 0.0, 4: 19.0}, prev=a, dt_s=3.0)
    assert b.turn_rate_dps == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# loopback: a fake X-Plane on 127.0.0.1
# --------------------------------------------------------------------------- #
def _converted(raw: dict[int, float]) -> dict[int, float]:
    """raw XP units -> project units, the way XPlaneFeed.poll does it."""
    return {i: xf.DATAREFS.get(i, (None, float))[1](v) for i, v in raw.items()}


def test_feed_goes_live_from_a_loopback_packet_then_stales():
    xplane = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    xplane.bind(("127.0.0.1", 0))
    xplane.settimeout(0.5)

    feed = xf.XPlaneFeed(host="127.0.0.1", xplane_port=xplane.getsockname()[1],
                         stale_after_s=5.0)
    clock = {"t": 1000.0}
    feed._clock = lambda: clock["t"]
    feed.open()
    try:
        # the feed should have sent us 11 subscription requests
        got = 0
        try:
            while True:
                xplane.recvfrom(1024)
                got += 1
        except socket.timeout:
            pass
        assert got == len(xf.DATAREFS)

        # push one response datagram covering lat/lon/elev/gs
        raw = {0: 41.0, 1: -73.0, 2: 304.8, 3: 51.44}      # elev m, gs m/s
        body = b"".join(struct.pack("<if", i, v) for i, v in raw.items())
        feed.sock.sendto(b"RREF," + body, ("127.0.0.1", feed.sock.getsockname()[1]))

        assert feed.poll() == 1
        assert feed.alive
        st = feed.state
        assert (st.pos.lat, st.pos.lon) == (41.0, -73.0)
        assert st.altitude_ft == pytest.approx(1000.0, abs=1.0)     # 304.8 m
        assert st.gs_kt == pytest.approx(100.0, abs=0.5)            # 51.44 m/s

        clock["t"] += 10.0                                          # now well past stale_after_s
        assert not feed.alive
        assert feed.state is None
    finally:
        feed.close()
        xplane.close()
