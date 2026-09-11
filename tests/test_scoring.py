"""Session-scoring accumulator + grade thresholds."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scoring import ScoreTracker  # noqa: E402


class _Nav:
    def __init__(self, xtk, tke=0.0, mode="LEG", valid=True):
        self.xtk_nm = xtk
        self.tke = tke
        self.mode = mode
        self.valid = valid


class _Cdi:
    def __init__(self, defl, valid=True):
        self.deflection = defl
        self.valid = valid


class _Panel:
    def __init__(self, defl, valid=True):
        self.cdi = _Cdi(defl, valid)


class _Own:
    def __init__(self, alt=5000.0):
        self.altitude_ft = alt


class _Head:
    def __init__(self, gs, valid=True):
        self.gs_deflection = gs
        self.gs_valid = valid


def test_perfect_tracking_is_an_A():
    t = ScoreTracker()
    for _ in range(200):
        t.sample(_Nav(0.0), _Own(), _Panel(0.0))
    s = t.summary()
    assert s.scored and s.grade == "A" and s.score == 100
    assert s.xtk_rms_nm == 0.0 and s.pegged == 0


def test_sloppy_tracking_scores_low():
    t = ScoreTracker()
    for _ in range(200):
        t.sample(_Nav(1.5, tke=12.0), _Own(), _Panel(0.9))    # 90% of full scale
    s = t.summary()
    assert s.cdi_rms > 0.75 and s.score < 40 and s.grade in ("D", "F")
    assert s.tke_rms_deg > 10.0


def test_pegged_needle_is_penalised():
    t = ScoreTracker()
    for _ in range(100):
        t.sample(_Nav(0.1), _Own(), _Panel(0.2))              # otherwise fine
    for _ in range(100):
        t.sample(_Nav(3.0), _Own(), _Panel(1.0))              # pegged
    s = t.summary()
    assert s.pegged == 100 and s.score < 90


def test_only_leg_and_dto_modes_are_scored():
    t = ScoreTracker()
    for m in ("OBS", "SUSP", "NOWPT"):
        t.sample(_Nav(2.0, mode=m), _Own(), _Panel(1.0))
    t.sample(_Nav(2.0, mode="NOWPT", valid=False), _Own(), _Panel(1.0))
    assert not t.summary().scored
    t.sample(_Nav(0.0, mode="DTO"), _Own(), _Panel(0.0))
    assert t.summary().scored


def test_glideslope_and_altitude_tracked_when_available():
    t = ScoreTracker()
    for _ in range(50):
        t.sample(_Nav(0.0), _Own(alt=5040.0), _Panel(0.0),
                 nav_head=_Head(0.1), alt_target=5000.0)
    s = t.summary()
    assert s.gs_samples == 50 and 0.05 < s.gs_rms < 0.15
    assert s.alt_samples == 50 and 35 < s.alt_rms_ft < 45
    assert "glideslope" in " ".join(s.lines())


def test_no_samples_summary_is_safe():
    s = ScoreTracker().summary()
    assert not s.scored and s.grade == "-" and s.lines() == ["SCORE:  no guided flying to grade"]
