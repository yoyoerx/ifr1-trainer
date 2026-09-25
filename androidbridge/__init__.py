"""androidbridge — the Android-facing seam onto the existing avionics brain.

Phase 1 (see ``docs/ANDROID_PORT_PLAN.md`` §6). Everything in this package is
plain, stdlib-only Python with no Android/Chaquopy/Kotlin dependency of its
own — it is exercised by the normal desktop `pytest` suite exactly like every
other pure module in this repo (`navmath`, `gpsnav`, `instruments`, ...).
Kotlin calls into it via Chaquopy; nothing in here imports Chaquopy or knows
it's being called from Kotlin at all.

Correcting an earlier (Phase 0) assumption here: this package does **not**
reimplement `main.py`'s `World`/`route_event` — it reuses them directly.
`main.py`'s *module-level* imports are stdlib-only; `pygame` and `render` are
only ever imported lazily, inside `run()`'s body, which nothing here calls.
`World` (owns `gns`/`sim`/`radios`/`ap`/baro/shift-latch/ident-timer state)
and `route_event` (the IFR-1 mode-selector routing table — COM/NAV tuning,
shift-latch semantics, AP-row buttons, XPDR, FMS bezel keys) are both plain,
pygame-free and safely importable under Chaquopy, so `TrainerSession`
(`session.py`) wraps them instead of hand-porting a second copy of that
routing table — single source of truth, same convention as every other
module here. `render.py`'s pygame drawing genuinely doesn't port as-is
(§3.6 covers what replaces it: a draw-command-list Kotlin/Compose replays),
which is the one part of the old rationale that's still true.
"""

from __future__ import annotations

from .session import TrainerSession

__all__ = ["TrainerSession"]
