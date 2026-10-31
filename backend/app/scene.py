"""Local YOLO and pose perception. Frames stay on this machine."""
from __future__ import annotations

import math
import threading
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]

SCENE_OBJECTS = {
    "chair", "couch", "bed", "dining table", "tv", "laptop", "remote",
    "cell phone", "microwave", "oven", "sink", "refrigerator",
    "book", "clock", "vase",
}
SUPPORT_LABELS = {"bed", "couch"}
_YOLO = None
_POSE = None
_LOCK = threading.Lock()
_INFER = threading.Lock()


def _models():
    global _YOLO, _POSE
    with _LOCK:
        if _YOLO is None:
            from ultralytics import YOLO  # noqa: PLC0415

            _YOLO = YOLO(str(ROOT / "yolov8n.pt"))
        if _POSE is None:
            from ultralytics import YOLO  # noqa: PLC0415

            _POSE = YOLO(str(ROOT / "yolov8n-pose.pt"))
    return _YOLO, _POSE


def warmup() -> None:
    """Load both models and run one real prediction through each.

    Torch registers CPU ops and oneDNN primitives lazily on the first
    inference; on this machine that costs seconds, far more than the weights.
    """
    yolo, pose = _models()
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    with _INFER:
        yolo.predict(frame, imgsz=416, verbose=False)
        pose.predict(frame, imgsz=448, verbose=False)


