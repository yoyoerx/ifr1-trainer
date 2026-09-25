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
BLACK = 0xFF000000
AP_KEY_BG = 0xFF181B20     # (24, 27, 32)
AP_KEY_OFF = 0xFF24282E    # (36, 40, 46)
AP_RDY_OFF_BG = 0xFF282C30  # (40, 44, 48)
MAGENTA = 0xFFE66ED2  # (230, 110, 210) - Garmin active-leg color

_FONT_SM = 12.0   # sizeSp for render.py's r.f_sm-equivalent labels (real: 13pt)
_FONT_MD = 15.0   # sizeSp for render.py's r.f_md-equivalent (real: 15pt)
_FONT_LG = 21.0   # sizeSp for render.py's r.f_lg-equivalent, bold (real: 21pt bold)
_FONT_LCD = 20.0  # sizeSp for r.lcd-equivalent digital readouts

# `_Cmds.text`'s `y` is the text's TOP (InstrumentCanvas.kt converts to
# Android's baseline convention) - matching render.py's pygame blit
# convention directly. render.py's `center=True` calls (r._t(..., center=
# True) / r.lcd(..., center=True)) center text on a POINT on both axes;
# Kotlin's `align` only expresses horizontal alignment, so a `center=True`
# call is ported here as `align=1` plus subtracting half the font size from
# the target y - this helper does that subtraction once, at each call site,
# rather than leaving ad hoc offsets that don't correspond to anything (a
# real on-device bug, found 2026-09-24: the AP panel's tightly-packed info
# box overlapped badly under offsets tuned by eyeballing the HSI alone).
def _centered_y(center_y: float, size: float = _FONT_SM) -> float:
    return center_y - size / 2


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
        c.text(title, x + 8, y + 4, color=DIM)


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
        c.text(lab, lx, _centered_y(ly - 6), align=1)
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
        c.text("GS", fx, _centered_y(fy - 7), color=RED, align=1)


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
    c.text(f"{hdg:03.0f}", cx, _centered_y(cy - rad * 0.34, _FONT_LCD), size=_FONT_LCD,
           color=WHITE, font="seven", align=1)
    if nav1 is not None and (nav1.gs_valid or nav1.is_localizer):
        _gs_scale(c, cx, cy, rad, nav1.gs_deflection, nav1.gs_valid)
        ix = cx + rad + 20
        ident = nav1.ident or "---"
        label = f"{'LOC' if nav1.is_localizer else 'VOR'} {ident}"
        c.text(label, ix, cy - rad, color=CYAN)
        # render.py measures r.f_sm.size(label) exactly; no font-metrics call
        # is available here (no pygame), so this is an approximate monospace
        # width - close enough to place the ident-blink dot just past the
        # label, not pixel-exact. Fine to tighten once real fonts are wired.
        _ident_dot(c, ix + len(label) * 7 + 8, cy - rad + 4, ident, t, CYAN)
        c.text(f"CRS {course:03.0f}", ix, cy - rad + 18, color=DIM)
    return encode(c)


