"""Short clip assembly from the in-memory ring buffer."""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("aegis.clip")


@dataclass
class Frame:
    t: float
    jpeg: bytes
    width: int
    height: int


class RingBuffer:
    """Fixed-size memory buffer of encoded frames. No disk, no growth."""

    def __init__(self, seconds: float, fps: int) -> None:
        self.frames: deque[Frame] = deque(maxlen=int(seconds * fps) + 4)

    def add(self, frame_bgr: np.ndarray, t: float, quality: int = 80) -> None:
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return
        h, w = frame_bgr.shape[:2]
        self.frames.append(Frame(t, buf.tobytes(), w, h))

    def window(self, t0: float, t1: float) -> list[Frame]:
        return [f for f in self.frames if t0 <= f.t <= t1]

    def nearest(self, t: float) -> Frame | None:
        if not self.frames:
            return None
        return min(self.frames, key=lambda f: abs(f.t - t))

    def __len__(self) -> int:
        return len(self.frames)

    def bytes_in_ram(self) -> int:
        return sum(len(f.jpeg) for f in self.frames)


@dataclass
class ClipArtifacts:
    clip_path: str
    still_path: str
    thumb_path: str
    sha256: str
    duration_s: float
    frames: int
    bytes: int


def encode(frames: list[Frame], out_path: Path, fps: int, crf: int = 26) -> Path:
    """Write numbered JPEGs and let ffmpeg do the encoding. Deterministic codec."""
    if not frames:
        raise ValueError("no frames to encode")
    work = Path(tempfile.mkdtemp(prefix="aegis_clip_"))
    try:
        for i, f in enumerate(frames):
            (work / f"{i:06d}.jpg").write_bytes(f.jpeg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", str(work / "%06d.jpg"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path


def build(
    ring: RingBuffer,
    trigger_t: float,
    out_dir: Path,
    event_id: str,
    pre_s: float = 6.0,
    post_s: float = 4.0,
    fps: int = 10,
    crf: int = 26,
) -> ClipArtifacts | None:
    """Cut pre-roll + post-roll into an MP4 plus a still and a thumbnail.

    Returns None when there is not enough buffered footage; the caller then
    ships the incident without a clip rather than blocking the alert.
    """
    frames = ring.window(trigger_t - pre_s, trigger_t + post_s)
    if len(frames) < 3:
        log.warning("not enough buffered frames for %s (%d)", event_id, len(frames))
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    clip_path = out_dir / "clip.mp4"
    encode(frames, clip_path, fps=fps, crf=crf)

    still_src = ring.nearest(trigger_t) or frames[0]
    still = cv2.imdecode(np.frombuffer(still_src.jpeg, np.uint8), cv2.IMREAD_COLOR)
    still_path = out_dir / "still.jpg"
    thumb_path = out_dir / "thumb.jpg"
    if still is not None:
        cv2.imwrite(str(still_path), still)
        thumb = cv2.resize(still, (320, 180), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(thumb_path), thumb)

    digest = hashlib.sha256(clip_path.read_bytes()).hexdigest()
    size = clip_path.stat().st_size
    duration = (frames[-1].t - frames[0].t) if len(frames) > 1 else 0.0
    return ClipArtifacts(
        clip_path=str(clip_path),
        still_path=str(still_path),
        thumb_path=str(thumb_path),
        sha256=digest,
        duration_s=round(duration, 2),
        frames=len(frames),
        bytes=size,
    )


def probe_ok(path: Path) -> bool:
    """Confirm the container is something SAM will accept before we upload it."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return res.returncode == 0 and "h264" in res.stdout.lower()
    except Exception:  # noqa: BLE001
        return False
