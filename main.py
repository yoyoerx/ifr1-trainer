"""main.py - wire the trainer together and run the loop.

    python main.py --plan "KBOS PVD KJFK" --wind 300/25
    python main.py --no-device            # keyboard-fly (no IFR-1 needed)
    python main.py --layout steam         # start on the steam-gauge panel
    python main.py --xplane-feed          # take live ownship position from a running X-Plane
    python main.py --winds-aloft "3000:280/20/-05 9000:300/35/-15 18000:310/55/-30"
    python main.py --wx-region BOS --wx-station BOS    # fly a fetched/cached forecast
                                           # (fetch first: python -m datasrc.wx winds-aloft BOS)
    python main.py --time-warp 5          # start at 5x sim speed (keys 1-4 change it live)
    python main.py --gdl90 --gdl90-discover   # broadcast GDL90, then auto-switch to a
                                           # discovered ForeFlight tablet's unicast address
    python main.py --dual                 # a 530 on FMS1 + a 430 on FMS2, stacked on screen
    python main.py --unit 430 --dual      # a 430 on FMS1 + a 530 on FMS2

Loop (~30 Hz): drain IFR-1 events -> route by mode (FMS -> GPS, COM/NAV -> radio
tuning, XPDR -> squawk, AP row -> autopilot) -> guidance (autopilot | GPS follow
| manual heading) -> sim.step -> instruments -> render.

IFR-1 mode-selector routing (`route_event`):
  FMS1/FMS2  outer=page group (no wrap), inner=page, KNOB=CRSR, DCT/ENT/CLR/MNU;
             DCT opens the Select Direct-To page (knob types the ident); AP row
             becomes bezel keys: AP=CDI HDG=OBS NAV=MSG APR=FPL ALT=VNAV VS=PROC
             With --dual, FMS1 and FMS2 drive two independent GNS units (their
             own flight plan/cursor/nav state - both start on the same plan,
             then diverge); without it, both positions fly the one unit.
             (MSG's on-screen box/annunciator only ever reflects FMS1's queue;
             FMS2's MSG key still acks its own queue, it just has no box yet.)
  COM1/COM2/NAV1/NAV2/XPDR  the KNOB press toggles a latched "shift" (a UI hint
             shows what it does; it clears when the mode selector moves):
    COM1  normal knob=MHz/kHz + SWAP flip;  shift: knob=heading bug
    COM2  normal knob=MHz/kHz + SWAP flip;  shift: knob=altimeter setting
    NAV1/2 normal knob=MHz/kHz + SWAP flip; shift: knob turns the OBS/CRS card
          (NAV1's CRS knob is also the "external OBS selector" the Pilot's
           Guide refers to - with CDI source GPS and OBS mode on, it sets
           the GPS's own OBS course too, not just NAV1's VOR/ILS course)
    XPDR  normal inner=digit value, outer=digit select (underlined);
          SWAP=IDENT pulse;  shift: knob=transponder mode
  AP         outer=ALT select (100 ft), inner=VS (100 fpm); AP-row buttons=modes

Keyboard (works alongside or instead of the IFR-1; "Shift+x" = hold Shift):

  Flight controls
    arrows        manual heading -/+ 5      target altitude -/+ 500 ft
    , .           target IAS -/+ 5 kt
  GNS 530 pages
    PgUp PgDn     page within the group (small knob)
    Shift+PgUp/PgDn  page GROUP: NAV -> WPT -> AUX -> NRST (large knob) - the
                  only keyboard route to WPT/AUX (incl. Weather, Charts)/NRST
    TAB           CRSR (cursor on/off)     D  Direct-To entry page
    R             PROC (approach/SID/STAR selector)
    [ ]           map range out / in       Home  Default NAV page
    S             suspend            V  CDI source (GPS/VLOC)
    B             OBS on/off         - =  OBS course -/+ 1 (Shift x10)
    M             messages
    U             (--dual only) swap which unit the GNS-page keys above
                  drive - FMS1 or FMS2; an annunciator shows "KBD FMS2"
  Radios / autopilot
    o O           NAV1 OBS -/+              k p   NAV2 OBS -/+
                  (also sets the GPS's OBS course when CDI source=GPS and
                   OBS mode is on - see the NAV1 shift note above)
    H             NAV1 CDI<->HSI            F11   COM1 emergency 121.5
    A             AP master
    F1 F2 F3 F4 F5 F6   HDG NAV APR REV ALT VS
    g             GPSS toggle                9 0   AP VS knob -/+
  View / session
    L             layout (gps -> steam -> stack -> gps, or -> dual too with --dual)
    1 2 3 4       time warp 1x/5x/10x/20x (see TIME_WARP_LEVELS)
    ESC           quit
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

from navmath import Point, destination, great_circle_nm, initial_bearing

import navdata
import config as config_mod
import gns530 as gns530_mod
import gns430 as gns430_mod
import instruments as instr
import sim_model as simmod
import windsaloft
from autopilot import Autopilot
from radios import RadioStack


TIME_WARP_LEVELS = (1, 5, 10, 20)   # keyboard 1/2/3/4; see _on_key and run()'s sub-tick loop


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
class Config:
    def __init__(self, c: dict):
        self.plan = str(c["plan"]).split() if c["plan"] else []
        self.approach = str(c.get("approach", "")).split()  # [ICAO, IDENT, TRANSITION?]
        self.no_device = bool(c["no_device"])
        self.xplane_feed = bool(c["xplane_feed"])
        self.xplane_host = str(c["xplane_host"])
        self.gdl90_discover = bool(c.get("gdl90_discover", False))
        self.gdl90 = bool(c["gdl90"]) or self.gdl90_discover  # discovery implies sending
        self.gdl90_host = str(c["gdl90_host"])
        self.gdl90_port = int(c["gdl90_port"])
        self.wind_from, self.wind_kt = _parse_wind(c["wind"])
        self.winds_aloft = _parse_winds_aloft(c.get("winds_aloft", ""))
        self.wx_region = str(c.get("wx_region", ""))
        self.wx_station = str(c.get("wx_station", ""))
        self.wx_auto_refresh = bool(c.get("wx_auto_refresh", False))
        self.wx_metar_minutes = float(c.get("wx_metar_minutes", 20.0))
        self.wx_taf_minutes = float(c.get("wx_taf_minutes", 60.0))
        self.wx_winds_aloft_minutes = float(c.get("wx_winds_aloft_minutes", 60.0))
        self.tas = float(c["tas"])
        self.altitude = float(c["altitude"])
        self.gps_follow = not bool(c["manual"])
        self.layout = str(c["layout"])
        self.unit = str(c["unit"])
        self.dual = bool(c.get("dual", False))
        self.headless = bool(c["headless"])
        self.time_warp = int(c.get("time_warp", 1)) if int(c.get("time_warp", 1)) in TIME_WARP_LEVELS else 1


def _parse_wind(s):
    if not s:
        return 0.0, 0.0
    try:
        a, b = s.replace("@", "/").split("/")
        return float(a) % 360.0, float(b)
    except ValueError:
        return 0.0, 0.0


def _parse_winds_aloft(s):
    """--winds-aloft "ALT:DIR/SPD[/TEMPC] ..." -> a WindsAloftProfile, or None.
    A malformed value is reported and ignored (falls back to --wind) rather
    than aborting the run."""
    if not s:
        return None
    try:
        return windsaloft.parse_cli(s)
    except ValueError as exc:
        print(f"note: ignoring --winds-aloft ({exc})")
        return None


def parse_args(argv=None) -> Config:
    S = argparse.SUPPRESS
    p = argparse.ArgumentParser(description="Octavi IFR-1 GNS 530 / 430 trainer")
    p.add_argument("--config", default=None, help="path to octavi.toml / octavi.json")
    p.add_argument("--plan", default=S, help='fix idents, e.g. "KBOS BOS PVD"')
    p.add_argument("--approach", default=S,
                   help='load a CIFP approach: "ICAO IDENT [TRANSITION]", e.g. "KASE RNV-F"')
    p.add_argument("--wind", default=S, help="DIR/SPD, e.g. 300/25")
    p.add_argument("--winds-aloft", dest="winds_aloft", default=S,
                   help='multi-altitude wind (+ optional temp) profile, overrides --wind: '
                        '"ALT:DIR/SPD[/TEMPC] ..." e.g. '
                        '"3000:280/20/-05 9000:300/35/-15 18000:310/55/-30"')
    p.add_argument("--wx-region", dest="wx_region", default=S,
                   help="NWS FD region to read a cached winds-aloft fetch from "
                        "(python -m datasrc.wx winds-aloft REGION), e.g. BOS")
    p.add_argument("--wx-station", dest="wx_station", default=S,
                   help="station within --wx-region whose profile to fly with")
    p.add_argument("--wx-auto-refresh", dest="wx_auto_refresh", action="store_true", default=S,
                   help="keep re-fetching METAR/TAF (for the flight plan's airports) and, "
                        "with --wx-region, winds-aloft in the background while running "
                        "(opt-in: this is the one place the trainer talks to the network "
                        "on its own, on a timer - see --wx-*-minutes)")
    p.add_argument("--wx-metar-minutes", dest="wx_metar_minutes", type=float, default=S,
                   help="METAR refresh interval in minutes (default: 20)")
    p.add_argument("--wx-taf-minutes", dest="wx_taf_minutes", type=float, default=S,
                   help="TAF refresh interval in minutes (default: 60)")
    p.add_argument("--wx-winds-aloft-minutes", dest="wx_winds_aloft_minutes", type=float,
                   default=S, help="winds-aloft refresh interval in minutes (default: 60)")
    p.add_argument("--tas", default=S)
    p.add_argument("--altitude", default=S)
    p.add_argument("--layout", default=S, choices=("gps", "steam", "stack", "dual"))
    p.add_argument("--unit", default=S, choices=("530", "430"),
                   help="GPS unit to model (GNS 530 or GNS 430) - the FMS1 unit in --dual")
    p.add_argument("--dual", action="store_true", default=S,
                   help="run a second GNS unit (the other of 530/430) as FMS2, e.g. a real "
                        "530/430 stack: --unit 530 --dual puts a 530 on FMS1, a 430 on FMS2. "
                        "Implies --layout dual unless --layout is also given.")
    p.add_argument("--no-device", dest="no_device", action="store_true", default=S)
    p.add_argument("--manual", action="store_true", default=S,
                   help="start with GPS-follow off")
    p.add_argument("--xplane-feed", dest="xplane_feed", action="store_true", default=S,
                   help="take live ownship position from X-Plane over UDP")
    p.add_argument("--xplane-host", dest="xplane_host", default=S,
                   help="X-Plane host for the position feed (default 127.0.0.1)")
    p.add_argument("--gdl90", action="store_true", default=S,
                   help="broadcast ownship as GDL90 for a tablet EFB")
    p.add_argument("--gdl90-host", dest="gdl90_host", default=S,
                   help="GDL90 destination (default 255.255.255.255 broadcast)")
    p.add_argument("--gdl90-port", dest="gdl90_port", type=int, default=S)
    p.add_argument("--gdl90-discover", dest="gdl90_discover", action="store_true", default=S,
                   help="listen for ForeFlight's own UDP broadcast (port 63093) and switch "
                        "from --gdl90-host to a direct unicast once a tablet answers; "
                        "implies --gdl90")
    p.add_argument("--headless", action="store_true", default=S,
                   help="run a few frames then exit")
    p.add_argument("--time-warp", dest="time_warp", type=int, choices=TIME_WARP_LEVELS,
                   default=S, help="starting simulation speed multiplier "
                        "(1/5/10/20, keys 1-4 change it while running; default 1)")
    args = p.parse_args(argv)
    file_cfg = config_mod.load_config(getattr(args, "config", None))
    cli = {k: v for k, v in vars(args).items() if k != "config"}
    merged = config_mod.merge(cli, file_cfg)
    if merged.get("dual") and "layout" not in cli and "layout" not in file_cfg:
        merged["layout"] = "dual"          # --dual's own default, unless --layout overrides it
    return Config(merged)


# --------------------------------------------------------------------------- #
# world
# --------------------------------------------------------------------------- #
def _initial_position(db, gns):
    wps = gns.fpl.waypoints
    if len(wps) >= 2:
        brg = initial_bearing(wps[0].pos, wps[1].pos)
        # 2.0 nm out along the departure bearing - but never past the first
        # waypoint itself. A short first leg (e.g. an airport with a VOR of
        # the same name sitting under a mile away, like KBOS -> BOS) would
        # otherwise place ownship beyond the leg's own endpoint, so the GPS
        # sequences past it before the sim has even taken its first step.
        leg_nm = great_circle_nm(wps[0].pos, wps[1].pos)
        off_nm = min(2.0, leg_nm * 0.5)
        return destination(wps[0].pos, brg, off_nm), brg
    if wps:
        return wps[0].pos, 0.0
    apt = db.airport("KBOS") or next(iter(db.airports.values()), None)
    return (apt.pos if apt else Point(42.36, -71.01)), 0.0


def _load_cached_winds_aloft(region: str, station: str):
    """--wx-region/--wx-station: read a profile out of a winds-aloft fetch
    already cached by `python -m datasrc.wx winds-aloft REGION` (data is never
    pulled from the network inside the trainer loop - see ARCHITECTURE.md's
    "Data fetch is a separate CLI" principle). ``None`` (falls back to
    --wind) if nothing usable is cached, with a note explaining why."""
    try:
        from datasrc.wx import default_data_root, load_winds_aloft_profile
        profile = load_winds_aloft_profile(default_data_root(), region, station)
    except Exception as exc:                  # noqa: BLE001 - best-effort convenience
        print(f"note: could not read cached winds aloft for {station} ({exc})")
        return None
    if profile is None:
        print(f"note: no cached winds-aloft data for {station} in region {region} "
              f"- run: python -m datasrc.wx winds-aloft {region}")
        return None
    print(f"winds aloft: {station} from region {region} cache "
          f"({len(profile.levels)} level{'s' if len(profile.levels) != 1 else ''})")
    return profile


def _local_magvar(db, pos):
    """Ownship magnetic variation, east-positive. WMM model first; if the
    coefficient file is missing, fall back to the nearest navaid's declination."""
    try:
        import wmm
        d = wmm.declination(pos.lat, pos.lon)
        if d is not None:
            return d
    except Exception:                         # noqa: BLE001 - WMM is best-effort
        pass
    navs = db.nearest_navaids(pos, 1, ndb=False)
    return getattr(navs[0], "magvar_deg", 0.0) if navs else 0.0


