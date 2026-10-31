"""Run the complete wall gate (SAM + Muse + dispatch) on one external clip.

Creates a held incident from sampled frames, cuts the evidence clip, runs SAM,
the visual Evidence Agent and the release/reject path, and records exactly what
would have reached the family. Notification and check-in side effects are
instrumented, not sent.
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


def sample_frames(clip: Path, start: float, end: float, fps: float = 4.0):
    cap = cv2.VideoCapture(str(clip))
    frames = []
    t = start
    while t <= end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if ok:
            frames.append((1000.0 + t, buf.tobytes()))
        t += 1.0 / fps
    cap.release()
    return frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=7.0)
    parser.add_argument("--feed-name", default="Living room")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slug", default=None)
    args = parser.parse_args()

    load_env()
    if not os.environ.get("META_API_KEY"):
        raise SystemExit("META_API_KEY is not set")
    slug = args.slug or args.clip.stem
    work = ROOT / ".tmp" / "gate" / slug
    work.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_PATH"] = str(work / "aegis.db")
    os.environ["MEDIA_ROOT"] = str(work / "media")
    os.environ["AEGIS_WORKER"] = "off"
    os.environ["CHECKIN_TTS"] = "off"
    from app import api, db  # noqa: PLC0415 - env must be set before DB import

    db.init()

    frames = sample_frames(args.clip, args.start, args.end)
    if len(frames) < 4:
        raise SystemExit("not enough frames")
    incident_id = f"inc_gate_{uuid.uuid4().hex[:10]}"
    clip = api._build_wall_clip(incident_id, frames)
    if clip is None:
        raise SystemExit("clip build failed")

    alert = {
        "id": f"wa_{uuid.uuid4().hex[:8]}", "kind": "possible_fall",
        "severity": "alert",
        "evidence": {"posture": "lying", "support_surface": None, "track_id": 1,
                     "person_count": 1, "lying_for_s": 2.0, "objects": [],
                     "box": None, "distance": "mid"},
    }
    packet = api._wall_incident_packet(
        "probe", {"id": "probe_feed", "name": args.feed_name}, alert)
    packet["incident_id"] = incident_id

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

    async def run():
        await api._create_wall_incident(packet, clip)
        await asyncio.wait_for(
            api._verify_wall_alert("probe", "probe_feed", alert["id"],
                                   incident_id, packet, clip=clip), timeout=150)

    asyncio.run(run())
    incident = db.get_incident(incident_id) or {}
    sam = incident.get("sam") or {}
    output = {
        "clip": str(args.clip),
        "sampled_seconds": [args.start, args.end],
        "incident_id": incident_id,
        "status": incident.get("status"),
        "severity": incident.get("severity"),
        "verification": (incident.get("sensors") or {}).get("verification"),
        "sam": {k: sam.get(k) for k in
                ("person_count", "frames_present", "frames_total", "vertical_drop",
                 "height_ratio", "aspect_start", "aspect_end", "track_unavailable")},
        "agent": incident.get("agent"),
        "decision": incident.get("decision"),
        "dispatch_calls": dispatched,
        "checkin_calls": checkins,
        "media": sorted(p.name for p in (work / "media" / incident_id).glob("*")),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in
                      ("status", "severity", "dispatch_calls", "checkin_calls")}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
