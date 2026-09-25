"""render_commands.py is the Android/Chaquopy draw-command-list producer
(§3.6, docs/ANDROID_PORT_PLAN.md) - pure stdlib + math, no pygame, exercised
here without any Android/Kotlin involved, exactly like every other pure
module in this repo.
"""

from instruments import CDI, DME, BearingPointer, Markers, NavHead, Panel
from render_commands import hsi_commands


def _panel(hdg=45.0):
    return Panel(
        cdi=CDI(valid=True, deflection=0.3, to_from="TO", full_scale_deg=2.0, source="GPS"),
        hsi_heading_deg=hdg,
        bearing1=BearingPointer(valid=False), bearing2=BearingPointer(valid=False),
        dme=DME(), markers=Markers(),
    )


def test_hsi_commands_returns_nonempty_delimited_string():
    nav1 = NavHead(valid=True, ident="OOO", course_deg=90.0, deflection=0.4, to_from="TO")
    s = hsi_commands(10, 10, 300, 300, nav1, _panel(), 1.0)
    assert s, "expected a non-empty command string"
    lines = s.splitlines()
    assert len(lines) > 10
    min_fields = {"T": 7, "L": 6, "R": 6, "C": 5, "P": 3}
    for line in lines:
        op, *fields = line.split("|")
        assert op in min_fields
        assert len(fields) >= min_fields[op]


def test_hsi_commands_handles_no_receiver():
    """nav1=None (no VOR/LOC tuned) must not raise - render.py's draw_hsi_head
    draws the card/heading readout regardless, no receiver info column."""
    s = hsi_commands(0, 0, 200, 200, None, _panel(), 0.0)
    assert s
    assert "VOR" not in s and "LOC" not in s


def test_hsi_commands_heading_readout_reflects_panel():
    s = hsi_commands(0, 0, 200, 200, None, _panel(hdg=270.0), 0.0)
    assert "|270" in s
