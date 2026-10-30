"""State machine connecting perception to the event and upload pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import Config, Zone
from .geometry import TrackHistory


class Severity(str, Enum):
    INFO = "info"
    WATCH = "watch"
    URGENT = "urgent"
    EMERGENCY = "emergency"


ORDER = {"info": 0, "watch": 1, "urgent": 2, "emergency": 3}


class State(str, Enum):
    BOOT = "boot"
    IDLE = "idle"
    MOTION = "motion"
    PRESENT = "present"
    TRIGGER = "trigger"
    CAPTURE = "capture"
    COOLDOWN = "cooldown"
    DEGRADED = "degraded"
    PAUSED = "paused"


@dataclass
class Event:
    type: str
    t: float
    zone: str | None
    severity: Severity
    features: dict = field(default_factory=dict)
    dedupe_key: str = ""

    def __post_init__(self) -> None:
        if not self.dedupe_key:
            self.dedupe_key = f"{self.type}:{self.zone or '-'}"


@dataclass
class Context:
    """Everything the rules are allowed to know about right now."""

    t: float
    track: TrackHistory | None = None
    posture: str = "unknown"
    person_count: int = 0
    door_open: bool = False
    door_diff: float = 0.0
    stove_on: bool = False
    kitchen_absent_s: float = 0.0
    night: bool = False
    cognitive_condition: bool = False
    prayer_cycles: int = 0
    zone: Zone | None = None
    inside_any_rest: bool = False
    expected_visitor: bool = False


class RulesEngine:
    """Deterministic event detection. Holds only timers and cooldown memory."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._last_emit: dict[str, float] = {}
        self._lying_since: float | None = None
        self._door_open_since: float | None = None
        self._cooldown_s = 30.0

    # -- helpers ---------------------------------------------------------
    def _fresh(self, key: str, t: float, cooldown: float | None = None) -> bool:
        last = self._last_emit.get(key)
        window = self._cooldown_s if cooldown is None else cooldown
        return last is None or (t - last) >= window

    def _emit(self, ev: Event) -> Event:
        self._last_emit[ev.dedupe_key] = ev.t
        return ev

    # -- rules -----------------------------------------------------------
    def evaluate(self, ctx: Context) -> list[Event]:
        out: list[Event] = []
        out.extend(self._door(ctx))
        out.extend(self._fall(ctx))
        out.extend(self._stove(ctx))
        out.extend(self._presence(ctx))
        out.extend(self._restlessness(ctx))
        return out

    def _door(self, ctx: Context) -> list[Event]:
        zone = self.cfg.zone(self.cfg.door.zone)
        if zone is None or ctx.track is None:
            return []
        feats = ctx.track.to_features(zone.points)
        dwell = feats.get("dwell_in_zone_s", 0.0)
        inside = feats.get("inside_zone", False)

        # person at the door and the door is (or just became) open
        if (ctx.door_open or ctx.door_diff >= self.cfg.door.region_diff_ratio) and (inside or dwell > 0):
            key = "door_open:front_door"
            if self._fresh(key, ctx.t, cooldown=5.0):
                sev = Severity.URGENT if (ctx.night and ctx.cognitive_condition) else Severity.WATCH
                return [
                    self._emit(
                        Event(
                            "door_open",
                            ctx.t,
                            zone.name,
                            sev,
                            {
                                **feats,
                                "door_contact": "open" if ctx.door_open else "unknown",
                                "door_diff": round(ctx.door_diff, 3),
                                "night": ctx.night,
                            },
                        )
                    )
                ]
            return []

        # hesitating at the door: worth a quiet check, not an alarm
        if dwell >= self.cfg.door.dwell_s and not ctx.door_open:
            key = "door_approach:front_door"
            if self._fresh(key, ctx.t, cooldown=120.0):
                sev = Severity.WATCH if (ctx.night and ctx.cognitive_condition) else Severity.INFO
                return [
                    self._emit(Event("door_approach", ctx.t, zone.name, sev, {**feats, "night": ctx.night}))
                ]

        # A tripwire zone means "crossing this is leaving". Without this branch
        # the zone type was decorative: it drew a polygon, produced no event, and
        # a corridor exit was indistinguishable from an empty hallway. `entries`
        # counts how many times the foot point moved into the polygon, which is
        # the crossing signal a corridor camera can actually measure.
        if zone.type == "tripwire" and feats.get("zone_entries", 0) > 0:
            key = "door_open:front_door"
            if self._fresh(key, ctx.t, cooldown=5.0):
                sev = Severity.URGENT if (ctx.night and ctx.cognitive_condition) else Severity.WATCH
                return [
                    self._emit(
                        Event(
                            "door_open",
                            ctx.t,
                            zone.name,
                            sev,
                            {**feats, "door_contact": "unknown", "crossed_tripwire": True,
                             "night": ctx.night},
                        )
                    )
                ]
            return []
        return []

    def _fall(self, ctx: Context) -> list[Event]:
        if not self.cfg.fall.enabled or ctx.track is None:
            return []
        lying = ctx.posture == "lying"
        if not lying:
            self._lying_since = None
            return []
        if ctx.inside_any_rest:
            # lying on a bed or sofa is the definition of normal
            self._lying_since = None
            return []
        if self._lying_since is None:
            self._lying_since = ctx.t
        lying_for = ctx.t - self._lying_since

        still = (ctx.track.displacement(1.0) < 0.005) if len(ctx.track) > 5 else False
        zone = ctx.zone
        prayer_zone = zone is not None and zone.name == "prayer_mat"
        # cyclical posture changes mean prayer or exercise, not a collapse
        if prayer_zone and ctx.prayer_cycles >= 3:
            return []

        if lying_for >= self.cfg.fall.no_recovery_s and still:
            key = "fall_confirmed:hallway"
            if self._fresh(key, ctx.t, cooldown=60.0):
                return [
                    self._emit(
                        Event(
                            "fall_confirmed",
                            ctx.t,
                            zone.name if zone else None,
                            Severity.EMERGENCY,
                            {**ctx.track.to_features(zone.points if zone else None), "posture": ctx.posture,
                             "lying_for_s": round(lying_for, 1)},
                        )
                    )
                ]
            return []
        if lying_for >= 3.0:
            key = "fall_suspected:hallway"
            if self._fresh(key, ctx.t, cooldown=60.0):
                return [
                    self._emit(
                        Event(
                            "fall_suspected",
                            ctx.t,
                            zone.name if zone else None,
                            Severity.URGENT,
                            {**ctx.track.to_features(zone.points if zone else None), "posture": ctx.posture,
                             "lying_for_s": round(lying_for, 1)},
                        )
                    )
                ]
        return []

    def _stove(self, ctx: Context) -> list[Event]:
        if not ctx.stove_on:
            return []
        if ctx.kitchen_absent_s < 60.0:
            return []
        key = "stove_unattended:kitchen"
        if not self._fresh(key, ctx.t, cooldown=300.0):
            return []
        sev = Severity.EMERGENCY if ctx.kitchen_absent_s >= 600 else Severity.URGENT
        return [
            self._emit(
                Event(
                    "stove_unattended",
                    ctx.t,
                    "kitchen",
                    sev,
                    {"absent_s": round(ctx.kitchen_absent_s, 1), "stove_power_w": 900},
                )
            )
        ]

    def _presence(self, ctx: Context) -> list[Event]:
        """Track whether a person is present long enough to matter."""
        if not ctx.night or ctx.person_count < 2 or ctx.expected_visitor:
            return []
        key = "unexpected_presence:house"
        if not self._fresh(key, ctx.t, cooldown=300.0):
            return []
        zone_name = ctx.zone.name if ctx.zone else "hallway"
        return [
            self._emit(
                Event(
                    "unexpected_presence",
                    ctx.t,
                    zone_name,
                    Severity.URGENT,
                    {
                        "person_count": ctx.person_count,
                        "second_person_present": True,
                        "night": True,
                        "expected_visitor": False,
                    },
                )
            )
        ]


    def _restlessness(self, ctx: Context) -> list[Event]:
        """Detect long, many-turn paths with little net displacement."""
        cfg = self.cfg.restlessness
        if not cfg.enabled or ctx.track is None or ctx.person_count > 1:
            return []
        if ctx.zone is not None and ctx.zone.name == "prayer_mat" and ctx.prayer_cycles >= 3:
            return []
        feats = ctx.track.to_features(ctx.zone.points if ctx.zone else None)
        if (float(feats.get("present_s") or 0) < cfg.min_present_s
                or float(feats.get("path_length") or 0) < cfg.min_path
                or int(feats.get("direction_changes") or 0) < cfg.min_direction_changes):
            return []
        key = f"restlessness:{ctx.zone.name if ctx.zone else 'house'}"
        cooldown = cfg.night_cooldown_s if ctx.night else cfg.cooldown_s
        if not self._fresh(key, ctx.t, cooldown=cooldown):
            return []
        return [
            self._emit(
                Event(
                    "restlessness",
                    ctx.t,
                    ctx.zone.name if ctx.zone else None,
                    Severity.WATCH if ctx.night else Severity.INFO,
                    {
                        **feats,
                        "night": ctx.night,
                        "person_count": ctx.person_count,
                        "pattern": "repeated_pacing",
                    },
                )
            )
        ]


