"""Decode-logic tests for ifr1 (no hardware).

These pin the canonical-frame layout so the report-id / off-by-one handling
can't silently regress. On-device confirmation of the byte offsets is still a
separate manual step (`python ifr1.py raw`).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ifr1 import Event, IFR1, LAYOUT, Mode, State, normalize_frame  # noqa: E402

RID = 0x0B


def frame(bytes_by_index: dict[int, int]) -> list[int]:
    f = [0] * 8
    f[0] = RID
    for i, v in bytes_by_index.items():
        f[i] = v
    return f


# --------------------------------------------------------------------------- #
# normalize_frame                                                            #
# --------------------------------------------------------------------------- #
def test_normalize_keeps_frame_with_report_id():
    assert normalize_frame([RID, 1, 2, 3, 4, 5, 6, 7]) == [RID, 1, 2, 3, 4, 5, 6, 7]


def test_normalize_prepends_missing_report_id():
    assert normalize_frame([1, 2, 3, 4, 5, 6, 7]) == [RID, 1, 2, 3, 4, 5, 6, 7]


def test_normalize_pads_short_frame():
    assert normalize_frame([RID, 9, 9]) == [RID, 9, 9, 0, 0, 0, 0, 0]


def test_normalize_empty_is_none():
    assert normalize_frame([]) is None


# --------------------------------------------------------------------------- #
# Mode                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, mode",
    [(0, Mode.COM1), (4, Mode.FMS1), (7, Mode.XPDR), (0x74, Mode.FMS1), (9, Mode.COM1)],
)
def test_mode_from_raw(raw, mode):
    assert Mode.from_raw(raw) == mode


# --------------------------------------------------------------------------- #
# State.decode -- offsets are relative to the report-id byte                 #
# --------------------------------------------------------------------------- #
def test_decode_mode_is_frame_byte_7():
    assert State.decode(frame({7: 4})).mode == Mode.FMS1
    assert State.decode(frame({7: 1})).mode == Mode.COM2


def test_decode_encoders_are_bytes_5_and_6_signed():
    st = State.decode(frame({5: 0x05, 6: 0xFB}))
    assert st.outer == 5
    assert st.inner == -5


def test_decode_buttons_bytes_1_2_3():
    st = State.decode(frame({1: 0x10 | 0x80, 3: 0x0C}))
    assert st.buttons == {"DCT", "ENT", "ALT", "VS"}
    assert not st.shift


def test_decode_swap_sets_shift():
    st = State.decode(frame({2: 0x01}))
    assert "SWAP" in st.buttons and st.shift is True


def test_decode_flags_unmapped_bits_in_byte_4():
    st = State.decode(frame({4: 0x21}))
    assert (4, 0x21) in st.unknown_bits


def test_decode_no_false_unknown_on_known_bits():
    # every mapped button bit set -> nothing "unknown"
    f = frame({1: 0xF0, 2: 0xC3, 3: 0x0F})
    assert State.decode(f).unknown_bits == ()


def test_decode_raw_is_first_eight_bytes():
    st = State.decode(normalize_frame([RID, 1, 2, 3, 4, 5, 6, 7, 99, 99]))
    assert st.raw == (RID, 1, 2, 3, 4, 5, 6, 7)


def test_layout_defaults_match_flywithlua_frame():
    assert (LAYOUT.outer, LAYOUT.inner, LAYOUT.mode) == (5, 6, 7)
    assert LAYOUT.buttons["DCT"] == (1, 0x10)
    assert LAYOUT.buttons["NAV"] == (3, 0x01)


# --------------------------------------------------------------------------- #
# Event diffing                                                              #
# --------------------------------------------------------------------------- #
def _dev() -> IFR1:
    return IFR1()  # not opened; _diff only touches _prev


def test_first_diff_reports_mode_changed():
    ev = _dev()._diff(State(mode=Mode.FMS1))
    assert ev.mode_changed and ev.mode == Mode.FMS1


def test_diff_button_edges_and_encoder_accumulation():
    d = _dev()
    d._prev = State(mode=Mode.FMS1, buttons=frozenset({"DCT"}))
    ev = d._diff(State(mode=Mode.FMS1, buttons=frozenset({"ENT"}), outer=3, inner=-1))
    assert ev.pressed == ("ENT",)
    assert ev.released == ("DCT",)
    assert ev.outer == 3 and ev.inner == -1
    assert not ev.mode_changed


def test_diff_empty_when_nothing_changed():
    d = _dev()
    s = State(mode=Mode.NAV1)
    d._prev = s
    assert d._diff(State(mode=Mode.NAV1)).is_empty()


def test_event_is_empty_helper():
    assert Event(mode=Mode.COM1).is_empty()
    assert not Event(mode=Mode.COM1, outer=1).is_empty()
    assert not Event(mode=Mode.COM1, long_press=("CLR",)).is_empty()


# --------------------------------------------------------------------------- #
# long-press (CLR-hold / COM SWAP-hold): timed off the wall clock since a     #
# sustained press with nothing else changing produces no further reports.    #
# --------------------------------------------------------------------------- #
def test_diff_records_press_time_and_clears_on_release():
    from ifr1 import LONG_PRESS_S

    d = _dev()
    d._diff(State(mode=Mode.FMS1, buttons=frozenset({"CLR"})), now=10.0)
    assert d._press_time["CLR"] == 10.0
    d._prev = State(mode=Mode.FMS1, buttons=frozenset({"CLR"}))
    assert d._check_holds(10.0 + LONG_PRESS_S - 0.01) == ()
    assert d._check_holds(10.0 + LONG_PRESS_S + 0.001) == ("CLR",)
    assert d._check_holds(10.0 + LONG_PRESS_S + 5.0) == ()      # fires once
    d._diff(State(mode=Mode.FMS1, buttons=frozenset()), now=20.0)   # released
    assert "CLR" not in d._press_time and "CLR" not in d._long_fired


def test_check_holds_ignores_a_button_pressed_too_recently():
    d = _dev()
    d._diff(State(mode=Mode.FMS1, buttons=frozenset({"CLR"})), now=100.0)
    d._prev = State(mode=Mode.FMS1, buttons=frozenset({"CLR"}))
    assert d._check_holds(100.2) == ()


def test_poll_emits_a_long_press_event_with_no_new_frame(monkeypatch):
    from ifr1 import LONG_PRESS_S

    d = _dev()
    monkeypatch.setattr("ifr1.time.monotonic", lambda: 100.0 + LONG_PRESS_S + 0.001)
    d._diff(State(mode=Mode.FMS1, buttons=frozenset({"CLR"})), now=100.0)
    d._prev = State(mode=Mode.FMS1, buttons=frozenset({"CLR"}))
    monkeypatch.setattr(d, "read_state", lambda: None)   # no new frame pending
    events = d.poll()
    assert len(events) == 1 and events[0].long_press == ("CLR",)
    assert events[0].mode == Mode.FMS1


# --------------------------------------------------------------------------- #
# real frames captured from an IFR-1 on Windows (2026-09-09) -- byte [0] is    #
# the report id, so LAYOUT offsets are used verbatim. Regression guard.        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, mode, outer, inner, buttons",
    [
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x00], Mode.COM1, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x02], Mode.NAV1, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x04], Mode.FMS1, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x06], Mode.AP, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x01], Mode.COM2, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x05], Mode.FMS2, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0x01, 0x07], Mode.XPDR, 0, 1, set()),
        ([0x0B, 0, 0, 0, 0, 0, 0xFF, 0x07], Mode.XPDR, 0, -1, set()),
        ([0x0B, 0, 0, 0, 0, 0x01, 0, 0x07], Mode.XPDR, 1, 0, set()),
        ([0x0B, 0, 0, 0, 0, 0xFF, 0, 0x07], Mode.XPDR, -1, 0, set()),
        ([0x0B, 0, 0x02, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"KNOB"}),
        ([0x0B, 0x10, 0, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"DCT"}),
        ([0x0B, 0x20, 0, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"MNU"}),
        ([0x0B, 0x40, 0, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"CLR"}),
        ([0x0B, 0x80, 0, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"ENT"}),
        ([0x0B, 0, 0x40, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"AP"}),
        ([0x0B, 0, 0x80, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"HDG"}),
        ([0x0B, 0, 0, 0x01, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"NAV"}),
        ([0x0B, 0, 0, 0x02, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"APR"}),
        ([0x0B, 0, 0, 0x04, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"ALT"}),
        ([0x0B, 0, 0, 0x08, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"VS"}),
        ([0x0B, 0, 0x01, 0, 0, 0, 0, 0x07], Mode.XPDR, 0, 0, {"SWAP"}),
    ],
)
def test_real_captured_frames(raw, mode, outer, inner, buttons):
    st = State.decode(normalize_frame(raw))
    assert st.mode == mode
    assert st.outer == outer
    assert st.inner == inner
    assert st.buttons == buttons
    assert st.unknown_bits == ()          # byte [4] stayed 0 for every control
    assert st.shift == ("SWAP" in buttons)
