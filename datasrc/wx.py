"""Fetch live FAA/NWS aviation weather: METARs, TAFs, and winds/temps aloft.

Unlike ``datasrc/faa.py`` (28-day AIRAC nav data, cached for weeks) this
product is time-sensitive - a METAR is stale in an hour, a winds-aloft (FD)
forecast covers a 6/12/24-hour window. There is no "cycle"; each fetch is
cached under its own timestamp plus a ``latest.json`` pointer per key so the
trainer always has *something* even offline, with an explicit staleness
warning instead of a hard expiry.

Sources (public, no key required):

* METAR / TAF -- the NWS Aviation Weather Center's Data API
  (``aviationweather.gov/api/data/...``), JSON format.
* Winds / temperatures aloft -- the same API's ``windtemp`` endpoint, which
  returns the raw fixed-width NWS "FD" text product (the same numbers printed
  on a DUATS/1800wxbrief winds-aloft table). :func:`decode_fd_text` parses it
  per the standard FD group encoding (ddff[tt], see NWS WSOM Ch. D-1): a
  4-digit group is direction-tens + speed, +50/-50 on the direction tens
  flags speeds >=100 kt; an optional 2-digit temperature follows, negative
  unless explicitly signed (always true below 24 000 ft).

Nothing here runs in the trainer loop -- network access goes through
``_http_get`` / ``_http_get_text`` so tests can stub it, same convention as
``datasrc/faa.py``.

CLI::

    python -m datasrc.wx metar KLNS KJFK
    python -m datasrc.wx taf KLNS
    python -m datasrc.wx winds-aloft BOS --fcst 06
    python -m datasrc.wx status
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

USER_AGENT = "octavi-ifr-trainer/0.1 (weather updater)"
HTTP_TIMEOUT = 30
STALE_AFTER_MIN = {"metar": 75, "taf": 6 * 60, "windtemp": 8 * 60}

AWC_BASE = "https://aviationweather.gov/api/data/"


class DataUnavailable(RuntimeError):
    """The requested product/station is not published or not reachable."""


# --------------------------------------------------------------------------- #
# HTTP (stubbed in tests)                                                    #
# --------------------------------------------------------------------------- #
def _http_get_text(url: str, *, timeout: int = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DataUnavailable(f"404 Not Found: {url}") from exc
        raise
    except urllib.error.URLError as exc:
        raise DataUnavailable(f"cannot reach {url}: {exc.reason}") from exc


# --------------------------------------------------------------------------- #
# FD (winds/temps aloft) text decode                                        #
# --------------------------------------------------------------------------- #
# the standard forecast levels the FD product reports (feet, except the top
# few which are pressure-altitude flight levels printed the same way)
FD_LEVELS = (3000, 6000, 9000, 12000, 18000, 24000, 30000, 34000, 39000)

_FD_GROUP_RE = re.compile(r"^(?P<ddff>\d{4})(?P<temp>[+-]?\d{1,2})?$")


def decode_fd_group(code: str, level_ft: int) -> tuple[float | None, float | None, float | None]:
    """Decode one ``ddfftt`` group -> ``(from_deg, kt, temp_c)``.

    ``9900`` (or blank) means light-and-variable / not forecast for this
    level-and-station -> ``(None, None, None)``. Temperature is omitted at
    3000 ft (too close to standard-day surface temp to be useful) and is
    always negative below 24 000 ft unless a ``+`` sign is present; at or
    above 24 000 ft a bare number is still negative (the product drops the
    sign there too in the classic teletype encoding), but a leading ``+`` is
    honoured if present.
    """
    code = code.strip()
    if not code or code in ("9900", "990000", "00000"):
        return None, None, None
    m = _FD_GROUP_RE.match(code)
    if not m:
        return None, None, None
    dd = int(m["ddff"][0:2])
    ff = int(m["ddff"][2:4])
    if dd >= 51:                     # >=100 kt encoding
        dd -= 50
        ff += 100
    from_deg = float((dd * 10) % 360)
    kt = float(ff)
    temp_c = None
    traw = m["temp"]
    if traw is not None and level_ft >= 6000:      # 3000 ft column carries no temp
        if traw.startswith("+"):
            temp_c = float(traw[1:])
        elif traw.startswith("-"):
            temp_c = float(traw)
        else:
            temp_c = -float(traw)
    return from_deg, kt, temp_c


@dataclass(frozen=True, slots=True)
class WindsAloftStation:
    ident: str
    # one 4-tuple per FD_LEVELS entry actually present in the row
    levels: tuple[tuple[int, float | None, float | None, float | None], ...]


def decode_fd_text(text: str) -> list[WindsAloftStation]:
    """Parse a raw NWS FD product body into per-station level rows.

    The product is a fixed-width table: a header line naming the level
    columns (``FT  3000 6000 9000 ...``), then one line per station: a
    3-4 char station id followed by one whitespace-padded group per level
    it forecasts (stations near/above a level's altitude, or too far from
    its data source, are left blank).
    """
    stations: list[WindsAloftStation] = []
    header_cols: list[int] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lstrip().upper().startswith("FT "):
            # column layout: station field (~4 wide) then one 7-char field/level
            header_cols = [m.start() for m in re.finditer(r"\d{3,5}", line)]
            continue
        if header_cols is None:
            continue                                    # preamble / data-basis line
        ident = line[:3].strip()
        if not ident or not ident.isalpha():
            continue
        levels: list[tuple[int, float | None, float | None, float | None]] = []
        for level_ft, col in zip(FD_LEVELS, header_cols):
            field_text = line[col:col + 7].strip() if col < len(line) else ""
            if not field_text:
                continue
            from_deg, kt, temp_c = decode_fd_group(field_text, level_ft)
            if from_deg is None:            # light-and-variable / not forecast
                continue
            levels.append((level_ft, from_deg, kt, temp_c))
        if levels:
            stations.append(WindsAloftStation(ident, tuple(levels)))
    return stations


# --------------------------------------------------------------------------- #
# METAR / TAF                                                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Metar:
    station: str
    raw: str
    obs_time: str = ""
    wind_from_deg: float | None = None
    wind_kt: float | None = None
    wind_gust_kt: float | None = None
    temp_c: float | None = None
    dewpoint_c: float | None = None
    altim_inhg: float | None = None
    flight_category: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Metar":
        altim = d.get("altim")            # AWC reports altim in hPa
        return cls(
            station=str(d.get("icaoId") or d.get("station_id") or ""),
            raw=str(d.get("rawOb") or d.get("raw_text") or ""),
            obs_time=str(d.get("obsTime") or d.get("observation_time") or ""),
            wind_from_deg=_num(d.get("wdir")),
            wind_kt=_num(d.get("wspd")),
            wind_gust_kt=_num(d.get("wgst")),
            temp_c=_num(d.get("temp")),
            dewpoint_c=_num(d.get("dewp")),
            altim_inhg=(_num(altim) * 0.02953) if _num(altim) is not None else None,
            flight_category=str(d.get("fltCat") or ""),
        )


@dataclass(frozen=True, slots=True)
class Taf:
    station: str
    raw: str
    issue_time: str = ""
    valid_from: str = ""
    valid_to: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "Taf":
        return cls(
            station=str(d.get("icaoId") or d.get("station_id") or ""),
            raw=str(d.get("rawTAF") or d.get("raw_text") or ""),
            issue_time=str(d.get("issueTime") or d.get("issue_time") or ""),
            valid_from=str(d.get("validTimeFrom") or ""),
            valid_to=str(d.get("validTimeTo") or ""),
        )


def _num(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def fetch_metar(idents: list[str], *, get_text=_http_get_text) -> list[Metar]:
    url = AWC_BASE + "metar?ids=" + urllib.parse.quote(",".join(idents)) + "&format=json"
    data = json.loads(get_text(url))
    if not data:
        raise DataUnavailable(f"no METAR returned for {','.join(idents)}")
    return [Metar.from_json(d) for d in data]


def fetch_taf(idents: list[str], *, get_text=_http_get_text) -> list[Taf]:
    url = AWC_BASE + "taf?ids=" + urllib.parse.quote(",".join(idents)) + "&format=json"
    data = json.loads(get_text(url))
    if not data:
        raise DataUnavailable(f"no TAF returned for {','.join(idents)}")
    return [Taf.from_json(d) for d in data]


def fetch_winds_aloft(region: str, *, fcst: str = "06",
                       get_text=_http_get_text) -> list[WindsAloftStation]:
    url = AWC_BASE + f"windtemp?region={urllib.parse.quote(region)}&fcst={fcst}&level=low"
    text = get_text(url)
    stations = decode_fd_text(text)
    if not stations:
        raise DataUnavailable(f"no winds-aloft data parsed for region {region} fcst {fcst}")
    return stations


# --------------------------------------------------------------------------- #
# cache                                                                      #
# --------------------------------------------------------------------------- #
def default_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def wx_dir(root: Path) -> Path:
    return root / "wx"


def _now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _cache(root: Path, kind: str, key: str, payload: dict) -> Path:
    d = wx_dir(root) / kind
    d.mkdir(parents=True, exist_ok=True)
    payload = dict(payload, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())
    dated = d / f"{key}_{_now_stamp()}.json"
    dated.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (d / f"{key}_latest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dated


def load_latest(root: Path, kind: str, key: str) -> dict | None:
    p = wx_dir(root) / kind / f"{key}_latest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def staleness_minutes(payload: dict, *, on: dt.datetime | None = None) -> float | None:
    fetched = payload.get("fetched_at")
    if not fetched:
        return None
    on = on or dt.datetime.now(dt.timezone.utc)
    return (on - dt.datetime.fromisoformat(fetched)).total_seconds() / 60.0


def load_metar(root: Path, ident: str) -> Metar | None:
    """The most recently cached METAR for one station, or ``None``."""
    payload = load_latest(root, "metar", ident.upper())
    if payload is None or not payload.get("stations"):
        return None
    return Metar(**payload["stations"][0])


def load_taf(root: Path, ident: str) -> Taf | None:
    """The most recently cached TAF for one station, or ``None``."""
    payload = load_latest(root, "taf", ident.upper())
    if payload is None or not payload.get("stations"):
        return None
    return Taf(**payload["stations"][0])


def fetch_and_cache_metar(root: Path, idents: list[str], *, get_text=None) -> list[Metar]:
    """Fetch + cache METARs for ``idents``, one file per station. Shared by
    the CLI and by :mod:`wx_auto`'s background poller."""
    reports = fetch_metar(idents, get_text=get_text or _http_get_text)
    for m in reports:
        _cache(root, "metar", m.station, {"stations": [asdict(m)]})
    return reports


