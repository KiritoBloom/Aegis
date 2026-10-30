"""The watch plan may turn down the volume. It may never turn off the net.

Onboarding asks a family what they want to be told about. That is a settings
page in every other product; here it is a settings page that can silently
disable a fall alarm, so it gets its own test file.

Run: python tests/test_watch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import policy, watch  # noqa: E402

PROFILE = policy.ResidentProfile()


def _incident(itype: str, zone: str = "hallway", **sensors) -> dict:
    return {"id": "inc_t", "type": itype, "zone_id": zone, "sensors": sensors,
            "track": {}, "local_time": "2026-11-02T03:10:00+02:00",
            "started_at": "2026-11-02T01:10:00Z"}


def _floor(itype: str, hour: int = 3, **sensors):
    inc = _incident(itype, **sensors)
    return policy.evaluate(inc, PROFILE, local_hour=hour), inc


def test_a_safety_class_cannot_be_muted():
    for key in ("fall", "camera", "checkin", "stove"):
        spec = watch.BY_ID[key]
        assert spec.locked, f"{key} should be locked"
        try:
            watch.validate({"modes": {key: "off"}})
            raise AssertionError(f"{key} accepted mode 'off'")
        except ValueError:
            pass


def test_muting_a_care_class_holds_a_watch_but_not_an_emergency():
    # Long stillness is the honest example of a class a family may legitimately
    # turn off: it is a comfort signal, and muting it costs a note, not a night.
    plan = watch.normalise({"modes": {"stillness": "off"}})

    quiet, inc = _floor("person_unwell_still", hour=14, still_min=780)
    assert quiet.severity == "watch", quiet.severity
    eff, note = watch.apply(plan, inc, quiet, 14)
    assert eff.severity == "info", eff.severity
    assert note == "muted"
    assert "muted_by_household" in eff.reasons

    # The same class, but the floor has already decided this is serious.
    # A plan does not get a vote on that.
    loud, inc = _floor("fall_confirmed", hour=3, no_recovery_s=20)
    assert loud.severity == "emergency", loud.severity
    eff, note = watch.apply(plan, inc, loud, 3)
    assert eff.severity == "emergency", eff.severity
    assert note is None


def test_the_stove_cannot_be_switched_off_because_the_floor_never_allows_it():
    # A five-minute empty kitchen is already urgent, so a "tell me at ten
    # minutes instead" option would be a promise the policy always breaks.
    # Rather than ship a control that does nothing, the class is locked.
    assert watch.BY_ID["stove"].locked
    try:
        watch.validate({"modes": {"stove": "off"}})
        raise AssertionError("stove accepted mode 'off'")
    except ValueError:
        pass


def test_a_digest_door_plan_still_sends_the_3am_exit():
    plan = watch.normalise({"modes": {"door": "digest"}})

    # Daytime: a delivery. Held.
    day, inc = _floor("door_open", hour=14, door_contact="open")
    eff, note = watch.apply(plan, inc, day, 14)
    assert eff.severity == "info" and note == "digest", (eff.severity, note)

    # 03:10 with dementia: the floor is already urgent. Nothing may hold it.
    night, inc = _floor("door_open", hour=3, door_contact="open")
    assert night.severity == "urgent", night.severity
    eff, note = watch.apply(plan, inc, night, 3)
    assert eff.severity == "urgent", eff.severity
    assert note is None


def test_night_only_door_hours_hold_daytime_opens_only():
    plan = watch.normalise({"modes": {"door": "alert"}, "door_hours": "night"})
    day, inc = _floor("door_open", hour=14, door_contact="open")
    eff, note = watch.apply(plan, inc, day, 14)
    assert note == "door_hours", note
    assert eff.severity == "info"

    night, inc = _floor("door_open", hour=3, door_contact="open")
    eff, note = watch.apply(plan, inc, night, 3)
    assert note is None and eff.severity == "urgent"


def test_a_fall_ignores_every_preference():
    # Even a plan that mutes everything cannot touch a confirmed fall.
    plan = watch.normalise({"modes": {"fall": "off", "door": "off", "stove": "off"}})
    floor, inc = _floor("fall_confirmed", no_recovery_s=20)
    eff, note = watch.apply(plan, inc, floor, 3)
    assert eff.severity == "emergency", eff.severity
    assert note is None


def test_an_old_plan_without_a_new_class_falls_back_to_that_class_default():
    # A plan written before "stillness" existed must not make it vanish.
    old = {"modes": {"door": "digest"}}
    plan = watch.normalise(old)
    assert plan["modes"]["stillness"] == "digest"
    assert plan["modes"]["fall"] == "alert"
    assert plan["modes"]["door"] == "digest"


def test_a_corrupt_mode_is_repaired_to_the_default_not_honoured():
    plan = watch.normalise({"modes": {"fall": "off", "door": "banana"}})
    assert plan["modes"]["fall"] == "alert"
    assert plan["modes"]["door"] == "alert"


def test_the_quiet_window_is_the_households_and_can_move():
    quiet = {"enabled": True, "start": 23, "end": 6}
    assert policy.in_quiet_hours(23, quiet)
    assert policy.in_quiet_hours(5, quiet)
    assert not policy.in_quiet_hours(6, quiet)
    assert not policy.in_quiet_hours(22, quiet)
    assert policy.defer_until(23, quiet) == 7 * 3600
    # Disabled quiet hours means nothing is held for the morning.
    assert not policy.in_quiet_hours(3, {"enabled": False})


def test_summary_counts_match_the_catalog():
    plan = watch.normalise(None)
    s = watch.summary(plan)
    assert set(s["locked"]) == {"fall", "camera", "checkin", "stove", "help", "night"}
    assert len(s["alert"]) + len(s["digest"]) + len(s["off"]) + len(s["locked"]) == len(watch.CATALOG)


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
