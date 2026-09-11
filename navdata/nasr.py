"""Merge FAA NASR CSV data into a CIFP-built :class:`NavDatabase`.

CIFP carries no communication frequencies; the 28-Day NASR Subscription does
(``FRQ.csv``). NASR keys airports by FAA identifier (``JFK``) while CIFP uses the
ICAO identifier (``KJFK``), so ``APT_BASE.csv`` is read for the id crosswalk.

Only VHF voice frequencies (118.000-136.975 MHz) in a handful of useful roles are
kept; the rest of NASR (mag-var history, ARTCC sectors, class-B/C shelves, UHF,
military ops) is ignored for now.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .model import NavDatabase

__all__ = ["merge_comms", "load_frequencies", "faa_icao_crosswalk"]

# FREQ_USE (may be a space-joined combo) -> our category. Checked as substrings.
_USE_MAP = [
    ("ATIS", "ATIS"),
    ("LCL", "TWR"),
    ("GND", "GND"),
    ("CLNC", "CLNC"),
    ("CD", "CLNC"),
    ("APCH", "APP"),
    ("DEP", "DEP"),
    ("CTAF", "CTAF"),
    ("UNICOM", "UNICOM"),
]


def _rows(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def _vhf_voice(raw: str) -> float | None:
    """Parse a FREQ cell to a VHF voice MHz value, or ``None``.

    Handles plain ``"121.9"`` and paired ``"115.9/106X"`` (takes the MHz part).
    """
    head = raw.split("/", 1)[0].strip()
    try:
        mhz = float(head)
    except ValueError:
        return None
    return mhz if 118.0 <= mhz <= 136.975 else None


def _category(freq_use: str) -> str | None:
    u = freq_use.upper()
    for needle, cat in _USE_MAP:
        if needle in u:
            return cat
    return None


def faa_icao_crosswalk(data_dir: Path) -> dict[str, str]:
    """``{FAA id -> ICAO id}`` for airports that have an ICAO id in NASR."""
    out: dict[str, str] = {}
    for row in _rows(data_dir / "APT_BASE.csv"):
        icao = row.get("ICAO_ID", "").strip()
        faa = row.get("ARPT_ID", "").strip()
        if icao and faa:
            out[faa] = icao
    return out


def load_frequencies(data_dir: Path) -> dict[str, dict[str, list[float]]]:
    """``{FAA airport id -> {category -> [MHz, ...]}}`` from ``FRQ.csv``."""
    out: dict[str, dict[str, list[float]]] = {}
    for row in _rows(data_dir / "FRQ.csv"):
        apt = row.get("SERVICED_FACILITY", "").strip()
        if not apt:
            continue
        cat = _category(row.get("FREQ_USE", ""))
        if cat is None:
            continue
        mhz = _vhf_voice(row.get("FREQ", ""))
        if mhz is None:
            continue
        bucket = out.setdefault(apt, {}).setdefault(cat, [])
        if mhz not in bucket:
            bucket.append(mhz)
    return out


def merge_comms(db: NavDatabase, data_dir: Path) -> int:
    """Attach NASR comm frequencies to ``db.airports``. Returns airports updated.

    Raises ``FileNotFoundError`` if ``FRQ.csv`` / ``APT_BASE.csv`` are absent.
    """
    data_dir = Path(data_dir)
    freqs = load_frequencies(data_dir)
    icao_to_faa = {v: k for k, v in faa_icao_crosswalk(data_dir).items()}

    updated = 0
    for apt in db.airports.values():
        # small fields: CIFP ident == FAA id; towered: CIFP ident is ICAO
        by_faa = freqs.get(apt.ident) or freqs.get(icao_to_faa.get(apt.ident, ""))
        if by_faa:
            apt.comms = {k: list(v) for k, v in by_faa.items()}
            updated += 1
    return updated
