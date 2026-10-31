"""Run the wall's local fall candidate over external surveillance clips.

This measures the local gate that decides whether to start verification. It does
not call SAM or Muse. Frames are sampled at a chosen cadence and fed to the same
stateful FeedState the console wall uses, so tracks, support surfaces and the
cooldown behave as they do in the app.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in (".vendor", "backend", "edge"):
    entry = str(ROOT / sub)
    if entry not in sys.path:
        sys.path.insert(0, entry)

import cv2  # noqa: E402

from app import wall  # noqa: E402


def run_clip(path: Path, fps: float, width: int = 768) -> dict:
    state = wall.FeedState({"id": path.stem, "name": path.stem, "kind": "file"})
    cap = cv2.VideoCapture(str(path))
    native = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(native / fps)))
    index = 0
    triggers = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % step == 0:
            if frame.shape[1] != width:
                scale = width / frame.shape[1]
                frame = cv2.resize(frame, (width, max(2, int(frame.shape[0] * scale))),
                                   interpolation=cv2.INTER_AREA)
            t = 1000.0 + index / native
            result = state.process(frame, t)
            if result.get("trigger"):
                trigger = result["trigger"]
                evidence = trigger.get("evidence") or {}
                triggers.append({"at_native_s": round(index / native, 2),
                                 "kind": trigger.get("kind"),
                                 "person_count": result.get("person_count"),
                                 "posture": evidence.get("posture"),
                                 "support_surface": evidence.get("support_surface"),
                                 "geometry_fall": evidence.get("geometry_fall"),
                                 "box_aspect": evidence.get("box_aspect"),
                                 "track_id": evidence.get("track_id"),
                                 "box": evidence.get("box"),
                                 "distance": evidence.get("distance"),
                                 "lying_for_s": evidence.get("lying_for_s")})
        index += 1
    cap.release()
    return {"clip": path.name, "frames": index, "triggers": triggers}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path, nargs="?", default=ROOT / "data/footage/external")
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    clips = sorted(args.folder.glob("*.mp4"))
    if not clips:
        raise SystemExit(f"no clips in {args.folder}")
    results = [run_clip(clip, args.fps) for clip in clips]
    for item in results:
        label = "FALL" if "fall" in item["clip"].lower() else "ADL"
        fired = "candidate" if item["triggers"] else "quiet"
        print(f"{label:4} {item['clip']:32} {fired:9} triggers={len(item['triggers'])}")
    falls = [r for r in results if "fall" in r["clip"].lower()]
    adls = [r for r in results if "fall" not in r["clip"].lower()]
    summary = {
        "fps": args.fps,
        "clips": len(results),
        "falls": len(falls),
        "falls_with_candidate": sum(1 for r in falls if r["triggers"]),
        "adls": len(adls),
        "adls_with_candidate": sum(1 for r in adls if r["triggers"]),
        "results": results,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
