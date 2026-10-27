"""Camera sources: file replay, USB, MJPEG phone, RTSP.

One interface, four backends, plus realtime pacing so a replayed reel produces
genuine latency numbers instead of a fast-forward.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from .config import Config

log = logging.getLogger("aegis.capture")


class Cues:
    """Timestamped sensor events for footage that cannot provide them.

    Clearly simulated: every cue is echoed to the backend with `source=replay`.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.items: list[dict] = []
        if path and Path(path).exists():
            try:
                self.items = json.loads(Path(path).read_text(encoding="utf-8"))
                log.info("loaded %d sensor cues from %s", len(self.items), path)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read cues: %s", exc)
        self._fired: set[int] = set()

    def due(self, t: float) -> list[dict]:
        out = []
        for i, c in enumerate(self.items):
            if i not in self._fired and t >= float(c.get("t", 0)):
                self._fired.add(i)
                out.append(c)
        return out

    def state(self, sensor: str, default=None):
        val = default
        for c in self.items:
            if c.get("sensor") == sensor:
                val = c.get("state", c.get("value", val))
        return val


class CameraSource:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.kind, self.target = self._parse(cfg.camera.source)
        self.cap: cv2.VideoCapture | None = None
        self.fps = cfg.camera.fps
        self._next_due = 0.0
        self._t0 = 0.0
        self._frame_index = 0
        self.cues = Cues(self._cue_path())

    @staticmethod
    def _parse(source: str) -> tuple[str, str]:
        if source.startswith("file:"):
            return "file", source[5:]
        if source.startswith("usb:"):
            return "usb", source[4:]
        if source.startswith(("http://", "https://")):
            return "http", source
        if source.startswith("rtsp://"):
            return "rtsp", source
        return "usb", source

    def _cue_path(self) -> Path | None:
        if self.kind != "file":
            return None
        p = Path(self.target)
        return p.with_suffix(".cues.json")

    def open(self) -> bool:
        if self.kind == "file":
            if not Path(self.target).exists():
                log.error("source file missing: %s", self.target)
                return False
            self.cap = cv2.VideoCapture(self.target)
        elif self.kind == "usb":
            self.cap = cv2.VideoCapture(int(self.target), cv2.CAP_DSHOW)
        else:
            self.cap = cv2.VideoCapture(self.target)
        if not self.cap or not self.cap.isOpened():
            log.error("could not open source %s", self.cfg.camera.source)
            return False
        if self.kind == "file":
            src_fps = self.cap.get(cv2.CAP_PROP_FPS) or 0
            self.fps = src_fps if 1 <= src_fps <= 60 else self.cfg.camera.fps
        self._t0 = time.monotonic()
        self._next_due = self._t0
        return True

    def read(self) -> tuple[np.ndarray | None, float]:
        """Returns (frame, timestamp in seconds since session start)."""
        if self.cap is None:
            return None, 0.0

        if self.cfg.camera.realtime:
            self._next_due += 1.0 / max(1.0, self.fps)
            delay = self._next_due - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 0.2))

        ok, frame = self.cap.read()
        if not ok or frame is None:
            if self.kind == "file" and self.cfg.camera.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._frame_index = 0
                ok, frame = self.cap.read()
            if not ok or frame is None:
                return None, time.monotonic() - self._t0

        self._frame_index += 1
        # File sources use deterministic media time, so a fast replay produces the
        # same dwell and latency numbers as a real-time one. Live sources use wall
        # clock, because for a camera that is the only honest clock.
        if self.kind == "file":
            t = self._frame_index / max(1.0, self.fps)
        else:
            t = time.monotonic() - self._t0
        if frame.shape[1] != self.cfg.camera.width or frame.shape[0] != self.cfg.camera.height:
            frame = cv2.resize(
                frame, (self.cfg.camera.width, self.cfg.camera.height),
                interpolation=cv2.INTER_AREA,
            )
        return frame, t

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