# -- the AP programmer panel - render.py's draw_ap_panel --------------------
def ap_panel_commands(x: float, y: float, w: float, h: float, ap, t: float,
                        ias_bug: float | None = None, *, show_info: bool = True) -> str:
    """`ap` is `World.ap` (autopilot.Autopilot) directly - no new data
    plumbing needed. `t` is `World.t` (elapsed session seconds), used for
    the POH's flashing annunciations - render.py uses `pygame.time
    .get_ticks()` for the same 400ms half-period blink; `t` is this
    module's equivalent since there's no pygame clock here."""
    c = _Cmds()
    if show_info:
        info_w = min(190.0, w * 0.24)
        ap_x, ap_w = x, w - info_w - 8
        info_x = ap_x + ap_w + 8
        _panel_box(c, info_x, y, info_w, h, "")
    else:
        ap_x, ap_w = x, w
        info_x = None

    _panel_box(c, ap_x, y, ap_w, h, "")
    if ap is None:
        c.text("no autopilot", ap_x + ap_w / 2, _centered_y(y + h / 2), color=DIM, align=1)
        return encode(c)
    top = y + 8

    ready = ap.ready
    if "RDY" in ap.flashing and int(t / 0.4) % 2 == 1:
        ready = False
    c.text("S-TEC 55X", ap_x + 10, top, color=DIM)
    rl_x, rl_y, rl_w, rl_h = ap_x + 10, top + 16, 42.0, 18.0
    c.rect(rl_x, rl_y, rl_w, rl_h, color=GPS_GREEN if ready else AP_RDY_OFF_BG, filled=True)
    c.text("RDY", rl_x + rl_w / 2, _centered_y(rl_y + rl_h / 2), color=BLACK if ready else DIM, align=1)

    lat = ap.lateral.value
    vert = ap.vertical.value
    alat = ap.armed_lat.value if ap.armed_lat is not None else None
    avert = ap.armed_vert.value if ap.armed_vert is not None else None

    flashing = ap.flashing
    blink_off = int(t / 0.4) % 2 == 1

    def key(text: str, lit: bool, armed: bool, bx: list[float]) -> None:
        if text in flashing and blink_off:
            lit = armed = False
        col = GPS_GREEN if lit else (AMBER if armed else AP_KEY_OFF)
        box_x, box_y, box_w, box_h = bx[0], top + 4, 40.0, 26.0
        c.rect(box_x, box_y, box_w, box_h, color=AP_KEY_BG, filled=True)
        c.rect(box_x, box_y, box_w, box_h, color=col, filled=False)
        c.text(text, box_x + box_w / 2, _centered_y(box_y + box_h / 2),
               color=col if (lit or armed) else DIM, align=1)
        bx[0] = box_x + box_w + 5

    bx = [ap_x + 92]
    key("HDG", lat == "HDG", alat == "HDG", bx)
    key("NAV", lat == "NAV", alat == "NAV", bx)
    key("APR", lat == "APR", alat == "APR", bx)
    key("REV", lat == "REV", alat == "REV", bx)
    key("ALT", vert == "ALT", False, bx)
    key("VS", vert == "VS", False, bx)

    vlab = bx[0] + 14
    vcol = vlab + 16 + 8 + 95
    vy = top
    if vcol > ap_x + ap_w - 4:
        vlab = ap_x + 92
        vcol = vlab + 16 + 8 + 95
        vy = top + 34
    c.text("VS", vlab, vy + 2, color=DIM)
    c.text(f"{ap.vs_target:.0f}", vcol, vy, size=_FONT_LCD, font="seven",
           color=GPS_GREEN if vert in ("VS", "GS") else DIM, align=2)

    ann: list[tuple[str, int]] = []
    if ap.gpss and lat in ("NAV", "APR"):
        ann.append(("GPSS", CYAN))
    gs_blink = "GS" in flashing
    if gs_blink and blink_off:
        pass
    elif vert == "GS":
        ann.append(("GS", GPS_GREEN))
    elif avert == "GS" or gs_blink:
        ann.append(("GS ARM", AMBER))
    tr = ap.trim
    if ap.fail:
        ann.append(("FAIL", RED))
    if tr and not ("TRIM" in flashing and blink_off):
        ann.append((f"TRIM {'UP' if tr > 0 else 'DN'}", AMBER))
    ax = ap_x + 92
    ann_y = (top + 36 if vy == top else vy + 22) + (_FONT_LCD - _FONT_SM)   # clear the VS row's tall digits above
    for text, col in ann:
        c.text(text, ax, ann_y, color=col)
        # approximate monospace width, same caveat as hsi_commands' ident label
        ax += len(text) * 7 + 10

    if info_x is not None:
        # A tight vertical pitch matching render.py's real DSEG7-font info
        # box isn't reproducible without that font's real metrics (no
        # pygame here) - this uses a plainer, generously-spaced layout with
        # a smaller LCD size instead of guessing at pixel-exact overlap
        # tolerances. Same information, less visually compact.
        iy = y + 6
        pitch = 26.0
        info_lcd = 15.0
        icol = info_x + info_w - 10
        c.text("HDG BUG", info_x + 10, iy, color=DIM)
        c.text(f"{ap.heading_bug:03.0f}", icol, iy - 2, size=info_lcd,
               font="seven", color=CYAN, align=2)
        c.text("ALT SEL", info_x + 10, iy + pitch, color=DIM)
        c.text(f"{ap.alt_preselect:.0f}", icol, iy + pitch - 2, size=info_lcd,
               font="seven", color=TEXT, align=2)
        if ias_bug:
            c.text("IAS SET", info_x + 10, iy + pitch * 2, color=DIM)
            c.text(f"{ias_bug:.0f}", icol, iy + pitch * 2 - 2, size=info_lcd,
                   font="seven", color=CYAN, align=2)
    return encode(c)


