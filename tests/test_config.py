"""M7 - on-disk config file, merged under the CLI."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config as cfg  # noqa: E402
import main as main_mod  # noqa: E402


def test_merge_precedence_defaults_file_cli():
    merged = cfg.merge({"unit": "430"}, {"unit": "530", "layout": "steam"})
    assert merged["unit"] == "430"          # CLI wins over file
    assert merged["layout"] == "steam"      # file wins over default
    assert merged["tas"] == cfg.DEFAULTS["tas"]   # untouched default


def test_merge_ignores_none_cli_values_and_unknown_file_keys():
    merged = cfg.merge({"unit": None, "bogus": 1}, {"nonsense": True})
    assert merged["unit"] == cfg.DEFAULTS["unit"]
    assert "bogus" not in merged and "nonsense" not in merged


def test_load_config_reads_toml(tmp_path, monkeypatch):
    p = tmp_path / "octavi.toml"
    p.write_text('unit = "430"\nlayout = "steam"\ngdl90 = true\n'
                 'weird-key = 3\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    loaded = cfg.load_config()
    assert loaded == {"unit": "430", "layout": "steam", "gdl90": True}


def test_load_config_reads_json(tmp_path, monkeypatch):
    (tmp_path / "octavi.json").write_text('{"wind": "270/15", "tas": 160}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cfg.load_config() == {"wind": "270/15", "tas": 160}


def test_load_config_absent_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cfg.load_config() == {}


def test_parse_args_applies_config_then_cli(tmp_path, monkeypatch):
    (tmp_path / "octavi.toml").write_text(
        'unit = "430"\nlayout = "steam"\nwind = "300/25"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    c = main_mod.parse_args(["--no-device", "--layout", "gps"])
    assert c.unit == "430"                  # from the file
    assert c.layout == "gps"                # CLI overrides the file
    assert c.wind_from == 300.0 and c.wind_kt == 25.0
    assert c.no_device is True
    assert c.gdl90 is False                 # untouched default


def test_parse_args_winds_aloft_overrides_uniform_wind():
    c = main_mod.parse_args(["--no-device", "--wind", "300/25",
                             "--winds-aloft", "3000:280/20/-05 9000:300/35/-15"])
    assert c.winds_aloft is not None
    assert len(c.winds_aloft.levels) == 2
    assert c.winds_aloft.levels[0].temp_c == -5.0


def test_parse_args_malformed_winds_aloft_is_ignored_not_fatal(capsys):
    c = main_mod.parse_args(["--no-device", "--winds-aloft", "garbage"])
    assert c.winds_aloft is None
    assert "ignoring --winds-aloft" in capsys.readouterr().out


def test_parse_args_wx_region_and_station_default_empty():
    c = main_mod.parse_args(["--no-device"])
    assert c.wx_region == "" and c.wx_station == ""
    assert c.winds_aloft is None


def test_parse_args_wx_region_and_station_pass_through():
    c = main_mod.parse_args(["--no-device", "--wx-region", "BOS", "--wx-station", "KLNS"])
    assert c.wx_region == "BOS" and c.wx_station == "KLNS"


def test_parse_args_wx_auto_refresh_defaults_off_with_sensible_intervals():
    c = main_mod.parse_args(["--no-device"])
    assert c.wx_auto_refresh is False
    assert c.wx_metar_minutes == 20.0
    assert c.wx_taf_minutes == 60.0
    assert c.wx_winds_aloft_minutes == 60.0


def test_parse_args_wx_auto_refresh_flags_pass_through():
    c = main_mod.parse_args(["--no-device", "--wx-auto-refresh",
                             "--wx-metar-minutes", "15", "--wx-taf-minutes", "45",
                             "--wx-winds-aloft-minutes", "90"])
    assert c.wx_auto_refresh is True
    assert c.wx_metar_minutes == 15.0
    assert c.wx_taf_minutes == 45.0
    assert c.wx_winds_aloft_minutes == 90.0


def test_parse_args_time_warp_defaults_to_1x():
    c = main_mod.parse_args(["--no-device"])
    assert c.time_warp == 1


def test_parse_args_time_warp_accepts_a_valid_level():
    c = main_mod.parse_args(["--no-device", "--time-warp", "10"])
    assert c.time_warp == 10


def test_parse_args_time_warp_rejects_an_invalid_level():
    with pytest.raises(SystemExit):
        main_mod.parse_args(["--no-device", "--time-warp", "3"])


def test_config_time_warp_falls_back_to_1x_for_a_bad_config_file_value(tmp_path, monkeypatch):
    (tmp_path / "octavi.toml").write_text("time_warp = 7\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    c = main_mod.parse_args(["--no-device"])
    assert c.time_warp == 1


def test_parse_args_new_feed_flags():
    c = main_mod.parse_args(["--no-device", "--xplane-feed", "--xplane-host", "10.0.0.5",
                             "--gdl90", "--gdl90-host", "192.168.1.255"])
    assert c.xplane_feed is True and c.xplane_host == "10.0.0.5"
    assert c.gdl90 is True and c.gdl90_host == "192.168.1.255"
    assert c.gdl90_port == 4000


def test_parse_args_gdl90_discover_defaults_off():
    c = main_mod.parse_args(["--no-device"])
    assert c.gdl90_discover is False
    assert c.gdl90 is False


def test_parse_args_gdl90_discover_implies_gdl90():
    c = main_mod.parse_args(["--no-device", "--gdl90-discover"])
    assert c.gdl90_discover is True
    assert c.gdl90 is True                  # --gdl90 itself was NOT passed


def test_parse_args_gdl90_explicit_still_works_without_discover():
    c = main_mod.parse_args(["--no-device", "--gdl90"])
    assert c.gdl90 is True
    assert c.gdl90_discover is False
