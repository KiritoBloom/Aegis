"""Repeated pacing is a care signal, not a fall or a diagnosis."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "edge"))

from aegis_edge import config as config_mod  # noqa: E402
from aegis_edge.geometry import TrackHistory  # noqa: E402
from aegis_edge.state import Context, RulesEngine  # noqa: E402


def _cfg():
    return config_mod.load(str(ROOT / "config" / "edge.yaml"))


def test_pacing_emits_a_restlessness_candidate_but_a_straight_walk_does_not():
    cfg = _cfg()
    engine = RulesEngine(cfg)

    pacing = TrackHistory(maxlen=120)
    for i in range(24):
        pacing.add(i * 0.5, 0.2 if i % 2 == 0 else 0.8, 0.5)

    events = engine.evaluate(Context(t=12.0, track=pacing, posture="upright",
                                     person_count=1, night=False,
                                     cognitive_condition=True))
    assert "restlessness" in [e.type for e in events]
    restless = next(e for e in events if e.type == "restlessness")
    assert restless.features["direction_changes"] >= 6

    engine2 = RulesEngine(cfg)
    straight = TrackHistory(maxlen=120)
    for i in range(24):
        straight.add(i * 0.5, 0.1 + i * 0.03, 0.5)
    assert "restlessness" not in {
        e.type for e in engine2.evaluate(Context(
            t=12.0, track=straight, posture="upright", person_count=1, night=False))
    }


def test_prayer_ritual_is_not_restlessness():
    cfg = _cfg()
    engine = RulesEngine(cfg)
    th = TrackHistory(maxlen=120)
    for i in range(24):
        th.add(i * 0.5, 0.2 if i % 2 == 0 else 0.8, 0.5)
    zone = cfg.zone("prayer_mat")
    assert zone is not None
    events = engine.evaluate(Context(t=12.0, track=th, posture="upright",
                                     person_count=1, night=False,
                                     zone=zone, prayer_cycles=4))
    assert "restlessness" not in {e.type for e in events}