def fetch_and_cache_taf(root: Path, idents: list[str], *, get_text=None) -> list[Taf]:
    """Fetch + cache TAFs for ``idents``, one file per station."""
    reports = fetch_taf(idents, get_text=get_text or _http_get_text)
    for t in reports:
        _cache(root, "taf", t.station, {"stations": [asdict(t)]})
    return reports


def fetch_and_cache_winds_aloft(root: Path, region: str, *, fcst: str = "06",
                                get_text=None) -> list[WindsAloftStation]:
    """Fetch + cache an FD winds/temps-aloft forecast for one region."""
    stations = fetch_winds_aloft(region, fcst=fcst, get_text=get_text or _http_get_text)
    _cache(root, "windtemp", region.upper(),
          {"region": region.upper(), "fcst": fcst,
           "stations": [{"ident": s.ident, "levels": list(s.levels)} for s in stations]})
    return stations


def load_winds_aloft_profile(root: Path, region: str, station: str):
    """Bridge a cached winds-aloft fetch to a :class:`windsaloft.WindsAloftProfile`
    for one station, or ``None`` if nothing cached / the station isn't in it."""
    from windsaloft import WindsAloftProfile
    payload = load_latest(root, "windtemp", region.upper())
    if payload is None:
        return None
    for row in payload.get("stations", []):
        if row.get("ident", "").upper() == station.upper():
            return WindsAloftProfile.from_fd_levels(
                [tuple(lv) for lv in row.get("levels", [])])
    return None


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #
def _cmd_metar(root: Path, idents: list[str]) -> int:
    try:
        reports = fetch_and_cache_metar(root, idents)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    for m in reports:
        print(m.raw or f"{m.station}: (no data)")
    return 0