class Frame:
    """One tick's output - everything render needs."""

    __slots__ = ("own", "nav", "panel", "nav1", "nav2", "sixpack", "panel2")

    def __init__(self, own, nav, panel, nav1, nav2, sixpack, panel2=None):
        self.own, self.nav, self.panel = own, nav, panel
        self.nav1, self.nav2, self.sixpack = nav1, nav2, sixpack
        self.panel2 = panel2          # FMS2's own CDI panel (--dual), else None


class World:
    def __init__(self, cfg: Config):
        self.db = navdata.load()
        from datetime import date
        unit_cls = gns430_mod.Gns430 if getattr(cfg, "unit", "530") == "430" else gns530_mod.Gns530
        self.gns = unit_cls(self.db, today=date.today())
        if cfg.plan:
            missing = self.gns.load_flight_plan(cfg.plan)
            if missing:
                print(f"note: could not resolve {missing}")
        # --dual: a second, independent GNS unit (the other of 530/430) driven
        # by the IFR-1's FMS2 mode selector position - a real dual stack, e.g.
        # a 530 on FMS1 and a 430 on FMS2. It starts on the same flight plan
        # as FMS1 (as a freshly-crossfilled pair would in the real aircraft);
        # from there the two fly and page independently. It gets its own
        # `.update()` call each tick (see `tick()`) so its sequencing/CDI stay
        # live even though only FMS1 drives the autopilot/instrument panel.
        self.gns2 = None
        if getattr(cfg, "dual", False):
            unit2_cls = gns530_mod.Gns530 if unit_cls is gns430_mod.Gns430 else gns430_mod.Gns430
            self.gns2 = unit2_cls(self.db, today=date.today())
            if cfg.plan:
                self.gns2.load_flight_plan(cfg.plan)
        self._appr_tuned = False
        self._vloc_reminded = False
        self._last_approach_freq = None
        if getattr(cfg, "approach", None):
            apt, ident, *rest = cfg.approach + ["", ""]
            n = self.gns.load_procedure(apt.upper(), ident.upper(),
                                        rest[0].upper() or None)
            print(f"approach {apt} {ident}: {n} legs loaded"
                  if n else f"approach {apt} {ident}: not found")
        start, hdg = _initial_position(self.db, self.gns)
        winds_aloft = cfg.winds_aloft
        # only re-apply a fresher winds-aloft profile automatically (see
        # wx_auto below) when it came from the region/station cache in the
        # first place - an explicit --winds-aloft profile is the pilot's
        # own numbers and is never silently overwritten.
        self._wx_from_cache = winds_aloft is None and bool(cfg.wx_region) and bool(cfg.wx_station)
        self._wx_region, self._wx_station = cfg.wx_region, cfg.wx_station
        if self._wx_from_cache:
            winds_aloft = _load_cached_winds_aloft(cfg.wx_region, cfg.wx_station)
        self.sim = simmod.SimModel(pos=start, heading_deg=hdg, altitude_ft=cfg.altitude,
                                   tas_kt=cfg.tas, wind_from_deg=cfg.wind_from,
                                   wind_kt=cfg.wind_kt, winds_aloft=winds_aloft)
        self.sim.command(altitude=cfg.altitude, tas=cfg.tas)
        # pseudo speed manager: an IAS set-point the pilot trims with the AP-mode
        # shifted inner knob (there is no throttle model). Not commanded until it
        # is first touched, so --tas still means "hold this TAS".
        self.ias_target = round(simmod.ias_from_tas(cfg.tas, cfg.altitude))
        self._ias_managed = False
        self.radios = RadioStack()
        if self.gns.approach_freq:            # GNS loads the ILS/VOR freq to standby
            self.radios.nav1.standby_mhz = self.gns.approach_freq
            print(f"VLOC standby -> {self.gns.approach_freq:07.3f} "
                  f"({self.gns.approach_ref or 'approach'})")
        self.ap = Autopilot()
        self.ap.alt_preselect = cfg.altitude
        self.ap.heading_bug = round(hdg)
        self.magvar = _local_magvar(self.db, start)
        self.gps_follow = cfg.gps_follow
        self.manual_heading = self.sim.heading
        self.show_msg = False                 # Message page visible (MSG key / M)
        self.baro_inhg = 29.92                # altimeter setting (COM2 shift knob)
        import scoring
        self.score = scoring.ScoreTracker()   # grades the flying, printed on exit
        self.shift_latched = False            # knob-latched modifier for the current mode
        self._last_mode = None                # mode selector position (latch resets on change)
        self.ident_timer = 0.0               # seconds left on the transponder IDENT pulse
        self.t = 0.0                          # elapsed session seconds (VLOC ident blink)

        self.feed = None
        self.feed_live = False
        if cfg.xplane_feed:
            try:
                import xplane_feed
                self.feed = xplane_feed.XPlaneFeed(host=cfg.xplane_host).open()
                print(f"X-Plane feed: listening for {cfg.xplane_host}:49000")
            except Exception as exc:               # noqa: BLE001 - feed is optional
                print(f"X-Plane feed not started ({exc}); using the built-in model.")

        self.wx_updater = None
        if cfg.wx_auto_refresh:
            try:
                import wx_auto
                region = cfg.wx_region or None
                self.wx_updater = wx_auto.WxAutoUpdater(
                    stations=self.gns.wx_station_idents,
                    region=(lambda: region) if region else None,
                    on_winds_aloft=self._reload_cached_winds_aloft if self._wx_from_cache else None,
                    metar_s=cfg.wx_metar_minutes * 60.0,
                    taf_s=cfg.wx_taf_minutes * 60.0,
                    winds_s=cfg.wx_winds_aloft_minutes * 60.0,
                ).start()
                print(f"weather auto-refresh: METAR/{cfg.wx_metar_minutes:.0f}min "
                      f"TAF/{cfg.wx_taf_minutes:.0f}min" +
                      (f" winds-aloft/{cfg.wx_winds_aloft_minutes:.0f}min ({region})"
                       if region else ""))
            except Exception as exc:               # noqa: BLE001 - auto-refresh is optional
                print(f"weather auto-refresh not started ({exc}).")

    def _reload_cached_winds_aloft(self) -> None:
        """`wx_auto`'s on-winds-aloft callback: after a background refetch,
        pull the newer profile out of the cache and hand it to the sim -
        only wired up when the active profile itself came from the
        --wx-region/--wx-station cache (never overrides an explicit
        --winds-aloft). Runs on the updater's background thread; assigning
        `SimModel.winds_aloft` is a single attribute set, safe enough under
        the GIL for a hobby trainer with no other writer of that field."""
        profile = _load_cached_winds_aloft(self._wx_region, self._wx_station)
        if profile is not None:
            self.sim.set_winds_aloft(profile)

    def _sync_sim_from(self, st) -> None:
        """Keep the fallback model parked on the live position so a feed dropout
        does not teleport the aircraft."""
        self.sim.pos = st.pos
        self.sim.heading = st.heading_deg
        self.sim.altitude = st.altitude_ft
        self.sim.tas = max(st.tas_kt, 1.0)
        self.manual_heading = st.heading_deg
        # track the live speed so the set-point does not jump on a feed dropout
        self.ias_target = round(simmod.ias_from_tas(self.sim.tas, self.sim.altitude))
        if self._ias_managed:                  # keep the sim's IAS hold in step
            self.sim.command(ias=self.ias_target)

    def set_ias_target(self, ias_kt: float) -> None:
        """Trim the pseudo speed manager (AP-mode shifted inner knob, or the
        ,/. keys). Engages an IAS hold on the sim so a climb reads rising TAS/GS."""
        self.ias_target = _clamp(round(ias_kt), _IAS_MIN, _IAS_MAX)
        self._ias_managed = True
        self.sim.command(ias=self.ias_target)

    def tick(self, dt: float) -> Frame:
        self.t += dt
        if self.ident_timer > 0.0:
            self.ident_timer = max(0.0, self.ident_timer - dt)
            self.radios.xpdr.identing = self.ident_timer > 0.0

        ext = None
        if self.feed is not None:
            self.feed.poll()
            ext = self.feed.state
        self.feed_live = ext is not None

        if ext is not None:
            st = ext
            self._sync_sim_from(st)
            mv = self.feed.magvar_deg
            if mv is not None:
                self.magvar = mv
            self.radios.resolve(self.db, st.pos, st.altitude_ft)
            nav = self.gns.update(st.pos, st.track_deg, st.gs_kt, dt)
            self._auto_vloc(nav)
            if self.gns2 is not None:          # FMS2: stays live, doesn't drive AP/instruments
                self.gns2.update(st.pos, st.track_deg, st.gs_kt, dt)
            n1 = instr.nav_head(self.radios.nav1, st.pos, st.altitude_ft, st.gs_kt, self.magvar)
            n2 = instr.nav_head(self.radios.nav2, st.pos, st.altitude_ft, st.gs_kt, self.magvar)
            own_i = instr.Ownship(st.pos, st.track_deg, st.heading_deg, st.gs_kt,
                                  st.altitude_ft, self.magvar)
            panel = self._panel(own_i, nav)
            sp = instr.six_pack(st, self.magvar)
            self.score.sample(nav, st, panel, nav_head=n1,
                              alt_target=self.ap.alt_hold_ft if self.ap.engaged else None)
            return Frame(st, nav, panel, n1, n2, sp, self._panel2(own_i))

        st = self.sim.state
        self.radios.resolve(self.db, st.pos, st.altitude_ft)
        nav = self.gns.update(st.pos, st.track_deg, st.gs_kt, dt)
        self._auto_vloc(nav)
        if self.gns2 is not None:              # FMS2: stays live, doesn't drive AP/instruments
            self.gns2.update(st.pos, st.track_deg, st.gs_kt, dt)
        n1 = instr.nav_head(self.radios.nav1, st.pos, st.altitude_ft, st.gs_kt, self.magvar)
        n2 = instr.nav_head(self.radios.nav2, st.pos, st.altitude_ft, st.gs_kt, self.magvar)

        if self.ap.engaged:
            cmd = self.ap.update(
                nav, st, self.magvar, dt=dt,
                vloc_course_deg=self.radios.nav1.course_deg,
                vloc_deflection=n1.deflection, vloc_valid=n1.valid,
                gs_deflection=n1.gs_deflection, gs_valid=n1.gs_valid,
            )
            self._apply(cmd)
        elif self.gps_follow:
            self.sim.follow_leg(nav)
        else:
            self.sim.command(heading=self.manual_heading)

        self.sim.step(dt)
        st = self.sim.state
        own_i = instr.Ownship(st.pos, st.track_deg, st.heading_deg, st.gs_kt,
                              st.altitude_ft, self.magvar)
        panel = self._panel(own_i, nav)
        sp = instr.six_pack(st, self.magvar)
        self.score.sample(nav, st, panel, nav_head=n1,
                          alt_target=self.ap.alt_hold_ft if self.ap.engaged else None)
        return Frame(st, nav, panel, n1, n2, sp, self._panel2(own_i))

    def _auto_vloc(self, nav) -> None:
        """Approach VLOC handling. On the real 530W, activating the staged VLOC
        frequency (the flip-flop / SWAP key) is ALWAYS a pilot action - so that
        only happens here when nobody is at the controls to press it (GPS-follow
        with the autopilot off). Otherwise the pilot is reminded once, near the
        FAF, if the box is still sitting on standby. The CDI GPS->VLOC auto-switch
        near the FAF once the correct frequency is actually being received is
        genuine WAAS-era 530W behaviour and always applies."""
        g = self.gns
        if not g._approach_active or not g.approach_freq:
            return
        if g.approach_freq != self._last_approach_freq:   # a new approach staged
            self._last_approach_freq = g.approach_freq
            self._appr_tuned = False
            self._vloc_reminded = False
        wps = g.fpl.waypoints
        faf_i = next((i for i, w in enumerate(wps) if w.is_faf), None)
        if faf_i is None:
            return
        to_faf_nm = None
        if g.fpl.active <= faf_i and nav is not None and getattr(nav, "valid", False):
            # rough distance-to-FAF along the plan
            to_faf_nm = getattr(nav, "dist_nm", None)
            for j in range(g.fpl.active, faf_i):
                to_faf_nm = (to_faf_nm or 0.0) + great_circle_nm(wps[j].pos, wps[j + 1].pos)
        past_faf = g.fpl.active > faf_i
        near = past_faf or (to_faf_nm is not None and to_faf_nm < 15.0)

        n1 = self.radios.nav1
        on_freq = (n1.tuned and abs(n1.active_mhz - g.approach_freq) < 0.005)
        staged = abs(n1.standby_mhz - g.approach_freq) < 0.005
        unattended = self.gps_follow and not self.ap.engaged   # nobody to press SWAP
        if near and not on_freq and staged:
            if unattended and not self._appr_tuned:
                n1.swap()                                # activate the staged freq
                self._appr_tuned = True
                on_freq = (n1.tuned and abs(n1.active_mhz - g.approach_freq) < 0.005)
                print(f"VLOC active  -> {g.approach_freq:07.3f}")
            elif not unattended and not self._vloc_reminded:
                self._vloc_reminded = True
                g.messages.append(f"TUNE VLOC {g.approach_freq:07.3f} ({g.approach_ref})")

        close = past_faf or (to_faf_nm is not None and to_faf_nm < 2.0)
        if (close and on_freq and not g._auto_vloc_done and g.cdi_source == "GPS"):
            g.toggle_cdi_source()
            g._auto_vloc_done = True
            g.messages.append(f"CDI -> VLOC ({n1.station_ident or g.approach_ref})")

    def _panel(self, own_i, nav):
        """Build the instrument panel. When the CDI source is VLOC the NAV
        receivers feed compute_panel so the CDI/HSI shows the NAV1 course; in
        GPS mode they are left out and GPS guidance drives the needle."""
        vloc = getattr(nav, "cdi_source", "GPS") == "VLOC"
        return instr.compute_panel(
            own_i, nav, phase=_phase_for(nav),
            nav1=_tuned_nav(self.radios.nav1) if vloc else None,
            nav2=_tuned_nav(self.radios.nav2) if vloc else None,
        )

    def _panel2(self, own_i):
        """FMS2's instrument panel (``--dual`` only): FMS2 drives NAV2 the way
        FMS1 drives NAV1 - its own CDI source picks GPS guidance or the NAV2
        receiver, so the NAV2 head and FMS2's CDI strip follow FMS2's CDI key
        rather than FMS1's."""
        g2 = self.gns2
        if g2 is None:
            return None
        nav2 = g2.nav
        vloc = getattr(nav2, "cdi_source", "GPS") == "VLOC"
        return instr.compute_panel(
            own_i, nav2, phase=_phase_for(nav2),
            nav1=_tuned_nav(self.radios.nav2) if vloc else None,
        )

    def _apply(self, cmd) -> None:
        kw = {}
        if cmd.heading is not None:
            kw["heading"] = cmd.heading
        if cmd.altitude is not None:
            kw["altitude"] = cmd.altitude
        if cmd.vs is not None:
            kw["vs"] = cmd.vs
        elif cmd.clear_vs:
            kw["clear_vs"] = True
        if kw:
            self.sim.command(**kw)


