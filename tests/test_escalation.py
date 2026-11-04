"""Silence after an alert must produce another real notification, not just a log row."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import api, db  # noqa: E402


def test_unacknowledged_alert_dispatches_phase_three(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "escalation.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_escalate", "household_id": "hh_demo",
        "camera_id": "cam_wall", "type": "fall_suspected", "status": "detected",
        "severity_hint": "urgent", "started_at": "2026-10-29T20:00:00Z",
        "local_time": "2026-10-29T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {"posture": "lying"}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    db.set_status("inc_escalate", "notified", "urgent")
    db.mark_phase("inc_escalate", 1, time.time() - 1)

    sent = []

    async def fake_dispatch(alert, phase, broadcast=None):
        sent.append((phase, alert["title"], alert["body"]))
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    inc = {"id": "inc_escalate", "severity": "urgent"}
    alert = asyncio.run(api.escalate_unacknowledged(inc))

    assert sent and sent[0][0] == 3
    assert "Margaret" in alert["title"]
    stored = db.get_incident("inc_escalate")
    assert stored["escalation_due_at"] is None
    assert stored["acked_at"] is None
    assert any(e["type"] == "no_acknowledgement" for e in db.recent_events(10))


def test_recovery_closes_the_incident_and_silences_every_later_page(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "recovery.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_recover", "household_id": "hh_demo",
        "camera_id": "cam_wall", "type": "fall_suspected", "status": "detected",
        "severity_hint": "urgent", "started_at": "2026-10-29T20:00:00Z",
        "local_time": "2026-10-29T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {"posture": "lying"}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    db.set_status("inc_recover", "notified", "urgent")
    db.mark_phase("inc_recover", 1, time.time() - 1)
    assert db.start_checkin("inc_recover", "Are you okay?", time.time() + 30, 15)

    out = db.recover_wall_incident("inc_recover")
    assert out["changed"] is True
    stored = db.get_incident("inc_recover")
    assert stored["status"] == "resolved"
    assert stored["checkin_status"] == "cancelled"
    assert stored["checkin_due_at"] is None
    assert stored["escalation_due_at"] is None
    assert stored["sensors"]["recovered"] is True
    assert db.due_escalations(time.time() + 1) == []
    assert db.due_checkins(time.time() + 60) == []
    # A terminal incident is not reopened by a second recovery report.
    assert db.recover_wall_incident("inc_recover")["changed"] is False


def test_phase_one_never_pages_a_closed_or_paused_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "closed.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_closed", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_suspected", "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    sent = []

    async def fake_dispatch(alert, phase, broadcast=None):
        sent.append((alert, phase))
        return []

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)

    db.set_status("inc_closed", "resolved", "info")
    asyncio.run(api.phase_one("inc_closed"))
    assert sent == []

    db.upsert_incident({
        "incident_id": "inc_paused_phase_one", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "detected",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "wall",
    })
    api.privacy.pause("test pause", "Margaret")
    try:
        asyncio.run(api.phase_one("inc_paused_phase_one"))
        assert sent == []
    finally:
        api.privacy.resume()


def test_enrichment_does_not_page_after_the_resident_recovers(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "race.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_race", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_suspected", "status": "detected", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    async def fake_dispatch(alert, phase, broadcast=None):
        raise AssertionError("a closed incident must not dispatch")

    class FakeAgent:
        def __init__(self, *_a, **_k):
            pass

        async def run(self, **_kwargs):
            db.set_status("inc_race", "resolved", "info")
            return SimpleNamespace(
                raw={"severity": "emergency", "notify_now": True, "title": "Stale",
                     "body": "Stale page", "evidence": [], "actions": [],
                     "confidence": 0.9, "follow_up_s": 60,
                     "escalate_if_no_ack": True, "rationale": "stale"},
                final_packet={}, trace=[], usage=[], model="fake", latency_ms=1,
            )

    monkeypatch.setenv("META_API_KEY", "test-key")
    # The fake agent must not instantiate a real client or parse host proxy settings.
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:bad-port")
    monkeypatch.setattr(api, "meta", lambda: object())
    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "EvidenceAgent", FakeAgent)
    incident = db.get_incident("inc_race")
    asyncio.run(api.enrich(incident))
    assert (db.get_incident("inc_race") or {}).get("status") == "resolved"


def test_expiry_rechecks_recovery_after_claiming_the_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "expiry-race.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_expiry_race", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {"posture": "lying"}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    db.start_checkin("inc_expiry_race", "Are you okay?", time.time() - 1, 15)

    def claim_with_recovery(_incident_id):
        db.recover_wall_incident("inc_expiry_race")
        return True

    async def fail_dispatch(alert, phase, broadcast=None):
        raise AssertionError("a recovered incident must not escalate")

    monkeypatch.setattr(db, "mark_checkin_no_answer", claim_with_recovery)
    monkeypatch.setattr(api.notify, "dispatch", fail_dispatch)
    incident = db.get_incident("inc_expiry_race")
    asyncio.run(api.expire_checkin(incident))
    assert (db.get_incident("inc_expiry_race") or {}).get("status") == "resolved"


def test_phase_one_escalation_is_due_even_if_enrichment_stalls(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "phase1-due.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_phase1_due", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_suspected", "status": "detected", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    db.mark_phase("inc_phase1_due", 1, time.time() - 1)
    due = db.due_escalations(time.time())
    assert any(i["id"] == "inc_phase1_due" for i in due)


def test_an_ack_cannot_reopen_a_resolved_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ack-terminal.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_ack_terminal", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "wall",
    })
    db.set_status("inc_ack_terminal", "resolved", "info")
    db.add_ack("inc_ack_terminal", "call_resident")
    assert db.get_incident("inc_ack_terminal")["status"] == "resolved"


def test_a_late_phase_two_never_pages_after_phase_three(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "late-phase.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_late", "household_id": "hh_demo", "camera_id": "cam",
        "type": "fall_suspected", "status": "detected", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z", "local_time": "2026-10-31T22:00:00+02:00",
        "zone_id": None, "track": {}, "sensors": {}, "privacy": {},
        "evidence": [], "source": "wall",
    })
    db.mark_phase("inc_late", 3, None)

    async def fail_dispatch(alert, phase, broadcast=None):
        raise AssertionError("phase 2 must not page after phase 3")

    class FakeAgent:
        def __init__(self, *_a, **_k):
            pass

        async def run(self, **_kwargs):
            return SimpleNamespace(
                raw={"severity": "emergency", "notify_now": True, "title": "Late",
                     "body": "Late page", "evidence": [], "actions": [],
                     "confidence": 0.9, "follow_up_s": 60,
                     "escalate_if_no_ack": True, "rationale": "late"},
                final_packet={}, trace=[], usage=[], model="fake", latency_ms=1,
            )

    monkeypatch.setenv("META_API_KEY", "test-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:bad-port")
    monkeypatch.setattr(api, "meta", lambda: object())
    monkeypatch.setattr(api.notify, "dispatch", fail_dispatch)
    monkeypatch.setattr(api, "EvidenceAgent", FakeAgent)
    asyncio.run(api.enrich(db.get_incident("inc_late")))
    assert any(e["type"] == "decision_suppressed" for e in db.recent_events(5))


def test_phase_records_never_move_backwards(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "phase-ratchet.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_phase_ratchet", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "wall",
    })
    db.mark_phase("inc_phase_ratchet", 3, None)
    db.mark_phase("inc_phase_ratchet", 1, time.time() + 600)
    inc = db.get_incident("inc_phase_ratchet")
    assert inc["notify_phase"] == 3
    assert inc["escalation_due_at"] is None
    assert db.claim_notify_phase("inc_phase_ratchet", 1, time.time() + 600) is False


def test_phase_one_never_pages_after_phase_three(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "late-phase1.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_late_phase1", "household_id": "hh_demo",
        "camera_id": "cam", "type": "fall_suspected", "status": "notified",
        "severity_hint": "urgent", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "wall",
    })
    db.mark_phase("inc_late_phase1", 3, None)
    sent = []

    async def fake_dispatch(alert, phase, broadcast=None):
        sent.append(phase)
        return []

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    asyncio.run(api.phase_one("inc_late_phase1"))
    assert sent == []
    assert db.get_incident("inc_late_phase1")["notify_phase"] == 3


def test_a_suppressed_repeat_is_persisted_as_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "suppressed.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_suppressed", "household_id": "hh_demo",
        "camera_id": "cam", "type": "door_open", "status": "detected",
        "severity_hint": "watch", "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": "front_door",
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "edge",
    })
    sent = []

    async def fake_dispatch(alert, phase, broadcast=None):
        sent.append(phase)
        return []

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(db, "previous_event", lambda _id: {"incident_id": "inc_old"})
    monkeypatch.setattr(api.policy, "dedupe_action", lambda _prev, _inc: "suppress")
    asyncio.run(api.phase_one("inc_suppressed"))
    assert sent == []
    assert db.get_incident("inc_suppressed")["status"] == "resolved"
    assert any(e["reason"] == "false_alarm_cooldown" for e in db.recent_events(5))


def test_metadata_retention_purges_old_rows_but_keeps_new_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "retention.db"))
    db.init()
    for iid, old in (("inc_old", "2020-01-01T00:00:00Z"),
                     ("inc_new", "2099-01-01T00:00:00Z")):
        db.upsert_incident({
            "incident_id": iid, "household_id": "hh_demo", "camera_id": "cam",
            "type": "fall_suspected", "status": "detected", "severity_hint": "urgent",
            "started_at": old, "local_time": old, "zone_id": None,
            "track": {}, "sensors": {}, "privacy": {}, "evidence": [], "source": "wall",
        })
        with db.connect() as conn:
            conn.execute("UPDATE incidents SET created_at=? WHERE id=?", (old, iid))
            conn.execute("INSERT INTO events (incident_id,camera_id,type,ts,severity,"
                         "suppressed,reason,source) VALUES (?,?,?,?,?,?,?,?)",
                         (iid, "cam", "fall_suspected", old, "urgent", 0, "test", "wall"))
            conn.commit()
    db.purge_metadata(90)
    ids = {r["id"] for r in db.list_incidents(50)}
    assert "inc_old" not in ids
    assert "inc_new" in ids
    assert not any(e["incident_id"] == "inc_old" for e in db.recent_events(20))
