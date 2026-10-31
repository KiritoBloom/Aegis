"""A resident kiosk link is short-lived, scoped, and never a family session."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402

from app import api, auth, db  # noqa: E402


def _seed(iid: str = "inc_kiosk") -> None:
    db.upsert_incident({
        "incident_id": iid, "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_confirmed", "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": "hallway", "track": {"person_count": 1},
        "sensors": {"no_recovery_s": 10}, "privacy": {}, "evidence": [], "source": "sim",
    })
    db.start_checkin(iid, "Are you okay?", time.time() + 15, 15)


def test_resident_get_returns_only_the_checkin(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "kiosk.db"))
    monkeypatch.setattr(api, "MEDIA_ROOT", tmp_path / "media")
    db.init()
    _seed()
    token = auth.checkin_token("inc_kiosk")
    payload = api.resident_checkin("inc_kiosk", token)
    assert payload["checkin_prompt"] == "Are you okay?"
    assert payload["checkin_status"] == "asking"
    assert "decision" not in payload and "clip_url" not in payload
    assert "resident_checkin_url" in api.with_media(db.get_incident("inc_kiosk"))


def test_resident_link_is_scoped_and_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "kiosk2.db"))
    db.init()
    _seed("inc_kiosk2")
    token = auth.checkin_token("inc_kiosk2")
    assert auth.checkin_ok("inc_kiosk2", token) is True
    assert auth.checkin_ok("inc_other", token) is False
    expired = auth.checkin_token("inc_kiosk2", now=time.time() - auth.RESIDENT_LINK_S - 1)
    try:
        api.resident_checkin("inc_kiosk2", expired)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("expired resident token was accepted")


def test_resident_api_is_public_to_the_session_gate(monkeypatch):
    monkeypatch.setattr(auth, "PIN", "482913")
    assert api.session_gate_open("/api/resident/checkin/inc_1") is True
    assert api.session_gate_open("/api/incidents/inc_1") is False