def _tuned_nav(rx):
    """Adapt a radios.NavReceiver to the instruments.TunedNav that compute_panel
    wants, so the CDI key's GPS<->VLOC toggle actually shows the NAV1 course."""
    if rx is None or not getattr(rx, "tuned", False):
        return None
    return instr.TunedNav(
        ident=getattr(rx, "station_ident", ""),
        pos=rx.station_pos,
        station_magvar_deg=getattr(rx, "station_magvar", 0.0),
        course_deg=rx.course_deg,
        is_localizer=getattr(rx, "is_localizer", False),
        has_dme=getattr(rx, "has_dme", False),
    )


def _phase_for(nav) -> "instr.Phase":
    dist = getattr(nav, "dist_nm", None)
    if dist is not None and dist < 3.0:
        return instr.Phase.APPROACH
    if dist is not None and dist < 30.0:
        return instr.Phase.TERMINAL
    return instr.Phase.ENROUTE


# -- backward-compatible one-shot tick (used by older tests) ---------------
def build_world(cfg: Config):
    w = World(cfg)
    return w.db, w.gns, w.sim, w.sim.state.pos


def step_once(gns, sim, dt: float, *, autopilot: bool, magvar: float):
    st = sim.state
    nav = gns.update(st.pos, st.track_deg, st.gs_kt)
    if autopilot:
        sim.follow_leg(nav)
    sim.step(dt)
    st = sim.state
    own_i = instr.Ownship(st.pos, st.track_deg, st.heading_deg, st.gs_kt, st.altitude_ft, magvar)
    panel = instr.compute_panel(own_i, nav, phase=_phase_for(nav))
    return nav, panel, st


