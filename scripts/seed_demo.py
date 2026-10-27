"""Build a coherent seven-day history for the dashboard.

The restraint counter is the product: "38 events seen / 3 alerts sent / 35
stayed home". That number is only worth showing if the denominator is real, so
this writes genuine `events` rows — one per moment the edge would have noticed
— and lets the alerts be produced by the actual pipeline afterwards.

What this does NOT do is fabricate decisions. Suppressed events carry the
deterministic policy reason that suppressed them, which is checkable by
re-running `policy.evaluate` on the same inputs. Alerts come from
`scripts/replay.py` against real CCTV clips, so every alert on the dashboard has
a real clip, a real SAM pass and a real model decision behind it.

Run:  python scripts/seed_demo.py [--days 7] [--reset]
Then: python scripts/replay.py ...   (to create the real alerts)
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("MEDIA_ROOT", str(ROOT / "data" / "media"))

from app import db, policy  # noqa: E402

TZ = timezone(timedelta(hours=3))  # Africa/Cairo, matching HOUSEHOLD_TZ

PROFILE = policy.ResidentProfile()

# A week of an 81-year-old with dementia who lives alone. Times are local.
# (hour, minute, type, zone, note, extra) — `extra` becomes the sensor payload,
# and the note becomes the suppression reason after the policy floor is
# evaluated, so nothing here is asserted twice.
DAY: list[tuple[int, int, str, str, str, dict]] = [
    (6, 41, "motion_only", "hallway", "first movement of the morning", {}),
    (6, 58, "door_approach", "bathroom_door", "morning wash", {}),
    (7, 22, "stove_unattended", "kitchen", "kettle, she stayed in the room", {}),
    (8, 5, "motion_only", "living_room", "sitting down with the radio", {}),
    (9, 12, "person_unwell_still", "living_room", "resting in her chair", {}),
    (10, 34, "door_approach", "front_door", "checked the post", {}),
    (11, 2, "motion_only", "hallway", "walking to the kitchen", {}),
    # The one that mattered: she put lunch on and went to sit down.
    (11, 40, "stove_unattended", "kitchen", "left the kitchen with the hob on",
     {"absent_s": 420}),
    (13, 15, "person_unwell_still", "living_room", "afternoon nap", {}),
    (14, 48, "door_approach", "front_door", "looking out at the street", {}),
    (15, 30, "motion_only", "hallway", "moving between rooms", {}),
    (16, 10, "pet_motion", "hallway", "the neighbour's cat at the door", {}),
    (17, 5, "stove_unattended", "kitchen", "tea, she stayed in the room", {}),
    (18, 22, "person_unwell_still", "living_room", "evening television", {}),
    (19, 50, "motion_only", "prayer_room", "evening prayer", {}),
    (20, 35, "door_approach", "bathroom_door", "evening wash", {}),
    (21, 40, "motion_only", "living_room", "tidying before bed", {}),
]

NIGHT: list[tuple[int, int, str, str, str, dict]] = [
    (2, 14, "door_approach", "bathroom_door", "bathroom trip", {}),
    (3, 12, "door_approach", "front_door",
     "stood at the front door, did not open it", {}),
    (5, 10, "motion_only", "prayer_room", "early prayer", {}),
]

# One night she did open it. Two nights later, the same approach stopped at
# the door. The difference between those two nights is the whole product.
NIGHT_DOOR_OPEN_ON = 4  # days back from today

# Deliberately sparse: most nights have one bathroom trip, some have a door.
NIGHT_PATTERN = [1, 1, 2, 1, 0, 1, 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--reset", action="store_true",
                    help="delete existing events first (not incidents or media)")
    args = ap.parse_args()

    db.init()
    if args.reset:
        with db.connect() as conn:
            conn.execute("DELETE FROM events")

    rng = random.Random(7)  # a stable week, so screenshots match a rerun
    now = datetime.now(TZ)
    written = 0

    for back in range(args.days - 1, -1, -1):
        day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0,
                                                  microsecond=0)
        rows = [(h, m, t, z, n, x) for h, m, t, z, n, x in DAY]
        rows += [(h, m, t, z, n, x)
                 for h, m, t, z, n, x in NIGHT[:NIGHT_PATTERN[back % 7]]]

        for hour, minute, etype, zone, note, extra in sorted(rows):
            ts = day.replace(hour=hour, minute=minute) + timedelta(
                seconds=rng.randint(0, 50))
            if ts > now:
                continue
            local_hour = ts.hour
            sensors = dict(extra)
            # The night she actually opened the door. Everything else in that
            # hour is an approach that stopped.
            if (back == NIGHT_DOOR_OPEN_ON and hour == 3
                    and zone == "front_door"):
                etype = "door_open"
                sensors["door_contact"] = "open"
                note = "front door opened, she went out"
            incident = {"type": etype, "zone_id": zone,
                        "local_time": ts.isoformat(),
                        "track": {"person_count": 1, "dwell_in_door_zone_s": 4.0,
                                  **extra},
                        "sensors": sensors}
            floor = policy.evaluate(incident, PROFILE, local_hour=local_hour)
            suppressed = not floor.notifies
            reason = ",".join(floor.reasons) if floor.reasons else note
            db.record_event(
                f"seed_{ts.strftime('%Y%m%d%H%M')}_{etype}",
                "cam_hallway", etype, zone, ts.isoformat(), floor.severity,
                suppressed=suppressed, reason=reason, source="replay")
            written += 1

    counts = db.event_counts(days=7)
    print(f"wrote {written} events across {args.days} days")
    print(f"  seen {counts['seen']} · escalated {counts['alerts']} · "
          f"stayed home {counts['stayed_home']}")
    print("  by type:", ", ".join(f"{r['name']} {r['count']}" for r in counts["by_type"]))
    print("  by zone:", ", ".join(f"{r['name']} {r['count']}" for r in counts["by_zone"]))
    print("\nAlerts are not seeded. Run scripts/replay.py against a real clip so "
          "every alert has footage behind it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
