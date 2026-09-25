"""Draw-command-list producers for the Android/Chaquopy render bridge (§3.6,
docs/ANDROID_PORT_PLAN.md). Pure stdlib + math - no pygame, no I/O - so it
imports cleanly under Chaquopy exactly like the other brain modules.

Each `*_commands()` function is a port of the matching `render.py` drawing
function, rewritten to *append encoded command strings* to a list instead of
calling `pygame.draw.*`/`r._t` - same math, same layout constants, same
call shape, so the port is a mechanical substitution against the reference
rather than a redesign. `render.py` itself is never imported here (it pulls
in pygame at module scope in places this module must stay free of); the
handful of color constants it defines are duplicated verbatim below with a
comment pointing back at the source of truth.

Encoding (see android/.../render/DrawCommand.kt's `parseDrawCommands` for
the Kotlin-side decoder): one command per line, pipe-delimited fields,
joined with "\n" by `encode()`. A newline-delimited *string* is used rather
than a Python list handed across the Chaquopy boundary as a `PyObject`/Java
`List` - a real on-device crash (2026-09-24) found that direction of
Chaquopy's collection marshaling produces a Python-side object `tuple()`/
`list()` can't consume, so every cross-boundary value in this codebase
after that point is a primitive or a string, deliberately.

    T|x|y|sizeSp|colorArgb|fontId|align|text   (text last - only variable-length field)
    L|x1|y1|x2|y2|widthPx|colorArgb
    R|x|y|w|h|colorArgb|filled(0/1)
    C|cx|cy|radius|colorArgb|filled(0/1)
    P|colorArgb|filled(0/1)|x0,y0;x1,y1;...
"""

from __future__ import annotations

import math

# -- colors, duplicated from render.py's module-level constants (source of
# truth there - kept in sync by hand since this module must stay pygame-free) --
PANEL = 0xFF12161A   # (18, 22, 26)
EDGE = 0xFF3C444C     # (60, 68, 76)
TEXT = 0xFFD2DCE1     # (210, 220, 225)
DIM = 0xFF78828A      # (120, 130, 138)
GPS_GREEN = 0xFF78E68C  # (120, 230, 140)
CYAN = 0xFF5AD2EB     # (90, 210, 235)
AMBER = 0xFFF0B43C    # (240, 180, 60)
WHITE = 0xFFEBF0F5    # (235, 240, 245)
RED = 0xFFEB5A50      # (235, 90, 80)
DIAL_BG = 0xFF06090B  # (6, 9, 11)
OFF_FLAG_BG = 0xFF422828  # (66, 40, 40)

