"""Render SAM masks and tracks into the annotated clip."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("aegis.render")

# Palette from the design tokens: white person, sage mask, amber crossing.
PERSON = (255, 255, 255)
MASK_FILL = (122, 158, 137)
REDACT = (24, 30, 36)
INK = (11, 15, 20)


def _decode_mask(obj) -> tuple[int, int, bytes] | None:
    try:
        return obj.raster()
    except Exception:  # noqa: BLE001
        return None


def _primary_track(objects, resident_box: list[float] | None = None) -> int | None:
    """Pick the resident's SAM track.

    With a local hint (the box that triggered the alert) match by overlap, so a
    visitor walking through the frame is not mistaken for the resident. Without
    one, prefer the track seen longest, then the larger body: in a fall clip the
    person on the floor is both.
    """
    per: dict[int, list] = {}
    for o in objects:
        per.setdefault(o.obj_id, []).append(o)
    if not per:
        return None
    hint = tuple(resident_box) if resident_box and len(resident_box) == 4 else None

    def overlap(o) -> float:
        if hint is None:
            return 0.0
        ax1, ay1, ax2, ay2 = hint
        bx1, by1, bx2, by2 = o.box_norm
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
        return inter / union if union > 0 else 0.0

    if hint is not None:
        best = max(per, key=lambda k: max(overlap(o) for o in per[k]))
        if max(overlap(o) for o in per[best]) >= 0.15:
            return best
    return max(per, key=lambda k: (
        len(per[k]),
        sum((o.x2 - o.x1) * (o.y2 - o.y1) for o in per[k]) / len(per[k]),
    ))


def _write_still(video: Path, dst: Path) -> bool:
    """Write the last frame: the evidence state, not the pre-fall scene."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video))
        frame = None
        while True:
            ok, current = cap.read()
            if not ok:
                break
            frame = current
        cap.release()
        return bool(frame is not None and cv2.imwrite(str(dst), frame))
    except Exception:  # noqa: BLE001
        return False


def _blur_region(frame, mask, strength: int = 61):
    """Blur exactly the pixels under a boolean mask. Returns the count blurred."""
    import cv2
    sel = mask.astype(bool)
    count = int(sel.sum())
    if count == 0:
        return 0
    blur = cv2.GaussianBlur(frame, (strength, strength), 0)
    frame[sel] = blur[sel]
    return count


