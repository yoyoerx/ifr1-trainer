"""On-device approach-plate PDF fetching for Android - the §3.4-flagged
"hand plates off to an Android Intent for the system PDF viewer" fallback
(docs/ANDROID_PORT_PLAN.md §3.4), i.e. desktop's own `main._open_selected_
chart` behavior (every non-`stack` layout's AUX>Charts ENT key), not the
`stack` layout's separate `pypdfium2` inline-rasterization path - that one
stays desktop-only, its native-wheel-on-Android question is still open.

A thin wrapper around `datasrc.dtpp`, which is already stdlib-only
(`urllib`, `xml.etree`, `pickle` - no `requests`/native deps) so it needs
no extra Chaquopy packaging, same story as `datasrc.faa` (see
`androidbridge/nav_update.py`'s module docstring). The one genuinely
desktop-only piece is `dtpp.open_with_os_default` (`os.startfile`/
`subprocess.Popen(["open"/"xdg-open", ...])`) - meaningless on Android, so
this module stops at handing back a cached local file **path**; opening it
is `BrainBridge.openPdf`'s job (an `Intent.ACTION_VIEW` through a
`FileProvider` content URI, the Android-native equivalent of "hand it to
whatever the OS already has for viewing PDFs").

Every function returns a plain string, never raises - same convention as
`androidbridge/nav_update.py` and the rest of `androidbridge`.
"""

from __future__ import annotations

_SEP = "\x1f"   # ASCII unit separator - chart_code/chart_name never contain it, unlike "|" or ","


def update_index(root: str) -> str:
    """Fetch + cache the current AIRAC cycle's chart index (`d-TPP_Metafile
    .xml`, ~16MB, one-time per cycle) - must succeed at least once before
    `list_charts`/`fetch_chart_path` can find anything. Returns "ok" or a
    message describing what went wrong."""
    from pathlib import Path

    from datasrc.airac import current_cycle
    from datasrc.dtpp import DataUnavailable, fetch_and_cache_metafile

    try:
        fetch_and_cache_metafile(Path(root), current_cycle())
    except DataUnavailable as exc:
        return f"unavailable: {exc}"
    except Exception as exc:                    # noqa: BLE001 - surfaced to the UI, not swallowed
        return f"error: {exc}"
    return "ok"


def list_charts(root: str, ident: str) -> str:
    """Charts available for `ident` (FAA LID or ICAO, either form - see
    `datasrc.dtpp.charts_for_airport`), as one line per chart:
    `chart_code<0x1F>chart_name`, newline-joined. Returns "no index" if
    `update_index` hasn't been run yet, or "no charts" if the index has
    nothing for this airport (a real gap - non-contiguous-US idents aren't
    resolved yet, see `datasrc.dtpp`'s module docstring)."""
    from pathlib import Path

    from datasrc.airac import current_cycle
    from datasrc.dtpp import charts_for_airport, load_index

    records = load_index(Path(root), current_cycle())
    if records is None:
        return "no index"
    charts = charts_for_airport(records, ident)
    if not charts:
        return "no charts"
    return "\n".join(f"{c.chart_code}{_SEP}{c.chart_name}" for c in charts)


def fetch_chart_path(root: str, ident: str, index: int) -> str:
    """Fetch (if not already cached) and return the local path to chart
    `index` (0-based, into the same order `list_charts` returns) for
    `ident`. Returns the path, or "no index"/"no charts"/"bad index"/an
    "unavailable: ..."/"error: ..." message - see [BrainBridge.openPdf] for
    what happens to a successful path."""
    from pathlib import Path

    from datasrc.airac import current_cycle
    from datasrc.dtpp import DataUnavailable, charts_for_airport, fetch_and_cache_chart, load_index

    cycle = current_cycle()
    records = load_index(Path(root), cycle)
    if records is None:
        return "no index"
    charts = charts_for_airport(records, ident)
    if not charts:
        return "no charts"
    if not (0 <= index < len(charts)):
        return "bad index"
    try:
        path = fetch_and_cache_chart(Path(root), cycle, charts[index].pdf_name)
    except DataUnavailable as exc:
        return f"unavailable: {exc}"
    except Exception as exc:                    # noqa: BLE001 - surfaced to the UI, not swallowed
        return f"error: {exc}"
    return str(path)