# --------------------------------------------------------------------------- #
# IFR-1 event routing
# --------------------------------------------------------------------------- #
_AP_BTN = {
    "AP": "press_ap", "HDG": "press_hdg", "NAV": "press_nav",
    "APR": "press_apr", "ALT": "press_alt", "VS": "press_vs",
}
# In FMS1 / FMS2 the AP-row keys become GNS 530 bezel keys.
_FMS_BEZEL = {"AP": "CDI", "HDG": "OBS", "NAV": "MSG",
              "APR": "FPL", "ALT": "VNAV", "VS": "PROC"}
_BARO_MIN, _BARO_MAX = 28.00, 31.00
# Modes where a latched "shift" (toggled by the knob press) changes what the
# knobs do, and the label of that shifted function.
_SHIFT_FN = {"COM1": "HDG BUG", "COM2": "BARO", "NAV1": "CRS1",
             "NAV2": "CRS2", "XPDR": "XPDR MODE", "AP": "IAS"}
_IAS_MIN, _IAS_MAX = 40.0, 350.0
_IAS_STEP_KT = 5.0


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _next_layout(current: str, *, dual: bool) -> str:
    """`L` key: cycle gps -> steam -> stack -> gps, or gps -> steam -> stack
    -> dual -> gps when a second FMS unit (`--dual`) is present. An
    unrecognised starting value (e.g. `--layout dual` without `--dual`, so
    `dual` isn't offered here) resets to the front of the cycle rather than
    raising."""
    cycle = ("gps", "steam", "stack", "dual") if dual else ("gps", "steam", "stack")
    if current not in cycle:
        return cycle[0]
    return cycle[(cycle.index(current) + 1) % len(cycle)]


