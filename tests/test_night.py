"""The night window and the edge's second-person rule are pure enough to test."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "edge"))

from app import api, db  # noqa: E402
from aegis_edge.config import (Camera, Clip, Config, Door, Fall, Household,  # noqa: E402
                               Motion, Person, Upload)
from aegis_edge.state import Context, RulesEngine  # noqa: E402


def test_atomic_night_window_includes_three_am_and_excludes_two_pm(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "night.db"))
    db.init()
    db.record_event("inc_night", "cam_hallway", "door_open", "front_door",
                    "2026-10-27T03:12:00+03:00", "urgent")
    db.record_event("inc_night", "cam_hallway", "unexpected_presence", "front_door",
                    "2026-10-27T03:13:00+03:00", "urgent")
    db.record_event("inc_day", "cam_hallway", "door_open", "front_door",
                    "2026-10-27T14:12:00+03:00", "watch")
    out = api.night(date="2026-10-26")
    assert out["summary"]["events"] == 2
    assert out["summary"]["door"] == 1
    assert out["summary"]["unexpected"] == 1
    current = api.night()
    previous = api.night(offset=-1)
    assert previous["start"] < current["start"]
    assert previous["active"] is False


def _config() -> Config:
    return Config(
        camera=Camera(), household=Household(), motion=Motion(), person=Person(),
        fall=Fall(), clip=Clip(), door=Door(), upload=Upload(),
    )


def test_edge_flags_two_people_after_dark():
    events = RulesEngine(_config()).evaluate(
        Context(t=10.0, person_count=2, night=True, expected_visitor=False)
    )
    assert any(e.type == "unexpected_presence" for e in events)


def test_edge_keeps_an_expected_visitor_quiet():
    events = RulesEngine(_config()).evaluate(
        Context(t=10.0, person_count=2, night=True, expected_visitor=True)
    )
    assert not any(e.type == "unexpected_presence" for e in events)


def test_edge_does_not_treat_daytime_company_as_a_night_event():
    events = RulesEngine(_config()).evaluate(
        Context(t=10.0, person_count=2, night=False, expected_visitor=False)
    )
    assert not any(e.type == "unexpected_presence" for e in events)


def test_expected_visitor_state_is_a_timed_window(monkeypatch, tmp_path):
    import time

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "visitor.db"))
    db.init()
    db.set_state("expected_visitor", {"until": time.time() + 3600, "by": "family"})
    state = api.visitor_state()
    assert state["expected"] is True and state["minutes_left"] >= 59
    db.set_state("expected_visitor", {"until": time.time() - 1, "by": "family"})
    assert api.visitor_state()["expected"] is False


def test_expected_visitor_makes_a_second_person_quiet(monkeypatch, tmp_path):
    import time

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "visitor2.db"))
    db.init()
    db.set_state("expected_visitor", {"until": time.time() + 3600, "by": "family"})
    incident = api.with_expected_visitor({
        "type": "unexpected_presence",
        "sensors": {"person_count": 2, "expected_visitor": False},
        "track": {"person_count": 2},
    })
    assert incident["sensors"]["expected_visitor"] is True
    floor = api.effective_floor(incident, local_hour=23)[0]
    assert floor.severity == "info"


def test_night_note_is_dropped_when_the_rows_change(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "note.db"))
    db.init()
    db.record_event("inc_n", "cam_hallway", "door_open", "front_door",
                    "2026-10-27T03:12:00+03:00", "urgent")
    db.set_state("night_note:2026-10-26", {"event_count": 0, "note": "old"})
    assert api.night(date="2026-10-26")["note"] is None
    db.record_event("inc_n", "cam_hallway", "unexpected_presence", "front_door",
                    "2026-10-27T03:13:00+03:00", "urgent")
    db.set_state("night_note:2026-10-26", {"event_count": 2, "note": "current"})
    assert api.night(date="2026-10-26")["note"]["note"] == "current"
