"""Unit tests for wx_auto.py - the background weather-refresh scheduler.

Scheduling is tested by driving `poll_once(now=...)` with explicit clock
values (no real threads, no real sleeps). A separate pair of tests exercises
`start()`/`stop()` with a tiny poll step to confirm the thread actually runs
and shuts down cleanly.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc import wx  # noqa: E402
from wx_auto import METAR_REFRESH_S, TAF_REFRESH_S, WINDS_ALOFT_REFRESH_S, WxAutoUpdater  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_http(monkeypatch):
    """No real network from any test in this file."""
    metar_json = '[{"icaoId": "KLNS", "rawOb": "KLNS METAR"}]'
    taf_json = '[{"icaoId": "KLNS", "rawTAF": "KLNS TAF"}]'
    fd_text = "FT  3000   \nBOS 2113   \n"

    def fake_get(url, timeout=wx.HTTP_TIMEOUT):
        if "metar" in url:
            return metar_json
        if "taf" in url:
            return taf_json
        return fd_text

    monkeypatch.setattr(wx, "_http_get_text", fake_get)


def _updater(tmp_path, **kw):
    return WxAutoUpdater(stations=lambda: ["KLNS"], root=tmp_path, **kw)


def test_default_refresh_intervals_are_within_the_asked_for_range():
    assert 15 * 60 <= METAR_REFRESH_S <= 30 * 60
    assert TAF_REFRESH_S >= METAR_REFRESH_S
    assert WINDS_ALOFT_REFRESH_S >= METAR_REFRESH_S


def test_poll_once_fetches_metar_and_taf_immediately_on_the_first_pass(tmp_path):
    u = _updater(tmp_path)
    u.poll_once(now=0.0)
    assert wx.load_metar(tmp_path, "KLNS") is not None
    assert wx.load_taf(tmp_path, "KLNS") is not None
    assert u.fetch_count == 2


def test_poll_once_does_not_refetch_before_the_interval_elapses(tmp_path):
    u = _updater(tmp_path, metar_s=1000.0, taf_s=5000.0)
    u.poll_once(now=0.0)
    assert u.fetch_count == 2
    u.poll_once(now=1.0)                   # far short of either interval
    assert u.fetch_count == 2              # no new fetch


def test_poll_once_refetches_metar_only_once_its_interval_elapses(tmp_path):
    u = _updater(tmp_path, metar_s=1000.0, taf_s=5000.0)
    u.poll_once(now=0.0)
    assert u.fetch_count == 2
    u.poll_once(now=1000.0)                # metar due again, taf not
    assert u.fetch_count == 3
    u.poll_once(now=5000.0)                # taf due, and metar due again too (every 1000s)
    assert u.fetch_count == 5


def test_poll_once_skips_metar_and_taf_with_no_stations():
    u = WxAutoUpdater(stations=lambda: [], root=None)
    u.poll_once(now=0.0)
    assert u.fetch_count == 0


def test_poll_once_fetches_winds_aloft_when_a_region_is_given(tmp_path):
    u = WxAutoUpdater(stations=lambda: [], region=lambda: "BOS", root=tmp_path)
    u.poll_once(now=0.0)
    assert wx.load_winds_aloft_profile(tmp_path, "BOS", "BOS") is not None
    assert u.fetch_count == 1


def test_poll_once_skips_winds_aloft_without_a_region(tmp_path):
    u = WxAutoUpdater(stations=lambda: [], region=lambda: "", root=tmp_path)
    u.poll_once(now=0.0)
    assert u.fetch_count == 0


def test_on_winds_aloft_callback_fires_after_a_successful_fetch(tmp_path):
    calls = []
    u = WxAutoUpdater(stations=lambda: [], region=lambda: "BOS",
                      on_winds_aloft=lambda: calls.append(1), root=tmp_path)
    u.poll_once(now=0.0)
    assert calls == [1]


def test_on_winds_aloft_callback_does_not_fire_on_a_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "_http_get_text",
                        lambda url, timeout=wx.HTTP_TIMEOUT: (_ for _ in ()).throw(
                            wx.DataUnavailable("boom")))
    calls = []
    u = WxAutoUpdater(stations=lambda: [], region=lambda: "BOS",
                      on_winds_aloft=lambda: calls.append(1), root=tmp_path)
    u.poll_once(now=0.0)
    assert calls == []
    assert "boom" in u.last_winds_error


def test_a_fetch_error_is_recorded_but_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "_http_get_text",
                        lambda url, timeout=wx.HTTP_TIMEOUT: (_ for _ in ()).throw(
                            wx.DataUnavailable("network down")))
    u = _updater(tmp_path)
    u.poll_once(now=0.0)                   # must not raise
    assert "network down" in u.last_metar_error
    assert "network down" in u.last_taf_error
    assert u.fetch_count == 0


def test_intervals_are_floored_so_a_tiny_value_cannot_hammer_the_api():
    u = WxAutoUpdater(stations=lambda: [], metar_s=1.0, taf_s=1.0, winds_s=1.0)
    assert u._metar_s >= 60.0
    assert u._taf_s >= 60.0
    assert u._winds_s >= 60.0


# --------------------------------------------------------------------------- #
# real thread start/stop                                                    #
# --------------------------------------------------------------------------- #
def test_start_runs_the_poller_in_a_background_thread_and_stop_ends_it(tmp_path):
    u = _updater(tmp_path, metar_s=100000.0, taf_s=100000.0, poll_step_s=0.02)
    assert not u.running
    u.start()
    try:
        for _ in range(50):
            if u.fetch_count >= 2:
                break
            time.sleep(0.02)
        assert u.fetch_count >= 2           # the immediate first-pass fetch happened
        assert u.running
    finally:
        u.stop(join=True)
    assert not u.running


def test_start_is_idempotent(tmp_path):
    u = _updater(tmp_path, poll_step_s=0.02)
    u.start()
    t1 = u._thread
    u.start()                               # second call is a no-op
    assert u._thread is t1
    u.stop(join=True)
