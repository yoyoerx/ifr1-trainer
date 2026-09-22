"""Fetch FAA public-domain nav data for an AIRAC cycle, cache it, record a manifest.

Products (see ARCHITECTURE.md sec. 5):

* **CIFP** -- ``FAACIFP18``, one ARINC 424-18 file, inside a per-cycle zip under
  ``aeronav.faa.gov/Upload_313-d/cifp/``. The file name has drifted over the
  years, so several candidates are tried.
* **NASR 28 Day Subscription** -- a zip of CSVs. Its file name has drifted even
  more, so the per-cycle landing page is scraped for the ``.zip`` link.

Nothing here runs in the trainer loop. Network access goes through
``_http_get`` / ``_http_get_text`` so tests can stub it.

CLI::

    python -m datasrc.faa status
    python -m datasrc.faa update                 # current cycle
    python -m datasrc.faa update --cycle 2611
    python -m datasrc.faa list

Both core products are US-Government public domain: caching and redistribution
are permitted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .airac import Cycle, current_cycle, cycle_from_ident

USER_AGENT = "octavi-ifr-trainer/0.1 (nav-data updater)"
HTTP_TIMEOUT = 120
MANIFEST_NAME = "manifest.json"

CIFP_BASE = "https://aeronav.faa.gov/Upload_313-d/cifp/"
NASR_LANDING = (
    "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/"
    "NASR_Subscription/{eff}/"
)

# Members we keep from the NASR CSV zip. Others are skipped to keep the cache
# small; extend as navdata.py grows to need more.
NASR_KEEP = {
    "NAV_BASE.csv",
    "APT_BASE.csv",
    "APT_RWY.csv",
    "ILS_BASE.csv",
    "FRQ.csv",
    "FIX_BASE.csv",
    "AWY_BASE.csv",
}


class DataUnavailable(RuntimeError):
    """The requested cycle/product is not published or not reachable."""


# --------------------------------------------------------------------------- #
# HTTP (stubbed in tests)                                                    #
# --------------------------------------------------------------------------- #
def _http_get(url: str, *, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DataUnavailable(f"404 Not Found: {url}") from exc
        raise
    except urllib.error.URLError as exc:
        raise DataUnavailable(f"cannot reach {url}: {exc.reason}") from exc


def _http_get_text(url: str, *, timeout: int = HTTP_TIMEOUT) -> str:
    return _http_get(url, timeout=timeout).decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# URL resolution                                                             #
# --------------------------------------------------------------------------- #
def cifp_url_candidates(cycle: Cycle) -> list[str]:
    """Best-known per-cycle CIFP zip URLs, most likely first."""
    yymmdd = cycle.effective.strftime("%y%m%d")
    return [
        f"{CIFP_BASE}CIFP_{yymmdd}.zip",
        f"{CIFP_BASE}cifp_{yymmdd}.zip",
    ]


def resolve_nasr_url(cycle: Cycle, *, get_text=_http_get_text) -> str:
    """Scrape the per-cycle NASR landing page for its CSV ``.zip`` link."""
    landing = NASR_LANDING.format(eff=cycle.effective.isoformat())
    try:
        html = get_text(landing)
    except DataUnavailable:
        raise DataUnavailable(
            f"NASR subscription for cycle {cycle.ident} "
            f"(effective {cycle.effective}) is not published yet"
        )
    hrefs = re.findall(r'href=["\']([^"\']+\.zip)["\']', html, flags=re.I)
    if not hrefs:
        raise DataUnavailable(f"no .zip link found on {landing}")
    # prefer an explicitly CSV-flavoured link
    csv_first = sorted(hrefs, key=lambda h: (0 if "csv" in h.lower() else 1))
    return urllib.parse.urljoin(landing, csv_first[0])


# --------------------------------------------------------------------------- #
# manifest                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class ProductRecord:
    url: str
    files: list[str] = field(default_factory=list)
    sha256: dict[str, str] = field(default_factory=dict)
    bytes: int = 0

    def to_dict(self) -> dict:
        return {"url": self.url, "files": self.files, "sha256": self.sha256, "bytes": self.bytes}

    @classmethod
    def from_dict(cls, d: dict) -> "ProductRecord":
        return cls(url=d["url"], files=list(d.get("files", [])),
                   sha256=dict(d.get("sha256", {})), bytes=int(d.get("bytes", 0)))


@dataclass
class Manifest:
    cycle: str
    effective: dt.date
    expires: dt.date
    fetched_at: str
    products: dict[str, ProductRecord] = field(default_factory=dict)
    source: str = "FAA"

    # -- (de)serialisation ------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "cycle": self.cycle,
            "effective": self.effective.isoformat(),
            "expires": self.expires.isoformat(),
            "fetched_at": self.fetched_at,
            "products": {k: v.to_dict() for k, v in self.products.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(
            cycle=d["cycle"],
            effective=dt.date.fromisoformat(d["effective"]),
            expires=dt.date.fromisoformat(d["expires"]),
            fetched_at=d.get("fetched_at", ""),
            products={k: ProductRecord.from_dict(v) for k, v in d.get("products", {}).items()},
            source=d.get("source", "FAA"),
        )

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # -- validity -------------------------------------------------------
    def as_cycle(self) -> Cycle:
        return cycle_from_ident(self.cycle)

    def validity_line(self, on: dt.date | None = None) -> str:
        on = on or dt.date.today()
        c = self.as_cycle()
        days = c.days_until_expiry(on)
        if days > 0:
            tail = f"current, expires in {days} day{'s' if days != 1 else ''} ({c.expiration})"
        elif days == 0:
            tail = f"EXPIRES TODAY ({c.expiration})"
        else:
            tail = f"EXPIRED {-days} day{'s' if days != -1 else ''} ago ({c.expiration})"
        return f"cycle {self.cycle}  effective {self.effective}  -  {tail}"


# --------------------------------------------------------------------------- #
# cache layout                                                               #
# --------------------------------------------------------------------------- #
def default_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def cache_dir(root: Path, cycle: Cycle) -> Path:
    return root / "faa" / cycle.ident


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_extract(zf: zipfile.ZipFile, dest: Path, keep: set[str] | None) -> list[str]:
    """Extract (optionally a subset of) members, flattened, rejecting path escapes."""
    written: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        if info.filename.startswith(("/", "\\")) or ".." in Path(info.filename).parts:
            raise ValueError(f"unsafe zip member: {info.filename!r}")
        name = Path(info.filename).name
        if not name:
            continue
        if keep is not None and name not in keep:
            continue
        (dest / name).write_bytes(zf.read(info))
        written.append(name)
    return written


# --------------------------------------------------------------------------- #
# fetch                                                                      #
# --------------------------------------------------------------------------- #
def _download_zip(candidates: list[str], *, get=_http_get) -> tuple[str, bytes]:
    last: Exception | None = None
    for url in candidates:
        try:
            return url, get(url)
        except DataUnavailable as exc:
            last = exc
    raise DataUnavailable(
        "none of the candidate URLs resolved:\n  " + "\n  ".join(candidates)
    ) from last


def fetch(
    cycle: Cycle,
    *,
    root: Path | None = None,
    kinds: tuple[str, ...] = ("cifp", "nasr"),
    force: bool = False,
    get=_http_get,
    get_text=_http_get_text,
) -> Manifest:
    """Download the requested products for ``cycle`` into the cache and write a
    manifest. Returns the :class:`Manifest`.
    """
    root = root or default_data_root()
    dest = cache_dir(root, cycle)
    dest.mkdir(parents=True, exist_ok=True)
    mpath = dest / MANIFEST_NAME

    manifest = Manifest.read(mpath) if mpath.exists() else Manifest(
        cycle=cycle.ident,
        effective=cycle.effective,
        expires=cycle.expiration,
        fetched_at="",
    )

    for kind in kinds:
        if kind in manifest.products and not force:
            continue
        if kind == "cifp":
            url, blob = _download_zip(cifp_url_candidates(cycle), get=get)
            keep = None  # keep everything in the (small) CIFP zip
        elif kind == "nasr":
            url = resolve_nasr_url(cycle, get_text=get_text)
            _, blob = _download_zip([url], get=get)
            keep = set(NASR_KEEP)
        else:
            raise ValueError(f"unknown product kind: {kind!r}")

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            files = _safe_extract(zf, dest, keep)
        if not files:
            raise DataUnavailable(f"{kind}: zip from {url} held none of the expected files")
        manifest.products[kind] = ProductRecord(
            url=url,
            files=sorted(files),
            sha256={f: _sha256((dest / f).read_bytes()) for f in files},
            bytes=len(blob),
        )

    manifest.fetched_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest.write(mpath)
    return manifest


# --------------------------------------------------------------------------- #
# read-side helpers                                                          #
# --------------------------------------------------------------------------- #
def load_manifest(root: Path, cycle: Cycle) -> Manifest | None:
    mpath = cache_dir(root, cycle) / MANIFEST_NAME
    return Manifest.read(mpath) if mpath.exists() else None


def cached_cycles(root: Path) -> list[str]:
    base = root / "faa"
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir() if (p / MANIFEST_NAME).exists()
    )


def newest_cached_manifest(root: Path) -> Manifest | None:
    idents = cached_cycles(root)
    if not idents:
        return None
    return load_manifest(root, cycle_from_ident(idents[-1]))


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #
def _cmd_status(root: Path, on: dt.date) -> int:
    m = newest_cached_manifest(root)
    if m is None:
        print(f"no cached FAA data under {root / 'faa'}")
        print("run:  python -m datasrc.faa update")
        return 1
    print(m.validity_line(on))
    for kind, rec in sorted(m.products.items()):
        print(f"  {kind:5s}  {len(rec.files)} files  {rec.bytes/1e6:.1f} MB  <- {rec.url}")
    cur = current_cycle(on)
    if cur.ident != m.cycle:
        print(f"note: current cycle is {cur.ident}; run  python -m datasrc.faa update")
    return 0


def _cmd_update(root: Path, cycle: Cycle, kinds: tuple[str, ...], force: bool) -> int:
    print(f"fetching {', '.join(kinds)} for cycle {cycle.label()}")
    try:
        m = fetch(cycle, root=root, kinds=kinds, force=force)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    print(f"cached under {cache_dir(root, cycle)}")
    print(m.validity_line())
    return 0


def _cmd_list(root: Path) -> int:
    idents = cached_cycles(root)
    if not idents:
        print(f"no cached cycles under {root / 'faa'}")
        return 1
    for ident in idents:
        m = load_manifest(root, cycle_from_ident(ident))
        assert m is not None
        print(m.validity_line())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m datasrc.faa", description=__doc__)
    p.add_argument("--data-dir", type=Path, default=default_data_root(),
                   help=f"cache root (default: {default_data_root()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="report the newest cached cycle and its validity")
    sub.add_parser("list", help="list all cached cycles")
    up = sub.add_parser("update", help="download a cycle into the cache")
    up.add_argument("--cycle", help="AIRAC ident YYNN (default: current)")
    up.add_argument("--kinds", default="cifp,nasr",
                    help="comma list of products (default: cifp,nasr)")
    up.add_argument("--force", action="store_true", help="re-download even if cached")

    args = p.parse_args(argv)
    root: Path = args.data_dir
    today = dt.date.today()

    if args.cmd == "status":
        return _cmd_status(root, today)
    if args.cmd == "list":
        return _cmd_list(root)
    if args.cmd == "update":
        cycle = cycle_from_ident(args.cycle) if args.cycle else current_cycle(today)
        kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
        return _cmd_update(root, cycle, kinds, args.force)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
