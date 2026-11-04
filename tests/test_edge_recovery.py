"""The always-on edge must report recovery, not just the browser wall."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "edge"))

from aegis_edge.runner import EdgeRunner  # noqa: E402
from aegis_edge.state import RulesEngine  # noqa: E402
from app import api, db  # noqa: E402


def test_edge_recovery_is_reported_once_and_clears_the_fall_cooldown():
    posted = []
    cleared = []

    runner = SimpleNamespace(
        last_fall_incident="inc_edge", fall_up_since=None,
        fall_recovery_attempts=0, posture="upright",
        cfg=SimpleNamespace(fall=SimpleNamespace(no_recovery_s=8)),
        uploader=SimpleNamespace(post_recovery=lambda iid, posture: posted.append((iid, posture)) or True),
        rules=SimpleNamespace(clear_fall_cooldown=lambda: cleared.append(True)),
        _workers=[],
    )
    check = types.MethodType(EdgeRunner._check_recovery, runner)
    runner._report_recovery = types.MethodType(EdgeRunner._report_recovery, runner)

    check(10.0, True)
    assert posted == []  # first upright sample starts the timer
    check(11.0, True)
    assert posted == []
    check(12.0, True)
    for _ in range(50):
        if posted:
            break
        import time as _time
        _time.sleep(0.01)
    assert posted == [("inc_edge", "upright")]
    assert cleared == [True]
    assert runner.last_fall_incident is None


def test_edge_recovery_ignores_an_absent_or_lying_resident():
    posted = []
    runner = SimpleNamespace(
        last_fall_incident="inc_edge", fall_up_since=10.0,
        fall_recovery_attempts=0, posture="lying",
        cfg=SimpleNamespace(fall=SimpleNamespace(no_recovery_s=8)),
        uploader=SimpleNamespace(post_recovery=lambda *a: posted.append(a) or True),
        rules=SimpleNamespace(clear_fall_cooldown=lambda: None),
    )
    check = types.MethodType(EdgeRunner._check_recovery, runner)
    check(12.0, True)
    check(12.0, False)
    assert posted == []
    assert runner.fall_up_since is None


def test_clear_fall_cooldown_removes_only_fall_keys():
    cfg = SimpleNamespace(fall=SimpleNamespace(enabled=True), zones=[])
    rules = RulesEngine(SimpleNamespace())
    rules._last_emit = {"fall_suspected:hallway": 10.0, "fall_confirmed:hallway": 12.0,
                        "door_open:front_door": 12.0}
    rules._lying_since = 9.0
    rules.clear_fall_cooldown()
    assert rules._last_emit == {"door_open:front_door": 12.0}
    assert rules._lying_since is None


def test_edge_recovery_endpoint_closes_the_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "edge-recovery.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_edge", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_confirmed", "status": "notified", "severity_hint": "emergency",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": "hallway", "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "edge",
    })
    out = api.apply_resident_recovery("inc_edge", "edge")
    assert out["changed"] is True
    stored = db.get_incident("inc_edge")
    assert stored["status"] == "resolved"
    assert stored["checkin_status"] in (None, "cancelled")
    assert any(e["type"] == "resident_recovered" for e in db.recent_events(5))


def test_stream_end_flushes_a_pending_clip_and_waits_for_the_upload():
    calls = []
    runner = SimpleNamespace(
        pending={"incident": {"incident_id": "inc_flush"}, "t": 5.0},
        ring=SimpleNamespace(window=lambda a, b: ["frame-a", "frame-b", "frame-c"]),
        cfg=SimpleNamespace(clip=SimpleNamespace(pre_s=6.0, post_s=4.0)),
        gate=SimpleNamespace(captured=lambda t: calls.append(("captured", t))),
        _workers=[],
        _render_and_upload=lambda pending, frames: calls.append(
            ("upload", pending["incident"]["incident_id"], len(frames))),
    )
    finish = types.MethodType(EdgeRunner._finish_pending, runner)
    finish(8.0, wait=True)
    assert runner.pending is None
    assert ("captured", 8.0) in calls
    assert ("upload", "inc_flush", 3) in calls
    assert runner._workers and not runner._workers[0].is_alive()
