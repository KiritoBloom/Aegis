"""Exact symbolic inputs, independent responses and the demo/safety boundary."""
import asyncio
import json
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app import api, db, motion_review, wall


class Mask:
    def __init__(self, frame, obj_id, x1, y1, x2, y2, present=True):
        self.frame, self.obj_id = frame, obj_id
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.img_w, self.img_h, self.present = 64, 36, present
        self.box_norm = [x1/64, y1/36, x2/64, y2/36]
    def raster(self):
        return (2, 2, bytes([1]*4)) if self.present else None


def test_packed_bits_match_target_and_do_not_include_bystander_or_box_fallback():
    small, seen = motion_review.bitmap([Mask(0, 0, 0, 0, 32, 36), Mask(0, 1, 32, 0, 64, 36)], 0, 64, 36)
    rows = [row.tobytes().hex() for row in np.packbits(small, axis=1, bitorder="big")]
    assert seen and rows == ["ffff0000"]*18
    empty, seen = motion_review.bitmap([Mask(0, 0, 0, 0, 64, 36, False)], 0, 64, 36)
    assert not seen and not empty.any()


def test_evidence_preserves_timestamps_and_atomic_inputs(tmp_path):
    source = tmp_path/"source.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 4, (64, 36))
    rng = np.random.default_rng(4)
    for _ in range(4): writer.write(rng.integers(0, 255, (36, 64, 3), dtype=np.uint8))
    writer.release()
    bundle = motion_review.build_evidence(source, [Mask(i, 0, 0, 0, 32, 36) for i in (0, 1, 3)], 0, tmp_path)
    packet = json.loads((tmp_path/"motion_hex.txt").read_text())
    assert packet == bundle["packet"]
    assert [row["seconds"] for row in packet["observations"]] == [0, .25, .5, .75]
    assert not packet["observations"][2]["mask_observed"]
    assert packet["observations"][2]["rows_hex"] == ["00000000"]*18
    assert bundle["binary_bytes"] == 4*72 and bundle["observed_frames"] == 3
    assert bundle["image"] == (tmp_path/"motion_image.jpg").read_bytes()
    assert not list(tmp_path.glob("*.pending"))


@pytest.mark.parametrize("image_fails", [False, True])
def test_reviews_are_independent_and_never_modify_incident(monkeypatch, tmp_path, image_fails):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path/"review.db")); db.init()
    db.upsert_incident({"incident_id": "inc_compare", "household_id": "hh_demo", "camera_id": "demo",
                       "type": "fall_suspected", "status": "verifying", "severity_hint": "urgent",
                       "sensors": {}, "track": {}, "evidence": []})
    packet = {"width": 32, "height": 18, "observations": [{"frame": 1, "seconds": 0, "rows_hex": ["ffff0000"]*18}]}
    bundle = {"packet": packet, "image": b"protected-image", "frames": 1, "observed_frames": 1,
              "binary_bytes": 72, "json_bytes": 250, "image_sha256": "image-hash", "hex_sha256": "hex-hash"}
    monkeypatch.setattr(motion_review, "build_evidence", lambda *args: bundle)
    monkeypatch.setattr(api.privacy, "paused_state", lambda: {"paused": False})
    started = threading.Barrier(2)
    usage = SimpleNamespace(latency_ms=12, output_tokens=50)
    class Model:
        def vision(self, image, prompt, schema, effort, **kwargs):
            assert image == b"protected-image" and "hex" not in prompt
            started.wait(5)
            if image_fails: raise ValueError("image unavailable")
            return {"action": "controlled_lowering", "observation": "Image response"}, usage
        def decide(self, data, prompt, schema, effort):
            assert data == packet and set(data) == {"width", "height", "observations"}
            assert "previous verdict" not in json.dumps(data)
            started.wait(5)
            return {"action": "fall", "observation": "Hex response"}, usage
    monkeypatch.setattr(api, "meta", lambda: Model())
    async def no_broadcast(_): pass
    monkeypatch.setattr(api, "broadcast", no_broadcast)
    asyncio.run(api._compare_motion("inc_compare", tmp_path/"unused", [], 0, ["image", "hex"]))
    rows = db.pipeline_steps(100, incident_id="inc_compare")
    for mode in ("image", "hex"):
        matching = [row for row in rows if row["metrics"]["phase"] == f"compare_{mode}"]
        request = next(row["metrics"]["request"] for row in matching if row["metrics"].get("request"))
        assert request["image_attachments"] == int(mode == "image") and request["tools"] == 0
        assert matching[-1]["status"] == ("failed" if image_fails and mode == "image" else "done")
    incident = db.get_incident("inc_compare")
    assert incident["status"] == "verifying" and incident["decision"] is None
    assert not db.notifications_for("inc_compare")


