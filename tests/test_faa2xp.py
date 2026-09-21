"""faa2xp: CIFP -> X-Plane earth_*.dat rows, merge, and reversible install."""

from __future__ import annotations

from datetime import date

import pytest

from faa2xp import install as inst
from faa2xp.extract import extract
from faa2xp.merge import build_all, fmt_awy, fmt_fix, fmt_hold, fmt_nav

APT = "SUSAP KBOSK6ABOS     0     100YHN42214660W071002300W015000019         1800018000C    MNAR    GENERAL EDWARD LAWRENCE LOGAN 388731711"
ILS = "SUSAP KBOSK6IIBOS3   011030RW04RN42225597W0705948190347N42212182W0710024552058 10160367300W01505100010                     398211711"
LOC_ONLY = "SUSAP KBIHK2IIBIHA   010910RW17 N37222775W1182111871407                   3306-    0600   E0150                            337931707"
ACK = "SUSAD        ACK   K6011620VDHW N41165475W070013613    N41165479W070013616W0150000772     NARNANTUCKET                     249372609"
WPT = "SUSAP 0G7 K6CHADCI K60    W     N42394083W076452855                       W0115     NAR           HADCI                    812102504"
HOLD = "SUSAP 0G7 K6FR01   AHADCI 010HADCIK6PC0EE AR   HF                     00740040    + 03900     18000                 A JS   812191502"


def rows(*lines):
    return extract(list(lines))


def test_ils_row_matches_x_planes_own_packing():
    nav = rows(APT, ILS).nav
    loc, gs = nav
    # X-Plane's shipped file has 12619.675 / 300019.675 for this ILS (35*360 + true 19.7, 3.00 deg)
    assert (loc.type, loc.ident, loc.area, loc.region, loc.name) == (4, "IBOS", "KBOS", "K6", "04R ILS-cat-III")
    assert loc.freq == 11030 and loc.bearing == pytest.approx(12619.7)
    assert (gs.type, gs.name) == (6, "04R GS") and gs.bearing == pytest.approx(300019.7)
    assert loc.elev == 19


def test_localizer_without_glideslope_is_type_5():
    (loc,) = rows(LOC_ONLY).nav
    assert loc.type == 5 and loc.name == "17 LOC"


def test_vor_dme_gives_vor_and_dme_rows():
    a, b = rows(ACK).nav
    assert (a.type, b.type) == (3, 12)
    assert a.freq == 11620 and a.range_nm == 130 and a.bearing == -15.0
    assert a.name.endswith("VOR/DME")


def test_terminal_waypoint_carries_its_airport_and_hold_is_built_from_the_leg():
    ex = rows(WPT, HOLD)
    assert (ex.fix[0].ident, ex.fix[0].area, ex.fix[0].region) == ("HADCI", "0G7", "K6")
    h = ex.hold[0]
    assert (h.ident, h.area, h.type, h.turn, h.length_nm, h.min_alt) == ("HADCI", "0G7", 11, "R", 4.0, 3900)
    assert h.course == pytest.approx(7.4)


def test_line_formats_match_x_plane_columns():
    ex = rows(APT, ILS, WPT, HOLD)
    assert fmt_nav(ex.nav[0]) == " 4  42.382213889  -70.996719444       19    11030    18  12619.700 IBOS KBOS K6 04R ILS-cat-III"
    assert fmt_fix(ex.fix[0]) == " 42.661341667  -76.757930556  HADCI 0G7 K6 4530263 HADCI"
    assert fmt_hold(ex.hold[0]) == "HADCI K6  0G7 11      7.4      0.0      4.0 R     3900        0        0"


def test_airway_segments_and_format():
    # awy_rows directly; the ER record layout is exercised end to end by the real build
    from faa2xp.extract import awy_rows
    from navdata.model import AirwayPoint
    pts = [(AirwayPoint(10, "AAA", "K6", "E", min_alt_ft=18000, max_alt_ft=45000), ("E", "A")),
           (AirwayPoint(20, "BBB", "K6", "D"), ("D", " "))]
    (r,) = awy_rows("J90", pts)
    assert fmt_awy(r) == "  AAA K6 11   BBB K6  3 N 2 180 450 J90"


def _fake_xp(tmp_path, default_lines=True):
    xp = tmp_path / "XP"
    d = xp / "Resources" / "default data"
    d.mkdir(parents=True)
    hdr = "I\r\n1200 Version - data cycle 2406, build 1, metadata X.\r\n\r\n"
    (d / "earth_nav.dat").write_bytes((hdr +
        " 3  1.0  2.0  0 11000 130 0.0  ZZZ ENRT DN FOREIGN VOR\r\n"
        " 4  1.0  2.0  0 11030 18 1.0 IBOS KBOS K6 04R OLD\r\n"
        "12  1.0  2.0  0 11030 25 0.0 IBOS KBOS K6 LOGAN INTL DME-ILS\r\n99\r\n").encode())
    (d / "earth_fix.dat").write_bytes((hdr + " 1.0 2.0 OLDFX ENRT K6 1 OLDFX\r\n1.0 2.0 HADCI 0G7 K6 1 HADCI\r\n99\r\n").encode())
    (d / "earth_awy.dat").write_bytes((hdr + "AAA K6 11 BBB K6 11 N 1 1 2 V1\r\n99\r\n").encode())
    (d / "earth_hold.dat").write_bytes((hdr + "HADCI K6 0G7 11 1.0 1.0 0.0 R 0 0 0\r\n99\r\n").encode())
    return xp