# -- the GNS unit screen (default NAV page only) - render.py's _gns_unit /
# _draw_nav_default / _turn_advisory / _cdi_strip / _bezel_labels ----------
def gns_commands(x: float, y: float, w: float, h: float, gns, nav, own, panel,
                   magvar: float) -> str:
    """`gns` is `World.gns` (a `Gns530`/`Gns430` Variant); `nav`/`own`/
    `panel` are exactly `World.tick()`'s returned `Frame.nav`/`.own`/
    `.panel` - no new data plumbing needed, same objects render.py's
    `_gns_unit` already consumes on desktop.

    **Only the default NAV page renders** - Map, Flight Plan, VNAV, NAV/
    COM, WPT, NRST, AUX, and every modal dialog (PROC, DTO, confirms,
    message page) are a deliberately separate, later pass (see
    docs/ANDROID_PORT_PLAN.md §3.6 / §7). `route_event` already dispatches
    real FMS bezel-key input into `GpsNav.handle_event` correctly (proven
    by the Phase 1 core-loop milestone), so turning the FMS knob does move
    `gns.cursor.page_name` server-side - this function just doesn't have a
    body for any page but the default one yet, and always draws that one
    regardless of what page is actually selected.
    """
    key_area = 58.0   # reserved below the screen box for the bezel key row + hint text
    screen_h = h - key_area

    c = _Cmds()
    _panel_box(c, x, y, w, screen_h, "")

    cursor = gns.cursor
    group = getattr(cursor, "group_name", "NAV")
    page = getattr(cursor, "page_name", "")
    c.text(f"{group}  {page}", x + 8, y + 6, color=CYAN)
    if getattr(cursor, "cursor_on", False):
        c.text("CRSR", x + w - 8, y + 6, color=AMBER, align=2)

    cdi_h = 40.0
    b_x, b_y = x + 8, y + 28
    b_w, b_h = w - 16, screen_h - 28 - cdi_h - 8
    b_right = b_x + b_w

    # -- _draw_nav_default -------------------------------------------
    act = nav.valid
    to = nav.to_ident or "----"
    frm = nav.from_ident or "----"
    mode = nav.mode or ""
    sym = {"DTO": "D>", "OBS": "OBS", "SUSP": "SUSP", "HOLD": "HOLD"}.get(mode, "->")
    symcol = AMBER if mode in ("OBS", "SUSP", "HOLD") else (MAGENTA if act else DIM)
    c.text(sym, b_x, b_y, color=symcol)
    c.text(frm, b_x + 36, b_y, color=DIM)
    c.text(to, b_x + 86, b_y - 4, size=_FONT_LG, font="mono",
           color=MAGENTA if act else DIM)
    if getattr(gns, "obs_active", False):
        oc = (getattr(gns, "obs_course", 0.0) - magvar) % 360.0
        c.text(f"OBS {oc:03.0f}", b_right, b_y, color=AMBER, align=2)

    row_y = b_y + 34
    dy = max(15.0, min(20.0, (b_h - 40) / 6))
    room = max(2, int((b_h - 34) // dy))
    dtk = nav.dtk
    dis = nav.dist_nm
    gs = own.gs_kt
    ete = (dis / gs * 60.0) if (dis and gs > 20) else None
    xtk = nav.xtk_nm
    rows: list[tuple[str, str, int]] = [
        ("DTK", f"{(dtk - magvar) % 360.0:03.0f}" if dtk is not None else "---", TEXT),
        ("TRK", f"{(own.track_deg - magvar) % 360.0:03.0f}", TEXT),
        ("DIS", f"{dis:5.1f}nm" if dis is not None else "--.-nm", TEXT),
        ("GS", f"{gs:3.0f}kt", TEXT),
        ("ETE", f"{int(ete):02d}:{int((ete * 60) % 60):02d}" if ete else "--:--", TEXT),
    ]
    if xtk is not None:
        side = "R" if xtk > 0 else "L"
        rows.append(("XTK", f"{abs(xtk):4.2f}nm {side}", AMBER if abs(xtk) > 1.0 else TEXT))
    for i, (lab, val, col) in enumerate(rows[:room]):
        yy = row_y + i * dy
        c.text(lab, b_x, yy, color=DIM)
        c.text(val, b_right, yy - 2, size=_FONT_MD, color=col, align=2)

    # -- _turn_advisory -------------------------------------------------
    nxt = nav.next_dtk
    if nxt is not None and (nav.wpt_alert or nav.turn_now):
        mag = (nxt - magvar) % 360.0
        turning = nav.turn_now
        text = f"{'TURN TO' if turning else 'NEXT DTK'} {mag:03.0f}"
        c.text(text, x + w - 8, y + screen_h - cdi_h - 58, color=AMBER if turning else CYAN, align=2)

    # -- _cdi_strip -------------------------------------------------------
    cdi_y = y + screen_h - cdi_h
    cdi = panel.cdi
    cx, cy = x + w / 2, cdi_y + cdi_h / 2
    c.line(x + 30, cy, x + w - 30, cy, color=EDGE)
    for k in (-2, -1, 1, 2):
        dxk = k * (w / 2 - 34) / 2
        c.circle(cx + dxk, cy, 2, color=DIM, filled=True)
    src = cdi.source
    c.text(src, x + 2, cy - 14, color=GPS_GREEN if src == "GPS" else CYAN)
    svc = cdi.service
    if src == "GPS" and svc:
        c.text(svc, x + 2 + len(src) * 7 + 8, cy - 14, color=GPS_GREEN)
    fs = cdi.full_scale_nm
    if src == "GPS" and fs:
        lbl = f"{fs:.2f}" if fs < 1.0 else f"{fs:.1f}"
        c.text(lbl, x + 26, y + screen_h - 20, color=DIM)
        c.text(lbl, x + w - 26, y + screen_h - 20, color=DIM, align=2)
    if cdi.valid:
        dfl = _clamp(cdi.deflection, -1, 1)
        nx = cx + dfl * (w / 2 - 34)
        col = GPS_GREEN if src == "GPS" else CYAN
        c.line(nx, cdi_y + 6, nx, cdi_y + cdi_h - 6, width=3, color=col)
        tf = cdi.to_from
        if tf in ("TO", "FROM"):
            pts = ([(cx, cy - 9), (cx - 7, cy + 4), (cx + 7, cy + 4)] if tf == "TO"
                   else [(cx, cy + 9), (cx - 7, cy - 4), (cx + 7, cy - 4)])
            c.polygon(pts, color=col, filled=True)
            c.text("TO" if tf == "TO" else "FR", x + w - 2, cy - 14, color=col, align=2)
    else:
        c.text("--FLAG--", cx, _centered_y(cy), color=RED, align=1)

    # -- _bezel_labels (flat/no-bezel style key row) ---------------------
    key_y = y + screen_h + 4
    keys = ["D>", "MENU", "CLR", "ENT", "CRSR", "OBS", "MSG", "FPL"]
    kw = w / len(keys)
    for i, k in enumerate(keys):
        kx = x + i * kw
        c.rect(kx + 4, key_y, kw - 8, 22, color=AP_KEY_BG, filled=True)
        c.text(k, kx + kw / 2, _centered_y(key_y + 11), color=DIM, align=1)
    c.text("outer: page group / field    inner: page / value", x + 6, key_y + 26, color=DIM)
    if getattr(gns, "obs_active", False):
        obs_txt = f"OBS {getattr(gns, 'obs_course', 0.0):03.0f}"
        c.text(obs_txt, x + w - 6, key_y + 26, color=AMBER, align=2)
    return encode(c)