def posture_from_keypoints(kp: list[tuple[float, float, float]] | None) -> str:
    """Classify posture from 17 COCO keypoints in one uniform coordinate scale.

    Coordinates must be aspect-correct: x and y need the same divisor, otherwise
    a wide box turns a standing person into a lying one.
    """
    if not kp or len(kp) < 17:
        return "unknown"
    ls, rs, lh, rh = kp[5], kp[6], kp[11], kp[12]
    shoulders = [point for point in (ls, rs) if point[2] >= 0.3]
    hips = [point for point in (lh, rh) if point[2] >= 0.3]
    if len(shoulders) + len(hips) < 3 or not shoulders or not hips:
        return "unknown"
    sh = (sum(p[0] for p in shoulders) / len(shoulders),
          sum(p[1] for p in shoulders) / len(shoulders))
    hp = (sum(p[0] for p in hips) / len(hips),
          sum(p[1] for p in hips) / len(hips))
    dx, dy = hp[0] - sh[0], hp[1] - sh[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return "unknown"
    angle = abs(math.degrees(math.atan2(dx, dy)))
    knees = [point for point in (kp[13], kp[14]) if point[2] >= 0.3]
    knee_y = sum(p[1] for p in knees) / len(knees) if knees else None
    if angle > 70:
        return "lying"
    if angle > 45:
        if knee_y is not None and hp[1] > knee_y - 0.02:
            return "sitting"
        return "bending"
    if knee_y is not None and knee_y > hp[1] and hp[1] > sh[1]:
        if hp[1] - knee_y > -0.02 and angle > 25:
            return "kneeling"
    return "upright"


def _intersection(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    return max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)


def _cover(inner: tuple[float, float, float, float],
           outer: tuple[float, float, float, float]) -> float:
    """How much of the inner box lies inside the outer box."""
    base = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return _intersection(inner, outer) / base if base > 0 else 0.0


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter = _intersection(a, b)
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def _pose_people(pose, frame) -> list[dict]:
    """Find people with pose when the detector misses a lying body."""
    h, w = frame.shape[:2]
    try:
        result = pose.predict(frame, imgsz=448, conf=0.40, verbose=False)
    except Exception:  # noqa: BLE001
        return []
    if not result:
        return []
    r = result[0]
    if r.boxes is None:
        return []
    kp_all = r.keypoints.data if r.keypoints is not None else None
    out: list[dict] = []
    for i, box in enumerate(r.boxes.xyxy):
        if str(r.names[int(r.boxes.cls[i])]) != "person":
            continue
        x1, y1, x2, y2 = (float(v) for v in box.tolist())
        norm = (x1 / w, y1 / h, x2 / w, y2 / h)
        if _is_edge_sliver(norm):
            continue
        posture = "unknown"
        if kp_all is not None and i < len(kp_all):
            scale = max(1.0, x2 - x1, y2 - y1)
            kp = kp_all[i].tolist()
            points = [((float(point[0]) - x1) / scale,
                       (float(point[1]) - y1) / scale,
                       float(point[2])) for point in kp]
            posture = posture_from_keypoints(points)
        out.append({
            "box": [round(v, 4) for v in norm],
            "conf": round(float(r.boxes.conf[i]), 3),
            "posture": posture,
            "seen_by": "pose",
        })
    return out



def _is_edge_sliver(box: list[float] | tuple[float, ...]) -> bool:
    """Reject edge artifacts without discarding a small, distant person."""
    w, h = box[2] - box[0], box[3] - box[1]
    if w < 0.02 or h < 0.025:
        return True
    if w < 0.15 and (box[0] <= 0.02 or box[2] >= 0.98):
        return True
    return h < 0.10 and (box[1] <= 0.02 or box[3] >= 0.98)

def _same_person(a: dict, b: dict) -> bool:
    abox, bbox = tuple(a["box"]), tuple(b["box"])
    if _iou(abox, bbox) >= 0.55:
        return True
    smaller = min((abox[2] - abox[0]) * (abox[3] - abox[1]),
                  (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return smaller > 0 and _intersection(abox, bbox) / smaller >= 0.72


def _merge_people(yolo_people: list[dict], pose_people: list[dict]) -> list[dict]:
    merged = [dict(p, seen_by="person") for p in yolo_people]
    for pp in pose_people:
        best_idx, best_iou = -1, 0.45
        for i, yp in enumerate(merged):
            score = _iou(tuple(pp["box"]), tuple(yp["box"]))
            if score > best_iou:
                best_idx, best_iou = i, score
        if best_idx < 0:
            # A pose-only box with no usable keypoints adds a duplicate person
            # without adding posture evidence. YOLO already covers that body.
            if pp.get("posture") == "unknown":
                continue
            merged.append(pp)
            continue
        yp = merged[best_idx]
        yp["box"] = pp["box"]
        yp["posture"] = pp["posture"]
        yp["conf"] = max(yp["conf"], pp["conf"])
        yp["seen_by"] = "pose+yolo"
    # Two detectors can both return the same body, and pose can return a
    # duplicate at a second scale. A duplicate is not a second person.
    kept: list[dict] = []
    for person in sorted(merged, key=lambda p: p["conf"], reverse=True):
        if any(_same_person(person, other) for other in kept):
            continue
        kept.append(person)
    return kept


def _pose_for_crop(pose, frame, box: tuple[float, float, float, float]) -> str:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    cx1, cy1 = max(0, int(x1 * w) - 10), max(0, int(y1 * h) - 10)
    cx2, cy2 = min(w, int(x2 * w) + 10), min(h, int(y2 * h) + 10)
    if cx2 - cx1 < 24 or cy2 - cy1 < 24:
        return "unknown"
    crop = frame[cy1:cy2, cx1:cx2]
    try:
        res = pose.predict(crop, imgsz=256, verbose=False)
    except Exception:  # noqa: BLE001
        return "unknown"
    if not res or res[0].keypoints is None or res[0].keypoints.data is None:
        return "unknown"
    kp = res[0].keypoints.data
    if len(kp) == 0:
        return "unknown"
    ch, cw = crop.shape[:2]
    skeletons = kp.tolist()
    best = max(skeletons, key=lambda skeleton: sum(1 for point in skeleton if point[2] >= 0.3))
    scale = max(1, cw, ch)
    points = [(float(point[0]) / scale, float(point[1]) / scale, float(point[2]))
              for point in best]
    return posture_from_keypoints(points)



def analyze_image_bytes(data: bytes) -> dict:
    import numpy as np  # noqa: PLC0415

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("image could not be decoded")
    return analyze_frame_array(frame)


def analyze_frame_array(frame, objects: list[dict] | None = None,
                        detect_objects: bool = True) -> dict:
    """Analyze one in-memory BGR frame; optionally reuse cached object boxes."""
    yolo, pose = _models()
    h, w = frame.shape[:2]
    with _INFER:
        return _analyze(frame, yolo, pose, w, h,
                        cached_objects=objects, detect_objects=detect_objects)


def _analyze(frame, yolo, pose, w: int, h: int,
            cached_objects: list[dict] | None = None,
            detect_objects: bool = True) -> dict:
    yolo_people: list[dict] = []
    objects: list[dict] = []
    if detect_objects or cached_objects is None:
        result = yolo.predict(frame, imgsz=416, conf=0.30, verbose=False)
    else:
        # Objects are cached, people are not: a person can enter at any time.
        objects = [dict(obj) for obj in cached_objects]
        result = yolo.predict(frame, imgsz=320, conf=0.30, classes=[0], verbose=False)
    if result:
        r = result[0]
        if r.boxes is not None:
            for i, box in enumerate(r.boxes.xyxy):
                x1, y1, x2, y2 = (float(v) for v in box.tolist())
                label = str(r.names[int(r.boxes.cls[i])])
                conf = float(r.boxes.conf[i])
                norm = (x1 / w, y1 / h, x2 / w, y2 / h)
                if label == "person":
                    if _is_edge_sliver(norm):
                        continue
                    yolo_people.append({
                        "box": [round(v, 4) for v in norm],
                        "conf": round(conf, 3),
                        "posture": "unknown",
                    })
                elif detect_objects and label in SCENE_OBJECTS:
                    objects.append({
                        "label": label,
                        "conf": round(conf, 3),
                        "box": [round(v, 4) for v in norm],
                    })
    persons = _merge_people(yolo_people, _pose_people(pose, frame))
    for person in persons:
        if person["seen_by"] == "person":
            person["posture"] = _pose_for_crop(pose, frame, tuple(person["box"]))
        height = max(1e-6, person["box"][3] - person["box"][1])
        person["distance"] = ("near" if height >= 0.50 else
                              "mid" if height >= 0.18 else "far")
    objects.sort(key=lambda o: o["conf"], reverse=True)
    objects = objects[:8]
    for person in persons:
        box = tuple(person["box"])
        foot = ((box[0] + box[2]) / 2, box[3])
        support = None
        best_cover = 0.0
        for obj in objects:
            if obj["label"] not in SUPPORT_LABELS:
                continue
            obox = tuple(obj["box"])
            ox1, oy1, ox2, oy2 = obox
            cover = _cover(box, obox)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            # A lying body hangs over the edge, so the foot point alone is not
            # enough. But overlap alone is not support either: a person on the
            # floor in front of a bed overlaps its box in perspective. Require
            # the body's foot or centre to be on the furniture.
            foot_on = ox1 <= foot[0] <= ox2 and oy1 <= foot[1] <= oy2
            centre_on = ox1 <= cx <= ox2 and oy1 <= cy <= oy2
            if (foot_on or centre_on) and cover >= 0.30 and cover > best_cover:
                support, best_cover = obj["label"], cover
        person["support_surface"] = support
    person_count = len(persons)
    lying = any(p["posture"] == "lying" for p in persons)
    possible_fall = any(
        p["posture"] == "lying" and not p["support_surface"] for p in persons
    )
    flags = []
    if person_count == 0:
        flags.append("no_person")
    if person_count > 1:
        flags.append("multiple_people")
    if lying:
        flags.append("lying")
    if possible_fall:
        flags.append("possible_fall")
    if person_count == 0 and objects:
        flags.append("objects_only")
    return {
        "width": w,
        "height": h,
        "person_count": person_count,
        "persons": persons[:4],
        "objects": objects,
        "flags": flags,
        "status": ("multiple_people" if person_count > 1 else
                   "person" if person_count == 1 else
                   "objects_only" if objects else "empty"),
    }
