"""Two independent demo observations of the same SAM-tracked event.

This module never releases, resolves or changes the severity of an incident.
The image contains protected room context; the hex request contains occupancy
and timestamps only. Missing SAM masks are never replaced with bounding boxes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from . import render

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["fall", "controlled_lowering", "getting_up", "walking", "exercise", "uncertain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "observation": {"type": "string"},
        "evidence_frames": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["action", "confidence", "observation", "evidence_frames"],
}
DEFINITIONS = """Classify the observed movement, not the final posture alone.
fall = loss-of-balance/collapse descent; controlled_lowering = apparently
controlled sitting/kneeling/lying down; getting_up = rising from low posture;
exercise = bending/stretching with maintained control; walking = walking.
Use uncertain when the evidence cannot distinguish these. Do not infer identity,
intent, medical condition or unseen furniture. Cite numbered observations.
This is an observation, not a decision to alert or to declare someone safe."""
HEX_PROMPT = """Read a sequence of binary person-occupancy bitmaps from text only.
Each bitmap is 32 columns by 18 rows. Rows are top-to-bottom; each eight-character
hexadecimal row encodes 32 bits, most significant bit on the left. 1 is an
observed person; 0 is everything else. An unobserved mask means missing evidence,
not an empty room. The camera coordinates are fixed. Read the timestamps and
changing occupancy. No image, video, prior verdict or tool is provided.
""" + DEFINITIONS
IMAGE_PROMPT = """Read this chronological contact sheet left-to-right, then top-to-bottom.
Every tile has a frame number and timestamp. Human appearance is blurred;
room context is retained. The green rectangle identifies the target when SAM
observed it. Other people are not the target. Fully blurred frames mean missing
tracking evidence. No previous verdict is supplied.
""" + DEFINITIONS


def bitmap(objects, target_id: int, width: int, height: int) -> tuple[np.ndarray, bool]:
    mask = np.zeros((height, width), np.uint8)
    observed = False
    for obj in objects:
        if obj.obj_id != target_id:
            continue
        decoded = obj.raster()
        if decoded is None:
            continue
        mw, mh, bits = decoded
        x1, y1 = round(obj.x1 * width / obj.img_w), round(obj.y1 * height / obj.img_h)
        x2, y2 = round(obj.x2 * width / obj.img_w), round(obj.y2 * height / obj.img_h)
        if x2 <= x1 or y2 <= y1:
            continue
        patch = cv2.resize(np.frombuffer(bits, np.uint8).reshape(mh, mw),
                           (x2-x1, y2-y1), interpolation=cv2.INTER_NEAREST)
        left, top, right, bottom = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if right > left and bottom > top:
            mask[top:bottom, left:right] |= patch[top-y1:bottom-y1, left-x1:right-x1]
            observed = True
    small = (cv2.resize(mask.astype(np.float32), (32, 18), interpolation=cv2.INTER_AREA) >= .25).astype(np.uint8)
    return small, observed


def _sheet(images, observations):
    cols, tile_w, tile_h, caption = 4, 256, 144, 28
    out = np.full(((len(images)+cols-1)//cols*(tile_h+caption), cols*tile_w, 3), 20, np.uint8)
    for index, (image, observation) in enumerate(zip(images, observations)):
        x, y = index % cols * tile_w, index // cols * (tile_h+caption)
        h, w = image.shape[:2]
        scale = min(tile_w/w, tile_h/h)
        thumb = cv2.resize(image, (round(w*scale), round(h*scale)), interpolation=cv2.INTER_NEAREST)
        dx, dy = x+(tile_w-thumb.shape[1])//2, y+(tile_h-thumb.shape[0])//2
        out[dy:dy+thumb.shape[0], dx:dx+thumb.shape[1]] = thumb
        cv2.putText(out, f'{observation["frame"]:02d}  {observation["seconds"]:.2f}s',
                    (x+8, y+tile_h+19), cv2.FONT_HERSHEY_SIMPLEX, .45, (220, 230, 224), 1)
    return out


def build_evidence(src: Path, objects, target_id: int, folder: Path) -> dict:
    cap = cv2.VideoCapture(str(src))
    try:
        fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or fps <= 0 or count < 2:
            raise ValueError("The event video could not be decoded")
        indices = set(np.linspace(0, count-1, min(24, count)).round().astype(int).tolist())
        grouped = {}
        for obj in objects:
            grouped.setdefault(obj.frame, []).append(obj)
        observations, protected, silhouettes = [], [], []
        for index in range(count):
            ok, frame = cap.read()
            if not ok:
                break
            if index not in indices:
                continue
            h, w = frame.shape[:2]
            current = grouped.get(index, [])
            small, observed = bitmap(current, target_id, w, h)
            packed = np.packbits(small, axis=1, bitorder="big")
            observations.append({"frame": len(observations)+1, "seconds": round(index/fps, 3),
                                 "mask_observed": observed, "rows_hex": [row.tobytes().hex() for row in packed]})
            safe = render.privacy_frame(frame, current)[0] if current else cv2.GaussianBlur(frame, (71, 71), 0)
            for obj in current:
                if obj.obj_id == target_id:
                    cv2.rectangle(safe, (round(obj.x1*w/obj.img_w), round(obj.y1*h/obj.img_h)),
                                  (round(obj.x2*w/obj.img_w), round(obj.y2*h/obj.img_h)), (140, 220, 170), 2)
            protected.append(safe)
            silhouettes.append(cv2.cvtColor(small*255, cv2.COLOR_GRAY2BGR))
    finally:
        cap.release()
    if sum(row["mask_observed"] for row in observations) < 2:
        raise ValueError("SAM returned too few decoded target masks for a motion comparison")
    packet = {"width": 32, "height": 18, "observations": observations}
    data = json.dumps(packet, separators=(",", ":")).encode()
    for name, content in (("motion_hex.txt", data),
                          ("motion_image.jpg", cv2.imencode(".jpg", _sheet(protected, observations), [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()),
                          ("motion_masks.png", cv2.imencode(".png", _sheet(silhouettes, observations))[1].tobytes())):
        temp = folder / (name+".pending")
        temp.write_bytes(content)
        temp.replace(folder/name)
    image = (folder/"motion_image.jpg").read_bytes()
    return {"packet": packet, "image": image, "frames": len(observations),
            "observed_frames": sum(row["mask_observed"] for row in observations),
            "binary_bytes": len(observations)*72, "json_bytes": len(data),
            "image_sha256": hashlib.sha256(image).hexdigest(), "hex_sha256": hashlib.sha256(data).hexdigest()}
