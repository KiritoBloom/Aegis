"""The resident's tap path must use the same policy merge as speech.

This test uses a fake model read and a temp database; it verifies the JSON body
path, the answer class, the resolution, and the fact that no audio ledger row is
written when the answer was tapped rather than spoken.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import api, db  # noqa: E402


class FakeMeta:
    def decide(self, packet, system, schema, effort="low"):
        return (
            {"class": "affirmative", "read": "She says she is up.", "evidence": ["upright"],
             "confidence": 0.9},
            SimpleNamespace(output_tokens=12, latency_ms=8),
        )


class FakeRequest:
    headers = {"content-type": "application/json"}

    async def body(self):
        return b'{"answer":"I\'m okay, I am up now."}'


def test_text_answer_uses_the_policy_floor_and_writes_no_audio_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "text-checkin.db"))
    monkeypatch.setattr(api, "meta", lambda: FakeMeta())
    db.init()
    db.upsert_incident({
        "incident_id": "inc_text",
        "household_id": "hh_demo",
        "camera_id": "cam_hallway",
        "type": "fall_confirmed",
        "status": "notified",
        "severity_hint": "urgent",
        "started_at": "2026-10-27T20:00:00+00:00",
        "local_time": "2026-10-27T23:00:00+03:00",
        "zone_id": "hallway",
        "track": {"person_count": 1, "posture": "upright"},
        "sensors": {"recovered": True, "no_recovery_s": 4},
        "privacy": {},
        "evidence": [],
        "source": "sim",
    })
    assert db.start_checkin("inc_text", "Margaret, are you okay?", time.time() + 15, 15)

    result = asyncio.run(api.checkin("inc_text", FakeRequest()))
    assert result["class"] == "affirmative"
    assert result["resolution"] == "self_recovered"
    assert result["severity"] == "urgent"  # floor retained

    inc = db.get_incident("inc_text")
    assert inc["checkin_status"] == "answered"
    assert inc["status"] == "resolved"
    assert inc["checkin"]["outcome"] == "self_recovered"
    assert not any(row["purpose"] == "transcribe" for row in inc["ledger"])
    assert any(row["purpose"] == "checkin_interpret" for row in inc["ledger"])

def test_live_recovery_outweighs_the_trigger_track_posture(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "text-recovered.db"))
    monkeypatch.setattr(api, "meta", lambda: FakeMeta())
    db.init()
    db.upsert_incident({
        "incident_id": "inc_recovered",
        "household_id": "hh_demo",
        "camera_id": "cam_wall",
        "type": "fall_suspected",
        "status": "notified",
        "severity_hint": "urgent",
        "started_at": "2026-10-27T20:00:00+00:00",
        "local_time": "2026-10-27T23:00:00+03:00",
        "zone_id": None,
        "track": {"person_count": 1, "posture": "lying"},
        "sensors": {"posture": "upright", "person_count": 1,
                    "no_recovery_s": 0, "recovered": True},
        "privacy": {},
        "evidence": [],
        "source": "wall",
    })
    assert db.start_checkin("inc_recovered", "Margaret, are you okay?", time.time() + 15, 15)

    result = asyncio.run(api.checkin("inc_recovered", FakeRequest()))
    assert result["resolution"] == "self_recovered"
    assert db.get_incident("inc_recovered")["checkin_status"] == "answered"


def test_an_unclear_answer_keeps_a_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unclear.db"))
    monkeypatch.setattr(api, "meta", lambda: FakeMeta())
    db.init()
    db.upsert_incident({
        "incident_id": "inc_unclear", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": "hallway",
        "track": {"person_count": 1, "posture": "upright"},
        "sensors": {"posture": "upright", "person_count": 1,
                    "no_recovery_s": 2, "still_s": 0, "recovered": False},
        "privacy": {}, "evidence": [], "source": "wall",
    })
    assert db.start_checkin("inc_unclear", "Margaret, are you okay?",
                            time.time() + 15, 15)

    result = asyncio.run(api.checkin("inc_unclear", FakeRequest()))

    assert result["resolution"] == "unclear"
    inc = db.get_incident("inc_unclear")
    assert inc["status"] == "notified"
    assert inc["checkin_status"] == "asking"
    assert inc["checkin_due_at"] > time.time()


def test_an_ack_during_the_answer_does_not_page_again(monkeypatch, tmp_path):
    class AckingMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            db.add_ack("inc_ack_race", "im_checking")
            db.mark_acked("inc_ack_race")
            return (
                {"class": "help", "read": "She says help.", "evidence": ["help"],
                 "confidence": 0.95},
                SimpleNamespace(output_tokens=12, latency_ms=8),
            )

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ack-race.db"))
    monkeypatch.setattr(api, "meta", lambda: AckingMeta())
    db.init()
    db.upsert_incident({
        "incident_id": "inc_ack_race", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_confirmed", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": "hallway",
        "track": {"person_count": 1, "posture": "lying"},
        "sensors": {"no_recovery_s": 10}, "privacy": {}, "evidence": [],
        "source": "sim",
    })
    db.start_checkin("inc_ack_race", "Are you okay?", time.time() + 15, 15)
    sent = []

    async def fake_dispatch(alert, phase, broadcast=None):
        sent.append(phase)
        return []

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)

    class HelpRequest:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"answer":"help me"}'

    result = asyncio.run(api.checkin("inc_ack_race", HelpRequest()))
    assert result["resolution"] == "already_closed"
    assert sent == []
    # "I'm checking" resolves an incident in this product; the point is that
    # the older check-in answer does not reopen or page it.
    assert db.get_incident("inc_ack_race")["status"] == "resolved"


def test_an_empty_answer_is_rejected_and_the_deadline_stays_live(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "empty-answer.db"))
    monkeypatch.setattr(api, "meta", lambda: FakeMeta())
    db.init()
    db.upsert_incident({
        "incident_id": "inc_empty", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_confirmed", "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": "hallway", "track": {"person_count": 1, "posture": "lying"},
        "sensors": {"no_recovery_s": 10}, "privacy": {}, "evidence": [], "source": "sim",
    })
    db.start_checkin("inc_empty", "Are you okay?", time.time() + 15, 15)

    class EmptyRequest:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"answer":"   "}'

    try:
        asyncio.run(api.checkin("inc_empty", EmptyRequest()))
    except Exception as exc:
        assert getattr(exc, "status_code", 0) == 422
    else:
        raise AssertionError("an empty answer must not be accepted")
    inc = db.get_incident("inc_empty")
    assert inc["checkin_status"] == "asking"
    assert inc["checkin_due_at"] is not None


def test_help_opens_an_emergency_gate_without_waiting_for_timeout(monkeypatch, tmp_path):
    class HelpMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            return ({"class": "help", "read": "Help requested", "evidence": ["help"],
                     "confidence": 0.99}, SimpleNamespace(output_tokens=12, latency_ms=8))

    class HelpRequest(FakeRequest):
        async def body(self):
            return b'{"answer":"help me"}'

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "emergency-gate.db"))
    monkeypatch.setattr(api, "meta", lambda: HelpMeta())
    db.init()
    db.upsert_incident({"incident_id": "inc_help", "type": "fall_confirmed",
                        "status": "resident_check", "severity_hint": "emergency",
                        "track": {"person_count": 1, "posture": "lying"},
                        "sensors": {"no_recovery_s": 30}, "source": "wall"})
    db.record_event("inc_help", None, "fall_confirmed", None, db.now(), "emergency",
                    suppressed=True, reason="verification_pending", source="wall")
    db.start_checkin("inc_help", "Are you okay?", time.time() + 30, 30)
    sent = []

    async def dispatch(alert, phase, broadcast=None):
        sent.append((alert, phase))
        return []

    monkeypatch.setattr(api.notify, "dispatch", dispatch)
    asyncio.run(api.checkin("inc_help", HelpRequest()))
    assert len(sent) == 1 and sent[0][1] == 3
    assert db.get_incident("inc_help")["status"] == "notified"
    assert db.recent_events(family=True)[0]["reason"] == "resident_requested_help"


def test_recovered_gate_history_is_visible_without_claiming_a_family_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "recovered-gate.db"))
    monkeypatch.setattr(api, "meta", lambda: FakeMeta())
    db.init()
    db.upsert_incident({"incident_id": "inc_gate_ok", "type": "fall_suspected",
                        "status": "resident_check", "severity_hint": "urgent",
                        "track": {"person_count": 1, "posture": "upright"},
                        "sensors": {"recovered": True, "no_recovery_s": 0}, "source": "wall"})
    db.record_event("inc_gate_ok", None, "fall_suspected", None, db.now(), "urgent",
                    suppressed=True, reason="verification_pending", source="wall")
    db.start_checkin("inc_gate_ok", "Are you okay?", time.time() + 30, 30)
    asyncio.run(api.checkin("inc_gate_ok", FakeRequest()))
    events = db.recent_events(family=True)
    assert len(events) == 2
    assert all(event["suppressed"] and event["severity"] == "info" for event in events)
    assert db.notifications_for("inc_gate_ok") == []
    db.init()
    assert all(event["reason"] != "family_alert_released" for event in db.recent_events(family=True))


def test_a_failed_audio_decode_restores_the_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bad-audio.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_bad_audio", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_confirmed", "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": "hallway", "track": {"person_count": 1, "posture": "lying"},
        "sensors": {"no_recovery_s": 10}, "privacy": {}, "evidence": [], "source": "sim",
    })
    db.start_checkin("inc_bad_audio", "Are you okay?", time.time() + 15, 15)

    class BadAudioRequest:
        headers = {"content-type": "audio/wav"}

        async def body(self):
            return b"this is not a wav file"

    try:
        asyncio.run(api.checkin("inc_bad_audio", BadAudioRequest()))
    except Exception as exc:
        assert getattr(exc, "status_code", 0) in (422, 503)
    else:
        raise AssertionError("undecodable audio must not silently disarm the check-in")
    inc = db.get_incident("inc_bad_audio")
    assert inc["checkin_status"] == "asking"
    assert inc["checkin_due_at"] is not None