def _cmd_taf(root: Path, idents: list[str]) -> int:
    try:
        reports = fetch_and_cache_taf(root, idents)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    for t in reports:
        print(t.raw or f"{t.station}: (no data)")
    return 0


def _cmd_winds_aloft(root: Path, region: str, fcst: str) -> int:
    try:
        stations = fetch_and_cache_winds_aloft(root, region, fcst=fcst)
    except DataUnavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 2
    for s in stations:
        parts = ", ".join(
            f"{alt}ft {d:03.0f}@{k:02.0f}" + (f" {t:+.0f}C" if t is not None else "")
            for alt, d, k, t in s.levels if d is not None)
        print(f"{s.ident}: {parts}")
    return 0


def _cmd_status(root: Path) -> int:
    d = wx_dir(root)
    if not d.is_dir():
        print(f"no cached weather under {d}")
        return 1
    found = False
    for kind_dir in sorted(p for p in d.iterdir() if p.is_dir()):
        for f in sorted(kind_dir.glob("*_latest.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            age = staleness_minutes(payload)
            stale = age is not None and age > STALE_AFTER_MIN.get(kind_dir.name, 60)
            tag = "STALE" if stale else "fresh"
            found = True
            print(f"{kind_dir.name:9s} {f.stem[:-7]:12s} "
                  f"{age:.0f} min ago  [{tag}]" if age is not None else
                  f"{kind_dir.name:9s} {f.stem[:-7]:12s} (no timestamp)")
    if not found:
        print(f"no cached weather under {d}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m datasrc.wx", description=__doc__)
    p.add_argument("--data-dir", type=Path, default=default_data_root(),
                   help=f"cache root (default: {default_data_root()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("metar", help="fetch + cache current METARs")
    m.add_argument("idents", nargs="+", help="ICAO station idents, e.g. KLNS KJFK")

    t = sub.add_parser("taf", help="fetch + cache current TAFs")
    t.add_argument("idents", nargs="+", help="ICAO station idents")

    w = sub.add_parser("winds-aloft", help="fetch + cache an FD winds/temps-aloft forecast")
    w.add_argument("region", help="NWS FD region code, e.g. BOS, MIA, SLC")
    w.add_argument("--fcst", default="06", choices=("06", "12", "24"),
                   help="forecast period (default: 06)")

    sub.add_parser("status", help="report what weather is cached and how stale")

    args = p.parse_args(argv)
    root: Path = args.data_dir

    if args.cmd == "metar":
        return _cmd_metar(root, args.idents)
    if args.cmd == "taf":
        return _cmd_taf(root, args.idents)
    if args.cmd == "winds-aloft":
        return _cmd_winds_aloft(root, args.region, args.fcst)
    if args.cmd == "status":
        return _cmd_status(root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
