"""Local YOLO and pose perception. Frames stay on this machine."""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]

SCENE_OBJECTS = {
    "chair", "couch", "bed", "dining table", "tv", "laptop", "remote",
    "cell phone", "microwave", "oven", "sink", "refrigerator",
    "book", "clock", "vase", "bench",
}
# Anything a person can be resting *on* rather than lying on the floor. A body
# on a couch or a bench is not a fall, and treating only beds as support made
# every other sleeping surface look like the floor.
SUPPORT_LABELS = {"bed", "couch", "bench"}
_YOLO = None
_POSE = None
_RECHECK = None
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


def _recheck_yolo():
    """The optional stronger person pass for a feed that just lost its person.

    Measured on the external corpus: yolov8m sees prone bodies the 320px fast
    pass drops (and that yolo11n/yolo26n miss entirely), but it also turned one
    ADL clip into a family alert with no net floor-fall gain. It is therefore
    opt-in: set AEGIS_RECHECK_MODEL=yolov8m.pt to use it. The default fast
    recheck at 448px keeps the measured-best restraint.
    """
    global _RECHECK
    name = os.environ.get("AEGIS_RECHECK_MODEL", "").strip()
    if not name:
        return None
    path = ROOT / name
    if not path.exists():
        return None
    with _LOCK:
        if _RECHECK is None:
            from ultralytics import YOLO  # noqa: PLC0415

            _RECHECK = YOLO(str(path))
    return _RECHECK


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
    # The lost-person recheck must not pay a cold model load at the moment a
    # body disappears; load and warm it in the same background startup task.
    stronger = _recheck_yolo()
    if stronger is not None:
        with _INFER:
            stronger.predict(frame, imgsz=320, verbose=False)


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
    # A seated person can have a vertical torso. Look at both legs rather than
    # calling every vertical shoulder-to-hip line "upright". Foreshortened
    # thighs and lower legs extending down are the front-facing seated case.
    seated_legs = 0
    for hip, knee, ankle in ((11, 13, 15), (12, 14, 16)):
        a, b, c = kp[hip], kp[knee], kp[ankle]
        if min(a[2], b[2], c[2]) < .5:
            continue
        shin_drop = c[1] - b[1]
        if shin_drop > .06 and abs(b[1] - a[1]) < shin_drop * .55:
            seated_legs += 1
    if angle <= 45 and seated_legs == 2:
        return "sitting"
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


def _is_floor_like(box: tuple[float, float, float, float]) -> bool:
    """True for a wide, flat box that runs off the bottom of the frame.

    Measured on the external corpus: a body falling onto a patterned rug had
    the rug detected as a "bed" spanning x=0.02..0.78 and y=0.73..1.00, which
    made a person on the floor look like a person on a bed and suppressed a
    real fall. The floor is not furniture. A real bed in the same corpus is
    never both this wide relative to its height and flush with the bottom
    edge, because some floor stays visible beneath it.
    """
    width = max(0.0, box[2] - box[0])
    height = max(1e-6, box[3] - box[1])
    return box[3] >= 0.985 and width >= 0.60 and (width / height) >= 2.2


# How far a lying body may hang below a surface's near edge and still be on it.
# A bed's near edge is closer to the camera, so it sits lower in the frame than
# the body resting on top of it. A body that extends further below that edge is
# in front of the bed, on the floor. Measured on the corpus: a stale bed box was
# covering a man who had already fallen onto the rug in front of it.
LYING_HANG = 0.06


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter = _intersection(a, b)
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def _skeleton(points, box, width: int, height: int, offset=(0, 0)) -> list[list[float]]:
    """Map a model's own image coordinates once; reject unusable joints."""
    x1, y1, x2, y2 = box
    pad_x, pad_y = (x2 - x1) * .08, (y2 - y1) * .08
    skeleton = []
    for point in points:
        x, y, confidence = float(point[0]) + offset[0], float(point[1]) + offset[1], float(point[2])
        if (not all(math.isfinite(v) for v in (x, y, confidence))
                or x <= 0 or y <= 0 or x > width or y > height
                or not (x1 - pad_x <= x <= x2 + pad_x and y1 - pad_y <= y <= y2 + pad_y)):
            skeleton.append([0.0, 0.0, 0.0])
        else:
            skeleton.append([round(x / width, 4), round(y / height, 4), round(confidence, 3)])
    return skeleton


def _posture(skeleton, width: int, height: int) -> str:
    visible = [p for p in skeleton if p[2] >= .3]
    scale = max(1, (max((p[0] for p in visible), default=0) - min((p[0] for p in visible), default=0)) * width,
                (max((p[1] for p in visible), default=0) - min((p[1] for p in visible), default=0)) * height)
    return posture_from_keypoints([(p[0] * width / scale, p[1] * height / scale, p[2]) for p in skeleton])