def _fms_bezel(w: World, key: str, gns=None) -> None:
    g = gns if gns is not None else w.gns
    if key == "CDI":
        g.toggle_cdi_source()
    elif key == "OBS":
        # on the real 530 this is one physical key (OBS/SUSP): while suspended
        # (at a MAP/manual leg, or mid-hold) it releases the suspend; only
        # otherwise does it toggle OBS. `toggle_obs`'s set_obs(on=True) path
        # itself clears `suspended`, so without this branch the same press
        # both un-suspends AND drops the GPS into OBS mode.
        if g.suspended or g._hold_state is not None:
            g.toggle_suspend()
        else:
            g.toggle_obs()
    elif key == "MSG":
        # in --dual the message box/annunciator only ever reflects FMS1's
        # queue (see run()'s Scene build) - MSG on FMS2 still acks its own
        # queue so it stops re-arriving, it just has no on-screen box of its
        # own yet.
        if g is w.gns:
            w.show_msg = not w.show_msg
            if not w.show_msg:
                g.ack_messages()
        else:
            g.ack_messages()
    elif key == "FPL":
        g.cursor.go_to_flight_plan()
    elif key == "PROC":
        g.begin_proc_select()
    elif key == "VNAV":
        g.cursor.go_to_vnav()


def route_event(ev, w: World) -> None:
    from ifr1 import Mode

    pressed = set(getattr(ev, "pressed", ()))
    outer = getattr(ev, "outer", 0)
    inner = getattr(ev, "inner", 0)
    m = getattr(ev, "mode", None)
    long_press = set(getattr(ev, "long_press", ()))

    # FMS2 drives the second unit in --dual (`w.gns2`); everywhere else - no
    # --dual, or FMS1 - it's `w.gns`, so a single-unit setup behaves exactly
    # as before (FMS1 and FMS2 both just fly the one box).
    fms_gns = w.gns2 if (m == Mode.FMS2 and w.gns2 is not None) else w.gns

    # long-press events (F8/F9): CLR-hold -> Default NAV in FMS mode, COM
    # SWAP-hold -> the 121.500 emergency channel. Real long presses now drive
    # these directly; the keyboard `Home` / `F11` shortcuts remain as a
    # no-hardware fallback.
    if "CLR" in long_press and m in (Mode.FMS1, Mode.FMS2):
        fms_gns.cursor.go_to_default_nav()
    if "SWAP" in long_press and m in (Mode.COM1, Mode.COM2):
        (w.radios.com1 if m == Mode.COM1 else w.radios.com2).set_emergency()

    # the latched "shift" clears whenever the mode selector moves
    if m != w._last_mode:
        w._last_mode = m
        w.shift_latched = False
    if "KNOB" in pressed and (getattr(m, "name", "") in _SHIFT_FN):
        w.shift_latched = not w.shift_latched
    shift = w.shift_latched

    if m in (Mode.FMS1, Mode.FMS2):
        # while the PROC selector is up it owns every key (like the DTO page)
        if getattr(fms_gns, "_proc_dialog", None) is None:
            for b in pressed & set(_FMS_BEZEL):    # AP row -> bezel keys
                _fms_bezel(w, _FMS_BEZEL[b], fms_gns)
        # ENT on the Charts page fetches + opens a plate PDF - real file I/O,
        # so it's intercepted here rather than in gpsnav.py's pure state
        # machine (same split as the Weather page's data living in render.py).
        if "ENT" in pressed and fms_gns.cursor.page_name == "Charts":
            _open_selected_chart(w, fms_gns)
        fms_gns.handle_event(ev)
        return

    # every non-FMS mode: AP-row buttons drive the autopilot
    for b in pressed & set(_AP_BTN):
        getattr(w.ap, _AP_BTN[b])()

    if m in (Mode.COM1, Mode.COM2):
        radio = w.radios.com1 if m == Mode.COM1 else w.radios.com2
        if "SWAP" in pressed:
            radio.swap()
        if shift and m == Mode.COM1:              # shift latched -> heading bug
            if outer:
                w.ap.turn_heading_bug(outer * 10)
            if inner:
                w.ap.turn_heading_bug(inner)
        elif shift:                               # COM2 shift latched -> altimeter setting
            if outer:
                w.baro_inhg = _clamp(w.baro_inhg + outer * 0.10, _BARO_MIN, _BARO_MAX)
            if inner:
                w.baro_inhg = _clamp(w.baro_inhg + inner * 0.01, _BARO_MIN, _BARO_MAX)
        else:
            if outer:
                radio.tune(mhz=outer)
            if inner:
                radio.tune(khz=inner)
    elif m in (Mode.NAV1, Mode.NAV2):
        radio = w.radios.nav1 if m == Mode.NAV1 else w.radios.nav2
        if "SWAP" in pressed:
            radio.swap()
        if shift:                                 # shift latched -> CRS (OBS card)
            if outer:
                radio.turn_obs(outer * 10)
            if inner:
                radio.turn_obs(inner)
            # Pilot's Guide: "When OBS mode is selected, the pilot may set
            # the desired course... using the Select OBS Course pop-up
            # window, OR AN EXTERNAL OBS SELECTOR ON THE HSI OR CDI" - this
            # knob (NAV1's CRS selector) is that external selector, so it
            # also drives the GPS's own OBS course whenever GPS-source OBS
            # is active (NAV2's knob only ever sets its own VOR/ILS course).
            if m == Mode.NAV1 and w.gns.obs_active and w.gns.cdi_source == "GPS":
                w.gns.set_obs(radio.obs_deg)
        else:
            if outer:
                radio.tune(mhz=outer)
            if inner:
                radio.tune(khz=inner)
    elif m == Mode.XPDR:
        if "SWAP" in pressed:                     # flip-flop key -> IDENT pulse
            w.radios.xpdr.ident()
            w.ident_timer = 18.0
        if shift:                                 # shift latched -> transponder mode
            if inner:
                w.radios.xpdr.cycle_mode(1 if inner > 0 else -1)
            if outer:
                w.radios.xpdr.cycle_mode(1 if outer > 0 else -1)
        else:
            if inner:
                w.radios.xpdr.edit_digit(w.radios.xpdr.cursor, inner)
            if outer:
                w.radios.xpdr.move_cursor(outer)
    elif m == Mode.AP:
        if outer:
            w.ap.set_preselect(max(0.0, w.ap.alt_preselect + outer * 100))   # ALT select
        if inner:
            if shift:                            # shift latched -> IAS set-point
                w.set_ias_target(w.ias_target + inner * _IAS_STEP_KT)
            else:
                w.ap.set_vs_target(_clamp(w.ap.vs_target + inner * 100, -2000.0, 2000.0))


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def run(cfg: Config) -> int:
    if cfg.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"

    import pygame
    from render import Renderer, Scene

    w = World(cfg)

    gdl90 = None
    if cfg.gdl90 and not cfg.headless:
        try:
            import gdl90_out
            gdl90 = gdl90_out.GDL90Sender(host=cfg.gdl90_host, port=cfg.gdl90_port).open()
            print(f"GDL90: broadcasting ownship to {cfg.gdl90_host}:{cfg.gdl90_port}")
        except Exception as exc:               # noqa: BLE001 - EFB output is optional
            print(f"GDL90 not started ({exc}).")

    ff_listener = None
    ff_broadcast_addr = gdl90.addr if gdl90 is not None else None   # to fall back to
    ff_target_ip = None                       # currently-unicast-targeted device, or None
    if cfg.gdl90_discover and gdl90 is not None and not cfg.headless:
        try:
            import foreflight_discovery
            ff_listener = foreflight_discovery.ForeFlightListener().open()
            print(f"ForeFlight discovery: listening on UDP "
                  f"{foreflight_discovery.DISCOVERY_PORT}")
        except Exception as exc:               # noqa: BLE001 - discovery is optional
            print(f"ForeFlight discovery not started ({exc}).")

    device = None
    if not cfg.no_device and not cfg.headless:
        try:
            from ifr1 import IFR1
            device = IFR1().open()
        except Exception as exc:               # noqa: BLE001 - device is optional
            print(f"IFR-1 not opened ({exc}); keyboard control only.")

    from render import STACK_W, STACK_H, WIN_W, WIN_H

    pygame.init()
    screen = pygame.display.set_mode(
        (STACK_W, STACK_H) if cfg.layout == "stack" else (WIN_W, WIN_H))
    pygame.display.set_caption("Octavi IFR-1 trainer")
    renderer = Renderer(screen)
    clock = pygame.time.Clock()

    ui = {"layout": cfg.layout, "map_range": 20.0, "nav1_hsi": False, "running": True,
          "time_warp": cfg.time_warp, "stack_tab": "WX", "plate_filter": "ALL"}
    frames = 0
    nearby: list = []
    last_pos = None
    last_leds = -1
    last_layout = ui["layout"]

    while ui["running"]:
        dt = min(clock.tick(30) / 1000.0, 0.1)

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                ui["running"] = False
            elif e.type == pygame.KEYDOWN:
                _on_key(e, w, ui)
            elif e.type == pygame.MOUSEBUTTONDOWN and ui["layout"] == "stack":
                _on_stack_click(e, w, ui, renderer)

        # `stack` is the only layout that needs meaningfully more screen than
        # the other three comfortably share (1000x640) - resize only on an
        # actual layout change, not every frame, so a manual window resize by
        # the user isn't fought every tick.
        if ui["layout"] != last_layout:
            screen = pygame.display.set_mode(
                (STACK_W, STACK_H) if ui["layout"] == "stack" else (WIN_W, WIN_H))
            renderer.surf = screen
            last_layout = ui["layout"]

        if device is not None:
            for ev in device.poll():
                route_event(ev, w)

        # Time warp: run N physics/nav ticks per rendered frame, each with the
        # ORIGINAL (real-time) dt, rather than one tick with an inflated dt.
        # That keeps every dt-sensitive threshold (altitude/GS capture bands,
        # waypoint sequencing, turn anticipation) exactly as fine-grained as at
        # 1x - only the render/LED/nearby-airport refresh below runs once per
        # real frame, using the last sub-tick's Frame. A live X-Plane feed sets
        # its own pace regardless (it's the real world's clock, not ours), so
        # warp has no effect while one is connected.
        warp = max(1, int(ui.get("time_warp", 1)))
        fr = None
        for _ in range(warp):
            fr = w.tick(dt)

        if ff_listener is not None:
            ff_listener.poll()
            ff_target_ip = _apply_ff_discovery(gdl90, ff_listener, ff_target_ip, ff_broadcast_addr)

        if gdl90 is not None:
            gdl90.send(fr.own)

        if device is not None:
            leds = w.ap.led_bitmask()
            if leds != last_leds:
                try:
                    device.set_leds(leds)
                except Exception:      # noqa: BLE001 - LED write is best-effort
                    pass
                last_leds = leds

        if last_pos is None or great_circle_nm(last_pos, fr.own.pos) > 3.0:
            nearby = _nearby(w.db, fr.own.pos, ui["map_range"])
            if not w.feed_live:                # feed supplies its own magvar
                w.magvar = _local_magvar(w.db, fr.own.pos)
            last_pos = fr.own.pos

        mname = w._last_mode.name if w._last_mode is not None else ""
        shift_hint = (f"SHIFT {_SHIFT_FN[mname]}"
                      if (w.shift_latched and mname in _SHIFT_FN) else "")
        # keyboard-only "which FMS unit" indicator (the IFR-1 shows this for
        # free via its physical FMS1/FMS2 selector position - see `mname`).
        if ui.get("kbd_fms2") and w.gns2 is not None and not shift_hint:
            shift_hint = "KBD FMS2"
        renderer.draw(Scene(
            own=fr.own, nav=fr.nav, panel=fr.panel, gns=w.gns, db=w.db, magvar=w.magvar,
            variant=w.gns.variant,
            map_range_nm=ui["map_range"], autopilot=w.ap.engaged or w.gps_follow,
            fps=clock.get_fps(), nearby=nearby,
            messages=w.gns.peek_messages(), show_messages=w.show_msg,
            baro_inhg=w.baro_inhg, shift_hint=shift_hint, selector_mode=mname,
            layout=ui["layout"], sixpack=fr.sixpack, nav1_head=fr.nav1, nav2_head=fr.nav2,
            nav1_hsi=ui["nav1_hsi"], ap=w.ap, radios=w.radios,
            ias_target=w.ias_target, ias_managed=w._ias_managed, t=w.t,
            time_warp=warp, gns2=w.gns2,
            stack_tab=ui["stack_tab"], wind_from_deg=w.sim.wind_from, wind_kt=w.sim.wind_kt,
            plate_filter=ui["plate_filter"], panel2=fr.panel2,
        ))
        pygame.display.flip()

        frames += 1
        if cfg.headless and frames >= 5:
            ui["running"] = False

    if device is not None:
        device.close()
    if w.feed is not None:
        w.feed.close()
    if gdl90 is not None:
        gdl90.close()
    if ff_listener is not None:
        ff_listener.close()
    if w.wx_updater is not None:
        w.wx_updater.stop()               # daemon thread; no need to block exit on join
    pygame.quit()

    summary = w.score.summary()
    if summary.scored:
        for line in summary.lines():
            print(line)
    return 0


