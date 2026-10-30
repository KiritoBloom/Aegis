"""Local detection: motion gate, YOLO, pose, and scene objects."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .config import Config, Zone
from .geometry import Point

log = logging.getLogger("aegis.vision")


@dataclass
class Detection:
    """One person, normalized to 0..1 image space."""

    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    keypoints: list[tuple[float, float, float]] | None = None

    @property
    def foot(self) -> Point:
        return ((self.x1 + self.x2) / 2, self.y2)

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    def as_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "box": [round(v, 4) for v in self.box],
            "conf": round(self.conf, 3),
        }


# COCO classes worth knowing for a care room. This is deliberately a small
# scene vocabulary, not "detect everything": a chair, a bed, a table and a
# television explain more about a posture than a hundred unrelated labels.
OBJECT_CLASSES = {
    56: "chair", 57: "couch", 59: "bed", 60: "dining table",
    62: "tv", 63: "laptop", 65: "remote", 67: "cell phone",
    68: "microwave", 69: "oven", 71: "sink", 72: "refrigerator",
    73: "book", 74: "clock", 75: "vase",
}
SUPPORT_CLASSES = {"bed", "couch"}


@dataclass
class SceneObject:
    """A non-person object in normalized frame space."""

    label: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float
    zone: str | None = None

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def centre(self) -> Point:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def overlap(self, other: "SceneObject | Detection") -> float:
        """Intersection over the other object's area; useful for support surfaces."""
        ax1, ay1, ax2, ay2 = self.box
        if isinstance(other, Detection):
            bx1, by1, bx2, by2 = other.box
        else:
            bx1, by1, bx2, by2 = other.box
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        base = (bx2 - bx1) * (by2 - by1)
        return inter / base if base > 0 else 0.0

    def as_dict(self, zone: str | None = None) -> dict:
        return {
            "label": self.label,
            "conf": round(self.conf, 3),
            "box": [round(v, 4) for v in self.box],
            "zone": zone if zone is not None else self.zone,
        }


