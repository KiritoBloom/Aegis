"""Reproduce the continuous-wall performance measurement.

The wall samples every feed at ~2 fps. Each sample runs two models (a person
detector at 320/416 and a pose model at 448) plus a small tracker, so the
steady-state cost is almost entirely inference. This script measures that cost
on real external footage, with a synthetic fallback, and separates the phases
so a change can be attributed instead of guessed at.

    python scripts/bench_wall.py --mode dynamic
    python scripts/bench_wall.py --mode static
    python scripts/bench_wall.py --mode empty
    python scripts/bench_wall.py --mode cold
    python scripts/bench_wall.py --mode dynamic --profile

The wall keeps all-class object detection on an object-free feed: a bed can
appear later, and skipping that pass changed real corpus decisions.

Every number is wall time measured at run time. Model load and the first
inference are excluded from steady-state runs; `--mode cold` measures them.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / ".vendor"), str(ROOT / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# Four fixed-camera clips from the external corpus, two falls and two ADLs.
DEFAULT_CLIPS = (
    "gmd-sub2-fall-01.mp4",
    "gmd-sub2-adl-01.mp4",
    "gmd-sub2-fall-17.mp4",
    "gmd-sub2-adl-05.mp4",
)
EXTERNAL = ROOT / "data" / "footage" / "external"
WIDTH = 768  # the browser wall captures at most 768 px wide


def _sample_clip(path: Path, count: int) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    frames: list[np.ndarray] = []
    for i in range(count):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 1) / (count + 1)))
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if w > WIDTH:
            frame = cv2.resize(frame, (WIDTH, round(h * WIDTH / w)))
        frames.append(frame)
    cap.release()
    return frames


def _synthetic(count: int, size: tuple[int, int] = (432, 768)) -> list[np.ndarray]:
    """A moving rectangle on a noisy floor. Used when no footage is present."""
    h, w = size
    rng = np.random.default_rng(7)
    base = np.full((h, w, 3), 95, dtype=np.uint8)
    base[: h // 3] = (130, 125, 120)
    frames = []
    for i in range(count):
        frame = base.copy()
        x = 80 + i * 14
        cv2.rectangle(frame, (x, h // 2), (x + 70, h - 30), (55, 60, 65), -1)
        frame = np.clip(frame + rng.normal(0, 2.0, frame.shape), 0, 255).astype(np.uint8)
        frames.append(frame)
    return frames


def _empty_room(count: int, size: tuple[int, int] = (432, 768)) -> list[np.ndarray]:
    """Frames with no object the detector recognises, and a small moving box.

    Frames differ, so the identical-frame cache cannot explain a difference;
    the only variable is whether the empty object cache is trusted.
    """
    h, w = size
    rng = np.random.default_rng(11)
    base = np.full((h, w, 3), 110, dtype=np.uint8)
    frames = []
    for i in range(count):
        frame = base.copy()
        x = 40 + i * 9
        cv2.rectangle(frame, (x, h - 80), (x + 30, h - 40), (185, 185, 185), -1)
        frame = np.clip(frame + rng.normal(0, 1.5, frame.shape), 0, 255).astype(np.uint8)
        frames.append(frame)
    return frames


def _load_frames(mode: str, count: int) -> tuple[list[np.ndarray], str]:
    if mode == "empty":
        return _empty_room(count), "synthetic empty room"
    if EXTERNAL.is_dir():
        names = [n for n in DEFAULT_CLIPS if (EXTERNAL / n).exists()]
        if names:
            frames: list[np.ndarray] = []
            per_clip = max(1, count // len(names))
            for name in names:
                frames.extend(_sample_clip(EXTERNAL / name, per_clip))
            return frames, f"external/{names[0]} + {len(names) - 1} more"
    return _synthetic(count), "synthetic moving box"


class _Timers:
    """Wrap the two models and the two small per-frame helpers."""

    def __init__(self, scene, wall) -> None:
        self.wall = wall
        self.yolo, self.pose = scene._models()
        self._orig_yolo = self.yolo.predict
        self._orig_pose = self.pose.predict
        self._orig_desc = wall.FeedState._descriptor
        self._orig_track = wall.FeedState._assign_tracks
        self.calls: dict[str, list[float]] = {}

    def __enter__(self):
        timers = self

        def wrap(kind, original):
            def timed(*args, **kwargs):
                started = time.perf_counter()
                result = original(*args, **kwargs)
                elapsed = time.perf_counter() - started
                key = kind
                if kind == "pose":
                    key = "pose_crop" if kwargs.get("imgsz") == 256 else "pose_full"
                elif kind == "yolo":
                    key = "yolo_objects" if kwargs.get("classes") is None else "yolo_people"
                timers.calls.setdefault(key, []).append(elapsed)
                return result

            return timed

        def timed_descriptor(inner):
            def wrapper(*args, **kwargs):
                started = time.perf_counter()
                result = inner(*args, **kwargs)
                timers.calls.setdefault("descriptor", []).append(
                    time.perf_counter() - started)
                return result

            return wrapper

        def timed_track(inner):
            def wrapper(*args, **kwargs):
                started = time.perf_counter()
                result = inner(*args, **kwargs)
                timers.calls.setdefault("tracker", []).append(
                    time.perf_counter() - started)
                return result

            return wrapper

        self.yolo.predict = wrap("yolo", self._orig_yolo)
        self.pose.predict = wrap("pose", self._orig_pose)
        self.wall.FeedState._descriptor = timed_descriptor(self._orig_desc)
        self.wall.FeedState._assign_tracks = timed_track(self._orig_track)
        return self

    def __exit__(self, *exc):
        self.yolo.predict = self._orig_yolo
        self.pose.predict = self._orig_pose
        self.wall.FeedState._descriptor = self._orig_desc
        self.wall.FeedState._assign_tracks = self._orig_track


def _run(frames: list[np.ndarray], wall, scene, label: str, profile: bool):
    state = wall.FeedState({"id": label, "name": label})
    sample_ms: list[float] = []
    person_frames = 0
    started = time.perf_counter()
    with _Timers(scene, wall) as timers:
        if profile:
            profiler = cProfile.Profile()
            profiler.enable()
        for i, frame in enumerate(frames):
            t0 = time.perf_counter()
            result = state.process(frame, 1000.0 + i * 0.5)
            sample_ms.append((time.perf_counter() - t0) * 1000)
            person_frames += 1 if result["person_count"] else 0
        if profile:
            profiler.disable()
    elapsed = time.perf_counter() - started
    calls = timers.calls
    total_infer = sum(sum(v) for k, v in calls.items()
                      if k in ("yolo_people", "yolo_objects", "pose_full", "pose_crop"))
    mean = statistics.mean(sample_ms)
    report = {
        "label": label,
        "frames": len(frames),
        "person_frames": person_frames,
        "mean_ms": round(mean, 1),
        "p50_ms": round(statistics.median(sample_ms), 1),
        "p95_ms": round(statistics.quantiles(sample_ms, n=20)[-1], 1),
        "aggregate_fps": round(1000 / mean, 2) if mean else 0,
        "per_feed_fps_4": round(1000 / (mean * 4), 2) if mean else 0,
        "inference_share": round(total_infer / elapsed * 100, 1) if elapsed else 0,
        "wall_overhead_ms": round(max(0.0, mean - total_infer / len(frames) * 1000), 1),
        "calls": {k: {"n": len(v), "mean_ms": round(statistics.mean(v) * 1000, 1)}
                  for k, v in sorted(calls.items())},
    }
    if profile:
        text = io.StringIO()
        pstats.Stats(profiler, stream=text).sort_stats("cumulative").print_stats(25)
        report["profile"] = text.getvalue()
    return report


def _print(report: dict) -> None:
    print(f"\n[{report['label']}] {report['frames']} frames, "
          f"{report['person_frames']} with a person")
    print(f"  mean {report['mean_ms']} ms/frame  p50 {report['p50_ms']}  "
          f"p95 {report['p95_ms']}")
    print(f"  aggregate {report['aggregate_fps']} fps  "
          f"-> {report['per_feed_fps_4']} fps/feed with 4 feeds")
    print(f"  inference {report['inference_share']}%  "
          f"wall/tracker/descriptor ~{report['wall_overhead_ms']} ms/frame")
    for key, row in report["calls"].items():
        print(f"    {key:<12} n={row['n']:<3} mean={row['mean_ms']} ms")
    if "profile" in report:
        print(report["profile"])


def _cold(wall, scene) -> dict:
    """Model load plus the first analysed frame, measured from this process."""
    started = time.perf_counter()
    wall.warmup()
    loaded = time.perf_counter() - started
    frame = np.zeros((432, 768, 3), dtype=np.uint8)
    started = time.perf_counter()
    scene.analyze_frame_array(frame)
    first = time.perf_counter() - started
    return {
        "label": "cold start",
        "model_load_and_warmup_s": round(loaded, 2),
        "first_frame_after_warmup_s": round(first, 2),
        "total_first_sample_s": round(loaded + first, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dynamic", "static", "empty", "cold", "all"),
                        default="dynamic")
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    from app import scene, wall  # noqa: PLC0415

    if args.mode == "cold":
        report = _cold(wall, scene)
        print(json.dumps(report, indent=2))
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps([report], indent=2), encoding="utf-8")
        return 0

    output = []
    modes = ("dynamic", "static", "empty") if args.mode == "all" else (args.mode,)
    for mode in modes:
        if mode == "static":
            source, _ = _load_frames("dynamic", 1)
            frames = [source[0]] * max(2, args.frames)
            label = "static repeated frame"
        else:
            frames, label = _load_frames(mode, args.frames)

        # Warm the lazy torch init once, outside every timed run.
        wall.warmup()
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        scene.analyze_frame_array(dummy)

        after = _run(frames, wall, scene, label, args.profile)
        output.append(after)
        _print(after)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
