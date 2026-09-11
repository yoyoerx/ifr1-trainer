"""Fetch FAA d-TPP (digital Terminal Procedures Publication) approach plates.

d-TPP is the electronic form of the paper Terminal Procedures Publication:
one PDF per chart (instrument approach, SID, STAR, airport diagram, takeoff/
alternate minimums). Two pieces, both per AIRAC cycle:

* **Index** -- ``d-TPP_Metafile.xml`` at
  ``aeronav.faa.gov/d-tpp/<cycle>/xml_data/d-TPP_Metafile.xml`` (confirmed by
  fetching the live directory listing while building this - ~16 MB, covers
  every US airport). Nested ``state_code > city_name > airport_name >
  record``; a ``record``'s ``chart_code``/``chart_name``/``useraction``/
  ``pdf_name`` child elements are confirmed against a third-party parser
  (jlmcgraw/GeoReferencePlates) since the FAA doesn't publish a bare XSD.
  ``useraction`` flags a chart Added/Changed/Deleted since the prior cycle.
* **Plates** -- served flat and individually per cycle, e.g.
  ``aeronav.faa.gov/d-tpp/<cycle>/00775IL8.PDF`` -- no bulk archive needed,
  so a single chart can be fetched on its own instead of downloading the
  whole (multi-gigabyte) cycle.

Scope, deliberately: this fetches + caches + opens plates, nothing renders a
PDF inside the trainer. `open_with_os_default` hands the cached file to
whatever the OS already has for viewing PDFs -- reusing a real, full-featured
viewer beats reimplementing pan/zoom/search, and doing this from any single
box precludes a tablet with ForeFlight always being on hand.

Airport idents here are the FAA LID the metafile uses (often the ICAO ident
minus its leading "K" for the continental US, e.g. "LNS" for KLNS) --
`charts_for_airport` tries both forms. Non-contiguous-US idents (PA../PH..
etc.) are a known gap; the FAA LID/ICAO crosswalk `navdata/nasr.py` already
has (`APT_BASE.csv`) isn't threaded through here yet.

Nothing here runs in the trainer's render/physics loop -- fetches happen
either from the CLI or from one pilot-initiated keypress (the AUX Charts
page's ENT), off the main thread so a slow download can't stall the ~30 Hz
loop. Network access goes through ``_http_get`` / ``_http_get_text`` so
tests can stub it, same convention as ``datasrc/faa.py`` and ``datasrc/wx.py``.

CLI::

    python -m datasrc.dtpp update-index [--cycle 2609]
    python -m datasrc.dtpp list KLNS
    python -m datasrc.dtpp fetch KLNS 3          # chart #3 from `list`'s numbering
    python -m datasrc.dtpp open KLNS 3           # fetch (if needed) + open it

Public domain (US Government work): caching and redistribution are permitted.
"""

from __future__ import annotations

import argparse
import pickle
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .airac import Cycle, current_cycle, cycle_from_ident

USER_AGENT = "octavi-ifr-trainer/0.1 (chart updater)"
HTTP_TIMEOUT = 120
DTPP_BASE = "https://aeronav.faa.gov/d-tpp/"
_INDEX_SCHEMA = 1     # bump if ChartRecord's shape changes so stale pickles are ignored


class DataUnavailable(RuntimeError):
    """The requested cycle/chart is not published or not reachable."""


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


# --------------------------------------------------------------------------- #
# URLs                                                                       #
# --------------------------------------------------------------------------- #
def metafile_url(cycle: Cycle) -> str:
    return f"{DTPP_BASE}{cycle.ident}/xml_data/d-TPP_Metafile.xml"


def pdf_url(cycle: Cycle, pdf_name: str) -> str:
    return f"{DTPP_BASE}{cycle.ident}/{urllib.parse.quote(pdf_name)}"


# --------------------------------------------------------------------------- #
# metafile parsing                                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ChartRecord:
    state: str
    city: str
    volume: str
    airport_ident: str        # FAA LID, e.g. "LNS" (usually ICAO minus a leading K)
    airport_name: str
    military: str
    chart_seq: str
    chart_code: str           # IAP / APD / STAR / DP / MIN / ...
    chart_name: str           # e.g. "ILS OR LOC RWY 08"
    useraction: str           # A/C/D since the prior cycle, or blank
    pdf_name: str