_FONT_SM = 12.0   # sizeSp for render.py's r.f_sm-equivalent labels
_FONT_LCD = 20.0  # sizeSp for r.lcd-equivalent digital readouts


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class _Cmds:
    """Same call shape as render.py's pygame.draw.*/r._t calls - a thin
    builder so the ported functions below read almost line-for-line like
    the reference they're mirroring."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def text(self, text: str, x: float, y: float, *, size: float = _FONT_SM,
              color: int = TEXT, font: str = "mono", align: int = 0) -> None:
        safe = text.replace("\n", " ").replace("|", "/")
        self.lines.append(f"T|{x:.1f}|{y:.1f}|{size:.1f}|{color}|{font}|{align}|{safe}")

    def line(self, x1: float, y1: float, x2: float, y2: float, *,
              width: float = 1.0, color: int = TEXT) -> None:
        self.lines.append(f"L|{x1:.1f}|{y1:.1f}|{x2:.1f}|{y2:.1f}|{width:.1f}|{color}")

    def rect(self, x: float, y: float, w: float, h: float, *,
              color: int = TEXT, filled: bool = True) -> None:
        self.lines.append(f"R|{x:.1f}|{y:.1f}|{w:.1f}|{h:.1f}|{color}|{1 if filled else 0}")

    def circle(self, cx: float, cy: float, radius: float, *,
                color: int = TEXT, filled: bool = True) -> None:
        self.lines.append(f"C|{cx:.1f}|{cy:.1f}|{radius:.1f}|{color}|{1 if filled else 0}")

    def polygon(self, points: list[tuple[float, float]], *,
                 color: int = TEXT, filled: bool = True) -> None:
        pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self.lines.append(f"P|{color}|{1 if filled else 0}|{pts}")


def encode(cmds: _Cmds) -> str:
    return "\n".join(cmds.lines)


# -- panel_box / dial - render.py's _panel_box / _dial ----------------------
def _panel_box(c: _Cmds, x: float, y: float, w: float, h: float, title: str) -> None:
    c.rect(x, y, w, h, color=PANEL, filled=True)
    c.rect(x, y, w, h, color=EDGE, filled=False)
    if title:
        c.text(title, x + 8, y + 14, color=DIM)


def _dial(c: _Cmds, cx: float, cy: float, rad: float, *, ticks: int = 12) -> None:
    c.circle(cx, cy, rad, color=DIAL_BG, filled=True)
    c.circle(cx, cy, rad, color=EDGE, filled=False)
    for i in range(ticks):
        a = math.radians(i * 360 / ticks)
        x1, y1 = cx + (rad - 6) * math.sin(a), cy - (rad - 6) * math.cos(a)
        x2, y2 = cx + rad * math.sin(a), cy - rad * math.cos(a)
        c.line(x1, y1, x2, y2, color=DIM)


# -- card geometry - render.py's _card_geometry ------------------------------
def _card_geometry(x: float, y: float, w: float, h: float,
                     radius: float | None) -> tuple[float, float, float]:
    if radius is None:
        rad = min(w * 0.5, h * 0.5) - 26
        return rad, min(x + w / 2, x + rad + 20), y + h / 2 + 6
    rad = radius
    cx = x + rad + 18
    cy = _clamp(y + 19 + rad, y + rad + 5, y + h - rad - 2)
    return rad, cx, cy


# -- shared VOR/HSI face - render.py's _cdi_card -----------------------------
def _cdi_card(c: _Cmds, cx: float, cy: float, rad: float, card_up_deg: float,
               course_deg: float, deflection: float, valid: bool, color: int,
               *, to_from: str = "OFF") -> None:
    _dial(c, cx, cy, rad)
    for d in range(0, 360, 30):
        a = math.radians(d - card_up_deg)
        lab = ("N" if d == 0 else "E" if d == 90 else "S" if d == 180
               else "W" if d == 270 else str(d // 10))
        lx, ly = cx + (rad - 14) * math.sin(a), cy - (rad - 14) * math.cos(a)
        c.text(lab, lx, ly - 6, align=1)
    ca = math.radians(course_deg - card_up_deg)
    for sgn in (1, -1):
        ex, ey = cx + sgn * (rad - 4) * math.sin(ca), cy - sgn * (rad - 4) * math.cos(ca)
        c.line(cx, cy, ex, ey, width=2 if sgn == 1 else 1, color=color)
    hx, hy = cx + (rad - 4) * math.sin(ca), cy - (rad - 4) * math.cos(ca)
    c.circle(hx, hy, 4, color=color, filled=True)
    for dot in (-2, -1, 1, 2):
        px = cx + math.cos(ca) * dot * rad * 0.28
        py = cy + math.sin(ca) * dot * rad * 0.28
        c.circle(px, py, 2, color=DIM, filled=True)
    if valid:
        off = _clamp(deflection, -1, 1) * rad * 0.56
        bx, by = cx + math.cos(ca) * off, cy + math.sin(ca) * off
        c.line(
            bx - math.sin(ca) * rad * 0.5, by + math.cos(ca) * rad * 0.5,
            bx + math.sin(ca) * rad * 0.5, by - math.cos(ca) * rad * 0.5,
            width=3, color=color,
        )
    if to_from == "TO":
        c.polygon(
            [(cx + math.sin(ca) * 10, cy - math.cos(ca) * 10),
             (cx + math.sin(ca) * 10 - 6, cy - math.cos(ca) * 10 + 8),
             (cx + math.sin(ca) * 10 + 6, cy - math.cos(ca) * 10 + 8)],
            color=color, filled=True,
        )


# -- glideslope scale - render.py's _gs_scale --------------------------------
def _gs_scale(c: _Cmds, cx: float, cy: float, rad: float, gs_deflection: float,
               gs_valid: bool) -> None:
    xl, xr = cx - rad * 0.80, cx + rad * 0.80
    for dot in (-2, -1, 1, 2):
        dy = cy + dot * rad * 0.32
        c.circle(xl, dy, 2, color=WHITE, filled=True)
        c.circle(xr, dy, 2, color=WHITE, filled=True)
    c.circle(xl, cy, 3, color=WHITE, filled=False)
    c.circle(xr, cy, 3, color=WHITE, filled=False)
    if gs_valid:
        gy = cy - _clamp(gs_deflection, -1, 1) * rad * 0.64
        c.line(xl + 8, gy, xr - 8, gy, width=3, color=GPS_GREEN)
    else:
        fx, fy = cx + rad * 0.5, cy + rad * 0.42
        c.rect(fx - 13, fy - 8, 26, 16, color=OFF_FLAG_BG, filled=True)
        c.text("GS", fx, fy + 1, color=RED, align=1)


# -- station-ident blink dot - render.py's _ident_dot ------------------------
def _ident_dot(c: _Cmds, x: float, y: float, ident: str, t: float, color: int) -> None:
    if not ident or ident == "---":
        return
    from radios import morse_is_keyed

    keyed = morse_is_keyed(ident, t)
    c.circle(x, y, 3, color=color if keyed else DIM, filled=True)


# -- the HSI head itself - render.py's draw_hsi_head -------------------------
def hsi_commands(x: float, y: float, w: float, h: float, nav1, panel, t: float,
                   *, radius: float | None = None) -> str:
    """`nav1`/`panel` are exactly `World.tick()`'s returned `Frame.nav1`/
    `Frame.panel` (instruments.NavHead / instruments.Panel) - no new data
    plumbing needed, same objects render.py's draw_hsi_head already
    consumes on desktop."""
    c = _Cmds()
    _panel_box(c, x, y, w, h, "HSI")
    rad, cx, cy = _card_geometry(x, y, w, h, radius)
    hdg = panel.hsi_heading_deg if panel is not None else 0.0
    course = nav1.course_deg if nav1 is not None else hdg
    _cdi_card(
        c, cx, cy, rad, hdg, course,
        nav1.deflection if nav1 is not None else 0.0,
        nav1.valid if nav1 is not None else False,
        AMBER,
        to_from=nav1.to_from if nav1 is not None else "OFF",
    )
    c.polygon(
        [(cx, cy - rad - 1), (cx - 5, cy - rad - 10), (cx + 5, cy - rad - 10)],
        color=WHITE, filled=True,
    )
    c.text(f"{hdg:03.0f}", cx, cy - rad * 0.34 + 7, size=_FONT_LCD, color=WHITE,
           font="seven", align=1)
    if nav1 is not None and (nav1.gs_valid or nav1.is_localizer):
        _gs_scale(c, cx, cy, rad, nav1.gs_deflection, nav1.gs_valid)
        ix = cx + rad + 20
        ident = nav1.ident or "---"
        label = f"{'LOC' if nav1.is_localizer else 'VOR'} {ident}"
        c.text(label, ix, cy - rad + 10, color=CYAN)
        # render.py measures r.f_sm.size(label) exactly; no font-metrics call
        # is available here (no pygame), so this is an approximate monospace
        # width - close enough to place the ident-blink dot just past the
        # label, not pixel-exact. Fine to tighten once real fonts are wired.
        _ident_dot(c, ix + len(label) * 7 + 8, cy - rad + 4, ident, t, CYAN)
        c.text(f"CRS {course:03.0f}", ix, cy - rad + 28, color=DIM)
    return encode(c)
