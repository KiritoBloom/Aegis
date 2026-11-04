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
# Judge physical width / height. The body must be wider than tall; padding or
# side-by-side camera views must not change that judgment.
LYING_MIN_ASPECT = 1.0
# A body that fell toward the camera runs off the bottom of the frame, so its
# visible box is shorter than the body and reads less horizontal than it is.
# These bodies are only held to a looser bar. It is a narrow relaxation on
# purpose: measured on the external corpus, a person bent over touching the
# floor reads "lying" with aspect 0.62-0.70 and a tall box, while a genuinely
# prone body reads 0.93-1.5 with a flat one. A looser bar here pages a family
# for someone doing stretches.
LYING_MIN_ASPECT_TRUNCATED = 0.95
POSTURE_GRACE_S = 1.2
RESOLVE_S = 2.0
# One local situation, one incident. A person can stay on the floor for
# minutes; a second page for the same ongoing event is noise, not safety.
COOLDOWN_S = 600.0
TRACK_TTL_S = 3.0
IOU_MATCH = 0.25
OBJECT_EVERY_S = 5.0
# The 320px person pass trades recall for speed, and a prone body far from the
# camera is exactly what it drops. When a feed saw a person moments ago and now
# sees none, these higher-resolution passes decide empty room vs. missed body.
# Cycle sizes across successive missed observations. One bounded retry per
# sample prevents an empty feed from monopolising the shared inference lock.
LOST_RECHECK_S = 6.0
LOST_RECHECK_IMGSZ = (448, 640, 960)
# A repeated decoded frame is provably the same input. Reusing the previous
# analysis is exact, not an approximation, and keeps a paused or looping feed
# from burning both models on every upload.
REUSE_IDENTICAL_FRAMES = True
# A bed or couch disappears from the object list for a sample or two while the
# body on it is still there. Remember the surface for this long so a detection
# gap cannot turn "resting on the bed" into "lying on the floor".
SUPPORT_MEMORY_S = 3.0
SUPPORT_MEMORY_IOU = 0.35
# How far down a body may drift and still count as the same resting position.
# In frame fractions. Beyond this the surface is stale, not remembered.
SUPPORT_MEMORY_DROP = 0.02
SESSION_TTL_S = 30 * 60
MAX_FEEDS = 6
# A browser tab that is closed without calling /session/close must not grow an
# unbounded per-session frame buffer in the API process.
MAX_SESSIONS = 12

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


