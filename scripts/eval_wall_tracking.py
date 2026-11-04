"""Replay local footage through the real wall tracker, without cloud calls.

Reports sampling coverage, track changes and candidate times; these are not
clinical accuracy estimates. Use --clips to compare the same recorded cases.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend")]
import cv2
from app.wall import FeedState, warmup


def evaluate(path: Path, sample_fps: float) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS)
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / native_fps
    state = FeedState({"id": path.stem, "name": path.stem, "kind": "file"})
    times, ids, candidates, observations = [], [], [], []
    try:
        for sample in range(int(duration * sample_fps)):
            at = sample / sample_fps
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
            ok, image = cap.read()
            if not ok: break
            h, w = image.shape[:2]
            if w > 768:
                image = cv2.resize(image, (768, round(h * 768 / w)))
            started = time.perf_counter()
            result = state.process(image, 1000 + at)
            times.append((time.perf_counter() - started) * 1000)
            primary = next((p for p in result["persons"] if p["primary"]), None)
            if primary: ids.append(primary["track_id"])
            observations.append({"video_s": round(at, 2), "people": result["person_count"],
                "track_id": primary["track_id"] if primary else None,
                "posture": primary["posture"] if primary else None})
            if result.get("trigger"):
                candidates.append({"video_s": round(at, 2), "kind": result["trigger"]["kind"]})
    finally:
        cap.release()
    ordered = sorted(times)
    return {"clip": path.name, "duration_s": round(duration, 2), "samples": len(times),
            "person_coverage_pct": round(len(ids) / max(1, len(times)) * 100, 1),
            "track_changes": sum(a != b for a, b in zip(ids, ids[1:])),
            "unique_tracks": len(set(ids)), "mean_ms": round(statistics.mean(times), 1),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 1),
            "candidates": candidates, "observations": observations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", nargs="+", default=["gmd-sub3-fall-05.mp4", "gmd-sub2-adl-01.mp4", "gmd-sub2-adl-05.mp4"])
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--json", type=Path, default=ROOT / ".tmp" / "tracking-evaluation.json")
    args = parser.parse_args()
    if args.fps <= 0: parser.error("--fps must be positive")
    warmup()
    results = [evaluate(Path(clip) if Path(clip).is_absolute() else ROOT / "data/footage/external" / clip, args.fps) for clip in args.clips]
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps([{k: v for k, v in row.items() if k != "observations"} for row in results], indent=2))
