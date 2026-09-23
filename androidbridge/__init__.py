"""androidbridge — the Android-facing seam onto the existing avionics brain.

Phase 0 scaffolding for the Android port (see ``docs/ANDROID_PORT_PLAN.md``).
Everything in this package is plain, stdlib-only Python with no Android/
Chaquopy/Kotlin dependency of its own — it is exercised by the normal desktop
`pytest` suite exactly like every other pure module in this repo (`navmath`,
`gpsnav`, `instruments`, ...). Kotlin calls into it via Chaquopy; nothing in
here imports Chaquopy or knows it's being called from Kotlin at all.

Why this package exists rather than reusing `main.py` directly: `main.py` is
the desktop orchestrator and imports `pygame` for both the render loop and
its own CLI/event-loop plumbing, which does not exist on Android. The pure
avionics modules it drives (`gpsnav.GpsNav`, `sim_model.SimModel`, and
friends) have no such dependency and port to Android as-is (see
`ANDROID_PORT_PLAN.md` §1, §3.4) — this package is the thin layer that wires
them together the way `main.py` does, but returns plain data (dicts/lists/
floats/strings) instead of driving pygame draw calls, since that's what
marshals cheaply across the Chaquopy JNI boundary.

Not yet ported here (tracked in `ANDROID_PORT_PLAN.md`, not silently
skipped): `main.py`'s `_fms_bezel` key/knob → `GpsNav`-method dispatch table.
That mapping is desktop-keyboard-shaped today; porting/extending it for
Android touch + Octavi-IFR-1-over-USB input is follow-up work once the
Phase 0 hardware spike (§3.1) shows what the actual Android input events
look like, so it isn't guessed at here.
"""

from __future__ import annotations

from .session import TrainerSession

__all__ = ["TrainerSession"]
