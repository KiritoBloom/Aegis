"""Run the full SAM + Muse gate on every external pilot clip with a trigger.

Reads evals/fall_pilot.json, cuts the same 2 fps candidate windows, and records
the family-facing outcome (dispatch calls) for each clip. Notification and
check-in side effects are instrumented, not sent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in (".vendor", "backend"):
    entry = str(ROOT / sub)
    if entry not in sys.path:
        sys.path.insert(0, entry)

import cv2  # noqa: E402



def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def frames(clip: Path, start: float, end: float, fps: float = 4.0):
    cap = cv2.VideoCapture(str(clip))
    out = []
    t = start
    while t <= end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if ok:
            out.append((1000.0 + t, buf.tobytes()))
        t += 1.0 / fps
    cap.release()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", type=Path, default=ROOT / "evals/fall_pilot.json")
    parser.add_argument("--out", type=Path, default=ROOT / "evals/gate_pilot.json")
    parser.add_argument("--only", default="",
                        help="comma-separated clip-name substrings to run")
    args = parser.parse_args()
    load_env()
    if not os.environ.get("META_API_KEY"):
        raise SystemExit("META_API_KEY is not set")

    pilot = json.loads(args.pilot.read_text(encoding="utf-8"))
    work = ROOT / ".tmp" / "gate" / "pilot"
    work.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_PATH"] = str(work / "aegis.db")
    os.environ["MEDIA_ROOT"] = str(work / "media")
    os.environ["AEGIS_WORKER"] = "off"
    os.environ["CHECKIN_TTS"] = "off"
    from app import api, db  # noqa: PLC0415 - env must be set before DB import

    db.init()

    dispatched: list[dict] = []
    checkins: list[str] = []

    async def fake_dispatch(payload, phase, broadcast=None):
        dispatched.append({"phase": phase, "title": payload.get("title"),
                           "severity": payload.get("severity")})
        return []

    async def fake_checkin(iid: str):
        checkins.append(iid)

    async def fake_broadcast(_payload):
        return None

    api.notify.dispatch = fake_dispatch
    api.start_checkin = fake_checkin
    api.broadcast = fake_broadcast

    results = []
    only = [part.strip() for part in args.only.split(",") if part.strip()]
    for item in pilot["results"]:
        if only and not any(token in item["clip"] for token in only):
            continue
        if not item["triggers"]:
            results.append({**item, "gate": "no_candidate", "dispatch_calls": []})
            continue
        clip_path = ROOT / "data/footage/external" / item["clip"]
        trigger = item["triggers"][0]["at_native_s"]
        start = max(0.0, trigger - 3.0)
        end = trigger + 4.0
        sampled = frames(clip_path, start, end)
        incident_id = f"inc_gate_{uuid.uuid4().hex[:10]}"
        clip = api._build_wall_clip(incident_id, sampled)
        if clip is None:
            results.append({**item, "gate": "clip_failed", "dispatch_calls": []})
            continue
        trigger_evidence = item["triggers"][0]
        alert = {
            "id": f"wa_{uuid.uuid4().hex[:8]}", "kind": "possible_fall",
            "severity": "alert",
            "evidence": {"posture": trigger_evidence.get("posture") or "lying",
                         "support_surface": trigger_evidence.get("support_surface"),
                         "geometry_fall": bool(trigger_evidence.get("geometry_fall")),
                         "box_aspect": trigger_evidence.get("box_aspect"),
                         "track_id": trigger_evidence.get("track_id") or 1,
                         "person_count": trigger_evidence.get("person_count") or 1,
                         "lying_for_s": trigger_evidence.get("lying_for_s") or 2.0,
                         "objects": [], "box": trigger_evidence.get("box"),
                         "distance": trigger_evidence.get("distance")},
        }
        packet = api._wall_incident_packet(
            "pilot", {"id": "pilot_feed", "name": item["clip"]}, alert)
        packet["incident_id"] = incident_id
        dispatched.clear()
        checkins.clear()
        try:
            async def run():
                await api._create_wall_incident(packet, clip)
                await asyncio.wait_for(
                    api._verify_wall_alert("pilot", "pilot_feed", alert["id"],
                                           incident_id, packet, clip=clip),
                    timeout=150)
            asyncio.run(run())
            incident = db.get_incident(incident_id) or {}
            verdict = next((s.get("verdict") for s in reversed(incident.get("agent") or [])
                            if s.get("verdict")), None)
            results.append({
                **item, "gate": verdict or "unknown",
                "status": incident.get("status"),
                "dispatch_calls": list(dispatched),
                "checkin_calls": list(checkins),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({**item, "gate": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                            "dispatch_calls": list(dispatched)})
        print(f"{item['clip']}: {results[-1]['gate']} "
              f"dispatches={len(results[-1]['dispatch_calls'])}", flush=True)

    falls = [r for r in results if "fall" in r["clip"].lower()]
    adls = [r for r in results if "fall" not in r["clip"].lower()]
    summary = {
        "fall_clips": len(falls),
        "fall_alerts": sum(1 for r in falls if r["dispatch_calls"]),
        "adl_clips": len(adls),
        "adl_alerts": sum(1 for r in adls if r["dispatch_calls"]),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
