"""windsaloft.py - a multi-altitude wind/temperature profile.

The trainer's wind used to be a single uniform DIR/SPD for the whole flight.
This models the real "winds aloft" forecast (the FAA/NWS FD product): a small
table of levels, each with a wind (direction/speed) and an outside-air
temperature, that `sim_model.SimModel` interpolates against the ownship's
current altitude. A profile can come from the command line
(:func:`parse_cli`) or from a fetched/cached FD forecast
(``datasrc.wx`` -> :meth:`WindsAloftProfile.from_fd_levels`).

No I/O here - pure data + interpolation, like `navmath.py`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from navmath import norm360

__all__ = ["WindLevel", "WindsAloftProfile", "isa_temp_c", "parse_cli"]


def isa_temp_c(altitude_ft: float) -> float:
    """ISA standard temperature (C) at a pressure altitude, troposphere only."""
    return 15.0 - 1.98 * (max(altitude_ft, 0.0) / 1000.0)


@dataclass(frozen=True, slots=True)
class WindLevel:
    alt_ft: float
    from_deg: float          # meteorological, degrees TRUE, direction it blows FROM
    kt: float
    temp_c: float | None = None


def _interp(lo: WindLevel, hi: WindLevel, alt_ft: float) -> tuple[float, float, float | None]:
    if hi.alt_ft == lo.alt_ft:
        f = 0.0
    else:
        f = (alt_ft - lo.alt_ft) / (hi.alt_ft - lo.alt_ft)
    f = max(0.0, min(1.0, f))
    # interpolate direction via the wind's vector components so 350->010 blends
    # through 000, not backwards through 180
    lo_rad, hi_rad = math.radians(lo.from_deg), math.radians(hi.from_deg)
    lx = lo.kt * math.sin(lo_rad); ly = lo.kt * math.cos(lo_rad)
    hx = hi.kt * math.sin(hi_rad); hy = hi.kt * math.cos(hi_rad)
    x = lx + (hx - lx) * f
    y = ly + (hy - ly) * f
    kt = math.hypot(x, y)
    from_deg = norm360(math.degrees(math.atan2(x, y))) if kt > 1e-9 else lo.from_deg
    temp = None
    if lo.temp_c is not None and hi.temp_c is not None:
        temp = lo.temp_c + (hi.temp_c - lo.temp_c) * f
    return from_deg, kt, temp


@dataclass(frozen=True, slots=True)
class WindsAloftProfile:
    """A sorted table of :class:`WindLevel` the sim interpolates by altitude.

    Below the lowest level or above the highest, the nearest level's wind/temp
    holds constant (no extrapolation) - the same assumption pilots make when
    reading a printed winds-aloft table.
    """

    levels: tuple[WindLevel, ...]

    def __post_init__(self):
        object.__setattr__(self, "levels", tuple(sorted(self.levels, key=lambda w: w.alt_ft)))

    @property
    def has_temps(self) -> bool:
        return any(w.temp_c is not None for w in self.levels)

    def wind_at(self, alt_ft: float) -> tuple[float, float]:
        """(from_deg, kt) at ``alt_ft``, interpolated between bracketing levels."""
        if not self.levels:
            return 0.0, 0.0
        if len(self.levels) == 1 or alt_ft <= self.levels[0].alt_ft:
            w = self.levels[0]
            return w.from_deg, w.kt
        if alt_ft >= self.levels[-1].alt_ft:
            w = self.levels[-1]
            return w.from_deg, w.kt
        for lo, hi in zip(self.levels, self.levels[1:]):
            if lo.alt_ft <= alt_ft <= hi.alt_ft:
                from_deg, kt, _ = _interp(lo, hi, alt_ft)
                return from_deg, kt
        return self.levels[-1].from_deg, self.levels[-1].kt   # pragma: no cover - unreachable

    def temp_at(self, alt_ft: float) -> float | None:
        """OAT (C) at ``alt_ft``, or ``None`` if no level in the profile carries one."""
        if not self.has_temps:
            return None
        with_t = [w for w in self.levels if w.temp_c is not None]
        if len(with_t) == 1 or alt_ft <= with_t[0].alt_ft:
            return with_t[0].temp_c
        if alt_ft >= with_t[-1].alt_ft:
            return with_t[-1].temp_c
        for lo, hi in zip(with_t, with_t[1:]):
            if lo.alt_ft <= alt_ft <= hi.alt_ft:
                _, _, temp = _interp(lo, hi, alt_ft)
                return temp
        return with_t[-1].temp_c   # pragma: no cover - unreachable

    def isa_dev_c_at(self, alt_ft: float) -> float | None:
        """Deviation from ISA standard temperature at ``alt_ft`` (+ = warmer than standard)."""
        t = self.temp_at(alt_ft)
        return None if t is None else t - isa_temp_c(alt_ft)

    @classmethod
    def uniform(cls, from_deg: float, kt: float, temp_c: float | None = None) -> "WindsAloftProfile":
        """A degenerate one-level profile - the old single DIR/SPD behaviour."""
        return cls((WindLevel(0.0, norm360(from_deg), float(kt), temp_c),))

    @classmethod
    def from_fd_levels(cls, levels: list[tuple[int, float | None, float | None, float | None]]
                        ) -> "WindsAloftProfile | None":
        """Build from ``datasrc.wx`` decoded FD rows: ``(alt_ft, from_deg, kt, temp_c)``,
        dropping levels the forecast left blank/light-and-variable (``from_deg`` or
        ``kt`` is ``None``). ``None`` if nothing usable remains."""
        out = [WindLevel(float(alt), norm360(d), float(k), t)
               for alt, d, k, t in levels if d is not None and k is not None]
        return cls(tuple(out)) if out else None


_ENTRY_RE = re.compile(
    r"^(?P<alt>\d+)\s*:\s*(?P<dir>\d+(?:\.\d+)?)\s*/\s*(?P<spd>\d+(?:\.\d+)?)"
    r"(?:\s*/\s*(?P<temp>[+-]?\d+(?:\.\d+)?))?$"
)


def parse_cli(s: str) -> WindsAloftProfile:
    """Parse the ``--winds-aloft`` CLI value: one or more whitespace- or
    comma-separated ``ALT:DIR/SPD`` or ``ALT:DIR/SPD/TEMPC`` entries, e.g.::

        "3000:280/20/-05 9000:300/35/-15 18000:310/55/-30"

    Altitude is feet MSL, direction true degrees, speed knots, temperature
    (optional) Celsius. Raises :class:`ValueError` with the bad token on a
    malformed entry.
    """
    levels: list[WindLevel] = []
    for tok in re.split(r"[,\s]+", s.strip()):
        if not tok:
            continue
        m = _ENTRY_RE.match(tok)
        if not m:
            raise ValueError(f"bad --winds-aloft entry {tok!r} "
                             "(want ALT:DIR/SPD or ALT:DIR/SPD/TEMPC)")
        temp = float(m["temp"]) if m["temp"] is not None else None
        levels.append(WindLevel(float(m["alt"]), norm360(float(m["dir"])), float(m["spd"]), temp))
    if not levels:
        raise ValueError("--winds-aloft: no entries parsed")
    return WindsAloftProfile(tuple(levels))