def test_merge_replaces_same_entities_and_keeps_the_rest(tmp_path):
    xp = _fake_xp(tmp_path)
    ex = rows(APT, ILS, WPT, HOLD)
    stats = build_all(inst.default_dir(xp), ex, tmp_path / "out", "2609", date(2026, 9, 20))
    nav = (tmp_path / "out" / "earth_nav.dat").read_bytes().decode().split("\r\n")
    assert nav[0] == "I" and nav[2] == "" and nav[-2] == "99"
    text = "\n".join(nav)
    assert "FOREIGN VOR" in text                 # non-US row kept
    assert "04R OLD" not in text and "04R ILS-cat-III" in text   # same ILS replaced
    assert "DME-ILS" in text                     # ILS-DME kept (FAA data has none)
    assert "data cycle 2609" in nav[1]
    fix = (tmp_path / "out" / "earth_fix.dat").read_text()
    assert "OLDFX" in fix and fix.count("HADCI 0G7") == 1
    assert stats["earth_hold.dat"] == {"kept": 0, "dropped": 1, "added": 1}


def test_install_dry_run_apply_and_restore(tmp_path):
    xp = _fake_xp(tmp_path)
    cd = xp / "Custom Data"
    cd.mkdir()
    (cd / "earth_nav.dat").write_text("user's own file")
    src = tmp_path / "src"
    src.mkdir()
    files = {}
    for n in ("earth_nav.dat", "FAACIFP18"):
        (src / n).write_text("new " + n)
        files[n] = src / n
    plan = inst.install(xp, files, "2609", apply=False)
    assert plan[0].startswith("[dry run") and (cd / "earth_nav.dat").read_text() == "user's own file"
    inst.install(xp, files, "2609", apply=True)
    assert (cd / "FAACIFP18").read_text() == "new FAACIFP18"
    assert (cd / inst.BACKUP / "earth_nav.dat").read_text() == "user's own file"
    assert "2609" in inst.status(xp)
    with pytest.raises(inst.InstallError):
        inst.install(xp, files, "2609", apply=True)
    inst.restore(xp, apply=True)
    assert (cd / "earth_nav.dat").read_text() == "user's own file"
    assert not (cd / "FAACIFP18").exists() and not (cd / inst.MANIFEST).exists()
    assert inst.status(xp) == "not installed"


def test_restore_keeps_a_file_the_user_edited_after_install(tmp_path):
    xp = _fake_xp(tmp_path)
    (tmp_path / "f").write_text("v1")
    inst.install(xp, {"FAACIFP18": tmp_path / "f"}, "2609", apply=True)
    (xp / "Custom Data" / "FAACIFP18").write_text("edited")
    inst.restore(xp, apply=True)
    assert (xp / "Custom Data" / "FAACIFP18").read_text() == "edited"


def test_not_an_xplane_folder(tmp_path):
    with pytest.raises(inst.InstallError):
        inst.custom_dir(tmp_path)


def test_restamp_changes_only_the_cycle_in_the_version_line(tmp_path):
    from faa2xp.merge import restamp
    src = tmp_path / "msa.dat"
    src.write_bytes(b"I\r\n1150 Version - data cycle 2406, build 1, metadata MSAXP1150.\r\n\r\nrow \xe9 1\r\n99\r\n")
    restamp(src, tmp_path / "o.dat", "2609")
    assert (tmp_path / "o.dat").read_bytes() == \
        b"I\r\n1150 Version - data cycle 2609, build 1, metadata MSAXP1150.\r\n\r\nrow \xe9 1\r\n99\r\n"


def test_install_copies_a_tree_and_restore_removes_only_what_it_copied(tmp_path):
    xp = _fake_xp(tmp_path)
    tree = tmp_path / "CIFPsrc"
    (tree / "sub").mkdir(parents=True)
    (tree / "KBOS.dat").write_text("a")
    (tree / "sub" / "x.dat").write_text("b")
    (tmp_path / "f").write_text("v")
    plan = inst.install(xp, {"FAACIFP18": tmp_path / "f"}, "2609", apply=True, dirs={"CIFP": tree})
    cd = xp / "Custom Data"
    assert (cd / "CIFP" / "sub" / "x.dat").read_text() == "b" and "copytree" in plan[0]
    (cd / "CIFP" / "mine.dat").write_text("user file")
    inst.restore(xp, apply=True)
    assert not (cd / "CIFP" / "KBOS.dat").exists() and not (cd / "CIFP" / "sub").exists()
    assert (cd / "CIFP" / "mine.dat").read_text() == "user file"


def test_install_refuses_to_merge_into_an_existing_tree(tmp_path):
    xp = _fake_xp(tmp_path)
    (xp / "Custom Data" / "CIFP").mkdir(parents=True)
    (tmp_path / "f").write_text("v")
    with pytest.raises(inst.InstallError):
        inst.install(xp, {"FAACIFP18": tmp_path / "f"}, "2609", apply=True, dirs={"CIFP": tmp_path})
