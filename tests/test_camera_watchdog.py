"""A camera that stops checking in must become an incident, not just a rule.

The edge heartbeats every ten seconds. These tests pin the producer behind the
`camera_offline` floor: only a camera that has connected before is watched, one
outage pages once, and a camera that never connected is not a false alarm.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import api, db  # noqa: E402


def _set_last_heartbeat(camera_id: str, ago_s: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S",
                          time.gmtime(time.time() - ago_s)) + "Z"
    with db.connect() as conn:
        conn.execute("UPDATE cameras SET last_heartbeat_at=?, status='online'"
                     " WHERE id=?", (stamp, camera_id))
        conn.commit()
    return stamp


def _camera(camera_id: str = "cam_hallway") -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id=?", (camera_id,)).fetchone()
    return dict(row)


def test_a_stale_camera_creates_one_urgent_incident_per_outage(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watchdog.db"))
    db.init()
    pages: list[str] = []

    async def fake_phase_one(incident_id):
        pages.append(incident_id)

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        _set_last_heartbeat("cam_hallway", 600)
        first = await api.sweep_camera_heartbeats()
        await asyncio.sleep(0)
        second = await api.sweep_camera_heartbeats()
        return first, second

    first, second = asyncio.run(scenario())

    assert len(first) == 1
    assert second == []
    assert pages == first
    incident = db.get_incident(first[0])
    assert incident["type"] == "camera_offline"
    assert incident["status"] == "detected"
    assert incident["severity"] == "urgent"
    assert incident["sensors"]["camera_state"] == "offline"
    assert incident["sensors"]["offline_for_s"] >= 500
    assert incident["source"] == "watchdog"
    assert _camera()["status"] == "offline"
    assert any(e["incident_id"] == first[0] and e["reason"] == "heartbeat_missing"
               for e in db.recent_events(20))
    # The camera offline incident is a family incident, not a held candidate.
    assert first[0] in {i["id"] for i in api.list_incidents(limit=50)["items"]}


def test_a_reconnecting_camera_can_lose_coverage_again(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watchdog2.db"))
    db.init()

    async def fake_phase_one(incident_id):
        return None

    async def fake_broadcast(payload):
        return None

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)

    async def scenario():
        _set_last_heartbeat("cam_hallway", 600)
        first = await api.sweep_camera_heartbeats()
        await asyncio.sleep(0)
        db.add_heartbeat({"camera_id": "cam_hallway", "camera_state": "online",
                          "detector": "yolo", "fps": 15})
        db.resolve_camera_outages("cam_hallway")
        reconnected = await api.sweep_camera_heartbeats()
        _set_last_heartbeat("cam_hallway", 900)
        second = await api.sweep_camera_heartbeats()
        await asyncio.sleep(0)
        return first, reconnected, second

    first, reconnected, second = asyncio.run(scenario())

    assert len(first) == 1
    assert reconnected == []
    assert len(second) == 1
    assert second[0] != first[0]


def test_a_reconnecting_heartbeat_closes_the_outage_incident(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watchdog4.db"))
    db.init()
    recovered: list[dict] = []

    async def fake_phase_one(incident_id):
        return None

    async def fake_broadcast(payload):
        if payload.get("type") == "incident.recovered":
            recovered.append(payload)

    monkeypatch.setattr(api, "phase_one", fake_phase_one)
    monkeypatch.setattr(api, "broadcast", fake_broadcast)
    monkeypatch.setattr(api, "verify_edge", lambda *a, **k: None)

    async def scenario():
        _set_last_heartbeat("cam_hallway", 600)
        outage = await api.sweep_camera_heartbeats()
        await asyncio.sleep(0)
        body = json.dumps({"camera_id": "cam_hallway",
                           "camera_state": "online", "fps": 15}).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "headers": [],
                           "client": ("127.0.0.1", 0)}, receive)
        await api.ingest_heartbeat(request, None, None)
        return outage

    outage = asyncio.run(scenario())

    assert len(outage) == 1
    incident = db.get_incident(outage[0])
    assert incident["status"] == "resolved"
    assert incident["escalation_due_at"] is None
    assert recovered and recovered[0]["id"] == outage[0]
    assert any(e["incident_id"] == outage[0] and e["reason"] == "camera_online"
               for e in db.recent_events(20))


def test_a_camera_that_never_connected_is_not_an_outage(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watchdog3.db"))
    db.init()
    with db.connect() as conn:
        conn.execute("UPDATE cameras SET last_heartbeat_at=NULL WHERE id='cam_hallway'")
        conn.commit()

    async def scenario():
        return await api.sweep_camera_heartbeats()

    assert asyncio.run(scenario()) == []
    assert _camera()["status"] == "online"
