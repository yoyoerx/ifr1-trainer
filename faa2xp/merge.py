"""Format records as earth_*.dat rows and merge them over X-Plane's default files.

Merge rule: a base row is *replaced* when the FAA data supplies the same entity
(same ident + area + region, per file); every other row (non-US data, markers,
LPV/GLS path points, and US entities the FAA CIFP does not carry) is kept
verbatim. Trade-off: an entity decommissioned since the base cycle survives
until the base file itself drops it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .extract import AwyRow, Extracted, FixRow, HoldRow, NavRow

# nav row types we regenerate from the FAA data; everything else in the base is kept
REPLACED_NAV_TYPES = {"2", "3", "4", "5", "6", "12", "13"}

_HEADERS = {
    "nav": ("1200", "NavXP1200"),
    "fix": ("1200", "FixXP1200"),
    "awy": ("1100", "AwyXP1100"),
    "hold": ("1140", "HoldXP1140"),
}


def header(kind: str, cycle: str, built: date) -> list[str]:
    ver, meta = _HEADERS[kind]
    return ["I", f"{ver} Version - data cycle {cycle}, build {built:%Y%m%d}, metadata {meta}. "
                 f"Source: FAA CIFP/NASR (public domain) via faa2xp; not for real-world navigation.", ""]


def fmt_nav(r: NavRow) -> str:
    return (f"{r.type:2d} {r.lat:13.9f} {r.lon:14.9f} {r.elev:8d} {r.freq:8d} {r.range_nm:5d} "
            f"{r.bearing:10.3f} {r.ident:>4} {r.area} {r.region} {r.name}")


def fmt_fix(r: FixRow) -> str:
    return f"{r.lat:13.9f} {r.lon:14.9f} {r.ident:>6} {r.area} {r.region} {r.code} {r.name}"


def fmt_awy(r: AwyRow) -> str:
    return (f"{r.a_ident:>5} {r.a_region:>2} {r.a_type:2d} {r.b_ident:>5} {r.b_region:>2} {r.b_type:2d} "
            f"{r.direction} {r.level} {r.base_fl:3d} {r.top_fl:3d} {r.name}")


def fmt_hold(r: HoldRow) -> str:
    return (f"{r.ident:<5} {r.region} {r.area:>4} {r.type:2d} {r.course:8.1f} {r.time_min:8.1f} "
            f"{r.length_nm:8.1f} {r.turn} {r.min_alt:8d} {r.max_alt:8d} {r.speed:8d}")


def _read_body(path: Path) -> list[str]:
    """A data file's records: header (up to the first blank line) and the ``99`` trailer dropped."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        start = lines.index("") + 1
    except ValueError:
        start = 3
    body = lines[start:]
    if body and body[-1].strip() == "99":
        body.pop()
    return body


def _write(path: Path, head: list[str], body: list[str], trailer: bool) -> None:
    text = "\r\n".join(head + body + (["99"] if trailer else [])) + "\r\n"
    path.write_bytes(text.encode("utf-8"))


def _nav_key(t: str, ident: str, area: str, region: str) -> tuple:
    return ("loc" if t in ("4", "5") else t, ident, area, region)


def merge_nav(base: Path, ex: Extracted, out: Path, cycle: str, built: date) -> dict:
    supplied = {_nav_key(str(r.type), r.ident, r.area, r.region) for r in ex.nav}
    kept, dropped = [], 0
    for ln in _read_body(base):
        p = ln.split()
        if (len(p) < 10 or p[0] not in REPLACED_NAV_TYPES or ln.rstrip().endswith("DME-ILS")
                or _nav_key(p[0], p[7], p[8], p[9]) not in supplied):
            kept.append(ln)   # includes ILS-DME rows: the FAA data has no ILS DME records
        else:
            dropped += 1
    body = kept + [fmt_nav(r) for r in ex.nav]
    _write(out, header("nav", cycle, built), body, trailer=True)
    return {"kept": len(kept), "dropped": dropped, "added": len(ex.nav)}


def merge_fix(base: Path, ex: Extracted, out: Path, cycle: str, built: date) -> dict:
    supplied = {(r.ident, r.area, r.region) for r in ex.fix}
    kept, dropped = [], 0
    for ln in _read_body(base):
        p = ln.split()
        if len(p) >= 5 and (p[2], p[3], p[4]) in supplied:
            dropped += 1
        else:
            kept.append(ln)
    _write(out, header("fix", cycle, built), kept + [fmt_fix(r) for r in ex.fix], trailer=True)
    return {"kept": len(kept), "dropped": dropped, "added": len(ex.fix)}


def merge_awy(base: Path, ex: Extracted, out: Path, cycle: str, built: date) -> dict:
    regions = {r.a_region for r in ex.awy} | {r.b_region for r in ex.awy}
    names = {r.name for r in ex.awy}
    kept, dropped = [], 0
    for ln in _read_body(base):
        p = ln.split()
        # replace a base segment when its whole route is US-region and re-supplied by the FAA data
        if len(p) >= 11 and p[1] in regions and p[4] in regions and any(n in names for n in p[10].split("-")):
            dropped += 1
        else:
            kept.append(ln)
    _write(out, header("awy", cycle, built), kept + [fmt_awy(r) for r in ex.awy], trailer=True)
    return {"kept": len(kept), "dropped": dropped, "added": len(ex.awy)}


def merge_hold(base: Path, ex: Extracted, out: Path, cycle: str, built: date) -> dict:
    supplied = {(r.ident, r.region, r.area) for r in ex.hold}
    kept, dropped = [], 0
    for ln in _read_body(base):
        p = ln.split()
        if len(p) >= 3 and (p[0], p[1], p[2]) in supplied:
            dropped += 1
        else:
            kept.append(ln)
    _write(out, header("hold", cycle, built), kept + [fmt_hold(r) for r in ex.hold], trailer=True)
    return {"kept": len(kept), "dropped": dropped, "added": len(ex.hold)}


def restamp(base: Path, out: Path, cycle: str) -> None:
    """Copy a data file unchanged except the ``data cycle NNNN`` in its version line."""
    raw = base.read_bytes()                       # bytes: the body must stay byte-identical
    first, nl1, rest = raw.partition(b"\n")       # "I"
    ver, nl2, body = rest.partition(b"\n")        # version line
    ver = re.sub(rb"data cycle \d+", f"data cycle {cycle}".encode(), ver, count=1)
    out.write_bytes(first + nl1 + ver + nl2 + body)


FILES = {
    "earth_nav.dat": merge_nav,
    "earth_fix.dat": merge_fix,
    "earth_awy.dat": merge_awy,
    "earth_hold.dat": merge_hold,
}


def build_all(default_dir: Path, ex: Extracted, out_dir: Path, cycle: str, built: date | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    built = built or date.today()
    return {name: fn(default_dir / name, ex, out_dir / name, cycle, built) for name, fn in FILES.items()}
