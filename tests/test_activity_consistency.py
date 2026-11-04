"""Regressions for missing history, duplicate alerts, and stale lifecycle writes."""
import asyncio
import json
import time

import pytest

from app import api, db, policy


@pytest.fixture(autouse=True)
def isolated_house(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "activity.db"))
    monkeypatch.setattr(api, "CHECKIN_TTS", False)
    monkeypatch.setattr(api, "PROFILE", policy.ResidentProfile())
    db.init()


def add(ident, status="detected", severity="urgent", **extra):
    row = {"incident_id": ident, "type": "fall_suspected", "source": "wall",
           "status": status, "severity_hint": severity, "camera_id": "cam_wall",
           "sensors": {}, "track": {}, "started_at": db.now(), "local_time": db.now()}
    row.update(extra)
    db.upsert_incident(row)
    return row


def test_rejections_do_not_starve_incident_limit():
    add("real")
    for n in range(40):
        add(f"rejected_{n}", "resolved", sensors={"verification": "rejected"})
    assert [i["id"] for i in api.list_incidents(limit=1)["items"]] == ["real"]


def test_incident_filter_is_applied_before_limit():
    add("urgent")
    for n in range(5):
        add(f"quiet_{n}", severity="info")
    assert [i["id"] for i in api.list_incidents(limit=1, severity="urgent")["items"]] == ["urgent"]


def test_activity_visibility_is_filtered_before_limit():
    db.record_event(None, "cam", "motion_only", "hall", db.now(), "info", suppressed=True)
    for n in range(8):
        add(f"held_{n}", "resident_check")
        db.record_event(f"held_{n}", "cam", "fall_suspected", None, db.now(), "urgent", reason="verification_deadline")
    result = api.events(limit=1)["items"]
    assert len(result) == 1 and result[0]["type"] == "motion_only"
    assert result[0]["incident_available"] == 0


def test_rejected_candidate_stays_in_activity_without_broken_detail_link():
    packet = add("rejected", "verifying")
    db.record_event("rejected", "cam", packet["type"], None, db.now(), "urgent",
                    suppressed=True, reason="verification_pending")
    asyncio.run(api._reject_wall_incident("rejected", "Person resting"))
    items = api.events()["items"]
    assert len(items) == 1
    assert items[0]["suppressed"] == 1
    assert items[0]["incident_available"] == 0
    assert "Person resting" in items[0]["reason"]


def test_gate_release_makes_original_event_visible(monkeypatch):
    async def scenario():
        packet = add("release", "verifying")
        await api._create_wall_incident(packet)
        await api._release_wall_incident("release", "verification_unavailable_local_floor")
        assert api.events()["items"] == []
        assert db.get_incident("release")["decision"]["model"] == "template"
        await api.page_resident_check("release")
    asyncio.run(scenario())
    items = api.events()["items"]
    assert len(items) == 1
    assert items[0]["reason"] == "family_alert_released"
    assert items[0]["incident_available"] == 1


def test_ack_atomically_stops_both_timers():
    add("ack")
    db.start_checkin("ack", "Are you okay?", time.time() + 30, 30)
    db.mark_phase("ack", 1, time.time() + 60)
    asyncio.run(api.ack("ack", api.AckIn(action="im_checking")))
    inc = db.get_incident("ack")
    assert inc["status"] == "resolved"
    assert inc["acked_at"]
    assert inc["checkin_status"] == "cancelled"
    assert inc["checkin_due_at"] is None and inc["escalation_due_at"] is None
    assert api.events()["items"][0]["type"] == "family_acknowledged"


def test_inflight_gate_cannot_be_acknowledged():
    add("held", "resident_check")
    with pytest.raises(api.HTTPException) as exc:
        asyncio.run(api.ack("held", api.AckIn(action="im_checking")))
    assert exc.value.status_code == 404


def test_profile_patch_validates_every_field_before_mutation():
    before = api.PROFILE.display_name
    with pytest.raises(api.HTTPException):
        asyncio.run(api.put_household({"profile": {"display_name": "Changed", "age": "invalid"}}))
    assert api.PROFILE.display_name == before


def test_duplicate_ingest_does_not_create_another_event(monkeypatch):
    class Request:
        async def body(self):
            return json.dumps({"incident_id": "duplicate", "type": "motion_only"}).encode()
    async def quiet(*args, **kwargs):
        pass
    monkeypatch.setattr(api, "EDGE_TOKEN", "")
    monkeypatch.setattr(api, "phase_one", quiet)
    async def scenario():
        await api.ingest_incident(Request())
        second = await api.ingest_incident(Request())
        assert second["duplicate"] is True
    asyncio.run(scenario())
    assert len(db.recent_events()) == 1


def test_restart_repairs_old_settled_pending_events():
    add("old", "notified")
    db.mark_phase("old", 2)
    db.record_event("old", "cam", "fall_suspected", None, db.now(), "urgent",
                    suppressed=True, reason="verification_pending")
    add("still_held", "verifying")
    db.record_event("still_held", "cam", "fall_suspected", None, db.now(), "urgent",
                    suppressed=True, reason="verification_pending")
    db.init()
    assert [e["incident_id"] for e in api.events()["items"]] == ["old"]


def test_claimed_timeout_is_recoverable_after_crash():
    async def scenario():
        await api._create_wall_incident(add("crash", "verifying"))
        await api._release_wall_incident("crash", "unavailable")
    asyncio.run(scenario())
    assert db.mark_checkin_no_answer("crash")
    inc = db.get_incident("crash")
    assert inc["verification_due_at"] is not None
    assert [i["id"] for i in db.due_verifications(inc["verification_due_at"] + 1)] == ["crash"]


def test_fail_open_cannot_overwrite_or_resend_later_escalation(monkeypatch):
    add("late")
    decision = policy.Decision(severity="emergency", notify_now=True, title="Needs help",
        body="Please check now", evidence=[], actions=[], confidence=1,
        follow_up_s=0, escalate_if_no_ack=False, rationale="No answer")
    db.add_decision("late", 3, "urgent", [], decision, "template", "test", [], 0)
    db.mark_phase("late", 3)
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not resend after phase three")
    monkeypatch.setattr(api.notify, "dispatch", forbidden)
    asyncio.run(api.fail_open(db.get_incident("late"), RuntimeError("model unavailable")))
    assert db.get_incident("late")["decision"]["title"] == "Needs help"