def test_duplicate_start_shares_one_task_and_completed_reads_are_not_repeated(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "pipeline_steps", lambda *args, **kwargs: [])
    calls = []
    async def compare(*args): calls.append(args)
    monkeypatch.setattr(api, "_compare_motion", compare)
    async def scenario():
        assert api._start_motion_comparison("inc_duplicate", tmp_path, [], 0)
        assert not api._start_motion_comparison("inc_duplicate", tmp_path, [], 0)
        await api._MOTION_TASKS["inc_duplicate"]
    asyncio.run(scenario())
    assert len(calls) == 1
    monkeypatch.setattr(db, "pipeline_steps", lambda *args, **kwargs: [
        {"stage": "muse", "status": "done", "metrics": {"phase": f"compare_{mode}"}} for mode in ("image", "hex")])
    assert not api._start_motion_comparison("inc_duplicate", tmp_path, [], 0)


def test_width_padding_cannot_suppress_the_same_observed_prone_body(monkeypatch):
    for width in (640, 1280):
        # Identical 280px-wide x 160px-high body, on differently sized canvases.
        def detect(*_, **kw):
            return {"width": width, "height": 480, "persons": [{"box": [200/width, .5, 480/width, .5+160/480],
                    "conf": .9, "posture": "lying", "support_surface": None}], "person_count": 1,
                    "objects": [], "flags": [], "status": "person"}
        monkeypatch.setattr(wall.scene, "analyze_frame_array", detect)
        state = wall.FeedState({"id": str(width)})
        assert state.process(None, 1000)["trigger"] is None
        assert state.process(None, 1001.1)["trigger"] is not None


def test_overhead_full_body_is_not_confused_with_a_forward_bend():
    person = {"box": [.4, .1, .6, .95], "skeleton": [[0, 0, 0] for _ in range(17)]}
    for indices, y in (((5, 6), .8), ((11, 12), .6), ((13, 14), .4), ((15, 16), .2)):
        for index in indices: person["skeleton"][index] = [.5, y, .95]
    assert wall._inverted_body(person)
    for index in (15, 16): person["skeleton"][index][1] = .95
    assert not wall._inverted_body(person)
    for index in (15, 16): person["skeleton"][index][1] = .2
    person["skeleton"][15][2] = .2
    assert not wall._inverted_body(person)


def test_corroborated_lying_track_can_turn_but_new_narrow_pose_cannot_start_candidate(monkeypatch):
    person = {"box": [.3, .3, .7, .7], "conf": .9, "posture": "lying", "support_surface": None}
    def detect(*_, **kw):
        return {"width": 640, "height": 360, "persons": [dict(person)], "person_count": 1,
                "objects": [], "flags": [], "status": "person"}
    monkeypatch.setattr(wall.scene, "analyze_frame_array", detect)
    state = wall.FeedState({"id": "turn"})
    assert state.process(None, 1000)["trigger"] is None
    person["box"] = [.35, .2, .65, .8]
    assert state.process(None, 1000.5)["trigger"] is None
    assert state.process(None, 1001.1)["trigger"] is not None
    fresh = wall.FeedState({"id": "narrow"})
    for t in (1000, 1000.5, 1001.1, 1002.2):
        assert fresh.process(None, t)["trigger"] is None
