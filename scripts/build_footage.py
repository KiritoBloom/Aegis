"""Normalise raw surveillance footage into the clips the pipeline actually eats.

Why this exists: the pipeline must be validated on *real fixed-camera footage*,
not cinematic stock. A shallow-depth-of-field shot of an actor leaving a hallway
tests nothing — the motion gate, the zone polygons, the person check and the
track geometry are all calibrated against the flat, wide, often fisheye framing
of a security camera. Every clip in `data/reels/` therefore comes from a real
surveillance corpus, and the licence travels with it in `manifest.json`.

Sources (never redistributed: `data/` is gitignored, and the manifest states the
licence so nothing is republished by accident):

* **CAVIAR** — INRIA/Edinburgh, real wide-angle lobby and corridor cameras,
  384x288 MPEG-2 with burned-in timestamps, CC BY-SA.
  https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/
* **URFD** — University of Rzeszow fall detection, Kinect fixed camera,
  RGB stored side-by-side with depth in a 640x240 frame, CC BY-NC-SA 4.0.
  https://fenix.ur.edu.pl/mkepski/ds/uf.html

Usage:
    python scripts/build_footage.py            # build everything missing
    python scripts/build_footage.py --force    # rebuild all
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOOTAGE = ROOT / "data" / "footage"
REELS = ROOT / "data" / "reels"

# Normalised target: matches what the edge agent emits, so the backend sees the
# same geometry in a replay as it would from a real camera.
W, H, FPS = 640, 480, 15

CREDITS = {
    "caviar": {
        "dataset": "CAVIAR",
        "credit": "CAVIAR test case scenarios, INRIA/University of Edinburgh",
        "license": "CC BY-SA",
        "url": "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/",
        "note": "Real wide-angle surveillance camera, burned-in timestamp.",
    },
    "urfd": {
        "dataset": "UR Fall Detection Dataset (URFD)",
        "credit": "Kwolek & Kepski 2014, University of Rzeszow",
        "license": "CC BY-NC-SA 4.0",
        "url": "https://fenix.ur.edu.pl/mkepski/ds/uf.html",
        "note": "Fixed Kinect camera; RGB half of a side-by-side RGB|depth frame.",
    },
}

# (output name, source path, crop filter or None, start seconds, duration seconds)
JOBS = [
    # Corridor: a resident walking away down a hallway, real CCTV perspective.
    ("cctv_corridor.mp4", "caviar/OneStopEnter1cor.mpg", None, 0.0, 12.0),
    ("cctv_corridor_wide.mp4", "caviar/Walk1.mpg", None, 2.0, 12.0),
    # Entrance: crossing paths at a doorway, for the door-exit scenario.
    ("cctv_entrance.mp4", "caviar/EnterExitCrossingPaths1cor.mpg", None, 0.0, 7.0),
    ("cctv_shopfront.mp4", "caviar/OneLeaveShop1cor.mpg", None, 0.0, 5.5),
    # Real fall in a lobby, captured by a fixed surveillance camera.
    ("cctv_fall.mp4", "caviar/Rest_FallOnFloor.mpg", None, 0.0, 10.0),
    # URFD RGB half only: 640x240 side-by-side -> right 320x240.
    ("urfd_fall-01.mp4", "urfd/fall-01.mp4", "crop=320:240:320:0", 0.0, 5.3),
    ("urfd_fall-02.mp4", "urfd/fall-02.mp4", "crop=320:240:320:0", 0.0, 3.6),
    ("urfd_fall-03.mp4", "urfd/fall-03.mp4", "crop=320:240:320:0", 0.0, 7.2),
    ("urfd_fall-04.mp4", "urfd/fall-04.mp4", "crop=320:240:320:0", 0.0, 4.0),
]


def _source(job: tuple) -> dict:
    return CREDITS["urfd" if job[1].startswith("urfd/") else "caviar"]


def build(job: tuple, force: bool = False) -> dict:
    name, rel, crop, start, dur = job
    src = FOOTAGE / rel
    dst = REELS / name
    if not src.exists():
        return {"clip": name, "ok": False, "reason": f"missing source {rel}"}
    if dst.exists() and not force and dst.stat().st_size > 4096:
        return {"clip": name, "ok": True, "cached": True,
                "bytes": dst.stat().st_size, "source": rel, **_source(job)}

    vf = [f"fps={FPS}", f"scale={W}:{H}:force_original_aspect_ratio=decrease",
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if crop:
        vf.insert(0, crop)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-i", str(src),
           "-t", str(dur), "-vf", ",".join(vf), "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", b"")
        return {"clip": name, "ok": False,
                "reason": f"{type(exc).__name__}: {detail[:200]!r}"}

    # A normalised clip must actually decode; a broken container would make the
    # whole replay path look like a model failure.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", str(dst)],
        capture_output=True, text=True, timeout=120)
    try:
        stream = json.loads(probe.stdout)["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError):
        return {"clip": name, "ok": False, "reason": "unreadable output"}
    frames = int(stream.get("nb_read_frames") or 0)
    if frames < 10:
        return {"clip": name, "ok": False, "reason": f"only {frames} frames"}
    return {"clip": name, "ok": True, "bytes": dst.stat().st_size, "frames": frames,
            "size": f"{stream.get('width')}x{stream.get('height')}",
            "seconds": round(frames / FPS, 2), "source": rel, **_source(job)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    REELS.mkdir(parents=True, exist_ok=True)

    results = [build(job, args.force) for job in JOBS]
    manifest = {
        "generated_by": "scripts/build_footage.py",
        "target": {"width": W, "height": H, "fps": FPS},
        "rule": ("Real fixed-camera surveillance only. Cinematic stock is not "
                 "used to validate the pipeline; it does not match the framing "
                 "the rules were calibrated on."),
        "clips": results,
    }
    (REELS / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    ok = 0
    for r in results:
        if r["ok"]:
            ok += 1
            tag = "cached" if r.get("cached") else f"{r['frames']}f"
            print(f"  ok   {r['clip']:<24} {tag:<10} {r.get('size', ''):<10} "
                  f"{r.get('license', '')}")
        else:
            print(f"  FAIL {r['clip']:<24} {r['reason']}")
    print(f"\n{ok}/{len(results)} clips ready in data/reels/")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
