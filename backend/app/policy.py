"""The policy floor: deterministic, model-free, and the only thing allowed to
set a minimum severity.

The rule is one line long and it is the safety guarantee of the whole product:

    the model may raise the alarm, never lower it.

Everything else here exists to make that line auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ORDER = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}
SEVERITIES = tuple(ORDER)


def max_severity(a: str, b: str) -> str:
    return a if ORDER.get(a, 0) >= ORDER.get(b, 0) else b


@dataclass
class Floor:
    severity: str
    reasons: list[str] = field(default_factory=list)

    @property
    def notifies(self) -> bool:
        return ORDER.get(self.severity, 0) >= ORDER["urgent"]


@dataclass
class ResidentProfile:
    display_name: str = "Margaret"
    age: int = 81
    cognitive_condition: str | None = "dementia"
    wanders: bool = True
    uses_mobility_aid: bool = False
    anticoagulant: bool = False
    lives_alone: bool = True


def evaluate(
    incident: dict,
    profile: ResidentProfile,
    *,
    local_hour: int,
    night: bool | None = None,
    person_inside: bool | None = None,
) -> Floor:
    """Compute the minimum response for an incident. Pure function."""
    itype = incident.get("type", "")
    sensors = incident.get("sensors") or {}
    door = str(sensors.get("door_contact", "unknown"))
    is_night = night if night is not None else (local_hour >= 22 or local_hour < 6)
    cognitive = bool(profile.cognitive_condition)

    if itype in ("fall_suspected", "fall_confirmed"):
        # A fall is not one event. The plan's distinction is the difference
        # between waking a daughter at 3AM and telling her at breakfast, so the
        # rule has to look at recovery, company, and whether this is a shape the
        # household makes every day on purpose.
        recovery = float(sensors.get("no_recovery_s", 0) or 0)
        recovered = bool(sensors.get("recovered")) or 0 < recovery < 8
        helper = bool(sensors.get("helper_present") or sensors.get("caregiver_present")
                      or sensors.get("second_person_present"))
        ritual = (int(sensors.get("posture_transitions", 0) or 0) >= 6
                  and bool(sensors.get("recurring_daily")))

        if itype == "fall_suspected":
            if ritual:
                # Eight sujud cycles in the prayer room is not a fall. Suppressing
                # this is the product working, not the product failing.
                return Floor("info", ["recurring_ritual_posture"])
            if recovered:
                return Floor("info", ["recovered_within_8s"])
            return Floor("urgent", ["fall_suspected"])

        # Confirmed fall: the only things that keep it below emergency are a
        # person already with her, or evidence she is back on her feet.
        if helper:
            return Floor("urgent", ["fall_confirmed", "second_person_present"])
        if recovered:
            return Floor("urgent", ["fall_confirmed", "recovered_within_8s"])
        reasons = ["fall_confirmed"]
        age = profile.age or 0
        if age >= 75 or profile.anticoagulant or profile.lives_alone:
            reasons.append("high_risk_profile")
        return Floor("emergency", reasons)

    if itype == "fall_assisted":
        return Floor("urgent", ["second_person_present"])

    if itype == "door_open":
        reasons = ["door_open"]
        if is_night:
            reasons.append("night_hours")
        if cognitive:
            reasons.append("cognitive_condition")
        if is_night and cognitive:
            if person_inside is False or door == "open":
                reasons.append("resident_may_be_outside")
            return Floor("urgent", reasons)
        return Floor("watch" if not (is_night and cognitive) else "urgent", reasons)

    if itype == "door_approach":
        return Floor("watch" if (is_night and cognitive) else "info", ["approach_only"])

    if itype == "door_left_open":
        return Floor("watch", ["door_open_over_120s"])

    if itype == "stove_unattended":
        # The edge reports absence in the track; a sensor payload may carry it
        # too. Read both, because a missing field must not silently downgrade an
        # emergency to "urgent".
        track = incident.get("track") or {}
        absent = float(sensors.get("absent_s") or track.get("absent_s") or 0)
        smoke = bool(sensors.get("smoke_visible")
                     or (incident.get("vision_check") or {}).get("smoke_visible"))
        # A hob that is on while she is still in the kitchen is dinner, not an
        # emergency. The plan's threshold is five minutes with nobody there.
        if absent < 300 and not smoke:
            return Floor("info", ["person_in_kitchen"])
        if absent >= 600 or smoke:
            return Floor("emergency", ["unattended_heat", "smoke" if smoke else "over_10min"])
        return Floor("urgent", ["unattended_heat"])

    if itype == "camera_offline":
        return Floor("watch", ["camera_offline"])
    if itype == "device_offline":
        return Floor("urgent", ["device_offline"])
    if itype == "checkin_no_response":
        return Floor("urgent", ["no_answer"])
    if itype == "person_unwell_still":
        # No movement for 12h and no caregiver visit is worth a quiet question,
        # not an alarm: stillness is also what a long nap looks like.
        still_min = float(sensors.get("still_min") or 0)
        if still_min >= 720 and not sensors.get("caregiver_visit"):
            return Floor("watch", ["no_movement_12h"])
        return Floor("info", ["still_but_recent_activity"])

    return Floor("info", ["no_rule"])


@dataclass
class Decision:
    severity: str
    notify_now: bool
    title: str
    body: str
    evidence: list[str]
    actions: list[dict]
    confidence: float
    follow_up_s: int
    escalate_if_no_ack: bool
    rationale: str


TEMPLATES: dict[str, tuple[str, str, list[dict]]] = {
    "door_open": (
        "Door opened",
        "{name} opened the front door at {time}. Someone should check.",
        [{"id": "call_resident", "label": "Call now"},
         {"id": "im_checking", "label": "I'm checking"},
         {"id": "false_alarm", "label": "False alarm"}],
    ),
    "fall_suspected": (
        "Possible fall",
        "{name} may have fallen in the hallway and has not stood up.",
        [{"id": "call_resident", "label": "Call now"},
         {"id": "send_help", "label": "Send help"},
         {"id": "false_alarm", "label": "False alarm"}],
    ),
    "fall_confirmed": (
        "Fall — no movement",
        "{name} is on the floor and has not moved. Please call her now.",
        [{"id": "call_resident", "label": "Call now"},
         {"id": "send_help", "label": "Send help"},
         {"id": "false_alarm", "label": "False alarm"}],
    ),
    "stove_unattended": (
        "Hot plate left on",
        "The hot plate has been on with no one in the kitchen.",
        [{"id": "call_neighbor", "label": "Call neighbor"},
         {"id": "im_checking", "label": "I'm checking"},
         {"id": "false_alarm", "label": "False alarm"}],
    ),
}


def template_decision(floor: Floor, incident: dict, profile: ResidentProfile,
                      local_time: str = "") -> Decision:
    """The fail-open path. No model, no network, still an alert."""
    itype = incident.get("type", "")
    title, body, actions = TEMPLATES.get(
        itype,
        ("Aegis alert", "An event needs your attention at {time}.",
         [{"id": "im_checking", "label": "I'm checking"},
          {"id": "false_alarm", "label": "False alarm"}]),
    )
    body = body.format(name=profile.display_name.split()[0], time=local_time or "now")
    return Decision(
        severity=floor.severity,
        notify_now=floor.notifies,
        title=title,
        body=body[:240],
        evidence=list(incident.get("evidence") or []),
        actions=actions,
        confidence=0.5,
        follow_up_s=60 if itype.startswith("fall") else 90,
        escalate_if_no_ack=True,
        rationale="Deterministic policy floor; the model was unavailable.",
    )


ALLOWED_ACTIONS = {
    "call_resident", "im_checking", "false_alarm",
    "call_neighbor", "send_help", "view_clip", "snooze_30m",
}

FORBIDDEN_TERMS = (" ai ", "algorithm", "model", "anomaly", "subject", "confidence score")

# Words that carry no evidence on their own. A token made only of these is a
# citation of nothing even if every word happens to appear in the packet.
_STOPWORDS = {
    "a", "an", "the", "at", "in", "on", "of", "to", "and", "or", "is", "was",
    "true", "false", "null", "none", "en", "unknown", "info", "null",
}


def _num(v: float) -> set[str]:
    """Every plausible way a model writes a number back: 6.8, 7, 6.8s, 7_seconds."""
    forms = {f"{v:g}", f"{v:.1f}", str(int(v)), f"{v:g}s"}
    if float(v).is_integer():
        forms |= {f"{int(v)}", f"{int(v)}s", f"{int(v)}_seconds"}
    return {f.lower() for f in forms}


def packet_vocabulary(packet: dict) -> set[str]:
    """Every fact the packet actually contains, as comparable lowercase words.

    This is what makes the evidence gate honest in both directions: a token may
    only survive if the packet says it, and a token the packet *does* support must
    not be thrown away for being phrased differently from the schema.
    """
    vocab: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                vocab.add(str(k).lower())
                vocab.update(re.split(r"[_\-]", str(k).lower()))
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            vocab.add("true" if node else "false")
        elif isinstance(node, (int, float)):
            vocab.update(_num(node))
        elif isinstance(node, str):
            s = node.lower().strip()
            vocab.add(s)
            if " " not in s:                      # a token, not prose
                vocab.update(p for p in re.split(r"[_\-:.]", s) if p)
            for ts in re.findall(r"\d{1,2}:\d{2}", s):   # "03:12" from an ISO stamp
                vocab.add(ts)

    walk(packet)
    return vocab


class EvidenceGate:
    """`in`-test used by `validate()`: may this token be shown to a family?

    Accepts a token when the packet supports it, whatever the phrasing:
    exact match, a composition of real packet tokens (`door_contact_open`,
    `dwell_6.8s`), or a number the packet measured. Rejects invented facts.
    """

    def __init__(self, packet: dict, extra: set[str] | None = None) -> None:
        self.vocab = packet_vocabulary(packet)
        # Facts the packet deterministically implies. `local_hour: 15` is
        # measured; "daylight" is what that hour means, and a family reader
        # understands it instantly. These are derived, not invented, so the gate
        # admits them — and only these.
        hour = (packet.get("constraints") or {}).get("local_hour")
        if isinstance(hour, (int, float)):
            night = hour >= 22 or hour < 6
            self.vocab |= {"night", "nighttime", "dark"} if night else {
                "daylight", "daytime", "day", "morning", "afternoon", "evening"}
        # `person_count: 7` in a single-resident household means six other
        # people. Saying so is arithmetic, not invention, and it is the detail a
        # caregiver needs in order to judge the scene.
        count = (packet.get("track") or {}).get("person_count")
        if isinstance(count, (int, float)) and count > 1:
            self.vocab |= {"non-resident", "nonresident", "other people",
                           "visitor", "visitors", "second person",
                           # the gate splits on hyphens, so the parts of a
                           # derived phrase have to be admissible too
                           "non", "resident", "other", "people"}
        if extra:
            self.vocab |= {str(e).lower() for e in extra}
        self.rejected: list[str] = []

    def _supported(self, token: str) -> bool:
        if not token:
            return False
        if token in self.vocab:
            return True
        # A clock time the packet contains, written in any of the ways a person
        # writes a clock time: "12:02", "12:02:46", "12:02:46+02:00". The packet
        # stores "12:02" and full ISO stamps; every one of these is the same
        # measured moment. Rejecting them would discard a real fact.
        clock = re.fullmatch(r"(\d{1,2}:\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2}|z)?",
                             token, re.I)
        if clock and clock.group(1) in self.vocab:
            return True
        # "person_count 1" — a model writes a key and its value separated by a
        # space about as often as it writes them joined. Both are the same fact.
        # Only the two-group "key value" shape qualifies: splitting a whole
        # sentence would let a fabricated phrase pass because each word of it
        # happens to exist somewhere in the packet.
        groups = token.split()
        if len(groups) > 2:
            return False
        # `key: value` and `key:value` are the same fact as `key value`. A colon
        # inside a clock time was already handled above, so splitting on it here
        # cannot corrupt a timestamp.
        parts: list[str] = []
        for chunk in re.split(r"[_\-\s]+", token):
            if not chunk:
                continue
            parts.extend(p for p in chunk.split(":") if p)
        if not parts or len(parts) > 4:
            return False
        if not any(p not in _STOPWORDS for p in parts):
            return False
        for p in parts:
            if p in self.vocab:
                continue
            if re.fullmatch(r"\d+(?:\.\d+)?s?", p) and re.sub(r"s$", "", p) in self.vocab:
                continue
            return False
        return True

    def __contains__(self, item: object) -> bool:
        token = str(item).lower().strip()
        if self._supported(token):
            return True
        self.rejected.append(str(item))
        return False


def validate(decision: Decision, floor: Floor,
             allowed_evidence) -> tuple[Decision, list[str]]:
    """Enforce the runtime invariants on whatever the model produced.

    Returns the corrected decision and a list of violations, which is logged and
    surfaced rather than swallowed.
    """
    problems: list[str] = []

    if ORDER.get(decision.severity, 0) < ORDER.get(floor.severity, 0):
        problems.append(f"severity_below_floor:{decision.severity}->{floor.severity}")
        decision.severity = floor.severity

    if ORDER.get(decision.severity, 0) >= ORDER["urgent"] and not decision.notify_now:
        problems.append("notify_suppressed_for_urgent")
        decision.notify_now = True

    kept = [a for a in decision.actions if a.get("id") in ALLOWED_ACTIONS]
    if len(kept) != len(decision.actions):
        problems.append(f"dropped_actions:{len(decision.actions) - len(kept)}")
    decision.actions = kept or [{"id": "im_checking", "label": "I'm checking"}]

    known = [e for e in decision.evidence if e in allowed_evidence]
    if len(known) != len(decision.evidence):
        dropped = [str(e) for e in decision.evidence if e not in known]
        problems.append(f"dropped_evidence:{len(dropped)}:{'|'.join(dropped)[:180]}")
    decision.evidence = known

    low = decision.body.lower()
    if any(term in low for term in FORBIDDEN_TERMS):
        problems.append("forbidden_jargon_in_body")

    if len(decision.body) > 240:
        decision.body = decision.body[:237].rstrip() + "..."
        problems.append("body_truncated")

    if len(decision.title) > 60:
        decision.title = decision.title[:57].rstrip() + "..."
        problems.append("title_truncated")

    decision.follow_up_s = max(0, min(3600, int(decision.follow_up_s or 90)))
    decision.confidence = max(0.0, min(1.0, float(decision.confidence or 0.5)))
    return decision, problems


# ---------------------------------------------------------------------------
# interruption discipline
#
# "Alert fatigue is a safety bug" is not a sentiment, it is a rule with numbers.
# A system that cries wolf at 3 AM for a bathroom trip gets muted, and a muted
# system is worse than no system at all.
# ---------------------------------------------------------------------------
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7


def in_quiet_hours(local_hour: int) -> bool:
    return local_hour >= QUIET_START_HOUR or local_hour < QUIET_END_HOUR


def defer_until(local_hour: int) -> int:
    """Seconds until 07:00 local. Only ever applied to `watch`."""
    if not in_quiet_hours(local_hour):
        return 0
    return ((QUIET_END_HOUR - local_hour) % 24) * 3600


def should_interrupt(decision: Decision, floor: Floor, local_hour: int) -> tuple[bool, str]:
    """Whether this may buzz a phone right now, and why not if it may not.

    Emergencies and anything at or above `urgent` always send. Only `watch` is
    held for the morning — and the hold is disclosed in the UI, never silent.
    """
    rank = ORDER.get(decision.severity, 0)
    if rank >= ORDER["urgent"] or decision.notify_now:
        return True, "sends_now"
    if floor.notifies:
        return True, "floor_requires"
    if in_quiet_hours(local_hour):
        return False, "quiet_hours"
    return False, "watch_only"


DEDUPE_WINDOW_S = 30
SAME_ZONE_WINDOW_S = 300
FALSE_ALARM_COOLDOWN_S = 1800


def event_key(incident: dict) -> str:
    return f"{incident.get('type')}:{incident.get('zone_id') or 'unknown'}"


def dedupe_action(previous: dict | None, incident: dict) -> str:
    """`notify`, `update`, or `suppress`.

    A repeated event inside the window updates the existing alert instead of
    starting a second conversation about the same thing.
    """
    if not previous:
        return "notify"
    age = float(incident.get("age_s") or 0)
    same_key = event_key(previous) == event_key(incident)
    if same_key and age <= DEDUPE_WINDOW_S:
        return "update"
    if same_key and age <= SAME_ZONE_WINDOW_S:
        return "update"
    if previous.get("outcome") == "false_alarm" and age <= FALSE_ALARM_COOLDOWN_S:
        # Never applied to falls: a real fall after a false alarm is still a fall.
        if not str(incident.get("type", "")).startswith("fall"):
            return "suppress"
    return "notify"



# ---------------------------------------------------------------------------
# Resident check-in: two channels must agree before an alert comes down.
#
# One channel can never lower an alert. Perception can be wrong (a shadow, a
# cat, a coat on a chair) and language can be wrong (she says "I'm fine" from
# the floor because she is embarrassed, confused, or cannot get up). Two
# independent wrongness modes disagreeing is the cheapest safety property
# available, and it costs one transcribe call.
# ---------------------------------------------------------------------------

CHECKIN_CLASSES = ("affirmative", "help", "confused", "other", "no_response")


def classify_transcript(text: str) -> str:
    """Map a short answer to one of five classes.

    Deliberately a word list, not a model call: this runs on the safety path
    after an alert, and a classifier that can be argued with is a classifier
    that can be talked into "fine". Negation is checked first so "I'm not okay"
    is never read as affirmative.
    """
    t = f" {text.lower().strip()} "
    if not t.strip():
        return "no_response"
    # Punctuation must never change a safety verdict. "I'm fine, I dropped the
    # remote" is the demo line, and a comma after "fine" used to make it fall
    # through to `other` — which meant the one case the product is built to
    # handle never de-escalated.
    t = " " + re.sub(r"[^\w']+", " ", t).strip() + " "
    negated = ("not ok", "not okay", "not fine", "can't get up", "cannot get up",
               "can not get up", "no i am not", "don't feel", "do not feel")
    if any(n in t for n in negated):
        return "help"
    help_words = (" help ", " hurts ", " hurt ", " pain ", " fell ", " fall ",
                  " stuck ", " can't move ", " cannot move ", " dizzy ",
                  " bleeding ", " ambulance ", " doctor ", " emergency ")
    if any(h in t for h in help_words):
        return "help"
    confused_words = (" what ", " where am i ", " who is ", " don't know ",
                      " do not know ", " confused ", " what happened ")
    if any(c in t for c in confused_words):
        return "confused"
    affirmative = (" i'm fine ", " im fine ", " i am fine ", " fine ",
                   " okay ", " ok ", " alright ", " all right ", " yes ",
                   " i'm good ", " im good ", " good ")
    if any(a in t for a in affirmative):
        return "affirmative"
    return "other"


def checkin_outcome(transcript_class: str, *, upright_moving: bool,
                    on_floor: bool, still_s: float = 0.0) -> dict:
    """The de-escalation table from plan §7.10, as a pure function.

    Returns `{"severity_delta": int, "resolution": str, "contradiction": bool,
    "reason": str}`. A positive delta escalates; a negative delta is only ever
    granted when perception independently agrees.

    `affirmative` + upright and moving is the *only* path to de-escalation.
    `affirmative` from the floor is a contradiction: keep the severity and say
    so in the copy, because that is exactly the case a posture-only system
    misses and a transcript-only system gets dangerously wrong.
    """
    cls = transcript_class if transcript_class in CHECKIN_CLASSES else "other"
    lying = on_floor or still_s > 10.0

    if cls == "help":
        return {"severity_delta": 1, "resolution": "escalated",
                "contradiction": False,
                "reason": "resident asked for help"}
    if cls == "no_response":
        return {"severity_delta": 1, "resolution": "escalated",
                "contradiction": False, "reason": "no answer after 20s"}
    if cls == "affirmative":
        if on_floor or still_s > 10.0:
            return {"severity_delta": 0, "resolution": "contradiction",
                    "contradiction": True,
                    "reason": "said she is fine but has not stood up"}
        if not upright_moving:
            # She says she is fine, but perception has no evidence she is on her
            # feet. Hold rather than resolve: an all-clear nobody can check is
            # the one mistake this product must not make.
            return {"severity_delta": 0, "resolution": "unclear",
                    "contradiction": False,
                    "reason": "said she is fine but is not clearly on her feet"}
        return {"severity_delta": -1, "resolution": "self_recovered",
                "contradiction": False,
                "reason": "said she is fine and is upright and moving"}
    return {"severity_delta": 0, "resolution": "unclear", "contradiction": False,
            "reason": "answer was not clear"}
