"""Unit tests for datasrc.dtpp. No real network: the HTTP layer is stubbed."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc import dtpp  # noqa: E402
from datasrc.airac import cycle_from_ident  # noqa: E402

CYCLE = cycle_from_ident("2609")

SAMPLE_METAFILE = b"""<?xml version="1.0"?>
<digital_tpp cycle="2609" from_edate="2026-08-13" to_edate="2026-09-10">
  <state_code ID="PA">
    <city_name volume="NE-3">LANCASTER
      <airport_name apt_ident="LNS" military="N">LANCASTER
        <record>
          <chartseq>10100</chartseq>
          <chart_code>IAP</chart_code>
          <chart_name>ILS OR LOC RWY 08</chart_name>
          <useraction>C</useraction>
          <pdf_name>00775IL8.PDF</pdf_name>
        </record>
        <record>
          <chartseq>10200</chartseq>
          <chart_code>IAP</chart_code>
          <chart_name>RNAV (GPS) RWY 26</chart_name>
          <useraction></useraction>
          <pdf_name>00775R26.PDF</pdf_name>
        </record>
        <record>
          <chartseq>10300</chartseq>
          <chart_code>APD</chart_code>
          <chart_name>AIRPORT DIAGRAM</chart_name>
          <useraction>A</useraction>
          <pdf_name>00775AD.PDF</pdf_name>
        </record>
      </airport_name>
    </city_name>
  </state_code>
  <state_code ID="MA">
    <city_name volume="NE-2">BOSTON
      <airport_name apt_ident="BOS" military="N">BOSTON
        <record>
          <chartseq>20100</chartseq>
          <chart_code>IAP</chart_code>
          <chart_name>ILS OR LOC RWY 04R</chart_name>
          <useraction></useraction>
          <pdf_name>00001IL4R.PDF</pdf_name>
        </record>
      </airport_name>
    </city_name>
  </state_code>
