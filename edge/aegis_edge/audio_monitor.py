"""Event-gated microphone monitoring; audio is retained only after a loudness trigger."""
from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

from .config import Audio

log = logging.getLogger("aegis.audio")


def dbfs(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    return 20.0 * np.log10(max(rms, 1.0) / 32768.0)


class LoudnessGate:
    """A sustained, baseline-aware loudness trigger with a cooldown."""

    def __init__(self, threshold_dbfs: float = -30.0, sustain_s: float = 0.6,
                 cooldown_s: float = 30.0) -> None:
        self.threshold = threshold_dbfs
        self.sustain_s = sustain_s
        self.cooldown_s = cooldown_s
        self._above_since: float | None = None
        self._last_trigger = -1e9
        self._armed = True
        self.baseline = -60.0

    def feed(self, level_dbfs: float, now: float) -> bool:
        # Adapt to a noisy room slowly, but require a real jump above the floor.
        floor = max(self.threshold, self.baseline + 12.0)
        if level_dbfs >= floor:
            if not self._armed:
                return False
            if self._above_since is None:
                self._above_since = now
            if (now - self._above_since >= self.sustain_s
                    and now - self._last_trigger >= self.cooldown_s):
                self._last_trigger = now
                self._above_since = None
                self._armed = False   # one event per loud episode
                return True
        else:
            self._above_since = None
            self._armed = True
            self.baseline = 0.98 * self.baseline + 0.02 * level_dbfs
        return False


class AudioMonitor:
    """Records PCM blocks and hands one WAV to `on_event` when the gate fires."""

    def __init__(self, cfg: Audio, on_event, out_dir: str | Path = ".tmp",
                 cue_state=None) -> None:
        self.cfg = cfg
        self.on_event = on_event
        self.out_dir = Path(out_dir)
        self.cue_state = cue_state or (lambda name, default=None: default)
        self.gate = LoudnessGate(cfg.threshold_dbfs, cfg.sustain_s, cfg.cooldown_s)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._recent: deque[bytes] = deque(maxlen=max(1, int(cfg.pre_s / max(0.02, cfg.block_s))))
        self._t0 = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        try:
            import sounddevice as sd  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            log.warning("audio monitor unavailable (sounddevice): %s", exc)
            return False
        block = max(1, int(self.cfg.sample_rate * self.cfg.block_s))
        try:
            self._stream = sd.InputStream(
                samplerate=self.cfg.sample_rate, channels=1, dtype="int16",
                blocksize=block, device=self.cfg.device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("audio monitor unavailable (device): %s", exc)
            self._stream = None
            return False
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._worker, name="aegis-audio",
                                        daemon=True)
        self._thread.start()
        log.info("audio monitor listening at %d Hz (local gate, no stream)",
                 self.cfg.sample_rate)
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("audio status: %s", status)
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self._recent.append(block)
            pcm = np.frombuffer(block, dtype=np.int16)
            now = time.monotonic() - self._t0
            if not self.gate.feed(dbfs(pcm), now):
                continue
            path = self._write_wav()
            if path is None:
                continue
            log.info("audio event captured (%d bytes, local gate)", len(block))
            try:
                self.on_event(path, {
                    "rms_dbfs": round(dbfs(pcm), 1),
                    "tv_on": self.cue_state("tv_on", None),
                    "radio_on": self.cue_state("radio_on", None),
                })
            except Exception as exc:  # noqa: BLE001
                log.warning("audio event handler failed: %s", exc)
            finally:
                path.unlink(missing_ok=True)

    def _write_wav(self) -> Path | None:
        data = b"".join(self._recent)
        if not data:
            return None
        path = self.out_dir / f"audio-event-{int(time.time())}.wav"
        try:
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(self.cfg.sample_rate)
                out.writeframes(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write audio event: %s", exc)
            return None
        return path
