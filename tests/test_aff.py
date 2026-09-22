"""Unit tests for datasrc.aff (AFF.txt RCAG site parser, F62)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc.aff import parse_aff  # noqa: E402

# Real-shaped excerpts (column layout preserved) from a live 28 Day NASR AFF.txt.
_AFF1_RCAG = ('AFF1ZAB ALBUQUERQUE                             BUCKEYE                                    '
             '                                     RCAG 09/03/2026ARIZONA                       AZ33-27-12'
             '.600N 120432.600N112-49-28.600W406168.600WKZAB                         ')
_AFF1_ARTCC = ('AFF1ZAB ALBUQUERQUE                             ALBUQUERQUE                   FACILITY LOCA'
              'TED AT ALBUQUERQUE, NM               ARTCC09/03/2026NEW MEXICO                    NM35-10-2'
              '4.150N 126624.150N106-34-03.080W383643.080WKZAB                         ')
_AFF3_LOW = ('AFF3ZAB AMARILLO                      RCAG 127.85  LOW                       YAMA TEXAS       '
            '                  TXAMARILLO                                RICK HUSBAND AMARILLO INTL         '
            '               35-13-09.700N 126789.700N101-42-21.300W366141.300W')
_AFF1_AMARILLO = ('AFF1ZAB ALBUQUERQUE                             AMARILLO                                 '
                  '                                      RCAG 09/03/2026TEXAS                         TX35-'
                  '13-30.190N 126810.190N102-02-15.690W367335.690WKZAB                         ')


def test_parses_an_rcag_site_position_and_skips_the_plain_artcc_row():
    sites = parse_aff("\n".join([_AFF1_RCAG, _AFF1_ARTCC]))
    assert len(sites) == 1
    s = sites[0]
    assert s.artcc == "ZAB" and s.name == "BUCKEYE"
    assert s.lat == pytest_approx(33 + 27 / 60 + 12.6 / 3600)
    assert s.lon == pytest_approx(-(112 + 49 / 60 + 28.6 / 3600))
    assert s.freqs == []


def test_matches_a_frequency_line_to_its_site_by_name_and_dedupes():
    text = "\n".join([_AFF1_AMARILLO, _AFF3_LOW, _AFF3_LOW])
    sites = parse_aff(text)
    assert len(sites) == 1
    s = sites[0]
    assert s.name == "AMARILLO" and s.freqs == [(127.85, "LOW")]


def test_a_frequency_line_with_no_matching_site_is_ignored():
    sites = parse_aff(_AFF3_LOW)                       # no AFF1 line at all
    assert sites == []


def test_unrecognized_lines_are_ignored():
    sites = parse_aff("garbage\nAFF9 not a real record type\n")
    assert sites == []


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=1e-6)