def _zone_mask(shape: tuple[int, int], zones: tuple[Zone, ...]) -> np.ndarray | None:
    """Boolean mask (True = ignore) for zones that must never drive motion."""
    if not zones:
        return None
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    drew = False
    for z in zones:
        pts = np.array([[int(x * w), int(y * h)] for x, y in z.points], dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
        drew = True
    return mask.astype(bool) if drew else None


class MotionGate:
    """MOG2 background subtraction at 160x120. The always-on layer."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.w, self.h = cfg.motion.downscale
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=16, detectShadows=True
        )
        ignore_names = {z.name for z in cfg.zones}
        wanted = set(cfg.motion.ignore_zones)
        self._ignore_zones = tuple(z for z in cfg.zones if z.name in ignore_names and (
            z.name in wanted or z.type in wanted or z.private
        ))
        self._mask: np.ndarray | None = None
        self._streak = 0
        self.last_ratio = 0.0
        self.last_boxes: list[tuple[int, int, int, int]] = []

    def _ensure_mask(self, shape: tuple[int, int]) -> np.ndarray | None:
        if self._mask is None or self._mask.shape != shape:
            self._mask = _zone_mask(shape, self._ignore_zones)
        return self._mask

    def update(self, frame_bgr: np.ndarray) -> tuple[bool, float]:
        small = cv2.resize(frame_bgr, (self.w, self.h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        fg = self.bg.apply(gray)
        _, fg = cv2.threshold(fg, 25, 255, cv2.THRESH_BINARY)
        # shadows come back as 127; the threshold above already dropped them
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((5, 5), np.uint8))

        ignore = self._ensure_mask(fg.shape[:2])
        if ignore is not None:
            fg[ignore] = 0

        changed = float(np.count_nonzero(fg)) / fg.size
        self.last_ratio = changed
        if changed >= self.cfg.motion.min_changed_ratio:
            self._streak += 1
        else:
            self._streak = 0

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        self.last_boxes = [
            cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 40
        ]
        return self._streak >= self.cfg.motion.persistence_frames, changed

    def relearn(self) -> None:
        """Reset the background after a long quiet period or a camera bump."""
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=16, detectShadows=True
        )


class PersonDetector:
    """Person-only detection plus stable IDs.

    Primary: Ultralytics YOLO with ByteTrack. Fallback: OpenCV HOG people
    detector with an IoU tracker, so the pipeline still runs without torch.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.mode = "none"
        self._model = None
        self._pose = None
        self._hog = None
        self._tracker: SimpleTracker | None = None
        self._pose_tracker: SimpleTracker | None = None
        self._try_load()

    def _try_load(self) -> None:
        try:
            from ultralytics import YOLO  # noqa: PLC0415

            self._model = YOLO(self.cfg.person.model)
            self.mode = "yolo"
            try:
                self._pose = YOLO("yolov8n-pose.pt")
            except Exception as exc:  # noqa: BLE001
                log.warning("pose model unavailable (%s); fall detection is disabled", exc)
            log.info("person detector: YOLO (%s)", self.cfg.person.model)
        except Exception as exc:  # noqa: BLE001
            log.warning("YOLO unavailable (%s); falling back to HOG", exc)
            try:
                hog = cv2.HOGDescriptor()
                hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                self._hog = hog
                self._tracker = SimpleTracker()
                self.mode = "hog"
                log.info("person detector: HOG fallback")
            except Exception as exc2:  # noqa: BLE001
                log.error("no person detector available: %s", exc2)
                self.mode = "none"

    @property
    def available(self) -> bool:
        return self.mode != "none"

    def _filter(self, det: Detection, frame_area: float = 1.0) -> bool:
        w = det.x2 - det.x1
        h = det.y2 - det.y1
        if w <= 0 or h <= 0:
            return False
        if det.area < self.cfg.person.min_area_ratio:
            return False
        if det.area > 0.90:
            return False
        return True

    def track(self, frame: np.ndarray, conf: float) -> list[Detection]:
        if self.mode == "yolo" and self._model is not None:
            return self._track_yolo(frame, conf)
        if self.mode == "hog" and self._hog is not None:
            return self._track_hog(frame)
        return []

    def _track_yolo(self, frame: np.ndarray, conf: float) -> list[Detection]:
        assert self._model is not None
        h, w = frame.shape[:2]
        try:
            results = self._model.track(
                frame,
                persist=True,
                classes=[0],
                imgsz=self.cfg.person.imgsz,
                conf=conf,
                iou=0.5,
                tracker=self.cfg.person.tracker,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("track() failed, retrying as plain detect: %s", exc)
            results = self._model.predict(
                frame, classes=[0], imgsz=self.cfg.person.imgsz, conf=conf, verbose=False
            )
        out: list[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            ids = r.boxes.id
            for i, box in enumerate(r.boxes.xyxy):
                x1, y1, x2, y2 = (float(v) for v in box.tolist())
                tid = int(ids[i]) if ids is not None else -1
                det = Detection(tid, x1 / w, y1 / h, x2 / w, y2 / h, float(r.boxes.conf[i]))
                if self._filter(det):
                    out.append(det)
        if not out and self._pose is not None:
            # The person box detector misses horizontal bodies: on our own fall
            # clip it found nobody once the man was on the floor, while pose
            # found him at 0.75. Falling back to a full-frame pose pass is what
            # keeps a fall from becoming "camera empty".
            out = self._pose_people(frame)
        return out

    def _pose_people(self, frame: np.ndarray, conf: float = 0.4) -> list[Detection]:
        """Full-frame pose detection, used when the box detector finds nobody."""
        assert self._pose is not None
        h, w = frame.shape[:2]
        try:
            results = self._pose.predict(frame, imgsz=512, conf=conf, verbose=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("pose fallback failed: %s", exc)
            return []
        dets: list[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            kp_all = r.keypoints.data if r.keypoints is not None else None
            for i, box in enumerate(r.boxes.xyxy):
                if str(r.names[int(r.boxes.cls[i])]) != "person":
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.tolist())
                det = Detection(-1, x1 / w, y1 / h, x2 / w, y2 / h, float(r.boxes.conf[i]))
                if not self._filter(det):
                    continue
                if kp_all is not None and i < len(kp_all):
                    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
                    det.keypoints = [
                        ((float(p[0]) - x1) / bw, (float(p[1]) - y1) / bh, float(p[2]))
                        for p in kp_all[i].tolist()
                    ]
                dets.append(det)
        if self._pose_tracker is None:
            self._pose_tracker = SimpleTracker()
        tracked = self._pose_tracker.update(dets)
        for det in tracked:
            det.track_id += 100000
        return tracked

    def _track_hog(self, frame: np.ndarray) -> list[Detection]:
        assert self._hog is not None and self._tracker is not None
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
        rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        dets: list[Detection] = []
        for (x, y, bw, bh), wt in zip(rects, weights):
            det = Detection(
                -1,
                x / 320.0,
                y / 240.0,
                (x + bw) / 320.0,
                (y + bh) / 240.0,
                float(min(0.99, max(0.0, wt))),
            )
            if self._filter(det):
                dets.append(det)
        return self._tracker.update(dets)

    def keypoints(self, frame: np.ndarray, det: Detection) -> list[tuple[float, float, float]] | None:
        """Pose keypoints for one person crop, in crop-relative normalized coords."""
        if self._pose is None:
            return None
        h, w = frame.shape[:2]
        x1 = max(0, int(det.x1 * w) - 8)
        y1 = max(0, int(det.y1 * h) - 8)
        x2 = min(w, int(det.x2 * w) + 8)
        y2 = min(h, int(det.y2 * h) + 8)
        if x2 - x1 < 24 or y2 - y1 < 24:
            return None
        crop = frame[y1:y2, x1:x2]
        try:
            res = self._pose.predict(crop, imgsz=256, verbose=False)
        except Exception:  # noqa: BLE001
            return None
        if not res or res[0].keypoints is None or res[0].keypoints.data is None:
            return None
        kp = res[0].keypoints.data
        if len(kp) == 0:
            return None
        arr = kp[0].tolist()
        ch, cw = crop.shape[:2]
        return [(p[0] / cw, p[1] / ch, p[2]) for p in arr]


class SceneDetector:
    """Sampled detector state for motion gating and object context."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.mode = "none"
        self._model = None
        if cfg.objects.enabled:
            self._load()

    def _load(self) -> None:
        try:
            from ultralytics import YOLO  # noqa: PLC0415

            self._model = YOLO(self.cfg.objects.model)
            self.mode = "yolo"
            log.info("scene objects: YOLO (%s), sampled every %.1fs",
                     self.cfg.objects.model, self.cfg.objects.every_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("scene object detector unavailable (%s)", exc)
            self.mode = "none"

    @property
    def available(self) -> bool:
        return self.mode != "none"

    def detect(self, frame: np.ndarray) -> list[SceneObject]:
        if self._model is None:
            return []
        h, w = frame.shape[:2]
        try:
            results = self._model.predict(
                frame,
                imgsz=self.cfg.objects.imgsz,
                conf=self.cfg.objects.conf,
                classes=list(OBJECT_CLASSES),
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("scene object predict failed: %s", exc)
            return []
        out: list[SceneObject] = []
        for r in results:
            if r.boxes is None:
                continue
            for i, box in enumerate(r.boxes.xyxy):
                x1, y1, x2, y2 = (float(v) for v in box.tolist())
                cw, ch = (x2 - x1) / w, (y2 - y1) / h
                if cw * ch < self.cfg.objects.min_area_ratio:
                    continue
                cls_id = int(r.boxes.cls[i])
                label = OBJECT_CLASSES.get(cls_id)
                if not label:
                    continue
                out.append(SceneObject(
                    label, float(r.boxes.conf[i]),
                    x1 / w, y1 / h, x2 / w, y2 / h,
                ))
        out.sort(key=lambda o: o.conf, reverse=True)
        return out[: self.cfg.objects.max_items]


def support_surface(person: Detection, objects: list[SceneObject]) -> SceneObject | None:
    """The bed/couch the person is actually on, if object evidence is clear."""
    best: SceneObject | None = None
    best_score = 0.0
    for obj in objects:
        if obj.label not in SUPPORT_CLASSES:
            continue
        x1, y1, x2, y2 = obj.box
        inside = x1 <= person.foot[0] <= x2 and y1 <= person.foot[1] <= y2
        score = obj.overlap(person)
        # A lying body can hang over the edge, putting the foot point just
        # outside the bed. Substantial overlap of the person's box is enough.
        qualifies = (inside and score >= 0.18) or score >= 0.30
        if qualifies and score > best_score:
            best, best_score = obj, score
    return best


class SimpleTracker:
    """Minimal IoU tracker for the HOG path. ByteTrack when YOLO is present."""
    def __init__(self, iou_threshold: float = 0.3, max_age: int = 15) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self._next_id = 1
        self._tracks: dict[int, tuple[Detection, int]] = {}

    @staticmethod
    def _iou(a: Detection, b: Detection) -> float:
        ax1, ay1, ax2, ay2 = a.box
        bx1, by1, bx2, by2 = b.box
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = a.area + b.area - inter
        return inter / union if union > 0 else 0.0

    def update(self, dets: list[Detection]) -> list[Detection]:
        for tid in list(self._tracks):
            det, age = self._tracks[tid]
            self._tracks[tid] = (det, age + 1)
            if age + 1 > self.max_age:
                del self._tracks[tid]

        assigned: set[int] = set()
        for det in dets:
            best_id, best_iou = -1, self.iou_threshold
            for tid, (prev, _) in self._tracks.items():
                if tid in assigned:
                    continue
                score = self._iou(det, prev)
                if score >= best_iou:
                    best_id, best_iou = tid, score
            if best_id == -1:
                best_id = self._next_id
                self._next_id += 1
            det.track_id = best_id
            self._tracks[best_id] = (det, 0)
            assigned.add(best_id)
        return dets


class DoorRegionWatcher:
    """Measures how much the door area changed against a calibration frame."""

    def __init__(self, zone: Zone, ref: np.ndarray | None = None) -> None:
        self.zone = zone
        self.ref = ref

    def set_reference(self, frame: np.ndarray) -> None:
        self.ref = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def diff_ratio(self, frame: np.ndarray) -> float:
        if self.ref is None:
            return 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if gray.shape != self.ref.shape:
            gray = cv2.resize(gray, (self.ref.shape[1], self.ref.shape[0]))
        h, w = gray.shape
        pts = np.array([[int(x * w), int(y * h)] for x, y in self.zone.points], dtype=np.int32)
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [pts], 255)
        if not mask.any():
            return 0.0
        diff = cv2.absdiff(gray, self.ref)
        diff = cv2.GaussianBlur(diff, (5, 5), 0)
        changed = (diff > 25) & (mask > 0)
        total = int(np.count_nonzero(mask))
        return float(np.count_nonzero(changed)) / max(1, total)


def posture_for(det: Detection) -> str:
    """Map pose keypoints to upright, sitting, lying, or unknown."""
    if not det.keypoints:
        return "unknown"
    from .geometry import posture_from_keypoints  # noqa: PLC0415

    return posture_from_keypoints(det.keypoints)
