"""Zones can be switched off, but never into silence about a fall.

The areas screen in onboarding asks which rooms to watch. That answer has to
reach the floor, and it has to be bounded the same way the class modes are: an
area can stop producing chatter, it cannot stop producing a fall alarm.

Run: python tests/test_zones.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import policy, watch  # noqa: E402

PROFILE = policy.ResidentProfile()


def _incident(itype: str, zone: str, **sensors) -> dict:
    return {"id": "inc_t", "type": itype, "zone_id": zone, "sensors": sensors,
            "track": {}, "local_time": "2026-11-02T14:10:00+02:00",
            "started_at": "2026-11-02T12:10:00Z"}


def test_an_unwatched_area_mutes_ordinary_events_there():
    plan = watch.normalise({"zones": {"hallway": False}})
    inc = _incident("person_unwell_still", "hallway", still_min=780)
    floor = policy.evaluate(inc, PROFILE, local_hour=14)
    assert floor.severity == "watch", floor.severity
    eff, note = watch.apply(plan, inc, floor, 14)
    assert eff.severity == "info", eff.severity
    assert note == "zone_off", note
    assert "zone_off" in eff.reasons


def test_an_unwatched_area_cannot_hold_a_locked_class_either():
    # Switching off the kitchen does not silence the stove: that class is a
    # safety net, and the zone toggle is a preference about chatter.
    plan = watch.normalise({"zones": {"kitchen": False}})
    inc = _incident("stove_unattended", "kitchen", absent_s=700)
    floor = policy.evaluate(inc, PROFILE, local_hour=14)
    assert floor.severity == "emergency", floor.severity
    eff, note = watch.apply(plan, inc, floor, 14)
    assert eff.severity == "emergency", eff.severity
    assert note is None


def test_an_unwatched_area_still_sends_a_fall_that_happens_in_it():
    # The point of the guard: switching off the hallway is a choice about
    # chatter, not a decision to be blind to someone on the floor.
    plan = watch.normalise({"zones": {"hallway": False}})
    inc = _incident("fall_confirmed", "hallway", no_recovery_s=20)
    floor = policy.evaluate(inc, PROFILE, local_hour=14)
    assert floor.severity == "emergency", floor.severity
    eff, note = watch.apply(plan, inc, floor, 14)
    assert eff.severity == "emergency", eff.severity
    assert note is None


def test_a_watched_area_behaves_normally():
    plan = watch.normalise({"zones": {"kitchen": True}})
    inc = _incident("stove_unattended", "kitchen", absent_s=700)
    floor = policy.evaluate(inc, PROFILE, local_hour=14)
    eff, note = watch.apply(plan, inc, floor, 14)
    assert eff.severity == "emergency" and note is None


def test_an_unknown_zone_is_watched_not_ignored():
    # A zone added to edge.yaml after the plan was written must not be dark.
    plan = watch.normalise({"zones": {"kitchen": False}})
    assert watch.zone_off(plan, {"zone_id": "new_room"}) is False
    assert watch.zone_off(plan, {}) is False


def test_zones_off_appear_in_the_summary():
    plan = watch.normalise({"zones": {"kitchen": False, "hallway": True}})
    assert watch.summary(plan)["zones_off"] == ["kitchen"]


def test_a_plan_without_zones_is_fully_watched():
    plan = watch.normalise({"modes": {"door": "digest"}})
    assert plan["zones"] == {}
    assert watch.summary(plan)["zones_off"] == []


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                fails += 1
                print(f"  FAIL {name}: {exc}")
    print(f"\n{fails} failure(s)")
    sys.exit(1 if fails else 0)
