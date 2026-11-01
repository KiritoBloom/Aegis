"""The wall watches continuously, per feed, and escalates through the real path.

These tests pin the things the old one-shot wall got wrong: an ambiguous single
frame cannot alert, a high-confidence fall transition is only a candidate, beds count as support, one feed's alert never touches another
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


def test_a_second_fall_after_recovery_is_not_suppressed_by_the_cooldown(monkeypatch):
    state = wall.FeedState({"id": "f-twice", "name": "Hall", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 5000.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    first = state.process(None, 5000.4)
    assert first["trigger"] is not None
    first_id = first["trigger"]["id"]

    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 5001.0)
    state.process(None, 5002.5)
    resolved = state.process(None, 5004.0)
    assert resolved["resolved"] is not None

    state.process(None, 5004.4)  # upright again, then a new fall
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    second = state.process(None, 5005.0)
    assert second["trigger"] is not None
    assert second["trigger"]["id"] != first_id


def test_late_verification_for_a_recovered_alert_does_not_arm_the_cooldown(monkeypatch):
    state = wall.FeedState({"id": "f-late-verdict", "name": "Hall", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 8000.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    triggered = state.process(None, 8000.4)
    assert triggered["trigger"] is not None
    alert_id = triggered["trigger"]["id"]

    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 8001.0)
    state.process(None, 8002.5)
    assert state.process(None, 8004.0)["resolved"] is not None

    # SAM/Muse finish after the resident is already back up. The API tells the
    # wall not to arm a cooldown for a verdict that no longer pages anyone.
    state.set_verification(alert_id, status="rejected", escalated=False,
                           arm_cooldown=False)
    state.set_verification(alert_id, status="verified", escalated=False,
                           arm_cooldown=False)
    state.process(None, 8004.4)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    second = state.process(None, 8005.0)
    assert second["trigger"] is not None


def test_absence_alone_does_not_resolve_a_fall(monkeypatch):
    state = wall.FeedState({"id": "f-gone", "name": "Hall", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    state.process(None, 6000.0)
    state.process(None, 6000.6)
    triggered = state.process(None, 6001.2)
    assert triggered["trigger"] is not None

    empty = {"width": 640, "height": 480, "person_count": 0, "persons": [],
             "objects": [], "flags": [], "status": "empty"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: empty)
    for ts in (6001.6, 6003.0, 6005.0, 6008.0):
        result = state.process(None, ts)
        assert result["resolved"] is None
        assert result["alert"] is not None


def test_a_fast_transition_to_floor_is_a_candidate(monkeypatch):
    state = wall.FeedState({"id": "f-fast", "name": "Hall", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("upright"))
    state.process(None, 3000.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    first = state.process(None, 3000.4)
    assert first["trigger"] is not None
    assert first["trigger"]["kind"] == "possible_fall"


def test_two_people_keep_their_track_ids_while_walking(monkeypatch):
    state = wall.FeedState({"id": "f-two", "name": "Living room", "kind": "file"})

    def two(a, b):
        return {
            "width": 640, "height": 480, "person_count": 2,
            "persons": [
                {"box": a, "conf": 0.9, "posture": "upright", "support_surface": None,
                 "seen_by": "pose+yolo"},
                {"box": b, "conf": 0.85, "posture": "upright", "support_surface": None,
                 "seen_by": "pose+yolo"},
            ],
            "objects": [], "flags": [], "status": "multiple_people",
        }

    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: two(
        [0.10, 0.20, 0.25, 0.90], [0.70, 0.20, 0.85, 0.90]))
    first = state.process(None, 4000.0)
    ids = {p["box"][0]: p["track_id"] for p in first["persons"]}
    assert len(set(ids.values())) == 2
    assert sum(1 for p in first["persons"] if p["primary"]) == 1

    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: two(
        [0.14, 0.20, 0.29, 0.90], [0.66, 0.20, 0.81, 0.90]))
    second = state.process(None, 4000.5)
    moved = {p["box"][0]: p["track_id"] for p in second["persons"]}
    assert moved[0.14] == ids[0.10]
    assert moved[0.66] == ids[0.70]


def test_appearance_keeps_ids_when_boxes_overlap():
    state = wall.FeedState({"id": "f-cross", "name": "Hall", "kind": "file"})
    first = [{"box": [0.10, 0.20, 0.30, 0.90]},
             {"box": [0.70, 0.20, 0.90, 0.90]}]
    state._assign_tracks(first, 7000.0)
    left_id, right_id = first[0]["track_id"], first[1]["track_id"]
    state.tracks[left_id].desc = np.array([1.0, 0.0], dtype=np.float32)
    state.tracks[right_id].desc = np.array([0.0, 1.0], dtype=np.float32)
    # Both people walk toward the middle and meet at the same point.
    state.tracks[left_id].vx = 0.75
    state.tracks[right_id].vx = -0.75

    # Both boxes occupy the same space on the crossing frame. Only clothing
    # appearance can keep the identities apart.
    crossed = [{"box": [0.40, 0.20, 0.60, 0.90]},
               {"box": [0.40, 0.20, 0.60, 0.90]}]
    state._assign_tracks(crossed, 7000.4, [
        np.array([1.0, 0.0], dtype=np.float32),
        np.array([0.0, 1.0], dtype=np.float32),
    ])
    assert crossed[0]["track_id"] == left_id
    assert crossed[1]["track_id"] == right_id
    assert len(state.tracks) == 2


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
        # The person can get back up while SAM/Muse are still checking. That
        # recovery must not rewrite the floor of an already-admitted fall.
        db.update_incident_sensors(incident_id, {"recovered": True, "posture": "upright"})
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
    assert inc["decision"]["policy_floor"] == "urgent"
    assert inc["agent"][0]["tool"] == "household_memory"


def test_a_bystander_standing_up_does_not_close_the_resident_fall(monkeypatch):
    state = wall.FeedState({"id": "f-bystander", "name": "Hall", "kind": "file"})
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: _det("lying"))
    state.process(None, 8000.0)
    state.process(None, 8000.6)
    triggered = state.process(None, 8001.2)
    assert triggered["trigger"] is not None

    visitor = {
        "width": 640, "height": 480, "person_count": 1,
        "persons": [{"box": [0.72, 0.20, 0.86, 0.90], "conf": 0.9,
                     "posture": "upright", "support_surface": None,
                     "seen_by": "pose"}],
        "objects": [], "flags": [], "status": "person",
    }
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: visitor)
    for ts in (8001.8, 8003.4, 8005.0, 8007.0):
        result = state.process(None, ts)
        assert result["resolved"] is None
        assert result["alert"] is not None


def _person(box, posture="upright", support=None):
    return {"box": box, "conf": 0.9, "posture": posture,
            "support_surface": support, "seen_by": "pose+yolo"}


def test_box_geometry_catches_a_fall_that_pose_calls_upright(monkeypatch):
    state = wall.FeedState({"id": "f-geom", "name": "Hall", "kind": "file"})
    upright = {"width": 640, "height": 480, "person_count": 1,
               "persons": [_person([0.40, 0.10, 0.52, 0.95])],
               "objects": [], "flags": [], "status": "person"}
    wide = {"width": 640, "height": 480, "person_count": 1,
            "persons": [_person([0.15, 0.70, 0.90, 0.98], posture="upright")],
            "objects": [], "flags": [], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array",
                        lambda frame, **_: upright)
    state.process(None, 9000.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array",
                        lambda frame, **_: wide)
    result = state.process(None, 9000.5)
    assert result["trigger"] is not None
    assert result["trigger"]["evidence"]["geometry_fall"] is True
    assert "body changed" in result["trigger"]["summary"]


def test_box_geometry_does_not_trigger_without_an_upright_transition(monkeypatch):
    state = wall.FeedState({"id": "f-geom2", "name": "Hall", "kind": "file"})
    wide = {"width": 640, "height": 480, "person_count": 1,
            "persons": [_person([0.15, 0.70, 0.90, 0.98])],
            "objects": [], "flags": [], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: wide)
    for t in (9100.0, 9101.0, 9102.0):
        result = state.process(None, t)
        assert result["trigger"] is None


def test_a_body_already_resting_on_a_bed_does_not_trigger(monkeypatch):
    state = wall.FeedState({"id": "f-geom3", "name": "Bedroom", "kind": "file"})
    on_bed = {"width": 640, "height": 480, "person_count": 1,
              "persons": [_person([0.15, 0.70, 0.90, 0.98], posture="lying",
                                  support="bed")],
              "objects": [], "flags": ["lying"], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: on_bed)
    for t in (9200.0, 9201.0, 9202.0):
        result = state.process(None, t)
        assert result["trigger"] is None


def test_a_fast_collapse_onto_a_bed_is_a_candidate_not_a_silent_support(monkeypatch):
    state = wall.FeedState({"id": "f-geom-bed", "name": "Bedroom", "kind": "file"})
    upright = {"width": 640, "height": 480, "person_count": 1,
               "persons": [_person([0.40, 0.10, 0.52, 0.95])],
               "objects": [], "flags": [], "status": "person"}
    wide = {"width": 640, "height": 480, "person_count": 1,
            "persons": [_person([0.15, 0.70, 0.90, 0.98], posture="lying",
                                support="bed")],
            "objects": [], "flags": ["lying"], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: upright)
    state.process(None, 9300.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: wide)
    result = state.process(None, 9300.4)
    assert result["trigger"] is not None
    assert result["trigger"]["evidence"]["support_surface"] == "bed"
    assert result["trigger"]["evidence"]["geometry_fall"] is True


def test_a_supported_lie_down_after_sitting_is_not_a_candidate(monkeypatch):
    state = wall.FeedState({"id": "f-bed-sit", "name": "Bedroom", "kind": "file"})
    sitting = {"width": 640, "height": 480, "person_count": 1,
               "persons": [_person([0.42, 0.45, 0.58, 0.95], posture="sitting",
                                   support="bed")],
               "objects": [], "flags": [], "status": "person"}
    lying = {"width": 640, "height": 480, "person_count": 1,
             "persons": [_person([0.15, 0.70, 0.90, 0.98], posture="lying",
                                 support="bed")],
             "objects": [], "flags": ["lying"], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: sitting)
    state.process(None, 9400.0)
    monkeypatch.setattr(wall.scene, "analyze_frame_array", lambda frame, **_: lying)
    result = state.process(None, 9401.0)
    assert result["trigger"] is None
