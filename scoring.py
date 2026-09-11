"""scoring.py - grade how well a leg / procedure was flown.

Pure accumulator: :meth:`ScoreTracker.sample` is fed one snapshot per loop
(the derived nav state, ownship, and the instrument panel), and
:meth:`ScoreTracker.summary` reduces the run to a :class:`ScoreSummary` with
RMS / max tracking errors and an ACS-style letter grade.

Only samples where there is real lateral guidance are scored (``NavState.mode``
in ``LEG`` / ``DTO``); OBS, SUSP and no-waypoint states are ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["ScoreSummary", "ScoreTracker"]

# ACS-ish full-scale tolerances: 3/4-scale CDI, 3/4-scale glideslope.
_CDI_TOL = 0.75
_GS_TOL = 0.75
_PEG = 0.98                      # |deflection| at/after which the needle is "pegged"
_SCORED_MODES = ("LEG", "DTO")


def _rms(vals: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else 0.0


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    samples: int = 0
    xtk_rms_nm: float = 0.0
    xtk_max_nm: float = 0.0
    cdi_rms: float = 0.0          # fraction of full scale
    cdi_max: float = 0.0
    gs_rms: float = 0.0           # fraction of full scale (ILS only)
    gs_max: float = 0.0
    gs_samples: int = 0
    tke_rms_deg: float = 0.0
    pegged: int = 0              # loops with the CDI needle at the stop
    alt_rms_ft: float = 0.0     # deviation from the autopilot's selected altitude
    alt_samples: int = 0
    score: int = 0
    grade: str = "-"

    @property
    def scored(self) -> bool:
        return self.samples > 0

    def lines(self) -> list[str]:
        if not self.scored:
            return ["SCORE:  no guided flying to grade"]
        out = [
            f"SCORE:  {self.score}/100   grade {self.grade}   ({self.samples} samples)",
            f"  lateral   xtk rms {self.xtk_rms_nm:5.2f} nm  max {self.xtk_max_nm:5.2f} nm"
            f"   CDI rms {self.cdi_rms*100:3.0f}%  max {self.cdi_max*100:3.0f}%",
            f"  course    TKE rms {self.tke_rms_deg:4.1f} deg   needle pegged {self.pegged}x",
        ]
        if self.gs_samples:
            out.append(f"  glideslope rms {self.gs_rms*100:3.0f}%  max {self.gs_max*100:3.0f}%"
                       f"   ({self.gs_samples} samples)")
        if self.alt_samples:
            out.append(f"  altitude  rms {self.alt_rms_ft:5.0f} ft vs the selected altitude")
        return out


@dataclass
class ScoreTracker:
    _xtk: list[float] = field(default_factory=list)
    _cdi: list[float] = field(default_factory=list)
    _gs: list[float] = field(default_factory=list)
    _tke: list[float] = field(default_factory=list)
    _alt: list[float] = field(default_factory=list)
    _pegged: int = 0

    def reset(self) -> None:
        self.__init__()

    def sample(self, nav, own, panel, *, nav_head=None, alt_target: float | None = None) -> None:
        """One loop's worth of data. ``nav`` is a ``gpsnav.NavState``, ``own`` a
        ``sim_model.Ownship`` (duck-typed), ``panel`` an ``instruments.Panel``."""
        if nav is None or getattr(nav, "mode", "NOWPT") not in _SCORED_MODES:
            return
        if not getattr(nav, "valid", False):
            return

        xtk = getattr(nav, "xtk_nm", None)
        if xtk is not None:
            self._xtk.append(abs(xtk))
        tke = getattr(nav, "tke", None)
        if tke is not None:
            self._tke.append(abs(tke))

        cdi = getattr(panel, "cdi", None)
        if cdi is not None and getattr(cdi, "valid", False):
            d = abs(getattr(cdi, "deflection", 0.0))
            self._cdi.append(d)
            if d >= _PEG:
                self._pegged += 1

        nh = nav_head
        if nh is not None and getattr(nh, "gs_valid", False):
            self._gs.append(abs(getattr(nh, "gs_deflection", 0.0)))

        if alt_target is not None:
            self._alt.append(getattr(own, "altitude_ft", 0.0) - alt_target)

    def summary(self) -> ScoreSummary:
        n = len(self._xtk) or len(self._cdi)
        if n == 0:
            return ScoreSummary()

        cdi_rms = _rms(self._cdi)
        gs_rms = _rms(self._gs)
        parts = [max(0.0, 1.0 - cdi_rms / _CDI_TOL)]
        if self._gs:
            parts.append(max(0.0, 1.0 - gs_rms / _GS_TOL))
        peg_pen = min(0.30, (self._pegged / n) * 3.0)
        score = int(round(100.0 * (sum(parts) / len(parts)) - 100.0 * peg_pen))
        score = max(0, min(100, score))
        grade = ("A" if score >= 90 else "B" if score >= 80 else
                 "C" if score >= 70 else "D" if score >= 60 else "F")

        return ScoreSummary(
            samples=n,
            xtk_rms_nm=_rms(self._xtk), xtk_max_nm=max(self._xtk, default=0.0),
            cdi_rms=cdi_rms, cdi_max=max(self._cdi, default=0.0),
            gs_rms=gs_rms, gs_max=max(self._gs, default=0.0), gs_samples=len(self._gs),
            tke_rms_deg=_rms(self._tke),
            pegged=self._pegged,
            alt_rms_ft=_rms(self._alt), alt_samples=len(self._alt),
            score=score, grade=grade,
        )
