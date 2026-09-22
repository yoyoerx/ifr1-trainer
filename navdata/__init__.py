"""Nav database: navaids, waypoints, airports, runways, procedures, airways.

The FAA's cached CIFP (see ``datasrc``) is the sole source, with NASR CSV
comm frequencies merged in when present -- no X-Plane data, no other
provider. Public surface::

    import navdata
    db = navdata.load()                     # newest cached FAA cycle (+ NASR comms)
    db = navdata.load(cycle="2609")         # a specific cycle
    jfk = db.find("JFK"); appr = db.approaches("KJFK")

All model types and ``navmath.Point`` are re-exported for convenience.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

from navmath import Point

from .cifp import parse_cifp
from .model import (
    Airport,
    Airway,
    AirwayPoint,
    LegType,
    NavDatabase,
    NdbNavaid,
    PathPoint,
    Procedure,
    ProcedureLeg,
    Runway,
    VhfNavaid,
    Waypoint,
)

__all__ = [
    "Point",
    "Waypoint",
    "VhfNavaid",
    "NdbNavaid",
    "Runway",
    "Airport",
    "LegType",
    "ProcedureLeg",
    "Procedure",
    "AirwayPoint",
    "Airway",
    "NavDatabase",
    "parse_cifp",
    "load",
]

CIFP_FILENAME = "FAACIFP18"
# bump when the model / parser output shape changes so stale pickles are ignored
_CACHE_SCHEMA = 5


def _cache_path(cdir: Path, cifp_path: Path, *, areas, comms: bool) -> Path:
    tag = hashlib.sha1(
        f"{_CACHE_SCHEMA}|{cifp_path.stat().st_size}|{cifp_path.stat().st_mtime_ns}"
        f"|comms={comms}|areas={sorted(areas) if areas else None}".encode()
    ).hexdigest()[:16]
    return cdir / f"navdb-{tag}.pkl"


def load(
    *,
    data_dir: str | Path | None = None,
    cycle: str | None = None,
    areas: set[str] | None = None,
    comms: bool = True,
    cache: bool = True,
) -> NavDatabase:
    """Load the nav database from the cached FAA CIFP -- the only source.

    ``data_dir`` -- FAA cache root (defaults to ``datasrc``'s ``data/``).
    ``cycle``    -- AIRAC ident to load; defaults to the newest cached.
    ``areas``    -- restrict to these CIFP area codes (e.g. ``{"USA"}``).
    ``comms``    -- merge NASR comm frequencies if the CSVs are cached.

    Raises ``FileNotFoundError`` with a fix-it message if no cycle is cached
    (run ``python -m datasrc.faa update``).
    """
    from datasrc import faa as _faa
    from datasrc.airac import cycle_from_ident

    root = Path(data_dir) if data_dir else _faa.default_data_root()

    if cycle:
        manifest = _faa.load_manifest(root, cycle_from_ident(cycle))
        if manifest is None:
            raise FileNotFoundError(
                f"cycle {cycle} not cached under {root / 'faa'}; run:  "
                f"python -m datasrc.faa update --cycle {cycle}"
            )
    else:
        manifest = _faa.newest_cached_manifest(root)
        if manifest is None:
            raise FileNotFoundError(
                f"no cached FAA data under {root / 'faa'}; run  "
                f"python -m datasrc.faa update"
            )

    cdir = _faa.cache_dir(root, cycle_from_ident(manifest.cycle))
    cifp_path = cdir / CIFP_FILENAME
    if not cifp_path.exists():
        raise FileNotFoundError(
            f"{cifp_path} missing; re-run:  python -m datasrc.faa "
            f"update --cycle {manifest.cycle} --kinds cifp --force"
        )

    cpath = _cache_path(cdir, cifp_path, areas=areas, comms=comms) if cache else None
    if cpath is not None and cpath.exists():
        try:
            db = pickle.loads(cpath.read_bytes())
            if isinstance(db, NavDatabase):
                return db
        except Exception:                       # corrupt / incompatible -> reparse
            pass

    db = parse_cifp(cifp_path, areas=areas)
    db.cycle = manifest.cycle
    db.effective = manifest.effective
    db.expires = manifest.expires

    if comms:
        from .nasr import merge_comms

        try:
            from .nasr import merge_runways
            merge_runways(db, cdir)
            n = merge_comms(db, cdir)
            if n:
                db.notes.append(f"{n} airports got NASR comm frequencies")
        except FileNotFoundError:
            db.notes.append("NASR CSVs not cached - no comm frequencies "
                            "(run: python -m datasrc.faa update --kinds nasr)")

    if cpath is not None:
        try:
            for stale in cdir.glob("navdb-*.pkl"):   # drop caches for older CIFP files
                if stale != cpath:
                    stale.unlink()
            tmp = cpath.with_suffix(".pkl.tmp")
            tmp.write_bytes(pickle.dumps(db, protocol=pickle.HIGHEST_PROTOCOL))
            tmp.replace(cpath)                   # atomic
        except Exception:                       # read-only fs etc. - cache is optional
            pass

    return db
