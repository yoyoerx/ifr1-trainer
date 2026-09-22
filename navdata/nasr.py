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

__all__ = ["merge_runways", "merge_comms", "load_frequencies", "faa_icao_crosswalk",
          "load_fss", "merge_fss", "merge_airspace_controlling"]

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


# NASR SURFACE_TYPE_CODE -> the GNS 530's words (Pilot's Guide p.93: Hard, Turf, Sealed, Gravel, Dirt, Soft, Unknown, Water);
# a composite code ("ASPH-TURF") takes its first component
_SURFACE = {"CONC": "Hard", "ASPH": "Hard", "PEM": "Hard", "MATS": "Hard", "PSP": "Hard", "METAL": "Hard",
            "ALUMINUM": "Hard", "TURF": "Turf", "GRASS": "Turf", "GRVL": "Gravel", "GRAVEL": "Gravel",
            "CALICHE": "Gravel", "DIRT": "Dirt", "SAND": "Dirt", "WATER": "Water", "TRTD": "Sealed"}
# RWY_LGT_CODE is a lighting *intensity*; the guide's Full Time / Part Time / Frequency schedule is not in NASR
_LIGHTS = {"HIGH": "High", "MED": "Medium", "LOW": "Low", "PERI": "Perimeter", "NSTD": "Non-std",
           "FLD": "Flood", "STRB": "Strobe"}


def merge_runways(db: NavDatabase, data_dir: Path) -> int:
    """Attach NASR runway surface and lighting (``APT_RWY.csv``) to ``db.airports``. Returns airports updated;
    a missing CSV (older caches) is not an error - the page then shows Unknown."""
    path = Path(data_dir) / "APT_RWY.csv"
    if not path.exists():
        return 0
    icao_to_faa = {v: k for k, v in faa_icao_crosswalk(data_dir).items()}
    by_faa: dict[str, dict[str, tuple[str, str]]] = {}
    for row in _rows(path):
        rid = row.get("RWY_ID", "").strip()
        apt = row.get("ARPT_ID", "").strip()
        if not rid or not apt:
            continue
        surf = _SURFACE.get(row.get("SURFACE_TYPE_CODE", "").strip().upper().split("-")[0].split("/")[0], "Unknown")
        by_faa.setdefault(apt, {})[rid] = (surf, _LIGHTS.get(row.get("RWY_LGT_CODE", "").strip().upper(), "Unknown"))
    updated = 0
    for apt in db.airports.values():
        info = by_faa.get(apt.ident) or by_faa.get(icao_to_faa.get(apt.ident, ""))
        if info:
            apt.rwy_info = dict(info)
            updated += 1
    return updated


def load_fss(data_dir: Path) -> list:
    """Flight Service remote comm outlets (F62): FRQ.csv FACILITY_TYPE 'RCO' rows give position + frequency,
    grouped by (controlling FSS, site name); FSS_BASE.csv gives the controlling FSS's on-air callsign. Raises
    ``FileNotFoundError`` if either CSV is absent."""
    from .model import Fss
    from navmath import Point

    voice_call: dict[str, str] = {}
    for row in _rows(Path(data_dir) / "FSS_BASE.csv"):
        fid = row.get("FSS_ID", "").strip()
        if fid:
            voice_call[fid] = row.get("VOICE_CALL", "").strip() or row.get("NAME", "").strip()

    sites: dict[tuple[str, str], dict] = {}
    for row in _rows(Path(data_dir) / "FRQ.csv"):
        if row.get("FACILITY_TYPE") != "RCO":
            continue
        fid = row.get("ARTCC_OR_FSS_ID", "").strip()
        name = (row.get("SERVICED_FAC_NAME") or row.get("FAC_NAME") or "").strip()
        if not fid or not name:
            continue
        mhz = _vhf_voice(row.get("FREQ", ""))
        if mhz is None:
            continue
        try:
            lat = float(row.get("LAT_DECIMAL", "") or "nan")
            lon = float(row.get("LONG_DECIMAL", "") or "nan")
        except ValueError:
            continue
        if lat != lat or lon != lon:                    # NaN check without importing math for one spot
            continue
        key = (fid, name)
        entry = sites.setdefault(key, {"pos": Point(lat, lon), "freqs": []})
        if mhz not in entry["freqs"]:
            entry["freqs"].append(mhz)

    return [
        Fss(fss_id=fid, voice_call=voice_call.get(fid, fid), ident=name, pos=v["pos"],
            freqs=tuple(sorted(v["freqs"])))
        for (fid, name), v in sites.items()
    ]


def merge_fss(db: NavDatabase, data_dir: Path) -> int:
    sites = load_fss(data_dir)
    db.fss = sites
    return len(sites)


def merge_airspace_controlling(db: NavDatabase, data_dir: Path) -> int:
    """The controlling agency for each Class B/C/D airspace (F64): Class B/C from FRQ.csv rows tagged
    FREQ_USE "CLASS B" / "CLASS C" (the TRACON's own sectorized frequencies, e.g. "POTOMAC TRACON"); Class D
    from the airport's own tower frequency (no "CLASS D" tag exists in FRQ.csv - a Class D's controlling agency
    *is* the airport's ATCT, already merged onto ``Airport.comms`` by ``merge_comms``, which must run first).
    Keyed the same way ``Airspace.ident`` is - the FAA airport id, not ICAO."""
    out: dict[tuple[str, str], dict] = {}
    for row in _rows(Path(data_dir) / "FRQ.csv"):
        use = row.get("FREQ_USE", "").strip()
        if use not in ("CLASS B", "CLASS C"):
            continue
        faa_id = row.get("SERVICED_FACILITY", "").strip()
        if not faa_id:
            continue
        mhz = _vhf_voice(row.get("FREQ", ""))
        if mhz is None:
            continue
        entry = out.setdefault((faa_id, use[-1]), {"name": row.get("FAC_NAME", "").strip(), "freqs": []})
        if mhz not in entry["freqs"]:
            entry["freqs"].append(mhz)

    for faa_id, icao in faa_icao_crosswalk(data_dir).items():
        apt = db.airports.get(icao)
        twr = apt.comms.get("TWR") if apt is not None else None
        if twr:
            out[(faa_id, "D")] = {"name": f"{apt.name or icao} TOWER", "freqs": list(twr)}

    db.airspace_controlling = {k: (v["name"], tuple(sorted(v["freqs"]))) for k, v in out.items()}
    return len(db.airspace_controlling)
