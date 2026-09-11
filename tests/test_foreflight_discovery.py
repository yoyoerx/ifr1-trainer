"""foreflight_discovery.py - ForeFlight's own UDP discovery broadcast, pure
decode + a loopback integration test for the listener."""

import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import foreflight_discovery as ffd  # noqa: E402


# --------------------------------------------------------------------------- #
# pure decode
# --------------------------------------------------------------------------- #
def test_parse_discovery_decodes_the_documented_message():
    msg = json.dumps({"App": "ForeFlight", "GDL90": {"port": 4000}}).encode()
    assert ffd.parse_discovery(msg) == ("ForeFlight", 4000)


def test_parse_discovery_accepts_any_app_name():
    msg = json.dumps({"App": "SomeOtherEFB", "GDL90": {"port": 4001}}).encode()
    assert ffd.parse_discovery(msg) == ("SomeOtherEFB", 4001)


def test_parse_discovery_defaults_app_name_when_missing():
    msg = json.dumps({"GDL90": {"port": 4000}}).encode()
    assert ffd.parse_discovery(msg) == ("ForeFlight", 4000)


def test_parse_discovery_rejects_non_json():
    assert ffd.parse_discovery(b"not json at all") is None


def test_parse_discovery_rejects_json_that_is_not_an_object():
    assert ffd.parse_discovery(b"[1, 2, 3]") is None


def test_parse_discovery_rejects_missing_gdl90_field():
    assert ffd.parse_discovery(json.dumps({"App": "ForeFlight"}).encode()) is None


def test_parse_discovery_rejects_gdl90_without_a_port():
    assert ffd.parse_discovery(json.dumps({"App": "X", "GDL90": {}}).encode()) is None


def test_parse_discovery_rejects_a_non_numeric_port():
    msg = json.dumps({"App": "X", "GDL90": {"port": "not-a-number"}}).encode()
    assert ffd.parse_discovery(msg) is None


def test_parse_discovery_rejects_undecodable_bytes():
    assert ffd.parse_discovery(b"\xff\xfe\x00\x01") is None


# --------------------------------------------------------------------------- #
# loopback: a fake ForeFlight broadcasting to us
# --------------------------------------------------------------------------- #
def test_listener_tracks_a_device_from_a_loopback_broadcast():
    listener = ffd.ForeFlightListener(port=0, stale_after_s=5.0)
    clock = {"t": 1000.0}
    listener._clock = lambda: clock["t"]
    listener.open()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        my_port = listener.sock.getsockname()[1]
        msg = json.dumps({"App": "ForeFlight", "GDL90": {"port": 4000}}).encode()
        sender.sendto(msg, ("127.0.0.1", my_port))

        assert listener.poll() == 1
        dev = listener.primary
        assert dev is not None
        assert dev.ip == "127.0.0.1"
        assert dev.gdl90_port == 4000
        assert dev.app == "ForeFlight"
        assert listener.devices == [dev]

        clock["t"] += 20.0                       # well past stale_after_s
        assert listener.primary is None
        assert listener.devices == []
    finally:
        sender.close()
        listener.close()


def test_listener_ignores_garbage_datagrams_without_crashing():
    listener = ffd.ForeFlightListener(port=0)
    listener.open()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        my_port = listener.sock.getsockname()[1]
        sender.sendto(b"not a discovery message", ("127.0.0.1", my_port))
        assert listener.poll() == 0
        assert listener.primary is None
    finally:
        sender.close()
        listener.close()


def test_listener_primary_is_the_most_recently_seen_device():
    # two distinct devices are keyed by source IP (127.0.0.0/8 loopback aliases
    # both work fine as separate addresses) - a re-broadcast from the SAME ip
    # (the normal case: one iPad, one IP, re-broadcasting every ~5s) updates
    # the existing entry rather than creating a duplicate.
    listener = ffd.ForeFlightListener(port=0, stale_after_s=5.0)
    clock = {"t": 1000.0}
    listener._clock = lambda: clock["t"]
    listener.open()
    sender_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender_a.bind(("127.0.0.1", 0))
    sender_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender_b.bind(("127.0.0.2", 0))
    try:
        my_port = listener.sock.getsockname()[1]
        msg = json.dumps({"App": "ForeFlight", "GDL90": {"port": 4000}}).encode()
        sender_a.sendto(msg, ("127.0.0.1", my_port))
        listener.poll()
        clock["t"] += 1.0
        sender_b.sendto(msg, ("127.0.0.1", my_port))
        listener.poll()

        assert len(listener.devices) == 2
        assert listener.primary.ip == "127.0.0.2"         # most recently heard from
        ips = {d.ip for d in listener.devices}
        assert ips == {"127.0.0.1", "127.0.0.2"}
    finally:
        sender_a.close()
        sender_b.close()
        listener.close()


def test_poll_with_no_socket_is_a_harmless_noop():
    listener = ffd.ForeFlightListener()
    assert listener.poll() == 0
