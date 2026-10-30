"""The wall watches continuously, per feed, and escalates through the real path.

These tests pin the things the old one-shot wall got wrong: a single frame can
never alert, beds count as support, one feed's alert never touches another
feed's samples, recovery is observed, and a sustained fall creates the same
incident and check-in an edge would create.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from starlette.datastructures import UploadFile  # noqa: E402

from app import api, db, policy, wall  # noqa: E402


def _det(posture: str, support: str | None = None, count: int = 1) -> dict:
    return {
        "width": 640, "height": 480, "person_count": count,
        "persons": [{"box": [0.2, 0.2, 0.6, 0.9], "conf": 0.9, "posture": posture,
                     "support_surface": support, "seen_by": "pose"}],
        "objects": [{"label": "chair", "conf": 0.7, "box": [0.0, 0.5, 0.2, 0.9]}],
        "flags": ["lying"] if posture == "lying" else [], "status": "person",
    }


def test_sustained_unsupported_lying_triggers_once_and_then_recovers(monkeypatch):
    wall.close("state-test")
    state = wall.FeedState({"id": "f1", "name": "Bedroom", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))

    first = state.process(None, 1000.0)
    assert first["alert"] is None and first["trigger"] is None

    second = state.process(None, 1000.7)
    assert second["trigger"] is None  # one lying sample is not enough
    third = state.process(None, 1001.4)
    assert third["trigger"] is not None
    assert third["trigger"]["kind"] == "possible_fall"
    assert third["alert"]["active"] is True
    assert third["alert"]["feed_id"] == "f1"
    assert third["alert"]["feed_name"] == "Bedroom"
    assert third["alert"]["evidence"]["lying_for_s"] >= 1.0

    # A persisting condition must not re-create the alert on every frame.
    fourth = state.process(None, 1002.1)
    assert fourth["trigger"] is None
    assert fourth["alert"]["id"] == third["alert"]["id"]

    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 1003.0)
    state.process(None, 1004.5)
    recovered = state.process(None, 1006.0)
    assert recovered["resolved"] is not None
    assert recovered["resolved"]["recovered"] is True
    assert recovered["alert"] is None


def test_lying_on_a_bed_never_alerts(monkeypatch):
    state = wall.FeedState({"id": "f2", "name": "Bed", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array",
                        lambda frame, **_: _det("lying", support="bed"))
    for i in range(6):
        result = state.process(None, 2000.0 + i * 0.7)
        assert result["trigger"] is None
        assert result["alert"] is None
        assert "lying" in result["flags"]


def test_one_feed_alerts_without_interrupting_another(monkeypatch):
    wall.close("independence-test")
    monkeypatch.setattr(wall, "decode", lambda data: data)
    monkeypatch.setattr(wall.scene, "analyze_frame_array",
                        lambda frame, **_: _det("lying") if frame == b"fall" else _det("upright"))
    now = 3000.0
    a1 = wall.process("independence-test", {"id": "a", "name": "Fall"}, b"fall", now)
    b1 = wall.process("independence-test", {"id": "b", "name": "Quiet"}, b"up", now)
    a2 = wall.process("independence-test", {"id": "a", "name": "Fall"}, b"fall", now + 1.5)
    b2 = wall.process("independence-test", {"id": "b", "name": "Quiet"}, b"up", now + 1.5)
    assert a2["trigger"] is not None
    assert b1["alert"] is None and b2["alert"] is None
    assert a2["tracking"]["samples"] == 2
    assert b2["tracking"]["samples"] == 2
    assert a2["trigger"]["feed_id"] == "a"
    wall.close("independence-test")


def test_the_evidence_buffer_keeps_only_recent_frames(monkeypatch):
    state = wall.FeedState({"id": "f1", "name": "Bedroom", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    for i in range(5):
        state.process(None, 1000.0 + i, raw=f"frame-{i}".encode())
    frames = state.evidence_frames(seconds=10)
    assert [raw for _, raw in frames] == [f"frame-{i}".encode() for i in range(5)]
    assert [raw for _, raw in state.evidence_frames(seconds=1.5)] == [
        b"frame-3", b"frame-4"]


def test_alert_frames_become_a_clip_and_a_still(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path)
    frames = []
    for i in range(4):
        img = np.full((120, 160, 3), (40 + i * 20, 90, 90), dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", img)
        assert ok
        frames.append((1000.0 + i * 0.5, buf.tobytes()))
    out = api._build_wall_clip("inc_clip_test", frames)
    assert out is not None
    clip, still, thumb = out
    assert clip.exists() and clip.stat().st_size > 1024
    assert still.exists() and still.stat().st_size > 0
    assert thumb.exists() and thumb.stat().st_size > 0
    assert clip.parent.name == "inc_clip_test"


def test_a_quiet_frame_endpoint_never_escalates(monkeypatch):
    quiet = {
        "feed_id": "f1", "feed_name": "Quiet", "at": "2026-10-29T20:00:00+00:00",
        "width": 640, "height": 480, "person_count": 1, "persons": [],
        "objects": [], "flags": [], "status": "person",
        "tracking": {"samples": 5, "person_samples": 5, "fps": 2.0,
                     "lying_for_s": 0, "alert_active": False, "recovered": False},
        "alert": None, "resolved": None, "trigger": None,
    }
    monkeypatch.setattr(api.wall, "process", lambda *args: dict(quiet))
    called = []
    async def fail_raise(packet):
        called.append(packet)
        return {"id": "never"}
    monkeypatch.setattr(api, "_create_wall_incident", fail_raise)

    frames = [UploadFile(file=io.BytesIO(b"frame"), filename="f.jpg")]
    result = asyncio.run(api.wall_frame(
        session="quiet-test", feed=json.dumps({"id": "f1", "name": "Quiet"}),
        frame=frames[0],
    ))
    assert result["alert"] is None
    assert called == []


def test_wall_fall_is_held_then_released_after_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "wall.db"))
    db.init()
    started = []
    scheduled = []

    async def fake_phase_one(incident_id):
        started.append(("phase_one", incident_id))

    async def fake_checkin(incident_id):
        started.append(("checkin", incident_id))

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    original_create_task = api.asyncio.create_task

    def capture_task(coro):
        scheduled.append(coro)
        return original_create_task(coro)

    monkeypatch.setattr(api.asyncio, "create_task", capture_task)

    alert = {
        "id": "wa_test", "kind": "possible_fall", "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 3,
                     "person_count": 1, "lying_for_s": 1.6,
                     "objects": [{"label": "chair", "conf": 0.7}]},
    }
    packet = api._wall_incident_packet("s1", {"id": "f1", "name": "Bedroom"}, alert)

    async def run():
        incident_id = await api._create_wall_incident(packet, None)
        held = db.get_incident(incident_id)
        assert held["status"] == "verifying"
        assert not db.notifications_for(incident_id)
        assert started == []
        await api._release_wall_incident(incident_id, "sam_muse_verified")
        await asyncio.sleep(0.01)
        return incident_id

    incident_id = asyncio.run(run())
    assert {name for name, _ in started} == {"phase_one", "checkin"}
    inc = db.get_incident(incident_id)
    assert inc["status"] == "detected"
    assert inc["type"] == "fall_suspected"
    assert inc["source"] == "wall"
    assert inc["sensors"]["verification"] == "sam_muse_verified"
    assert inc["sensors"]["wall_feed"]["feed_name"] == "Bedroom"
    assert inc["severity"] in ("urgent", "emergency")


def test_a_rejected_wall_fall_sends_no_family_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "wall2.db"))
    db.init()
    started = []

    async def fake_phase_one(incident_id):
        started.append("phase_one")

    async def fake_checkin(incident_id):
        started.append("checkin")

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    alert = {
        "id": "wa_rejected", "kind": "possible_fall", "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 1,
                     "person_count": 1, "lying_for_s": 1.4, "objects": []},
    }
    packet = api._wall_incident_packet("s2", {"id": "f2", "name": "Hall"}, alert)

    async def run():
        incident_id = await api._create_wall_incident(packet, None)
        await asyncio.sleep(0)
        assert not db.notifications_for(incident_id)
        await api._reject_wall_incident(incident_id, "SAM saw no person")
        return incident_id

    incident_id = asyncio.run(run())
    assert started == []
    inc = db.get_incident(incident_id)
    assert inc["status"] == "resolved"


def test_a_verified_agent_decision_is_the_single_family_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "wall3.db"))
    db.init()
    started = []
    scheduled = []
    dispatched = []

    async def fake_phase_one(incident_id):
        started.append("phase_one")

    async def fake_checkin(incident_id):
        started.append("checkin")

    async def fake_dispatch(alert, phase, broadcast=None):
        dispatched.append((phase, alert["title"]))
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    original_create_task = api.asyncio.create_task

    def capture_task(coro):
        scheduled.append(coro)
        return original_create_task(coro)

    monkeypatch.setattr(api.asyncio, "create_task", capture_task)
    alert = {
        "id": "wa_verified", "kind": "possible_fall", "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 2,
                     "person_count": 1, "lying_for_s": 1.5, "objects": []},
    }
    packet = api._wall_incident_packet("s3", {"id": "f3", "name": "Bedroom"}, alert)
    decision = policy.Decision(
        severity="urgent", notify_now=True, title="Possible fall",
        body="A person is lying in the bedroom and has not stood up.",
        evidence=["lying"], actions=[{"id": "call_resident", "label": "Call now"}],
        confidence=0.8, follow_up_s=60, escalate_if_no_ack=True,
        rationale="SAM tracked one person lying with no support.",
    )

    async def run():
        incident_id = await api._create_wall_incident(packet, None)
        await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=decision,
            trace=[{"step": "tool", "tool": "household_memory", "summary": "2 facts"}],
            violations=[], latency=1200,
        )
        await asyncio.sleep(0.01)
        return incident_id

    incident_id = asyncio.run(run())
    assert started == ["checkin"]
    assert dispatched and dispatched[0][0] == 2
    assert dispatched[0][1] == "Margaret may have fallen"
    inc = db.get_incident(incident_id)
    assert inc["status"] == "notified"
    assert inc["decision"]["title"] == "Margaret may have fallen"
    assert inc["agent"][0]["tool"] == "household_memory"
