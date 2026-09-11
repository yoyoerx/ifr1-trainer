"""wmm.py - World Magnetic Model declination for ownship-position magvar.

A compact implementation of the WMM spherical-harmonic synthesis (degree/order
12), reading the vendored NOAA coefficient file ``assets/wmm/WMM2025.COF``. The
algorithm is NOAA's published, public-domain method; the coefficients are U.S.
Government work (NGA / NOAA NCEI), not copyrightable.

Only :func:`declination` is used by the trainer - it returns the magnetic
declination in degrees, **east-positive**, matching the project convention
``true = magnetic + magvar`` (ARCHITECTURE sec.4). ``main`` uses it as the
primary source for ownship magvar and keeps the nearest-navaid value as a
fallback for positions the model can't cover (or if the file is missing).

Accuracy: agrees with NOAA's ``WMM2025_TestValues.txt`` to < 0.01 deg.
"""

from __future__ import annotations

import math
from datetime import date
from functools import lru_cache
from pathlib import Path

__all__ = ["WmmModel", "declination", "load_default", "decimal_year"]

_COF_PATH = Path(__file__).resolve().parent / "assets" / "wmm" / "WMM2025.COF"
_MAXORD = 12

# WGS-84 ellipsoid + geomagnetic reference radius, km
_A = 6378.137
_B = 6356.7523142
_RE = 6371.2


def decimal_year(d: date | None = None) -> float:
    d = d or date.today()
    start = date(d.year, 1, 1)
    year_len = (date(d.year + 1, 1, 1) - start).days
    return d.year + (d - start).days / year_len


