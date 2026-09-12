"""config.py - optional on-disk config, merged under the command line.

Precedence (lowest to highest): built-in :data:`DEFAULTS` < config file <
explicit command-line flags. The file is TOML (``octavi.toml``) or JSON
(``octavi.json``); the first found of:

    1. the path passed to ``--config``
    2. ``./octavi.toml`` / ``./octavi.json`` in the working directory
    3. ``~/.config/octavi-ifr-trainer/config.toml`` (or ``.json``)

Keys mirror the long CLI flags with dashes turned to underscores, e.g.::

    # octavi.toml
    plan = "KBOS PVD KJFK"
    wind = "300/25"
    unit = "430"
    layout = "steam"
    xplane_feed = true
    gdl90 = true
    gdl90_host = "192.168.1.255"

Unknown keys are ignored (with a note) so a config can carry comments-as-keys
without breaking a run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:      # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore

__all__ = ["DEFAULTS", "find_config", "load_config", "merge"]

# every configurable knob and its built-in default
DEFAULTS: dict[str, object] = {
    "plan": "",
    "approach": "",       # "ICAO IDENT [TRANSITION]" - loads a CIFP approach
    "wind": "",
    "winds_aloft": "",    # "ALT:DIR/SPD[/TEMPC] ..." - overrides "wind" when set
    "wx_region": "",      # NWS FD region (e.g. BOS) to pull a cached winds-aloft fetch from
    "wx_station": "",     # station within wx_region to read the profile for
    "wx_auto_refresh": False,   # background METAR/TAF/winds-aloft refresh (see wx_auto.py)
    "wx_metar_minutes": 20.0,
    "wx_taf_minutes": 60.0,
    "wx_winds_aloft_minutes": 60.0,
    "tas": 140.0,
    "altitude": 5000.0,
    "layout": "gps",
    "unit": "530",
    "dual": False,         # run a second GNS unit (the other of 530/430) as FMS2
    "no_device": False,
    "manual": False,
    "xplane_feed": False,
    "xplane_host": "127.0.0.1",
    "gdl90": False,
    "gdl90_host": "255.255.255.255",
    "gdl90_port": 4000,
    "gdl90_discover": False,    # listen for ForeFlight's discovery broadcast, switch to unicast
    "headless": False,
    "time_warp": 1,       # simulation speed multiplier: 1/5/10/20 (keys 1-4 while running)
}

_FILENAMES = ("octavi.toml", "octavi.json")
_USER_DIR = Path.home() / ".config" / "octavi-ifr-trainer"


def find_config(explicit: str | os.PathLike | None = None) -> Path | None:
    """First existing config file per the documented search order, or ``None``."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for name in _FILENAMES:
        p = Path.cwd() / name
        if p.is_file():
            return p
    for name in ("config.toml", "config.json"):
        p = _USER_DIR / name
        if p.is_file():
            return p
    return None


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text or "{}")
    if tomllib is None:
        raise RuntimeError("TOML config needs Python 3.11+ (or use octavi.json)")
    return tomllib.loads(text)


def load_config(explicit: str | os.PathLike | None = None) -> dict:
    """Load + validate a config file into a dict of known keys. ``{}`` if none."""
    path = find_config(explicit)
    if path is None:
        return {}
    raw = _parse(path)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a table/object")
    out: dict[str, object] = {}
    for k, v in raw.items():
        key = str(k).replace("-", "_")
        if key in DEFAULTS:
            out[key] = v
        else:
            print(f"config: ignoring unknown key {k!r} in {path}")
    return out


def merge(cli: dict, file_cfg: dict) -> dict:
    """DEFAULTS < file_cfg < cli (only keys the user actually passed on the CLI)."""
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in file_cfg.items() if k in DEFAULTS})
    merged.update({k: v for k, v in cli.items() if k in DEFAULTS and v is not None})
    return merged
