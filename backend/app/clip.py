"""Clip preparation for upload.

SAM bills per frame and per pixel. The clip the edge sends is already ~10 s, but
a reviewer can post anything, so the backend re-cuts every clip into the short
window SAM actually needs. The trajectory is preserved; the size is not.

This is also the last point before data leaves the house, so it is where the
media boundary is enforced rather than merely described.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("aegis.clip")

# 8 s at 8 fps = 64 frames. Enough to see a person walk through a door or fall
# and stay down; a quarter of the tokens of a 15 fps 30 s upload.
SAM_MAX_S = 8.0
SAM_FPS = 8
SAM_WIDTH = 768


def sam_ready(src: Path | str, dst: Path | str, *, max_s: float = SAM_MAX_S,
              fps: int = SAM_FPS, width: int = SAM_WIDTH) -> Path | None:
    """Trim, downscale and re-encode a clip for SAM. None means "don't upload"."""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-t", f"{max_s:g}", "-i", str(src),
        "-vf", f"fps={fps},scale={width}:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("sam_ready failed for %s: %s", src, exc)
        return None
    return dst if dst.exists() and dst.stat().st_size > 1024 else None


def duration_s(path: Path | str) -> float:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return round(float(res.stdout.strip()), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def frame_count(path: Path | str) -> int:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return int(res.stdout.strip().split(",")[0])
    except Exception:  # noqa: BLE001
        return 0
