"""In-memory tracker and alert state for continuous multi-feed monitoring."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import cv2
import numpy as np

from . import scene

LYING_ALERT_S = 1.0
LYING_ALERT_SAMPLES = 2
POSTURE_GRACE_S = 1.2
RESOLVE_S = 2.0
# One local situation, one incident. A person can stay on the floor for
# minutes; a second page for the same ongoing event is noise, not safety.
COOLDOWN_S = 600.0
TRACK_TTL_S = 3.0
IOU_MATCH = 0.25
SESSION_TTL_S = 30 * 60
MAX_FEEDS = 6

_SESSIONS: dict[str, dict[str, "FeedState"]] = {}
_LOCK = threading.Lock()
_LAST_PRUNE = 0.0


def warmup() -> None:
    """Load the local models before the first feed, not inside its first frame."""
    try:
        scene._models()
    except Exception:  # noqa: BLE001
        return


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
    return inter / union if union > 0 else 0.0


def decode(data: bytes) -> np.ndarray:
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("frame could not be decoded")
    return frame


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


@dataclass
class _Track:
    id: int
    box: tuple
    last: float


@dataclass
class Alert:
    id: str
    kind: str
    severity: str
    since: float
    evidence: dict
    summary: str
    active: bool = True
    resolved_at: float | None = None
    incident_id: str | None = None
    recovered: bool = False
    recovered_at: float | None = None
    verification: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "since": _iso(self.since),
            "active": self.active,
            "resolved_at": _iso(self.resolved_at),
            "summary": self.summary,
            "evidence": self.evidence,
            "incident_id": self.incident_id,
            "recovered": self.recovered,
            "recovered_at": _iso(self.recovered_at),
            "verification": self.verification,
        }


class FeedState:
    def __init__(self, meta: dict) -> None:
        self.lock = threading.Lock()
        self.id = str(meta.get("id") or "")[:40]
        self.name = str(meta.get("name") or self.id or "Feed")[:60]
        self.kind = str(meta.get("kind") or "file")[:20]
        self.started = time.time()
        self.seen = self.started
        self.samples = 0
        self.person_samples = 0
        self._sample_times: list[float] = []
        self.tracks: dict[int, _Track] = {}
        self._next_track = 1
        self._lying_since: float | None = None
        self._last_lie = 0.0
        self._lie_samples = 0
        self._last_objects: list[dict] = []
        self._object_cache: list[dict] = []
        self._objects_at = 0.0
        # Alert evidence only: a short rolling buffer in memory, written to a
        # clip only if this feed escalates. Quiet hours write nothing.
        self._raw_frames: deque[tuple[float, bytes]] = deque(maxlen=24)
        self._quiet_since: float | None = None
        self._cooldown_until = 0.0
        self.alert: Alert | None = None
        self.alerts: list[Alert] = []

    def update_meta(self, meta: dict) -> None:
        self.name = str(meta.get("name") or self.name)[:60]
        self.kind = str(meta.get("kind") or self.kind)[:20]

    # -- tracking --------------------------------------------------------
    def _assign_tracks(self, persons: list[dict], now: float) -> None:
        used: set[int] = set()
        for person in sorted(persons, key=lambda p: _area(p["box"]), reverse=True):
            box = tuple(person["box"])
            best: _Track | None = None
            best_iou = IOU_MATCH
            for track in self.tracks.values():
                if track.id in used or now - track.last > TRACK_TTL_S:
                    continue
                score = _iou(box, track.box)
                if score > best_iou:
                    best, best_iou = track, score
            if best is None:
                best = _Track(self._next_track, box, now)
                self._next_track += 1
                self.tracks[best.id] = best
            else:
                best.box = box
                best.last = now
            used.add(best.id)
            person["track_id"] = best.id
        for tid in [tid for tid, t in self.tracks.items() if now - t.last > TRACK_TTL_S]:
            del self.tracks[tid]

    # -- alert lifecycle -------------------------------------------------
    def _local_summary(self, kind: str, lying_for: float, count: int) -> str:
        if kind == "possible_fall":
            return (f"{self.name}: a person is lying with no bed or couch under them "
                    f"in this feed for {max(1, round(lying_for))}s.")
        return f"{self.name}: {count} people are in this feed."

    def _evidence(self, lying_for: float, count: int, primary: dict | None) -> dict:
        objects = []
        seen: set[str] = set()
        for obj in sorted(self._last_objects, key=lambda o: o.get("conf", 0), reverse=True):
            label = str(obj.get("label") or "")
            if not label or label in seen:
                continue
            seen.add(label)
            objects.append({"label": label, "conf": round(float(obj.get("conf") or 0), 2)})
            if len(objects) >= 5:
                break
        return {
            "posture": (primary or {}).get("posture") or "unknown",
            "support_surface": (primary or {}).get("support_surface"),
            "track_id": (primary or {}).get("track_id"),
            "person_count": count,
            "lying_for_s": round(lying_for, 1),
            "objects": objects,
            "sampled": True,
        }

    def _raise(self, kind: str, lying_for: float, count: int,
               primary: dict | None, now: float) -> Alert:
        alert = Alert(
            id=f"wa_{uuid.uuid4().hex[:10]}",
            kind=kind,
            severity="alert" if kind == "possible_fall" else "warn",
            since=self._lying_since or now,
            evidence=self._evidence(lying_for, count, primary),
            summary=self._local_summary(kind, lying_for, count),
        )
        self.alert = alert
        self.alerts.append(alert)
        self.alerts = self.alerts[-6:]
        self._cooldown_until = now + COOLDOWN_S
        self._quiet_since = None
        return alert

    def link_incident(self, alert_id: str, incident_id: str) -> None:
        with self.lock:
            alert = next((a for a in self.alerts if a.id == alert_id), None)
            if alert is not None:
                alert.incident_id = incident_id

    def set_verification(self, alert_id: str, **data) -> dict | None:
        with self.lock:
            alert = next((a for a in self.alerts if a.id == alert_id), None)
            if alert is None:
                return None
            alert.verification = {**alert.verification, **data,
                                  "updated_at": _iso(time.time())}
            if data.get("status") == "rejected":
                # A rejected false positive must not silence the feed for ten
                # minutes. A verified incident does: one situation, one incident.
                self._cooldown_until = min(self._cooldown_until, time.time() + 30.0)
            elif data.get("status") in ("verified", "unavailable"):
                self._cooldown_until = max(self._cooldown_until, time.time() + COOLDOWN_S)
            return {**alert.as_dict(), "feed_id": self.id, "feed_name": self.name}

    def evidence_frames(self, seconds: float = 8.0) -> list[tuple[float, bytes]]:
        with self.lock:
            if not self._raw_frames:
                return []
            cutoff = self._raw_frames[-1][0] - seconds
            return [item for item in self._raw_frames if item[0] >= cutoff]

    # -- one sampled frame ----------------------------------------------
    def process(self, frame, now: float, raw: bytes | None = None) -> dict:
        with self.lock:
            if raw:
                self._raw_frames.append((now, raw))
            detect_objects = now - self._objects_at >= 2.0 or not self._object_cache
            det = scene.analyze_frame_array(
                frame,
                objects=self._object_cache if not detect_objects else None,
                detect_objects=detect_objects,
            )
            if detect_objects:
                self._object_cache = det.get("objects") or []
                self._objects_at = now
            self._last_objects = self._object_cache
            self.samples += 1
            self.seen = now
            self._sample_times.append(now)
            self._sample_times = self._sample_times[-8:]
            if len(self._sample_times) >= 2:
                span = self._sample_times[-1] - self._sample_times[0]
                fps = (len(self._sample_times) - 1) / span if span > 0 else 0.0
            else:
                fps = 0.0
            persons = det.get("persons") or []
            count = len(persons)
            if count:
                self.person_samples += 1
            self._assign_tracks(persons, now)

            unsupported = [p for p in persons
                           if p.get("posture") == "lying" and not p.get("support_surface")]
            primary = max(unsupported, key=lambda p: _area(p["box"])) if unsupported else None
            if primary is not None:
                self._last_lie = now
                self._lie_samples += 1
                if self._lying_since is None:
                    self._lying_since = now
            elif self._lying_since is not None and now - self._last_lie > POSTURE_GRACE_S:
                self._lying_since = None
                self._lie_samples = 0
            lying_for = (now - self._lying_since) if self._lying_since is not None else 0.0

            kind = None
            if (primary is not None and lying_for >= LYING_ALERT_S
                    and self._lie_samples >= LYING_ALERT_SAMPLES):
                kind = "possible_fall"

            active = self.alert
            resolved = None
            triggered = None
            if active is not None:
                if kind == active.kind and primary is not None:
                    active.evidence = self._evidence(lying_for, count, primary)
                    active.recovered = False
                    active.recovered_at = None
                    active.summary = self._local_summary(kind, lying_for, count)
                    self._quiet_since = None
                elif kind is None:
                    if self._quiet_since is None:
                        self._quiet_since = now
                    else:
                        if not active.recovered and now - self._quiet_since >= 1.5:
                            active.recovered = True
                            active.recovered_at = now
                        if now - self._quiet_since >= RESOLVE_S:
                            active.active = False
                            active.resolved_at = now
                            resolved = {**active.as_dict(), "feed_id": self.id,
                                        "feed_name": self.name}
                            self.alert = None
                            active = None
                            self._quiet_since = None
                else:
                    self._quiet_since = None
            elif kind is not None and now >= self._cooldown_until:
                active = self._raise(kind, lying_for, count, primary, now)
                triggered = active

            public_persons = [
                {**p, "track_id": p.get("track_id")} for p in persons[:4]
            ]
            return {
                "feed_id": self.id,
                "feed_name": self.name,
                "at": _iso(now),
                "width": det["width"],
                "height": det["height"],
                "person_count": count,
                "persons": public_persons,
                "objects": det.get("objects") or [],
                "flags": det.get("flags") or [],
                "status": det.get("status"),
                "tracking": {
                    "samples": self.samples,
                    "person_samples": self.person_samples,
                    "fps": round(fps, 1),
                    "lying_for_s": round(lying_for, 1),
                    "alert_active": bool(self.alert and self.alert.active),
                    "recovered": bool(self.alert and self.alert.recovered),
                },
                "alert": ({**active.as_dict(), "feed_id": self.id,
                           "feed_name": self.name} if active is not None else None),
                "last_alert": ({**self.alerts[-1].as_dict(), "feed_id": self.id,
                                "feed_name": self.name} if self.alerts else None),
                "resolved": resolved,
                "trigger": {**triggered.as_dict(), "feed_id": self.id,
                            "feed_name": self.name} if triggered is not None else None,
            }


# -- sessions -------------------------------------------------------------
def _prune(now: float) -> None:
    global _LAST_PRUNE
    if now - _LAST_PRUNE < 60.0:
        return
    _LAST_PRUNE = now
    dead = [name for name, feeds in _SESSIONS.items()
            if all(now - f.seen > SESSION_TTL_S for f in feeds.values())]
    for name in dead:
        _SESSIONS.pop(name, None)


def process(session: str, feed_meta: dict, data: bytes, now: float | None = None) -> dict:
    now = now or time.time()
    frame = decode(data)
    feed_id = str(feed_meta.get("id") or "")[:40]
    if not feed_id:
        raise ValueError("feed id is required")
    with _LOCK:
        _prune(now)
        feeds = _SESSIONS.setdefault(session, {})
        state = feeds.get(feed_id)
        if state is None:
            if len(feeds) >= MAX_FEEDS:
                raise ValueError("at most six feeds")
            state = FeedState(feed_meta)
            feeds[feed_id] = state
        else:
            state.update_meta(feed_meta)
        state.seen = now
    return state.process(frame, now, raw=data)


def attach_review(session: str, feed_id: str, alert_id: str,
                  summary: str, action: str, model: str) -> dict | None:
    with _LOCK:
        state = _SESSIONS.get(session, {}).get(feed_id)
    if state is None:
        return None
    return state.attach_review(alert_id, summary, action, model)


def close(session: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session, None)


def link_incident(session: str, feed_id: str, alert_id: str, incident_id: str) -> None:
    state = get_state(session, feed_id)
    if state is not None:
        state.link_incident(alert_id, incident_id)


def evidence_frames(session: str, feed_id: str, seconds: float = 7.5) -> list[tuple[float, bytes]]:
    state = get_state(session, feed_id)
    return state.evidence_frames(seconds) if state is not None else []


def set_verification(session: str, feed_id: str, alert_id: str, **data) -> dict | None:
    state = get_state(session, feed_id)
    return state.set_verification(alert_id, **data) if state is not None else None


def latest_alert(session: str, feed_id: str) -> dict | None:
    state = get_state(session, feed_id)
    if state is None:
        return None
    with state.lock:
        if not state.alerts:
            return None
        alert = state.alerts[-1]
        return {**alert.as_dict(), "feed_id": state.id, "feed_name": state.name}


def get_state(session: str, feed_id: str) -> FeedState | None:
    with _LOCK:
        return _SESSIONS.get(session, {}).get(feed_id)
