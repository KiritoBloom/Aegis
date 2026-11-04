"""The gates that keep a closed, paused or malicious request out of the house."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402

from app import api, db, privacy  # noqa: E402


class FakeRequest:
    headers = {"content-type": "application/json"}

    async def body(self):
        return b'{"answer":"I am okay"}'


def _incident(iid: str, status: str = "notified", checkin: str = "asking") -> None:
    db.upsert_incident({
        "incident_id": iid, "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_confirmed", "status": status, "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": "hallway", "track": {"person_count": 1},
        "sensors": {"no_recovery_s": 10}, "privacy": {}, "evidence": [], "source": "sim",
    })
    if checkin:
        db.start_checkin(iid, "Are you okay?", time.time() + 15, 15)


def test_incident_ids_cannot_escape_the_media_directory():
    for bad in ("../x", "a/b", "..", "/etc/passwd", "x.png", "", "a" * 81):
        try:
            api.valid_incident_id(bad)
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError(f"accepted unsafe id {bad!r}")
    assert api.valid_incident_id("inc_ok-1") == "inc_ok-1"


def test_a_paused_checkin_is_refused_before_audio_or_model_work(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "paused-checkin.db"))
    db.init()
    _incident("inc_paused_checkin")
    privacy.pause("test", "Margaret")
    try:
        asyncio.run(api.checkin("inc_paused_checkin", FakeRequest()))
    except HTTPException as exc:
        assert exc.status_code == 423
    else:
        raise AssertionError("a paused check-in must not be accepted")
    finally:
        privacy.resume()


def test_a_closed_checkin_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "closed-checkin.db"))
    db.init()
    _incident("inc_closed_checkin")
    db.mark_checkin_no_answer("inc_closed_checkin")
    try:
        asyncio.run(api.checkin("inc_closed_checkin", FakeRequest()))
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("a closed check-in must not restart")


def test_resume_requires_resident_consent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "consent-resume.db"))
    db.init()
    privacy.pause("resident paused", "Margaret")
    try:
        asyncio.run(api.privacy_resume())
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("resume without consent must be refused")
    privacy.record_consent(member="Margaret", device="tablet", given=True)
    out = asyncio.run(api.privacy_resume())
    assert out["paused"] is False


def test_an_acknowledged_incident_cannot_reclaim_a_phase(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "acked-phase.db"))
    db.init()
    _incident("inc_acked_phase", checkin="")
    db.add_ack("inc_acked_phase", "call_resident")
    db.mark_acked("inc_acked_phase")
    assert db.claim_notify_phase("inc_acked_phase", 1, time.time() + 60) is False


def test_a_traversal_ingest_is_rejected_before_any_file_is_written(monkeypatch, tmp_path):
    from starlette.datastructures import UploadFile
    import io
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "traversal.db"))
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path / "media")
    db.init()
    upload = UploadFile(filename="clip.mp4", file=io.BytesIO(b"not-a-video"))
    try:
        asyncio.run(api.ingest_clip(incident_id="../escape", clip=upload))
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("traversal incident id must not be written")
    assert not (tmp_path / "escape").exists()
