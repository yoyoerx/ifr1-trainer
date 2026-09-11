"""ARINC 424-18 low-level field parsers, as used by the FAA CIFP (``FAACIFP18``).

Pure functions, stdlib only. Each record in the file is a fixed 132-character
line; these helpers turn individual fixed-width fields into Python values. Column
offsets for whole record types live in ``cifp.py`` -- this module only knows the
field *formats*.

The formats implemented here (coordinates, magnetic variation, VHF/NDB
frequency) are stable across ARINC 424 versions; they are the pieces most likely
to be got subtly wrong, so they are unit-tested against real CIFP strings.
"""

from __future__ import annotations

from navmath import Point

__all__ = [
    "parse_lat",
    "parse_lon",
    "parse_latlon",
    "parse_magvar",
    "parse_vhf_freq_mhz",
    "parse_ndb_freq_khz",
    "opt_int",
]


def parse_lat(field: str) -> float:
    """``"N40375838"`` -> 40.632883... degrees (N +, S -).

    Format: hemisphere + DD + MM + SS + hundredths-of-a-second (9 chars).
    """
    field = field.strip()
    if len(field) != 9 or field[0] not in "NS":
        raise ValueError(f"bad ARINC latitude: {field!r}")
    deg = int(field[1:3])
    minutes = int(field[3:5])
    seconds = int(field[5:9]) / 100.0
    value = deg + minutes / 60.0 + seconds / 3600.0
    return -value if field[0] == "S" else value


def parse_lon(field: str) -> float:
    """``"W073461701"`` -> -73.771391... degrees (E +, W -).

    Format: hemisphere + DDD + MM + SS + hundredths-of-a-second (10 chars).
    """
    field = field.strip()
    if len(field) != 10 or field[0] not in "EW":
        raise ValueError(f"bad ARINC longitude: {field!r}")
    deg = int(field[1:4])
    minutes = int(field[4:6])
    seconds = int(field[6:10]) / 100.0
    value = deg + minutes / 60.0 + seconds / 3600.0
    return -value if field[0] == "W" else value


def parse_latlon(lat_field: str, lon_field: str) -> Point:
    return Point(parse_lat(lat_field), parse_lon(lon_field))


def parse_magvar(field: str) -> float:
    """``"W0120"`` -> -12.0, ``"E0141"`` -> +14.1, ``"T0000"`` -> 0.0.

    Returned **east positive**, so ``true = magnetic + magvar``. A leading ``T``
    marks a station oriented to true north (no variation). Blank -> 0.0.
    """
    field = (field or "").strip()
    if not field:
        return 0.0
    sign, digits = field[0], field[1:]
    if not digits.isdigit():
        raise ValueError(f"bad ARINC magvar: {field!r}")
    tenths = int(digits) / 10.0
    if sign in ("T", "E"):
        return tenths
    if sign == "W":
        return -tenths
    raise ValueError(f"bad ARINC magvar sign: {field!r}")


def parse_vhf_freq_mhz(field: str) -> float:
    """VHF NAVAID frequency field, e.g. ``"11590"`` -> 115.90 MHz.

    Five digits, hundredths of a MHz.
    """
    field = field.strip()
    if not field.isdigit():
        raise ValueError(f"bad VHF frequency: {field!r}")
    return int(field) / 100.0


def parse_ndb_freq_khz(field: str) -> float:
    """NDB frequency field, e.g. ``"03650"`` -> 365.0 kHz (tenths of a kHz)."""
    field = field.strip()
    if not field.isdigit():
        raise ValueError(f"bad NDB frequency: {field!r}")
    return int(field) / 10.0


def opt_int(field: str) -> int | None:
    """Int from a fixed-width field, or ``None`` if blank."""
    field = (field or "").strip()
    if not field:
        return None
    return int(field)