def _apply_ff_discovery(gdl90, listener, target_ip: str | None,
                        broadcast_addr: tuple[str, int]) -> str | None:
    """Retarget ``gdl90`` (a `gdl90_out.GDL90Sender`) at whatever ForeFlight
    device ``listener`` currently considers ``primary`` - direct unicast once
    one answers, back to ``broadcast_addr`` once it goes stale (the tablet
    closed ForeFlight, walked out of range, etc.). Returns the IP now being
    targeted (``None`` if back on broadcast) for the caller to hold onto and
    pass back in next call. Pulled out of `run()`'s loop so it's a plain,
    directly testable function - no sockets, pygame, or the loop itself
    needed to exercise the retarget decision."""
    dev = listener.primary
    if dev is not None and dev.ip != target_ip:
        gdl90.addr = (dev.ip, dev.gdl90_port)
        print(f"ForeFlight discovered at {dev.ip}:{dev.gdl90_port} "
              f"({dev.app}) -> sending direct unicast")
        return dev.ip
    if dev is None and target_ip is not None:
        gdl90.addr = broadcast_addr               # the tablet went quiet - fall back
        print(f"ForeFlight discovery stale -> back to broadcast "
              f"{broadcast_addr[0]}:{broadcast_addr[1]}")
        return None
    return target_ip


def _open_selected_chart(w: World, gns=None) -> None:
    """ENT on the AUX Charts page: fetch (if not already cached) the
    currently-selected plate and hand it to the OS's default PDF viewer.
    Runs the actual fetch off the main thread - a slow download must not
    stall the ~30 Hz loop, same reasoning as `wx_auto.py`. Progress/errors
    surface as GNS messages rather than a return value since nothing is
    waiting on this synchronously. ``gns`` is the unit whose Charts page is
    open (FMS1's `w.gns` by default; the routed FMS2 unit in --dual)."""
    g = gns if gns is not None else w.gns
    idents = g.wx_station_idents()
    if not idents:
        return
    ai = max(0, min(len(idents) - 1, g.chart_airport_sel))
    ident = idents[ai]
    chart_sel = g.chart_sel

    def worker() -> None:
        try:
            from datasrc import dtpp
            from datasrc.airac import current_cycle
            root = dtpp.default_data_root()
            cycle = current_cycle()
            records = dtpp.load_index(root, cycle)
            if records is None:
                g.messages.append("NO CHART INDEX - datasrc.dtpp update-index")
                return
            charts = dtpp.charts_for_airport(records, ident)
            if not charts:
                g.messages.append(f"NO CHARTS FOR {ident}")
                return
            chart = charts[max(0, min(len(charts) - 1, chart_sel))]
            path = dtpp.fetch_and_cache_chart(root, cycle, chart.pdf_name)
            dtpp.open_with_os_default(path)
            g.messages.append(f"OPENED {chart.chart_name}")
        except Exception as exc:                  # noqa: BLE001 - background, must not die
            g.messages.append(f"CHART OPEN FAILED: {exc}")

    g.messages.append(f"OPENING CHART for {ident}...")
    threading.Thread(target=worker, daemon=True).start()