class WmmModel:
    """Loaded WMM coefficients + the field synthesis."""

    def __init__(self, epoch: float, name: str, c, cd, k, fn, fm, p0):
        self.epoch = epoch
        self.name = name
        self._c, self._cd, self._k = c, cd, k
        self._fn, self._fm, self._p0 = fn, fm, p0

    # -- construction --------------------------------------------------
    @classmethod
    def from_cof(cls, path: str | Path = _COF_PATH) -> "WmmModel":
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        head = lines[0].split()
        epoch, name = float(head[0]), head[1]

        size = _MAXORD + 1
        c = [[0.0] * size for _ in range(size)]
        cd = [[0.0] * size for _ in range(size)]
        for ln in lines[1:]:
            if ln.strip().startswith("9999") or not ln.strip():
                break
            n, m, gnm, hnm, dgnm, dhnm = ln.split()
            n, m = int(n), int(m)
            gnm, hnm, dgnm, dhnm = float(gnm), float(hnm), float(dgnm), float(dhnm)
            if n > _MAXORD:
                break
            c[m][n] = gnm
            cd[m][n] = dgnm
            if m != 0:
                c[n][m - 1] = hnm
                cd[n][m - 1] = dhnm

        # Schmidt semi-normalised -> unnormalised (NOAA recursion)
        snorm = [0.0] * (size * size)
        k = [[0.0] * size for _ in range(size)]
        fn = [0.0] * size
        fm = [float(i) for i in range(size)]
        snorm[0] = 1.0
        for n in range(1, _MAXORD + 1):
            snorm[n] = snorm[n - 1] * (2 * n - 1) / n
            j = 2
            for m in range(0, n + 1):
                k[m][n] = (((n - 1) ** 2) - (m * m)) / ((2 * n - 1) * (2 * n - 3))
                if m > 0:
                    flnmj = (n - m + 1) * j / (n + m)
                    snorm[n + m * size] = snorm[n + (m - 1) * size] * math.sqrt(flnmj)
                    j = 1
                    c[n][m - 1] *= snorm[n + m * size]
                    cd[n][m - 1] *= snorm[n + m * size]
                c[m][n] *= snorm[n + m * size]
                cd[m][n] *= snorm[n + m * size]
            fn[n] = float(n + 1)
        k[1][1] = 0.0
        return cls(epoch, name, c, cd, k, fn, fm, snorm)

    # -- synthesis --------------------------------------------------
    def field(self, glat: float, glon: float, alt_km: float, year: float
              ) -> tuple[float, float, float]:
        """North / East / Down field components (nT) at a geodetic position."""
        size = _MAXORD + 1
        dt = year - self.epoch
        c, cd, k, fn, fm = self._c, self._cd, self._k, self._fn, self._fm
        p = list(self._p0)                       # Legendre workspace (seeded by snorm)
        dp = [[0.0] * size for _ in range(size)]
        sp = [0.0] * size
        cp = [0.0] * size
        pp = [0.0] * size
        cp[0] = pp[0] = 1.0

        rlat, rlon = math.radians(glat), math.radians(glon)
        srlat, crlat = math.sin(rlat), math.cos(rlat)
        srlat2, crlat2 = srlat * srlat, crlat * crlat
        sp[1], cp[1] = math.sin(rlon), math.cos(rlon)

        a2, b2 = _A * _A, _B * _B
        c2 = a2 - b2
        a4, b4 = a2 * a2, b2 * b2
        c4 = a4 - b4

        # geodetic -> geocentric spherical
        q = math.sqrt(a2 - c2 * srlat2)
        q1 = alt_km * q
        q2 = ((q1 + a2) / (q1 + b2)) ** 2
        ct = srlat / math.sqrt(q2 * crlat2 + srlat2)
        st = math.sqrt(1.0 - ct * ct)
        r = math.sqrt(alt_km * alt_km + 2.0 * q1 + (a4 - c4 * srlat2) / (q * q))
        d = math.sqrt(a2 * crlat2 + b2 * srlat2)
        ca = (alt_km + d) / r
        sa = c2 * crlat * srlat / (r * d)

        for m in range(2, _MAXORD + 1):
            sp[m] = sp[1] * cp[m - 1] + cp[1] * sp[m - 1]
            cp[m] = cp[1] * cp[m - 1] - sp[1] * sp[m - 1]

        aor = _RE / r
        ar = aor * aor
        br = bt = bp = bpp = 0.0
        for n in range(1, _MAXORD + 1):
            ar *= aor
            for m in range(0, n + 1):
                # associated Legendre + derivative, via recursion
                if n == m:
                    p[n + m * size] = st * p[n - 1 + (m - 1) * size]
                    dp[m][n] = st * dp[m - 1][n - 1] + ct * p[n - 1 + (m - 1) * size]
                elif n == 1 and m == 0:
                    p[n + m * size] = ct * p[n - 1 + m * size]
                    dp[m][n] = ct * dp[m][n - 1] - st * p[n - 1 + m * size]
                else:  # n > 1 and n != m
                    if m > n - 2:
                        p[n - 2 + m * size] = 0.0
                        dp[m][n - 2] = 0.0
                    p[n + m * size] = (ct * p[n - 1 + m * size]
                                       - k[m][n] * p[n - 2 + m * size])
                    dp[m][n] = (ct * dp[m][n - 1] - st * p[n - 1 + m * size]
                                - k[m][n] * dp[m][n - 2])

                tcmn = c[m][n] + dt * cd[m][n]
                if m == 0:
                    t1 = tcmn * cp[m]
                    t2 = tcmn * sp[m]
                else:
                    thmn = c[n][m - 1] + dt * cd[n][m - 1]
                    t1 = tcmn * cp[m] + thmn * sp[m]
                    t2 = tcmn * sp[m] - thmn * cp[m]

                par = ar * p[n + m * size]
                bt -= ar * t1 * dp[m][n]
                bp += fm[m] * t2 * par
                br += fn[n] * t1 * par

                if st == 0.0 and m == 1:      # geographic pole special case
                    pp[n] = pp[n - 1] if n == 1 else ct * pp[n - 1] - k[m][n] * pp[n - 2]
                    bpp += fm[m] * t2 * (ar * pp[n])

        bp = bpp if st == 0.0 else bp / st

        # rotate spherical -> geodetic
        bx = -bt * ca - br * sa      # north
        by = bp                     # east
        bz = bt * sa - br * ca      # down
        return bx, by, bz

    def declination(self, lat: float, lon: float, *, alt_km: float = 0.0,
                    year: float | None = None) -> float:
        """Magnetic declination (deg, east-positive) at a geodetic position."""
        bx, by, _bz = self.field(lat, lon, alt_km, decimal_year() if year is None else year)
        return math.degrees(math.atan2(by, bx))


@lru_cache(maxsize=1)
def load_default() -> "WmmModel | None":
    try:
        return WmmModel.from_cof(_COF_PATH)
    except (OSError, ValueError):
        return None


def declination(lat: float, lon: float, *, alt_ft: float = 0.0,
                year: float | None = None) -> float | None:
    """Convenience wrapper on the bundled model. ``None`` if the file is absent."""
    model = load_default()
    if model is None:
        return None
    return model.declination(lat, lon, alt_km=alt_ft * 0.0003048, year=year)
