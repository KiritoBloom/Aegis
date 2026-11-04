"""Replay URFD originals/reels and the external corpus through the local tracker.

No cloud calls. Exact local detections are cached; tracking is rerun every time.
Optional --baseline points to a saved pre-fix wall.py for paired comparisons.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from app import scene, wall
from evidence_stamp import stamp


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    provenance = stamp()
    manifest = ROOT / "evals/corpus_manifest.json"
    external = json.loads(manifest.read_text(encoding="utf-8"))
    paths = (sorted((ROOT / "data/footage/urfd").glob("*.mp4"))
             + sorted((ROOT / "data/reels").glob("urfd*.mp4"))
             + sorted(ROOT / "data/footage/external" / row["clip"] for row in external))
    report = {"provenance": provenance, "script_sha256": sha(Path(__file__)), "sample_fps": 4,
              "scope": "Local candidates, not verified falls or family notifications. Reels duplicate originals.",
              "corpus_manifest_sha256": sha(manifest), "expected_cases": len(paths),
              "complete": False, "cases": []}
    modules = {"fixed": wall}
    if args.baseline:
        spec = importlib.util.spec_from_file_location("app.wall_baseline", args.baseline)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        modules = {"baseline": baseline, **modules}
        report["baseline_sha256"] = sha(args.baseline)
    fingerprint = hashlib.sha256(json.dumps({
        "scene": sha(ROOT / "backend/app/scene.py"),
        "weights": {name: sha(ROOT / name) for name in ("yolov8n.pt", "yolov8n-pose.pt")},
        "packages": provenance["packages"],
    }, sort_keys=True).encode()).hexdigest()
    cache_root = ROOT / ".tmp/urfd-regression" / fingerprint
    cache_root.mkdir(parents=True, exist_ok=True)
    actual = scene.analyze_frame_array
    memo = {}

    def analyze(frame, **kwargs):
        key = hashlib.sha256(frame.tobytes() + json.dumps(kwargs, sort_keys=True).encode()).hexdigest()
        if key not in memo:
            memo[key] = actual(frame, **kwargs)
        return copy.deepcopy(memo[key])

    scene.analyze_frame_array = analyze
    try:
        for path in paths:
            cache = cache_root / f"{path.parent.name}-{path.stem}-{sha(path)[:12]}.json"
            memo = json.loads(cache.read_text()) if cache.exists() else {}
            cap = cv2.VideoCapture(str(path))
            fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps <= 0 or count < 2:
                raise ValueError(f"Unreadable recording: {path}")
            duration = count / fps
            states = {name: module.FeedState({"id": name}) for name, module in modules.items()}
            item = {"path": path.relative_to(ROOT).as_posix(), "source_sha256": sha(path),
                    "duration": duration, "samples": 0, **{name: [] for name in states}}
            for t in np.arange(0, duration, .25):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
                ok, frame = cap.read()
                if not ok:
                    break
                if frame.shape[1] > 768:
                    frame = cv2.resize(frame, (768, round(frame.shape[0] * 768 / frame.shape[1])))
                frame = cv2.imdecode(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])[1], 1)
                item["samples"] += 1
                for name, state in states.items():
                    result = state.process(frame, 1000 + float(t))
                    if result.get("trigger"):
                        item[name].append(round(float(t), 2))
            cap.release()
            cache.write_text(json.dumps(memo), encoding="utf-8")
            report["cases"].append(item)
            print(item["path"], {name: item[name] for name in states}, flush=True)
            (ROOT / "evals/urfd_regression.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["complete"] = True
        (ROOT / "evals/urfd_regression.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        scene.analyze_frame_array = actual


if __name__ == "__main__":
    main()
