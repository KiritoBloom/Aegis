"""Redaction is a promise: the family clip must render and the raw clip must not leak."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import api, render  # noqa: E402


class FakeObject:
    def __init__(self, frame: int, obj_id: int, box: tuple[float, float, float, float],
                 width: int = 320, height: int = 240) -> None:
        self.frame = frame
        self.obj_id = obj_id
        self.x1, self.y1, self.x2, self.y2 = box
        self.img_w = width
        self.img_h = height

    @property
    def box_norm(self):
        return (self.x1 / self.img_w, self.y1 / self.img_h,
                self.x2 / self.img_w, self.y2 / self.img_h)

    @property
    def foot(self):
        return ((self.box_norm[0] + self.box_norm[2]) / 2, self.box_norm[3])

    def raster(self):
        w = max(1, int(self.x2 - self.x1))
        h = max(1, int(self.y2 - self.y1))
        return w, h, bytes([1]) * (w * h)


def _video(path: Path, frames: int = 4) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 4.0,
                             (320, 240))
    assert writer.isOpened()
    for i in range(frames):
        img = np.full((240, 320, 3), (30 + i * 10, 70, 90), dtype=np.uint8)
        writer.write(img)
    writer.release()


def test_redacted_render_honours_annotate_false_and_writes_a_still(tmp_path):
    source = tmp_path / "clip.mp4"
    _video(source)
    objects = [
        FakeObject(f, 1, (20, 40, 100, 220)) for f in range(4)
    ] + [
        FakeObject(f, 2, (200, 40, 290, 220)) for f in range(4)
    ]
    dst = tmp_path / "redacted.mp4"
    manifest = render.render(source, dst, objects, redact=True, annotate=False,
                            resident_box=[0.07, 0.17, 0.32, 0.92])

    assert manifest["rendered"] is True
    assert manifest["people_redacted"] == 1
    assert manifest["frames_annotated"] == 0
    assert manifest["still"] == "redacted.jpg"
    assert (tmp_path / "redacted.jpg").exists()
    assert manifest["primary_track"] == 1


def test_primary_track_uses_the_local_resident_hint():
    objects = [
        FakeObject(f, 1, (20, 40, 100, 220)) for f in range(6)
    ] + [
        FakeObject(f, 2, (200, 40, 290, 220)) for f in range(4)
    ]
    # Most frames belongs to track 1; the fall evidence points at track 2.
    assert render._primary_track(objects) == 1
    assert render._primary_track(objects, [0.65, 0.17, 0.9, 0.92]) == 2


def test_with_media_hides_raw_media_once_a_visitor_was_redacted(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(api.db, "media_private", lambda _id: True)
    folder = tmp_path / "inc_red"
    folder.mkdir()
    for name in ("clip.mp4", "still.jpg", "thumb.jpg", "redacted.mp4",
                 "redacted.jpg"):
        (folder / name).write_bytes(b"x")
    inc = api.with_media({
        "id": "inc_red",
        "clip_path": str(folder / "clip.mp4"),
        "still_path": str(folder / "still.jpg"),
        "thumb_path": str(folder / "thumb.jpg"),
        "sam": {"person_count": 2,
                "overlay": {"people_redacted": 1}},
    })

    assert "clip_url" not in inc
    assert "clip_path" not in inc
    assert inc["redacted_url"].startswith("/media/inc_red/redacted.mp4")
    assert "redacted.jpg" in inc["still_url"]
    assert "redacted.jpg" in inc["thumb_url"]
    assert inc["has_clip"] is True


def test_with_media_keeps_raw_media_for_a_single_resident(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(api.db, "media_private", lambda _id: False)
    folder = tmp_path / "inc_solo"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"x")
    inc = api.with_media({
        "id": "inc_solo", "clip_path": str(folder / "clip.mp4"),
        "sam": {"person_count": 1, "overlay": {"people_redacted": 0}},
    })
    assert inc["clip_url"].startswith("/media/inc_solo/clip.mp4")
    assert inc["has_clip"] is True


def test_media_private_fails_closed_when_sam_is_missing_or_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(api.db, "DB_PATH", str(tmp_path / "media.db"))
    api.db.init()

    def seed(iid, sam):
        api.db.upsert_incident({
            "incident_id": iid, "household_id": "hh_demo", "camera_id": "cam",
            "type": "fall_suspected", "status": "notified", "severity_hint": "urgent",
            "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
            "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
            "evidence": [], "source": "wall",
        })
        if sam is not None:
            api.db.attach_sam(iid, sam)

    seed("inc_none", None)
    seed("inc_failed", {"track_unavailable": True, "error": "timeout"})
    seed("inc_two", {"person_count": 2, "overlay": {"people_redacted": 1}})
    seed("inc_solo", {"person_count": 1, "overlay": {"people_redacted": 0}})

    assert api.db.media_private("inc_none") is True
    assert api.db.media_private("inc_failed") is True
    assert api.db.media_private("inc_two") is True
    assert api.db.media_private("inc_solo") is False


def test_media_route_refuses_raw_names_for_private_incidents(monkeypatch, tmp_path):
    from fastapi import HTTPException

    monkeypatch.setattr(api.db, "DB_PATH", str(tmp_path / "route.db"))
    monkeypatch.setattr(api.auth, "media_ok", lambda *_a, **_k: True)
    api.db.init()
    api.db.upsert_incident({
        "incident_id": "inc_route", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_suspected", "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    api.db.attach_sam("inc_route", {"track_unavailable": True})

    class Req:
        cookies = {}
        query_params = {}
        client = type("C", (), {"host": "test"})()

    try:
        api.media("inc_route", "clip.mp4", Req())
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("raw clip resolved for a private incident")

    # The redacted still remains available even when the raw still is sealed.
    (api.MEDIA_ROOT / "inc_route").mkdir(parents=True, exist_ok=True)
    (api.MEDIA_ROOT / "inc_route" / "redacted.jpg").write_bytes(b"x")
    response = api.media("inc_route", "redacted.jpg", Req())
    assert Path(response.path).name == "redacted.jpg"


def test_agent_still_is_never_the_raw_frame(monkeypatch, tmp_path):
    """Muse may see a blurred still or nothing. It never sees the raw frame."""
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path)
    folder = tmp_path / "inc_still"
    folder.mkdir()
    payload = b"x" * 4096
    (folder / "still.jpg").write_bytes(payload)
    monkeypatch.setattr(api.db, "media_private", lambda _id: False)
    assert api._still_for_agent("inc_still") is None

    (folder / "redacted.jpg").write_bytes(payload)
    assert api._still_for_agent("inc_still").name == "redacted.jpg"

    (folder / "privacy.jpg").write_bytes(payload)
    assert api._still_for_agent("inc_still").name == "privacy.jpg"


def test_agent_still_ignores_a_truncated_blur(monkeypatch, tmp_path):
    """A failed render leaves a stub file; a stub is not a usable still."""
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path)
    folder = tmp_path / "inc_tiny"
    folder.mkdir()
    (folder / "privacy.jpg").write_bytes(b"x" * 64)
    monkeypatch.setattr(api.db, "media_private", lambda _id: True)
    assert api._still_for_agent("inc_tiny") is None


def test_blurred_preview_includes_resident_and_missing_frames_without_overwriting_muse_image(tmp_path):
    source = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 4.0, (320, 240))
    # Fine detail makes a real blur measurable after browser-video encoding.
    pixels = ((np.indices((240, 320)).sum(axis=0) // 4) % 2 * 255).astype(np.uint8)
    frame = np.repeat(pixels[:, :, None], 3, axis=2)
    for _ in range(4):
        writer.write(frame)
    writer.release()
    image = tmp_path / "privacy.jpg"
    image.write_bytes(b"the exact protected image already prepared for Muse")
    before = image.read_bytes()
    objects = [FakeObject(0, 1, (20, 40, 100, 220)), FakeObject(0, 2, (200, 40, 290, 220))]
    manifest = render.render(source, tmp_path / "privacy.mp4", objects,
                             annotate=False, trail=False, blur_all=True)
    assert manifest["rendered"] is True
    assert manifest["people_redacted"] == 2  # Resident included, not bystanders only.
    assert manifest["untracked_frames_blurred"] == 3
    assert manifest["frames_annotated"] == 0
    assert manifest["still"] is None
    assert image.read_bytes() == before
    cap = cv2.VideoCapture(str(tmp_path / "privacy.mp4"))
    ok, blurred = cap.read()
    assert ok
    assert blurred[70:190, 40:80].std() < frame[70:190, 40:80].std() / 2
    assert blurred[70:190, 220:270].std() < frame[70:190, 220:270].std() / 2
    ok, missing = cap.read()
    assert ok and missing.std() < frame.std() / 2
    cap.release()
