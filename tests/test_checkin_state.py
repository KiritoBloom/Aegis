"""The spoken check-in is a state machine, so test the state machine.

No model, no audio: these are the invariants that make silence safe — one
check-in per incident, a deadline that can be claimed exactly once, and a
severity raise that a later phase cannot undo.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db  # noqa: E402


def _payload(incident_id: str) -> dict:
    return {
        "incident_id": incident_id,
        "household_id": "hh_demo",
        "camera_id": "cam_hallway",
        "type": "fall_confirmed",
        "status": "detected",
        "severity_hint": "info",
        "started_at": "2026-10-27T18:00:00+00:00",
        "local_time": "2026-10-27T21:00:00+03:00",
        "zone_id": "hallway",
        "track": {"person_count": 1, "posture": "lying"},
        "sensors": {"no_recovery_s": 12},
        "privacy": {},
        "evidence": ["posture_lying"],
        "source": "sim",
    }


def test_one_checkin_claim_and_one_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "aegis.db"))
    db.init()
    db.upsert_incident(_payload("inc_check"))

    assert db.start_checkin("inc_check", "Margaret, are you okay?", time.time() + 10, 15)
    # A second prompt must not reset or duplicate the deadline.
    assert not db.start_checkin("inc_check", "again", time.time() + 30, 15)
    assert db.checkin_pending("inc_check")

    due = db.due_checkins(time.time() + 11)
    assert [r["id"] for r in due] == ["inc_check"]
    # Only the first timeout claim may send an escalation.
    assert db.mark_checkin_no_answer("inc_check")
    assert not db.mark_checkin_no_answer("inc_check")
    inc = db.get_incident("inc_check")
    assert inc and inc["checkin_status"] == "no_answer"


def test_floor_severity_cannot_be_lowered_by_a_later_write(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "aegis.db"))
    db.init()
    db.upsert_incident(_payload("inc_floor"))
    db.raise_incident_severity("inc_floor", "urgent")
    db.raise_incident_severity("inc_floor", "info")
    assert db.get_incident("inc_floor")["severity"] == "urgent"


def test_cancel_checkin_stops_the_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "aegis.db"))
    db.init()
    db.upsert_incident(_payload("inc_cancel"))
    assert db.start_checkin("inc_cancel", "Margaret, are you okay?", time.time() + 10, 15)
    assert db.cancel_checkin("inc_cancel", "paused")
    assert not db.checkin_pending("inc_cancel")
    assert db.get_incident("inc_cancel")["checkin_status"] == "cancelled"
