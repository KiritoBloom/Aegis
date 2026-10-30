"""The Evidence Agent must investigate, not just narrate.

The planning call chooses read-only tools; the final call sees their structured
results; the trace records both. These tests use a fake Spark client so the
orchestration is deterministic.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app.agent import EvidenceAgent, PLAN_PROMPT  # noqa: E402


class FakeMeta:
    def __init__(self) -> None:
        self.calls = []
        self.packets = []

    def decide(self, packet, system, schema, effort="low"):
        self.calls.append(system)
        self.packets.append(packet)
        if system == PLAN_PROMPT:
            return ({"tools": ["household_memory", "recent_events"],
                     "why": "Check what is normal and whether this repeats."},
                    SimpleNamespace(output_tokens=10, latency_ms=4,
                                    input_tokens=20, reasoning_tokens=0))
        return ({"verdict": "escalate", "severity": "urgent", "notify_now": True,
                 "title": "Possible fall", "body": "A person is lying in the bedroom.",
                 "evidence": ["lying"], "actions": [{"id": "call_resident", "label": "Call now"}],
                 "confidence": 0.8, "follow_up_s": 60, "escalate_if_no_ack": True,
                 "rationale": "SAM saw one person lying; memory has no expected visitor."},
                SimpleNamespace(output_tokens=20, latency_ms=8,
                                input_tokens=40, reasoning_tokens=0))


def test_agent_uses_tools_and_passes_their_results_to_the_final_decision(monkeypatch):
    meta = FakeMeta()
    logged = []

    async def memory():
        return {"facts": ["Usually in the living room at 20:00"]}

    async def events():
        return {"items": [{"type": "door_open", "zone": "front_door"}]}

    async def log_call(purpose, nbytes, usage):
        logged.append((purpose, nbytes, usage.output_tokens))

    result = asyncio.run(EvidenceAgent(meta).run(
        packet={"local": {"kind": "possible_fall"}, "policy": {"floor": "urgent"}},
        tools={"household_memory": memory, "recent_events": events,
               "audio_context": memory},
        verify=True,
        floor="urgent",
        log_call=log_call,
    ))

    assert result.raw["verdict"] == "escalate"
    assert [s["step"] for s in result.trace] == ["plan", "tool", "tool", "decision"]
    assert [s.get("tool") for s in result.trace if s["step"] == "tool"] == [
        "household_memory", "recent_events"]
    assert result.final_packet["tool_results"]["household_memory"]["facts"]
    assert result.final_packet["tool_results"]["recent_events"]["items"]
    assert [p for p, _, _ in logged] == ["agent_plan", "agent_verify"]
    assert len(meta.calls) == 2


def test_agent_can_finish_without_tools(monkeypatch):
    class NoToolMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            if system == PLAN_PROMPT:
                return ({"tools": [], "why": "The packet is sufficient."},
                        SimpleNamespace(output_tokens=5, latency_ms=2,
                                        input_tokens=10, reasoning_tokens=0))
            return ({"verdict": "reject", "severity": "watch", "notify_now": False,
                     "title": "Not corroborated", "body": "",
                     "evidence": [], "actions": [], "confidence": 0.9,
                     "follow_up_s": 0, "escalate_if_no_ack": False,
                     "rationale": "SAM saw no person."},
                    SimpleNamespace(output_tokens=8, latency_ms=3,
                                    input_tokens=12, reasoning_tokens=0))

    result = asyncio.run(EvidenceAgent(NoToolMeta()).run(
        packet={"local": {"kind": "possible_fall"}},
        tools={},
        verify=True,
        floor="urgent",
    ))
    assert result.raw["verdict"] == "reject"
    assert [s["step"] for s in result.trace] == ["plan", "decision"]
