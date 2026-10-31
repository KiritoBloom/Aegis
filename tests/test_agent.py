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

from app.agent import EvidenceAgent, FOLLOWUP_PROMPT, PLAN_PROMPT  # noqa: E402


class FakeMeta:
    def __init__(self) -> None:
        self.calls = []
        self.packets = []

    def decide(self, packet, system, schema, effort="low"):
        self.calls.append(system)
        self.packets.append(packet)
        if system == PLAN_PROMPT:
            return ({"calls": [
                        {"tool": "household_memory", "question": ""},
                        {"tool": "recent_events", "question": ""}],
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

    async def memory(question=""):
        return {"facts": ["Usually in the living room at 20:00"]}

    async def events(question=""):
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
    assert "memory" not in meta.packets[0]
    assert "history" not in meta.packets[0]
    assert [p for p, _, _ in logged] == ["agent_plan", "agent_verify"]
    assert len(meta.calls) == 2


def test_agent_can_request_one_visual_observation_with_its_question():
    class VisualMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            if system == PLAN_PROMPT:
                return ({"calls": [{"tool": "inspect_frame",
                                    "question": "Is the body on the floor or on a couch?"}],
                         "why": "Structured evidence cannot settle the support surface."},
                        SimpleNamespace(output_tokens=7, latency_ms=3,
                                        input_tokens=15, reasoning_tokens=0))
            return ({"verdict": "escalate", "severity": "urgent", "notify_now": True,
                     "title": "Possible fall", "body": "A person is on the floor.",
                     "evidence": ["floor"], "actions": [],
                     "confidence": 0.8, "follow_up_s": 60, "escalate_if_no_ack": True,
                     "rationale": "The still shows the floor."},
                    SimpleNamespace(output_tokens=10, latency_ms=4,
                                    input_tokens=20, reasoning_tokens=0))

    seen = []

    async def inspect(question=""):
        seen.append(question)
        return {"available": True, "person_visible": True, "person_count": 1,
                "posture": "lying", "support_surface": "floor",
                "others_visible": False, "observation": "A person lies on the floor.",
                "confidence": 0.83}

    result = asyncio.run(EvidenceAgent(VisualMeta()).run(
        packet={"local": {"kind": "possible_fall"}},
        tools={"inspect_frame": inspect},
        verify=True,
        floor="urgent",
    ))
    assert seen == ["Is the body on the floor or on a couch?"]
    assert result.final_packet["tool_results"]["inspect_frame"]["support_surface"] == "floor"
    assert result.trace[0]["calls"][0]["tool"] == "inspect_frame"
    assert result.trace[1]["question"].startswith("Is the body")
    assert result.trace[1]["summary"].find("floor") >= 0


def test_agent_asks_a_second_visual_question_after_seeing_the_first():
    class FollowMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            usage = SimpleNamespace(output_tokens=6, latency_ms=3,
                                    input_tokens=14, reasoning_tokens=0)
            if system == PLAN_PROMPT:
                return ({"calls": [{"tool": "inspect_frame",
                                    "question": "Is the person on the floor?"}],
                         "why": "Support surface is unknown."}, usage)
            if system == FOLLOWUP_PROMPT:
                return ({"calls": [{"tool": "inspect_frame",
                                    "question": "Is a couch or bed under the body?"}],
                         "why": "The first answer did not settle what supports the body."}, usage)
            return ({"verdict": "escalate", "severity": "urgent", "notify_now": True,
                     "title": "Possible fall", "body": "A person is on the floor.",
                     "evidence": ["floor"], "actions": [],
                     "confidence": 0.8, "follow_up_s": 60, "escalate_if_no_ack": True,
                     "rationale": "Both visual answers agree."}, usage)

    seen = []

    async def inspect(question=""):
        seen.append(question)
        surface = "floor" if len(seen) == 2 else "unclear"
        return {"available": True, "person_visible": True, "person_count": 1,
                "posture": "lying", "support_surface": surface,
                "others_visible": False, "observation": "A body on the floor.",
                "confidence": 0.8}

    result = asyncio.run(EvidenceAgent(FollowMeta()).run(
        packet={"local": {"kind": "possible_fall"}},
        tools={"inspect_frame": inspect},
        verify=True,
        floor="urgent",
    ))
    assert seen == ["Is the person on the floor?",
                    "Is a couch or bed under the body?"]
    assert result.final_packet["tool_results"]["inspect_frame_2"]["support_surface"] == "floor"
    follow = [s for s in result.trace if s.get("follow_up")]
    assert follow and follow[0]["calls"][0]["tool"] == "inspect_frame"
    assert sum(1 for s in result.trace if s.get("tool") == "inspect_frame") == 2


def test_agent_can_finish_without_tools(monkeypatch):
    class NoToolMeta(FakeMeta):
        def decide(self, packet, system, schema, effort="low"):
            if system == PLAN_PROMPT:
                return ({"calls": [], "why": "The packet is sufficient."},
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
