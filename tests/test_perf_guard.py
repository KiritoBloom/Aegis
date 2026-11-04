"""Performance scheduling must not change what the wall decides.

The wall is a safety path, not a video renderer. These tests pin the two
optimizations that are easy to get subtly wrong:

- an empty object cache is *not* treated as a complete scene: a bed can appear
  later, so object-free feeds still run the all-class pass;
- an exactly repeated frame can reuse its previous analysis without changing
  tracking, sustained-lying timing, recovery, or the candidate itself.

The candidate sequence is run twice, with the pre-optimization object-cache
rule and with the packaged rule, and the decisions must be identical.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import scene, wall  # noqa: E402


def _frame(seed: int, size: tuple[int, int] = (48, 64)) -> np.ndarray:
    """A small, unique BGR frame. Different seeds are different pixels."""
    frame = np.full((*size, 3), 60, dtype=np.uint8)
    frame[seed % size[0], (seed * 7) % size[1]] = 255
    return frame


def _det(posture: str, support: str | None = None, person: bool = True) -> dict:
    # A lying body is wider than tall; anything else in this fixture is a
    # standing body. The wall rejects a "lying" verdict that still looks upright.
    box = ([0.32, 0.65, 0.68, 0.95] if posture == "lying"
           else [0.40, 0.20, 0.60, 0.95])
    persons = ([{"box": box, "conf": 0.9,
                 "posture": posture, "support_surface": support,
                 "seen_by": "pose+yolo"}] if person else [])
    return {
        "width": 64, "height": 48, "person_count": int(person),
        "persons": persons,
        "objects": [],
        "flags": (["lying"] if posture == "lying" else []),
        "status": "person" if person else "empty",
    }


def _decision(result: dict) -> dict:
    """The safety-relevant part of a wall result, without random alert ids."""
    trigger = result.get("trigger")
    resolved = result.get("resolved")
    alert = result.get("alert")
    return {
        "person_count": result["person_count"],
        "persons": [(p.get("track_id"), p.get("primary"), p.get("posture"))
                    for p in result["persons"]],
        "tracking": {k: result["tracking"][k] for k in
                     ("samples", "person_samples", "lying_for_s",
                      "alert_active", "recovered")},
        "trigger": None if trigger is None else {
            "kind": trigger["kind"], "since": trigger["since"],
            "summary": trigger["summary"],
            "evidence": trigger["evidence"],
        },
        "alert": None if alert is None else {
            "kind": alert["kind"], "active": alert["active"],
            "summary": alert["summary"],
        },
        "resolved": None if resolved is None else {
            "kind": resolved["kind"], "recovered": resolved["recovered"],
        },
    }


def test_an_empty_object_cache_still_checks_for_furniture_every_sample(monkeypatch):
    state = wall.FeedState({"id": "perf-empty", "name": "Hall"})
    flags: list[bool] = []

    def fake(frame, **kwargs):
        flags.append(bool(kwargs.get("detect_objects")))
        return _det("upright")

    monkeypatch.setattr(wall.scene, "analyze_frame_array", fake)
    for i in range(4):
        state.process(_frame(i), 1000.0 + i * 0.5)
    # A bed can enter an empty-looking hallway. Never skip the all-class pass
    # merely because the last cache was empty.
    assert flags == [True, True, True, True]


def test_identical_frame_reuses_the_cached_analysis(monkeypatch):
    state = wall.FeedState({"id": "perf-static", "name": "Bedroom"})
    calls = []

    def fake(frame, **kwargs):
        calls.append(1)
        return _det("lying")

    monkeypatch.setattr(wall.scene, "analyze_frame_array", fake)
    frame = _frame(3)
    first = state.process(frame, 2000.0)
    second = state.process(frame.copy(), 2000.5)
    third = state.process(frame.copy(), 2001.0)

    assert len(calls) == 1, "an identical frame must not re-run the models"
    assert first["alert"] is None
    assert second["tracking"]["lying_for_s"] == 0.5
    assert third["trigger"] is not None
    assert third["trigger"]["kind"] == "possible_fall"

    # The same sequence with the cache disabled must reach the same decision,
    # including the same trigger sample and the same tracker state.
    monkeypatch.setattr(wall, "REUSE_IDENTICAL_FRAMES", False)
    fresh = wall.FeedState({"id": "perf-static-fresh", "name": "Bedroom"})
    plain = [fresh.process(frame.copy(), t) for t in (2000.0, 2000.5, 2001.0)]
    assert len(calls) == 4
    assert [_decision(r) for r in plain] == [_decision(r) for r in
                                             (first, second, third)]


def test_warmup_runs_a_real_prediction_through_both_models(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.calls = []

        def predict(self, frame, **kwargs):
            self.calls.append({"shape": tuple(frame.shape), **kwargs})
            return []

    yolo, pose = FakeModel(), FakeModel()
    monkeypatch.setattr(scene, "_models", lambda: (yolo, pose))
    wall.warmup()
    assert len(yolo.calls) == 1 and yolo.calls[0]["imgsz"] == 416
    assert len(pose.calls) == 1 and pose.calls[0]["imgsz"] == 448
    assert yolo.calls[0]["shape"] == pose.calls[0]["shape"] == (64, 64, 3)
