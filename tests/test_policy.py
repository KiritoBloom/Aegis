"""Invariants that must hold no matter which model answers.

Run: python -m pytest tests -q   (or python tests/test_policy.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import policy  # noqa: E402
from app.policy import Decision, Floor, ResidentProfile, EvidenceGate  # noqa: E402

PROFILE = ResidentProfile()

PACKET = {
    "incident": {"id": "i1", "type": "door_open", "zone": "door_exit",
                 "started_at_local": "2026-10-27T03:12:00+03:00", "source": "live"},
    "resident": {"display_name": "Margaret", "age": 81,
                 "care_profile": {"cognitive_condition": "dementia", "wanders": True,
                                  "lives_alone": True, "anticoagulant": False}},
    "track": {"present": True, "dwell_s": 6.8, "direction": "outward", "confidence": 0.91,
              "person_count": 1},
    "sensors": {"door_contact": "open", "last_motion_s": 45},
    "policy": {"floor": "urgent", "floor_reasons": ["door_open", "night_hours"]},
    "constraints": {"local_hour": 3, "language": "en"},
}


def dec(**kw) -> Decision:
    base = dict(severity="watch", notify_now=False, title="t", body="b", evidence=[],
                actions=[{"id": "im_checking", "label": "I'm checking"}], confidence=0.7,
                follow_up_s=90, escalate_if_no_ack=True, rationale="r")
    base.update(kw)
    return Decision(**base)


# --- floor ---------------------------------------------------------------
def test_fall_confirmed_is_always_emergency():
    f = policy.evaluate({"type": "fall_confirmed"}, PROFILE, local_hour=14)
    assert f.severity == "emergency" and f.notifies


def test_night_door_open_with_cognitive_condition_is_urgent():
    f = policy.evaluate({"type": "door_open", "sensors": {"door_contact": "open"}},
                        PROFILE, local_hour=3)
    assert f.severity == "urgent"
    assert "night_hours" in f.reasons and "cognitive_condition" in f.reasons


def test_daytime_approach_is_not_an_alert():
    """The restraint case. This must never page anyone."""
    f = policy.evaluate({"type": "door_approach"}, ResidentProfile(cognitive_condition=None),
                        local_hour=11)
    assert f.severity == "info" and not f.notifies


def test_unknown_type_does_not_guess():
    assert policy.evaluate({"type": "something_new"}, PROFILE, local_hour=12).severity == "info"


# --- the model may raise, never lower ------------------------------------
def test_model_cannot_lower_the_floor():
    floor = Floor("emergency", ["fall_confirmed"])
    out, problems = policy.validate(dec(severity="watch"), floor, EvidenceGate(PACKET))
    assert out.severity == "emergency"
    assert any(p.startswith("severity_below_floor") for p in problems)
    assert out.notify_now is True


def test_model_may_raise_the_floor():
    out, problems = policy.validate(dec(severity="emergency", notify_now=True),
                                    Floor("watch"), EvidenceGate(PACKET))
    assert out.severity == "emergency"
    assert not any(p.startswith("severity_below_floor") for p in problems)


def test_urgent_cannot_silence_notification():
    out, problems = policy.validate(dec(severity="urgent", notify_now=False),
                                    Floor("watch"), EvidenceGate(PACKET))
    assert out.notify_now is True and "notify_suppressed_for_urgent" in problems


# --- evidence gate -------------------------------------------------------
def test_gate_accepts_real_facts_in_any_phrasing():
    """Regression: the first wiring threw away 7 true citations and kept none."""
    gate = EvidenceGate(PACKET)
    for token in ("door_open", "03:12", "door_exit", "outward", "dementia",
                  "wanders", "lives_alone", "open", "dwell_6.8s", "night_hours"):
        assert token in gate, token


def test_gate_rejects_invented_facts():
    gate = EvidenceGate(PACKET)
    for token in ("gunshot", "blood_on_floor", "neighbour_called", "police_arrived"):
        assert token not in gate, token


def test_gate_rejects_a_token_padded_with_only_stopwords():
    gate = EvidenceGate(PACKET)
    assert "the_and" not in gate


def test_gate_accepts_a_key_and_value_separated_by_a_space():
    # The model writes "person_count 1" as often as "person_count=1". Same fact.
    gate = EvidenceGate(PACKET)
    assert "person_count 1" in gate


def test_gate_accepts_a_key_and_value_separated_by_a_colon():
    gate = EvidenceGate(PACKET)
    assert "dwell_s: 6.8" in gate
    assert "dwell_s:6.8" in gate


def test_gate_accepts_daylight_because_the_packet_measured_the_hour():
    # local_hour 3 in PACKET is night; the derived word is admitted, the other
    # half of the day is not.
    gate = EvidenceGate(PACKET)
    assert "night" in gate
    assert "daylight" not in gate


def test_gate_accepts_a_clock_time_in_every_shape_the_model_writes_it():
    # The packet stores "03:12"; the model may cite seconds and an offset.
    gate = EvidenceGate(PACKET)
    for token in ("03:12", "03:12:41", "03:12:41+02:00", "03:12:41Z"):
        assert token in gate, token


def test_gate_rejects_a_fabricated_sentence_whose_words_are_all_in_the_packet():
    # Every word here appears somewhere in the packet. A three-word phrase is not
    # a fact, and the gate must not accept it just because the vocabulary matches.
    gate = EvidenceGate(PACKET)
    assert "door person hallway" not in gate


def test_validate_drops_invented_evidence_and_says_so():
    d = dec(evidence=["door_open", "gunshot"])
    out, problems = policy.validate(d, Floor("watch"), EvidenceGate(PACKET))
    assert out.evidence == ["door_open"]
    assert any(p.startswith("dropped_evidence:1") and "gunshot" in p for p in problems)


# --- actions and language ------------------------------------------------
def test_unknown_actions_are_removed_but_one_remains():
    d = dec(actions=[{"id": "call_police", "label": "x"}])
    out, problems = policy.validate(d, Floor("watch"), EvidenceGate(PACKET))
    assert len(out.actions) == 1 and out.actions[0]["id"] == "im_checking"
    assert any(p.startswith("dropped_actions") for p in problems)


def test_jargon_in_family_text_is_flagged():
    d = dec(body="The model detected an anomaly on camera.")
    _, problems = policy.validate(d, Floor("watch"), EvidenceGate(PACKET))
    assert "forbidden_jargon_in_body" in problems


# --- fail-open path ------------------------------------------------------
def test_template_decision_still_notifies_on_emergency():
    d = policy.template_decision(Floor("emergency", ["fall_confirmed"]),
                                 {"type": "fall_confirmed"}, PROFILE, "03:12")
    assert d.severity == "emergency" and d.notify_now and d.actions


def test_worker_sees_the_same_sensors_the_ingest_saw():
    # The queue used to hand `enrich` a raw row whose sensors were still in
    # `sensors_json`, so the ritual/recovery/stove rules silently missed and a
    # prayer event came back urgent instead of info. Decoding must round-trip.
    import json

    from app import db

    raw = {
        "id": "inc_test", "type": "fall_suspected", "zone_id": "prayer_room",
        "local_time": "2026-11-02T05:12:00+02:00", "features_json": "{}",
        "sensors_json": json.dumps({"posture_transitions": 8, "recurring_daily": True}),
        "privacy_json": "{}", "evidence_json": "[]", "sam_json": None,
    }
    decoded = db._decode_incident(dict(raw))
    floor = policy.evaluate(decoded, PROFILE, local_hour=5)
    assert floor.severity == "info", f"expected info, got {floor.severity}"


def test_punctuation_cannot_change_a_safety_verdict():
    # The plan's demo line. A comma after "fine" used to fall through to `other`.
    assert policy.classify_transcript("I'm fine, I just dropped the remote") == "affirmative"
    assert policy.classify_transcript("I'm fine.") == "affirmative"


def test_classifier_reads_negation_as_help_not_affirmative():
    # "I'm not okay" contains "okay". Order of checks is the safety property.
    assert policy.classify_transcript("I'm not okay") == "help"
    assert policy.classify_transcript("no, I can't get up") == "help"


def test_classifier_reads_an_empty_answer_as_no_response():
    assert policy.classify_transcript("") == "no_response"
    assert policy.classify_transcript("   ") == "no_response"


def test_only_two_agreeing_channels_may_lower_an_alert():
    # The one path down: she says she is fine AND she is on her feet.
    ok = policy.checkin_outcome("affirmative", upright_moving=True, on_floor=False)
    assert ok["severity_delta"] == -1 and ok["resolution"] == "self_recovered"


def test_saying_fine_from_the_floor_is_a_contradiction_not_a_downgrade():
    # S18: the case a posture-only system misses and a transcript-only system
    # gets dangerously wrong. Severity must not move.
    out = policy.checkin_outcome("affirmative", upright_moving=False, on_floor=True)
    assert out["severity_delta"] == 0
    assert out["contradiction"] is True
    assert out["resolution"] == "contradiction"


def test_saying_fine_while_motionless_on_the_floor_is_also_a_contradiction():
    out = policy.checkin_outcome("affirmative", upright_moving=True,
                                 on_floor=False, still_s=14.0)
    assert out["severity_delta"] == 0 and out["contradiction"] is True


def test_help_escalates_even_if_perception_looks_calm():
    out = policy.checkin_outcome("help", upright_moving=True, on_floor=False)
    assert out["severity_delta"] == 1


def test_silence_escalates():
    out = policy.checkin_outcome("no_response", upright_moving=True, on_floor=False)
    assert out["severity_delta"] == 1


def test_unclear_answer_never_changes_severity():
    for cls in ("confused", "other"):
        out = policy.checkin_outcome(cls, upright_moving=True, on_floor=False)
        assert out["severity_delta"] == 0 and out["contradiction"] is False


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
