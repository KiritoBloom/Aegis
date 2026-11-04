"""The judge's walkthrough observes real work without opening family media gates."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app import api, auth, db
from app.agent import EvidenceAgent, PLAN_PROMPT
from app.meta import SamObject


@pytest.fixture
def investigation(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "inspection.db"))
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path / "media")
    monkeypatch.setattr(auth, "PIN", "482913")
    db.init()
    iid = "inc_inspection"
    db.upsert_incident({
        "incident_id": iid, "household_id": "hh_demo", "camera_id": "feed_demo",
        "type": "fall_suspected", "status": "verifying", "severity_hint": "urgent",
        "started_at": "2026-11-04T12:00:00Z", "local_time": "2026-11-04T14:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {"wall_feed": {"id": "feed_demo", "name": "Hallway"}},
        "privacy": {}, "evidence": [], "source": "wall",
    })
    folder = api.MEDIA_ROOT / iid
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"private event video" * 200)
    (folder / "privacy.jpg").write_bytes(b"exact protected pixels" * 200)
    return iid, folder


def test_inspection_is_session_only_and_does_not_unlock_family_media(investigation):
    iid, folder = investigation
    client = TestClient(api.app)
    # A signed family URL is not a household session for the inspection route.
    signed = auth.media_url(iid, "clip.mp4").split("?", 1)[1]
    assert client.get(f"/api/diagnostics/{iid}").status_code == 401
    assert client.get(f"/api/diagnostics/{iid}/media/clip.mp4?{signed}").status_code == 401
    token, _ = auth.make_session()
    client.cookies.set(auth.COOKIE, token)
    step = db.add_pipeline_step(iid, "sam", "running", "Sending video", "", {"request": {"prompt": "person"}})
    response = client.get(f"/api/diagnostics/{iid}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["id"] == step["id"]
    assert {item["name"] for item in response.json()["media"]} == {"clip.mp4", "privacy.jpg"}
    assert client.get(f"/api/diagnostics/{iid}/media/privacy.jpg").content == (folder / "privacy.jpg").read_bytes()
    assert client.get(f"/media/{iid}/clip.mp4").status_code == 404
    assert client.get(f"/media/{iid}/privacy.jpg").status_code == 404
    # Range support is essential for scrubbing a returned video immediately.
    partial = client.get(f"/api/diagnostics/{iid}/media/clip.mp4", headers={"Range": "bytes=0-15"})
    assert partial.status_code == 206
    assert partial.content == (folder / "clip.mp4").read_bytes()[:16]
    assert partial.headers["cache-control"] == "no-store"


def test_inspection_allowlist_missing_and_expired_media(investigation):
    iid, folder = investigation
    client = TestClient(api.app)
    token, _ = auth.make_session()
    client.cookies.set(auth.COOKIE, token)
    (folder / "secret.env").write_text("not evidence")
    assert client.get(f"/api/diagnostics/{iid}/media/secret.env").status_code == 404
    assert client.get(f"/api/diagnostics/{iid}/media/annotated.mp4").status_code == 404
    assert client.get("/api/diagnostics/inc_missing").status_code == 404
    assert client.get("/api/diagnostics/%2E%2E%5Cprivate/media/clip.mp4").status_code >= 400
    (folder / "privacy.jpg").unlink()
    assert client.get(f"/api/diagnostics/{iid}/media/privacy.jpg").status_code == 404


def test_agent_stream_reports_exact_requests_and_returned_results(investigation, monkeypatch):
    iid, _ = investigation
    sent = []
    usage = SimpleNamespace(latency_ms=7, output_tokens=20)

    class Model:
        def decide(self, packet, instructions, schema, effort):
            sent.append(json.loads(json.dumps(packet)))
            if instructions == PLAN_PROMPT:
                return {"calls": [{"tool": "household_memory", "question": ""}], "why": "Check context."}, usage
            return {"verdict": "escalate", "severity": "urgent", "rationale": "Floor evidence."}, usage

    async def memory(_question):
        return {"facts": ["No expected visitor"]}

    async def no_broadcast(_payload):
        pass

    monkeypatch.setattr(api, "broadcast", no_broadcast)
    result = asyncio.run(EvidenceAgent(Model()).run(
        packet={"local": {"posture": "lying"}}, tools={"household_memory": memory},
        verify=True, floor="urgent", on_step=api._agent_progress(iid)))
    rows = db.pipeline_steps(100, incident_id=iid)
    requests = [row["metrics"]["request"]["packet"] for row in rows
                if row["metrics"]["phase"] in ("plan", "decision") and row["status"] == "running"]
    assert requests == sent
    assert rows[-1]["metrics"]["response"] == result.raw
    tool = next(row for row in rows if row["metrics"].get("tool") and row["status"] == "done")
    assert tool["metrics"]["response"] == {"facts": ["No expected visitor"]}
    assert tool["metrics"]["destination"] == "local"


def test_visual_request_shows_the_exact_protected_image_before_model_returns(investigation, monkeypatch):
    iid, folder = investigation
    usage = SimpleNamespace(latency_ms=10, output_tokens=12)

    class Model:
        def vision(self, image, prompt, schema, effort):
            assert image == (folder / "privacy.jpg").read_bytes()
            assert "floor" in prompt
            request = db.pipeline_steps(20, incident_id=iid)[-1]
            assert request["status"] == "running"
            assert request["metrics"]["media"][0]["name"] == "privacy.jpg"
            assert request["metrics"]["request"]["bytes"] == len(image)
            return {"observation": "A person on the floor.", "posture": "lying"}, usage

    async def no_broadcast(_payload):
        pass

    monkeypatch.setattr(api, "broadcast", no_broadcast)
    monkeypatch.setattr(api, "meta", lambda: Model())
    result = asyncio.run(api._agent_tools({}, iid, folder / "privacy.jpg")["inspect_frame"]("Is the person on the floor?"))
    assert result["available"] is True
    assert db.pipeline_steps(20, incident_id=iid)[-1]["metrics"]["response"]["posture"] == "lying"


def test_failed_model_step_is_reported_without_turning_failure_into_a_decision(investigation):
    iid, _ = investigation
    observed = []

    class Model:
        def decide(self, packet, instructions, schema, effort):
            if instructions == PLAN_PROMPT:
                return {"calls": [], "why": "Enough context."}, SimpleNamespace(latency_ms=1)
            raise ValueError("model response unavailable")

    async def capture(step):
        observed.append(step)

    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(EvidenceAgent(Model()).run(packet={}, tools={}, verify=True, floor="urgent", on_step=capture))
    assert observed[-1]["step"] == "decision"
    assert observed[-1]["status"] == "failed"
    assert "response" not in observed[-1]


def test_sam_artifacts_publish_in_order_and_preview_does_not_hold_up_verification(investigation, monkeypatch):
    iid, folder = investigation
    object_ = SamObject(0, 1, 20, 30, 100, 220, 320, 240, .9)
    preview_started = threading.Event()
    release_preview = threading.Event()
    notifications = []

    async def broadcast(payload):
        notifications.append(payload["step"])

    def prepare(src, dst):
        dst.write_bytes(src.read_bytes())
        return dst

    class Model:
        def sam_video(self, path, prompt):
            assert Path(path).name == "sam_input.mp4"
            assert notifications[-1]["metrics"]["request"]["prompt"] == prompt == "person"
            return "actual SAM mask response", SimpleNamespace(latency_ms=12), Path(path).stat().st_size

    def render_video(src, dst, objects, **kwargs):
        if kwargs.get("blur_all"):
            preview_started.set()
            assert release_preview.wait(5)
        dst.write_bytes(b"playable render" * 200)
        return {"rendered": True, "frames_total": 1, "people_redacted": 0}

    def privacy_still(src, dst, objects, resident):
        # Segmentation was already exposed, not withheld until blurring/Muse.
        assert any(row["metrics"].get("media", [{}])[0].get("name") == "annotated.mp4"
                   for row in notifications if row["metrics"].get("media"))
        dst.write_bytes(b"protected model image" * 200)
        return {"written": True}

    monkeypatch.setattr(api, "broadcast", broadcast)
    monkeypatch.setattr(api.clip_util, "sam_ready", prepare)
    monkeypatch.setattr(api, "meta", lambda: Model())
    monkeypatch.setattr(api, "parse_sam", lambda _text: [object_])
    monkeypatch.setattr(api.render, "render", render_video)
    monkeypatch.setattr(api.render, "privacy_still", privacy_still)
    monkeypatch.setattr(api, "_start_motion_comparison", lambda *args: False)

    async def scenario():
        try:
            features = await asyncio.wait_for(api._sam_features_for_clip(iid, folder / "clip.mp4"), 3)
            assert features["person_count"] == 1
            # SAM returns while the optional local movie is still blocked.
            assert not release_preview.is_set()
            assert await asyncio.to_thread(preview_started.wait, 2)
        finally:
            release_preview.set()
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        await asyncio.wait_for(asyncio.gather(*pending), 3)

    asyncio.run(scenario())
    assert (folder / "sam_response.txt").read_text() == "actual SAM mask response"
    ready_names = [row["metrics"]["media"][0]["name"] for row in notifications
                   if row["status"] == "done" and row["metrics"].get("media")]
    assert ready_names == ["sam_response.txt", "annotated.mp4", "privacy.jpg", "privacy.mp4"]
    assert len(db.pipeline_steps(50, incident_id=iid)) == len(notifications)
