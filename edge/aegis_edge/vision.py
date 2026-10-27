"""On-device perception: motion gate, person check, tracking, door-region watch.

Everything here runs on CPU. If a model is unavailable (no torch, no weights, no
disk) each stage degrades honestly and reports that it degraded — it never
silently pretends to see something it did not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .config import Config, Zone
from .geometry import Point, point_in_polygon

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
        return out

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
    """Posture, only when the pose model actually saw a body.

    Deliberately refuses to guess from the bounding box. A box is wide for a
    thousand reasons — a close camera, a wheelchair, a laundry basket, a wide
    coat — and inventing "lying" from one produced a false fall alert on a woman
    walking toward the camera on the first real clip we ran.

    `unknown` is a first-class answer: it never raises an alert on its own.
    """
    if not det.keypoints:
        return "unknown"
    from .geometry import posture_from_keypoints  # noqa: PLC0415

    return posture_from_keypoints(det.keypoints)


def blurness(frame: np.ndarray) -> float:
    """Laplacian variance. Low values mean a smeared or fogged lens."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def in_any_rest(det: Detection, zones: tuple[Zone, ...]) -> bool:
    for z in zones:
        if z.rest and point_in_polygon(det.foot, z.points):
            return True
    return False


def zone_at(det: Detection, zones: tuple[Zone, ...]) -> Zone | None:
    """The most specific zone containing the foot point (rest zones win ties)."""
    hits = [z for z in zones if not z.is_exclusion and point_in_polygon(det.foot, z.points)]
    if not hits:
        return None
    hits.sort(key=lambda z: (not z.rest, len(z.points)))
    return hits[0]
