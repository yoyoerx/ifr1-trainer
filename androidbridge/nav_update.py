"""On-device FAA nav data acquisition for Android (§3.5,
docs/ANDROID_PORT_PLAN.md). A thin wrapper around `datasrc.faa`'s existing
fetch/manifest machinery, which is already stdlib-only (`urllib`, not
`requests`) and so needs no extra Chaquopy packaging - the only
Android-specific thing this module adds is an explicit writable `root`
(`datasrc.faa.default_data_root()` hardcodes a path relative to this
repo's own file layout via `Path(__file__).resolve().parents[1]`, which
means nothing on Android's Chaquopy-staged, effectively-read-only asset
filesystem; Kotlin passes `context.filesDir`-derived path instead) and one
call per product "kind" so the caller can drive a progress indicator
between calls rather than needing a cross-language progress callback
mid-fetch (Chaquopy calls are synchronous/blocking either way, so there's
no way to get incremental byte-level progress out of one `fetch()` call
without one).

Every function returns a plain string, never raises - consistent with the
rest of `androidbridge` (see `androidbridge/session.py`'s module docstring
on why every cross-boundary value is a primitive/string) and because an
uncaught Python exception crossing into Kotlin needs its own handling path
`BrainBridge.kt` would rather not carry for what's fundamentally "the wifi
was off" / "the FAA server timed out."
"""

from __future__ import annotations

# Three of datasrc.faa.fetch()'s four product kinds. cifp/nasr are what
# navdata.load() actually needs (CIFP is the waypoint/procedure database,
# NASR CSVs are comm frequencies/runways/FSS) and are fetch()'s own
# defaults; artcc (~170KB, Nearest ARTCC/FSS page data) is cheap enough to
# always include too. "airspace" is deliberately left out here, matching
# datasrc.faa's own module docstring ("never fetched by default - opt in
# explicitly"): it's a ~150MB zipped one-time download (only the parsed,
# simplified airspace.json - a couple MB - is kept afterward), nowhere
# near "cheap," and would roughly quadruple Android's first-run download
# size for data that's only Nearest-Airspace-page/moving-map-overlay
# nicety, not core navigation. Worth its own explicit opt-in control later
# (§3.5 follow-up), not silently bundled into the default fetch.
KINDS: tuple[str, ...] = ("cifp", "nasr", "artcc")


def fetch_kind(root: str, kind: str) -> str:
    """Fetch one product kind into `root` for the current AIRAC cycle.
    Returns "ok" on success, or a message describing what went wrong
    ("unavailable: ..." / "error: ...") - call once per `KINDS` entry
    (same cycle each time, since `datasrc.airac.current_cycle()` is stable
    within a 28-day window) so the caller can update a progress indicator
    between calls."""
    from pathlib import Path

    from datasrc.airac import current_cycle
    from datasrc.faa import DataUnavailable, fetch

    try:
        fetch(current_cycle(), root=Path(root), kinds=(kind,))
    except DataUnavailable as exc:
        return f"unavailable: {exc}"
    except Exception as exc:                    # noqa: BLE001 - surfaced to the UI, not swallowed
        return f"error: {exc}"
    return "ok"


def status(root: str) -> str:
    """One line describing the newest cached cycle under `root` (same
    wording as `datasrc.faa`'s own CLI, `_cmd_status`), or a "not cached"
    message if nothing has been fetched yet."""
    from pathlib import Path

    from datasrc.faa import newest_cached_manifest

    m = newest_cached_manifest(Path(root))
    if m is None:
        return "no nav data cached"
    return m.validity_line()
