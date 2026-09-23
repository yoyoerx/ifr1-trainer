"""render.py - immediate-mode pygame drawing of the 530 + instruments + map.

One :class:`Renderer`; call :meth:`Renderer.draw` once per frame with a
:class:`Scene` (everything the frame needs, already computed by the loop). No
asset pipeline, no retained scene graph - just draw calls. Colours and the
layout grid are module constants so the look is easy to tweak.

Headless-safe: nothing here opens a window; ``main.py`` owns the display. Tests
run under ``SDL_VIDEODRIVER=dummy``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from navmath import Point, arc_points, destination, great_circle_nm, initial_bearing, norm360
from gpsnav import VARIANT_530
import instruments as instr

_ASSETS = Path(__file__).resolve().parent / "assets"
_FONT_DIR = _ASSETS / "fonts"
_INSTR_DIR = _ASSETS / "instruments"
_BEZEL_SVG = _INSTR_DIR / "garmin-gns-530" / "faceplate.svg"   # 530 default
# screen cutout as (x0, y0, x1, y1) fractions of the faceplate (measured from the SVG)
_SCREEN_FRAC = (0.163, 0.050, 0.838, 0.765)
_BEZEL_ASPECT = 165.0 / 120.0        # the real 530 faceplate is landscape
_BEZEL_CACHE: dict = {}
_WX_CACHE_TTL_S = 5.0   # Weather page: how often to re-read datasrc.wx's local cache
_HOLD_TURN_RADIUS_NM = 1.0   # stylized - the real 530's moving-map hold symbol
                             # isn't drawn to true turn-radius scale either


def _hold_track_points(fix: Point, inbound_true: float, turn: str,
                        leg_nm: float, arc_steps: int = 8) -> list[Point]:
    """Points tracing a stylized hold "racetrack": outbound leg from ``fix``,
    a 180 deg turn (to the R or L side per ``turn``), the parallel inbound
    leg, and the turn back to ``fix`` - the same schematic shape a real GNS
    530 moving map draws for a loaded hold (HM/HA/HF), rather than an
    undifferentiated ring. Built by composing ``navmath.destination`` calls
    in a fix-relative along/across frame (outbound-course-aligned), same
    style as `gpsnav._expand_leg`'s synthetic-fix composition - fine at
    hold-pattern scale, no need for exact great-circle offset math."""
    outbound = norm360(inbound_true + 180.0)
    # Turning right (or left) *at the fix* to reverse from inbound to
    # outbound sweeps through the inbound course's own +90/-90 side (e.g.
    # inbound 000, right turns: 000 -> 090 -> 180 sweeps through east), so
    # that's the side the whole racetrack ends up displaced to - not
    # outbound's +/-90, which is the reciprocal side and was backwards here
    # in an earlier version of this function (verified against the "turn at
    # the fix" derivation, not just plausible-looking output).
    side = 90.0 if turn == "R" else -90.0
    across = norm360(inbound_true + side)
    sign = -1.0 if turn == "R" else 1.0
    r = _HOLD_TURN_RADIUS_NM

    def off(along_nm: float, across_nm: float) -> Point:
        p = destination(fix, outbound, along_nm) if along_nm else fix
        return destination(p, across, across_nm) if across_nm else p

    far = off(leg_nm, 0.0)
    far_off = off(leg_nm, 2.0 * r)
    near_off = off(0.0, 2.0 * r)
    center_far = off(leg_nm, r)
    center_near = off(0.0, r)

    def arc(center: Point, start_bearing: float) -> list[Point]:
        return [destination(center, norm360(start_bearing + sign * 180.0 * i / arc_steps), r)
                for i in range(1, arc_steps)]

    pts = [fix, far]
    pts += arc(center_far, norm360(across + 180.0))     # far-end turn
    pts.append(far_off)
    pts.append(near_off)
    pts += arc(center_near, across)                     # near-end turn
    pts.append(fix)
    return pts


def _pt_symbol_points(tip: Point, inbound_true: float, half_nm: float = 0.35) -> list[Point]:
    """A small chevron at a procedure-turn's synthetic "PT" point, pointing
    along the course flown inbound after the turn - distinguishes it from a
    plain waypoint dot without needing the turn's exact (unknown, here)
    outbound leg length. ``inbound_true`` is stashed on the waypoint by
    `gpsnav._expand_leg`'s PI branch."""
    back = norm360(inbound_true + 180.0)
    left = destination(tip, norm360(back + 35.0), half_nm)
    right = destination(tip, norm360(back - 35.0), half_nm)
    return [left, tip, right]


def _fpl_tag(wp) -> str:
    """Short procedure annotation for a flight-plan waypoint (GNS Table 3-2)."""
    if getattr(wp, "is_map", False):
        return "MAP"
    if getattr(wp, "hold", False):
        return "HOLD"
    if getattr(wp, "is_faf", False):
        return "FAF"
    if getattr(wp, "is_iaf", False):
        return "IAF"
    if getattr(wp, "manual", False):
        return "MAN"
    if getattr(wp, "synthetic", False):
        return "~"
    return ""


def visible_fpl_rows(n_waypoints: int, screen_rows: int, *, space_rows: int | None = None) -> int:
    """How many flight-plan entries a unit's screen shows: the waypoint count,
    capped by the variant's ``screen_rows`` and by any tighter pixel budget.
    The GNS 430 (screen_rows=5) truncates a long plan the 530 shows in full."""
    cap = screen_rows if space_rows is None else min(screen_rows, max(1, space_rows))
    return max(0, min(n_waypoints, cap))


def _bezel_svg(variant) -> Path:
    """Faceplate SVG path for a gpsnav.Variant (falls back to the 530)."""
    sub = getattr(variant, "bezel_dir", None) or "garmin-gns-530"
    return _INSTR_DIR / sub / "faceplate.svg"


def _load_bezel(size, svg_path: Path | None = None):
    """A GNS faceplate SVG (Apache-2.0, allanglen/c172-flight-sim-panel),
    recoloured from laser-cut red to bezel grey. None if the file is absent."""
    svg_path = svg_path or _BEZEL_SVG
    key = (str(svg_path), tuple(size))
    if key in _BEZEL_CACHE:
        return _BEZEL_CACHE[key]
    surf = None
    if svg_path.is_file():
        try:
            img = pygame.image.load_sized_svg(str(svg_path), size)
            img = img.convert_alpha()
            img = pygame.transform.grayscale(img)          # red strokes -> dark grey, alpha kept
            img.fill((150, 156, 162, 0), special_flags=pygame.BLEND_RGB_ADD)  # lift to visible
            surf = img
        except Exception:      # pragma: no cover - SVG backend / corrupt file
            surf = None
    _BEZEL_CACHE[key] = surf
    return surf


def _load_font(filename: str, size: int, *, sysfallback: str, bold: bool = False):
    """Bundled TTF from assets/fonts, or a system fallback if it's missing."""
    path = _FONT_DIR / filename
    if path.is_file():
        try:
            return pygame.font.Font(str(path), size)
        except Exception:      # pragma: no cover - corrupt/locked file
            pass
    return pygame.font.SysFont(sysfallback, size, bold=bold)

# --------------------------------------------------------------------------- #
# palette / layout
# --------------------------------------------------------------------------- #
BG = (8, 10, 12)
PANEL = (18, 22, 26)
EDGE = (60, 68, 76)
TEXT = (210, 220, 225)
DIM = (120, 130, 138)
GPS_GREEN = (120, 230, 140)
CYAN = (90, 210, 235)
MAGENTA = (230, 110, 210)     # Garmin active-leg colour
AMBER = (240, 180, 60)
WHITE = (235, 240, 245)

# Nearest Airspace Page row wording (Pilot's Guide p.121-122) - distinct from the MSG-queue alert text
# (gpsnav._AIRSPACE_ALERT_MSG), which quotes different phrasing for the same four conditions.
_AIRSPACE_STATUS_LABEL = {
    "inside": "Inside of airspace",
    "near_ahead": "Ahead < 2nm",
    "near": "Within 2nm of airspace",
    "ahead": "Ahead",
}
RED = (235, 90, 80)

WIN_W, WIN_H = 1000, 640
GNS_W = 430                    # left column: the 530 unit
MAP_MARGIN = 12

# "stack" layout: everything relevant to instrument flight on one page (see
# WORKING.md's "Active project" section and ARCHITECTURE.md sec.8) - a taller,
# wider window than the other layouts comfortably need. main.py resizes the
# window to this only while `stack` is the active layout.
STACK_W, STACK_H = 1360, 860
_STACK_TABS = ("WX", "MAP", "PLATE", "SETTINGS")


@dataclass
class Scene:
    """Everything a frame draws. Built by the loop each tick."""

    own: object                       # sim_model.Ownship
    nav: object                       # gns530.NavState
    panel: object                     # instruments.Panel
    gns: object                       # gpsnav.GpsNav (flight plan / page / cursor)
    db: object = None                 # navdata.NavDatabase (nearby fixes); optional
    variant: object = None            # gpsnav.Variant; None -> read gns.variant / 530
    magvar: float = 0.0               # local variation, east positive (true = mag + magvar)
    map_range_nm: float = 20.0
    autopilot: bool = False
    fps: float = 0.0
    nearby: list = field(default_factory=list)   # cached [(ident, Point, kind)]
    messages: list = field(default_factory=list)  # pending system messages
    show_messages: bool = False        # draw the Message page over the screen
    baro_inhg: float = 29.92           # altimeter setting (steam layout)
    shift_hint: str = ""              # e.g. "SHIFT CRS1" when the knob shift is latched
    selector_mode: str = ""          # IFR-1 mode selector position (COM1 / NAV1 / ...)
    ias_target: float = 0.0          # pseudo speed-manager set-point, kt IAS
    ias_managed: bool = False        # True once the pilot has trimmed it
    t: float = 0.0                   # elapsed session seconds - drives the VLOC ident blink
    time_warp: int = 1               # simulation speed multiplier (keys 1/2/3/4 -> 1/5/10/20x)

    # -- steam-panel extras (layout="steam") --
    layout: str = "gps"               # "gps" | "steam" | "dual"
    sixpack: object = None            # instruments.SixPack
    nav1_head: object = None          # instruments.NavHead
    nav2_head: object = None
    nav1_hsi: bool = False            # draw NAV1 as an HSI instead of a plain CDI
    ap: object = None                 # autopilot.Autopilot
    radios: object = None             # radios.RadioStack

    # -- second FMS unit (layout="dual" and "stack") --
    gns2: object = None                # gpsnav.GpsNav for FMS2, or None (single-unit)
    panel2: object = None              # instruments.Panel driven by FMS2/NAV2 (--dual)

    # -- stack-panel extras (layout="stack") --
    stack_tab: str = "WX"             # which left-column tab is showing
    wind_from_deg: float = 0.0        # World.wind_from, for the SETTINGS tab
    wind_kt: float = 0.0              # World.wind_kt, for the SETTINGS tab
    wind_aloft: bool = False          # wind shown is from a winds-aloft profile
    plate_filter: str = "ALL"         # PLATE tab: "ALL" or one chart_code (IAP/DP/STAR/...)


