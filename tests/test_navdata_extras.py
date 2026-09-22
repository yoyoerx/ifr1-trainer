"""navdata._load_artcc / _load_airspace: reading the cached artcc.json / airspace.json (F62)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import navdata  # noqa: E402
from navdata.model import NavDatabase  # noqa: E402


def test_load_artcc_reads_cached_json(tmp_path):
    (tmp_path / "artcc.json").write_text(json.dumps([
        {"artcc": "ZDC", "name": "BALTIMORE", "lat": 39.17, "lon": -76.66, "freqs": [[134.5, "LOW"]]},
    ]), encoding="utf-8")
    db = NavDatabase()
    navdata._load_artcc(db, tmp_path)
    assert len(db.centers) == 1
    c = db.centers[0]
    assert c.artcc == "ZDC" and c.ident == "BALTIMORE" and c.freqs == ((134.5, "LOW"),)
    assert not db.notes


def test_load_artcc_missing_file_notes_and_leaves_centers_empty(tmp_path):
    db = NavDatabase()
    navdata._load_artcc(db, tmp_path)
    assert db.centers == []
    assert any("artcc" in n for n in db.notes)


def test_load_airspace_reads_cached_json_lon_lat_order(tmp_path):
    (tmp_path / "airspace.json").write_text(json.dumps([
        {"ident": "FNT", "name": "FLINT CLASS C", "class": "C", "floor_ft": 0, "ceiling_ft": 4800,
         "rings": [[[-83.9, 43.0], [-83.9, 43.1], [-83.8, 43.1], [-83.8, 43.0]]]},
    ]), encoding="utf-8")
    db = NavDatabase()
    navdata._load_airspace(db, tmp_path)
    assert len(db.airspaces) == 1
    a = db.airspaces[0]
    assert a.ident == "FNT" and a.cls == "C" and a.ceiling_ft == 4800
    pt = a.rings[0][0]
    assert (pt.lat, pt.lon) == (43.0, -83.9)              # stored [lon, lat] in JSON, Point is (lat, lon)


def test_load_airspace_missing_file_notes_and_leaves_airspaces_empty(tmp_path):
    db = NavDatabase()
    navdata._load_airspace(db, tmp_path)
    assert db.airspaces == []
    assert any("airspace" in n.lower() for n in db.notes)


def test_load_artcc_corrupt_json_is_ignored(tmp_path):
    (tmp_path / "artcc.json").write_text("not json", encoding="utf-8")
    db = NavDatabase()
    navdata._load_artcc(db, tmp_path)   # no raise
    assert db.centers == []
