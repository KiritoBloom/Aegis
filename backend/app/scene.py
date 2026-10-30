"""Local YOLO and pose perception. Frames stay on this machine."""
from __future__ import annotations

import math
import threading
from pathlib import Path

import cv2

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


def posture_from_keypoints(kp: list[tuple[float, float, float]] | None) -> str:
    """The edge's conservative posture classifier, copied for the wall.

    A single snapshot cannot see a fall transition. This only labels the pose
    in this frame; `unknown` is allowed and never becomes a warning by itself.
    """
    if not kp or len(kp) < 17:
        return "unknown"
    ls, rs, lh, rh = kp[5], kp[6], kp[11], kp[12]
    lk, rk = kp[13], kp[14]
    visible = [p for p in (ls, rs, lh, rh) if p[2] >= 0.3]
    if len(visible) < 3:
        return "unknown"
    sh = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hp = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    dx, dy = hp[0] - sh[0], hp[1] - sh[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return "unknown"
    angle = abs(math.degrees(math.atan2(dx, dy)))
    knees_visible = lk[2] >= 0.3 or rk[2] >= 0.3
    knee_y = (lk[1] + rk[1]) / 2 if knees_visible else None
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
        result = pose.predict(frame, imgsz=512, conf=0.40, verbose=False)
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
        posture = "unknown"
        if kp_all is not None and i < len(kp_all):
            bw, bh = max(1e-6, norm[2] - norm[0]), max(1e-6, norm[3] - norm[1])
            kp = kp_all[i].tolist()
            points = [((float(p[0]) / w - norm[0]) / bw,
                       (float(p[1]) / h - norm[1]) / bh,
                       float(p[2])) for p in kp]
            posture = posture_from_keypoints(points)
        out.append({
            "box": [round(v, 4) for v in norm],
            "conf": round(float(r.boxes.conf[i]), 3),
            "posture": posture,
            "seen_by": "pose",
        })
    return out


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
    points = [(float(p[0]) / cw, float(p[1]) / ch, float(p[2])) for p in kp[0].tolist()]
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
        if result:
            r = result[0]
            if r.boxes is not None:
                for i, box in enumerate(r.boxes.xyxy):
                    x1, y1, x2, y2 = (float(v) for v in box.tolist())
                    label = str(r.names[int(r.boxes.cls[i])])
                    conf = float(r.boxes.conf[i])
                    norm = (x1 / w, y1 / h, x2 / w, y2 / h)
                    if label == "person":
                        yolo_people.append({
                            "box": [round(v, 4) for v in norm],
                            "conf": round(conf, 3),
                            "posture": "unknown",
                        })
                    elif label in SCENE_OBJECTS:
                        objects.append({
                            "label": label,
                            "conf": round(conf, 3),
                            "box": [round(v, 4) for v in norm],
                        })
    else:
        objects = [dict(obj) for obj in cached_objects]
    persons = _merge_people(yolo_people, _pose_people(pose, frame))
    for person in persons:
        if person["seen_by"] == "person":
            person["posture"] = _pose_for_crop(pose, frame, tuple(person["box"]))
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
            # A lying body hangs over the edge of a bed, so the foot point is
            # not enough. Substantial overlap with the person's box counts.
            on_it = ox1 <= foot[0] <= ox2 and oy1 <= foot[1] <= oy2
            if (cover >= 0.30 or (on_it and cover >= 0.18)) and cover > best_cover:
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