class Renderer:
    def __init__(self, surface: pygame.Surface):
        self.surf = surface
        pygame.font.init()
        _mono = "consolas,dejavusansmono,monospace"
        # B612 Mono (SIL OFL) - designed for cockpit legibility; falls back to Consolas
        self.f_sm = _load_font("B612Mono-Regular.ttf", 13, sysfallback=_mono)
        self.f_md = _load_font("B612Mono-Regular.ttf", 15, sysfallback=_mono)
        self.f_lg = _load_font("B612Mono-Bold.ttf", 21, sysfallback=_mono, bold=True)
        # DSEG7 Classic (SIL OFL) - 7-segment LCD look, DIGITS / '.' / ':' / '-' ONLY
        self.f_lcd = _load_font("DSEG7Classic-Regular.ttf", 16, sysfallback=_mono)
        self.f_lcd_lg = _load_font("DSEG7Classic-Bold.ttf", 23, sysfallback=_mono, bold=True)
        # Weather page: cached cheap reads of datasrc.wx's local JSON cache, not
        # re-read every frame - keyed (station, kind) -> (report, read-at-seconds)
        self._wx_read_cache: dict[tuple[str, str], tuple] = {}
        # Charts page: the d-TPP chart index covers every US airport (tens of
        # thousands of records) - loaded once and held for the Renderer's
        # lifetime, not re-parsed every frame. None = not tried yet; [] = tried
        # and nothing cached (or a parse error).
        self._dtpp_index: list | None = None
        # "stack" layout: rects a MOUSEBUTTONDOWN can hit, rebuilt every frame
        # `_stack_layout` runs - main.py's mouse handler reads this dict right
        # after `draw()` to route a click (tab / AP-bug +- / etc.) back into
        # `ui`/`w`. Not populated by any other layout.
        self._stack_hit: dict[str, pygame.Rect] = {}
        # PLATE tab: rasterized approach-plate pages, keyed by pdf path ->
        # pygame.Surface. Populated off-thread by main._open_stack_plate;
        # `_draw_stack_plate` only ever reads this cache, never fetches.
        self._plate_cache: dict[str, pygame.Surface] = {}
        self._plate_loading: set[str] = set()
        self._plate_error: dict[str, str] = {}

    def lcd(self, value, x, y, *, color=GPS_GREEN, big=False, right=False, center=False):
        """Digital readout in the LCD font. `value` must be digits / '.' / ':' / '-'."""
        return self._t(str(value), x, y, font=self.f_lcd_lg if big else self.f_lcd,
                       color=color, right=right, center=center)

    # -- public ---------------------------------------------------------
    def draw(self, sc: Scene) -> None:
        self.surf.fill(BG)
        self._annunciator_bar(sc)
        if sc.layout == "steam":
            self._steam_layout(sc)
        elif sc.layout == "stack":
            self._stack_layout(sc)
        elif sc.layout == "dual" and sc.gns2 is not None:
            self._dual_layout(sc)
        else:
            self._gns_unit(sc)
            self._map(sc)
            self._hsi(sc)

    # -- dual FMS layout (two stacked GNS units) ------------------------
    def _dual_layout(self, sc: Scene) -> None:
        """Two GNS units sharing the left column, stacked one over the other -
        a real dual 530/430 stack. FMS1 (`sc.gns`, on top) drives the moving
        map + HSI on the right exactly as the single-unit "gps" layout does;
        FMS2 (`sc.gns2`, on the bottom) is a second, independent unit with its
        own flight plan / cursor / nav state, reachable only by putting the
        IFR-1's mode selector on FMS2 (or the keyboard's FMS-unit toggle)."""
        import dataclasses
        x0, y0, w = 8, 32, GNS_W - 16
        gap = 8
        total_h = WIN_H - 44
        h1 = (total_h - gap) // 2
        h2 = total_h - gap - h1
        self._gns_unit(sc, box=(x0, y0, w, h1))
        sc2 = self._unit2_scene(sc)
        self._gns_unit(sc2, box=(x0, y0 + h1 + gap, w, h2))
        self._map(sc)
        self._hsi(sc)

    # -- "stack" layout: everything relevant to IFR flight, one page -------
    def _stack_layout(self, sc: Scene) -> None:
        """Three columns (see WORKING.md's "Active project" section /
        ARCHITECTURE.md sec.8 for the full design record):

        - **left**: a tabbed WX/MAP/PLATE/SETTINGS reference panel, plus the
          HDG/IAS/ALT autopilot-bug boxes underneath it
        - **middle**: NAV1/NAV2 as round Bendix/King-style CDI+GS+OBS heads,
          plus a standalone heading indicator with a bug
        - **right**: GNS 530 + GNS 430 (flat vector skin, COM/NAV frequency
          annunciated), the radio/transponder strip, and the S-TEC 55X AP
          programmer, one continuous column

        The only layout with any mouse surface: rebuilds `self._stack_hit`
        (rect name -> pygame.Rect) every call for `main.py`'s
        MOUSEBUTTONDOWN handler to hit-test against."""
        import dataclasses
        self._stack_hit.clear()
        W, H = self.surf.get_size()
        gap = 8
        top = 30
        bottom_margin = 8
        right_w = 380
        # `draw_nav_head` left-biases its round card (`_card_geometry`) - the
        # dial itself is governed by the box's *height*, not its width (see
        # `_card_geometry`), so past the point where the info column's text
        # stops, extra width is just dead panel background (playtest: "boxes
        # are way too wide... shrink more right"). 380px leaves the NAV1/2
        # info column a small margin and still gives the HDG box's own info
        # column (`_stack_hdg_actuals`, to the right of its dial) enough room.
        # Fixed, not W-derived, so the middle column doesn't balloon on a
        # wider window - the tab panel gets whatever that leaves instead.
        mid_w = 380
        left_w = W - right_w - mid_w - gap * 4
        mid_x = left_w + gap * 2

        # -- right column: the avionics stack ---------------------------
        rx = W - right_w - gap
        avail = H - top - bottom_margin
        # ap_h a bit taller than steam's AP box needs: at stack's narrower
        # column width, draw_ap_panel's VS window wraps onto its own line
        # below the mode buttons (see draw_ap_panel's ap_rect.right check).
        radio_h, ap_h = 84, 112
        n_gns = 2 if sc.gns2 is not None else 1
        # Single unit: the slot a second GNS would take goes to a COM2/NAV2
        # tuning panel, as tall as the autopilot (`--dual` tunes those via
        # FMS2 instead, so it has no such panel).
        radio2_h = ap_h if sc.gns2 is None else 0
        n_blocks = n_gns + 1 + (1 if radio2_h else 0)
        gns_h = (avail - radio_h - ap_h - radio2_h - gap * n_blocks) // n_gns
        radios = sc.radios
        self._gns_unit(sc, box=(rx, top, right_w, gns_h), no_bezel=True,
                       freq=(radios.com1, radios.nav1, "1") if radios else None)
        y = top + gns_h + gap
        if sc.gns2 is not None:
            sc2 = self._unit2_scene(sc)
            self._gns_unit(sc2, box=(rx, y, right_w, gns_h), no_bezel=True,
                           freq=(radios.com2, radios.nav2, "2") if radios else None)
            y += gns_h + gap
        if radio2_h:
            self._stack_radio2(sc, pygame.Rect(rx, y, right_w, radio2_h))
            y += radio2_h + gap
        self._stack_xpdr(sc, pygame.Rect(rx, y, right_w, radio_h))
        y += radio_h + gap
        # show_info=False: the HDG-bug/ALT-preselect readout this panel
        # would otherwise draw in its own side box is edited under the tab
        # column instead (`_stack_setpoint_bugs`) and shown as an actual
        # next to the HDG indicator (`_stack_hdg_actuals`) - drawing it here
        # too would be a third copy, and the 6-button mode row didn't fit
        # next to a second box at this layout's column width anyway
        # (playtest: "overlapping of the AP and things at the bottom").
        draw_ap_panel(self.surf, pygame.Rect(rx, y, right_w, ap_h), sc.ap, sc.magvar, self,
                     show_info=False)

        # -- middle column: instrument cluster ---------------------------
        inst_h = (avail - gap * 2) // 3
        n1 = pygame.Rect(mid_x, top, mid_w, inst_h)
        n2 = pygame.Rect(mid_x, n1.bottom + gap, mid_w, inst_h)
        hd = pygame.Rect(mid_x, n2.bottom + gap, mid_w, avail - inst_h * 2 - gap * 2)
        scene_t = getattr(sc, "t", 0.0)
        nav1_nh, nav1_kind = self._nav1_view(sc)
        draw_nav_head(self.surf, n1, nav1_nh, "NAV1", self, t=scene_t, source_kind=nav1_kind)
        nav2_nh, nav2_kind = self._nav2_view(sc)
        draw_nav_head(self.surf, n2, nav2_nh, "NAV2", self, t=scene_t, source_kind=nav2_kind)
        draw_hdg_indicator(self.surf, hd, sc.sixpack, self,
                           hdg_bug=getattr(sc.ap, "heading_bug", None) if sc.ap else None)
        # Actual HDG/IAS/ALT, next to the HDG dial - the same placement
        # NAV1/NAV2 use for their own OBS readout - not the editable
        # autopilot bugs (those live under the tab column below instead, so
        # this column reads as "what the aircraft is actually doing" without
        # a click target mixed in). `sc.sixpack` is the same actual-value
        # source `draw_six_pack` itself reads.
        rad, cx, cy = _card_geometry(hd, None)
        info_x = int(cx + rad + 20)
        info_y = int(cy - rad)
        self._stack_hdg_actuals(sc, pygame.Rect(info_x, info_y, hd.right - info_x - 8,
                                                hd.bottom - info_y - 8))

        # -- left column: tabbed reference panel + AP set-point bugs -----
        tab_h = 26
        bug_h = 74
        tabs_rect = pygame.Rect(gap, top, left_w, tab_h)
        content_rect = pygame.Rect(gap, top + tab_h + 4, left_w,
                                   avail - tab_h - 4 - bug_h - gap)
        bugs_rect = pygame.Rect(gap, content_rect.bottom + gap, left_w, bug_h)
        self._stack_tabs(sc, tabs_rect)
        self._stack_tab_content(sc, content_rect)
        self._stack_setpoint_bugs(sc, bugs_rect)

    def _stack_radio2(self, sc: Scene, rect: pygame.Rect) -> None:
        """COM2 / NAV2 tuning panel (single-unit `stack` only): active and
        standby frequency, a flip-flop button, and standby tuning buttons
        (MHz and kHz steps) - click targets ``radio:<com2|nav2>:<swap|
        mhz-|mhz+|khz-|khz+>`` handled by `main._on_stack_click`. Same
        radios the IFR-1's COM2/NAV2 modes tune, so both stay in step."""
        _panel_box(self.surf, rect, "COM2 / NAV2", self)
        radios = sc.radios
        if radios is None:
            return
        row_h = (rect.h - 24) // 2
        rows = (("com2", "COM2", radios.com2, "{:07.3f}", GPS_GREEN),
                ("nav2", "NAV2", radios.nav2, "{:06.2f}", CYAN))
        for i, (key, label, rx_, fmt, col) in enumerate(rows):
            y = rect.y + 22 + i * row_h
            self._t(label, rect.x + 8, y + 4, font=self.f_sm, color=DIM)
            self.lcd(fmt.format(rx_.active_mhz), rect.x + 50, y, color=col)
            sw = pygame.Rect(rect.x + 138, y, 26, 20)
            self.lcd(fmt.format(rx_.standby_mhz), rect.x + 170, y, color=AMBER)
            btns = [(sw, "<>", "swap")]
            bx = rect.x + 262
            for txt, name in (("M-", "mhz-"), ("M+", "mhz+"), ("k-", "khz-"), ("k+", "khz+")):
                btns.append((pygame.Rect(bx, y, 26, 20), txt, name))
                bx += 29
            for rct, txt, name in btns:
                pygame.draw.rect(self.surf, PANEL, rct, border_radius=3)
                pygame.draw.rect(self.surf, EDGE, rct, width=1, border_radius=3)
                self._t(txt, rct.centerx, rct.y + 3, font=self.f_sm, color=TEXT, center=True)
                self._stack_hit[f"radio:{key}:{name}"] = rct

    def _stack_xpdr(self, sc: Scene, rect: pygame.Rect) -> None:
        """A compact transponder box - just the squawk/mode, restyled to sit
        directly under the GNS pair as one continuous column (see
        WORKING.md). COM/NAV frequencies are annunciated on the GNS screens
        themselves in this layout (`_gns_unit`'s ``freq`` arg), not repeated
        here."""
        _panel_box(self.surf, rect, "XPDR", self)
        radios = sc.radios
        if radios is None:
            return
        xpdr = radios.xpdr
        selector_mode = getattr(sc, "selector_mode", "")
        xpdr_sel = selector_mode == "XPDR"
        self.lcd(xpdr.squawk, rect.x + 12, rect.y + 22, big=True, color=AMBER)
        self._t(xpdr.mode + ("  ID" if getattr(xpdr, "identing", False) else ""),
               rect.right - 10, rect.y + 22, font=self.f_md, color=AMBER, right=True)
        src = getattr(getattr(sc.gns, "cdi_source", None), "value", None) or \
            getattr(sc.gns, "cdi_source", "GPS")
        self._t(f"CDI SRC: {src}", rect.right - 10, rect.bottom - 18,
               font=self.f_sm, color=DIM, right=True)

    def _stack_tabs(self, sc: Scene, rect: pygame.Rect) -> None:
        cw = rect.w / len(_STACK_TABS)
        for i, name in enumerate(_STACK_TABS):
            tr = pygame.Rect(int(rect.x + i * cw), rect.y, int(cw) - 4, rect.h)
            on = name == sc.stack_tab
            pygame.draw.rect(self.surf, (36, 60, 48) if on else PANEL, tr, border_radius=4)
            pygame.draw.rect(self.surf, GPS_GREEN if on else EDGE, tr, width=1, border_radius=4)
            self._t(name, tr.centerx, tr.centery - 7, font=self.f_sm,
                   color=GPS_GREEN if on else DIM, center=True)
            self._stack_hit[f"tab:{name}"] = tr

    def _stack_tab_content(self, sc: Scene, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.surf, (4, 8, 6), rect)
        pygame.draw.rect(self.surf, EDGE, rect, width=1)
        inner = rect.inflate(-16, -12)
        tab = sc.stack_tab
        if tab == "WX":
            self._draw_aux_weather(sc, inner)
        elif tab == "MAP":
            self._map(sc, rect=rect.inflate(-4, -4))
        elif tab == "PLATE":
            self._draw_stack_plate(sc, inner)
        elif tab == "SETTINGS":
            self._draw_stack_settings(sc, inner)

    def _draw_stack_plate(self, sc: Scene, rect: pygame.Rect) -> None:
        """Renders the selected AUX>Charts plate inline (rasterized by
        `pypdfium2`) instead of handing it to the OS's PDF viewer - the
        "stack" layout's own real behavior change. The fetch+rasterize runs
        off-thread (`main._open_stack_plate`, same pattern as
        `main._open_selected_chart`); this only ever reads
        `self._plate_cache`/`self._plate_loading`/`self._plate_error`, which
        that worker populates - never blocking I/O in a draw call."""
        gns = sc.gns
        idents = gns.wx_station_idents() if hasattr(gns, "wx_station_idents") else []
        if not idents:
            self._t("no flight-plan airports", rect.x, rect.y, font=self.f_sm, color=DIM)
            return
        ai = max(0, min(len(idents) - 1, getattr(gns, "chart_airport_sel", 0)))
        y = rect.y
        x = rect.x
        for i, ident in enumerate(idents):
            col = GPS_GREEN if i == ai else DIM
            r = self._t(f" {ident} ", x, y, font=self.f_sm, color=col)
            self._stack_hit[f"plate:airport:{i}"] = r      # click to switch airport
            x = r.right + 2
        y += 18
        ident = idents[ai]
        charts = self._dtpp_charts_for(ident)
        if not charts:
            self._t(f"no charts cached for {ident}", rect.x, y, font=self.f_sm, color=DIM)
            self._t("datasrc.dtpp update-index", rect.x, y + 16,
                   font=self.f_sm, color=DIM)
            return
        ci = max(0, min(len(charts) - 1, getattr(gns, "chart_sel", 0)))
        if len(charts) > 1:                         # multiple plates for this airport
            # A bare row of `chart_code`s (playtest: "list of procedures is
            # redundant/overflows with STR STR STR IAP IAP DP DP DP DP DP")
            # was both meaningless (every ILS/RNAV/VOR approach shows the
            # same "IAP") and could run off the panel for a busy airport.
            # Filter chips (one per distinct code actually present, plus
            # ALL) narrow the list first; the list itself then shows the
            # full chart name, not just its category, so entries are
            # distinguishable - a scrolling window (same technique as
            # `_draw_nrst_page`) keeps a long filtered list on-screen.
            codes = sorted({c.chart_code for c in charts})
            flt = getattr(sc, "plate_filter", "ALL")
            if len(codes) > 1:
                x = rect.x
                for label in ("ALL", *codes):
                    col = GPS_GREEN if flt == label else DIM
                    r = self._t(f" {label} ", x, y, font=self.f_sm, color=col)
                    self._stack_hit[f"plate:filter:{label}"] = r
                    x = r.right + 2
                y += 16
            else:
                flt = "ALL"

            visible = [c for c in charts if flt == "ALL" or c.chart_code == flt] or charts
            if charts[ci] not in visible:
                # the active filter just hid the current selection - snap to
                # the filtered list's first entry so what's highlighted and
                # what's about to load stay the same chart (same reasoning
                # as `_draw_aux_weather` writing a clamped `wx_scroll` back)
                ci = charts.index(visible[0])
                gns.chart_sel = ci
            sel_row = visible.index(charts[ci])

            room = 4
            top = max(0, min(max(0, len(visible) - room), sel_row - room // 2))
            list_top = y
            for row, c in enumerate(visible[top:top + room], start=top):
                i = charts.index(c)
                picked = i == ci
                col = AMBER if picked else TEXT
                mk = ">" if picked else " "
                r = self._t(f"{mk}{c.chart_code:<4} {c.chart_name}", rect.x, y,
                           font=self.f_sm, color=col)
                self._stack_hit[f"plate:chart:{i}"] = r     # click to switch chart
                y += 14
            if len(visible) > room:
                more = ("^" if top > 0 else " ") + ("v" if top + room < len(visible) else " ")
                self._t(more, rect.right - 2, list_top, font=self.f_sm, color=AMBER, right=True)
            y += 4
        chart = charts[ci]
        key = chart.pdf_name
        img = self._plate_cache.get(key)
        if img is not None:
            avail = pygame.Rect(rect.x, y, rect.w, rect.bottom - y)
            scale = min(avail.w / img.get_width(), avail.h / img.get_height(), 1.0)
            sz = (max(1, int(img.get_width() * scale)), max(1, int(img.get_height() * scale)))
            shown = pygame.transform.smoothscale(img, sz) if scale != 1.0 else img
            self.surf.blit(shown, (avail.x, avail.y))
        elif key in self._plate_error:
            self._t(f"plate failed: {self._plate_error[key]}", rect.x, y,
                   font=self.f_sm, color=RED)
        elif key in self._plate_loading:
            self._t("loading plate...", rect.x, y, font=self.f_sm, color=DIM)
        else:
            self._stack_hit["plate:load"] = pygame.Rect(rect.x, y, rect.w, 22)
            self._t("click to load plate", rect.x, y, font=self.f_sm, color=AMBER)

    def _draw_stack_settings(self, sc: Scene, rect: pygame.Rect) -> None:
        """Application-level settings a pilot would plausibly want to change
        *in flight* rather than only via a CLI flag at launch (see
        WORKING.md) - wind + time-warp today; the general home for any more
        of these going forward. Not the GNS's own CDI/Alarms Setup page,
        which stays on the GNS (AUX>Setup) where it already works."""
        y = rect.y
        self._t("WIND (winds aloft at current altitude; +/- sets a uniform wind)"
                if sc.wind_aloft else "WIND", rect.x, y, font=self.f_sm, color=DIM)
        y += 16
        row = pygame.Rect(rect.x, y, rect.w, 22)
        self._t(f"FROM {sc.wind_from_deg:03.0f} deg", row.x, row.y, font=self.f_md, color=TEXT)
        self._stack_step_buttons(row, "wind_dir", y)
        y += 26
        row = pygame.Rect(rect.x, y, rect.w, 22)
        self._t(f"SPEED {sc.wind_kt:.0f} kt", row.x, row.y, font=self.f_md, color=TEXT)
        self._stack_step_buttons(row, "wind_kt", y)
        y += 34

        self._t("TIME WARP", rect.x, y, font=self.f_sm, color=DIM)
        y += 16
        cw = rect.w / 4
        for i, lvl in enumerate((1, 5, 10, 20)):
            tr = pygame.Rect(int(rect.x + i * cw), y, int(cw) - 4, 24)
            on = getattr(sc, "time_warp", 1) == lvl
            pygame.draw.rect(self.surf, (36, 60, 48) if on else PANEL, tr, border_radius=4)
            pygame.draw.rect(self.surf, GPS_GREEN if on else EDGE, tr, width=1, border_radius=4)
            self._t(f"{lvl}x", tr.centerx, tr.centery - 7, font=self.f_sm,
                   color=GPS_GREEN if on else DIM, center=True)
            self._stack_hit[f"warp:{lvl}"] = tr

    def _stack_step_buttons(self, row: pygame.Rect, key: str, y: int) -> None:
        """A pair of small -/+ buttons right-aligned in ``row``, registered
        into ``self._stack_hit`` as ``f"{key}:-"``/``f"{key}:+"``."""
        bw = 26
        plus = pygame.Rect(row.right - bw, y, bw, 22)
        minus = pygame.Rect(row.right - bw * 2 - 4, y, bw, 22)
        for rct, txt, name in ((minus, "-", f"{key}:-"), (plus, "+", f"{key}:+")):
            pygame.draw.rect(self.surf, PANEL, rct, border_radius=4)
            pygame.draw.rect(self.surf, EDGE, rct, width=1, border_radius=4)
            self._t(txt, rct.centerx, rct.y + 3, font=self.f_sm, color=TEXT, center=True)
            self._stack_hit[name] = rct

    def _stack_hdg_actuals(self, sc: Scene, rect: pygame.Rect) -> None:
        """Actual HDG/IAS/ALT, read-only, next to the heading indicator's
        dial - the same placement NAV1/NAV2 use for their own OBS readout
        (playtest: "actual IAS, HDG, ALT should be displayed next to the
        heading indicator, in placement similar to the OBS displays next to
        NAV1/NAV2" - a follow-up clarifying these should be *actuals*, not
        the autopilot's set points, which moved to `_stack_setpoint_bugs`
        instead). ``rect`` is the space to the dial's right, inside the HDG
        box, top-aligned with the dial itself (`_stack_layout` passes
        ``cy - rad``, same as `draw_nav_head`'s own info column - not the
        box's top edge, which would land level with the panel title).
        Values come from `sc.sixpack`, the same actual-value snapshot
        `draw_six_pack` itself reads, so this always agrees with the six-pack
        on any layout that shows both."""
        sp = sc.sixpack
        specs = [
            ("HDG", f"{getattr(sp, 'heading_deg', 0):03.0f}" if sp else "---"),
            ("IAS", f"{getattr(sp, 'airspeed_kt', 0):.0f}" if sp else "---"),
            ("ALT", f"{getattr(sp, 'altitude_ft', 0):.0f}" if sp else "---"),
        ]
        row_h = max(20, rect.h // 3)
        y = rect.y
        for label, val in specs:
            self._t(label, rect.x, y, font=self.f_sm, color=DIM)
            self.lcd(val, rect.x + 30, y - 2, color=TEXT)
            y += row_h

    def _stack_setpoint_bugs(self, sc: Scene, rect: pygame.Rect) -> None:
        """HDG/IAS/ALT autopilot target bugs - shown *and* directly editable
        here (click +/-), not read-only readouts, back under the tabbed
        column (see WORKING.md / FINDINGS.md F32) - the actual values these
        set points steer toward are shown next to the HDG dial instead
        (`_stack_hdg_actuals`), so this row is unambiguously "what you're
        telling the autopilot", not "what the aircraft is doing"."""
        cw = rect.w / 3
        specs = [
            ("HDG", f"{getattr(sc.ap, 'heading_bug', 0):03.0f}", "hdg"),
            ("IAS", f"{sc.ias_target:.0f}" if getattr(sc, "ias_managed", False) else "---", "ias"),
            ("ALT", f"{getattr(sc.ap, 'alt_preselect', 0):.0f}", "alt"),
        ]
        for i, (label, val, key) in enumerate(specs):
            br = pygame.Rect(int(rect.x + i * cw), rect.y, int(cw) - 6, rect.h)
            _panel_box(self.surf, br, label, self)
            self.lcd(val, br.centerx, br.centery - 2, color=CYAN, center=True)
            bw = 28
            minus = pygame.Rect(br.x + 6, br.bottom - 26, bw, 20)
            plus = pygame.Rect(br.right - bw - 6, br.bottom - 26, bw, 20)
            for rct, txt, sign in ((minus, "-", f"bug:{key}:-"), (plus, "+", f"bug:{key}:+")):
                pygame.draw.rect(self.surf, PANEL, rct, border_radius=4)
                pygame.draw.rect(self.surf, EDGE, rct, width=1, border_radius=4)
                self._t(txt, rct.centerx, rct.y + 2, font=self.f_sm, color=TEXT, center=True)
                self._stack_hit[sign] = rct

    # -- steam-gauge layout ------------------------------------------
    def _steam_layout(self, sc: Scene) -> None:
        gap = 8
        # top band: a large six-pack on the left, NAV1 over NAV2 on the right.
        top_h = 402
        sp_rect = pygame.Rect(8, 26, 624, top_h)
        if sc.sixpack is not None:
            draw_six_pack(self.surf, sp_rect, sc.sixpack, self,
                          getattr(sc, "baro_inhg", 29.92),
                          hdg_bug=getattr(sc.ap, "heading_bug", None) if sc.ap else None,
                          spd_bug=(sc.ias_target if getattr(sc, "ias_managed", False)
                                   else None))
        # every round instrument uses the same diameter as one six-pack cell
        gauge_rad = six_pack_gauge_radius(sp_rect)

        nav_x = sp_rect.right + gap
        nav_w = WIN_W - nav_x - 8
        nav_h = (top_h - gap) // 2
        n1 = pygame.Rect(nav_x, sp_rect.y, nav_w, nav_h)
        n2 = pygame.Rect(nav_x, n1.bottom + gap, nav_w, nav_h)
        scene_t = getattr(sc, "t", 0.0)
        nav1_nh, nav1_kind = self._nav1_view(sc)
        if sc.nav1_hsi:
            draw_hsi_head(self.surf, n1, nav1_nh, sc.panel, "NAV1 / HSI", self,
                          radius=gauge_rad, t=scene_t)
        else:
            draw_nav_head(self.surf, n1, nav1_nh, "NAV1", self, radius=gauge_rad, t=scene_t,
                          source_kind=nav1_kind)
        nav2_nh, nav2_kind = self._nav2_view(sc)
        draw_nav_head(self.surf, n2, nav2_nh, "NAV2", self, radius=gauge_rad, t=scene_t,
                      source_kind=nav2_kind)

        # bottom: full-width radio strip, autopilot programmer beneath it.
        ap_h = 64
        rad_rect = pygame.Rect(8, sp_rect.bottom + gap, WIN_W - 16,
                               WIN_H - sp_rect.bottom - gap * 2 - ap_h - 8)
        draw_radio_strip(self.surf, rad_rect, sc.radios, sc.gns, self,
                         selector_mode=getattr(sc, "selector_mode", ""),
                         shift_mode=(getattr(sc, "selector_mode", "")
                                     if getattr(sc, "shift_hint", "") else ""),
                         t=scene_t)

        ap_rect = pygame.Rect(8, rad_rect.bottom + gap, WIN_W - 16, ap_h)
        draw_ap_panel(self.surf, ap_rect, sc.ap, sc.magvar, self,
                      ias_bug=(sc.ias_target if getattr(sc, "ias_managed", False)
                               else None))

    # -- text helper --------------------------------------------------
    def _t(self, s, x, y, *, font=None, color=TEXT, right=False, center=False):
        font = font or self.f_md
        img = font.render(str(s), True, color)
        r = img.get_rect()
        if right:
            r.topright = (x, y)
        elif center:
            r.midtop = (x, y)
        else:
            r.topleft = (x, y)
        self.surf.blit(img, r)
        return r

    def _wrap(self, text: str, font, max_w: int) -> list[str]:
        """Greedy word-wrap ``text`` to fit ``max_w`` px in ``font`` - for the
        long free-text METAR/TAF strings, which have no line breaks of their
        own. Falls back to a hard character break for a single word wider
        than ``max_w`` (shouldn't happen with real METAR/TAF groups)."""
        words = text.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and font.size(trial)[0] > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    # -- top annunciator strip -------------------------------------
    def _annunciator_bar(self, sc: Scene):
        pygame.draw.rect(self.surf, PANEL, (0, 0, WIN_W, 24))
        pygame.draw.line(self.surf, EDGE, (0, 24), (WIN_W, 24))
        x = 8
        anns = list(getattr(sc.nav, "annunciators", ()) or ())
        if sc.autopilot:
            anns.insert(0, "AP-NAV")
        sbas = getattr(getattr(sc, "gns", None), "sbas", "OK")
        if sbas != "OK" and getattr(getattr(getattr(sc, "gns", None), "variant", None), "waas", False):
            anns.insert(0, f"SBAS {sbas}")                # trainer control, not something the unit shows
        if getattr(sc, "messages", None):
            anns.insert(0, "MSG")
        if getattr(sc, "shift_hint", ""):
            anns.insert(0, sc.shift_hint)
        if getattr(sc, "time_warp", 1) > 1:      # hard to miss - flying faster than real time
            anns.insert(0, f"WARP {sc.time_warp}x")
        for a in anns:
            col = (AMBER if "EXPIRED" in a or a == "MSG" or a.startswith("WARP") or a.startswith("SBAS")
                   else (CYAN if a in ("WPT", "AP-NAV") or a.startswith("SHIFT") else WHITE))
            r = self._t(a, x, 4, font=self.f_sm, color=col)
            x = r.right + 14
        self._t(f"{sc.fps:4.0f} fps", WIN_W - 8, 4, font=self.f_sm, color=DIM, right=True)

    # -- the GPS unit (left column: 530 or 430) ------------------------
    def _variant(self, sc: Scene):
        return sc.variant or getattr(sc.gns, "variant", None) or VARIANT_530

    def _nav1_view(self, sc: Scene):
        """What the round NAV1 head should actually display: this trainer's
        NAV1 head is wired like a real GPS-slaved analog CDI, not an
        independent VOR/LOC receiver - it follows the GNS's own CDI/VLOC
        switch (`nav.cdi_source`), exactly like `panel.cdi` (the CDI strip
        on the GNS screen) and the autopilot's NAV mode (F27) already do.
        Returns ``(nav_head, source_kind)`` - `source_kind="GPS"` (passed
        straight through to `draw_nav_head`) while the switch is on GPS,
        else `sc.nav1_head` (the tuned NAV1 receiver) with no override, the
        same as before this existed. NAV2 is unaffected - a second, always-
        independent VOR/LOC receiver, the way a second nav radio really is."""
        if getattr(sc.nav, "cdi_source", "GPS") == "GPS":
            return instr.gps_nav_head(sc.nav, sc.panel), "GPS"
        return sc.nav1_head, None

    def _nav2_view(self, sc: Scene):
        """NAV2's round head. With a second FMS unit (`--dual`), FMS2 drives
        it exactly as FMS1 drives NAV1 (see `_nav1_view`): GPS course
        deviation from FMS2 while FMS2's own CDI source is GPS, the tuned
        NAV2 receiver once FMS2's CDI key selects VLOC. Single-unit, NAV2 is
        just its raw receiver, as before."""
        g2 = sc.gns2
        if g2 is not None and sc.panel2 is not None:
            if getattr(g2.nav, "cdi_source", "GPS") == "GPS":
                return instr.gps_nav_head(g2.nav, sc.panel2), "GPS"
        return sc.nav2_head, None

    def _unit2_scene(self, sc: Scene) -> Scene:
        """A Scene for drawing FMS2 as its own GNS unit: FMS2's gns/nav/
        variant, and (when available) FMS2's own CDI panel rather than
        FMS1's, so its CDI strip follows FMS2's CDI key."""
        import dataclasses
        return dataclasses.replace(
            sc, gns=sc.gns2, nav=sc.gns2.nav,
            panel=sc.panel2 if sc.panel2 is not None else sc.panel,
            variant=getattr(sc.gns2, "variant", None))

    def _gns_unit(self, sc: Scene, *, box: tuple | None = None, no_bezel: bool = False,
                  freq: tuple | None = None):
        """Draw one GNS unit (bezel + screen + below-bezel FPL strip) in
        ``box`` = (x0, y0, w, h), or the full single-unit column when
        ``box`` is omitted - the "gps" layout's original placement. ``sc``
        supplies the unit's own gns/nav/panel/variant (a `dual` layout scene
        is `dataclasses.replace`d per unit so the rest of this method and
        everything it calls stays polymorphic over `sc` unchanged).

        ``no_bezel=True`` (the "stack" layout only) skips the photorealistic
        SVG faceplate entirely and always falls back to the flat vector-panel
        style below, so the unit reads as one visual family with the
        transponder/AP boxes next to it rather than a cut-out photo.

        ``freq=(com, nav, label)`` (the "stack" layout only) annunciates one
        COM/NAV active+standby pair inline at the top of the screen, the way
        the real GNS 530/430 always does and this trainer otherwise never
        has - ``com``/``nav`` are `radios.ComRadio`/`NavReceiver`, ``label``
        a short tag ("1"/"2") for which pair this unit is wired to."""
        var = self._variant(sc)
        rows = getattr(var, "screen_rows", 12)
        aspect = getattr(var, "bezel_aspect", _BEZEL_ASPECT)
        frac = getattr(var, "screen_frac", _SCREEN_FRAC)

        x0, y0, w, h = box or (8, 32, GNS_W - 16, WIN_H - 44)
        bw = w
        bh = int(round(bw / aspect))
        if bh > h:            # box is short (stacked dual layout) - fit by height instead
            bh = h
            bw = int(round(bh * aspect))
        bezel = None if no_bezel else _load_bezel((bw, bh), _bezel_svg(var))

        if bezel is not None:
            pygame.draw.rect(self.surf, (12, 14, 17), (x0, y0, w, h))
            self.surf.blit(bezel, (x0, y0))
            fx0, fy0, fx1, fy1 = frac
            scr = pygame.Rect(x0 + fx0 * bw, y0 + fy0 * bh,
                              (fx1 - fx0) * bw, (fy1 - fy0) * bh)
            self._below_bezel(sc, pygame.Rect(x0, y0 + bh + 8, w, h - bh - 8), rows)
        else:
            pygame.draw.rect(self.surf, PANEL, (x0, y0, w, h), border_radius=8)
            pygame.draw.rect(self.surf, EDGE, (x0, y0, w, h), width=1, border_radius=8)
            scr = pygame.Rect(x0 + 12, y0 + 12, w - 24, h - 150)
        pygame.draw.rect(self.surf, (4, 8, 6), scr)
        pygame.draw.rect(self.surf, EDGE, scr, width=1)

        page = getattr(sc.gns.cursor, "group_name", "NAV")
        sub = getattr(sc.gns.cursor, "page_name", "")
        self._t(f"{page}  {sub}", scr.x + 8, scr.y + 6, font=self.f_sm, color=CYAN)
        if getattr(sc.gns.cursor, "cursor_on", False):
            self._t("CRSR", scr.right - 8, scr.y + 6, font=self.f_sm, color=AMBER, right=True)

        hdr_h = 20
        if freq is not None:
            com, nav, label = freq
            self._t(f"C{label} {com.active_mhz:.3f} [{com.standby_mhz:.3f}]",
                    scr.x + 8, scr.y + hdr_h, font=self.f_sm, color=GPS_GREEN)
            self._t(f"N{label} {nav.active_mhz:.2f} [{nav.standby_mhz:.2f}]",
                    scr.right - 8, scr.y + hdr_h, font=self.f_sm, color=CYAN, right=True)
            hdr_h += 14

        cdi_h = 40
        body = pygame.Rect(scr.x + 8, scr.y + hdr_h + 8, scr.w - 16,
                           scr.h - (hdr_h + 16) - cdi_h)
        if page == "NAV" and sub == "Map":
            self._map(sc, rect=body)
        elif page == "NAV" and sub == "Flight Plan":
            self._draw_fpl(sc, body, rows)
        elif page == "NAV" and sub == "Flight Plan Catalog":
            self._draw_fpl_catalog(sc, body, rows)
        elif page == "NAV" and sub == "VNAV":
            self._draw_vnav_page(sc, body)
        elif page == "NAV" and sub == "NAV/COM":
            self._draw_navcom_page(sc, body)
        elif page == "WPT":
            self._draw_wpt_page(sc, body, sub)
        elif page == "NRST":
            self._draw_nrst_page(sc, body, sub)
        elif page == "AUX":
            self._draw_aux_page(sc, body, sub)
        else:
            self._draw_nav_default(sc, body)

        self._turn_advisory(sc, scr)
        self._cdi_strip(sc, pygame.Rect(scr.x, scr.bottom - cdi_h, scr.w, cdi_h))
        if bezel is None:
            self._bezel_labels(sc, x0, scr.bottom + 22, w)
        else:
            self._bezel_key_labels(x0, y0, bw, bh, var)
        if getattr(sc.gns, "_proc_dialog", None) is not None:
            self._proc_page(sc, scr)
        elif getattr(sc.gns, "_leg_confirm", None) is not None:
            self._activate_leg_page(sc, scr)
        elif getattr(sc.gns, "_remove_confirm", None) is not None:
            self._remove_confirm_page(sc, scr)
        elif getattr(sc.gns, "_restart_confirm", None) is not None:
            self._restart_confirm_page(sc, scr)
        elif getattr(sc.gns, "_dto_dialog", None) is not None:
            self._direct_to_page(sc, scr)
        elif getattr(sc, "show_messages", False):
            self._message_page(sc, scr)
        elif getattr(sc.gns, "_fpl_menu", None) is not None:
            self._fpl_menu_page(sc, scr)
        elif getattr(sc.gns, "_airspace_info", None) is not None:
            self._airspace_info_page(sc, scr)

    def _activate_leg_page(self, sc: Scene, scr: pygame.Rect):
        """The "Activate Leg?" confirmation (DCT pressed twice on a highlighted
        flight-plan waypoint): shows the leg that will become active."""
        wps = sc.gns.fpl.waypoints
        row = sc.gns._leg_confirm["row"]
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, min(110, scr.h - 34))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, MAGENTA, box, width=1)
        self._t("ACTIVATE LEG", box.x + 8, box.y + 6, font=self.f_sm, color=MAGENTA)
        if 1 <= row < len(wps):
            self._t(f"{wps[row - 1].ident} -> {wps[row].ident}", box.x + 14, box.y + 30,
                    font=self.f_md, color=WHITE)
        self._t("Activate?", box.right - 10, box.bottom - 34, font=self.f_sm,
                color=AMBER, right=True)
        self._t("ENT=activate  CLR=cancel", box.x + 14, box.bottom - 18,
                font=self.f_sm, color=DIM)

    def _remove_confirm_page(self, sc: Scene, scr: pygame.Rect):
        """"REMOVE WAYPOINT" / "Remove Approach?" etc. confirmation window
        (Pilot's Guide sec.4 p.49, p.58-59): CLR on a flight-plan row, or MNU
        > Remove Approach/Arrival/Departure - lists what will be removed and
        requires ENT on "Yes?" before anything actually happens."""
        rc = sc.gns._remove_confirm
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, min(110, scr.h - 34))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, AMBER, box, width=1)
        wps = sc.gns.fpl.waypoints
        if rc["kind"] == "waypoint":
            row = rc["row"]
            wp = wps[row] if 0 <= row < len(wps) else None
            if wp is not None and wp.proc_kind:
                label = {"approach": "APPROACH", "star": "ARRIVAL", "sid": "DEPARTURE"}.get(
                    wp.proc_kind, wp.proc_kind.upper())
                self._t(f"REMOVE {label}", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
                self._t(wp.proc_ident, box.x + 14, box.y + 30, font=self.f_md, color=WHITE)
            else:
                self._t("REMOVE WAYPOINT", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
                if wp is not None:
                    self._t(wp.ident, box.x + 14, box.y + 30, font=self.f_md, color=WHITE)
        else:
            label = {"approach": "APPROACH", "star": "ARRIVAL", "sid": "DEPARTURE"}.get(
                rc["kind"], rc["kind"].upper())
            ident = next((w.proc_ident for w in wps if w.proc_kind == rc["kind"]), "")
            self._t(f"REMOVE {label}", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
            self._t(ident, box.x + 14, box.y + 30, font=self.f_md, color=WHITE)
        self._t("Yes?", box.right - 10, box.bottom - 34, font=self.f_sm, color=AMBER, right=True)
        self._t("ENT=remove  CLR=cancel", box.x + 14, box.bottom - 18,
                font=self.f_sm, color=DIM)

    def _restart_confirm_page(self, sc: Scene, scr: pygame.Rect):
        """"Restart Approach?" window - PROC > Activate (Vectors-To-Final)
        while that same approach is already being flown, before the MAP
        (Pilot's Guide sec.5 p.62)."""
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, min(90, scr.h - 34))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, AMBER, box, width=1)
        ident = next((w.proc_ident for w in sc.gns.fpl.waypoints if w.proc_kind == "approach"), "")
        self._t("RESTART APPROACH", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
        self._t(ident, box.x + 14, box.y + 28, font=self.f_md, color=WHITE)
        self._t("Yes?", box.right - 10, box.bottom - 34, font=self.f_sm, color=AMBER, right=True)
        self._t("ENT=restart  CLR=cancel", box.x + 14, box.bottom - 18,
                font=self.f_sm, color=DIM)

    def _direct_to_page(self, sc: Scene, scr: pygame.Rect):
        """Select Direct-To Waypoint page: the editable identifier + a live
        preview of what it resolves to."""
        dlg = sc.gns._dto_dialog
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, min(150, scr.h - 34))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, MAGENTA, box, width=1)
        self._t("DIRECT TO  ->", box.x + 8, box.y + 6, font=self.f_sm, color=MAGENTA)
        # the identifier cells
        cw = 16
        x0 = box.x + 14
        cy = box.y + 30
        for i, ch in enumerate(dlg.chars):
            cx = x0 + i * cw
            col = DIM if dlg.confirming else WHITE
            self._t(ch if ch.strip() else "_", cx + cw // 2, cy, font=self.f_md,
                    color=col, center=True)
            if i == dlg.cursor and not dlg.confirming:
                pygame.draw.line(self.surf, AMBER, (cx + 2, cy + 20),
                                 (cx + cw - 2, cy + 20), 2)
        # live resolution preview
        ent = None
        try:
            ent = sc.gns.lookup(dlg.ident())
        except Exception:                      # noqa: BLE001 - preview only
            ent = None
        if ent is not None:
            kind = type(ent).__name__.replace("Navaid", "").replace("Vhf", "VOR")
            self._t(f"{ent.ident}  {kind}", box.x + 14, cy + 34,
                    font=self.f_sm, color=GPS_GREEN)
            brg = norm360(initial_bearing(sc.own.pos, ent.pos) - sc.magvar)
            dis = great_circle_nm(sc.own.pos, ent.pos)
            self._t(f"{brg:03.0f}deg  {dis:5.1f}nm", box.x + 14, cy + 50,
                    font=self.f_sm, color=TEXT)
        elif dlg.ident():
            self._t("no match", box.x + 14, cy + 34, font=self.f_sm, color=DIM)
        if dlg.confirming:
            # "Activate?" highlighted lower-right, per the Pilot's Guide Fig.4-3 -
            # ENT here (a second press) is what actually activates the direct-to
            self._t("Activate?", box.right - 10, box.bottom - 34, font=self.f_sm,
                    color=AMBER, right=True)
            self._t("ENT=activate  CLR=back",
                    box.x + 14, box.bottom - 18, font=self.f_sm, color=DIM)
        else:
            self._t("knob=char/cursor  ENT=confirm  CLR=x",
                    box.x + 14, box.bottom - 18, font=self.f_sm, color=DIM)

    def _proc_page(self, sc: Scene, scr: pygame.Rect):
        """PROC key selector: a scrolling list (menu / procedures / transitions)
        with a `>` marker on the current row."""
        dlg = sc.gns._proc_dialog
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, min(168, scr.h - 34))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, CYAN, box, width=1)
        self._t(dlg.title, box.x + 8, box.y + 6, font=self.f_sm, color=CYAN)
        opts = list(dlg.options)
        if not opts:
            self._t("none available", box.x + 12, box.y + 28, font=self.f_sm, color=DIM)
        rows = max(1, (box.height - 44) // 15)
        top = max(0, min(dlg.sel - rows // 2, max(0, len(opts) - rows)))
        y = box.y + 26
        for i in range(top, min(len(opts), top + rows)):
            cur = i == dlg.sel
            self._t(("> " if cur else "  ") + opts[i], box.x + 10, y,
                    font=self.f_sm, color=WHITE if cur else TEXT)
            y += 15
        self._t("knob=sel  ENT=ok  CLR=back",
                box.x + 10, box.bottom - 16, font=self.f_sm, color=DIM)

    def _turn_advisory(self, sc: Scene, scr: pygame.Rect):
        """`NEXT DTK ###` / `TURN TO ###` in the lower-right of the screen
        (GNS 530 Pilot's Guide Fig 3-2)."""
        nav = sc.nav
        nxt = getattr(nav, "next_dtk", None)
        if nxt is None or not (getattr(nav, "wpt_alert", False)
                               or getattr(nav, "turn_now", False)):
            return
        mag = norm360(nxt - sc.magvar)
        turning = getattr(nav, "turn_now", False)
        text = f"{'TURN TO' if turning else 'NEXT DTK'} {mag:03.0f}°"
        self._t(text, scr.right - 8, scr.bottom - 58, font=self.f_sm,
                color=AMBER if turning else CYAN, right=True)

    def _message_page(self, sc: Scene, scr: pygame.Rect):
        """MSG key: a box listing pending system messages over the screen."""
        box = scr.inflate(-24, -40)
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, AMBER, box, width=1)
        self._t("MESSAGES", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
        msgs = list(getattr(sc, "messages", []) or [])
        if not msgs:
            self._t("no messages", box.x + 10, box.y + 26, font=self.f_sm, color=DIM)
        y = box.y + 26
        for m in msgs[: max(1, (box.height - 34) // 16)]:
            self._t(m, box.x + 10, y, font=self.f_sm, color=TEXT)
            y += 16

    def _bezel_key_labels(self, x0, y0, bw, bh, var):
        """Approximate legends over the faceplate SVG's button cutouts.
        The IFR-1 is the real input; these are cosmetic orientation only.
        Fractions are measured off the actual rendered faceplate art (not
        guessed) for each variant's distinct button layout."""
        def lab(text, fx, fy):
            self._t(text, x0 + fx * bw, y0 + fy * bh, font=self.f_sm,
                    color=DIM, center=True)
        if getattr(var, "short", "530") == "530":
            # bottom row: 6 softkey cutouts, left to right CDI/OBS/MSG/FPL/
            # VNAV/PROC, measured centers at fy ~0.815 (a prior pass had
            # only 5 fx's here - VNAV was missing and PROC sat in its slot)
            for text, fx in zip(("CDI", "OBS", "MSG", "FPL", "VNAV", "PROC"),
                                (0.239, 0.344, 0.448, 0.552, 0.656, 0.760)):
                lab(text, fx, 0.815)
            # right column: 5 stacked cutouts, measured centers
            for text, fy in zip(("RNG", "D>", "MENU", "CLR", "ENT"),
                                (0.203, 0.359, 0.450, 0.539, 0.630)):
                lab(text, 0.915, fy)
        else:
            # 430: bottom row is the same 5 keys as the 530 minus VNAV
            # (CDI/OBS/MSG/FPL/PROC), measured centers at fy ~0.905. The
            # right-side cluster is RNG (one wide cutout, top) over a 2x2
            # grid of D>/MENU (row 1) and CLR/ENT (row 2) - a prior pass
            # mislabeled this cluster CDI/OBS/MSG/FPL/PROC and left the
            # actual bottom-row cutouts unlabeled.
            for text, fx in zip(("CDI", "OBS", "MSG", "FPL", "PROC"),
                                (0.291, 0.403, 0.515, 0.627, 0.739)):
                lab(text, fx, 0.905)
            lab("RNG", 0.897, 0.144)
            lab("D>", 0.842, 0.278)
            lab("MENU", 0.933, 0.278)
            lab("CLR", 0.843, 0.426)
            lab("ENT", 0.933, 0.426)

    def _below_bezel(self, sc: Scene, r: pygame.Rect, rows: int = 12):
        """Compact strip under the (landscape) unit - flight plan + nav-data state.
        ``rows`` caps the visible list to the variant's screen height."""
        if r.height < 30:
            return
        pygame.draw.rect(self.surf, PANEL, r, border_radius=6)
        pygame.draw.rect(self.surf, EDGE, r, width=1, border_radius=6)
        self._t("FPL", r.x + 8, r.y + 6, font=self.f_sm, color=DIM)
        wps = getattr(sc.gns.fpl, "waypoints", [])
        active = getattr(sc.gns.fpl, "active", 1)
        y = r.y + 22
        dto = getattr(sc.gns, "dto", None)
        if dto is not None:                    # Direct-To leads the list
            self._t(f"D> {dto.target.ident}", r.x + 10, y, font=self.f_sm, color=MAGENTA)
            y += 16
        cap = visible_fpl_rows(len(wps), rows, space_rows=(r.height - 46) // 16)
        for i, wp in enumerate(wps[:cap]):
            in_dto = dto is not None
            col = MAGENTA if (i == active and not in_dto) else (DIM if in_dto else TEXT)
            mark = "  " if in_dto else ("> " if i == active else "  ")
            self._t(mark + wp.ident, r.x + 10, y, font=self.f_sm, color=col)
            y += 16
        if not wps and dto is None:
            self._t("no flight plan", r.x + 10, y, font=self.f_sm, color=DIM)
        exp = getattr(sc.gns.db, "expires", None)
        if exp is not None:
            expd = getattr(sc.gns, "today", None)
            stale = expd is not None and expd >= exp
            self._t(f"NAV DATA EXP {exp:%m-%d-%y}", r.right - 8, r.bottom - 18,
                    font=self.f_sm, color=AMBER if stale else DIM, right=True)

    def _draw_nav_default(self, sc: Scene, b: pygame.Rect):
        nav, own, pan = sc.nav, sc.own, sc.panel
        act = getattr(nav, "valid", False)
        to = getattr(nav, "to_ident", "") or "----"
        frm = getattr(nav, "from_ident", "") or "----"
        mode = getattr(nav, "mode", "") or ""
        # active-leg symbol (GNS 530 Pilot's Guide Table 3-2): direct-to arrow,
        # course line, or the mode word for OBS / SUSP.
        sym = {"DTO": "D>", "OBS": "OBS", "SUSP": "SUSP", "HOLD": "HOLD"}.get(mode, "->")
        symcol = AMBER if mode in ("OBS", "SUSP", "HOLD") else (MAGENTA if act else DIM)
        self._t(sym, b.x, b.y, font=self.f_sm, color=symcol)
        self._t(f"{frm}", b.x + 36, b.y, font=self.f_sm, color=DIM)
        self._t(f"{to}", b.x + 86, b.y - 4, font=self.f_lg,
                color=MAGENTA if act else DIM)
        if getattr(sc.gns, "obs_active", False):
            oc = norm360(getattr(sc.gns, "obs_course", 0.0) - sc.magvar)
            self._t(f"OBS {oc:03.0f}°", b.right, b.y, font=self.f_sm,
                    color=AMBER, right=True)

        def row(label, val, yy, color=TEXT):
            self._t(label, b.x, yy, font=self.f_sm, color=DIM)
            self._t(val, b.right, yy - 2, font=self.f_md, color=color, right=True)

        y = b.y + 34
        mv = sc.magvar
        # short screens (GNS 430) get tighter rows and drop the last one or two
        dy = max(15, min(20, (b.height - 40) // 6))
        room = max(2, (b.height - 34) // dy)
        dtk = getattr(nav, "dtk", None)
        dis = getattr(nav, "dist_nm", None)
        gs = getattr(own, "gs_kt", 0.0)
        ete = (dis / gs * 60.0) if (dis and gs > 20) else None
        xtk = getattr(nav, "xtk_nm", None)
        rows = [
            ("DTK", f"{norm360(dtk - mv):03.0f}°" if dtk is not None else "---°", TEXT),
            ("TRK", f"{norm360(getattr(own, 'track_deg', 0.0) - mv):03.0f}°", TEXT),
            ("DIS", f"{dis:5.1f}nm" if dis is not None else "--.-nm", TEXT),
            ("GS", f"{gs:3.0f}kt", TEXT),
            ("ETE", f"{int(ete):02d}:{int((ete*60) % 60):02d}" if ete else "--:--", TEXT),
        ]
        if xtk is not None:
            side = "R" if xtk > 0 else "L"
            rows.append(("XTK", f"{abs(xtk):4.2f}nm {side}",
                         AMBER if abs(xtk) > 1.0 else TEXT))
        for i, (lab, val, col) in enumerate(rows[:room]):
            row(lab, val, y + i * dy, color=col)

    def _draw_fpl(self, sc: Scene, b: pygame.Rect, rows: int = 12):
        wps = getattr(sc.gns.fpl, "waypoints", [])
        active = getattr(sc.gns.fpl, "active", 1)
        y = b.y
        mv = sc.magvar
        dto = getattr(sc.gns, "dto", None)
        if dto is not None:
            brg = norm360(initial_bearing(sc.own.pos, dto.target.pos) - mv)
            dis = great_circle_nm(sc.own.pos, dto.target.pos)
            self._t(f"D> {dto.target.ident:<6}", b.x, y, font=self.f_sm, color=MAGENTA)
            self._t(f"{brg:03.0f}°{dis:6.1f}", b.right, y, font=self.f_sm,
                    color=MAGENTA, right=True)
            y += 17
        cap = visible_fpl_rows(len(wps), rows, space_rows=b.height // 17 or rows)
        ed = getattr(sc.gns, "_fpl_edit", None)
        sel = ed["row"] if ed else -1
        buf = ed.get("buf") if ed else None
        prev_kind = ""
        for i, wp in enumerate(wps[:cap]):
            if wp.proc_kind and wp.proc_kind != prev_kind:
                # a procedure's title, "in light blue text", directly above
                # its waypoints (Pilot's Guide sec.4 p.59) - CLR on any of
                # its legs removes the whole procedure, same as the title.
                label = {"approach": "APR", "star": "STAR", "sid": "SID"}.get(
                    wp.proc_kind, wp.proc_kind.upper())
                self._t(f"{label} {wp.proc_ident}", b.x, y, font=self.f_sm, color=CYAN)
                y += 15
            prev_kind = wp.proc_kind
            leg_to_here = i == active and dto is None
            col = MAGENTA if leg_to_here else (DIM if dto is not None else TEXT)
            marker = "->" if leg_to_here else ("[]" if i == sel else "  ")
            if i == sel and buf is not None:
                prefix = f"{marker} "
                r0 = self._t(f"{prefix}{''.join(buf.chars)}", b.x, y, font=self.f_sm, color=AMBER)
                # underline the character the knob is actually on - same cue
                # as the Direct-To entry page, so it's clear what typing next
                # (or scrolling the knob) will change
                cw = self.f_sm.size("0")[0]
                ux = b.x + self.f_sm.size(prefix)[0] + buf.cursor * cw
                pygame.draw.line(self.surf, AMBER, (ux, r0.bottom - 1), (ux + cw, r0.bottom - 1), 3)
            else:
                self._t(f"{marker} {wp.ident:<7}", b.x, y, font=self.f_sm,
                        color=AMBER if i == sel else col)
            tag = _fpl_tag(wp)                            # IAF / FAF / MAP / HOLD / ~
            if tag:
                self._t(tag, b.x + 78, y, font=self.f_sm,
                        color=AMBER if tag in ("MAP", "HOLD") else DIM)
            if i >= 1:                                   # per-leg DTK / DIS (sec.5.2)
                p0 = wps[i - 1].pos
                dtk = norm360(initial_bearing(p0, wp.pos) - mv)
                dis = great_circle_nm(p0, wp.pos)
                self._t(f"{dtk:03.0f}°{dis:6.1f}", b.right, y, font=self.f_sm,
                        color=DIM, right=True)
            y += 17
        if sel == len(wps):                              # the "add a waypoint" row
            txt = "".join(buf.chars) if buf is not None else "_____"
            self._t(f"[] {txt}", b.x, y, font=self.f_sm, color=AMBER)
            y += 17
        if not wps and sel < 0:
            self._t("no flight plan", b.x, y, font=self.f_sm, color=DIM)

    def _draw_fpl_catalog(self, sc: Scene, b: pygame.Rect, rows: int = 12):
        """Flight Plan Catalog page: FPL 01-19, big knob (cursor on) selects
        a row, ENT recalls it as the active plan, CLR deletes it in place."""
        gns = sc.gns
        cat = getattr(gns, "fpl_catalog", [])
        on = getattr(gns.cursor, "cursor_on", False)
        sel = getattr(gns, "cat_sel", 0)
        if on:
            self._t("ENT=recall  CLR=delete", b.right, b.y, font=self.f_sm,
                    color=DIM, right=True)
        y = b.y + 18
        room = max(1, (b.height - 18) // 15)
        for i, plan in enumerate(cat[:room]):
            mk = ">" if (on and i == sel) else " "
            col = AMBER if (on and i == sel) else (TEXT if plan else DIM)
            if plan and plan.waypoints:
                comment = plan.comment or f"{plan.waypoints[0].ident}/{plan.waypoints[-1].ident}"
                label = f"{comment:<12} {len(plan.waypoints)} wpts"
            else:
                label = "-- empty --"
            self._t(f"{mk} {i + 1:02d}  {label}", b.x, y, font=self.f_sm, color=col)
            y += 15

    def _draw_vnav_page(self, sc: Scene, b: pygame.Rect):
        """VNAV profile page: pilot-entered target fix/altitude/VS profile
        (edited with cursor-on, outer=field/inner=value) + live status once
        armed. The real GNS 530's own VNAV input is a vertical SPEED, not a
        flight-path angle (Pilot's Guide sec.10/11 - "VS Profile")."""
        gns = sc.gns
        prof = gns.vnav
        on = getattr(gns.cursor, "cursor_on", False)
        field = getattr(gns, "vnav_field", 0)
        alt = getattr(sc.own, "altitude_ft", None)
        gs = getattr(sc.own, "gs_kt", None)
        st = gns.vnav_status(alt, gs) if hasattr(gns, "vnav_status") else None
        y = b.y
        rows = [
            ("TARGET", prof.target_ident or "----"),
            ("TGT ALT", f"{prof.target_alt_ft:.0f} ft"),
            ("VS PROFILE", f"{prof.vs_fpm:+.0f} fpm"),
        ]
        for i, (label, val) in enumerate(rows):
            col = AMBER if (on and i == field) else TEXT
            mk = ">" if (on and i == field) else " "
            self._t(f"{mk} {label:<11}{val}", b.x, y, font=self.f_sm, color=col)
            y += 17
        self._t(f"VNV {'ARMED' if prof.armed else 'OFF  '}", b.x, y, font=self.f_sm,
                color=GPS_GREEN if prof.armed else DIM)
        y += 20
        if st is None or not st.valid:
            if not prof.armed:
                msg = "no active VNAV target"
            elif gs is not None and gs <= 35.0:
                msg = "GS too low (need > 35 kt)"
            else:
                msg = "target not ahead"
            self._t(msg, b.x, y, font=self.f_sm, color=DIM)
            return
        self._t(f"DIS {st.distance_to_target_nm:5.1f} nm", b.x, y, font=self.f_sm, color=TEXT)
        y += 15
        if st.distance_to_tod_nm is not None:
            label = "TOD IN" if st.distance_to_tod_nm >= 0 else "PAST TOD"
            col = AMBER if st.alert else TEXT
            self._t(f"{label} {abs(st.distance_to_tod_nm):5.1f} nm", b.x, y,
                    font=self.f_sm, color=col)
            y += 15
            if st.time_to_tod_min is not None and st.distance_to_tod_nm >= 0:
                self._t(f"TIME TO TOD {st.time_to_tod_min:4.1f} min", b.x, y,
                        font=self.f_sm, color=col)
                y += 15
        if st.required_vs_fpm is not None:
            self._t(f"REQ VS {st.required_vs_fpm:+.0f} fpm", b.x, y, font=self.f_sm, color=TEXT)
            y += 15
        if st.deviation_ft is not None:
            sign = "+" if st.deviation_ft >= 0 else ""
            self._t(f"DEV {sign}{st.deviation_ft:.0f} ft", b.x, y, font=self.f_sm,
                    color=AMBER if abs(st.deviation_ft) > 100 else GPS_GREEN)

    def _fpl_menu_page(self, sc: Scene, scr: pygame.Rect):
        """MNU pop-up on the Flight Plan / Flight Plan Catalog pages."""
        menu = sc.gns._fpl_menu
        box = pygame.Rect(scr.x + 24, scr.y + 30, scr.w - 48,
                          20 + 16 * len(menu.options))
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, AMBER, box, width=1)
        y = box.y + 8
        for i, opt in enumerate(menu.options):
            col = AMBER if i == menu.sel else TEXT
            mk = ">" if i == menu.sel else " "
            self._t(f"{mk} {opt}", box.x + 8, y, font=self.f_sm, color=col)
            y += 16

    def _airspace_info_page(self, sc: Scene, scr: pygame.Rect):
        """The Airspace Information Page + its Frequency Page (Pilot's Guide p.122-123), opened by ENT on a
        highlighted Nearest Airspace Page row."""
        gns = sc.gns
        info = gns._airspace_info
        aw = info.airspace
        box = pygame.Rect(scr.x + 8, scr.y + 24, scr.w - 16, scr.h - 34)
        pygame.draw.rect(self.surf, (10, 14, 18), box)
        pygame.draw.rect(self.surf, AMBER, box, width=1)
        if info.freqs_open:
            self._t("FREQUENCIES", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
            agency = gns.airspace_controlling(aw) if hasattr(gns, "airspace_controlling") else None
            name, freqs = agency if agency is not None else ("", ())
            self._t(name, box.x + 8, box.y + 24, font=self.f_sm, color=GPS_GREEN)
            y = box.y + 44
            for i, mhz in enumerate(freqs):
                hot = i == info.freq_sel
                self._t(f"{'>' if hot else ' '} {mhz:7.3f}", box.x + 8, y,
                        font=self.f_sm, color=AMBER if hot else TEXT)
                y += 16
            done_on = info.freq_sel == len(freqs)
            self._t("Done?", box.x + 8, y, font=self.f_sm, color=AMBER if done_on else TEXT)
            self._t("ENT=standby  CLR=back", box.right - 10, box.bottom - 14,
                    font=self.f_sm, color=DIM, right=True)
            return
        self._t("AIRSPACE INFORMATION", box.x + 8, box.y + 6, font=self.f_sm, color=AMBER)
        self._t(f"{aw.ident}  CLASS {aw.cls}", box.x + 8, box.y + 26, font=self.f_md, color=GPS_GREEN)
        own = sc.own
        cat = aw.alert_category(own.pos, getattr(own, "track_deg", 0.0), getattr(own, "gs_kt", 0.0) or 0.0)
        status = _AIRSPACE_STATUS_LABEL.get(cat) or f"{aw.distance_nm(own.pos):4.1f}nm"
        self._t(status, box.x + 8, box.y + 44, font=self.f_sm, color=TEXT)
        floor = "SFC" if (aw.floor_ft or 0) == 0 else f"{aw.floor_ft}ft"
        ceil = f"{aw.ceiling_ft}ft" if aw.ceiling_ft is not None else "---"
        self._t(f"{floor} - {ceil}", box.x + 8, box.y + 60, font=self.f_sm, color=TEXT)
        y = box.y + 82
        for i, label in enumerate(("View Frequencies?", "Done?")):
            hot = i == info.sel
            self._t(f"{'>' if hot else ' '} {label}", box.x + 8, y, font=self.f_sm, color=AMBER if hot else TEXT)
            y += 16
        self._t("ENT=select  CLR=back", box.right - 10, box.bottom - 14,
                font=self.f_sm, color=DIM, right=True)

    def _draw_wpt_page(self, sc: Scene, b: pygame.Rect, sub: str):
        gns = sc.gns
        buf = getattr(gns, "wpt_entry", None)
        chars = "".join(buf.chars) if buf is not None else ""
        on = getattr(gns.cursor, "cursor_on", False)
        # the page name (e.g. "INTERSECTION") on its own line - it's the
        # longest of the four sub-page names and was overlapping the
        # identifier when both shared a line - then per-character identifier
        # cells with an underline on the active one, same cue as the
        # Direct-To page and the Flight Plan edit field.
        self._t(sub.upper(), b.x, b.y, font=self.f_sm, color=DIM)
        cw = 16
        cy = b.y + 22
        for i, ch in enumerate(buf.chars if buf is not None else "______"):
            cx = b.x + i * cw
            self._t(ch if ch.strip() else "_", cx + cw // 2, cy, font=self.f_md,
                    color=AMBER if on else TEXT, center=True)
            if on and buf is not None and i == buf.cursor:
                pygame.draw.line(self.surf, AMBER, (cx + 2, cy + 20),
                                 (cx + cw - 2, cy + 20), 2)
        ent = gns.lookup(chars, sub) if buf is not None else None
        y = cy + 30
        if ent is None:
            self._t("no match" if chars.strip() else "knob: enter identifier",
                    b.x, y, font=self.f_sm, color=DIM)
            return
        mv = sc.magvar
        brg = norm360(initial_bearing(sc.own.pos, ent.pos) - mv)
        dis = great_circle_nm(sc.own.pos, ent.pos)
        kind = type(ent).__name__
        self._t(getattr(ent, "name", "") or kind, b.x, y, font=self.f_sm, color=GPS_GREEN)
        y += 16
        if sub == "Airport Runway":
            rows = gns.wpt_runways()
            r_on = on and getattr(gns, "wpt_field", 0) == 1
            if not rows:
                self._t("no runway data", b.x, y, font=self.f_sm, color=DIM)
                return
            rw = rows[getattr(gns, "wpt_sel", 0) % len(rows)]
            surf, lgt = ent.runway_info(rw.ident)
            self._t(f"{'>' if r_on else ' '}RWY {rw.number:<4} {getattr(gns, 'wpt_sel', 0) % len(rows) + 1}/{len(rows)}",
                    b.x, y, font=self.f_sm, color=AMBER if r_on else TEXT)
            y += 15
            dims = f"{rw.length_ft or '---'} x {rw.width_ft or '---'} ft"
            lines = [f"  {dims}", f"  HDG {rw.bearing_deg:03.0f}° mag",
                     f"  SFC {surf}", f"  LGT {lgt}"]
            if rw.elev_ft is not None:
                lines.append(f"  ELEV {rw.elev_ft} ft")
            ils = next(iter(gns.db.vhf.get(rw.ils_ident, [])), None) if rw.ils_ident else None
            if ils is not None:
                lines.append(f"  {'ILS' if rw.ils_category else 'LOC'} {ils.freq_mhz:.2f}")
            for ln in lines:
                self._t(ln, b.x, y, font=self.f_sm, color=TEXT)
                y += 15
            if on:
                self._t("small knob" if r_on else "large knob", b.right, b.y,
                        font=self.f_sm, color=DIM, right=True)
            return
        if sub == "Airport Freq":
            rows = gns.wpt_frequencies(sub)
            f_on = on and getattr(gns, "wpt_field", 0) == 1
            self._draw_freq_rows(rows, getattr(gns, "wpt_sel", 0) if f_on else -1, b.x, y, b.bottom, b.right)
            if on:
                self._t("ENT = standby" if f_on else "large knob: list", b.right, b.y,
                        font=self.f_sm, color=DIM, right=True)
            return
        if kind == "Airport":
            info = [f"ELEV {getattr(ent, 'elev_ft', 0) or 0} ft",
                    f"RWY  {getattr(ent, 'longest_runway_ft', 0) or 0} ft",
                    f"TWR  {ent.comm('TWR') or '---'}",
                    f"ATIS {ent.comm('ATIS', 'ASOS', 'AWOS') or '---'}"]
        elif kind == "VhfNavaid":
            f_on = on and getattr(gns, "wpt_field", 0) == 1      # frequency field highlighted: ENT -> VLOC standby
            info = [f"{'>' if f_on else ' '}FREQ {ent.freq_mhz:07.3f}", f"VAR  {ent.magvar_deg:+.0f}°"]
        elif kind == "NdbNavaid":
            info = [f"FREQ {ent.freq_khz:.0f} kHz"]
        else:
            info = [f"RGN  {getattr(ent, 'region', '') or '--'}"]
        for line in info:
            self._t(line, b.x, y, font=self.f_sm, color=TEXT)
            y += 15
        self._t(f"BRG {brg:03.0f}°   {dis:6.1f} nm", b.x, y + 2, font=self.f_sm, color=CYAN)
        if on:
            hint = "ENT = standby" if (kind == "VhfNavaid" and getattr(gns, "wpt_field", 0) == 1) else ("CLR = back" if getattr(gns, "_wpt_return", None) else "")
            self._t(hint, b.right, b.y, font=self.f_sm, color=DIM, right=True)

    def _draw_nrst_airports(self, sc: Scene, b: pygame.Rect, hits, on: bool, sel: int, y: int):
        """Nearest Airport: two lines per airport (the guide shows four) - identifier, bearing, distance and best
        approach; then the tower / CTAF frequency and the longest runway (p.116)."""
        gns = sc.gns
        room = max(1, (b.bottom - y) // 30)
        top = max(0, min(max(0, len(hits) - room), sel - room // 2))
        for i, e in enumerate(hits[top:top + room], start=top):
            brg = norm360(initial_bearing(sc.own.pos, e.pos) - sc.magvar)
            dis = great_circle_nm(sc.own.pos, e.pos)
            row_on = on and i == sel
            id_on = row_on and getattr(gns, "nrst_col", 0) == 0
            self._t(f"{'>' if id_on else ' '} {e.ident:<5} {brg:03.0f}° {dis:5.1f}nm {gns.best_approach(e.ident):>3}",
                    b.x, y, font=self.f_sm, color=AMBER if id_on else TEXT)
            fr = gns.nearest_frequency("Nearest APT", e)
            fon = row_on and not id_on
            rwy = f"{e.longest_runway_ft}ft" if e.longest_runway_ft else "---"
            self._t(f"{'>' if fon else ' '}   {f'{fr.mhz:7.3f}' if fr else '  ---  '}", b.x, y + 15,
                    font=self.f_sm, color=AMBER if fon else (TEXT if fr else DIM))
            self._t(rwy, b.right - 18, y + 15, font=self.f_sm, color=CYAN, right=True)
            y += 30
        if len(hits) > room:
            more = ("^" if top > 0 else " ") + ("v" if top + room < len(hits) else " ")
            self._t(more, b.right - 2, b.y + 16, font=self.f_sm, color=AMBER, right=True)

    def _draw_freq_rows(self, rows, sel: int, x: int, y: int, bottom: int, right: int):
        """A scrolling list of tunable frequencies (Airport Frequency / NAV/COM pages); ``sel`` -1 = none highlighted."""
        if not rows:
            self._t("no frequencies", x, y, font=self.f_sm, color=DIM)
            return
        room = max(1, (bottom - y) // 15)
        top = max(0, min(max(0, len(rows) - room), sel - room // 2)) if sel >= 0 else 0
        for i, fr in enumerate(rows[top:top + room], start=top):
            hot = i == sel
            col = AMBER if hot else (CYAN if fr.radio == "VLOC" else TEXT)
            note = f" {fr.note}" if fr.note else ""
            self._t(f"{'>' if hot else ' '} {fr.label:<10} {fr.mhz:7.3f}{note}", x, y, font=self.f_sm, color=col)
            y += 15
        if len(rows) > room:
            more = ("^" if top > 0 else " ") + ("v" if top + room < len(rows) else " ")
            self._t(more, right - 2, bottom - 15, font=self.f_sm, color=AMBER, right=True)

    def _draw_navcom_page(self, sc: Scene, b: pygame.Rect):
        """NAV/COM page: the frequencies of the flight-plan airports, tunable (Pilot's Guide p.24)."""
        gns = sc.gns
        on = getattr(gns.cursor, "cursor_on", False)
        apt = gns.navcom_airport()
        self._t("NAV/COM", b.x, b.y, font=self.f_sm, color=DIM)
        if apt is None:
            self._t("no airport in flight plan", b.x, b.y + 20, font=self.f_sm, color=DIM)
            return
        f_on = on and gns.navcom_sel >= 0
        self._t(f"{gns.navcom_role()}  {apt.ident}", b.x, b.y + 20, font=self.f_md,
                color=AMBER if (on and not f_on) else GPS_GREEN)
        self._draw_freq_rows(gns.navcom_frequencies(), gns.navcom_sel if on else -1, b.x, b.y + 46, b.bottom, b.right)
        if on:
            self._t("ENT = standby" if f_on else "small knob: airport", b.right, b.y,
                    font=self.f_sm, color=DIM, right=True)

    def _draw_nrst_facility(self, sc: Scene, b: pygame.Rect, hits, on: bool):
        """Nearest ARTCC / FSS: "one facility at a time" (Pilot's Guide p.119) - name, bearing, distance, then its
        frequency list; the small knob steps the facility, the large knob the highlighted frequency."""
        gns = sc.gns
        i = max(0, min(len(hits) - 1, getattr(gns, "nrst_facility", 0)))
        fac = hits[i]
        brg = norm360(initial_bearing(sc.own.pos, fac.pos) - sc.magvar)
        dis = great_circle_nm(sc.own.pos, fac.pos)
        # the guide's "facility name" is the controlling ARTCC/FSS, not the ground site itself; full ARTCC names
        # aren't in this data (only the 3-letter id, F62), so "ZDC CENTER" stands in for "WASHINGTON CENTER"
        name = f"{fac.voice_call} RADIO" if hasattr(fac, "voice_call") else f"{fac.artcc} CENTER"
        self._t(f"{name}  ({i + 1}/{len(hits)})", b.x, b.y + 20, font=self.f_md, color=GPS_GREEN)
        self._t(f"{fac.ident}  {brg:03.0f}°  {dis:5.1f}nm", b.x, b.y + 40, font=self.f_sm, color=TEXT)
        rows = gns.facility_frequencies(fac)
        self._draw_freq_rows(rows, getattr(gns, "nrst_freq_sel", 0) if on else -1, b.x, b.y + 60, b.bottom, b.right)

    def _draw_nrst_airspace(self, sc: Scene, b: pygame.Rect, hits, on: bool, sel: int, y: int):
        """Nearest Airspace: name/class, the alert status (F63: the same four conditions that post an MSG-queue
        alert, in the page's own wording), floor/ceiling, then the controlling agency and its primary frequency
        (F64 - ENT on a highlighted row tunes it to COM standby). No drill-down page or sectorized frequency
        list - see FINDINGS F62/F64 for what isn't modelled here."""
        gns = sc.gns
        own = sc.own
        track = getattr(own, "track_deg", 0.0)
        gs = getattr(own, "gs_kt", 0.0) or 0.0
        row_h = 45
        room = max(1, (b.bottom - y) // row_h)
        top = max(0, min(max(0, len(hits) - room), sel - room // 2))
        for i, aw in enumerate(hits[top:top + room], start=top):
            d = aw.distance_nm(own.pos)
            cat = aw.alert_category(own.pos, track, gs)
            status = _AIRSPACE_STATUS_LABEL.get(cat) or f"{d:4.1f}nm"
            row_on = on and i == sel
            mk = ">" if row_on else " "
            self._t(f"{mk} {aw.ident:<5} CLASS {aw.cls}  {status}", b.x, y,
                    font=self.f_sm, color=AMBER if row_on else TEXT)
            floor = "SFC" if (aw.floor_ft or 0) == 0 else f"{aw.floor_ft}ft"
            ceil = f"{aw.ceiling_ft}ft" if aw.ceiling_ft is not None else "---"
            self._t(f"   {floor} - {ceil}", b.x, y + 15, font=self.f_sm, color=DIM)
            agency = gns.airspace_controlling(aw) if hasattr(gns, "airspace_controlling") else None
            if agency is not None and agency[1]:
                name, freqs = agency
                more = f" +{len(freqs) - 1}" if len(freqs) > 1 else ""
                self._t(f"   {name[:14]:<14} {freqs[0]:7.3f}{more}", b.x, y + 30,
                        font=self.f_sm, color=AMBER if row_on else CYAN)
            y += row_h
        if len(hits) > room:
            more = ("^" if top > 0 else " ") + ("v" if top + room < len(hits) else " ")
            self._t(more, b.right - 2, b.y + 16, font=self.f_sm, color=AMBER, right=True)

    def _draw_nrst_page(self, sc: Scene, b: pygame.Rect, sub: str):
        gns = sc.gns
        hits = gns.nearest_for_page(sub) if hasattr(gns, "nearest_for_page") else []
        on = getattr(gns.cursor, "cursor_on", False)
        sel = getattr(gns, "nrst_sel", 0)
        self._t(sub.upper(), b.x, b.y, font=self.f_sm, color=DIM)
        if on:
            if sub in ("Nearest ARTCC", "Nearest FSS"):
                hint = "sm=site lg=freq"
            elif sub == "Nearest Airspace":
                hint = "ENT = standby"
            else:
                hint = "ENT = standby" if getattr(gns, "nrst_col", 0) == 1 else "ENT = info  D-> = DCT"
            if hint:
                self._t(hint, b.right - 16, b.y, font=self.f_sm, color=DIM, right=True)
        mv = sc.magvar
        y = b.y + 20
        room = max(1, (b.height - 20) // 15)
        if not hits:
            msg = "no user waypoints stored" if sub == "Nearest User" else "none within range"
            self._t(msg, b.x, y, font=self.f_sm, color=DIM)
            return
        if sub == "Nearest APT":
            self._draw_nrst_airports(sc, b, hits, on, sel, y)
            return
        if sub in ("Nearest ARTCC", "Nearest FSS"):
            self._draw_nrst_facility(sc, b, hits, on)
            return
        if sub == "Nearest Airspace":
            self._draw_nrst_airspace(sc, b, hits, on, sel, y)
            return
        # scroll the window to keep the selection on-screen (same idea as the
        # Weather page's CRSR scroll) - the list always started at the top
        # before, so selecting past the first `room` entries (further-away
        # POIs) moved the selection off-screen with nothing ever scrolling
        # to show them.
        top = max(0, min(max(0, len(hits) - room), sel - room // 2))
        for i, e in enumerate(hits[top:top + room], start=top):
            brg = norm360(initial_bearing(sc.own.pos, e.pos) - mv)
            dis = great_circle_nm(sc.own.pos, e.pos)
            row_on = on and i == sel
            id_on = row_on and getattr(gns, "nrst_col", 0) == 0
            mk = ">" if id_on else " "
            self._t(f"{mk} {e.ident:<6} {brg:03.0f}° {dis:6.1f}nm", b.x, y,
                    font=self.f_sm, color=AMBER if id_on else TEXT)
            fr = gns.nearest_frequency(sub, e) if hasattr(gns, "nearest_frequency") else None
            if fr is not None:
                fx = b.right - 64
                fon = row_on and not id_on
                self._t(f"{'>' if fon else ' '}{fr.mhz:7.3f}", fx, y, font=self.f_sm,
                        color=AMBER if fon else (CYAN if fr.radio == "VLOC" else TEXT))
            elif sub == "Nearest NDB" and getattr(e, "freq_khz", None):
                self._t(f" {e.freq_khz:.0f}", b.right - 64, y, font=self.f_sm, color=TEXT)
            y += 15
        if len(hits) > room:
            more = ("^" if top > 0 else " ") + ("v" if top + room < len(hits) else " ")
            self._t(more, b.right - 2, b.y, font=self.f_sm, color=AMBER, right=True)

    # -- AUX group: Trip Planning / Utility / Setup / Nav Data -------------
    def _draw_aux_page(self, sc: Scene, b: pygame.Rect, sub: str):
        if sub == "Nav Data":
            self._draw_aux_navdata(sc, b)
        elif sub == "Trip Planning":
            self._draw_aux_trip(sc, b)
        elif sub == "Utility":
            self._draw_aux_utility(sc, b)
        elif sub == "Setup":
            self._draw_aux_setup(sc, b)
        elif sub == "Weather":
            self._draw_aux_weather(sc, b)
        elif sub == "Charts":
            self._draw_aux_charts(sc, b)

    def _draw_aux_navdata(self, sc: Scene, b: pygame.Rect):
        db = sc.db
        y = b.y
        if db is None:
            self._t("no nav database", b.x, y, font=self.f_sm, color=DIM)
            return
        self._t(f"SOURCE  {db.source or '---'}", b.x, y, font=self.f_sm, color=TEXT); y += 17
        self._t(f"CYCLE   {db.cycle or '---'}", b.x, y, font=self.f_sm, color=TEXT); y += 17
        eff = db.effective.isoformat() if db.effective else "---"
        self._t(f"EFF     {eff}", b.x, y, font=self.f_sm, color=TEXT); y += 17
        expired = False
        if db.expires is not None:
            from datetime import date
            expired = date.today() > db.expires
        exp = db.expires.isoformat() if db.expires else "---"
        self._t(f"EXP     {exp}", b.x, y, font=self.f_sm, color=AMBER if expired else TEXT)
        y += 20
        for label, n in (("APT", len(db.airports)), ("VOR", len(db.vhf)),
                         ("NDB", len(db.ndb)), ("WPT", len(db.waypoints)),
                         ("AWY", len(db.airways))):
            self._t(f"{label}  {n}", b.x, y, font=self.f_sm, color=DIM)
            y += 15

    def _draw_aux_trip(self, sc: Scene, b: pygame.Rect):
        wps = getattr(sc.gns.fpl, "waypoints", [])
        if len(wps) < 2:
            self._t("no flight plan", b.x, b.y, font=self.f_sm, color=DIM)
            return
        leg_nm = [great_circle_nm(wps[i].pos, wps[i + 1].pos) for i in range(len(wps) - 1)]
        total = sum(leg_nm)
        active = getattr(sc.gns.fpl, "active", 1)
        dtg = getattr(sc.nav, "dtg_nm", None)
        if dtg is not None and getattr(sc.gns.fpl, "has_active_leg", False):
            remaining = dtg + sum(leg_nm[active:])
        else:
            remaining = total
        gs = getattr(sc.own, "gs_kt", 0.0)
        ete = remaining / gs * 60.0 if gs > 20 else None
        y = b.y
        self._t(f"{wps[0].ident} -> {wps[-1].ident}", b.x, y, font=self.f_sm, color=GPS_GREEN)
        y += 18
        self._t(f"TOTAL DIS   {total:6.1f} nm", b.x, y, font=self.f_sm, color=TEXT); y += 17
        self._t(f"DIS REMAIN  {remaining:6.1f} nm", b.x, y, font=self.f_sm, color=TEXT); y += 17
        self._t(f"GS          {gs:6.0f} kt", b.x, y, font=self.f_sm, color=TEXT); y += 17
        ete_txt = f"{int(ete):02d}:{int((ete * 60) % 60):02d}" if ete else "--:--"
        self._t(f"ETE         {ete_txt}", b.x, y, font=self.f_sm, color=TEXT)

    def _draw_aux_utility(self, sc: Scene, b: pygame.Rect):
        t = getattr(sc, "t", 0.0)
        hh, rem = divmod(int(t), 3600)
        mm, ss = divmod(rem, 60)
        y = b.y
        self._t("FLIGHT TIMER", b.x, y, font=self.f_sm, color=DIM)
        y += 17
        self.lcd(f"{hh:02d}:{mm:02d}:{ss:02d}", b.x, y, color=GPS_GREEN)
        y += 24
        own = sc.own
        self._t(f"GS   {getattr(own, 'gs_kt', 0.0):5.0f} kt", b.x, y, font=self.f_sm, color=TEXT)
        y += 15
        self._t(f"TAS  {getattr(own, 'tas_kt', 0.0):5.0f} kt", b.x, y, font=self.f_sm, color=TEXT)
        y += 15
        self._t(f"ALT  {getattr(own, 'altitude_ft', 0.0):6.0f} ft", b.x, y,
                font=self.f_sm, color=TEXT)

    def _draw_aux_setup(self, sc: Scene, b: pygame.Rect):
        gns = sc.gns
        variant = getattr(gns, "variant", None)
        on = getattr(gns.cursor, "cursor_on", False)
        y = b.y
        self._t(f"UNIT     {getattr(variant, 'name', '---')}", b.x, y, font=self.f_sm, color=TEXT)
        y += 17
        self._t(f"CDI SRC  {getattr(gns, 'cdi_source', 'GPS')}", b.x, y,
                font=self.f_sm, color=TEXT)
        y += 17
        self._t(f"BARO     {getattr(sc, 'baro_inhg', 29.92):.2f} in", b.x, y,
                font=self.f_sm, color=TEXT)
        y += 20
        alarm = getattr(gns, "cdi_alarm_max_nm", None)
        val = "AUTO" if alarm is None else (f"{alarm:.2f}" if alarm < 1.0 else f"{alarm:.1f}")
        mk = ">" if on else " "
        self._t(f"{mk} CDI/ALARMS  {val}", b.x, y, font=self.f_sm,
                color=AMBER if on else TEXT)
        y += 20
        self._t("NAV UNITS  nm / kt / ft", b.x, y, font=self.f_sm, color=DIM)

    def _wx_read(self, sc: Scene, ident: str, kind: str):
        """A cached-on-disk METAR/TAF (``datasrc.wx``) for one station, as
        ``(report, age_minutes)`` or ``None`` if nothing is cached - re-read
        at most every ``_WX_CACHE_TTL_S``. Nothing here fetches over the
        network; it only reads whatever `python -m datasrc.wx metar/taf`
        already cached. Re-reading a small JSON file every frame while parked
        on this page would be needless disk I/O, so the read is memoised
        against the session clock (`Scene.t`)."""
        key = (ident, kind)
        now = getattr(sc, "t", 0.0)
        cached = self._wx_read_cache.get(key)
        if cached is not None and now - cached[1] < _WX_CACHE_TTL_S:
            return cached[0]
        result = None
        try:
            from datasrc.wx import Metar, Taf, default_data_root, load_latest, staleness_minutes
            payload = load_latest(default_data_root(), kind, ident.upper())
            if payload and payload.get("stations"):
                cls = Metar if kind == "metar" else Taf
                result = (cls(**payload["stations"][0]), staleness_minutes(payload))
        except Exception:                          # noqa: BLE001 - display is best-effort
            result = None
        self._wx_read_cache[key] = (result, now)
        return result

    def _draw_aux_weather(self, sc: Scene, b: pygame.Rect):
        """METAR/TAF for the flight plan's airports (departure, enroute stops,
        destination), one station at a time - outer knob (cursor on) scrolls
        the station strip across the top. Text comes straight from
        `datasrc.wx`'s local cache (`python -m datasrc.wx metar/taf IDENT`
        fetches it); nothing here touches the network."""
        gns = sc.gns
        idents = gns.wx_station_idents() if hasattr(gns, "wx_station_idents") else []
        on = getattr(gns.cursor, "cursor_on", False)
        y = b.y
        if not idents:
            self._t("WEATHER", b.x, y, font=self.f_sm, color=DIM)
            y += 18
            self._t("no flight-plan airports", b.x, y, font=self.f_sm, color=DIM)
            return
        sel = max(0, min(len(idents) - 1, getattr(gns, "wx_sel", 0)))
        x = b.x
        for i, ident in enumerate(idents):
            picked = on and i == sel
            col = AMBER if picked else (GPS_GREEN if i == sel else DIM)
            r = self._t(f">{ident}<" if picked else f" {ident} ", x, y,
                       font=self.f_sm, color=col)
            # click-to-switch (the "stack" layout's only way to pick a
            # station - there's no bezel outer knob to turn there; harmless
            # to register on the "gps"/"dual" AUX Weather page too, since
            # main._on_stack_click is only ever consulted while
            # Scene.layout == "stack")
            self._stack_hit[f"wx:airport:{i}"] = r
            x = r.right + 2
        y += 18

        ident = idents[sel]
        metar_r = self._wx_read(sc, ident, "metar")
        taf_r = self._wx_read(sc, ident, "taf")
        max_w = b.width

        # One combined scrollable list (METAR then TAF) instead of a fixed
        # METAR/TAF split that just clipped whatever didn't fit - the 530's
        # small screen routinely can't show a full TAF at once, and CRSR
        # (cursor on, inner knob) now scrolls through all of it rather than
        # permanently hiding the tail end.
        lines: list[tuple[str, tuple[int, int, int]]] = [("METAR", DIM)]
        if metar_r is None:
            lines.append((f"no cached METAR - datasrc.wx metar {ident}", DIM))
        else:
            metar, age = metar_r
            lines += [(ln, TEXT) for ln in self._wrap(metar.raw or "(no data)", self.f_sm, max_w)]
            if age is not None:
                lines.append((f"{age:.0f} min ago", AMBER if age > 75 else DIM))
        lines.append(("", TEXT))
        lines.append(("TAF", DIM))
        if taf_r is None:
            lines.append((f"no cached TAF - datasrc.wx taf {ident}", DIM))
        else:
            taf, _age = taf_r
            lines += [(ln, TEXT) for ln in self._wrap(taf.raw or "(no data)", self.f_sm, max_w)]

        room = max(1, (b.bottom - y) // 14)
        top = max(0, min(max(0, len(lines) - room), getattr(gns, "wx_scroll", 0)))
        # write the clamped position back - GpsNav doesn't know the rendered
        # line count (same reason the Charts page's chart_sel is clamped
        # here, not there), so without this the raw wx_scroll counter could
        # run far past the real max on a fast scroll and the knob would then
        # have to un-scroll all the way back through the overshoot before
        # the screen visibly moved again, instead of just capping in place.
        if hasattr(gns, "wx_scroll"):
            gns.wx_scroll = top
        for text, col in lines[top:top + room]:
            self._t(text, b.x, y, font=self.f_sm, color=col)
            y += 14
        if len(lines) > room:
            more = ("^" if top > 0 else " ") + ("v" if top + room < len(lines) else " ")
            self._t(more, b.right - 2, b.y, font=self.f_sm, color=AMBER, right=True)

    def _dtpp_charts_for(self, ident: str) -> list:
        """Charts for one airport from the cached d-TPP index (see
        `datasrc.dtpp`), loaded once and held for the Renderer's lifetime."""
        if self._dtpp_index is None:
            try:
                from datasrc import dtpp
                from datasrc.airac import current_cycle
                self._dtpp_index = dtpp.load_index(dtpp.default_data_root(), current_cycle()) or []
            except Exception:                      # noqa: BLE001 - display is best-effort
                self._dtpp_index = []
        if not self._dtpp_index:
            return []
        from datasrc import dtpp
        return dtpp.charts_for_airport(self._dtpp_index, ident)

    def _draw_aux_charts(self, sc: Scene, b: pygame.Rect):
        """Approach plates / airport diagrams for the flight plan's airports,
        browsed by airport (outer knob) then by chart (inner knob); ENT
        fetches (if needed) and hands the PDF to the OS's default viewer -
        `main._open_selected_chart` does that real I/O, off the main thread,
        not this draw call. The chart list itself comes from `datasrc.dtpp`'s
        cached index (`python -m datasrc.dtpp update-index` fetches it);
        nothing here touches the network."""
        gns = sc.gns
        idents = gns.wx_station_idents() if hasattr(gns, "wx_station_idents") else []
        on = getattr(gns.cursor, "cursor_on", False)
        y = b.y
        if not idents:
            self._t("CHARTS", b.x, y, font=self.f_sm, color=DIM)
            y += 18
            self._t("no flight-plan airports", b.x, y, font=self.f_sm, color=DIM)
            return
        ai = max(0, min(len(idents) - 1, getattr(gns, "chart_airport_sel", 0)))
        x = b.x
        for i, ident in enumerate(idents):
            picked = on and i == ai
            col = AMBER if picked else (GPS_GREEN if i == ai else DIM)
            r = self._t(f">{ident}<" if picked else f" {ident} ", x, y,
                       font=self.f_sm, color=col)
            x = r.right + 2
        y += 18

        ident = idents[ai]
        charts = self._dtpp_charts_for(ident)
        if not charts:
            self._t(f"no charts cached for {ident}", b.x, y, font=self.f_sm, color=DIM)
            y += 14
            self._t("datasrc.dtpp update-index", b.x, y, font=self.f_sm, color=DIM)
            return

        ci = max(0, min(len(charts) - 1, getattr(gns, "chart_sel", 0)))
        room = max(1, (b.bottom - y) // 14)
        start = max(0, min(ci - room // 2, max(0, len(charts) - room)))
        for row, chart in enumerate(charts[start:start + room], start=start):
            picked = on and row == ci
            col = AMBER if picked else TEXT
            mk = ">" if picked else " "
            flag = (f" [{chart.useraction}]"
                    if chart.useraction and chart.useraction.upper() != "N" else "")
            self._t(f"{mk}{chart.chart_code:<4} {chart.chart_name}{flag}", b.x, y,
                    font=self.f_sm, color=col)
            y += 14
        if on:
            self._t("ENT=open", b.right - 4, b.bottom - 14, font=self.f_sm, color=DIM, right=True)

    def _cdi_strip(self, sc: Scene, r: pygame.Rect):
        pan = sc.panel
        cdi = getattr(pan, "cdi", None)
        cx, cy = r.centerx, r.centery
        pygame.draw.line(self.surf, EDGE, (r.x + 30, cy), (r.right - 30, cy))
        for k in (-2, -1, 1, 2):
            dx = k * (r.w / 2 - 34) / 2
            pygame.draw.circle(self.surf, DIM, (int(cx + dx), cy), 2)
        src = getattr(cdi, "source", "GPS")
        valid = getattr(cdi, "valid", False)
        self._t(src, r.x + 2, cy - 8, font=self.f_sm,
                color=GPS_GREEN if src == "GPS" else CYAN)
        svc = getattr(cdi, "service", "")
        if src == "GPS" and svc:                # a WAAS unit's flight-mode annunciation (LPV / LNAV / ENR ...)
            # same row as the "GPS" source label, right after it - not
            # stacked almost directly on top of it (both were landing within
            # 2px of each other at the left edge, so e.g. "TERM" visibly
            # overlapped "GPS")
            svc_x = r.x + 2 + self.f_sm.size(src)[0] + 8
            self._t(svc, svc_x, cy - 8, font=self.f_sm, color=GPS_GREEN)
        fs = getattr(cdi, "full_scale_nm", None)
        if src == "GPS" and fs:                 # numeric scale at both ends (sec.3.3)
            lbl = f"{fs:.2f}" if fs < 1.0 else f"{fs:.1f}"
            self._t(lbl, r.x + 26, r.bottom - 12, font=self.f_sm, color=DIM)
            self._t(lbl, r.right - 26, r.bottom - 12, font=self.f_sm, color=DIM, right=True)
        if valid:
            dfl = max(-1.0, min(1.0, getattr(cdi, "deflection", 0.0)))
            nx = cx + dfl * (r.w / 2 - 34)
            col = GPS_GREEN if src == "GPS" else CYAN
            pygame.draw.line(self.surf, col, (nx, r.y + 6), (nx, r.bottom - 6), 3)
            tf = getattr(cdi, "to_from", "")
            if tf in ("TO", "FROM"):
                # Pilot's Guide sec.1: "the TO/FROM arrow in the CENTER of
                # the scale" - a fixed mark at the scale's center, not one
                # that dodges the needle, so on course the two overlap
                pts = ([(cx, cy - 9), (cx - 7, cy + 4), (cx + 7, cy + 4)] if tf == "TO"
                       else [(cx, cy + 9), (cx - 7, cy - 4), (cx + 7, cy - 4)])
                pygame.draw.polygon(self.surf, col, pts)
                self._t("TO" if tf == "TO" else "FR", r.right - 2, cy - 8,
                        font=self.f_sm, color=col, right=True)
        else:
            self._t("--FLAG--", cx, cy - 8, font=self.f_sm, color=RED, center=True)

    def _bezel_labels(self, sc: Scene, x0, y, w):
        keys = ["D>", "MENU", "CLR", "ENT", "CRSR", "OBS", "MSG", "FPL"]
        cw = w / len(keys)
        for i, k in enumerate(keys):
            rx = x0 + i * cw + cw / 2
            pygame.draw.rect(self.surf, (30, 36, 42),
                             (x0 + i * cw + 4, y, cw - 8, 22), border_radius=4)
            self._t(k, rx, y + 3, font=self.f_sm, color=DIM, center=True)
        self._t("outer: page group / field    inner: page / value", x0 + 6, y + 30,
                font=self.f_sm, color=DIM)
        obs = "OBS " + f"{sc.gns.obs_course:03.0f}" if getattr(sc.gns, "obs_active", False) else ""
        if obs:
            self._t(obs, x0 + w - 6, y + 30, font=self.f_sm, color=AMBER, right=True)

    # -- moving map (right, top) --------------------------------
    def _map(self, sc: Scene, rect: pygame.Rect | None = None):
        """The track-up moving map. Also reused, at a smaller size, for the
        in-screen NAV / Map page (`_draw_map_page`) - same drawing, just a
        different rect and a tighter default range."""
        if rect is None:
            x0 = GNS_W + MAP_MARGIN
            y0 = 32
            w = WIN_W - x0 - MAP_MARGIN
            h = WIN_H - y0 - 210
            rect = pygame.Rect(x0, y0, w, h)
        h = rect.height
        pygame.draw.rect(self.surf, (4, 6, 8), rect)
        pygame.draw.rect(self.surf, EDGE, rect, width=1)
        prev = self.surf.get_clip()
        self.surf.set_clip(rect)

        own = sc.own
        cx, cy = rect.centerx, rect.bottom - h * 0.32
        rng = max(2.0, sc.map_range_nm)
        px_per_nm = (h * 0.62) / rng
        track_up = getattr(own, "track_deg", 0.0)

        # range rings
        for frac in (0.5, 1.0):
            pygame.draw.circle(self.surf, (26, 32, 38), (cx, int(cy)),
                               int(rng * frac * px_per_nm), 1)
        self._t(f"{rng:.0f}nm", rect.right - 6, rect.y + 4, font=self.f_sm,
                color=DIM, right=True)
        self._t("TRK UP", rect.x + 6, rect.y + 4, font=self.f_sm, color=DIM)

        def project(p: Point):
            d = great_circle_nm(own.pos, p)
            rel = math.radians(initial_bearing(own.pos, p) - track_up)
            return (cx + d * px_per_nm * math.sin(rel),
                    cy - d * px_per_nm * math.cos(rel))

        # label placement with overlap declutter
        placed: list = []

        def place(text, x, y, color, *, force=False):
            img = self.f_sm.render(text, True, color)
            rr = img.get_rect(topleft=(x, y))
            if not force:
                if not rect.contains(rr) or any(rr.colliderect(q) for q in placed):
                    return
            placed.append(rr)
            self.surf.blit(img, rr)

        # Class B/C/D airspace (F62) - sectional-chart-like outline colors; own-position bounding-box prefilter
        # so a nationwide airspace list costs nothing once off-screen.
        db = getattr(sc, "db", None)
        if db is not None and getattr(db, "airspaces", None):
            # `Airspace.near` is an O(1) bounding-box reject (precomputed once
            # at load) - walking every one of a nationwide list's boundary
            # points here, every frame, was the map's share of a real
            # playtest CPU/framerate regression (F62's ~42k total points).
            _AIRSPACE_COLOR = {"B": (60, 110, 220), "C": (200, 60, 190), "D": (60, 130, 210)}
            for aw in db.airspaces:
                if not aw.rings or not aw.near(own.pos, rng * 1.2):
                    continue
                col = _AIRSPACE_COLOR.get(aw.cls, (90, 90, 90))
                for ring in aw.rings:
                    poly = [project(p) for p in ring]
                    if aw.cls == "D":                     # sectional convention: Class D is a dashed blue line
                        for i in range(len(poly)):
                            a, b_ = poly[i], poly[(i + 1) % len(poly)]
                            seg = math.hypot(b_[0] - a[0], b_[1] - a[1])
                            n = max(1, int(seg // 6))
                            for k in range(0, n, 2):
                                t0, t1 = k / n, min(1.0, (k + 1) / n)
                                pygame.draw.line(self.surf, col,
                                                 (a[0] + (b_[0] - a[0]) * t0, a[1] + (b_[1] - a[1]) * t0),
                                                 (a[0] + (b_[0] - a[0]) * t1, a[1] + (b_[1] - a[1]) * t1), 1)
                    else:
                        pygame.draw.lines(self.surf, col, True, poly, 1)

        # flight-plan legs (labels are forced - they always win)
        wps = getattr(sc.gns.fpl, "waypoints", [])
        active = getattr(sc.gns.fpl, "active", 1)
        dto_on = getattr(sc.gns, "dto", None) is not None
        pts = [project(w.pos) for w in wps]
        for i in range(1, len(pts)):
            is_active = i == active and not dto_on   # DTO course owns the magenta
            col = MAGENTA if is_active else (150, 155, 160)
            wpi = wps[i]
            if getattr(wpi, "arc_centre", None) is not None:      # DME arc: draw the curve
                curve = [project(p) for p in arc_points(
                    wpi.arc_centre, wps[i - 1].pos, wpi.pos, wpi.arc_turn)]
                pygame.draw.lines(self.surf, col, False, curve, 2 if is_active else 1)
            else:
                pygame.draw.line(self.surf, col, pts[i - 1], pts[i], 2 if is_active else 1)
        gs_kt = getattr(sc.own, "gs_kt", 0.0) or 0.0
        for wp, sp in zip(wps, pts):
            x, yy = int(sp[0]), int(sp[1])
            if getattr(wp, "is_map", False):             # MAP: amber X
                pygame.draw.line(self.surf, AMBER, (x - 4, yy - 4), (x + 4, yy + 4), 2)
                pygame.draw.line(self.surf, AMBER, (x - 4, yy + 4), (x + 4, yy - 4), 2)
            elif getattr(wp, "hold", False):             # hold: the actual racetrack, not a bare ring
                inbound = wp.hold_inbound_true if wp.hold_inbound_true is not None else 0.0
                leg_nm = wp.hold_leg_nm or max(
                    0.5, (gs_kt if gs_kt > 20.0 else 90.0) / 60.0 *
                    (wp.hold_leg_min if wp.hold_leg_min is not None else 1.0))
                track = [project(p) for p in
                         _hold_track_points(wp.pos, inbound, wp.hold_turn or "R", leg_nm)]
                pygame.draw.lines(self.surf, AMBER, True, track, 1)
                pygame.draw.circle(self.surf, AMBER, (x, yy), 2)
            elif wp.ident == "PT" and getattr(wp, "synthetic", False) \
                    and wp.hold_inbound_true is not None:      # procedure turn: a chevron, not a dot
                chevron = [project(p) for p in _pt_symbol_points(wp.pos, wp.hold_inbound_true)]
                pygame.draw.lines(self.surf, AMBER, False, chevron, 2)
            elif getattr(wp, "is_faf", False):           # FAF: filled
                pygame.draw.circle(self.surf, WHITE, (x, yy), 3)
            else:
                pygame.draw.circle(self.surf, WHITE, (x, yy), 3, 1)
            place(wp.ident, sp[0] + 5, sp[1] - 6, DIM, force=True)

        # DTO course
        dto = getattr(sc.gns, "dto", None)
        if dto is not None:
            pygame.draw.line(self.surf, MAGENTA, (cx, cy), project(dto.target.pos), 2)

        # nearby fixes - dot always, label only if it fits clear
        for ident, p, kind in getattr(sc, "nearby", []):
            sp = project(p)
            if not rect.collidepoint(sp):
                continue
            c = CYAN if kind == "navaid" else (DIM if kind == "wpt" else GPS_GREEN)
            pygame.draw.circle(self.surf, c, sp, 2)
            place(ident, sp[0] + 4, sp[1] - 6, c)

        # ownship
        self._ownship_symbol(cx, cy)
        self.surf.set_clip(prev)

    def _ownship_symbol(self, cx, cy):
        pts = [(cx, cy - 9), (cx - 7, cy + 7), (cx, cy + 3), (cx + 7, cy + 7)]
        pygame.draw.polygon(self.surf, WHITE, pts)
        pygame.draw.polygon(self.surf, (0, 0, 0), pts, 1)

    # -- HSI + DME + markers (right, bottom) --------------------
    def _hsi(self, sc: Scene):
        x0 = GNS_W + MAP_MARGIN
        top = WIN_H - 210 + 32 - 24
        rect = pygame.Rect(x0, top, WIN_W - x0 - MAP_MARGIN, WIN_H - top - 12)
        pygame.draw.rect(self.surf, PANEL, rect, border_radius=6)
        pygame.draw.rect(self.surf, EDGE, rect, width=1, border_radius=6)

        pan, own, nav = sc.panel, sc.own, sc.nav
        R = min(rect.h, rect.w * 0.45) / 2 - 14
        cx, cy = rect.x + R + 20, rect.centery
        hdg = getattr(pan, "hsi_heading_deg", 0.0)

        pygame.draw.circle(self.surf, (6, 9, 11), (int(cx), int(cy)), int(R))
        pygame.draw.circle(self.surf, EDGE, (int(cx), int(cy)), int(R), 1)
        for deg in range(0, 360, 30):
            a = math.radians(deg - hdg)
            x1 = cx + (R - 8) * math.sin(a); y1 = cy - (R - 8) * math.cos(a)
            x2 = cx + R * math.sin(a); y2 = cy - R * math.cos(a)
            pygame.draw.line(self.surf, DIM, (x1, y1), (x2, y2))
        # lubber line
        pygame.draw.polygon(self.surf, WHITE,
                            [(cx, cy - R - 2), (cx - 6, cy - R - 12), (cx + 6, cy - R - 12)])
        self._t(f"{hdg:03.0f}°", cx, cy - 8, font=self.f_lg, color=WHITE, center=True)

        # course pointer + CDI bar
        cdi = getattr(pan, "cdi", None)
        crs = getattr(cdi, "course_deg", 0.0)
        ca = math.radians(crs - hdg)
        col = GPS_GREEN if getattr(cdi, "source", "GPS") == "GPS" else CYAN
        for sgn in (1, -1):
            ex = cx + sgn * R * math.sin(ca); ey = cy - sgn * R * math.cos(ca)
            pygame.draw.line(self.surf, col, (cx, cy), (ex, ey), 2)
        if getattr(cdi, "valid", False):
            dfl = max(-1.0, min(1.0, getattr(cdi, "deflection", 0.0)))
            # perpendicular offset of the deviation bar
            px = math.cos(ca) * dfl * (R * 0.7)
            py = math.sin(ca) * dfl * (R * 0.7)
            bx, by = cx + px, cy + py
            pygame.draw.line(self.surf, col,
                             (bx - 0.5 * R * math.sin(ca), by + 0.5 * R * math.cos(ca)),
                             (bx + 0.5 * R * math.sin(ca), by - 0.5 * R * math.cos(ca)), 3)

        # bearing pointers
        for bp, style in ((getattr(pan, "bearing1", None), "single"),
                          (getattr(pan, "bearing2", None), "double")):
            if bp is None or not getattr(bp, "valid", False) or bp.bearing_deg is None:
                continue
            ba = math.radians(bp.bearing_deg - hdg)
            hx = cx + (R - 4) * math.sin(ba); hy = cy - (R - 4) * math.cos(ba)
            tx = cx - (R - 4) * math.sin(ba); ty = cy + (R - 4) * math.cos(ba)
            c = CYAN if bp.source == "ADF" else GPS_GREEN
            pygame.draw.line(self.surf, c, (tx, ty), (hx, hy), 2)
            pygame.draw.circle(self.surf, c, (int(hx), int(hy)), 4, 1 if style == "double" else 0)

        # right side: DME window + markers + speeds
        rx = cx + R + 30
        dme = getattr(pan, "dme", None)
        self._t("DME", rx, rect.y + 12, font=self.f_sm, color=DIM)
        if dme is not None and getattr(dme, "valid", False):
            self._t(f"{dme.distance_nm:5.1f}nm", rx, rect.y + 28, font=self.f_md, color=GPS_GREEN)
            self._t(dme.ident or "", rx, rect.y + 48, font=self.f_sm, color=DIM)
            if dme.time_min is not None:
                self._t(f"{dme.time_min:4.1f}min", rx, rect.y + 64, font=self.f_sm, color=TEXT)
        else:
            self._t("  ---", rx, rect.y + 28, font=self.f_md, color=DIM)

        mk = getattr(pan, "markers", None)
        lamps = [("O", getattr(mk, "outer", False), CYAN),
                 ("M", getattr(mk, "middle", False), AMBER),
                 ("I", getattr(mk, "inner", False), WHITE)]
        for i, (lab, on, c) in enumerate(lamps):
            lr = pygame.Rect(rx + i * 34, rect.y + 92, 28, 20)
            pygame.draw.rect(self.surf, c if on else (40, 44, 48), lr, border_radius=4)
            self._t(lab, lr.centerx, lr.y + 2, font=self.f_sm,
                    color=(0, 0, 0) if on else DIM, center=True)

        self._t(f"ALT {getattr(own,'altitude_ft',0):6.0f}", rx, rect.bottom - 54,
                font=self.f_sm, color=TEXT)
        self._t(f"VS  {getattr(own,'vs_fpm',0):+5.0f}", rx, rect.bottom - 38,
                font=self.f_sm, color=TEXT)
        ias = getattr(own, "ias_kt", None) or getattr(own, "tas_kt", 0.0)
        bug = f"  bug {sc.ias_target:3.0f}" if getattr(sc, "ias_managed", False) else ""
        self._t(f"IAS {ias:4.0f}{bug}", rx, rect.bottom - 22,
                font=self.f_sm, color=TEXT)


# ========================================================================= #
# steam-gauge widgets (module-level; take the Renderer `r` for fonts/text)
# ========================================================================= #
def _panel_box(surf, rect, title, r):
    pygame.draw.rect(surf, PANEL, rect, border_radius=6)
    pygame.draw.rect(surf, EDGE, rect, width=1, border_radius=6)
    if title:
        r._t(title, rect.x + 8, rect.y + 4, font=r.f_sm, color=DIM)


def _dial(surf, cx, cy, rad, *, ticks=12):
    pygame.draw.circle(surf, (6, 9, 11), (int(cx), int(cy)), int(rad))
    pygame.draw.circle(surf, EDGE, (int(cx), int(cy)), int(rad), 1)
    for i in range(ticks):
        a = math.radians(i * 360 / ticks)
        x1 = cx + (rad - 6) * math.sin(a); y1 = cy - (rad - 6) * math.cos(a)
        x2 = cx + rad * math.sin(a); y2 = cy - rad * math.cos(a)
        pygame.draw.line(surf, DIM, (x1, y1), (x2, y2))


def _needle(surf, cx, cy, ang_deg, length, color, width=2, back=0.0):
    a = math.radians(ang_deg)
    hx = cx + length * math.sin(a); hy = cy - length * math.cos(a)
    tx = cx - back * math.sin(a); ty = cy + back * math.cos(a)
    pygame.draw.line(surf, color, (tx, ty), (hx, hy), width)


def _windowed_text(surf, cx, cy, text, font, color, r):
    """A small opaque "Kollsman window" style readout: on a round dial the
    sweeping needle can pass behind any fixed point on the face, so a bare
    label there is unreadable at the wrong moment. Drawn last (after the
    needle), an opaque backing box keeps it legible regardless."""
    img = font.render(text, True, color)
    box = img.get_rect(center=(int(cx), int(cy))).inflate(8, 8)
    pygame.draw.rect(surf, (8, 10, 12), box, border_radius=2)
    pygame.draw.rect(surf, EDGE, box, width=1, border_radius=2)
    r._t(text, cx, cy - img.get_height() // 2, font=font, color=color, center=True)


def six_pack_gauge_radius(rect):
    """Radius of one six-pack cell's dial - shared so the NAV heads match."""
    return min(rect.w / 3, rect.h / 2) / 2 - 12


def _hdg_card(surf, hdx, hdy, rad, hd, hdg_bug, r):
    """The directional-gyro card itself: dial + rotating labels, the fixed
    lubber-line index, an optional heading-bug marker, the fixed ownship
    symbol, and the digital heading readout. Shared by `draw_six_pack`'s
    HDG cell and the "stack" layout's standalone `draw_hdg_indicator` so
    there is exactly one heading-card drawing (labels sit well inboard of
    the rim, at ``rad - 24``, so the lubber line and bug - which both ride
    the rim - never print through a tick's number)."""
    _dial(surf, hdx, hdy, rad)
    for d in range(0, 360, 30):
        a = math.radians(d - hd)
        lab = "N" if d == 0 else "E" if d == 90 else "S" if d == 180 else "W" if d == 270 else str(d // 10)
        lx = hdx + (rad - 24) * math.sin(a); ly = hdy - (rad - 24) * math.cos(a)
        r._t(lab, lx, ly - 6, font=r.f_sm, color=TEXT, center=True)
    pygame.draw.polygon(surf, AMBER, [(hdx, hdy - rad + 2), (hdx - 5, hdy - rad + 12),
                                      (hdx + 5, hdy - rad + 12)])
    if hdg_bug is not None:                     # selected-heading bug on the card
        a = math.radians(hdg_bug - hd)
        bx = hdx + (rad - 3) * math.sin(a); by = hdy - (rad - 3) * math.cos(a)
        px, py = math.cos(a), math.sin(a)      # tangent to the card
        pygame.draw.polygon(surf, CYAN, [
            (bx - px * 6 - math.sin(a) * 5, by - py * 6 + math.cos(a) * 5),
            (bx + px * 6 - math.sin(a) * 5, by + py * 6 + math.cos(a) * 5),
            (bx + px * 6, by + py * 6), (bx - px * 6, by - py * 6)])
    # fixed ownship symbol - the card rotates under it, the little airplane
    # (nose up, wings level) never moves, same as a real directional gyro.
    pygame.draw.line(surf, WHITE, (hdx, hdy - rad * 0.22), (hdx, hdy + rad * 0.12), 2)
    pygame.draw.line(surf, WHITE, (hdx - rad * 0.24, hdy), (hdx + rad * 0.24, hdy), 3)
    pygame.draw.line(surf, WHITE, (hdx - rad * 0.09, hdy + rad * 0.14),
                     (hdx + rad * 0.09, hdy + rad * 0.14), 2)
    _windowed_text(surf, hdx, hdy + rad * 0.3, f"{hd:03.0f}", r.f_lcd, WHITE, r)


def draw_hdg_indicator(surf, rect, sp, r, hdg_bug=None):
    """Standalone directional-gyro/heading-indicator instrument - the
    "stack" layout's middle column, below NAV1/NAV2 (see WORKING.md). Same
    card as one cell of `draw_six_pack`, in its own titled box - left-biased
    via `_card_geometry`, same as `draw_nav_head`'s VOR/LOC card, so the
    actual-value info column `Renderer._stack_hdg_actuals` draws to its
    right starts exactly where the dial ends (a centered dial and a
    left-biased info-column position, computed separately, used to overlap)."""
    _panel_box(surf, rect, "HDG", r)
    if sp is None:
        r._t("no data", rect.centerx, rect.centery, font=r.f_sm, color=DIM, center=True)
        return
    rad, cx, cy = _card_geometry(rect, None)
    _hdg_card(surf, cx, cy, rad, getattr(sp, "heading_deg", 0.0), hdg_bug, r)


def draw_six_pack(surf, rect, sp, r, baro_inhg=29.92, hdg_bug=None, spd_bug=None):
    _panel_box(surf, rect, "", r)
    cols, rows = 3, 2
    cw = rect.w / cols
    ch = rect.h / rows
    rad = six_pack_gauge_radius(rect)
    centers = [(rect.x + cw * (i % cols) + cw / 2, rect.y + ch * (i // cols) + ch / 2)
               for i in range(6)]
    labels = ["A/S", "ATT", "ALT", "T/C", "HDG", "V/S"]
    (asx, asy), (atx, aty), (alx, aly), (tcx, tcy), (hdx, hdy), (vsx, vsy) = centers

    # airspeed: 40 kt at bottom-left (~210 deg), ~ 260 deg span to 200 kt
    _dial(surf, asx, asy, rad)
    spd = max(0.0, getattr(sp, "airspeed_kt", 0.0))

    def _as_ang(v):
        return math.radians(30 + min(max(v, 0.0), 200) / 200 * 300)

    if spd_bug:                                    # pseudo speed-manager set-point bug
        ba = _as_ang(spd_bug)
        pygame.draw.line(surf, CYAN,
                         (asx + (rad - 5) * math.sin(ba), asy - (rad - 5) * math.cos(ba)),
                         (asx + (rad + 3) * math.sin(ba), asy - (rad + 3) * math.cos(ba)), 3)
    _needle(surf, asx, asy, math.degrees(_as_ang(spd)), rad - 6, WHITE, 2, back=rad * 0.25)
    _windowed_text(surf, asx, asy + rad * 0.34, f"{spd:.0f}", r.f_lcd, CYAN, r)
    if spd_bug:
        _windowed_text(surf, asx, asy - rad * 0.52, f"bug {spd_bug:.0f}", r.f_sm, CYAN, r)

    # attitude indicator
    _attitude(surf, atx, aty, rad, getattr(sp, "pitch_deg", 0.0), getattr(sp, "bank_deg", 0.0))

    # altimeter: 100-ft hand + 1000-ft hand; indicated alt reflects the Kollsman
    _dial(surf, alx, aly, rad)
    pa = getattr(sp, "altitude_ft", 0.0)
    alt = pa + (baro_inhg - 29.92) * 1000.0             # ~1000 ft / inHg
    _needle(surf, alx, aly, (alt % 1000) / 1000 * 360, rad - 6, WHITE, 3, back=rad * 0.2)
    _needle(surf, alx, aly, (alt % 10000) / 10000 * 360, rad * 0.6, WHITE, 2, back=rad * 0.2)
    _windowed_text(surf, alx, aly + rad * 0.34, f"{alt:.0f}", r.f_lcd, CYAN, r)
    _windowed_text(surf, alx, aly - rad * 0.52, f"{baro_inhg:.2f}", r.f_sm, AMBER, r)

    # turn coordinator: little wings banked by turn rate; slip ball. The
    # "2 MIN" doghouse reference marks sit at the wing angle (wa) a standard-
    # rate turn (3 deg/s) produces in this same wa = f(turn_rate) mapping, so
    # a pilot flying standard rate sees the wingtip align with the marks.
    _dial(surf, tcx, tcy, rad, ticks=4)
    std_wa = math.radians(20.0)                        # wa at tr == 3 deg/s (standard rate)
    for sgn in (-1, 1):
        wx = tcx + sgn * math.cos(std_wa) * (rad - 8)
        wy = tcy + math.sin(std_wa) * (rad - 8)
        pygame.draw.line(surf, DIM, (wx - sgn * 5, wy - 4), (wx + sgn * 5, wy + 4), 2)
    tr = getattr(sp, "turn_rate_dps", 0.0)
    wa = math.radians(_clamp(tr / 3.0, -1.5, 1.5) * 20.0)
    dx, dy = math.cos(wa) * (rad - 8), math.sin(wa) * (rad - 8)
    pygame.draw.line(surf, WHITE, (tcx - dx, tcy - dy), (tcx + dx, tcy + dy), 3)
    bx = tcx + _clamp(getattr(sp, "slip_skid", 0.0), -1, 1) * rad * 0.4
    pygame.draw.circle(surf, CYAN, (int(bx), int(tcy + rad * 0.55)), 4)
    r._t("2 MIN", tcx, tcy + rad * 0.62, font=r.f_sm, color=DIM, center=True)

    _hdg_card(surf, hdx, hdy, rad, getattr(sp, "heading_deg", 0.0), hdg_bug, r)

    # VSI: 0 at 9 o'clock (270 deg), +/-2000 fpm over +/-160 deg
    _dial(surf, vsx, vsy, rad)
    vs = _clamp(getattr(sp, "vsi_fpm", 0.0), -2000, 2000)
    _needle(surf, vsx, vsy, 270 + vs / 2000 * 160, rad - 6, WHITE, 2, back=rad * 0.2)
    _windowed_text(surf, vsx, vsy + rad * 0.34, f"{vs:.0f}", r.f_lcd, CYAN, r)

    for (lx, ly), lab in zip(centers, labels):
        r._t(lab, lx, ly - rad - 12, font=r.f_sm, color=DIM, center=True)


def _attitude(surf, cx, cy, rad, pitch, bank):
    prev = surf.get_clip()
    clip = pygame.Rect(int(cx - rad), int(cy - rad), int(rad * 2), int(rad * 2))
    surf.set_clip(clip)
    ppx = _clamp(pitch, -25, 25) * (rad / 25.0) * 0.8
    ba = math.radians(-bank)
    ux, uy = math.sin(ba), -math.cos(ba)        # unit normal toward the SKY (0,-1 level)
    rx, ry = math.cos(ba), math.sin(ba)         # along the horizon (1,0 at wings level)
    hx, hy = cx - ppx * ux, cy - ppx * uy       # nose up -> horizon moves down
    big = rad * 2
    # sky quad sits on the +u (up) side of the horizon, ground on the -u side
    sky = [(hx - rx * big + ux * big, hy - ry * big + uy * big),
           (hx + rx * big + ux * big, hy + ry * big + uy * big),
           (hx + rx * big, hy + ry * big), (hx - rx * big, hy - ry * big)]
    gnd = [(hx - rx * big, hy - ry * big), (hx + rx * big, hy + ry * big),
           (hx + rx * big - ux * big, hy + ry * big - uy * big),
           (hx - rx * big - ux * big, hy - ry * big - uy * big)]
    pygame.draw.polygon(surf, (70, 120, 190), sky)     # blue over ...
    pygame.draw.polygon(surf, (120, 85, 55), gnd)      # ... brown
    pygame.draw.line(surf, WHITE, (hx - rx * rad, hy - ry * rad),
                     (hx + rx * rad, hy + ry * rad), 2)
    # mask the square corners back to the panel colour -> a round instrument
    pygame.draw.circle(surf, PANEL, (int(cx), int(cy)), int(rad * 1.7), int(rad * 0.82))
    surf.set_clip(prev)
    pygame.draw.circle(surf, EDGE, (int(cx), int(cy)), int(rad), 1)

    # roll scale: ticks at 10/20/30/45/60 around the top, plus a bank pointer
    for mark in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
        a = math.radians(mark)
        long = mark in (0, -30, 30, -60, 60)
        r0 = rad - (7 if long else 4)
        x1 = cx + r0 * math.sin(a); y1 = cy - r0 * math.cos(a)
        x2 = cx + rad * math.sin(a); y2 = cy - rad * math.cos(a)
        pygame.draw.line(surf, DIM if not long else WHITE, (x1, y1), (x2, y2), 1)
    pa = math.radians(_clamp(bank, -60, 60))
    tip = (cx + (rad - 8) * math.sin(pa), cy - (rad - 8) * math.cos(pa))
    b1 = (cx + (rad - 16) * math.sin(pa - 0.06), cy - (rad - 16) * math.cos(pa - 0.06))
    b2 = (cx + (rad - 16) * math.sin(pa + 0.06), cy - (rad - 16) * math.cos(pa + 0.06))
    pygame.draw.polygon(surf, AMBER, [tip, b1, b2])

    # fixed aircraft reference
    pygame.draw.line(surf, AMBER, (cx - rad * 0.5, cy), (cx - rad * 0.15, cy), 3)
    pygame.draw.line(surf, AMBER, (cx + rad * 0.15, cy), (cx + rad * 0.5, cy), 3)
    pygame.draw.circle(surf, AMBER, (int(cx), int(cy)), 2)


def _cdi_card(surf, cx, cy, rad, card_up_deg, course_deg, deflection, valid,
              color, *, to_from="OFF", r=None):
    """Shared VOR/HSI face: compass card, course arrow, deviation bar."""
    _dial(surf, cx, cy, rad)
    if r is not None:
        for d in range(0, 360, 30):
            a = math.radians(d - card_up_deg)
            lab = ("N" if d == 0 else "E" if d == 90 else "S" if d == 180
                   else "W" if d == 270 else str(d // 10))
            lx = cx + (rad - 14) * math.sin(a); ly = cy - (rad - 14) * math.cos(a)
            r._t(lab, lx, ly - 6, font=r.f_sm, color=TEXT, center=True)
    ca = math.radians(course_deg - card_up_deg)
    for sgn in (1, -1):
        ex = cx + sgn * (rad - 4) * math.sin(ca); ey = cy - sgn * (rad - 4) * math.cos(ca)
        pygame.draw.line(surf, color, (cx, cy), (ex, ey), 2 if sgn == 1 else 1)
    # arrowhead
    hx = cx + (rad - 4) * math.sin(ca); hy = cy - (rad - 4) * math.cos(ca)
    pygame.draw.circle(surf, color, (int(hx), int(hy)), 4)
    for dot in (-2, -1, 1, 2):
        px = cx + math.cos(ca) * dot * rad * 0.28
        py = cy + math.sin(ca) * dot * rad * 0.28
        pygame.draw.circle(surf, DIM, (int(px), int(py)), 2)
    if valid:
        off = _clamp(deflection, -1, 1) * rad * 0.56
        bx = cx + math.cos(ca) * off; by = cy + math.sin(ca) * off
        pygame.draw.line(surf, color,
                         (bx - math.sin(ca) * rad * 0.5, by + math.cos(ca) * rad * 0.5),
                         (bx + math.sin(ca) * rad * 0.5, by - math.cos(ca) * rad * 0.5), 3)
    if to_from == "TO":
        pygame.draw.polygon(surf, color, [(cx + math.sin(ca) * 10, cy - math.cos(ca) * 10),
                                          (cx + math.sin(ca) * 10 - 6, cy - math.cos(ca) * 10 + 8),
                                          (cx + math.sin(ca) * 10 + 6, cy - math.cos(ca) * 10 + 8)])


def _vor_cdi_face(surf, cx, cy, rad, course_deg, deflection, valid, color,
                  *, to_from="OFF", is_loc=False, r=None):
    """Old-school round VOR/LOC indicator: a course card that rotates the
    selected radial to the top under a fixed index, a centre CDI needle that
    swings left/right, a 5-dot scale, and a TO/FROM triangle."""
    _dial(surf, cx, cy, rad)
    # rotating compass card - selected course to the top. Card numbers are kept
    # well inboard of the rim (rad - 22, not rad - 14) so they never collide
    # with the fixed course index below, which rides the rim itself and would
    # otherwise print right through whichever tick lands near the top.
    if r is not None:
        for d in range(0, 360, 30):
            a = math.radians(d - course_deg)
            lab = ("N" if d == 0 else "E" if d == 90 else "S" if d == 180
                   else "W" if d == 270 else str(d // 10))
            lx = cx + (rad - 22) * math.sin(a); ly = cy - (rad - 22) * math.cos(a)
            r._t(lab, lx, ly - 6, font=r.f_sm, color=TEXT, center=True)
    # fixed course index (top) + reciprocal stub (bottom)
    pygame.draw.polygon(surf, color, [(cx, cy - rad + 3), (cx - 6, cy - rad + 13),
                                      (cx + 6, cy - rad + 13)])
    pygame.draw.line(surf, color, (cx, cy + rad - 13), (cx, cy + rad - 3), 2)
    # deviation scale: dots across the middle
    for dot in (-2, -1, 1, 2):
        pygame.draw.circle(surf, DIM, (int(cx + dot * rad * 0.28), int(cy)), 3, 1)
    if valid:
        # the CDI needle - a vertical bar translated by the deviation
        nx = cx + _clamp(deflection, -1, 1) * rad * 0.56
        pygame.draw.line(surf, color, (nx, cy - rad * 0.60), (nx, cy + rad * 0.60), 4)
        if not is_loc and to_from in ("TO", "FROM"):
            # Pilot's Guide sec.1: "the TO/FROM arrow in the CENTER of the
            # scale" - a fixed mark at the scale's own center (cx), not
            # offset to one side, so on course it overlaps the needle
            # instead of permanently sitting apart from it
            up = to_from == "TO"
            ty = cy - rad * 0.34 if up else cy + rad * 0.34
            pts = ([(cx, ty - 9), (cx - 8, ty + 5), (cx + 8, ty + 5)] if up else
                   [(cx, ty + 9), (cx - 8, ty - 5), (cx + 8, ty - 5)])
            pygame.draw.polygon(surf, color, pts)
    elif r is not None:                              # OFF / NAV flag
        pygame.draw.rect(surf, (66, 40, 40), (int(cx - 18), int(cy - 9), 36, 18))
        r._t("OFF", cx, cy - 8, font=r.f_sm, color=RED, center=True)


def _gs_scale(surf, cx, cy, rad, gs_deflection, gs_valid, r=None):
    """Glideslope on the face of the round VOR/LOC head, as on a GA OBS/ILS
    indicator: a dot scale down the left and right edges of the dial and a
    HORIZONTAL needle that sweeps across the whole face (above centre = fly
    up), replaced by a red "GS" flag whenever the glideslope isn't valid."""
    xl, xr = cx - rad * 0.80, cx + rad * 0.80
    for dot in (-2, -1, 1, 2):
        dy = int(cy + dot * rad * 0.32)
        pygame.draw.circle(surf, WHITE, (int(xl), dy), 2)
        pygame.draw.circle(surf, WHITE, (int(xr), dy), 2)
    pygame.draw.circle(surf, WHITE, (int(xl), int(cy)), 3, 1)     # the centre marks
    pygame.draw.circle(surf, WHITE, (int(xr), int(cy)), 3, 1)
    if gs_valid:
        gy = int(cy - _clamp(gs_deflection, -1, 1) * rad * 0.64)   # + = fly up
        pygame.draw.line(surf, GPS_GREEN, (int(xl) + 8, gy), (int(xr) - 8, gy), 3)
    else:
        flag = pygame.Rect(0, 0, 26, 16)
        flag.center = (int(cx + rad * 0.5), int(cy + rad * 0.42))
        pygame.draw.rect(surf, (66, 40, 40), flag)
        if r is not None:
            r._t("GS", flag.centerx, flag.y + 1, font=r.f_sm, color=RED, center=True)


def _card_geometry(rect, radius):
    """Fixed-radius compass card, left-biased in its box with the dial centred in
    the space below the panel title, so several heads share one diameter."""
    if radius is None:
        rad = min(rect.w * 0.5, rect.h * 0.5) - 26
        return rad, min(rect.centerx, rect.x + rad + 20), rect.centery + 6
    rad = radius
    cx = rect.x + rad + 18
    cy = _clamp(rect.y + 19 + rad, rect.top + rad + 5, rect.bottom - rad - 2)
    return rad, cx, cy


def _ident_dot(surf, x, y, ident, t, color):
    """A small dot beside a station ident, blinking in time with its Morse
    ident (FINDINGS D8 remainder) - lets the pilot positively ID the station
    the way the audible ident does on a real receiver."""
    if not ident or ident == "---":
        return
    from radios import morse_is_keyed
    keyed = morse_is_keyed(ident, t)
    pygame.draw.circle(surf, color if keyed else DIM, (x, y), 3)


def draw_nav_head(surf, rect, nh, label, r, radius=None, t=0.0, source_kind=None):
    """``source_kind`` overrides the auto "LOC"/"VOR" label - pass "GPS" for
    a NAV1 head built from `instruments.gps_nav_head` (the round CDI slaved
    to the GNS's own CDI/VLOC switch: GPS course deviation while the GNS
    shows GPS, the plain VOR/LOC receiver reading once it's VLOC - see
    FINDINGS.md). A GPS "ident" is a waypoint, not a station that sends
    Morse, so the ident-blink dot and the "DME" label (-> "DIS") are skipped/
    relabelled accordingly."""
    _panel_box(surf, rect, label, r)
    if nh is None:
        r._t("no receiver", rect.centerx, rect.centery, font=r.f_sm, color=DIM, center=True)
        return
    rad, cx, cy = _card_geometry(rect, radius)
    course = getattr(nh, "course_deg", 0.0)
    is_loc = getattr(nh, "is_localizer", False)
    is_gps = source_kind == "GPS"
    color = CYAN if is_loc else GPS_GREEN
    # old-school round VOR/LOC head: rotating card + a centre CDI needle
    _vor_cdi_face(surf, cx, cy, rad, course, getattr(nh, "deflection", 0.0),
                  getattr(nh, "valid", False), color,
                  to_from=getattr(nh, "to_from", "OFF"), is_loc=is_loc, r=r)
    gs = getattr(nh, "gs_valid", False) or is_loc
    if gs:
        _gs_scale(surf, cx, cy, rad, getattr(nh, "gs_deflection", 0.0),
                  getattr(nh, "gs_valid", False), r)
    # info column, right of the card
    ix = int(cx + rad + 20)
    ident = getattr(nh, "ident", "") or "---"
    kind = source_kind if source_kind is not None else ("LOC" if is_loc else "VOR")
    r._t(f"{kind} {ident}", ix, cy - rad, font=r.f_sm, color=color)
    if not is_gps:
        _ident_dot(surf, ix + r.f_sm.size(f"{kind} {ident}")[0] + 8, cy - rad + 4, ident, t, color)
    num = f"{(getattr(nh,'obs_deg',0) if not is_loc else course):03.0f}"
    # a GPS head's number is the selected course (the pointer); the 530 shows the DTK itself
    r._t("CRS" if is_gps else ("OBS" if not is_loc else "CRS"), ix, cy - rad + 18,
        font=r.f_sm, color=DIM)
    r.lcd(num, ix + 34, cy - rad + 16, color=TEXT)
    tf = getattr(nh, "to_from", "OFF")
    if tf in ("TO", "FROM"):
        r._t(tf, ix, cy - rad + 36, font=r.f_sm, color=color)
    dme = getattr(nh, "dme_nm", None)
    if dme is not None:
        r._t(f"{'DIS' if is_gps else 'DME'} {dme:4.1f}", ix, cy - rad + 54,
            font=r.f_sm, color=TEXT)


def draw_hsi_head(surf, rect, nh, panel, label, r, radius=None, t=0.0):
    _panel_box(surf, rect, label, r)
    rad, cx, cy = _card_geometry(rect, radius)
    hdg = getattr(panel, "hsi_heading_deg", 0.0)
    course = getattr(nh, "course_deg", hdg) if nh else hdg
    _cdi_card(surf, cx, cy, rad, hdg, course,
              getattr(nh, "deflection", 0.0) if nh else 0.0,
              getattr(nh, "valid", False) if nh else False, AMBER,
              to_from=getattr(nh, "to_from", "OFF") if nh else "OFF", r=r)
    pygame.draw.polygon(surf, WHITE, [(cx, cy - rad - 1), (cx - 5, cy - rad - 10),
                                      (cx + 5, cy - rad - 10)])
    r.lcd(f"{hdg:03.0f}", cx, cy - rad * 0.34, color=WHITE, center=True)
    if nh and (getattr(nh, "gs_valid", False) or getattr(nh, "is_localizer", False)):
        _gs_scale(surf, cx, cy, rad, getattr(nh, "gs_deflection", 0.0),
                  getattr(nh, "gs_valid", False), r)
        ix = int(cx + rad + 20)
        ident = getattr(nh, "ident", "") or "---"
        label_txt = f"{'LOC' if getattr(nh, 'is_localizer', False) else 'VOR'} {ident}"
        r._t(label_txt, ix, cy - rad, font=r.f_sm, color=CYAN)
        _ident_dot(surf, ix + r.f_sm.size(label_txt)[0] + 8, cy - rad + 4, ident, t, CYAN)
        r._t(f"CRS {course:03.0f}", ix, cy - rad + 18, font=r.f_sm, color=DIM)


def draw_ap_panel(surf, rect, ap, magvar, r, ias_bug=None, *, show_info=True):
    """An S-TEC Fifty Five X style programmer: RDY lamp, HDG/NAV/APR/REV/ALT/VS
    button row, a VS window, GPSS / GS / TRIM annunciators - in its own box,
    matching the real unit's faceplate. The heading bug (lives on the DG/HSI,
    not the 55X), the altitude preselect (a separate Altitude Selector/Alerter
    accessory box on the real aircraft), and ``ias_bug`` (the trainer's own
    invented pseudo speed-manager set-point, not a real 55X feature at all)
    are avionics/instrument info, not part of the AP programmer - they get
    their own box to the right rather than crowding onto the 55X's face,
    unless ``show_info=False`` (the "stack" layout, which shows the actual
    values next to the HDG indicator instead - `Renderer._stack_hdg_actuals`
    - and edits the set points under the tab column - `_stack_setpoint_bugs`
    - and needs the 6-button mode row's full width; the row doesn't fit next
    to a second box at `stack`'s narrower column width)."""
    if show_info:
        info_w = min(190, rect.w * 0.24)
        ap_rect = pygame.Rect(rect.x, rect.y, rect.w - info_w - 8, rect.h)
        info_rect = pygame.Rect(ap_rect.right + 8, rect.y, info_w, rect.h)
        _panel_box(surf, info_rect, "", r)
    else:
        ap_rect = rect
        info_rect = None

    _panel_box(surf, ap_rect, "", r)
    if ap is None:
        r._t("no autopilot", ap_rect.centerx, ap_rect.centery, font=r.f_sm,
             color=DIM, center=True)
        return
    y = ap_rect.y + 8

    # RDY lamp (it flashes for 5 s after a disconnect, POH sec.3.7)
    ready = getattr(ap, "ready", False)
    if "RDY" in getattr(ap, "flashing", ()) and int(pygame.time.get_ticks() / 400) % 2 == 1:
        ready = False
    r._t("S-TEC 55X", ap_rect.x + 10, y, font=r.f_sm, color=DIM)
    rl = pygame.Rect(ap_rect.x + 10, y + 16, 42, 18)
    pygame.draw.rect(surf, GPS_GREEN if ready else (40, 44, 48), rl, border_radius=3)
    r._t("RDY", rl.centerx, rl.y + 2, font=r.f_sm,
         color=(0, 0, 0) if ready else DIM, center=True)

    lat = getattr(getattr(ap, "lateral", None), "value", "OFF")
    vert = getattr(getattr(ap, "vertical", None), "value", "OFF")
    alat = getattr(getattr(ap, "armed_lat", None), "value", None)
    avert = getattr(getattr(ap, "armed_vert", None), "value", None)

    flashing = getattr(ap, "flashing", frozenset())
    blink_off = int(pygame.time.get_ticks() / 400) % 2 == 1     # the POH's flashing annunciations

    def key(text, lit, armed):
        if text in flashing and blink_off:
            lit = armed = False
        c = GPS_GREEN if lit else (AMBER if armed else (36, 40, 46))
        box = pygame.Rect(bx[0], y + 4, 40, 26)
        pygame.draw.rect(surf, (24, 27, 32), box, border_radius=4)
        pygame.draw.rect(surf, c, box, width=2, border_radius=4)
        r._t(text, box.centerx, y + 9, font=r.f_sm,
             color=c if (lit or armed) else DIM, center=True)
        bx[0] = box.right + 5

    bx = [ap_rect.x + 92]
    key("HDG", lat == "HDG", alat == "HDG")
    key("NAV", lat == "NAV", alat == "NAV")
    key("APR", lat == "APR", alat == "APR")
    key("REV", lat == "REV", alat == "REV")
    key("ALT", vert == "ALT", False)
    key("VS", vert == "VS", False)      # (VS blinks via `flashing` above)

    # VS window, right after the mode-button row (not pinned to the box's
    # far right edge - the box is wide, and anchoring the readout that far
    # away just leaves a dead gap between the buttons and the numbers). The
    # digit slot is reserved wide enough for the DSEG7 LCD font's widest
    # case ("-1500", 5 glyphs incl. its full-width '-' slot) so the label
    # stays a fixed, small gap clear of the digits at any VS value.
    vlab = bx[0] + 14
    vcol = vlab + 16 + 8 + 95           # label width + gap + max digit width
    vy = y
    if vcol > ap_rect.right - 4:
        # doesn't fit beside the button row at this box's width (`stack`'s
        # show_info=False box, far narrower than steam's) - wrap onto its
        # own line below the buttons instead of running off the box's edge
        vlab = ap_rect.x + 92
        vcol = vlab + 16 + 8 + 95
        vy = y + 34
    r._t("VS", vlab, vy + 2, font=r.f_sm, color=DIM)
    r.lcd(f"{getattr(ap, 'vs_target', 0):.0f}", vcol, vy, big=True,
          color=GPS_GREEN if vert in ("VS", "GS") else DIM, right=True)

    ann = []
    if getattr(ap, "gpss", False) and lat in ("NAV", "APR"):
        ann.append(("GPSS", CYAN))
    gs_blink = "GS" in flashing
    if gs_blink and blink_off:
        pass                                             # the GS annunciation is flashing
    elif vert == "GS":
        ann.append(("GS", GPS_GREEN))
    elif avert == "GS" or gs_blink:
        ann.append(("GS ARM", AMBER))
    tr = getattr(ap, "trim", 0)
    if getattr(ap, "fail", False):
        ann.append(("FAIL", RED))
    if tr and not ("TRIM" in flashing and blink_off):
        ann.append((f"TRIM {'UP' if tr > 0 else 'DN'}", AMBER))
    ax = ap_rect.x + 92
    ann_y = y + 36 if vy == y else vy + 22   # below the wrapped VS row too, when wrapped
    for text, col in ann:
        rr = r._t(text, ax, ann_y, font=r.f_sm, color=col)
        ax = rr.right + 10

    # -- info box: not part of the 55X itself --------------------------
    if info_rect is not None:
        iy = info_rect.y + 6
        pitch = 17
        icol = info_rect.right - 10
        r._t("HDG BUG", info_rect.x + 10, iy, font=r.f_sm, color=DIM)
        r.lcd(f"{getattr(ap, 'heading_bug', 0):03.0f}", icol, iy - 1, color=CYAN, right=True)
        r._t("ALT SEL", info_rect.x + 10, iy + pitch, font=r.f_sm, color=DIM)
        r.lcd(f"{getattr(ap, 'alt_preselect', 0):.0f}", icol, iy + pitch - 1, color=TEXT, right=True)
        if ias_bug:
            r._t("IAS SET", info_rect.x + 10, iy + pitch * 2, font=r.f_sm, color=DIM)
            r.lcd(f"{ias_bug:.0f}", icol, iy + pitch * 2 - 1, color=CYAN, right=True)


def draw_radio_strip(surf, rect, radios, gns, r, *, selector_mode="", shift_mode="", t=0.0):
    _panel_box(surf, rect, "RADIOS", r)
    if radios is None:
        return
    y = rect.y + 22
    lh = 20

    def row(label, active, standby, extra="", extra_col=CYAN, ident=""):
        nonlocal y
        active_sel = label == selector_mode        # this mode is on the selector
        shifted = label == shift_mode              # ... and its shift is latched
        r._t(label, rect.x + 10, y, font=r.f_sm,
             color=AMBER if shifted else (WHITE if active_sel else DIM))
        r.lcd(f"{active:07.3f}", rect.x + 66, y - 1, color=GPS_GREEN)
        # yellow standby == this radio is the one the mode selector is pointing at
        r.lcd(f"{standby:07.3f}", rect.x + 168, y - 1, color=AMBER if active_sel else DIM)
        if extra:
            r._t(extra, rect.x + 272, y, font=r.f_sm, color=extra_col)
            if ident:
                _ident_dot(surf, rect.x + 272 + r.f_sm.size(extra)[0] + 8, y + 4, ident, t,
                          extra_col)
        y += lh

    def nav_extra(nv, mode):
        crs = shift_mode == mode
        lbl = "CRS" if crs else "OBS"
        return (f"{nv.station_ident or '---':4}  {lbl} {nv.obs_deg:03.0f}",
                (AMBER if crs else CYAN), nv.station_ident)

    row("COM1", radios.com1.active_mhz, radios.com1.standby_mhz,
        "HDG BUG" if shift_mode == "COM1" else "", AMBER)
    row("COM2", radios.com2.active_mhz, radios.com2.standby_mhz,
        "BARO" if shift_mode == "COM2" else "", AMBER)
    row("NAV1", radios.nav1.active_mhz, radios.nav1.standby_mhz, *nav_extra(radios.nav1, "NAV1"))
    row("NAV2", radios.nav2.active_mhz, radios.nav2.standby_mhz, *nav_extra(radios.nav2, "NAV2"))
    xpdr_sel = selector_mode == "XPDR"
    r._t("XPDR", rect.x + 10, y, font=r.f_sm,
         color=AMBER if shift_mode == "XPDR" else (WHITE if xpdr_sel else DIM))
    sq_x = rect.x + 66
    r.lcd(radios.xpdr.squawk, sq_x, y - 1, color=AMBER)
    if xpdr_sel:                               # digit-under-edit hint, only in XPDR mode
        cur = max(0, min(3, getattr(radios.xpdr, "cursor", 3)))
        dw = r.f_lcd.size("0")[0]
        ux0 = sq_x + cur * dw
        pygame.draw.line(surf, AMBER, (ux0 + 1, y + 15), (ux0 + dw - 2, y + 15), 2)
    r._t(radios.xpdr.mode + ("  ID" if getattr(radios.xpdr, "identing", False) else ""),
         rect.x + 150, y, font=r.f_md, color=AMBER)
    src = getattr(getattr(gns, "cdi_source", None), "value", None) or getattr(gns, "cdi_source", "GPS")
    r._t(f"CDI SRC: {src}", rect.right - 12, rect.y + 22, font=r.f_sm, color=DIM, right=True)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
