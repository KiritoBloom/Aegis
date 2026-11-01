"""The decision fixtures must be generated from the current policy floor.

A stale fixture is worse than no fixture: it passed a camera-offline case at
`watch` while the runtime floor was `urgent`, because the runner compares the
model to the fixture's own recorded floor. This test recomputes every floor with
the function the runtime uses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import policy  # noqa: E402

FIXTURES = ROOT / "evals" / "fixtures"


def _profile(resident: dict) -> policy.ResidentProfile:
    care = resident.get("care_profile") or {}
    return policy.ResidentProfile(
        display_name=resident.get("display_name") or "Resident",
        age=resident.get("age"),
        cognitive_condition=care.get("cognitive_condition"),
        wanders=bool(care.get("wanders")),
        uses_mobility_aid=bool(care.get("uses_mobility_aid")),
        anticoagulant=bool(care.get("anticoagulant")),
        lives_alone=bool(care.get("lives_alone")),
        conditions=list(care.get("conditions") or []),
    )


def test_every_fixture_floor_matches_the_runtime_policy():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) == 30
    for path in files:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        packet = fixture["packet"]
        local = str(packet["incident"]["started_at_local"])
        incident = {
            "type": packet["incident"]["type"],
            "zone_id": packet["incident"].get("zone"),
            "source": packet["incident"].get("source") or "live",
            "sensors": packet.get("sensors") or {},
            "track": packet.get("track") or {},
            "local_time": local,
        }
        floor = policy.evaluate(incident, _profile(packet["resident"]),
                                local_hour=int(local[11:13]))
        assert floor.severity == fixture["policy_floor"], path.name
        assert floor.severity == packet["policy"]["floor"], path.name
        minimum = fixture["expect"].get("min_severity", floor.severity)
        assert policy.ORDER[minimum] >= policy.ORDER[floor.severity], path.name
