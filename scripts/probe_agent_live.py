"""Run the multimodal Evidence Agent against one real frame and save its trace.

Reproduces the live claim in evals/report.md without touching the demo database.
Needs META_API_KEY. The clip itself stays local; one frame is sent for the visual
tool and recorded in the outbound trace.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in (".vendor", "backend"):
    entry = str(ROOT / sub)
    if entry not in sys.path:
        sys.path.insert(0, entry)

import cv2  # noqa: E402

from app.agent import EvidenceAgent  # noqa: E402
from app.meta import MetaClient, SPARK_MODEL  # noqa: E402

INSPECT_SCHEMA = {
    "type": "object",
    "properties": {
        "person_visible": {"type": "boolean"},
        "person_count": {"type": "integer"},
        "posture": {"type": "string",
                    "enum": ["upright", "lying", "sitting", "kneeling",
                             "not_visible", "unclear"]},
        "support_surface": {"type": "string",
                            "enum": ["floor", "bed", "couch", "chair",
                                     "other", "none", "unclear"]},
        "others_visible": {"type": "boolean"},
        "observation": {"type": "string", "maxLength": 200},
        "confidence": {"type": "number"},
    },
    "required": ["person_visible", "person_count", "posture", "support_surface",
                 "others_visible", "observation", "confidence"],
    "additionalProperties": False,
}
INSPECT_PROMPT = (
    "One still from a home-safety camera. Answer only what is visible. Never "
    "identify anyone. Question: {question}"
)


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def extract_frame(clip: Path, at: float, out: Path) -> None:
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps * at))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"could not read {clip} at {at}s")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 84])


async def run(args, client: MetaClient, still: Path, ledger: list[dict]):
    image = still.read_bytes()

    async def memory(_question: str = ""):
        return {"facts": ["Lives alone", "Usually in the living room in the evening"]}

    async def events(_question: str = ""):
        return {"items": [{"type": "fall_suspected",
                           "at": "2026-10-31T20:00:00Z"}]}

    async def inspect(question: str = ""):
        observed, usage = await asyncio.to_thread(
            client.vision, image,
            INSPECT_PROMPT.format(question=question or "What is visible?"),
            INSPECT_SCHEMA, "low")
        ledger.append({"purpose": "agent_frame", "bytes": len(image),
                       "latency_ms": usage.latency_ms,
                       "input_tokens": usage.input_tokens,
                       "output_tokens": usage.output_tokens})
        return {"available": True, **observed}

    packet = {
        "incident": {"type": "fall_suspected", "source": "wall", "zone": None},
        "feed": {"feed_name": args.feed_name},
        "local": {"kind": "possible_fall", "posture": "lying",
                  "support_surface": args.support if args.support != "unknown" else None,
                  "lying_for_s": 2.2, "person_count": 1, "objects": []},
        "sam": {"frames_present": 20, "person_count": 1, "track_unavailable": False},
        "policy": {"floor": "urgent", "floor_reasons": ["fall"]},
        "constraints": {"clip_seconds": 5.0,
                        "pixels": "at most one still from this clip",
                        "do_not_claim": ["identity", "intent", "medical", "safe"]},
    }
    result = await EvidenceAgent(client).run(
        packet=packet,
        tools={"household_memory": memory, "recent_events": events,
               "inspect_frame": inspect},
        verify=True, floor="urgent", effort="low",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", type=Path,
                        default=ROOT / "data/footage/external/gmdcsa24-fall-01.mp4")
    parser.add_argument("--at", type=float, default=6.0)
    parser.add_argument("--feed-name", default="Living room")
    parser.add_argument("--support", default="unknown",
                        choices=["unknown", "floor", "bed", "couch", "chair"])
    parser.add_argument("--out", type=Path,
                        default=ROOT / "evals/agent_live_trace.json")
    parser.add_argument("--frame", type=Path, default=ROOT / ".tmp/agent_probe.jpg")
    args = parser.parse_args()
    load_env()
    if not os.environ.get("META_API_KEY"):
        raise SystemExit("META_API_KEY is not set")
    extract_frame(args.clip, args.at, args.frame)
    client = MetaClient(os.environ["META_API_KEY"])
    ledger: list[dict] = []
    result = asyncio.run(run(args, client, args.frame, ledger))
    try:
        clip_label = str(args.clip.resolve().relative_to(ROOT))
    except ValueError:
        clip_label = str(args.clip)
    output = {
        "clip": clip_label,
        "at_s": args.at,
        "model": SPARK_MODEL,
        "trace": result.trace,
        "observation": next(
            (v for k, v in result.final_packet.get("tool_results", {}).items()
             if k.startswith("inspect_frame")), None),
        "decision": {k: result.raw.get(k) for k in
                     ("verdict", "severity", "notify_now", "title", "body",
                      "evidence", "confidence", "rationale")},
        "latency_ms": result.latency_ms,
        "ledger": ledger,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("decision", "latency_ms")}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