class Gate:
    """Tracks which capture state we are in, and when a clip must be cut."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = State.BOOT
        self.entered = 0.0
        self.last_motion = 0.0
        self.last_person = 0.0
        self.last_trigger = -1e9

    def _to(self, state: State, t: float) -> None:
        self.state = state
        self.entered = t

    def update(self, t: float, motion: bool, person: bool, camera_ok: bool = True) -> State:
        if not camera_ok:
            self._to(State.DEGRADED, t)
            return self.state

        if motion:
            self.last_motion = t
        if person:
            self.last_person = t

        s = self.state
        if s in (State.DEGRADED, State.BOOT, State.PAUSED):
            if s != State.PAUSED:
                self._to(State.IDLE, t)
            return self.state

        if s == State.IDLE and motion:
            self._to(State.MOTION, t)
        elif s == State.MOTION:
            if person:
                self._to(State.PRESENT, t)
            elif t - self.last_motion > 1.5:
                self._to(State.IDLE, t)
        elif s == State.PRESENT and t - self.last_person > 10.0 and t - self.last_motion > 10.0:
            self._to(State.IDLE, t)
        elif s == State.COOLDOWN and t - self.entered > 30.0:
            self._to(State.IDLE, t)
        return self.state

    def may_trigger(self, t: float) -> bool:
        return self.state in (State.PRESENT, State.MOTION) and (t - self.last_trigger) > 20.0

    def triggered(self, t: float) -> None:
        self.last_trigger = t
        self._to(State.CAPTURE, t)

    def captured(self, t: float) -> None:
        self._to(State.COOLDOWN, t)


def night_at(local_hour: int) -> bool:
    return local_hour >= 22 or local_hour < 6
