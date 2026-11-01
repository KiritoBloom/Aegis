"""Prove Spark's check-in reading on cases the word list misses.

The five-case ASR suite tests the deterministic classifier. This probe tests the
model call that runs at runtime: two transcripts that the word list classifies
as `other` or `affirmative`, with a resident on the floor, handed to
`muse-spark-1.3` under the production check-in prompt and schema.

    python scripts/probe_checkin_read.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / ".vendor"), str(ROOT / "backend")]

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

from app import policy  # noqa: E402
from app.meta import SPARK_MODEL, MetaClient  # noqa: E402
from app.prompt import CHECKIN_READ_PROMPT, CHECKIN_READ_SCHEMA  # noqa: E402

CASES = [
    ("other", "I slipped and I'm on the floor, my hip is sore.",
     "The word list has no help token; the model must surface the fall."),
    ("affirmative", "I'm okay, I just can't catch my breath.",
     "Says okay, but the second clause is distress the list does not know."),
]


def main() -> None:
    if not os.environ.get("META_API_KEY"):
        raise SystemExit("META_API_KEY is not set")
    client = MetaClient(os.environ["META_API_KEY"])
    rows = []
    for expected, transcript, why in CASES:
        classifier = policy.classify_transcript(transcript)
        context = {
            "incident": {"type": "fall_suspected", "zone": "hallway",
                         "question": "Margaret, it's Aegis. I noticed a fall. Are you okay?",
                         "started_at_local": datetime.now(timezone.utc).isoformat()},
            "resident": {"display_name": "Margaret"},
            "transcript": transcript,
            "classifier_class": classifier,
            "perception": {
                "posture": "lying", "person_count": 1, "on_floor": True,
                "still_s": 18.0, "no_recovery_s": 18.0, "recovered": False,
                "track_available": True, "objects": [], "support_surface": None,
            },
            "audio": {}, "media": {},
        }
        started = time.time()
        raw, usage = client.decide(context, CHECKIN_READ_PROMPT,
                                   CHECKIN_READ_SCHEMA, "low")
        read = {
            "class": str(raw.get("class") or "other"),
            "read": str(raw.get("read") or "")[:200],
            "evidence": list(raw.get("evidence") or [])[:8],
            "confidence": raw.get("confidence"),
            "latency_ms": int((time.time() - started) * 1000),
        }
        merged = classifier
        if classifier == "help" or read["class"] == "help":
            merged = "help"
        elif classifier == "affirmative" and read["class"] in ("confused", "other"):
            merged = read["class"]
        elif classifier in ("confused", "other") and read["class"] == "affirmative":
            merged = classifier
        rows.append({
            "case": expected,
            "transcript": transcript,
            "why": why,
            "classifier_class": classifier,
            "model_read": read,
            "merged_class": merged,
            "model_lift": merged != classifier,
            "ok": merged == "help",
        })
        print(f"{expected:<12} rules={classifier:<12} model={read['class']:<12} "
              f"merged={merged:<12} ok={rows[-1]['ok']}")
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True,
                                 cwd=ROOT).stdout.strip(),
        "model": SPARK_MODEL,
        "prompt": "CHECKIN_READ_PROMPT",
        "cases": rows,
        "passed": sum(1 for r in rows if r["ok"]),
        "total": len(rows),
    }
    out = ROOT / "evals" / "checkin_read.json"
    out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if output["passed"] != output["total"]:
        raise SystemExit("check-in read probe failed")


if __name__ == "__main__":
    main()
