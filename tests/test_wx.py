"""Unit tests for datasrc.wx. No real network: the HTTP layer is stubbed."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc import wx  # noqa: E402


def _fd_text(levels_used, groups_by_station):
    """Build a synthetic FD product body in the REAL layout: station id in
    cols 0-3, each level column 8 wide, with every group RIGHT-aligned under
    its header number (as the NWS product is - the original left-justified
    fixture is why a slicing bug survived until real text hit it)."""
    header = "FT  " + "".join(f"{lv:>8}" for lv in levels_used)
    lines = [header]
    for ident, groups in groups_by_station.items():
        lines.append(f"{ident:<4}" + "".join(f"{g:>8}" for g in groups))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# FD group decode                                                            #
# --------------------------------------------------------------------------- #
def test_decode_fd_group_basic_wind_and_negative_temp():
    from_deg, kt, temp = wx.decode_fd_group("2225-04", 6000)
    assert from_deg == pytest.approx(220.0)
    assert kt == pytest.approx(25.0)
    assert temp == pytest.approx(-4.0)


def test_decode_fd_group_implied_negative_temp_below_24000():
    from_deg, kt, temp = wx.decode_fd_group("232109", 9000)
    assert from_deg == pytest.approx(230.0)
    assert kt == pytest.approx(21.0)
    assert temp == pytest.approx(-9.0)


def test_decode_fd_group_explicit_positive_temp():
    from_deg, kt, temp = wx.decode_fd_group("2230+05", 6000)
    assert temp == pytest.approx(5.0)


def test_decode_fd_group_high_speed_encoding_adds_100kt():
    # dd=51 -> true dd=01 (010 deg), ff=30 -> 130 kt
    from_deg, kt, _ = wx.decode_fd_group("5130", 9000)
    assert from_deg == pytest.approx(10.0)
    assert kt == pytest.approx(130.0)


def test_decode_fd_group_light_and_variable_is_none():
    assert wx.decode_fd_group("9900", 9000) == (None, None, None)
    assert wx.decode_fd_group("", 9000) == (None, None, None)


def test_decode_fd_group_3000ft_column_has_no_temperature():
    from_deg, kt, temp = wx.decode_fd_group("2113", 3000)
    assert from_deg == pytest.approx(210.0)
    assert kt == pytest.approx(13.0)
    assert temp is None


# --------------------------------------------------------------------------- #
# FD text decode                                                             #
# --------------------------------------------------------------------------- #
def test_decode_fd_text_parses_multiple_stations_and_levels():
    text = _fd_text([3000, 6000, 9000], {
        "BOS": ["2113", "2225-04", "2321-09"],
        "ALB": ["9900", "2230+05", "2540-12"],
    })
    stations = wx.decode_fd_text(text)
    by_id = {s.ident: s for s in stations}
    assert by_id["BOS"].levels == (
        (3000, 210.0, 13.0, None),
        (6000, 220.0, 25.0, -4.0),
        (9000, 230.0, 21.0, -9.0),
    )
    # ALB's 3000 ft group is light-and-variable -> dropped, the rest kept
    assert by_id["ALB"].levels == (
        (6000, 220.0, 30.0, 5.0),
        (9000, 250.0, 40.0, -12.0),
    )


def test_decode_fd_text_ignores_blank_lines_and_preamble():
    text = "DATA BASED ON 122000Z\n\n" + _fd_text([3000], {"BOS": ["2113"]})
    stations = wx.decode_fd_text(text)
    assert [s.ident for s in stations] == ["BOS"]


def test_decode_fd_text_empty_input_returns_nothing():
    assert wx.decode_fd_text("") == []


# --------------------------------------------------------------------------- #
# fetch (HTTP stubbed)                                                       #
# --------------------------------------------------------------------------- #
METAR_JSON = json.dumps([{
    "icaoId": "KLNS", "rawOb": "KLNS 111853Z 28012KT 10SM FEW250 22/09 A3005",
    "obsTime": 1234567890, "wdir": 280, "wspd": 12, "wgst": None,
    "temp": 22.0, "dewp": 9.0, "altim": 1017.5, "fltCat": "VFR",
}])

TAF_JSON = json.dumps([{
    "icaoId": "KLNS", "rawTAF": "TAF KLNS 111740Z 1118/1218 28010KT P6SM FEW250",
    "issueTime": 1234560000, "validTimeFrom": 1234561000, "validTimeTo": 1234600000,
}])


def test_fetch_metar_parses_fields():
    reports = wx.fetch_metar(["KLNS"], get_text=lambda url: METAR_JSON)
    assert len(reports) == 1
    m = reports[0]
    assert m.station == "KLNS"
    assert m.wind_from_deg == pytest.approx(280.0)
    assert m.wind_kt == pytest.approx(12.0)
    assert m.temp_c == pytest.approx(22.0)
    assert m.altim_inhg == pytest.approx(30.05, abs=0.01)


def test_fetch_metar_raises_when_empty():
    with pytest.raises(wx.DataUnavailable):
        wx.fetch_metar(["ZZZZ"], get_text=lambda url: "[]")


def test_fetch_taf_parses_fields():
    reports = wx.fetch_taf(["KLNS"], get_text=lambda url: TAF_JSON)
    assert reports[0].station == "KLNS"
    assert "TAF KLNS" in reports[0].raw


def test_fetch_winds_aloft_decodes_and_raises_when_empty():
    text = _fd_text([3000, 6000], {"BOS": ["2113", "2225-04"]})
    stations = wx.fetch_winds_aloft("BOS", get_text=lambda url: text)
    assert stations[0].ident == "BOS"
    with pytest.raises(wx.DataUnavailable):
        wx.fetch_winds_aloft("ZZZ", get_text=lambda url: "FT  3000\n")


def test_http_get_text_400_surfaces_the_json_error_detail(monkeypatch):
    """AWC returns 400 (not 404) with a JSON body for a bad query param -
    e.g. {"status":"error","error":"Invalid value for region"} - confirmed
    live. `_http_get_text` should surface that detail in a clean
    DataUnavailable, not let the raw HTTPError propagate as a traceback."""
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        body = json.dumps({"status": "error", "error": "Invalid value for region"}).encode()
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(wx.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(wx.DataUnavailable, match="Invalid value for region"):
        wx._http_get_text("https://example.invalid/x")


def test_fetch_winds_aloft_bad_region_lists_the_valid_ones():
    """A bad `region` should tell the pilot what to try instead, not just
    that the fetch failed."""
    def get_text(url):
        raise wx.DataUnavailable("400 Bad Request: ... (Invalid value for region)")

    with pytest.raises(wx.DataUnavailable, match="BOS.*MIA.*CHI.*DFW.*SLC.*SFO"):
        wx.fetch_winds_aloft("BWI", get_text=get_text)


def test_fetch_winds_aloft_lowercases_the_region_in_the_request_url():
    """AWC's windtemp endpoint 400s on an uppercase `region`
    ({"status":"error","error":"Invalid value for region"}), unlike every
    other AWC endpoint here - confirmed live (`region=BOS` -> 400,
    `region=bos` -> 200). The CLI/README document region codes upper-case
    (`BOS`, `MIA`, ...), so the fetch itself must lower-case it for the URL
    regardless of what case the caller passed."""
    text = _fd_text([3000], {"BOS": ["2113"]})
    seen = {}

    def get_text(url):
        seen["url"] = url
        return text

    wx.fetch_winds_aloft("BOS", get_text=get_text)
    assert "region=bos" in seen["url"]
    assert "region=BOS" not in seen["url"]


# --------------------------------------------------------------------------- #
# cache + status                                                             #
# --------------------------------------------------------------------------- #
def test_cache_writes_dated_and_latest_files(tmp_path):
    path = wx._cache(tmp_path, "metar", "KLNS", {"stations": [{"station": "KLNS"}]})
    assert path.exists()
    latest = wx.wx_dir(tmp_path) / "metar" / "KLNS_latest.json"
    assert latest.exists()
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["stations"][0]["station"] == "KLNS"
    assert "fetched_at" in payload


def test_load_latest_returns_none_when_uncached(tmp_path):
    assert wx.load_latest(tmp_path, "metar", "KZZZ") is None


def test_staleness_minutes_computes_elapsed_time():
    import datetime as dt
    fetched = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)
    payload = {"fetched_at": fetched.isoformat()}
    later = fetched + dt.timedelta(minutes=90)
    assert wx.staleness_minutes(payload, on=later) == pytest.approx(90.0)


def test_load_winds_aloft_profile_builds_a_profile_for_one_station(tmp_path):
    text = _fd_text([3000, 6000, 9000], {"BOS": ["2113", "2225-04", "2321-09"]})
    stations = wx.decode_fd_text(text)
    wx._cache(tmp_path, "windtemp", "BOS",
             {"region": "BOS", "fcst": "06",
              "stations": [{"ident": s.ident, "levels": list(s.levels)} for s in stations]})
    profile = wx.load_winds_aloft_profile(tmp_path, "BOS", "BOS")
    assert profile is not None
    from_deg, kt = profile.wind_at(6000)
    assert from_deg == pytest.approx(220.0)
    assert kt == pytest.approx(25.0)


def test_load_winds_aloft_profile_none_when_station_not_in_report(tmp_path):
    wx._cache(tmp_path, "windtemp", "BOS", {"region": "BOS", "stations": []})
    assert wx.load_winds_aloft_profile(tmp_path, "BOS", "ZZZ") is None


def test_load_metar_and_taf_round_trip(tmp_path):
    m = wx.Metar.from_json(json.loads(METAR_JSON)[0])
    wx._cache(tmp_path, "metar", m.station, {"stations": [wx.asdict(m)]})
    loaded = wx.load_metar(tmp_path, "KLNS")
    assert loaded == m

    t = wx.Taf.from_json(json.loads(TAF_JSON)[0])
    wx._cache(tmp_path, "taf", t.station, {"stations": [wx.asdict(t)]})
    loaded_t = wx.load_taf(tmp_path, "klns")     # case-insensitive
    assert loaded_t == t


def test_load_metar_and_taf_none_when_uncached(tmp_path):
    assert wx.load_metar(tmp_path, "KZZZ") is None
    assert wx.load_taf(tmp_path, "KZZZ") is None


def test_fetch_and_cache_metar_writes_a_per_station_cache_and_returns_reports(tmp_path):
    reports = wx.fetch_and_cache_metar(tmp_path, ["KLNS"], get_text=lambda url: METAR_JSON)
    assert reports[0].station == "KLNS"
    assert wx.load_metar(tmp_path, "KLNS") == reports[0]


def test_fetch_and_cache_taf_writes_a_per_station_cache_and_returns_reports(tmp_path):
    reports = wx.fetch_and_cache_taf(tmp_path, ["KLNS"], get_text=lambda url: TAF_JSON)
    assert reports[0].station == "KLNS"
    assert wx.load_taf(tmp_path, "KLNS") == reports[0]


def test_fetch_and_cache_winds_aloft_writes_the_region_cache(tmp_path):
    text = _fd_text([3000, 6000], {"BOS": ["2113", "2225-04"]})
    stations = wx.fetch_and_cache_winds_aloft(tmp_path, "BOS", get_text=lambda url: text)
    assert stations[0].ident == "BOS"
    profile = wx.load_winds_aloft_profile(tmp_path, "BOS", "BOS")
    assert profile is not None


def test_fetch_and_cache_functions_default_to_the_module_http_get_text(tmp_path, monkeypatch):
    """No get_text passed -> falls back to (a possibly monkeypatched)
    `_http_get_text`, the same hook `wx_auto`'s background poller relies on."""
    monkeypatch.setattr(wx, "_http_get_text", lambda url, timeout=wx.HTTP_TIMEOUT: METAR_JSON)
    reports = wx.fetch_and_cache_metar(tmp_path, ["KLNS"])
    assert reports[0].station == "KLNS"


