"""Replay a real clip through the whole pipeline and print what happened.

Not a unit test: it posts to the running API, uploads real bytes, and waits for
both alert phases. Used to produce the numbers quoted in the README and the
eval report, so it prints facts, not intentions.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

TZ = timezone(timedelta(hours=3))  # Africa/Cairo, matching HOUSEHOLD_TZ

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"


def incident_payload(itype: str, zone: str, *, night: bool = True,
                     incident_id: str | None = None) -> dict:
    """Build an edge-shaped incident.

    Everything here is derived from the type and the clock rather than written
    by hand, because a replay that claims `night_hours` at two in the afternoon
    puts a false fact on the alert page and into the model's packet.
    """
    now = datetime.now(TZ)
    hour = now.hour
    is_night = hour >= 22 or hour < 6
    door_open = itype in ("door_open", "door_left_open")
    dwell = 6.8
    track = {"track_id": 1, "primary": True, "dwell_in_zone_s": dwell,
             "dwell_in_door_zone_s": dwell if "door" in zone else 0.0,
             "inside_zone": True, "night": is_night, "posture": "upright",
             "person_count": 1, "path_length": 0.42,
             "crossed_door_line": door_open,
             "absent_s": 420 if itype == "stove_unattended" else 0}
    sensors = {"door_contact": "open" if door_open else "closed",
               "stove_power_w": 1450 if itype == "stove_unattended" else 0,
               "camera_fps": 15, "night": is_night}

    # Evidence is a restatement of the payload, so it is built from the payload.
    evidence = [f"type:{itype}", f"zone:{zone}"]
    if dwell:
        evidence.append(f"dwell_{dwell:g}s")
    if door_open:
        evidence.append("door_contact:open")
    if is_night:
        evidence.append("night_hours")
    if track["absent_s"]:
        evidence.append(f"absent_{track['absent_s']}s")

    return {
        "incident_id": incident_id or f"inc_{uuid.uuid4().hex[:16]}",
        "household_id": "hh_demo",
        "camera_id": "cam_hallway",
        "type": itype,
        "status": "detected",
        "started_at": now.isoformat(),
        "local_time": now.isoformat(),
        "zone_id": zone,
        "severity_hint": "urgent",
        "track": track,
        "sensors": sensors,
        "privacy": {"masks_applied": ["bedroom", "bathroom"], "audio": False,
                    "raw_video_left_home": False},
        "evidence": evidence,
        "source": "replay",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="door_open")
    ap.add_argument("--zone", default="front_door")
    ap.add_argument("--clip", default=str(ROOT / "data/footage/stock/door_exit.mp4"))
    ap.add_argument("--no-clip", action="store_true")
    ap.add_argument("--wait", type=float, default=75.0)
    args = ap.parse_args()

    payload = incident_payload(args.type, args.zone)
    iid = payload["incident_id"]
    with httpx.Client(timeout=90.0) as c:
        t0 = time.time()
        r = c.post(f"{BASE}/api/ingest/incident", json=payload)
        r.raise_for_status()
        print(f"ingest   {r.status_code} floor={r.json().get('floor')} in {time.time()-t0:.0f}ms")

        # phase 1 must exist before the clip even arrives: that is the whole point
        time.sleep(1.5)
        p1 = c.get(f"{BASE}/api/incidents/{iid}").json()
        notes = [n for n in p1.get("notifications", []) if n["phase"] == 1]
        print(f"phase1   {len(notes)} notification rows, "
              f"{sum(1 for n in notes if n['status']=='sent')} sent")

        clip = Path(args.clip)
        if not args.no_clip and clip.exists():
            t1 = time.time()
            with clip.open("rb") as fh:
                r = c.post(f"{BASE}/api/ingest/clip",
                           data={"incident_id": iid},
                           files={"clip": (clip.name, fh, "video/mp4")})
            print(f"clip     {r.status_code} {clip.stat().st_size//1024} KB "
                  f"in {(time.time()-t1)*1000:.0f}ms")

        deadline = time.time() + args.wait
        final = None
        while time.time() < deadline:
            inc = c.get(f"{BASE}/api/incidents/{iid}").json()
            if inc.get("decision") and inc["decision"].get("phase") == 2:
                final = inc
                break
            time.sleep(2.0)

        if not final:
            print("phase2   TIMEOUT")
            return 1

        d = final["decision"]
        print(f"phase2   {d['severity']} in {d['latency_ms']}ms via {d['model']}")
        print(f"  title  {d['title']}")
        print(f"  body   {d['body']}")
        print(f"  why    {d.get('rationale','')[:200]}")
        print(f"  facts  {d.get('evidence')}")
        print(f"  viol   {d.get('violations')}")
        print(f"  raised {d['severity'] != d['policy_floor']} "
              f"(floor {d['policy_floor']})")
        sam = final.get("sam") or {}
        print(f"  sam    frames={sam.get('frames_present')}/{sam.get('frames_total')} "
              f"people={sam.get('person_count')} "
              f"unavailable={sam.get('track_unavailable')}")
        print(f"id       {iid}")
        try:
            (Path(__file__).resolve().parent.parent / ".tmp" / "last_id").write_text(
                iid, encoding="utf-8")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