def _unrotate_xy(x: float, y: float, width: int, height: int, rotation: int) -> tuple[float, float]:
    """Undo a clockwise quarter-turn, using pixel or box-edge extents."""
    if rotation == 90:
        return y, height - x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return width - y, x
    return x, y


def _pose_people(pose, frame, rotation: int = 0) -> list[dict]:
    """Find people, mapping rotated observations back to the original frame."""
    h, w = frame.shape[:2]
    model_frame = np.ascontiguousarray(np.rot90(frame, -(rotation // 90))) if rotation else frame
    try:
        # A prone body scores lower than a standing one in every detector, and
        # the pose pass is the fallback that exists to catch exactly that case.
        # At 0.40 it was throwing away the bodies the object pass had already
        # dropped, so a real fall on the floor ended as an empty room.
        result = pose.predict(model_frame, imgsz=448, conf=0.25, verbose=False)
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
        if rotation:
            corners = [_unrotate_xy(x, y, w, h, rotation)
                       for x, y in ((x1, y1), (x2, y2))]
            x1, y1 = min(p[0] for p in corners), min(p[1] for p in corners)
            x2, y2 = max(p[0] for p in corners), max(p[1] for p in corners)
        norm = (x1 / w, y1 / h, x2 / w, y2 / h)
        if _is_edge_sliver(norm):
            continue
        posture = "unknown"
        strong = 0
        skeleton = []
        if kp_all is not None and i < len(kp_all):
            kp = kp_all[i].tolist()
            if rotation:
                # Joints are pixel centres; boxes are continuous image edges.
                # Preserve missing (0,0) joints before the inverse transform.
                kp = [[*_unrotate_xy(p[0], p[1], w - 1, h - 1, rotation), p[2]]
                      if p[0] != 0 or p[1] != 0 else [0, 0, 0] for p in kp]
            skeleton = _skeleton(kp, (x1, y1, x2, y2), w, h)
            strong = sum(1 for point in skeleton if point[2] >= 0.3)
            posture = _posture(skeleton, w, h)
        out.append({
            "box": [round(v, 4) for v in norm],
            "conf": round(float(r.boxes.conf[i]), 3),
            "posture": posture,
            "keypoints": strong,
            "skeleton": skeleton,
            "seen_by": "pose",
        })
    return out


def _pose_quality(person: dict) -> tuple[int, float]:
    """Compare complete observed body poses, never cherry-pick individual limbs."""
    body = (person.get("skeleton") or [])[5:17]
    confident = [p[2] for p in body if p[2] >= .5]
    return len(confident), sum(confident)


def _prone_rotation(person: dict, width: int, height: int) -> int:
    box = person["box"]
    if (person["posture"] != "lying"
            and (box[2] - box[0]) * width < (box[3] - box[1]) * height * 1.5):
        return 0
    skeleton = person.get("skeleton") or []
    shoulders = [p for p in skeleton[5:7] if p[2] >= .5]
    hips = [p for p in skeleton[11:13] if p[2] >= .5]
    if not shoulders or not hips:
        return 90
    dx = (sum(p[0] for p in hips) / len(hips)
          - sum(p[0] for p in shoulders) / len(shoulders)) * width
    dy = (sum(p[1] for p in hips) / len(hips)
          - sum(p[1] for p in shoulders) / len(shoulders)) * height
    if abs(dx) > abs(dy):
        return 90 if dx > 0 else 270
    return 180 if dy < 0 else 0



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
    # Two overlapping boxes can be two people. Distinct, confidently observed
    # torsos must not be collapsed just because one box contains the other.
    ka, kb = a.get("skeleton") or [], b.get("skeleton") or []
    if len(ka) >= 13 and len(kb) >= 13:
        distances = [math.hypot(ka[i][0] - kb[i][0], ka[i][1] - kb[i][1])
                     for i in (5, 6, 11, 12) if min(ka[i][2], kb[i][2]) >= .5]
        size = min(math.hypot(abox[2]-abox[0], abox[3]-abox[1]),
                   math.hypot(bbox[2]-bbox[0], bbox[3]-bbox[1]))
        if len(distances) >= 3 and sum(distances) / len(distances) > .15 * size:
            return False
    if _iou(abox, bbox) >= 0.55:
        return True
    smaller = min((abox[2] - abox[0]) * (abox[3] - abox[1]),
                  (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return smaller > 0 and _intersection(abox, bbox) / smaller >= 0.72


def _merge_people(yolo_people: list[dict], pose_people: list[dict]) -> list[dict]:
    merged = [dict(p, seen_by="person") for p in yolo_people]
    pairs = sorted((( _iou(tuple(pp["box"]), tuple(yp["box"])), pi, yi)
                    for pi, pp in enumerate(pose_people) for yi, yp in enumerate(yolo_people)), reverse=True)
    assigned, used = {}, set()
    for score, pi, yi in pairs:
        if score > .45 and pi not in assigned and yi not in used:
            assigned[pi] = yi
            used.add(yi)
    for pi, pp in enumerate(pose_people):
        best_idx = assigned.get(pi, -1)
        if best_idx < 0:
            # A pose-only box with no usable keypoints adds a duplicate person
            # without adding posture evidence. YOLO already covers that body.
            # But a box the pose model found with a real skeleton is a body the
            # object pass missed, and dropping it because the posture read was
            # inconclusive is how a person on the floor became an empty room.
            if pp.get("posture") == "unknown" and int(pp.get("keypoints") or 0) < 6:
                continue
            merged.append(pp)
            continue
        yp = merged[best_idx]
        if int(pp.get("keypoints") or 0) >= 6:
            yp["box"] = pp["box"]
        yp["skeleton"] = pp.get("skeleton") or []
        yp["keypoints"] = pp.get("keypoints", 0)
        # "unknown" is an absence of a reading, not a reading. Letting it
        # overwrite a posture keeps a good answer from being lost when the
        # second opinion is inconclusive.
        if pp["posture"] != "unknown":
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


def _pose_for_crop(pose, frame, box: tuple[float, float, float, float]) -> dict:
    """Refine the target person, returning joints in full-frame coordinates."""
    unknown = {"posture": "unknown", "skeleton": [], "keypoints": 0}
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    cx1, cy1 = max(0, int(x1 * w) - 10), max(0, int(y1 * h) - 10)
    cx2, cy2 = min(w, int(x2 * w) + 10), min(h, int(y2 * h) + 10)
    if cx2 - cx1 < 24 or cy2 - cy1 < 24:
        return unknown
    crop = frame[cy1:cy2, cx1:cx2]
    try:
        res = pose.predict(crop, imgsz=384, conf=.25, verbose=False)
    except Exception:  # noqa: BLE001
        return unknown
    if not res or res[0].keypoints is None or res[0].keypoints.data is None:
        return unknown
    kp = res[0].keypoints.data
    if len(kp) == 0:
        return unknown
    skeletons = kp.tolist()
    boxes = res[0].boxes.xyxy.tolist() if res[0].boxes is not None else []
    if len(boxes) == len(skeletons):
        # Prefer the skeleton belonging to this crop's target body, not a
        # nearby bystander with more visible joints.
        scores = [_iou(((b[0] + cx1) / w, (b[1] + cy1) / h,
                        (b[2] + cx1) / w, (b[3] + cy1) / h), box) for b in boxes]
        best_index = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best_index] < 0.2:
            return unknown
        best = skeletons[best_index]
    else:
        best = max(skeletons, key=lambda skeleton: sum(1 for point in skeleton if point[2] >= 0.3))
    skeleton = _skeleton(best, (x1*w, y1*h, x2*w, y2*h), w, h, (cx1, cy1))
    return {"posture": _posture(skeleton, w, h), "skeleton": skeleton,
            "keypoints": sum(p[2] >= .3 for p in skeleton)}



def analyze_image_bytes(data: bytes) -> dict:
    import numpy as np  # noqa: PLC0415

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("image could not be decoded")
    return analyze_frame_array(frame)


def analyze_frame_array(frame, objects: list[dict] | None = None,
                         detect_objects: bool = True,
                         person_imgsz: int | None = None,
                         cached_pose: list[dict] | None = None) -> dict:
    """Analyze one in-memory BGR frame; optionally reuse cached object boxes.

    `person_imgsz` is the lost-person recheck: it runs the stronger detector
    when its weights are present, or the fast detector at a larger input when
    they are not.
    """
    yolo, pose = _models()
    if person_imgsz is not None:
        stronger = _recheck_yolo()
        if stronger is not None:
            yolo = stronger
        else:
            person_imgsz = max(person_imgsz, 448)
    h, w = frame.shape[:2]
    queued = time.perf_counter()
    with _INFER:
        acquired = time.perf_counter()
        result = _analyze(frame, yolo, pose, w, h,
                        cached_objects=objects, detect_objects=detect_objects,
                         person_imgsz=person_imgsz, cached_pose=cached_pose)
        result["timing"] = {"queue_ms": round((acquired - queued) * 1000, 1),
                            "inference_ms": round((time.perf_counter() - acquired) * 1000, 1)}
        return result


def _analyze(frame, yolo, pose, w: int, h: int,
             cached_objects: list[dict] | None = None,
             detect_objects: bool = True,
             person_imgsz: int | None = None,
             cached_pose: list[dict] | None = None) -> dict:
    yolo_people: list[dict] = []
    objects: list[dict] = []
    if detect_objects:
        result = yolo.predict(frame, imgsz=416, conf=0.30, verbose=False)
    else:
        # Objects are cached, people are not: a person can enter at any time.
        objects = [dict(obj) for obj in (cached_objects or [])]
        result = yolo.predict(frame, imgsz=person_imgsz or 320, conf=0.30,
                              classes=[0], verbose=False)
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
    full_pose = _pose_people(pose, frame) if cached_pose is None else cached_pose
    persons = _merge_people(yolo_people, full_pose)
    # Standing-trained pose networks miss sideways/face-down bodies or fold
    # their legs into the torso. One additional full-frame orientation preserves
    # scene context without enlarging an observed body to include the rug.
    # All returned joints
    # belong to that one real model observation, not a synthetic skeleton.
    prone = [(p, _prone_rotation(p, w, h)) for p in persons]
    prone = [(p, rotation) for p, rotation in prone if rotation]
    rotation = (max(prone, key=lambda item: (item[0]["box"][2] - item[0]["box"][0])
                    * (item[0]["box"][3] - item[0]["box"][1]))[1] if prone else 0)
    if not persons:
        rotation = 90
    if rotation and cached_pose is None:
        rotated_pose = _pose_people(pose, frame, rotation=rotation)
        if not persons:
            persons = _merge_people([], rotated_pose)
        else:
            # Association is one-to-one. A rotated bystander cannot donate
            # joints to two boxes, and an unmatched rotated guess adds nobody.
            pairs = sorted([(_iou(tuple(p["box"]), tuple(r["box"])), pi, ri)
                            for pi, p in enumerate(persons)
                            for ri, r in enumerate(rotated_pose)], reverse=True)
            used_people, used_poses = set(), set()
            for overlap, pi, ri in pairs:
                if overlap < .45 or pi in used_people or ri in used_poses:
                    continue
                used_people.add(pi)
                used_poses.add(ri)
                person, refined = persons[pi], dict(rotated_pose[ri])
                target_box = tuple(v * size for v, size in zip(person["box"], (w, h, w, h)))
                refined["skeleton"] = _skeleton(
                    [[p[0] * w, p[1] * h, p[2]] for p in refined["skeleton"]], target_box, w, h)
                refined["keypoints"] = sum(p[2] >= .3 for p in refined["skeleton"])
                refined["posture"] = _posture(refined["skeleton"], w, h)
                if _pose_quality(refined) > _pose_quality(person):
                    person.update(skeleton=refined["skeleton"], keypoints=refined["keypoints"],
                                  posture=(refined["posture"] if refined["posture"] != "unknown"
                                           else person["posture"]),
                                  conf=max(person["conf"], refined["conf"]),
                                  seen_by=person.get("seen_by", "pose") + "+rotated_pose")
    for person in persons:
        if person["posture"] == "unknown":
            refined = _pose_for_crop(pose, frame, tuple(person["box"]))
            if refined["posture"] != "unknown" or refined["keypoints"] > person.get("keypoints", 0):
                person.update(refined)
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
        # Only a body that could be resting on something can be supported by it.
        # A standing person in front of a bed has their box centre inside the
        # bed's box in perspective, which used to label them "on the bed" and
        # then hand that label to the same track after it fell to the floor.
        resting = person["posture"] in ("lying", "sitting", "bending", "kneeling")
        for obj in objects if resting else ():
            if obj["label"] not in SUPPORT_LABELS:
                continue
            obox = tuple(obj["box"])
            # A rug detected as a bed is the floor, and the floor supports
            # nobody. Skipping it here is what stops a fall onto the ground
            # from being filed as a lie-down on the bed behind it.
            if _is_floor_like(obox):
                continue
            ox1, oy1, ox2, oy2 = obox
            cover = _cover(box, obox)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            # A lying body hangs over the edge, so the foot point alone is not
            # enough. But overlap alone is not support either: a person on the
            # floor in front of a bed overlaps its box in perspective. Require
            # the body's foot or centre to be on the furniture.
            foot_on = ox1 <= foot[0] <= ox2 and oy1 <= foot[1] <= oy2
            centre_on = ox1 <= cx <= ox2 and oy1 <= cy <= oy2
            # A body resting on a bed is photographed above the bed's near
            # edge. A body that hangs further below that edge is in front of
            # the furniture, on the floor, even when the two boxes overlap.
            hangs_off = box[3] > oy2 + LYING_HANG
            if (foot_on or centre_on) and not hangs_off and cover >= 0.30 and cover > best_cover:
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
        "_pose_people": full_pose,
        "status": ("multiple_people" if person_count > 1 else
                   "person" if person_count == 1 else
                   "objects_only" if objects else "empty"),
    }