def parse_metafile(xml_bytes: bytes) -> list[ChartRecord]:
    """Parse a ``d-TPP_Metafile.xml`` document into a flat list of charts."""
    root = ET.fromstring(xml_bytes)
    out: list[ChartRecord] = []
    for state in root.findall("state_code"):
        state_id = state.get("ID", "")
        for city in state.findall("city_name"):
            volume = city.get("volume", "")
            city_name = (city.text or "").strip()
            for apt in city.findall("airport_name"):
                apt_ident = apt.get("apt_ident", "")
                military = apt.get("military", "")
                apt_name = (apt.text or "").strip()
                for rec in apt.findall("record"):
                    def child(tag: str, _rec=rec) -> str:
                        el = _rec.find(tag)
                        return (el.text or "").strip() if el is not None and el.text else ""
                    pdf = child("pdf_name")
                    if not pdf:
                        continue                 # legend/general-info rows etc. - skip
                    out.append(ChartRecord(
                        state=state_id, city=city_name, volume=volume,
                        airport_ident=apt_ident, airport_name=apt_name, military=military,
                        chart_seq=child("chartseq"), chart_code=child("chart_code"),
                        chart_name=child("chart_name"), useraction=child("useraction"),
                        pdf_name=pdf,
                    ))
    return out


def charts_for_airport(records: list[ChartRecord], ident: str) -> list[ChartRecord]:
    """Charts for one airport. ``ident`` may be the FAA LID or the ICAO ident
    (a leading "K" is tried both with and without it)."""
    ident = ident.strip().upper()
    candidates = {ident}
    if ident.startswith("K") and len(ident) == 4:
        candidates.add(ident[1:])
    else:
        candidates.add("K" + ident)
    return [r for r in records if r.airport_ident.upper() in candidates]


# --------------------------------------------------------------------------- #
# cache                                                                      #
# --------------------------------------------------------------------------- #
def default_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def dtpp_dir(root: Path, cycle: Cycle) -> Path:
    return root / "faa" / cycle.ident / "dtpp"


def _metafile_path(root: Path, cycle: Cycle) -> Path:
    return dtpp_dir(root, cycle) / "d-TPP_Metafile.xml"


def _index_cache_path(root: Path, cycle: Cycle) -> Path:
    return dtpp_dir(root, cycle) / f"index-{_INDEX_SCHEMA}.pkl"


def fetch_and_cache_metafile(root: Path, cycle: Cycle, *, get=None, force: bool = False) -> Path:
    """Download + cache the metafile for ``cycle``, and drop the parsed-index
    pickle (if any) since it would now be stale."""
    d = dtpp_dir(root, cycle)
    d.mkdir(parents=True, exist_ok=True)
    path = _metafile_path(root, cycle)
    if force or not path.exists():
        blob = (get or _http_get)(metafile_url(cycle))
        path.write_bytes(blob)
        idx = _index_cache_path(root, cycle)
        if idx.exists():
            idx.unlink()
    return path


