"""Household watch plan: preferences above the policy floor. Safety classes stay locked."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import policy

MODES = ("alert", "digest", "off")
TIERS = ("safety", "care", "comfort")


@dataclass(frozen=True)
class WatchClass:
    """One row of the onboarding questionnaire, and the rule behind it."""

    id: str
    label: str
    question: str
    detail: str
    tier: str
    default: str
    types: tuple[str, ...]
    modes: tuple[str, ...] = MODES
    # What the family will actually experience, in their words. Shown under the
    # three-way choice so the decision is concrete instead of abstract.
    examples: dict[str, str] = field(default_factory=dict)

    @property
    def locked(self) -> bool:
        return self.tier == "safety"

    def allowed(self, mode: str) -> bool:
        return mode in (("alert",) if self.locked else self.modes)


CATALOG: tuple[WatchClass, ...] = (
    WatchClass(
        id="fall",
        label="Falls",
        question="If Margaret goes down and does not get up, who should know?",
        detail=("A fall is the one event where the minutes decide the outcome. "
                "Aegis calls you first and asks questions later."),
        tier="safety",
        default="alert",
        types=("fall_suspected", "fall_confirmed", "fall_assisted"),
        examples={"alert": "Your phone rings within a second, day or night."},
    ),
    WatchClass(
        id="door",
        label="The front door",
        question="When should the front door opening wake you?",
        detail=("Daytime door opens are usually a delivery. At night, with memory "
                "loss in the house, the same door is the whole risk."),
        tier="care",
        default="alert",
        types=("door_open", "door_approach", "door_left_open"),
        examples={
            "alert": "Any door open, any hour.",
            "digest": "Only at night; daytime opens wait for the morning letter.",
            "off": "Never interrupt. The counter still records it.",
        },
    ),
    WatchClass(
        id="stove",
        label="The stove",
        question="A hot plate with nobody in the kitchen.",
        detail=("Five minutes away from the hob is dinner. Longer than that is a "
                "fire, and a fire does not wait for a morning letter, so this one "
                "is not a setting. The area toggle in setup still silences "
                "ordinary kitchen movement."),
        tier="safety",
        default="alert",
        types=("stove_unattended",),
        examples={"alert": "A call at five minutes, and a louder one at ten."},
    ),
    WatchClass(
        id="restlessness",
        label="Pacing and restlessness",
        question="When should repeated back-and-forth be worth a quiet check?",
        detail=("A normal walk crosses a room and stops. Pacing has a long path, "
                "many turns, and little net displacement. It can be exercise, "
                "cooking, worry or pain; Aegis reports the pattern and never "
                "names a cause."),
        tier="care",
        default="digest",
        types=("restlessness",),
        examples={
            "alert": "A gentle check when the pattern is new or at night.",
            "digest": "A line in the morning note.",
            "off": "Count it silently.",
        },
    ),
    WatchClass(
        id="stillness",
        label="Long stillness",
        question="Should a whole day without movement be worth a quiet check?",
        detail=("Twelve hours with no movement is also what a long nap looks "
                "like. This is the gentlest signal in the system."),
        tier="comfort",
        default="digest",
        types=("person_unwell_still",),
        examples={
            "alert": "A gentle nudge in the afternoon.",
            "digest": "A line in the morning letter.",
            "off": "Keep it to yourself.",
        },
    ),
    WatchClass(
        id="camera",
        label="The camera going offline",
        question="If Aegis stops being able to see, you should know.",
        detail=("A care system that is blind and quiet is worse than no system. "
                "This one cannot be turned off."),
        tier="safety",
        default="alert",
        types=("camera_offline", "device_offline"),
        examples={"alert": "A red banner and a push until it is back."},
    ),
    WatchClass(
        id="night",
        label="Someone else at night",
        question="If a second person is in the house after dark, who should know?",
        detail=("This is not a burglar alarm and it does not identify anyone. "
                "It notices a second person after dark and gives the family the "
                "clip, the time, and the door state, so a human can decide."),
        tier="safety",
        default="alert",
        types=("unexpected_presence",),
        examples={"alert": "A phone alert with the clip and the time."},
    ),
    WatchClass(
        id="help",
        label="Calls for help",
        question="If she calls for help, who should hear it?",
        detail=("A cry with no television or radio to explain it is treated as a "
                "person in need. When the room is ambiguous, Aegis asks her first "
                "and escalates if she does not answer. This is a safety net, not "
                "a preference."),
        tier="safety",
        default="alert",
        types=("help_call",),
        examples={"alert": "A check-in at once; the family if she does not answer."},
    ),
    WatchClass(
        id="checkin",
        label="No answer",
        question="If she does not answer a check-in, we escalate.",
        detail=("Silence after an alert is data, not a reason to wait. This is "
                "locked for the same reason a fall is."),
        tier="safety",
        default="alert",
        types=("checkin_no_response",),
        examples={"alert": "Escalates to the emergency contact."},
    ),
    WatchClass(
        id="motion",
        label="Ordinary movement",
        question="Should the house simply going about its day ever buzz you?",
        detail=("Movement, pets, prayer, and resting on the sofa. Useful as a "
                "counter, never as an interruption."),
        tier="comfort",
        default="off",
        types=("motion_only", "pet_motion", "normal_rest", "prayer_activity"),
        examples={
            "alert": "Buzz me every time someone walks past.",
            "digest": "A count in the morning letter.",
            "off": "Count it silently.",
        },
    ),
)

BY_ID = {c.id: c for c in CATALOG}
BY_TYPE = {t: c for c in CATALOG for t in c.types}

DEFAULT_PLAN: dict = {
    "modes": {c.id: c.default for c in CATALOG},
    "door_hours": "night",
    "quiet": {"enabled": True, "start": 22, "end": 7},
    # Which areas are watched at all. Absent means watched: a plan written
    # before a zone existed must not silently stop watching the new one.
    "zones": {},
    "onboarded_at": None,
    "consent_at": None,
    "consent_by": "",
}

# Reasons that end up in the event log, so the dashboard can say why a thing
# was not escalated in the household's own words.
REASONS = {
    "muted": "you asked not to be told about this",
    "digest": "held for the morning letter",
    "door_hours": "door alerts are night-only in your plan",
    "zone_off": "that area is not watched in your plan",
    "floor": "too serious to hold, whatever the plan says",
}


def defaults() -> dict:
    return {
        "modes": dict(DEFAULT_PLAN["modes"]),
        "door_hours": DEFAULT_PLAN["door_hours"],
        "quiet": dict(DEFAULT_PLAN["quiet"]),
        "onboarded_at": None,
        "consent_at": None,
        "consent_by": "",
    }


def normalise(raw: dict | None) -> dict:
    """Merge stored preferences with defaults without losing new safety classes."""
    plan = defaults()
    raw = raw or {}
    modes = raw.get("modes") or {}
    for c in CATALOG:
        mode = str(modes.get(c.id, c.default))
        plan["modes"][c.id] = mode if c.allowed(mode) else c.default
    zones = raw.get("zones") or {}
    if isinstance(zones, dict):
        plan["zones"] = {
            str(name): bool(on) for name, on in zones.items() if isinstance(on, bool)
        }
    plan["door_hours"] = raw.get("door_hours") if raw.get("door_hours") in (
        "night", "always") else DEFAULT_PLAN["door_hours"]
    quiet = raw.get("quiet") or {}
    plan["quiet"] = {
        "enabled": bool(quiet.get("enabled", True)),
        "start": _hour(quiet.get("start"), 22),
        "end": _hour(quiet.get("end"), 7),
    }
    for key in ("onboarded_at", "consent_at"):
        plan[key] = raw.get(key) or None
    plan["consent_by"] = str(raw.get("consent_by") or "")
    return plan


def _hour(value, fallback: int) -> int:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return fallback
    return hour if 0 <= hour <= 23 else fallback


def validate(raw: dict) -> dict:
    """Reject a plan rather than quietly repair one that turns off a safety net.

    Unknown keys are ignored (forward compatibility), but a *known* class with
    a mode it may not have is an error the caller must see.
    """
    if not isinstance(raw, dict):
        raise ValueError("plan must be an object")
    modes = raw.get("modes") or {}
    for key, mode in modes.items():
        spec = BY_ID.get(key)
        if spec is None:
            continue
        if not spec.allowed(str(mode)):
            raise ValueError(
                f"{spec.label} cannot be set to '{mode}'. "
                f"{'It is a safety net, not a preference.' if spec.locked else 'Allowed: alert, digest, off.'}"
            )
    if "door_hours" in raw and raw["door_hours"] not in ("night", "always"):
        raise ValueError("door_hours must be 'night' or 'always'")
    quiet = raw.get("quiet")
    if quiet is not None:
        if not isinstance(quiet, dict):
            raise ValueError("quiet must be an object")
        for key in ("start", "end"):
            if key in quiet:
                try:
                    int(quiet[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"quiet.{key} must be an hour 0-23") from exc
    zones = raw.get("zones")
    if zones is not None and not isinstance(zones, dict):
        raise ValueError("zones must be an object of name -> watched")
    return normalise(raw)


def is_night(hour: int, quiet: dict) -> bool:
    start, end = int(quiet.get("start", 22)), int(quiet.get("end", 7))
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def zone_off(plan: dict, incident: dict) -> bool:
    """Is this area switched off in the household's plan?

    Absent means watched. Private areas are handled upstream by the mask, not
    here - this only ever reflects a choice somebody made on the areas screen.
    """
    zone = str(incident.get("zone_id") or "")
    if not zone:
        return False
    return plan.get("zones", {}).get(zone) is False


def plan_for(incident: dict, plan: dict, local_hour: int) -> tuple[str, str | None]:
    """Which mode governs this incident, and why it was overridden if it was."""
    spec = BY_TYPE.get(str(incident.get("type") or ""))
    if spec is None:
        return "alert", None
    mode = plan["modes"].get(spec.id, spec.default)
    if spec.locked:
        return "alert", None
    # The door is the one class with a schedule of its own, because "a door at
    # 3 AM" and "a door at 3 PM" are different events to the same family.
    if spec.id == "door" and mode == "alert":
        if plan.get("door_hours") == "night" and not is_night(local_hour, plan["quiet"]):
            return "digest", "door_hours"
    if zone_off(plan, incident):
        return "off", "zone_off"
    return mode, None


def apply(plan: dict, incident: dict, floor: policy.Floor,
          local_hour: int) -> tuple[policy.Floor, str | None]:
    """Fold the household's choices into the floor. Never lowers an emergency.

    Returns the effective floor and a short reason code when the plan changed
    the outcome (`muted`, `digest`, `door_hours`, `zone_off`), else `None`.
    """
    mode, note = plan_for(incident, plan, local_hour)

    # The hard guard, checked before any mode is honoured. A plan is a
    # preference; an emergency is not. Without this a household could mute the
    # stove rule and the twelve-minute fire would go to the morning letter, or
    # switch off the hallway and lose the fall that happens to be in it.
    if policy.ORDER.get(floor.severity, 0) >= policy.ORDER["urgent"]:
        if mode == "alert":
            return floor, None
        return policy.Floor(floor.severity, list(floor.reasons) + ["plan_cannot_hold"]), None

    if mode == "alert":
        return floor, None
    if mode == "off":
        code = "zone_off" if note == "zone_off" else "muted_by_household"
        return policy.Floor("info", list(floor.reasons) + [code]), note or "muted"
    return (policy.Floor("info", list(floor.reasons) + ["held_for_digest"]),
            note or "digest")


def summary(plan: dict) -> dict:
    """What the plan means in one line per class, for the dashboard and tests."""
    out = {"alert": [], "digest": [], "off": [], "locked": []}
    for c in CATALOG:
        mode = plan["modes"].get(c.id, c.default)
        if c.locked:
            out["locked"].append(c.id)      # always alerting, and not negotiable
        else:
            out[mode if mode in out else "off"].append(c.id)
    out["door_hours"] = plan.get("door_hours", "night")
    out["quiet"] = plan.get("quiet", DEFAULT_PLAN["quiet"])
    zones = plan.get("zones") or {}
    out["zones_off"] = sorted(name for name, on in zones.items() if on is False)
    return out
