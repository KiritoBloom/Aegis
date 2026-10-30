"""The audio gate must fire on sustained loudness, not one door slam."""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "edge"))

from aegis_edge.audio_monitor import AudioMonitor, LoudnessGate, dbfs  # noqa: E402
from aegis_edge.config import Audio  # noqa: E402


def test_loudness_gate_needs_sustained_level_and_honours_cooldown():
    gate = LoudnessGate(threshold_dbfs=-30, sustain_s=0.6, cooldown_s=10)
    quiet = -55.0
    for i in range(10):
        assert gate.feed(quiet, i * 0.1) is False
    assert gate.feed(-20, 1.0) is False
    assert gate.feed(-20, 1.3) is False
    assert gate.feed(-20, 1.7) is True
    # The same loud episode cannot trigger twice; a quiet gap re-arms it.
    assert gate.feed(-20, 2.0) is False
    assert gate.feed(-20, 2.8) is False
    assert gate.feed(-55, 3.0) is False
    assert gate.feed(-20, 3.4) is False
    assert gate.feed(-55, 12.0) is False
    assert gate.feed(-20, 12.1) is False
    assert gate.feed(-20, 12.8) is True


def test_a_gated_event_is_a_valid_mono_wav(tmp_path):
    cfg = Audio(sample_rate=16000, block_s=0.1, pre_s=1.0)
    monitor = AudioMonitor(cfg, lambda path, metrics: None, out_dir=tmp_path)
    block = (np.ones(1600, dtype=np.int16) * 1200).tobytes()
    monitor._recent.append(block)
    monitor._recent.append(block)
    path = monitor._write_wav()
    assert path is not None and path.exists()
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 3200
