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
