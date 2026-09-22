"""Unit tests for datasrc.shp (stdlib-only .shp/.dbf reader + simplifier)."""

import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc.shp import extract_class_airspace, read_dbf, read_polygons, simplify  # noqa: E402


def _dbf_bytes(fields: list[tuple[str, int]], rows: list[dict]) -> bytes:
    """A minimal, correctly-shaped .dbf: header + field descriptors + records."""
    hdr_len = 32 + 32 * len(fields) + 1
    rec_len = 1 + sum(l for _, l in fields)
    out = bytearray(32)
    struct.pack_into("<i", out, 4, len(rows))
    struct.pack_into("<2h", out, 8, hdr_len, rec_len)
    for name, length in fields:
        fd = bytearray(32)
        nb = name.encode("ascii")
        fd[0:len(nb)] = nb
        fd[11:12] = b"C"
        fd[16] = length
        out += fd
    out += b"\r"
    for row in rows:
        out += b" "
        for name, length in fields:
            val = str(row.get(name, "")).encode("ascii")[:length]
            out += val.ljust(length)
    return bytes(out)


def _shp_polygon_bytes(rings: list[list[tuple[float, float]]]) -> bytes:
    """One PolygonZ record (shape type 15, Z array all zero) - the FAA file's own shape type."""
    points = [pt for ring in rings for pt in ring]
    n_points = len(points)
    parts = []
    off = 0
    for ring in rings:
        parts.append(off)
        off += len(ring)
    content = struct.pack("<i", 15)
    content += struct.pack("<4d", 0.0, 0.0, 0.0, 0.0)
    content += struct.pack("<2i", len(rings), n_points)
    content += struct.pack(f"<{len(parts)}i", *parts)
    for x, y in points:
        content += struct.pack("<2d", x, y)
    content += struct.pack("<2d", 0.0, 0.0)          # Zmin, Zmax
    content += struct.pack(f"<{n_points}d", *([0.0] * n_points))   # Z array
    hdr = struct.pack(">2i", 1, len(content) // 2)
    file_hdr = bytearray(100)
    struct.pack_into(">i", file_hdr, 0, 9994)
    struct.pack_into(">i", file_hdr, 24, (100 + len(hdr) + len(content)) // 2)
    struct.pack_into("<i", file_hdr, 28, 1000)
    struct.pack_into("<i", file_hdr, 32, 15)
    return bytes(file_hdr) + hdr + content


def test_read_dbf_round_trips_fields():
    fields = [("IDENT", 6), ("CLASS", 1)]
    data = _dbf_bytes(fields, [{"IDENT": "FNT", "CLASS": "C"}, {"IDENT": "IAD", "CLASS": "B"}])
    rows = read_dbf(data)
    assert rows == [{"IDENT": "FNT", "CLASS": "C"}, {"IDENT": "IAD", "CLASS": "B"}]


def test_read_polygons_one_square_ring():
    square = [(-77.0, 39.0), (-77.0, 39.5), (-76.5, 39.5), (-76.5, 39.0), (-77.0, 39.0)]
    shp = _shp_polygon_bytes([square])
    shapes = read_polygons(shp)
    assert len(shapes) == 1 and len(shapes[0]) == 1
    assert shapes[0][0] == square


def test_read_polygons_multiple_records_and_rings():
    a = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    b = [(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (2.0, 2.0)]
    shp = _shp_polygon_bytes([a]) + _shp_polygon_bytes([a, b])[100:]   # append a 2nd record (skip its file header)
    shapes = read_polygons(shp)
    assert len(shapes) == 2
    assert len(shapes[0]) == 1 and len(shapes[1]) == 2


def test_simplify_keeps_endpoints_and_drops_near_collinear_points():
    ring = [(0.0, 0.0), (0.5, 0.0001), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    out = simplify(ring, epsilon_deg=0.01)
    assert out[0] == ring[0] and out[-1] == ring[-1]
    assert len(out) < len(ring)                       # the near-collinear midpoint is dropped
    assert (0.5, 0.0001) not in out


def test_simplify_keeps_a_real_corner():
    ring = [(0.0, 0.0), (0.5, 5.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    out = simplify(ring, epsilon_deg=0.01)
    assert (0.5, 5.0) in out                           # a genuine corner survives a small epsilon


def test_extract_class_airspace_filters_class_and_reads_altitudes():
    fields = [("IDENT", 6), ("NAME", 20), ("CLASS", 1), ("LOWER_VAL", 6), ("LOWER_CODE", 3),
              ("UPPER_VAL", 6), ("UPPER_CODE", 3)]
    rows = [
        {"IDENT": "FNT", "NAME": "FLINT CLASS C", "CLASS": "C", "LOWER_VAL": "0", "LOWER_CODE": "SFC",
         "UPPER_VAL": "4800", "UPPER_CODE": "MSL"},
        {"IDENT": "XXX", "NAME": "SOME CLASS E", "CLASS": "E", "LOWER_VAL": "700", "LOWER_CODE": "AGL",
         "UPPER_VAL": "17999", "UPPER_CODE": "MSL"},
    ]
    dbf = _dbf_bytes(fields, rows)
    square = [(-84.0, 43.0), (-84.0, 43.1), (-83.9, 43.1), (-83.9, 43.0), (-84.0, 43.0)]
    shp = _shp_polygon_bytes([square]) + _shp_polygon_bytes([square])[100:]
    out = extract_class_airspace(shp, dbf)
    assert len(out) == 1 and out[0].ident == "FNT" and out[0].cls == "C"
    assert out[0].floor_ft == 0 and out[0].ceiling_ft == 4800
    assert len(out[0].rings) == 1 and len(out[0].rings[0]) >= 4