</digital_tpp>
"""


# --------------------------------------------------------------------------- #
# URLs                                                                       #
# --------------------------------------------------------------------------- #
def test_metafile_url():
    assert dtpp.metafile_url(CYCLE) == \
        "https://aeronav.faa.gov/d-tpp/2609/xml_data/d-TPP_Metafile.xml"


def test_pdf_url_quotes_the_filename():
    assert dtpp.pdf_url(CYCLE, "00775 IL8.PDF") == \
        "https://aeronav.faa.gov/d-tpp/2609/00775%20IL8.PDF"


# --------------------------------------------------------------------------- #
# metafile parsing                                                          #
# --------------------------------------------------------------------------- #
def test_parse_metafile_reads_every_record():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    assert len(records) == 4
    lns = [r for r in records if r.airport_ident == "LNS"]
    assert len(lns) == 3
    ils = next(r for r in lns if "ILS" in r.chart_name)
    assert ils.chart_code == "IAP"
    assert ils.pdf_name == "00775IL8.PDF"
    assert ils.useraction == "C"
    assert ils.state == "PA"


def test_parse_metafile_skips_records_with_no_pdf():
    xml = SAMPLE_METAFILE.replace(b"<pdf_name>00775IL8.PDF</pdf_name>", b"<pdf_name></pdf_name>")
    records = dtpp.parse_metafile(xml)
    assert len(records) == 3           # the one with a blank pdf_name is dropped


def test_charts_for_airport_matches_icao_by_stripping_leading_k():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    charts = dtpp.charts_for_airport(records, "KLNS")
    assert len(charts) == 3
    assert all(c.airport_ident == "LNS" for c in charts)


def test_charts_for_airport_matches_the_bare_faa_lid_too():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    assert len(dtpp.charts_for_airport(records, "LNS")) == 3


def test_charts_for_airport_no_match_returns_empty():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    assert dtpp.charts_for_airport(records, "KZZZ") == []


def test_charts_for_airport_does_not_cross_match_other_airports():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    charts = dtpp.charts_for_airport(records, "KBOS")
    assert len(charts) == 1
    assert charts[0].chart_name == "ILS OR LOC RWY 04R"


# --------------------------------------------------------------------------- #
# cache                                                                      #
# --------------------------------------------------------------------------- #
def test_fetch_and_cache_metafile_writes_and_skips_on_second_call(tmp_path):
    calls = []

    def fake_get(url):
        calls.append(url)
        return SAMPLE_METAFILE

    path = dtpp.fetch_and_cache_metafile(tmp_path, CYCLE, get=fake_get)
    assert path.exists()
    assert path.read_bytes() == SAMPLE_METAFILE
    assert len(calls) == 1

    dtpp.fetch_and_cache_metafile(tmp_path, CYCLE, get=fake_get)
    assert len(calls) == 1            # already cached -> no second fetch


def test_fetch_and_cache_metafile_force_redownloads_and_drops_the_index_cache(tmp_path):
    dtpp.fetch_and_cache_metafile(tmp_path, CYCLE, get=lambda url: SAMPLE_METAFILE)
    dtpp.load_index(tmp_path, CYCLE)                    # populates the pickle cache
    assert dtpp._index_cache_path(tmp_path, CYCLE).exists()

    dtpp.fetch_and_cache_metafile(tmp_path, CYCLE, get=lambda url: SAMPLE_METAFILE, force=True)
    assert not dtpp._index_cache_path(tmp_path, CYCLE).exists()


def test_load_index_none_when_nothing_cached(tmp_path):
    assert dtpp.load_index(tmp_path, CYCLE) is None


def test_load_index_parses_and_then_reads_the_pickle_cache(tmp_path):
    dtpp.fetch_and_cache_metafile(tmp_path, CYCLE, get=lambda url: SAMPLE_METAFILE)
    records = dtpp.load_index(tmp_path, CYCLE)
    assert len(records) == 4
    assert dtpp._index_cache_path(tmp_path, CYCLE).exists()

    # corrupt the raw XML - if load_index still returns the right data, it read the pickle
    dtpp._metafile_path(tmp_path, CYCLE).write_bytes(b"not xml")
    records2 = dtpp.load_index(tmp_path, CYCLE)
    assert len(records2) == 4


def test_fetch_and_cache_chart_writes_and_never_refetches(tmp_path):
    calls = []

    def fake_get(url):
        calls.append(url)
        return b"%PDF-1.4 fake plate"

    path = dtpp.fetch_and_cache_chart(tmp_path, CYCLE, "00775IL8.PDF", get=fake_get)
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
    assert len(calls) == 1

    dtpp.fetch_and_cache_chart(tmp_path, CYCLE, "00775IL8.PDF", get=fake_get)
    assert len(calls) == 1            # cycle-scoped plates never change -> no re-fetch


def test_fetch_and_cache_chart_propagates_data_unavailable(tmp_path):
    def boom(url):
        raise dtpp.DataUnavailable("404")
    with pytest.raises(dtpp.DataUnavailable):
        dtpp.fetch_and_cache_chart(tmp_path, CYCLE, "nope.pdf", get=boom)


# --------------------------------------------------------------------------- #
# chart selector resolution                                                 #
# --------------------------------------------------------------------------- #
def test_resolve_chart_by_index():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    charts = dtpp.charts_for_airport(records, "LNS")
    assert dtpp._resolve_chart(charts, "1") is charts[0]
    assert dtpp._resolve_chart(charts, "3") is charts[2]
    assert dtpp._resolve_chart(charts, "99") is None


def test_resolve_chart_by_name_substring_case_insensitive():
    records = dtpp.parse_metafile(SAMPLE_METAFILE)
    charts = dtpp.charts_for_airport(records, "LNS")
    hit = dtpp._resolve_chart(charts, "rnav")
    assert hit.chart_name == "RNAV (GPS) RWY 26"
    assert dtpp._resolve_chart(charts, "nonexistent") is None


# --------------------------------------------------------------------------- #
# open_with_os_default (just check it dispatches per platform, no real launch)
# --------------------------------------------------------------------------- #
def test_open_with_os_default_dispatches_by_platform(tmp_path, monkeypatch):
    target = tmp_path / "chart.pdf"
    target.write_bytes(b"%PDF-1.4")

    calls = []
    monkeypatch.setattr(dtpp.subprocess, "Popen", lambda args: calls.append(args))
    monkeypatch.setattr(dtpp.sys, "platform", "linux")
    dtpp.open_with_os_default(target)
    assert calls == [["xdg-open", str(target)]]

    calls.clear()
    monkeypatch.setattr(dtpp.sys, "platform", "darwin")
    dtpp.open_with_os_default(target)
    assert calls == [["open", str(target)]]


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #
def test_cli_update_index_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dtpp, "_http_get", lambda url, timeout=dtpp.HTTP_TIMEOUT: SAMPLE_METAFILE)
    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "update-index"])
    assert rc == 0
    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "list", "KLNS"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ILS OR LOC RWY 08" in out
    assert "[C]" in out


def test_cli_list_without_a_cached_index_is_a_clean_error(tmp_path, capsys):
    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "list", "KLNS"])
    assert rc == 2
    assert "update-index" in capsys.readouterr().err


def test_cli_fetch_and_open(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dtpp, "_http_get", lambda url, timeout=dtpp.HTTP_TIMEOUT:
                        SAMPLE_METAFILE if "xml_data" in url else b"%PDF-1.4 fake")
    dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "update-index"])

    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "fetch", "KLNS", "1"])
    assert rc == 0
    assert "00775IL8.PDF" in capsys.readouterr().out

    opened = []
    monkeypatch.setattr(dtpp, "open_with_os_default", lambda path: opened.append(path))
    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "open", "KLNS", "1"])
    assert rc == 0
    assert len(opened) == 1


def test_cli_fetch_unknown_selector_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dtpp, "_http_get", lambda url, timeout=dtpp.HTTP_TIMEOUT: SAMPLE_METAFILE)
    dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "update-index"])
    rc = dtpp.main(["--data-dir", str(tmp_path), "--cycle", "2609", "fetch", "KLNS", "nope"])
    assert rc == 1
