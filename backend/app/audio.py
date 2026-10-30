"""Audio helpers: WAV encoding, duration, and event-gated transcription."""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import policy

log = logging.getLogger("aegis.audio")

LOUD_DBFS = float(os.environ.get("AUDIO_LOUD_DBFS", "-20"))
SCREAM_DBFS = float(os.environ.get("AUDIO_SCREAM_DBFS", "-15"))
SCREAM_LOUD_RATIO = float(os.environ.get("AUDIO_SCREAM_LOUD_RATIO", "0.30"))
_MIN_SECONDS = 0.20


@dataclass
class Levels:
    duration_s: float
    rms_dbfs: float
    peak_dbfs: float
    loud_ratio: float
    scream_like: bool


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def to_wav(source: str | Path, target: str | Path) -> Path:
    """Convert any uploaded audio to mono 16 kHz PCM for ASR and measurement."""
    src, dst = Path(source), Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    if ffmpeg:
        proc = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, text=True, timeout=45,
        )
        if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 44:
            return dst
        log.warning("ffmpeg could not decode audio: %s", (proc.stderr or "")[:200])
    # A WAV the caller already prepared is still usable without ffmpeg.
    if src.suffix.lower() == ".wav" and src.is_file():
        shutil.copyfile(src, dst)
        return dst
    raise ValueError("audio could not be decoded")


def levels(path: str | Path) -> Levels:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit PCM audio")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if samples.size == 0:
        return Levels(0.0, -120.0, -120.0, 0.0, False)

    duration = samples.size / max(1, rate)
    win = max(1, int(rate * 0.05))
    usable = (samples.size // win) * win
    chunks = samples[:usable].reshape(-1, win) if usable else samples.reshape(1, -1)
    rms = np.sqrt(np.mean(chunks ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-12)
    overall = float(20.0 * math.log10(math.sqrt(float(np.mean(samples ** 2))) + 1e-12))
    peak = float(20.0 * math.log10(float(np.max(np.abs(samples))) + 1e-12))
    loud_ratio = float(np.mean(db >= LOUD_DBFS))
    scream_like = bool(
        duration >= 0.35
        and overall >= SCREAM_DBFS
        and loud_ratio >= SCREAM_LOUD_RATIO
    )
    return Levels(round(duration, 2), round(overall, 1), round(peak, 1),
                  round(loud_ratio, 2), scream_like)


def assess(transcript: str, measured: Levels, hint: str = "") -> dict:
    """Turn a transcript + loudness into the few facts policy is allowed to use."""
    text = (transcript or "").strip()
    hint = (hint or "").strip().lower()
    cls = policy.classify_transcript(text) if text else "no_response"
    help_words = cls == "help"
    hinted = hint in ("help", "scream", "distress", "groan", "call_for_help")
    distress = bool(help_words or hinted or measured.scream_like)
    if help_words:
        kind = "help_words"
        confidence = 0.9
    elif probe_scream(hint):
        kind = "scream_like"
        confidence = 0.7
    elif measured.scream_like:
        kind = "scream_like"
        confidence = 0.55
    elif text:
        kind = "voice"
        confidence = 0.3
    else:
        kind = "noise" if measured.duration_s >= _MIN_SECONDS else "silence"
        confidence = 0.1
    if not distress:
        kind = "no_distress" if kind in ("voice", "noise", "silence") else kind
    return {
        "distress": distress,
        "kind": kind,
        "transcript": text[:600],
        "duration_s": measured.duration_s,
        "rms_dbfs": measured.rms_dbfs,
        "peak_dbfs": measured.peak_dbfs,
        "loud_ratio": measured.loud_ratio,
        "confidence": confidence,
        "method": "keyword+loudness heuristic; not a diagnosis",
    }


def probe_scream(hint: str) -> bool:
    return hint in ("scream", "distress", "groan")


def analyse(source: str | Path, workdir: str | Path, hint: str = "") -> tuple[dict, Path]:
    """Prepare the audio and return a measurement dict plus the 16 kHz WAV path.

    The caller transcribes the WAV, then calls `assess`. Splitting the two keeps
    the network call out of this module and makes the levels testable offline.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    wav = to_wav(source, work / "audio_16k.wav")
    measured = levels(wav)
    return {
        "wav": str(wav),
        "duration_s": measured.duration_s,
        "rms_dbfs": measured.rms_dbfs,
        "peak_dbfs": measured.peak_dbfs,
        "loud_ratio": measured.loud_ratio,
        "scream_like": measured.scream_like,
        "hint": hint,
    }, wav
