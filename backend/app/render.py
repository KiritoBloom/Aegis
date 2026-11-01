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


def render(src: Path | str, dst: Path | str, objects, *, timestamp: str = "",
           trail: bool = True, redact: bool = True, annotate: bool = True,
           resident_box: list[float] | None = None) -> dict:
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
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        frame_objs = by_frame.get(idx, [])

        # Redaction first, so the box we draw is never on top of a blurred face.
        if redact:
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
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dst)],
            check=True, capture_output=True, timeout=180,
        )
        tmp.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("annotated re-encode failed: %s", exc)
        if tmp.exists():
            tmp.replace(dst)

    still = _write_still(dst, dst.with_suffix(".jpg")) if not annotate else False
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
        "redaction_label": (f"redacted: {len(redacted_ids)} non-resident person"
                            + ("s" if len(redacted_ids) != 1 else ""))
        if redacted_ids else None,
        "primary_track": primary,
        "still": dst.with_suffix(".jpg").name if still else None,
    }