def _inverted_body(person: dict) -> bool:
    """An overhead prone body can run vertically down the image.

    Require both observed legs above the hips and hips above the shoulders.
    A normal forward bend has feet below the hips and does not qualify.
    This is evidence for a sustained candidate, never an automatic alert.
    """
    kp = person.get("skeleton") or []
    if len(kp) < 17 or any(kp[i][2] < .65 for i in (5, 6, 11, 12, 13, 14, 15, 16)):
        return False
    height = max(1e-6, person["box"][3] - person["box"][1])
    shoulders = (kp[5][1] + kp[6][1]) / 2
    hips = (kp[11][1] + kp[12][1]) / 2
    return (shoulders - hips > height * .12
            and all(kp[knee][1] < kp[hip][1] - height * .06
                    and kp[ankle][1] < kp[hip][1] - height * .15
                    for hip, knee, ankle in ((11, 13, 15), (12, 14, 16))))


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
    # When this track was first seen resting on a bed or couch, continuously.
    # Zero when the person is not currently supported by furniture.
    support_since: float = 0.0
    posture: str = "none"
    posture_at: float = 0.0
    pre_lie_posture: str = "none"
    display_box: tuple | None = None


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
        self._recheck_index = 0
        self._lying_since: float | None = None
        self._lying_track_id: int | None = None
        self._last_lie = 0.0
        self._lie_samples = 0
        self._last_posture = "none"
        self._last_posture_at = 0.0
        self._fall_transition = False
        # The posture the body held immediately before the current lie started.
        # `_last_posture` becomes "lying" on the first lying sample, so using it
        # for the settle check made every sustained lie look like a lie-down
        # after resting, and a real collapse onto a bed could never re-arm.
        self._pre_lie_posture = "none"
        self._last_objects: list[dict] = []
        self._object_cache: list[dict] = []
        self._objects_at = 0.0
        self._person_seen_at = -1e9
        self._last_frame: np.ndarray | None = None
        self._last_det: dict | None = None
        # Alert evidence only: a short rolling buffer in memory, written to a
        # clip only if this feed escalates. Quiet hours write nothing.
        self._raw_frames: deque[tuple[float, bytes]] = deque(maxlen=80)
        self._quiet_since: float | None = None
        self._saw_upright = False
        self._upright_h = 0.0
        self._upright_at = 0.0
        self._cooldown_by_track: dict[int, float] = {}
        # Per-track sleeping surface, carried across a short detection gap.
        self._support_memory: dict[int, tuple[str, tuple, float]] = {}
        self._primary_track_id: int | None = None
        self.alert: Alert | None = None
        self.alerts: list[Alert] = []

    def update_meta(self, meta: dict) -> None:
        self.name = str(meta.get("name") or self.name)[:60]
        self.kind = str(meta.get("kind") or self.kind)[:20]

    # -- tracking --------------------------------------------------------
    def _recall_support(self, persons: list[dict], now: float) -> None:
        """Hold a sleeping surface across a short object-detection gap.

        Measured on the bed clip: YOLO returns no objects at all for ~3s while
        the body on the bed has not moved. Support then reads as None, and a
        person resting in bed looks exactly like a person on the floor, which
        is what raised a fall for someone lying on a bed.

        The memory is per track, expires in seconds, and is only used when the
        body is still in the same place and at the same height. A body that
        slid off the bed has moved down, so nothing is recalled and the normal
        candidate path still sees a fall.
        """
        self._support_memory = {k: v for k, v in self._support_memory.items()
                                if k in self.tracks}
        for person in persons:
            track_id = int(person.get("track_id") or 0)
            box = tuple(person.get("box") or ())
            label = person.get("support_surface")
            if label and track_id and len(box) == 4:
                self._support_memory[track_id] = (str(label), box, now)
                continue
            if label or not track_id:
                continue
            remembered = self._support_memory.get(track_id)
            if remembered is None:
                continue
            label, rbox, seen_at = remembered
            if now - seen_at > SUPPORT_MEMORY_S:
                self._support_memory.pop(track_id, None)
                continue
            if len(box) != 4 or person.get("posture") != "lying":
                continue
            # Recall is for a detection gap, not for a body in motion. The
            # body's bottom edge may barely move while its top edge drops,
            # because the feet stay put and the head falls: compare the box
            # centre too. A body that slid off the bed has moved down, and
            # this used to recall the bed onto a person already on the floor.
            dropped = (box[3] - rbox[3]) + ((box[1] + box[3]) - (rbox[1] + rbox[3])) / 2
            if (_iou(box, rbox) >= SUPPORT_MEMORY_IOU
                    and dropped <= SUPPORT_MEMORY_DROP):
                person["support_surface"] = label
                person["support_recalled"] = True

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
                dt = min(1.0, max(0.05, now - track.last))
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
                # A fall changes the centre but often keeps the feet anchored.
                anchored = (abs(box[3] - track.box[3]) < 0.12
                            and abs(cx - (track.box[0] + track.box[2]) / 2) < 0.13
                            and (bh < tbh * 0.65 or tbh < bh * 0.65))
                if iou < IOU_MATCH and gap > 0.22 and not anchored:
                    continue
                sim = 0.0
                if desc is not None and track.desc is not None:
                    # Histogram intersection in [0,1], unlike a dot product
                    # whose scale changes with the number of clothing colours.
                    sim = float(np.sqrt(desc * track.desc).sum())
                score = iou * 0.60 + max(0.0, 0.30 - gap) * 0.75 + sim * 0.55 + (0.2 if anchored else 0)
                pairs.append((score, pi, tid))
        # Globally maximise the assignment. Greedy matching can steal the only
        # plausible match from a second person during a crossing. At most four
        # detections makes this O(tracks * 2^people * people), without a solver.
        assignments: dict[int, tuple[float, list[tuple[int, int]]]] = {0: (0.0, [])}
        for tid in active:
            candidates = [(score, pi) for score, pi, candidate in pairs if candidate == tid]
            updated = dict(assignments)
            for mask, (total, chosen) in assignments.items():
                for score, pi in candidates:
                    if mask & (1 << pi):
                        continue
                    next_mask = mask | (1 << pi)
                    if total + score > updated.get(next_mask, (-1.0, []))[0]:
                        updated[next_mask] = (total + score, chosen + [(pi, tid)])
            assignments = updated
        matches = max(assignments.values(), key=lambda item: item[0])[1]
        used_persons: set[int] = set()
        for pi, tid in matches:
            person, track = persons[pi], active[tid]
            box = tuple(person["box"])
            old_cx = (track.box[0] + track.box[2]) / 2
            old_cy = (track.box[1] + track.box[3]) / 2
            new_cx = (box[0] + box[2]) / 2
            new_cy = (box[1] + box[3]) / 2
            dt = max(0.05, now - track.last)
            track.vx = 0.65 * track.vx + 0.35 * (new_cx - old_cx) / dt
            height_changed = abs((box[3] - box[1]) / max(1e-6, track.box[3] - track.box[1]) - 1) > 0.35
            # A collapsing box moves its centre without the body translating
            # by that distance. Predict from the feet during that shape change;
            # otherwise the next prediction overshoots the person on the floor.
            dy = box[3] - track.box[3] if height_changed else new_cy - old_cy
            track.vy = 0.65 * track.vy + 0.35 * dy / dt
            previous = track.display_box or track.box
            # Smooth detector jitter only. A fast movement/fall uses the new
            # observation immediately; these display boxes never drive policy.
            alpha = 0.65 if _iou(box, track.box) > 0.75 else 1.0
            track.display_box = tuple(alpha * b + (1 - alpha) * a
                                      for a, b in zip(previous, box))
            track.box, track.last, track.hits = box, now, track.hits + 1
            desc = descriptors[pi] if pi < len(descriptors) else None
            if desc is not None:
                blend = desc if track.desc is None else 0.7 * track.desc + 0.3 * desc
                total = float(blend.sum())
                track.desc = (blend / total).astype(np.float32) if total > 0 else blend
            used_persons.add(pi)
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
        if visible and not any(p["track_id"] == self._primary_track_id for p in visible):
            primary = max(visible, key=lambda p: (int(p.get("track_hits") or 0),
                                                   _area(p["box"])))
            self._primary_track_id = int(primary["track_id"])
        for person in persons:
            person["primary"] = person.get("track_id") == self._primary_track_id
            track = self.tracks[int(person["track_id"])]
            person["display_box"] = list(track.display_box or track.box)
            person["velocity"] = [round(track.vx, 4), round(track.vy, 4)]
            person["track_state"] = "confirmed" if track.hits >= 3 else "acquiring"

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

    def set_verification(self, alert_id: str, arm_cooldown: bool = True,
                         **data) -> dict | None:
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
                if arm_cooldown:
                    self._cooldown_by_track[track_id] = min(
                        self._cooldown_by_track.get(track_id, 0.0), time.time() + 30.0)
            elif data.get("status") in ("verified", "unavailable") and arm_cooldown:
                # A late completion for an incident that was already recovered
                # must not arm a ten-minute cooldown over the next real fall.
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
        started = time.perf_counter()
        with self.lock:
            # Time-based evidence retention must survive a faster analysis loop.
            # At most eight evidence frames/second; all frames still get analysed.
            if raw and (not self._raw_frames or now - self._raw_frames[-1][0] >= 0.12):
                self._raw_frames.append((now, raw))
            while self._raw_frames and now - self._raw_frames[0][0] > 9.0:
                self._raw_frames.popleft()
            reused = self._same_frame(frame)
            rechecks = 0
            queue_ms = inference_ms = 0.0
            if reused:
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
                queue_ms += det.get("timing", {}).get("queue_ms", 0)
                inference_ms += det.get("timing", {}).get("inference_ms", 0)
                if not det.get("person_count") \
                        and now - self._person_seen_at <= LOST_RECHECK_S:
                    # Keep multi-scale recall without stalling every camera
                    # behind three consecutive retries for an empty image.
                    recheck_imgsz = LOST_RECHECK_IMGSZ[self._recheck_index % len(LOST_RECHECK_IMGSZ)]
                    self._recheck_index += 1
                    rechecks = 1
                    rechecked = scene.analyze_frame_array(
                        frame, objects=det.get("objects") or self._object_cache,
                        detect_objects=False, person_imgsz=recheck_imgsz,
                        **({"cached_pose": det["_pose_people"]} if "_pose_people" in det else {}),
                    )
                    queue_ms += rechecked.get("timing", {}).get("queue_ms", 0)
                    inference_ms += rechecked.get("timing", {}).get("inference_ms", 0)
                    if rechecked.get("person_count"):
                        det = rechecked
                        det["recheck"] = True
                if detect_objects:
                    self._object_cache = det.get("objects") or []
                    self._objects_at = now
                self._remember_frame(frame, det)
            if det.get("person_count"):
                self._person_seen_at = now
                self._recheck_index = 0
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
            pixel_ratio = det["width"] / max(1, det["height"])
            count = len(persons)
            if count:
                self.person_samples += 1
            descriptors = [self._descriptor(frame, p.get("box") or []) for p in persons]
            self._assign_tracks(persons, now, descriptors)
            self._recall_support(persons, now)

            # Corroborate the initial pose with physical box geometry or a
            # confidently inverted full body. Once corroborated, keep following
            # that same lying track as it turns; an overhead body need not stay
            # horizontal in image coordinates. Losing the track or observing a
            # different posture ends that continuity.
            def _credible_lying(p: dict) -> bool:
                box = p.get("box") or [0, 0, 0, 0]
                height = max(1e-6, box[3] - box[1])
                width = max(1e-6, box[2] - box[0])
                aspect = width / height * pixel_ratio
                if _inverted_body(p):
                    return True
                track = self.tracks.get(int(p.get("track_id") or 0))
                if (p.get("track_id") == self._lying_track_id
                        and self._lying_since is not None
                        and now - self._last_lie <= POSTURE_GRACE_S
                        and track is not None and track.posture == "lying"):
                    return True
                # A body falling toward the camera is cut off by the bottom of
                # the frame, so the box understates its length and a real fall
                # looks less horizontal than it is. Judge a truncated box on a
                # looser width only; an untruncated one must look properly flat.
                if box[3] >= 0.97:
                    return aspect >= LYING_MIN_ASPECT_TRUNCATED
                return aspect >= LYING_MIN_ASPECT

            unsupported = [p for p in persons
                           if p.get("posture") == "lying"
                           and not p.get("support_surface")
                           and _credible_lying(p)]
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
                # Geometry thresholds were calibrated on 16:9 frames. Compare
                # physical image proportions, not normalized canvas fractions.
                aspect = width / height * pixel_ratio / (16 / 9)
                track = self.tracks.get(int(person.get("track_id") or 0))
                if track is None:
                    continue
                ref_h = track.upright_h
                ref_at = track.upright_at
                support = bool(person.get("support_surface"))
                track.support_since = (track.support_since or now) if support else 0.0
                if aspect <= 0.85 and height >= 0.20:
                    # A standing sighting refreshes the reference, but only
                    # when it is at least as tall as what we already know
                    # standing looks like. A body that fell away from the
                    # camera is foreshortened and also reads narrow, and
                    # refreshing from it overwrote the real standing height
                    # with the fallen one, so the collapse check below then
                    # compared the body against itself and saw nothing.
                    if (not ref_h or height >= ref_h * 0.78
                            or not ref_at or now - ref_at > 10.0):
                        track.upright_h, track.upright_at = height, now
                        self._upright_h, self._upright_at = height, now
                    continue
                # A fast standing-to-horizontal change is a candidate even over
                # furniture; the visual gate decides whether it was a normal
                # lie-down. A body already resting on a bed from the start, or
                # lying after sitting on it, has no recent standing reference
                # and stays quiet.
                before_lying = track.pre_lie_posture if track.posture == "lying" else track.posture
                settled = (support and before_lying in
                           ("sitting", "bending", "kneeling", "lying")
                            and now - track.posture_at <= 3.0)
                # Furniture is not a shield. Someone who stood up and came down
                # again can land on the same bed they got out of, and that is a
                # real fall. But a body that has been on the bed continuously
                # since before its standing reference was taken was already
                # lying there: the reference is stale, not a transition.
                if (support and track.support_since
                        and track.support_since <= ref_at):
                    continue
                # A body that lost most of its standing height is down, even
                # when the box is not obviously wide. A fall away from the
                # camera is foreshortened: measured on the corpus, a real fall
                # read aspect 1.15 at 31% of standing height, while a person
                # sitting back on a bed read 53%. A crouch or a sit-down keeps
                # most of the height, so this does not fire on those.
                collapsed = height <= ref_h * (0.65 if support else 0.78)
                very_low = height <= ref_h * 0.45
                if (not settled and ref_h
                        and now - ref_at <= (3.0 if support else 4.0)
                        and (aspect >= (1.20 if support else 1.15) or very_low)
                        and box[3] >= 0.55
                        and collapsed):
                    person["geometry_fall"] = True
                    geometry_candidates.append(person)
            if primary is None and geometry_candidates:
                primary = max(geometry_candidates, key=lambda p: _area(p["box"]))
            if primary is not None and primary.get("geometry_fall"):
                primary["posture"] = "lying"
                primary["posture_source"] = "box_geometry"
            if primary is not None:
                primary_id = int(primary["track_id"])
                if self._lying_track_id != primary_id:
                    self._lying_since = None
                    self._lie_samples = 0
                    self._lying_track_id = primary_id
                self._last_lie = now
                self._lie_samples += 1
                if self._lying_since is None:
                    self._lying_since = now
                    prior = self.tracks[primary_id]
                    self._pre_lie_posture = prior.posture
                    self._fall_transition = (
                        prior.posture in ("upright", "bending", "sitting", "kneeling")
                        and now - prior.posture_at <= 2.5
                    )
            elif self._lying_since is not None and now - self._last_lie > POSTURE_GRACE_S:
                self._lying_since = None
                self._lie_samples = 0
                self._fall_transition = False
                self._pre_lie_posture = "none"
            lying_for = (now - self._lying_since) if self._lying_since is not None else 0.0
            if persons:
                posture_now = str(max(persons, key=lambda p: _area(p["box"])).get("posture") or "")
                if posture_now and posture_now != "unknown":
                    self._last_posture = posture_now
                    self._last_posture_at = now
            for person in persons:
                track = self.tracks[int(person["track_id"])]
                posture = person.get("posture", "unknown")
                if posture not in ("unknown", "none"):
                    if posture == "lying" and track.posture != "lying":
                        track.pre_lie_posture = track.posture
                    track.posture, track.posture_at = posture, now

            kind = None
            # A candidate must be visible for more than one sample. Object
            # detection flickers: measured on real footage, YOLO drops the bed
            # or couch under a resting body for a second or more, which makes
            # `support_surface` momentarily None while the person is lying
            # exactly where they were. A single-sample trigger turned that
            # flicker into a fall, so the shortest path is a third of a second
            # of continuous lying (two samples at the console's ~3 fps).
            sustained = (lying_for >= LYING_ALERT_S
                         and self._lie_samples >= LYING_ALERT_SAMPLES)
            quick_fall = (self._fall_transition and lying_for >= 0.30
                          and self._lie_samples >= LYING_ALERT_SAMPLES)
            geometry_fall = bool(primary is not None
                                 and primary.get("geometry_fall")
                                 and self._lie_samples >= LYING_ALERT_SAMPLES)
            if primary is not None and (sustained or quick_fall or geometry_fall):
                kind = "possible_fall"

            active = self.alert
            resolved = None
            triggered = None
            if active is not None:
                if (kind == active.kind and primary is not None
                        and primary.get("track_id") == active.evidence.get("track_id")):
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
                    "processing_ms": round((time.perf_counter() - started) * 1000, 1),
                    "inference_ms": round(inference_ms, 1),
                    "queue_ms": round(queue_ms, 1),
                    "reused_frame": reused,
                    "rechecks": rechecks,
                    "confirmed_tracks": sum(t.hits >= 3 for t in self.tracks.values()
                                             if now - t.last < 0.1),
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
        if session not in _SESSIONS and len(_SESSIONS) >= MAX_SESSIONS:
            raise ValueError("too many wall sessions")
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