def privacy_frame(frame, objects, resident_box=None, *, dilate: float = 0.06):
    """Blur every human silhouette so a model can read posture, not a person.

    The body is blurred along its SAM mask, not its bounding box: a blurred
    rectangle still shows the outline of a head and shoulders, and it is
    obvious at a glance that a person is under it. Growing the mask by a few
    percent first hides the rim of the silhouette where a face would leak.
    """
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    primary = _primary_track(objects, resident_box)
    by_frame: dict[int, list] = {}
    for o in objects:
        by_frame.setdefault(o.frame, []).append(o)
    if not by_frame:
        return frame, {"blurred_people": 0, "mask_accurate": False}

    idx = max(by_frame)
    out = frame.copy()
    blurred = 0
    accurate = True
    for o in by_frame[idx]:
        x1, y1 = max(0, int(o.x1)), max(0, int(o.y1))
        x2, y2 = min(w, int(o.x2)), min(h, int(o.y2))
        if x2 <= x1 or y2 <= y1:
            continue
        bw, bh = x2 - x1, y2 - y1
        raster = _decode_mask(o)
        if raster:
            mw, mh, bits = raster
            mask = np.frombuffer(bits, dtype=np.uint8).reshape(mh, mw)
            mask = cv2.resize(mask, (bw, bh), interpolation=cv2.INTER_NEAREST)
            full = np.zeros((h, w), dtype=np.uint8)
            full[y1:y2, x1:x2] = (mask > 0).astype(np.uint8)
            grow = max(3, int(round(min(bw, bh) * dilate)))
            if grow % 2 == 0:
                grow += 1
            full = cv2.dilate(full, np.ones((grow, grow), np.uint8), iterations=1)
            if _blur_region(out, full) > 0:
                blurred += 1
            continue
        # No usable mask. An ellipse reads as a body; a rectangle reads as
        # "someone is being hidden here", so prefer the ellipse.
        accurate = False
        ellipse = np.zeros((h, w), dtype=np.uint8)
        pad = int(round(max(bw, bh) * 0.06))
        cv2.ellipse(ellipse, ((x1 + x2) // 2, (y1 + y2) // 2),
                    (max(1, bw // 2 + pad), max(1, bh // 2 + pad)),
                    0, 0, 360, 1, -1)
        if _blur_region(out, ellipse) > 0:
            blurred += 1
    return out, {"blurred_people": blurred, "mask_accurate": accurate,
                 "primary_track": primary}


def privacy_still(src: Path | str, dst: Path | str, objects,
                  resident_box: list[float] | None = None) -> dict:
    """Write the still a cloud model may see: bodies blurred, room intact.

    Muse needs the room and the shape of the body to answer "floor or bed".
    It never needs a face, so the person is blurred and the furniture is not.
    """
    try:
        import cv2
    except ImportError:
        return {"written": False, "reason": "opencv_missing"}

    src, dst = Path(src), Path(dst)
    if not src.exists() or not objects:
        return {"written": False, "reason": "no_input"}

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {"written": False, "reason": "unreadable"}
    frame = None
    while True:
        ok, current = cap.read()
        if not ok:
            break
        frame = current
    cap.release()
    if frame is None:
        return {"written": False, "reason": "unreadable"}

    out, manifest = privacy_frame(frame, objects, resident_box)
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = bool(cv2.imwrite(str(dst), out))
    return {"written": written, "still": dst.name if written else None, **manifest}


def privacy_still_from_boxes(src: Path | str, dst: Path | str, boxes) -> dict:
    """Write the blurred still without SAM, from detector boxes alone.

    Used when SAM fails or times out. Muse still needs to answer "floor or
    bed", and that answer does not depend on a pixel-perfect silhouette: a
    body-shaped blur over each person box keeps the room and the posture
    readable while hiding the face. Skipping the still entirely would mean
    skipping the model judgement, which is the one thing that catches a
    person resting on a bed.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return {"written": False, "reason": "opencv_missing"}

    src, dst = Path(src), Path(dst)
    if not src.exists():
        return {"written": False, "reason": "no_input"}

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {"written": False, "reason": "unreadable"}
    frame = None
    while True:
        ok, current = cap.read()
        if not ok:
            break
        frame = current
    cap.release()
    if frame is None:
        return {"written": False, "reason": "unreadable"}

    h, w = frame.shape[:2]
    out = frame.copy()
    blurred = 0
    for box in boxes or []:
        if box is None or len(box) != 4:
            continue
        x1, y1 = max(0, int(box[0] * w)), max(0, int(box[1] * h))
        x2, y2 = min(w, int(box[2] * w)), min(h, int(box[3] * h))
        if x2 - x1 <= 1 or y2 - y1 <= 1:
            continue
        ellipse = np.zeros((h, w), dtype=np.uint8)
        pad = int(round(max(x2 - x1, y2 - y1) * 0.06))
        cv2.ellipse(ellipse, ((x1 + x2) // 2, (y1 + y2) // 2),
                    (max(1, (x2 - x1) // 2 + pad), max(1, (y2 - y1) // 2 + pad)),
                    0, 0, 360, 1, -1)
        if _blur_region(out, ellipse, strength=71) > 0:
            blurred += 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    written = bool(cv2.imwrite(str(dst), out))
    return {"written": written, "still": dst.name if written else None,
            "blurred_people": blurred, "mask_accurate": False,
            "source": "detector_boxes"}


def scene_map(frame, detections, *, note: str = ""):
    """Draw the room map: every thing found in one frame, labelled.

    Furniture boxes come from the local detector; people get their own box and
    posture. This is a map of what the tracker believes the room contains, so it
    is drawn entirely on this machine and keeps the frame as it was.
    """
    try:
        import cv2
        import numpy as np  # noqa: F401  (imported for the caller's contract)
    except ImportError:  # pragma: no cover
        return frame, {"mapped": False, "reason": "opencv_missing"}

    h, w = frame.shape[:2]
    out = frame.copy()

    furniture = [d for d in detections if d.get("label") != "person"]
    drawn = 0
    for det in furniture:
        box = det.get("box") or []
        if len(box) != 4:
            continue
        x1, y1 = max(0, int(box[0] * w)), max(0, int(box[1] * h))
        x2, y2 = min(w - 1, int(box[2] * w)), min(h - 1, int(box[3] * h))
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        cv2.rectangle(out, (x1, y1), (x2, y2), MASK_FILL, 1)
        label = str(det.get("label") or "object")
        chip = f"{label} {float(det.get('conf') or 0):.2f}"
        _chip(out, chip, x1, max(0, y1 - 6), MASK_FILL)
        drawn += 1

    for person in detections:
        if person.get("label") != "person":
            continue
        box = person.get("box") or []
        if len(box) != 4:
            continue
        x1, y1 = max(0, int(box[0] * w)), max(0, int(box[1] * h))
        x2, y2 = min(w - 1, int(box[2] * w)), min(h - 1, int(box[3] * h))
        cv2.rectangle(out, (x1, y1), (x2, y2), PERSON, 2)
        posture = str(person.get("posture") or "person")
        # ASCII only: OpenCV's Hershey fonts have no glyph for a middle dot and
        # draw it as "??" instead.
        _chip(out, f"person - {posture}", x1, max(0, y1 - 6), INK, fg=(236, 240, 237))

    strip = note or f"mapped locally - {drawn} object(s)"
    (tw, th), _ = cv2.getTextSize(strip, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
    cv2.rectangle(out, (0, h - th - 14), (tw + 22, h), INK, -1)
    cv2.putText(out, strip, (11, h - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (226, 232, 227), 1, cv2.LINE_AA)
    return out, {"mapped": True, "objects_drawn": drawn}


def _chip(frame, text: str, x: int, y: int, bg, fg=(255, 255, 255)) -> None:
    """A small label above a box, kept inside the frame."""
    import cv2

    h, w = frame.shape[:2]
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    x = max(0, min(x, w - tw - 12))
    top = max(0, y - th - 8)
    cv2.rectangle(frame, (x, top), (x + tw + 10, top + th + 8), bg, -1)
    cv2.putText(frame, text, (x + 5, top + th + 2), cv2.FONT_HERSHEY_SIMPLEX,
                0.38, fg, 1, cv2.LINE_AA)


def render(src: Path | str, dst: Path | str, objects, *, timestamp: str = "",
            trail: bool = True, redact: bool = True, annotate: bool = True,
           resident_box: list[float] | None = None, blur_all: bool = False) -> dict:
    """Composite the overlay onto every frame. Returns a small manifest.

    The manifest is what the API returns to the UI: how many people were seen,
    how many were redacted, and whether the overlay is trustworthy at all.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        log.warning("opencv missing; cannot annotate")
        return {"rendered": False, "reason": "opencv_missing"}

    src, dst = Path(src), Path(dst)
    if not src.exists() or not objects:
        return {"rendered": False, "reason": "no_input"}

    primary = _primary_track(objects, resident_box)
    by_frame: dict[int, list] = {}
    for o in objects:
        by_frame.setdefault(o.frame, []).append(o)
    others = {o.obj_id for o in objects if o.obj_id != primary}

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return {"rendered": False, "reason": "unreadable"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 8.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmp = dst.with_suffix(".silent.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    feet: list[tuple[int, int]] = []
    idx = -1
    drawn = 0
    redacted_ids: set[int] = set()
    masks_decoded = 0
    mask_pixels_redacted = 0
    box_fallbacks = 0
    untracked_frames_blurred = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        frame_objs = by_frame.get(idx, [])

        # Redaction first, so the box we draw is never on top of a blurred face.
        if blur_all:
            if frame_objs:
                frame, _ = privacy_frame(frame, frame_objs, resident_box)
                redacted_ids.update(o.obj_id for o in frame_objs)
            else:
                # A missing SAM observation is not proof that a frame contains
                # no person. Do not expose that frame in the protected preview.
                frame = cv2.GaussianBlur(frame, (71, 71), 0)
                untracked_frames_blurred += 1
        elif redact:
            for o in frame_objs:
                if o.obj_id == primary or o.obj_id not in others:
                    continue
                raster = _decode_mask(o)
                if raster:
                    mw, mh, bits = raster
                    mask = np.frombuffer(bits, dtype=np.uint8).reshape(mh, mw)
                    mask = cv2.resize(mask, (int(o.x2 - o.x1), int(o.y2 - o.y1)),
                                      interpolation=cv2.INTER_NEAREST)
                    x1, y1 = max(0, int(o.x1)), max(0, int(o.y1))
                    x2, y2 = min(w, int(o.x2)), min(h, int(o.y2))
                    hh, ww = min(mask.shape[0], y2 - y1), min(mask.shape[1], x2 - x1)
                    if hh > 0 and ww > 0:
                        roi = frame[y1:y1 + hh, x1:x1 + ww]
                        blur = cv2.GaussianBlur(roi, (51, 51), 0)
                        sel = mask[:hh, :ww].astype(bool)
                        roi[sel] = blur[sel]
                        frame[y1:y1 + hh, x1:x1 + ww] = roi
                        redacted_ids.add(o.obj_id)
                        masks_decoded += 1
                        mask_pixels_redacted += int(sel.sum())
                else:
                    # Honest fallback: no mask means blur the padded box.
                    pad_x = (o.x2 - o.x1) * 0.08
                    pad_y = (o.y2 - o.y1) * 0.08
                    x1, y1 = max(0, int(o.x1 - pad_x)), max(0, int(o.y1 - pad_y))
                    x2, y2 = min(w, int(o.x2 + pad_x)), min(h, int(o.y2 + pad_y))
                    if x2 > x1 and y2 > y1:
                        roi = frame[y1:y2, x1:x2]
                        frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (51, 51), 0)
                        box_fallbacks += 1
                        redacted_ids.add(o.obj_id)
            for oid in redacted_ids:
                label = f"redacted: {len(redacted_ids)} non-resident"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                # Bottom-right: surveillance cameras burn their own timestamp
                # into the top strip, and covering that would look like tampering.
                bx, by = w - tw - 14, h - th - 22
                cv2.rectangle(frame, (bx - 6, by - th - 6), (bx + tw + 6, by + 8),
                              INK, -1)
                cv2.putText(frame, label, (bx, by), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (230, 230, 230), 1, cv2.LINE_AA)
                break

        for o in frame_objs:
            if annotate and o.obj_id == primary:
                x1, y1, x2, y2 = int(o.x1), int(o.y1), int(o.x2), int(o.y2)
                cv2.rectangle(frame, (x1, y1), (x2, y2), PERSON, 2)
                raster = _decode_mask(o)
                if raster:
                    mw, mh, bits = raster
                    mask = np.frombuffer(bits, dtype=np.uint8).reshape(mh, mw)
                    mask = cv2.resize(mask, (max(1, x2 - x1), max(1, y2 - y1)),
                                      interpolation=cv2.INTER_NEAREST)
                    x1c, y1c = max(0, x1), max(0, y1)
                    hh = min(mask.shape[0], h - y1c)
                    ww = min(mask.shape[1], w - x1c)
                    if hh > 0 and ww > 0:
                        roi = frame[y1c:y1c + hh, x1c:x1c + ww]
                        tint = np.zeros_like(roi)
                        tint[:] = MASK_FILL
                        sel = mask[:hh, :ww].astype(bool)
                        blended = (roi * 0.88 + tint * 0.12).astype(roi.dtype)
                        roi[sel] = blended[sel]
                        frame[y1c:y1c + hh, x1c:x1c + ww] = roi
                fx, fy = int(o.foot[0] * w), int(o.foot[1] * h)
                feet.append((fx, fy))
                drawn += 1

        if annotate and trail and len(feet) > 1:
            for a, b in zip(feet[-max(2, int(fps * 3)):-1], feet[-max(1, int(fps * 3)):]):
                cv2.line(frame, a, b, MASK_FILL, 1, cv2.LINE_AA)

        if annotate and timestamp:
            cv2.putText(frame, timestamp, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, PERSON, 1, cv2.LINE_AA)
        if annotate:
            # wordmark, low opacity so it reads as a watermark, not a bug
            overlay = frame.copy()
            cv2.putText(overlay, "AEGIS", (w - 84, 26), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, PERSON, 1, cv2.LINE_AA)
            cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

        writer.write(frame)

    cap.release()
    writer.release()

    # Re-encode to H.264 so every browser can play it, and drop the audio track.
    # Publish only a complete file: diagnostics may poll while encoding runs.
    encoded = dst.with_name(dst.stem + ".encoding.mp4")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(encoded)],
            check=True, capture_output=True, timeout=180,
        )
        encoded.replace(dst)
        tmp.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("annotated re-encode failed: %s", exc)
        if tmp.exists():
            tmp.replace(dst)
    finally:
        encoded.unlink(missing_ok=True)

    # privacy.jpg is the exact Muse input, not a still regenerated from a video.
    still = _write_still(dst, dst.with_suffix(".jpg")) if not annotate and not blur_all else False
    return {
        "rendered": dst.exists() and dst.stat().st_size > 1024,
        "frames_annotated": drawn,
        "frames_total": idx + 1,
        "people_redacted": len(redacted_ids),
        # The mask path is auditable: blob decodes and blurred pixels are
        # counted separately from the box fallback taken when a mask is bad.
        "masks_decoded": masks_decoded,
        "mask_pixels_redacted": mask_pixels_redacted,
        "box_fallbacks": box_fallbacks,
        "redaction_label": (f"blurred: {len(redacted_ids)} tracked people" if blur_all else
                            f"redacted: {len(redacted_ids)} non-resident person"
                            + ("s" if len(redacted_ids) != 1 else ""))
        if redacted_ids else None,
        "primary_track": primary,
        "still": dst.with_suffix(".jpg").name if still else None,
        "privacy_blurred": blur_all,
        "untracked_frames_blurred": untracked_frames_blurred,
    }