def test_cli_metar_multiple_idents_are_each_individually_addressable(tmp_path, monkeypatch):
    two = json.dumps([
        json.loads(METAR_JSON)[0],
        {**json.loads(METAR_JSON)[0], "icaoId": "KJFK"},
    ])
    monkeypatch.setattr(wx, "_http_get_text", lambda url, timeout=wx.HTTP_TIMEOUT: two)
    rc = wx.main(["--data-dir", str(tmp_path), "metar", "KLNS", "KJFK"])
    assert rc == 0
    assert wx.load_metar(tmp_path, "KLNS") is not None
    assert wx.load_metar(tmp_path, "KJFK") is not None


def test_load_winds_aloft_profile_none_when_nothing_cached(tmp_path):
    assert wx.load_winds_aloft_profile(tmp_path, "BOS", "BOS") is None


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #
def test_cli_metar_prints_and_caches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wx, "_http_get_text", lambda url, timeout=wx.HTTP_TIMEOUT: METAR_JSON)
    rc = wx.main(["--data-dir", str(tmp_path), "metar", "KLNS"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "KLNS" in out
    assert (wx.wx_dir(tmp_path) / "metar" / "KLNS_latest.json").exists()


def test_cli_status_reports_nothing_cached(tmp_path, capsys):
    rc = wx.main(["--data-dir", str(tmp_path), "status"])
    assert rc == 1
    assert "no cached weather" in capsys.readouterr().out


def test_cli_status_reports_a_cached_entry(tmp_path, capsys):
    wx._cache(tmp_path, "metar", "KLNS", {"stations": []})
    rc = wx.main(["--data-dir", str(tmp_path), "status"])
    assert rc == 0
    assert "KLNS" in capsys.readouterr().out


def test_cli_winds_aloft_unavailable_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wx, "_http_get_text", lambda url, timeout=wx.HTTP_TIMEOUT: "FT  3000\n")
    rc = wx.main(["--data-dir", str(tmp_path), "winds-aloft", "ZZZ"])
    assert rc == 2


REAL_FD = """(Extracted from FBUS31 KWNO 192001)
FD1US1
DATA BASED ON 191800Z
VALID 200000Z   FOR USE 2000-0300Z. TEMPS NEG ABV 24000

FT  3000    6000    9000   12000   18000   24000  30000  34000  39000
EMI 1417 2511+12 3022+10 3128+05 2828-07 2733-16 254331 263142 282355
PSB      3214+16 3219+11 3021+07 3028-04 2822-15 273231 263542 253755
"""


def test_decode_fd_text_on_real_nws_layout_keeps_every_level():
    """Regression (EMI-KLNS playtest: TRK 083 with DTK 051): on the real,
    right-aligned NWS text the decoder read the wrong columns and kept only
    39000 ft (320@135 by garbage), which the sim then applied at 5000 ft."""
    st = {s.ident: dict((lv, (d, k)) for lv, d, k, _t in s.levels)
          for s in wx.decode_fd_text(REAL_FD)}
    assert st["EMI"][3000] == (140.0, 17.0)
    assert st["EMI"][6000] == (250.0, 11.0)
    assert st["EMI"][39000] == (280.0, 23.0)
    assert len(st["EMI"]) == 9
    assert 3000 not in st["PSB"] and st["PSB"][6000] == (320.0, 14.0)   # blank 3000 col
