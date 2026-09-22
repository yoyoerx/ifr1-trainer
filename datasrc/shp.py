"""A minimal, stdlib-only ESRI Shapefile (.shp/.dbf) reader plus a Douglas-Peucker
simplifier, just enough to pull Class B/C/D airspace polygons out of the FAA's
``class_airspace_shape_files.zip`` (28 Day NASR Subscription) without adding a
GIS dependency (pyshp/shapely) to the trainer.

Format reference: ESRI Shapefile Technical Description (July 1998). Only shape
types 5 (Polygon) and 15 (PolygonZ) are handled - the only ones the FAA product
uses - and only the X/Y ring coordinates are read; Z/M arrays are skipped.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = ["read_dbf", "read_polygons", "simplify", "extract_class_airspace"]


# --------------------------------------------------------------------------- #
# .dbf (attribute table)
# --------------------------------------------------------------------------- #
def read_dbf(data: bytes) -> list[dict[str, str]]:
    """One dict per record, in file order (the same order as ``read_polygons``'s
    shapes - a shapefile's .shp and .dbf are parallel arrays, no join key)."""
    hdr_len, rec_len = struct.unpack("<2h", data[8:12])
    fields: list[tuple[str, int]] = []
    p = 32
    while data[p:p + 1] != b"\r":
        fd = data[p:p + 32]
        name = fd[0:11].split(b"\x00")[0].decode("latin1")
        length = fd[16]
        fields.append((name, length))
        p += 32
    n = (len(data) - hdr_len) // rec_len
    out = []
    for i in range(n):
        row = data[hdr_len + i * rec_len: hdr_len + (i + 1) * rec_len]
        rec, p = {}, 1                                   # byte 0 of each record is the deletion flag
        for name, length in fields:
            rec[name] = row[p:p + length].decode("latin1", "replace").strip()
            p += length
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# .shp (geometry) - Polygon (5) / PolygonZ (15) only
# --------------------------------------------------------------------------- #
def read_polygons(data: bytes) -> list[list[list[tuple[float, float]]]]:
    """One entry per record (parallel to ``read_dbf``): a list of rings, each
    ring a list of ``(lon, lat)`` points (outer + hole rings undifferentiated -
    the FAA airspace shapes are simple enough that this trainer only draws
    outlines, so winding direction is not needed)."""
    shapes: list[list[list[tuple[float, float]]]] = []
    pos = 100                                             # fixed-size file header
    n = len(data)
    while pos < n:
        content_len = struct.unpack(">i", data[pos + 4:pos + 8])[0] * 2
        content = data[pos + 8:pos + 8 + content_len]
        pos += 8 + content_len
        shape_type = struct.unpack("<i", content[0:4])[0]
        if shape_type == 0:                               # null shape
            shapes.append([])
            continue
        if shape_type not in (5, 15):
            raise ValueError(f"unsupported shape type {shape_type} (only Polygon/PolygonZ)")
        n_parts, n_points = struct.unpack("<2i", content[36:44])
        parts = struct.unpack(f"<{n_parts}i", content[44:44 + 4 * n_parts])
        pts_off = 44 + 4 * n_parts
        coords = struct.unpack(f"<{2 * n_points}d", content[pts_off:pts_off + 16 * n_points])
        points = list(zip(coords[0::2], coords[1::2]))
        bounds = list(parts) + [n_points]
        rings = [points[bounds[i]:bounds[i + 1]] for i in range(n_parts)]
        shapes.append(rings)
    return shapes


# --------------------------------------------------------------------------- #
# simplification (Douglas-Peucker) - the raw FAA boundaries run to thousands of
# vertices per ring (surveyed to a few metres); a moving-map display at 5-40 nm
# range range needs nowhere near that, so this cuts the cached/rendered size by
# ~95% with no visible shape change at trainer map scales.
# --------------------------------------------------------------------------- #
def _perp_dist(pt, a, b) -> float:
    (x, y), (ax, ay), (bx, by) = pt, a, b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5
    t = ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px, py = ax + t * dx, ay + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def simplify(ring: list[tuple[float, float]], epsilon_deg: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker, iterative (a naive recursive version blows the
    stack on rings with thousands of points)."""
    if len(ring) < 3:
        return list(ring)
    keep = bytearray(len(ring))
    keep[0] = keep[-1] = 1
    stack = [(0, len(ring) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = ring[i], ring[j]
        best_d, best_k = -1.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(ring[k], a, b)
            if d > best_d:
                best_d, best_k = d, k
        if best_d > epsilon_deg:
            keep[best_k] = 1
            stack.append((i, best_k))
            stack.append((best_k, j))
    return [pt for pt, k in zip(ring, keep) if k]


# --------------------------------------------------------------------------- #
# Class Airspace extraction
# --------------------------------------------------------------------------- #
@dataclass
class ClassAirspaceRecord:
    ident: str
    name: str
    cls: str                  # "B" | "C" | "D"
    floor_ft: int | None      # None = surface (LOWER_CODE "SFC")
    ceiling_ft: int | None
    rings: list[list[tuple[float, float]]]   # (lon, lat)


def _alt_ft(val: str, code: str) -> int | None:
    if code.strip().upper() == "SFC":
        return 0
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def extract_class_airspace(shp: bytes, dbf: bytes, *, classes=("B", "C", "D"),
                           epsilon_deg: float = 0.0008) -> list[ClassAirspaceRecord]:
    """Class B/C/D records from the FAA's Class Airspace shapefile, simplified
    for a moving map. ``epsilon_deg`` ~0.0008 deg (~90 m) is well under any
    trainer map range (5-40 nm) and cuts a ~3,000-point ring to a few dozen."""
    rows = read_dbf(dbf)
    shapes = read_polygons(shp)
    out = []
    for row, rings in zip(rows, shapes):
        cls = row.get("CLASS", "")
        if cls not in classes or not rings:
            continue
        simplified = [simplify(ring, epsilon_deg) for ring in rings]
        out.append(ClassAirspaceRecord(
            ident=row.get("IDENT", ""), name=row.get("NAME", ""), cls=cls,
            floor_ft=_alt_ft(row.get("LOWER_VAL", ""), row.get("LOWER_CODE", "")),
            ceiling_ft=_alt_ft(row.get("UPPER_VAL", ""), row.get("UPPER_CODE", "")),
            rings=[r for r in simplified if len(r) >= 3],
        ))
    return out