def load_index(root: Path, cycle: Cycle) -> list[ChartRecord] | None:
    """The parsed chart index for ``cycle``, or ``None`` if the metafile
    hasn't been fetched yet. Parsing ~16 MB of XML isn't free, so the parsed
    list is pickle-cached next to it (same idea as `navdata.load`'s DB
    cache) -- repeat calls (the CLI, or the Charts page re-opening) are fast."""
    path = _metafile_path(root, cycle)
    if not path.exists():
        return None
    cpath = _index_cache_path(root, cycle)
    if cpath.exists():
        try:
            records = pickle.loads(cpath.read_bytes())
            if isinstance(records, list):
                return records
        except Exception:                        # noqa: BLE001 - corrupt cache -> reparse
            pass
    records = parse_metafile(path.read_bytes())
    try:
        cpath.write_bytes(pickle.dumps(records, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:                            # noqa: BLE001 - cache is optional
        pass
    return records


def fetch_and_cache_chart(root: Path, cycle: Cycle, pdf_name: str, *, get=None) -> Path:
    """Download + cache one plate PDF. Cycle-scoped plates never change once
    published, so an already-cached file is never re-fetched."""
    d = dtpp_dir(root, cycle) / "pdf"
    d.mkdir(parents=True, exist_ok=True)
    path = d / pdf_name
    if not path.exists():
        blob = (get or _http_get)(pdf_url(cycle, pdf_name))
        path.write_bytes(blob)
    return path


def open_with_os_default(path: Path) -> None:
    """Hand a cached PDF to the OS's own default viewer."""
    if sys.platform.startswith("win"):
        import os
        os.startfile(str(path))                  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #
def _resolve_chart(charts: list[ChartRecord], selector: str) -> ChartRecord | None:
    """``selector`` is a 1-based index into ``charts`` (as printed by `list`),
    or a case-insensitive substring of a chart's name."""
    if selector.strip().isdigit():
        i = int(selector.strip()) - 1
        return charts[i] if 0 <= i < len(charts) else None
    needle = selector.strip().upper()
    for c in charts:
        if needle in c.chart_name.upper():
            return c
    return None


def _cmd_update_index(root: Path, cycle: Cycle, force: bool) -> int:
    try:
        path = fetch_and_cache_metafile(root, cycle, force=force)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    print(f"cached {path}")
    return 0


def _load_or_hint(root: Path, cycle: Cycle) -> list["ChartRecord"] | None:
    records = load_index(root, cycle)
    if records is None:
        print(f"no chart index cached for cycle {cycle.ident}; run:  "
              f"python -m datasrc.dtpp update-index --cycle {cycle.ident}", file=sys.stderr)
    return records


def _cmd_list(root: Path, cycle: Cycle, ident: str) -> int:
    records = _load_or_hint(root, cycle)
    if records is None:
        return 2
    charts = charts_for_airport(records, ident)
    if not charts:
        print(f"no charts found for {ident}")
        return 1
    for i, c in enumerate(charts, start=1):
        flag = f" [{c.useraction}]" if c.useraction and c.useraction.upper() != "N" else ""
        print(f"{i:3d}  {c.chart_code:<5} {c.chart_name}{flag}")
    return 0


def _cmd_fetch(root: Path, cycle: Cycle, ident: str, selector: str, *, do_open: bool) -> int:
    records = _load_or_hint(root, cycle)
    if records is None:
        return 2
    charts = charts_for_airport(records, ident)
    chart = _resolve_chart(charts, selector)
    if chart is None:
        print(f"no chart matching {selector!r} for {ident}", file=sys.stderr)
        return 1
    try:
        path = fetch_and_cache_chart(root, cycle, chart.pdf_name)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    print(f"{chart.chart_name} -> {path}")
    if do_open:
        open_with_os_default(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m datasrc.dtpp", description=__doc__)
    p.add_argument("--data-dir", type=Path, default=default_data_root(),
                   help=f"cache root (default: {default_data_root()})")
    p.add_argument("--cycle", help="AIRAC ident YYNN (default: current)")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("update-index", help="fetch + cache the chart index (d-TPP_Metafile.xml)")
    u.add_argument("--force", action="store_true", help="re-download even if cached")

    lst = sub.add_parser("list", help="list charts available for an airport")
    lst.add_argument("ident", help="airport ident, e.g. KLNS or LNS")

    f = sub.add_parser("fetch", help="fetch one chart PDF (by list-number or name substring)")
    f.add_argument("ident")
    f.add_argument("selector")

    o = sub.add_parser("open", help="fetch (if needed) + open a chart with the OS's PDF viewer")
    o.add_argument("ident")
    o.add_argument("selector")

    args = p.parse_args(argv)
    root: Path = args.data_dir
    cycle = cycle_from_ident(args.cycle) if args.cycle else current_cycle()

    if args.cmd == "update-index":
        return _cmd_update_index(root, cycle, args.force)
    if args.cmd == "list":
        return _cmd_list(root, cycle, args.ident)
    if args.cmd == "fetch":
        return _cmd_fetch(root, cycle, args.ident, args.selector, do_open=False)
    if args.cmd == "open":
        return _cmd_fetch(root, cycle, args.ident, args.selector, do_open=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