def _open_stack_plate(w: World, renderer, gns=None) -> None:
    """PLATE tab (``stack`` layout only): fetch (if not already cached) the
    same AUX>Charts selection and rasterize page 1 inline with `pypdfium2`,
    instead of `_open_selected_chart`'s hand-off to the OS's PDF viewer -
    the "stack" layout's one real PDF behavior change (see WORKING.md).
    Runs off the main thread, same reasoning as `_open_selected_chart`;
    writes into ``renderer._plate_cache``/``_plate_loading``/``_plate_error``,
    which `Renderer._draw_stack_plate` only ever reads."""
    g = gns if gns is not None else w.gns
    idents = g.wx_station_idents()
    if not idents:
        return
    ai = max(0, min(len(idents) - 1, g.chart_airport_sel))
    ident = idents[ai]
    chart_sel = g.chart_sel

    def worker() -> None:
        pdf_name = None
        try:
            from datasrc import dtpp
            from datasrc.airac import current_cycle
            root = dtpp.default_data_root()
            cycle = current_cycle()
            records = dtpp.load_index(root, cycle)
            if records is None:
                g.messages.append("NO CHART INDEX - datasrc.dtpp update-index")
                return
            charts = dtpp.charts_for_airport(records, ident)
            if not charts:
                g.messages.append(f"NO CHARTS FOR {ident}")
                return
            chart = charts[max(0, min(len(charts) - 1, chart_sel))]
            pdf_name = chart.pdf_name
            renderer._plate_loading.add(pdf_name)
            path = dtpp.fetch_and_cache_chart(root, cycle, pdf_name)

            import pygame
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(path))
            try:
                page = pdf[0]
                bitmap = page.render(scale=150 / 72, rev_byteorder=True)
                try:
                    fmt = "RGBA" if bitmap.n_channels == 4 else "RGB"
                    # .copy() (not .convert(), which touches the display) -
                    # this runs off the main thread and only needs its own
                    # backing buffer, not a display-format conversion
                    surf = pygame.image.frombuffer(
                        bytes(bitmap.buffer), (bitmap.width, bitmap.height), fmt).copy()
                finally:
                    bitmap.close()
            finally:
                pdf.close()
            renderer._plate_cache[pdf_name] = surf
            renderer._plate_error.pop(pdf_name, None)
        except Exception as exc:                  # noqa: BLE001 - background, must not die
            if pdf_name:
                renderer._plate_error[pdf_name] = str(exc)
            g.messages.append(f"PLATE LOAD FAILED: {exc}")
        finally:
            if pdf_name:
                renderer._plate_loading.discard(pdf_name)

    threading.Thread(target=worker, daemon=True).start()


def _on_stack_click(e, w: World, ui: dict, renderer) -> None:
    """MOUSEBUTTONDOWN routing for the `stack` layout - the only layout with
    any mouse surface. `Renderer._stack_layout` rebuilds `renderer._stack_hit`
    (rect name -> pygame.Rect) every frame; this just hit-tests the click
    against it and applies whichever control was hit."""
    if e.button != 1:
        return
    pos = e.pos
    for name, rect in renderer._stack_hit.items():
        if not rect.collidepoint(pos):
            continue
        if name.startswith("tab:"):
            ui["stack_tab"] = name.split(":", 1)[1]
        elif name.startswith("warp:"):
            ui["time_warp"] = int(name.split(":", 1)[1])
        elif name == "wind_dir:+":
            w.sim.set_wind(w.sim.wind_from + 10, w.sim.wind_kt)
        elif name == "wind_dir:-":
            w.sim.set_wind(w.sim.wind_from - 10, w.sim.wind_kt)
        elif name == "wind_kt:+":
            w.sim.set_wind(w.sim.wind_from, max(0.0, w.sim.wind_kt + 5))
        elif name == "wind_kt:-":
            w.sim.set_wind(w.sim.wind_from, max(0.0, w.sim.wind_kt - 5))
        elif name == "bug:hdg:+":
            w.ap.turn_heading_bug(5)
        elif name == "bug:hdg:-":
            w.ap.turn_heading_bug(-5)
        elif name == "bug:ias:+":
            w.set_ias_target(w.ias_target + _IAS_STEP_KT)
        elif name == "bug:ias:-":
            w.set_ias_target(w.ias_target - _IAS_STEP_KT)
        elif name == "bug:alt:+":
            w.ap.set_preselect(w.ap.alt_preselect + 100.0)
        elif name == "bug:alt:-":
            w.ap.set_preselect(w.ap.alt_preselect - 100.0)
        elif name.startswith("wx:airport:"):
            i = int(name.rsplit(":", 1)[1])
            idents = w.gns.wx_station_idents()
            if 0 <= i < len(idents):
                w.gns.wx_sel = i
                w.gns.wx_scroll = 0             # new station - back to the top (matches
                                                 # gpsnav._page_edit's own outer-knob behavior)
        elif name.startswith("plate:airport:"):
            i = int(name.rsplit(":", 1)[1])
            idents = w.gns.wx_station_idents()
            if 0 <= i < len(idents):
                w.gns.chart_airport_sel = i
                w.gns.chart_sel = 0             # new airport - reset chart index (ditto)
        elif name.startswith("plate:chart:"):
            w.gns.chart_sel = int(name.rsplit(":", 1)[1])
        elif name.startswith("radio:"):
            _, which, action = name.split(":")
            rx_ = w.radios.com2 if which == "com2" else w.radios.nav2
            if action == "swap":
                rx_.swap()
            elif action == "mhz+":
                rx_.tune(mhz=1)
            elif action == "mhz-":
                rx_.tune(mhz=-1)
            elif action == "khz+":
                rx_.tune(khz=1)
            elif action == "khz-":
                rx_.tune(khz=-1)
        elif name.startswith("plate:filter:"):
            ui["plate_filter"] = name.split(":", 2)[2]
        elif name == "plate:load":
            # the WX/PLATE tabs are always driven by the primary unit
            # (Renderer._draw_stack_plate/_draw_aux_weather read Scene.gns,
            # which main.py's Scene() always sets to w.gns - not the
            # kbd_fms2-routed unit the GNS bezel keys use) - fetch from the
            # same unit so what loads matches what's shown
            _open_stack_plate(w, renderer, gns=w.gns)
        return


def _nearby(db, pos, rng):
    out = [(a.ident, a.pos, "apt") for a in db.nearest_airports(pos, 6, max_nm=rng * 1.4)]
    out += [(n.ident, n.pos, "navaid")
            for n in db.nearest_navaids(pos, 8, max_nm=rng * 1.4, ndb=False)]
    return out


