"""The resident hears the question before the family hears anything.

A verified fall used to page the family immediately. It now passes through a
`resident_check` gate: the decision is stored, the resident is asked, and only
a silent or unhelpful answer pages. These tests pin the two halves of that
promise — the family is never paged while the gate is open, and a gate that
nobody can answer still pages.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import api, db, policy  # noqa: E402


def _packet(alert_id: str = "wa_gate") -> dict:
    alert = {
        "id": alert_id, "kind": "possible_fall", "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 7,
                     "person_count": 1, "lying_for_s": 1.4, "objects": []},
    }
    return api._wall_incident_packet(
        "session_gate", {"id": "f_gate", "name": "Bedroom"}, alert)


def _decision() -> policy.Decision:
    return policy.Decision(
        severity="urgent", notify_now=True, title="Possible fall",
        body="A person is lying in the bedroom and has not stood up.",
        evidence=["lying"], actions=[{"id": "call_resident", "label": "Call now"}],
        confidence=0.85, follow_up_s=60, escalate_if_no_ack=True,
        rationale="SAM tracked one person lying with no support.",
    )


def _row(incident_id: str, status: str = "verifying") -> dict:
    """A minimal incident row, as `db.upsert_incident` expects it."""
    return {
        "incident_id": incident_id,
        "household_id": "hh_demo",
        "camera_id": "cam_wall",
        "type": "fall_suspected",
        "status": status,
        "severity_hint": "urgent",
        "started_at": "2026-11-03T11:00:00+00:00",
        "local_time": "2026-11-03T13:00:00+02:00",
        "zone_id": None,
        "track": {},
        "sensors": {},
        "privacy": {},
        "evidence": [],
        "source": "wall",
    }


def _setup(monkeypatch, tmp_path, name: str = "gate.db"):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / name))
    db.init()


def _page(incident_id: str) -> bool:
    """Drive the gate the way the check-in timer does."""
    async def scenario():
        return await api.page_resident_check(incident_id)

    return asyncio.run(scenario())


def test_a_verified_fall_is_invisible_until_the_resident_is_asked(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    paged: list[tuple[int, str]] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        paged.append((phase, alert["title"]))
        return []

    async def fake_broadcast(payload):
        return None

    async def fake_checkin(incident_id, *_args, **_kwargs):
        return {"prompt": "Are you okay?", "audio_url": None,
                "due_at": time.time() + 30, "timeout_s": 30}

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet(), None)
        won = await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=_decision())
        return incident_id, won

    incident_id, won = asyncio.run(scenario())

    assert won is True
    incident = db.get_incident(incident_id)
    assert incident["status"] == "resident_check"
    # The check-in owns the first deadline; the gate keeps a later crash backstop.
    assert incident["verification_due_at"] > time.time() + 30
    assert incident["checkin_status"] in (None, "asking")
    # Nobody has been told yet, and the verified copy is already durable.
    assert paged == []
    assert db.notifications_for(incident_id) == []
    # The verified decision is durable before anyone is asked, and the
    # family-facing copy is already the fall copy the page will carry.
    assert incident["decision"]["title"] == "Margaret may have fallen"


def test_a_resident_check_row_is_hidden_from_the_family(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert db.hold_for_resident("inc_gate", "urgent", time.time() + 30) is False

    db.upsert_incident(_row("inc_gate"))
    assert db.hold_for_resident("inc_gate", "urgent", time.time() + 30) is True
    assert db.hidden_from_family(db.get_incident("inc_gate")) is True
    assert [i["id"] for i in db.list_incidents(20)] == []


def test_silence_at_the_gate_pages_the_stored_decision(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    paged: list[tuple[int, str]] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        paged.append((phase, alert["title"]))
        return []

    async def fake_broadcast(payload):
        return None

    async def fake_checkin(incident_id, *_args, **_kwargs):
        return {"prompt": "Are you okay?", "audio_url": None,
                "due_at": time.time() + 30, "timeout_s": 30}

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet(), None)
        await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=_decision())
        return incident_id

    incident_id = asyncio.run(scenario())
    assert _page(incident_id) is True

    assert paged == [(2, "Margaret may have fallen")]
    incident = db.get_incident(incident_id)
    assert incident["status"] == "notified"
    assert incident["verification_due_at"] is None


def test_the_gate_pages_once(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "once.db")
    paged: list[int] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        paged.append(phase)
        return []

    async def fake_broadcast(payload):
        return None

    async def fake_checkin(incident_id, *_args, **_kwargs):
        return {"prompt": "Are you okay?", "audio_url": None,
                "due_at": time.time() + 30, "timeout_s": 30}

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "start_checkin", fake_checkin)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_once"), None)
        await api._release_wall_incident(
            incident_id, "sam_muse_verified", decision=_decision())
        return incident_id

    incident_id = asyncio.run(scenario())
    assert _page(incident_id) is True
    # A second sweep pass finds nothing left to do.
    assert _page(incident_id) is False
    assert paged == [2]


def test_an_expired_gate_deadline_is_due_for_the_sweep(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "due.db")
    db.upsert_incident(_row("inc_due"))
    assert db.hold_for_resident("inc_due", "urgent", time.time() - 1.0)
    assert [r["id"] for r in db.due_verifications(time.time())] == ["inc_due"]


def test_a_late_write_cannot_promote_a_gated_row(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "promote.db")
    db.upsert_incident(_row("inc_promote"))
    assert db.hold_for_resident("inc_promote", "urgent", time.time() + 30)
    # The enrichment worker and phase one both write status. Neither may take
    # the row out of the gate: only an answer or the gate sweep may.
    db.set_status("inc_promote", "detected")
    db.set_status("inc_promote", "enriching")
    assert db.get_incident("inc_promote")["status"] == "resident_check"
    db.set_status("inc_promote", "resolved")
    assert db.get_incident("inc_promote")["status"] == "resolved"


def test_phase_one_will_not_page_under_an_open_gate(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "phase1.db")
    paged: list[int] = []

    async def fake_dispatch(alert, phase, broadcast=None):
        paged.append(phase)
        return []

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api.notify, "dispatch", fake_dispatch)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        incident_id = await api._create_wall_incident(_packet("wa_p1"), None)
        assert db.hold_for_resident(incident_id, "urgent", time.time() + 30)
        await api.phase_one(incident_id)
        return incident_id

    incident_id = asyncio.run(scenario())
    assert paged == []
    assert db.get_incident(incident_id)["status"] == "resident_check"
