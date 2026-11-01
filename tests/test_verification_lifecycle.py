"""A held wall candidate must not outlive the verifier that was holding it.

`verifying` is invisible to the enrichment worker. These tests pin the two
ways that used to strand a real fall (an API restart or a dead verification
task), the atomic release that keeps the sweeper and the task from double
paging, and the family list's treatment of a rejected candidate.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import api, db, policy  # noqa: E402


def _packet(alert_id: str = "wa_lifecycle", feed_id: str = "f_life") -> dict:
    alert = {
        "id": alert_id, "kind": "possible_fall", "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 4,
                     "person_count": 1, "lying_for_s": 1.8, "objects": []},
    }
    return api._wall_incident_packet(
        "session_life", {"id": feed_id, "name": "Hallway"}, alert)


def _expire_verification(incident_id: str, age_s: float = 1.0) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE incidents SET verification_due_at=? WHERE id=?",
            (time.time() - age_s, incident_id),
        )
        conn.commit()


def _decision() -> policy.Decision:
    return policy.Decision(
        severity="urgent", notify_now=True, title="Possible fall",
        body="A person is lying in the hallway and has not stood up.",
        evidence=["lying"], actions=[{"id": "call_resident", "label": "Call now"}],
        confidence=0.85, follow_up_s=60, escalate_if_no_ack=True,
        rationale="SAM tracked one person lying with no support.",
    )


def test_stale_verifying_row_is_released_on_the_local_floor(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "stale.db"))
    db.init()
    called: list[tuple[str, str]] = []

    async def fake_phase_one(incident_id):
        called.append(("phase_one", incident_id))

    async def fake_checkin(incident_id):
        called.append(("checkin", incident_id))

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet(), None)
        held = db.get_incident(incident_id)
        assert held["status"] == "verifying"
        assert held["verification_due_at"] > time.time()
        _expire_verification(incident_id)
        return incident_id, await api.sweep_stale_verifications()

    incident_id, released = asyncio.run(scenario())

    assert released == [incident_id]
    inc = db.get_incident(incident_id)
    assert inc["status"] == "detected"
    assert inc["sensors"]["verification"] == "verification_timeout_local_floor"
    assert ("phase_one", incident_id) in called
    assert ("checkin", incident_id) in called
    assert any(e["reason"] == "verification_deadline"
               and e["incident_id"] == incident_id
               for e in db.recent_events(20))


def test_fresh_verifying_row_is_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fresh.db"))
    db.init()
    called: list[str] = []

    async def fake_phase_one(incident_id):
        called.append("phase_one")

    async def fake_checkin(incident_id):
        called.append("checkin")

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_fresh"), None)
        return incident_id, await api.sweep_stale_verifications()

    incident_id, released = asyncio.run(scenario())

    assert released == []
    assert called == []
    assert db.get_incident(incident_id)["status"] == "verifying"


def test_sweep_release_makes_a_late_verifier_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "late.db"))
    db.init()
    pages: list[str] = []

    async def fake_phase_one(incident_id):
        pages.append("phase1")

    async def fake_checkin(incident_id):
        return None

    async def fake_dispatch(alert, phase, broadcast=None):
        pages.append(f"phase{phase}")
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_late"), None)
        _expire_verification(incident_id)
        assert await api.sweep_stale_verifications() == [incident_id]
        # The original verification task finishes after the sweep. It must
        # keep its audit but must not page a second time.
        late = await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=_decision())
        return incident_id, late

    incident_id, late = asyncio.run(scenario())

    assert late is False
    assert pages == ["phase1"]
    inc = db.get_incident(incident_id)
    assert inc["status"] == "detected"
    assert inc["decision"] is None


def test_a_second_release_cannot_double_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "double.db"))
    db.init()
    dispatched: list[tuple[str, int]] = []

    async def fake_checkin(incident_id):
        return None

    async def fake_dispatch(alert, phase, broadcast=None):
        dispatched.append((alert["incident_id"], phase))
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_double"), None)
        first = await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=_decision())
        second = await api._release_wall_incident(
            incident_id, "verification_timeout_local_floor")
        return incident_id, first, second

    incident_id, first, second = asyncio.run(scenario())

    assert first is True
    assert second is False
    assert dispatched == [(incident_id, 2)]
    assert db.get_incident(incident_id)["status"] == "notified"


def test_startup_and_periodic_loop_run_the_verification_sweep(monkeypatch):
    calls: list[str] = []

    async def fake_sweep():
        calls.append("sweep")

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(api, "sweep_stale_verifications", fake_sweep)
    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api.verification_sweep_loop())

    assert calls == ["sweep"]
    startup = inspect.getsource(api.startup)
    assert "verification_sweep_loop()" in startup


def test_rejected_wall_candidate_is_not_a_family_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "rejected.db"))
    db.init()
    db.upsert_incident({
        "incident_id": "inc_regular", "household_id": "hh_demo",
        "camera_id": "cam_hallway", "type": "fall_suspected",
        "status": "notified", "severity_hint": "urgent",
        "started_at": "2026-10-31T20:00:00Z",
        "local_time": "2026-10-31T22:00:00+02:00", "zone_id": None,
        "track": {}, "sensors": {}, "privacy": {}, "evidence": [],
        "source": "edge",
    })

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api.auth, "media_ok", lambda *a, **k: True)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_rejected"), None)
        await api._reject_wall_incident(incident_id, "SAM saw no person")
        return incident_id

    incident_id = asyncio.run(scenario())

    family = api.list_incidents(limit=50)["items"]
    assert incident_id not in {item["id"] for item in family}
    assert "inc_regular" in {item["id"] for item in family}

    # The audit stays available by id and in the event log.
    detail = db.get_incident(incident_id)
    assert detail["status"] == "resolved"
    assert detail["sensors"]["verification"] == "rejected"
    assert detail["sensors"]["verification_reason"] == "SAM saw no person"
    assert any(e["incident_id"] == incident_id
               and str(e["reason"]).startswith("verification_rejected:")
               for e in db.recent_events(20))

    # Detail and media are family routes, so a rejected candidate is hidden
    # there too; the audit remains reachable through db/events only.
    with pytest.raises(api.HTTPException) as detail_route:
        api.get_incident(incident_id)
    assert detail_route.value.status_code == 404

    class _Request:
        cookies: dict = {}
        query_params: dict = {}

    with pytest.raises(api.HTTPException) as media_route:
        api.media(incident_id, "clip.mp4", _Request())
    assert media_route.value.status_code == 404


def test_held_candidate_is_invisible_to_the_family_and_its_media(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "held.db"))
    db.init()
    monkeypatch.setattr(api, "broadcast", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(api.auth, "media_ok", lambda *a, **k: True)

    async def scenario():
        return await api._create_wall_incident(_packet("wa_held"), None)

    incident_id = asyncio.run(scenario())

    assert db.incident_held(incident_id) is True
    assert incident_id not in {i["id"] for i in api.list_incidents(limit=50)["items"]}
    assert all(e.get("reason") != "verification_pending"
               for e in api.events(limit=100)["items"])
    with pytest.raises(api.HTTPException) as detail:
        api.get_incident(incident_id)
    assert detail.value.status_code == 404

    class _Request:
        cookies: dict = {}
        query_params: dict = {}

    with pytest.raises(api.HTTPException) as media:
        api.media(incident_id, "clip.mp4", _Request())
    assert media.value.status_code == 404

    # Claiming the release keeps the row held, with a live lease.
    assert db.claim_verification_release(incident_id, time.time(), 120.0) is True
    held = db.get_incident(incident_id)
    assert held["status"] == "releasing"
    assert held["verification_due_at"] > time.time()
    assert db.incident_held(incident_id) is True
    assert incident_id not in {i["id"] for i in api.list_incidents(limit=50)["items"]}
    assert db.due_verifications(time.time()) == []


def test_expired_release_lease_is_recovered_without_double_paging(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "lease.db"))
    db.init()
    pages: list[str] = []

    async def fake_phase_one(incident_id):
        pages.append("phase_one")

    async def fake_checkin(incident_id):
        return None

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_lease"), None)
        # A process claims the release, then dies before writing a decision.
        assert db.claim_verification_release(incident_id, time.time(), 120.0)
        with db.connect() as conn:
            conn.execute("UPDATE incidents SET verification_due_at=? WHERE id=?",
                         (time.time() - 1.0, incident_id))
            conn.commit()
        recovered = await api.sweep_stale_verifications()
        return incident_id, recovered

    incident_id, recovered = asyncio.run(scenario())

    assert recovered == [incident_id]
    inc = db.get_incident(incident_id)
    assert inc["status"] == "detected"
    assert inc["sensors"]["verification"] == "release_lease_expired_local_floor"
    assert inc["verification_due_at"] is None
    assert pages == ["phase_one"]
    assert any(e["reason"] == "release_lease_expired" for e in db.recent_events(20))


def test_phase_one_finishes_a_claimed_but_unsent_page(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "phase1.db"))
    db.init()
    pages: list[str] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        pages.append(alert["incident_id"])
        db.add_notification(f"ntf_{len(pages)}", alert["incident_id"], "webpush",
                            phase, alert.get("severity"), alert.get("title"),
                            alert.get("body"))
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_phase1"), None)
        # Simulate a crash after phase 1 was claimed but before dispatch.
        assert db.claim_notify_phase(incident_id, 1, time.time() + 60)
        db.set_status(incident_id, "detected")
        await api.phase_one(incident_id)
        await api.phase_one(incident_id)
        return incident_id

    incident_id = asyncio.run(scenario())

    assert pages == [incident_id]


def test_a_stored_decision_is_delivered_after_a_crashed_release(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "lease3.db"))
    db.init()
    dispatched: list[tuple[str, int]] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        dispatched.append((alert["incident_id"], phase))
        return []

    async def fake_start_checkin(incident_id):
        return None

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "start_checkin", fake_start_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_lease3"), None)
        # Decision persisted and phase claimed, then the process died before
        # the page. The retry must deliver exactly that decision.
        assert db.claim_verification_release(incident_id, time.time(), 120.0)
        db.add_decision(incident_id, 2, "urgent", [], _decision(),
                        "muse-spark-1.3", "test", [], 100)
        assert db.claim_notify_phase(incident_id, 2, None)
        with db.connect() as conn:
            conn.execute("UPDATE incidents SET verification_due_at=? WHERE id=?",
                         (time.time() - 1.0, incident_id))
            conn.commit()
        return incident_id, await api.sweep_stale_verifications()

    incident_id, recovered = asyncio.run(scenario())

    assert recovered == [incident_id]
    assert dispatched == [(incident_id, 2)]
    inc = db.get_incident(incident_id)
    assert inc["status"] == "notified"
    assert inc["verification_due_at"] is None
    assert inc["decision"]["title"] == "Possible fall"


def test_release_crash_after_phase_two_claim_is_not_downgraded(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "lease2.db"))
    db.init()
    dispatched: list[int] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        dispatched.append(phase)
        return []

    async def fake_start_checkin(incident_id):
        return None

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "start_checkin", fake_start_checkin)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_lease2"), None)
        # The release claimed phase 2 and stored its decision, then crashed
        # before dispatching. The retry must not page again or downgrade.
        assert db.claim_verification_release(incident_id, time.time(), 120.0)
        assert db.claim_notify_phase(incident_id, 2, None)
        db.set_status(incident_id, "releasing")
        with db.connect() as conn:
            conn.execute("UPDATE incidents SET verification_due_at=? WHERE id=?",
                         (time.time() - 1.0, incident_id))
            conn.commit()
        return incident_id, await api.sweep_stale_verifications()

    incident_id, recovered = asyncio.run(scenario())

    assert recovered == [incident_id]
    inc = db.get_incident(incident_id)
    assert inc["status"] == "notified"
    assert inc["verification_due_at"] is None
    assert dispatched == []