def _on_key(e, w: World, ui: dict) -> None:
    import pygame
    from ifr1 import Event, Mode

    k = e.key
    shift = bool(e.mod & pygame.KMOD_SHIFT)

    # The keyboard has no physical FMS1/FMS2 selector, so in --dual `U`
    # toggles which unit every GNS-page key below drives (an annunciator
    # shows "FMS2" while it's the active one - see run()'s shift_hint).
    # `mode=Mode.FMS1` on the Event()s below is just which enum member
    # `handle_event` requires be "some FMS mode"; it does not mean unit 1
    # specifically - `g` is what actually selects the unit (see
    # gpsnav.GpsNav.handle_event, which only checks FMS-vs-not).
    if k == pygame.K_u and w.gns2 is not None:
        ui["kbd_fms2"] = not ui.get("kbd_fms2", False)
        return
    g = w.gns2 if (ui.get("kbd_fms2") and w.gns2 is not None) else w.gns

    pdlg = getattr(g, "_proc_dialog", None)
    if pdlg is not None:                      # PROC selector has the keyboard
        if k in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
        elif k in (pygame.K_UP, pygame.K_LEFT):
            pdlg.move(-1)
        elif k in (pygame.K_DOWN, pygame.K_RIGHT):
            pdlg.move(1)
        return

    mdlg = getattr(g, "_fpl_menu", None)
    if mdlg is not None:                      # MNU pop-up has the keyboard
        if k in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
        elif k in (pygame.K_UP, pygame.K_LEFT):
            g.handle_event(Event(mode=Mode.FMS1, outer=-1))
        elif k in (pygame.K_DOWN, pygame.K_RIGHT):
            g.handle_event(Event(mode=Mode.FMS1, outer=1))
        return

    dlg = getattr(g, "_dto_dialog", None)
    if dlg is not None:                       # Direct-To page has the keyboard
        if k == pygame.K_ESCAPE or k == pygame.K_BACKSPACE:
            g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
        elif k == pygame.K_LEFT:
            dlg.move_cursor(-1)
        elif k == pygame.K_RIGHT:
            dlg.move_cursor(1)
        elif k == pygame.K_UP:
            dlg.scroll_char(1)
        elif k == pygame.K_DOWN:
            dlg.scroll_char(-1)
        return

    fed = getattr(g, "_fpl_edit", None)
    fbuf = fed.get("buf") if fed else None
    if fbuf is not None:                      # editing a waypoint identifier on the FPL page
        if k == pygame.K_ESCAPE or k == pygame.K_BACKSPACE:
            g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
        elif k == pygame.K_LEFT:
            fbuf.move_cursor(-1)
        elif k == pygame.K_RIGHT:
            fbuf.move_cursor(1)
        elif k == pygame.K_UP:
            fbuf.scroll_char(1)
        elif k == pygame.K_DOWN:
            fbuf.scroll_char(-1)
        return
    if fed is not None:                       # FPL page, CRSR on, no row open yet: Up/Down pick the row
        if k == pygame.K_UP:
            g.handle_event(Event(mode=Mode.FMS1, outer=-1))
            return
        elif k == pygame.K_DOWN:
            g.handle_event(Event(mode=Mode.FMS1, outer=1))
            return
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            g.handle_event(Event(mode=Mode.FMS1, pressed=("ENT",)))
            return
        elif k == pygame.K_BACKSPACE:
            g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
            return
        # anything else (TAB to drop CRSR, ESC to quit, ...) falls through
        # to the normal keymap below

    if k == pygame.K_ESCAPE:
        ui["running"] = False
    elif k == pygame.K_LEFT:
        w.manual_heading = (w.manual_heading - 5) % 360
    elif k == pygame.K_RIGHT:
        w.manual_heading = (w.manual_heading + 5) % 360
    elif k == pygame.K_UP:
        w.sim.command(altitude=w.sim.altitude + 500)
    elif k == pygame.K_DOWN:
        w.sim.command(altitude=w.sim.altitude - 500)
    elif k == pygame.K_COMMA:
        w.set_ias_target(w.ias_target - _IAS_STEP_KT)
    elif k == pygame.K_PERIOD:
        w.set_ias_target(w.ias_target + _IAS_STEP_KT)
    elif k == pygame.K_LEFTBRACKET:
        ui["map_range"] = min(160.0, ui["map_range"] * 1.5)
    elif k == pygame.K_RIGHTBRACKET:
        ui["map_range"] = max(2.0, ui["map_range"] / 1.5)
    elif k == pygame.K_n:
        w.gps_follow = not w.gps_follow
    elif k == pygame.K_l:
        ui["layout"] = _next_layout(ui["layout"], dual=w.gns2 is not None)
    elif k == pygame.K_h:
        ui["nav1_hsi"] = not ui["nav1_hsi"]
    elif k == pygame.K_s:
        g.toggle_suspend()
    elif k == pygame.K_v:
        g.toggle_cdi_source()
    elif k == pygame.K_b:
        g.toggle_obs()
    elif k in (pygame.K_MINUS, pygame.K_EQUALS):
        step = (10 if shift else 1) * (1 if k == pygame.K_EQUALS else -1)
        g.nudge_obs(step)
    elif k == pygame.K_m:
        # the on-screen MSG box only ever reflects FMS1's queue (see run()'s
        # Scene build) - on FMS2 this just acks its queue, same as _fms_bezel.
        if g is w.gns:
            w.show_msg = not w.show_msg
            if not w.show_msg:
                g.ack_messages()
        else:
            g.ack_messages()
    elif k == pygame.K_HOME:
        g.cursor.go_to_default_nav()
    elif k == pygame.K_c:
        # the CLR bezel key outside any modal page/dialog - cancels an
        # active Direct-To (resumes the nearest flight-plan leg), deletes
        # the selected Flight Plan/Catalog row, or backs out to Default
        # NAV. `Home` above only jumps pages; this was the missing
        # keyboard route to a bare CLR press (no-device users had no way
        # to cancel a Direct-To without it).
        g.handle_event(Event(mode=Mode.FMS1, pressed=("CLR",)))
    elif k == pygame.K_F11:
        w.radios.com1.set_emergency()
    elif k == pygame.K_a:
        w.ap.press_ap()
    elif k == pygame.K_F1:
        w.ap.press_hdg()
    elif k == pygame.K_F2:
        w.ap.press_nav()
    elif k == pygame.K_F3:
        w.ap.press_apr()
    elif k == pygame.K_F4:
        w.ap.press_rev()
    elif k == pygame.K_F5:
        w.ap.press_alt()
    elif k == pygame.K_F6:
        w.ap.press_vs()
    elif k == pygame.K_g:
        w.ap.toggle_gpss()
    elif k == pygame.K_9:
        w.ap.turn_vs_knob(-1)
    elif k == pygame.K_0:
        w.ap.turn_vs_knob(1)
    elif k == pygame.K_o:
        w.radios.nav1.turn_obs(1 if shift else -1)
        if w.gns.obs_active and w.gns.cdi_source == "GPS":     # see the NAV1/CRS branch above
            w.gns.set_obs(w.radios.nav1.obs_deg)
    elif k == pygame.K_k:
        w.radios.nav2.turn_obs(-1)
    elif k == pygame.K_p:
        w.radios.nav2.turn_obs(1)
    elif k == pygame.K_TAB:
        g.handle_event(Event(mode=Mode.FMS1, pressed=("KNOB",)))
    elif k == pygame.K_d:
        g.handle_event(Event(mode=Mode.FMS1, pressed=("DCT",)))   # opens the entry page
    elif k == pygame.K_r:
        g.begin_proc_select()                                     # PROC key
    elif k == pygame.K_x:
        # MNU key - the Flight Plan / Flight Plan Catalog page menu (Invert/
        # Copy/Sort/Delete). Previously had no keyboard route at all.
        g.handle_event(Event(mode=Mode.FMS1, pressed=("MNU",)))
    elif k == pygame.K_PAGEUP:
        # plain = small (inner) knob: page within the group, e.g. Default NAV -> Map;
        # +Shift = large (outer) knob: page GROUP, e.g. NAV -> WPT -> AUX -> NRST -
        # the only keyboard route to the WPT/AUX/NRST pages (incl. AUX > Weather)
        # without the IFR-1's physical outer knob.
        g.handle_event(Event(mode=Mode.FMS1, outer=1 if shift else 0,
                             inner=0 if shift else 1))
    elif k == pygame.K_PAGEDOWN:
        g.handle_event(Event(mode=Mode.FMS1, outer=-1 if shift else 0,
                             inner=0 if shift else -1))
    elif pygame.K_1 <= k <= pygame.K_4:            # 1/2/3/4 -> TIME_WARP_LEVELS[0..3]
        ui["time_warp"] = TIME_WARP_LEVELS[k - pygame.K_1]


def cli() -> int:
    """Console-script entry point (`octavi-trainer`)."""
    return run(parse_args())


if __name__ == "__main__":
    sys.exit(cli())
