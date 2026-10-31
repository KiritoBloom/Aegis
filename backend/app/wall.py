"""In-memory tracker and alert state for continuous multi-feed monitoring."""
from __future__ import annotations

import copy
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
OBJECT_EVERY_S = 5.0
# A repeated decoded frame is provably the same input. Reusing the previous
# analysis is exact, not an approximation, and keeps a paused or looping feed
# from burning both models on every upload.
REUSE_IDENTICAL_FRAMES = True
SESSION_TTL_S = 30 * 60
MAX_FEEDS = 6

_SESSIONS: dict[str, dict[str, "FeedState"]] = {}
_LOCK = threading.Lock()
_LAST_PRUNE = 0.0


def warmup() -> None:
    """Load and warm the local models before the first feed.

    Loading weights is not enough: torch registers its CPU kernels and oneDNN
    primitives on the first inference, which costs several seconds. Doing one
    real prediction per model here keeps that off the wall's first sample.
    """
    try:
        scene.warmup()
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
    first: float
    hits: int = 1
    vx: float = 0.0
    vy: float = 0.0
    desc: np.ndarray | None = None
    upright_h: float = 0.0
    upright_at: float = 0.0


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
        self._last_posture = "none"
        self._last_posture_at = 0.0
        self._fall_transition = False
        self._last_objects: list[dict] = []
        self._object_cache: list[dict] = []
        self._objects_at = 0.0
        self._last_frame: np.ndarray | None = None
        self._last_det: dict | None = None
        # Alert evidence only: a short rolling buffer in memory, written to a
        # clip only if this feed escalates. Quiet hours write nothing.
        self._raw_frames: deque[tuple[float, bytes]] = deque(maxlen=24)
        self._quiet_since: float | None = None
        self._saw_upright = False
        self._upright_h = 0.0
        self._upright_at = 0.0
        self._cooldown_by_track: dict[int, float] = {}
        self._primary_track_id: int | None = None
        self.alert: Alert | None = None
        self.alerts: list[Alert] = []

    def update_meta(self, meta: dict) -> None:
        self.name = str(meta.get("name") or self.name)[:60]
        self.kind = str(meta.get("kind") or self.kind)[:20]

    # -- tracking --------------------------------------------------------
    def _descriptor(self, frame, box: list[float]) -> np.ndarray | None:
        """A small torso colour histogram: the tie-breaker when boxes cross.

        Geometry alone cannot tell two people apart on the frame where their
        boxes overlap. Clothing colour usually can, and costs one tiny crop.
        """
        if frame is None or not hasattr(frame, "shape") or len(box) < 4:
            return None
        height, width = frame.shape[:2]
        x1 = max(0, min(width - 1, int(box[0] * width)))
        x2 = max(0, min(width, int(box[2] * width)))
        y1 = max(0, min(height - 1, int((box[1] + 0.15 * (box[3] - box[1])) * height)))
        y2 = max(0, min(height, int((box[1] + 0.70 * (box[3] - box[1])) * height)))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        crop = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [10, 8], [0, 180, 0, 256]).flatten()
        total = float(hist.sum())
        return (hist / total).astype(np.float32) if total > 0 else None

    def _assign_tracks(self, persons: list[dict], now: float,
                       descriptors: list[np.ndarray | None] | None = None) -> None:
        """Match bodies using predicted motion, size and torso appearance."""
        active = {tid: t for tid, t in self.tracks.items()
                  if now - t.last <= TRACK_TTL_S}
        descriptors = descriptors or [None] * len(persons)
        pairs: list[tuple[float, int, int]] = []
        for pi, person in enumerate(persons):
            box = tuple(person["box"])
            bw, bh = max(1e-6, box[2] - box[0]), max(1e-6, box[3] - box[1])
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            area = bw * bh
            desc = descriptors[pi] if pi < len(descriptors) else None
            for tid, track in active.items():
                dt = max(0.05, now - track.last)
                px = (track.box[0] + track.vx * dt,
                      track.box[1] + track.vy * dt,
                      track.box[2] + track.vx * dt,
                      track.box[3] + track.vy * dt)
                tbw = max(1e-6, track.box[2] - track.box[0])
                tbh = max(1e-6, track.box[3] - track.box[1])
                ratio = area / max(1e-6, tbw * tbh)
                if ratio < 0.35 or ratio > 2.8:
                    continue
                pcx, pcy = (px[0] + px[2]) / 2, (px[1] + px[3]) / 2
                gap = ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5
                iou = _iou(box, px)
                if iou < IOU_MATCH and gap > 0.22:
                    continue
                sim = 0.0
                if desc is not None and track.desc is not None:
                    sim = float(np.dot(desc, track.desc))
                score = iou * 0.60 + max(0.0, 0.30 - gap) * 0.75 + sim * 0.55
                pairs.append((score, pi, tid))
        used_persons: set[int] = set()
        used_tracks: set[int] = set()
        for _score, pi, tid in sorted(pairs, reverse=True):
            if pi in used_persons or tid in used_tracks:
                continue
            person, track = persons[pi], active[tid]
            box = tuple(person["box"])
            old_cx = (track.box[0] + track.box[2]) / 2
            old_cy = (track.box[1] + track.box[3]) / 2
            new_cx = (box[0] + box[2]) / 2
            new_cy = (box[1] + box[3]) / 2
            dt = max(0.05, now - track.last)
            track.vx = 0.65 * track.vx + 0.35 * (new_cx - old_cx) / dt
            track.vy = 0.65 * track.vy + 0.35 * (new_cy - old_cy) / dt
            track.box, track.last, track.hits = box, now, track.hits + 1
            desc = descriptors[pi] if pi < len(descriptors) else None
            if desc is not None:
                blend = desc if track.desc is None else 0.7 * track.desc + 0.3 * desc
                total = float(blend.sum())
                track.desc = (blend / total).astype(np.float32) if total > 0 else blend
            used_persons.add(pi)
            used_tracks.add(tid)
            person["track_id"] = tid
            person["track_hits"] = track.hits
            person["track_age_s"] = round(now - track.first, 1)
        for pi, person in enumerate(persons):
            if pi in used_persons:
                continue
            desc = descriptors[pi] if pi < len(descriptors) else None
            track = _Track(self._next_track, tuple(person["box"]), now, now,
                           desc=desc)
            self._next_track += 1
            self.tracks[track.id] = track
            person["track_id"] = track.id
            person["track_hits"] = 1
            person["track_age_s"] = 0.0
        for tid in [tid for tid, t in self.tracks.items() if now - t.last > TRACK_TTL_S]:
            del self.tracks[tid]
            self._cooldown_by_track.pop(tid, None)
        visible = [p for p in persons if p.get("track_id") in self.tracks]
        if visible:
            primary = max(visible, key=lambda p: (int(p.get("track_hits") or 0),
                                                   _area(p["box"])))
            self._primary_track_id = int(primary["track_id"])
        for person in persons:
            person["primary"] = person.get("track_id") == self._primary_track_id

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
        box = (primary or {}).get("box") or []
        aspect = ((box[2] - box[0]) / max(1e-6, box[3] - box[1])
                  if len(box) >= 4 else 0.0)
        return {
            "posture": (primary or {}).get("posture") or "unknown",
            "support_surface": (primary or {}).get("support_surface"),
            "track_id": (primary or {}).get("track_id"),
            "track_hits": (primary or {}).get("track_hits"),
            "distance": (primary or {}).get("distance"),
            "box": [round(float(v), 3) for v in box[:4]] if len(box) >= 4 else None,
            "box_height": round(float(box[3] - box[1]), 3) if len(box) >= 4 else 0.0,
            "geometry_fall": bool((primary or {}).get("geometry_fall")),
            "box_aspect": round(aspect, 2),
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
        if primary and primary.get("geometry_fall"):
            alert.summary = (f"{self.name}: a person's body changed from upright to "
                             "horizontal and stayed down.")
        self.alert = alert
        self.alerts.append(alert)
        self.alerts = self.alerts[-6:]
        track_id = int((primary or {}).get("track_id") or 0)
        self._cooldown_by_track[track_id] = now + COOLDOWN_S
        self._quiet_since = None
        self._saw_upright = False
        self._upright_h, self._upright_at = 0.0, 0.0
        track = self.tracks.get(track_id)
        if track is not None:
            track.upright_h, track.upright_at = 0.0, 0.0
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
            track_id = int((alert.evidence or {}).get("track_id") or 0)
            if data.get("status") == "rejected":
                # A rejected false positive must not silence the feed for ten
                # minutes. A verified incident does: one situation, one incident.
                self._cooldown_by_track[track_id] = min(
                    self._cooldown_by_track.get(track_id, 0.0), time.time() + 30.0)
            elif data.get("status") in ("verified", "unavailable"):
                self._cooldown_by_track[track_id] = max(
                    self._cooldown_by_track.get(track_id, 0.0), time.time() + COOLDOWN_S)
            return {**alert.as_dict(), "feed_id": self.id, "feed_name": self.name}

    def evidence_frames(self, seconds: float = 8.0) -> list[tuple[float, bytes]]:
        with self.lock:
            if not self._raw_frames:
                return []
            cutoff = self._raw_frames[-1][0] - seconds
            return [item for item in self._raw_frames if item[0] >= cutoff]

    # -- one sampled frame ----------------------------------------------
    def _same_frame(self, frame) -> bool:
        """True only for a pixel-identical frame; never a perceptual guess.

        A slow slide down a wall can move very few pixels per sample, so a
        similarity threshold could skip exactly the fall this feed exists to
        catch. Exact equality has no such failure mode.
        """
        if not REUSE_IDENTICAL_FRAMES or self._last_det is None:
            return False
        if not isinstance(frame, np.ndarray) or self._last_frame is None:
            return False
        if (frame.shape != self._last_frame.shape
                or frame.dtype != self._last_frame.dtype):
            return False
        return bool(np.array_equal(frame, self._last_frame))

    def _remember_frame(self, frame, det: dict) -> None:
        if not isinstance(frame, np.ndarray):
            self._last_frame, self._last_det = None, None
            return
        self._last_frame = frame.copy()
        # Stored before the tracker annotates persons, so a reused frame starts
        # from the same pristine analysis a fresh inference would return.
        self._last_det = copy.deepcopy(det)

    def process(self, frame, now: float, raw: bytes | None = None) -> dict:
        with self.lock:
            if raw:
                self._raw_frames.append((now, raw))
            if self._same_frame(frame):
                det = copy.deepcopy(self._last_det)
            else:
                # An empty object list is still an unsampled scene: a bed or
                # chair may enter later. The extra all-class pass on object-free
                # feeds is the price of not missing furniture under a body.
                detect_objects = not self._object_cache                     or now - self._objects_at >= OBJECT_EVERY_S
                det = scene.analyze_frame_array(
                    frame,
                    objects=self._object_cache if not detect_objects else None,
                    detect_objects=detect_objects,
                )
                if detect_objects:
                    self._object_cache = det.get("objects") or []
                    self._objects_at = now
                self._remember_frame(frame, det)
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
            descriptors = [self._descriptor(frame, p.get("box") or []) for p in persons]
            self._assign_tracks(persons, now, descriptors)

            unsupported = [p for p in persons
                           if p.get("posture") == "lying" and not p.get("support_surface")]
            primary = max(unsupported, key=lambda p: _area(p["box"])) if unsupported else None
            # Pose can keep calling a horizontal body "upright" when knees or
            # shoulders are occluded. The person box does not lie: a standing
            # body is tall, a fallen one is wide and low. Track that transition
            # per person and let it raise a candidate, never a family alert.
            geometry_candidates = []
            for person in persons:
                box = person.get("box") or [0, 0, 0, 0]
                height = max(1e-6, box[3] - box[1])
                width = max(1e-6, box[2] - box[0])
                aspect = width / height
                track = self.tracks.get(int(person.get("track_id") or 0))
                if track is None:
                    continue
                if aspect <= 0.85 and height >= 0.20:
                    track.upright_h, track.upright_at = height, now
                    self._upright_h, self._upright_at = height, now
                    continue
                ref_h = track.upright_h or self._upright_h
                ref_at = track.upright_at or self._upright_at
                support = bool(person.get("support_surface"))
                # A fast standing-to-horizontal change is a candidate even over
                # furniture; the visual gate decides whether it was a normal
                # lie-down. A body already resting on a bed from the start, or
                # lying after sitting on it, has no recent standing reference
                # and stays quiet.
                settled = (support and self._last_posture in
                           ("sitting", "bending", "kneeling", "lying")
                           and now - self._last_posture_at <= 3.0)
                if (not settled and ref_h
                        and now - ref_at <= (3.0 if support else 4.0)
                        and aspect >= (1.20 if support else 1.15)
                        and box[3] >= 0.55
                        and height <= ref_h * (0.65 if support else 0.78)):
                    person["geometry_fall"] = True
                    geometry_candidates.append(person)
            if primary is None and geometry_candidates:
                primary = max(geometry_candidates, key=lambda p: _area(p["box"]))
            if primary is not None and primary.get("geometry_fall"):
                primary["posture"] = "lying"
                primary["posture_source"] = "box_geometry"
            if primary is not None:
                self._last_lie = now
                self._lie_samples += 1
                if self._lying_since is None:
                    self._lying_since = now
                    self._fall_transition = (
                        self._last_posture in ("upright", "bending", "sitting", "kneeling")
                        and now - self._last_posture_at <= 2.5
                    )
            elif self._lying_since is not None and now - self._last_lie > POSTURE_GRACE_S:
                self._lying_since = None
                self._lie_samples = 0
                self._fall_transition = False
            lying_for = (now - self._lying_since) if self._lying_since is not None else 0.0
            if persons:
                posture_now = str(max(persons, key=lambda p: _area(p["box"])).get("posture") or "")
                if posture_now and posture_now != "unknown":
                    self._last_posture = posture_now
                    self._last_posture_at = now

            kind = None
            sustained = (lying_for >= LYING_ALERT_S
                         and self._lie_samples >= LYING_ALERT_SAMPLES)
            quick_fall = (self._fall_transition and lying_for >= 0.30
                          and self._lie_samples >= LYING_ALERT_SAMPLES)
            # A high-confidence transition can be visible for less than one
            # sampling interval on a fast clip. It is still only a candidate;
            # SAM/Muse decide whether the family is told.
            high_confidence_fall = (
                self._fall_transition
                and self._lie_samples >= 1
                and float(primary.get("conf") or 0) >= 0.70
            ) if primary is not None else False
            geometry_fall = bool(primary is not None and primary.get("geometry_fall"))
            if primary is not None and (sustained or quick_fall or high_confidence_fall
                                        or geometry_fall):
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
                    alert_track = int((active.evidence or {}).get("track_id") or 0)
                    if any(
                        p.get("posture") in ("upright", "bending", "sitting", "kneeling")
                        and (not alert_track or int(p.get("track_id") or 0) == alert_track)
                        for p in persons
                    ):
                        self._saw_upright = True
                    if self._quiet_since is None:
                        self._quiet_since = now
                    else:
                        # Silence is not recovery: a person can leave the frame
                        # or be occluded. Only a seen upright body closes it.
                        if (not active.recovered and self._saw_upright
                                and now - self._quiet_since >= 1.5):
                            active.recovered = True
                            active.recovered_at = now
                        if (active.recovered
                                and now - self._quiet_since >= RESOLVE_S):
                            active.active = False
                            active.resolved_at = now
                            resolved = {**active.as_dict(), "feed_id": self.id,
                                        "feed_name": self.name}
                            # A person who is up can fall again. The cooldown
                            # exists to stop repeat pages for one ongoing
                            # situation, not to silence the next one.
                            self._cooldown_by_track.pop(
                                int((resolved.get("evidence") or {}).get("track_id") or 0),
                                None,
                            )
                            self.alert = None
                            active = None
                            self._quiet_since = None
                else:
                    self._quiet_since = None
            elif kind is not None and now >= self._cooldown_by_track.get(
                    int((primary or {}).get("track_id") or 0), 0.0):
                active = self._raise(kind, lying_for, count, primary, now)
                triggered = active

            public_persons = [
                {**p, "track_id": p.get("track_id"),
                 "primary": bool(p.get("primary"))} for p in persons[:4]
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
