"""Parser for ``AFF.txt`` (28 Day NASR Subscription, root-level legacy text
product): ARTCC remote air/ground (RCAG) communication sites and their
frequencies - the "five nearest ... points of communication" the GNS 530's
Nearest Center page shows (Pilot's Guide p.119) come from exactly this kind
of per-site data, not a single frequency per ARTCC (which the CSV/AIXM
products don't carry at all - see FINDINGS F62).

The file is a fixed-width flat file, but the field widths aren't documented
anywhere the FAA still hosts, so this parses by anchor instead: a record-type
marker immediately followed by a date (``RCAG DD/MM/YYYY`` or
``ARTCCDD/MM/YYYY``) splits the line into a name portion and a
state/position portion, and DD-MM-SS.sss[NSEW] is regexed out of the tail
rather than sliced by column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Site", "parse_aff"]

_ANCHOR = re.compile(r"(RCAG|ARTCC)\s*(\d{2}/\d{2}/\d{4})")
_DMS = re.compile(r"(\d{2,3})-(\d{2})-(\d{2}(?:\.\d+)?)([NSEW])")
_FREQ = re.compile(r"^AFF3(?P<artcc>[A-Z0-9]{3})\s+(?P<site>\S(?:.*\S)?)\s{2,}RCAG\s+"
                   r"(?P<freq>\d{3}\.\d+|\d{3})\s+(?P<band>LOW/HIGH|LOW|HIGH)")


def _dms_to_decimal(deg: str, mn: str, sec: str, hemi: str) -> float:
    val = int(deg) + int(mn) / 60.0 + float(sec) / 3600.0
    return -val if hemi in "SW" else val


@dataclass
class Site:
    artcc: str                                   # "ZDC"
    name: str                                     # RCAG site name, e.g. "BALTIMORE"
    lat: float
    lon: float
    freqs: list[tuple[float, str]] = field(default_factory=list)   # (MHz, "LOW"|"HIGH"|"LOW/HIGH")


def parse_aff(text: str) -> list[Site]:
    sites: dict[tuple[str, str], Site] = {}
    for line in text.splitlines():
        if line.startswith("AFF1"):
            m = _ANCHOR.search(line)
            if not m or m.group(1) != "RCAG":       # skip the plain ARTCC reference-point rows
                continue
            head = line[4:m.start()]
            parts = [p for p in re.split(r"\s{2,}", head) if p.strip()]
            if len(parts) < 2:
                continue
            artcc = parts[0][:3].strip()
            site = parts[1].strip()
            dms = _DMS.findall(line[m.end():])
            if len(dms) < 2:
                continue
            lat = _dms_to_decimal(*dms[0])
            lon = _dms_to_decimal(*dms[1])
            sites.setdefault((artcc, site), Site(artcc=artcc, name=site, lat=lat, lon=lon))
        elif line.startswith("AFF3"):
            m = _FREQ.match(line)
            if not m:
                continue
            key = (m.group("artcc"), m.group("site").strip())
            s = sites.get(key)
            if s is None:
                continue
            entry = (float(m.group("freq")), m.group("band"))
            if entry not in s.freqs:
                s.freqs.append(entry)
    return list(sites.values())
