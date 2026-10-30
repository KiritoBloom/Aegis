"""An escalation is a ratchet. A late writer may add words, never lower it.

The wall test found this in production shape: a phase-3 check-in timeout
escalated a possible fall to emergency, then the late phase-2 Muse write set
the row back to watch. The incident had already paged a phone.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import db, policy  # noqa: E402


def _incident(incident_id: str = "inc_ratchet") -> dict:
    return {
        "incident_id": incident_id,
        "household_id": "hh_demo",
        "camera_id": "cam_wall",
        "type": "fall_suspected",
        "status": "detected",
        "severity_hint": "emergency",
        "started_at": "2026-10-29T20:00:00Z",
        "local_time": "2026-10-29T22:00:00+02:00",
        "track": {},
        "sensors": {"posture": "lying"},
        "privacy": {},
        "evidence": [],
        "source": "wall",
    }


def _decision(severity: str) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, notify_now=True, title="late words", body="body",
        evidence=[], actions=[], confidence=0.5, follow_up_s=0,
        escalate_if_no_ack=False, rationale="late",
    )


def test_a_late_phase_two_cannot_lower_an_escalated_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ratchet.db"))
    db.init()
    db.upsert_incident(_incident())
    db.raise_incident_severity("inc_ratchet", "emergency")

    stored = db.add_decision("inc_ratchet", 2, "info", [], _decision("watch"),
                             "muse", "v", [], 0)
    assert stored == "emergency"

    inc = db.get_incident("inc_ratchet")
    assert inc["severity"] == "emergency"
    assert inc["decision"]["severity"] == "watch"  # the words are kept
    assert db.list_incidents()[0]["severity"] == "emergency"

    # A status refresh without new authority must not lower it either.
    db.set_status("inc_ratchet", "notified", "watch")
    assert db.get_incident("inc_ratchet")["severity"] == "emergency"

    # Only a resolution path may settle it lower.
    db.set_status("inc_ratchet", "resolved", "info")
    assert db.get_incident("inc_ratchet")["severity"] == "info"


def test_a_wall_fall_template_names_the_feed_not_the_hallway():
    inc = _incident("inc_place")
    inc["zone_id"] = None
    inc["sensors"] = {
        "posture": "lying",
        "wall_feed": {"feed_name": "home-fall-bedroom"},
    }
    profile = policy.ResidentProfile()
    floor = policy.evaluate(inc, profile, local_hour=12)
    decision = policy.template_decision(floor, inc, profile)
    assert "home fall bedroom" in decision.body
    assert "hallway" not in decision.body
