"""Identity continuity, inference reuse and durable observed decision steps."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from app import api, db, scene, wall
from app.agent import EvidenceAgent, PLAN_PROMPT, FOLLOWUP_PROMPT
from app.meta import SamObject, track_features


def person(box, posture="upright"):
    return {"box": box, "conf": .9, "posture": posture, "support_surface": None}


def detection(people):
    return {"width": 640, "height": 480, "person_count": len(people),
            "persons": copy.deepcopy(people), "objects": [], "flags": [], "status": "person"}


def test_a_fall_keeps_its_track_when_the_centre_drops_sharply():
    state = wall.FeedState({"id": "continuity"})
    upright = [person([.42, .10, .56, .95])]
    state._assign_tracks(upright, 1000)
    lying = [person([.17, .73, .87, .95], "lying")]
    state._assign_tracks(lying, 1000.4)
    assert lying[0]["track_id"] == upright[0]["track_id"]
    assert lying[0]["display_box"] == lying[0]["box"], "fast motion must not be smoothed away"
    lying_again = [person([.17, .73, .87, .95], "lying")]
    state._assign_tracks(lying_again, 1001.2)
    assert lying_again[0]["track_id"] == upright[0]["track_id"], "shape change must not predict the person off-screen"


def test_a_new_person_cannot_inherit_another_persons_down_timer(monkeypatch):
    state = wall.FeedState({"id": "two-people"})
    seen = [person([.03, .65, .38, .94], "lying")]
    monkeypatch.setattr(scene, "analyze_frame_array", lambda *a, **kw: detection(seen))
    state.process(None, 1000)
    state.process(None, 1000.7)
    seen[:] = [person([.66, .65, .99, .94], "lying")]
    result = state.process(None, 1001.1)
    assert result["trigger"] is None
    assert result["tracking"]["lying_for_s"] == 0


def test_global_assignment_preserves_both_tracks_at_an_ambiguous_crossing(monkeypatch):
    state = wall.FeedState({"id": "crossing"})
    old = [person([.1, .1, .3, .8]), person([.4, .1, .6, .8])]
    state._assign_tracks(old, 1000)
    new = [person([.25, .1, .45, .8]), person([.2, .1, .4, .8])]
    # The strongest individual pair is new0/old0, but choosing it strands
    # new1. The best joint assignment retains the two original identities.
    scores = {(tuple(new[0]["box"]), tuple(old[0]["box"])): .9,
              (tuple(new[0]["box"]), tuple(old[1]["box"])): .8,
              (tuple(new[1]["box"]), tuple(old[0]["box"])): .8,
              (tuple(new[1]["box"]), tuple(old[1]["box"])): .1}
    monkeypatch.setattr(wall, "_iou", lambda a, b: scores.get((tuple(a), tuple(b)), 0))
    state._assign_tracks(new, 1000.5)
    assert [p["track_id"] for p in new] == [old[1]["track_id"], old[0]["track_id"]]


def test_recheck_honours_size_and_reuses_empty_pose_observation(monkeypatch):
    class Model:
        def __init__(self): self.calls = []
        def predict(self, frame, **kwargs):
            self.calls.append(kwargs)
            return []
    yolo, pose = Model(), Model()
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    scene._analyze(frame, yolo, pose, 64, 64, detect_objects=False,
                   cached_objects=None, person_imgsz=960, cached_pose=[])
    assert yolo.calls[0]["imgsz"] == 960
    assert yolo.calls[0]["classes"] == [0]
    assert not pose.calls


def test_fast_sampling_preserves_a_full_evidence_window(monkeypatch):
    state = wall.FeedState({"id": "fast"})
    monkeypatch.setattr(scene, "analyze_frame_array", lambda *a, **kw: detection([]))
    for i in range(500):
        state.process(None, 1000 + i / 50, b"jpeg")
    frames = list(state._raw_frames)
    assert len(frames) <= 80
    assert frames[-1][0] - frames[0][0] >= 7.5


def test_lost_person_retry_is_bounded_and_still_visits_all_scales(monkeypatch):
    state = wall.FeedState({"id": "bounded"})
    seen = [person([.3, .2, .5, .9])]
    sizes = []
    def analyze(*args, person_imgsz=None, **kwargs):
        if person_imgsz: sizes.append(person_imgsz)
        return detection(seen)
    monkeypatch.setattr(scene, "analyze_frame_array", analyze)
    state.process(None, 1000)
    seen.clear()
    for at in [1000.2, 1000.4, 1000.6]:
        result = state.process(None, at)
        assert result["tracking"]["rechecks"] == 1
    assert tuple(sizes) == wall.LOST_RECHECK_IMGSZ


def test_sam_geometry_follows_the_candidate_not_the_largest_bystander():
    objects = []
    for i in range(8):
        objects.append(SamObject(i, 1, 0, .1, .55, .95, 1, 1, .95))
        objects.append(SamObject(i, 2, .65, .4 + i * .04, .95, .95, 1, 1, .85))
    result = track_features(objects, [.65, .68, .95, .95])
    assert result["primary_track_id"] == 2
    assert result["vertical_drop"] > .1
    assert len(result["trajectory"]) == 8


def test_independent_tools_run_concurrently_and_publish_started_steps():
    class Meta:
        def decide(self, packet, system, schema, effort="low"):
            return ({"calls": [{"tool": "household_memory"}, {"tool": "recent_events"}]}
                    if system == PLAN_PROMPT else {"verdict": "reject", "rationale": "No fall evidence."},
                    SimpleNamespace(latency_ms=1))

    async def scenario():
        entered = []
        both_started = asyncio.Event()
        progress = []

        async def tool(question):
            entered.append(question)
            if len(entered) == 2: both_started.set()
            await asyncio.wait_for(both_started.wait(), .5)
            return {"facts": ["Known household context"]}

        async def on_step(step): progress.append(step)
        result = await EvidenceAgent(Meta()).run(packet={}, tools={"household_memory": tool, "recent_events": tool},
            verify=True, floor="urgent", on_step=on_step)
        assert all(step.get("ok") for step in result.trace if step["step"] == "tool")
        starts = [step for step in progress if step["step"] == "tool" and step["status"] == "running"]
        assert len(starts) == 2
        assert all("latency_ms" in step for step in result.trace if step["step"] == "tool")
    asyncio.run(scenario())


def test_clear_visual_evidence_skips_a_redundant_followup_plan():
    class Meta:
        def __init__(self): self.calls = []
        def decide(self, packet, system, schema, effort="low"):
            self.calls.append(system)
            return ({"calls": [{"tool": "inspect_frame", "question": "Floor or bed?"}]}
                    if system == PLAN_PROMPT else {"verdict": "escalate"}, SimpleNamespace(latency_ms=1))
    client = Meta()
    async def inspect(question):
        return {"available": True, "posture": "lying", "support_surface": "floor", "confidence": .9}
    asyncio.run(EvidenceAgent(client).run(packet={}, tools={"inspect_frame": inspect}, verify=True, floor="urgent"))
    assert len(client.calls) == 2
    assert FOLLOWUP_PROMPT not in client.calls


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "pipeline.db"))
    db.init()
    db.upsert_incident({"incident_id": "inc_steps", "household_id": "h", "camera_id": "cam_wall",
        "type": "fall_suspected", "status": "verifying", "started_at": db.now(), "severity_hint": "urgent",
        "sensors": {"wall_feed": {"feed_id": "camera_02", "feed_name": "Hallway"}}})


def test_pipeline_is_durable_and_attributed_to_the_original_camera(database):
    asyncio.run(api.pipeline_step("inc_steps", "muse", "running", "Inspecting a protected still", "Floor or bed?"))
    db.init()
    rows = db.pipeline_steps(feed_id="camera_02")
    assert len(rows) == 1 and rows[0]["feed_name"] == "Hallway"
    assert not db.pipeline_steps(feed_id="another-camera")
    assert api.events(limit=20)["items"] == [], "installer evidence must not release a family incident"


def test_checkin_and_delivery_steps_appear_in_the_same_feed_log(database):
    asyncio.run(api.broadcast({"type": "checkin.started", "id": "inc_steps", "prompt": "Are you okay?", "due_at": 5000}))
    asyncio.run(api.broadcast({"type": "incident.notified", "id": "inc_steps", "channels": ["inapp"], "phase": 2}))
    rows = db.pipeline_steps(feed_id="camera_02")
    assert [r["stage"] for r in rows] == ["checkin", "delivery"]
    assert rows[0]["metrics"]["resident_url"].startswith("/checkin/inc_steps")
    assert rows[1]["metrics"]["channels"] == ["inapp"]


def test_pipeline_follows_metadata_retention(database):
    db.add_pipeline_step("inc_steps", "local", "done", "Observed")
    with db.connect() as conn:
        conn.execute("UPDATE incidents SET created_at='2020-01-01' WHERE id='inc_steps'")
        conn.commit()
    db.purge_metadata(30)
    assert db.pipeline_steps() == []
