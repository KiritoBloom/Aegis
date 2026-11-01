"""Prove mask-driven redaction against a live SAM response at this commit.

The gate and agent traces record decisions, not the mask codec. This probe runs
the production path end to end on a multi-person surveillance reel: SAM on the
prepared clip, the same parser and tracker the API uses, then the same renderer
that serves `redacted.mp4`. It writes a small JSON summary (no source footage,
no frames) so the result can be re-run and audited without redistributing the
licenced corpus.

    python scripts/probe_redaction.py
    python scripts/probe_redaction.py --clip data/reels/cctv_entrance.mp4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / ".vendor"), str(ROOT / "backend")]

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
os.environ.setdefault("TMPDIR", str(ROOT / ".tmp"))

from app import clip as clip_util  # noqa: E402
from app import render  # noqa: E402
from app.meta import SAM_MODEL, MetaClient, parse_sam, track_features  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", type=Path,
                        default=ROOT / "data/reels/cctv_entrance.mp4")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "evals/meta/sam_redaction.json")
    args = parser.parse_args()

    if not os.environ.get("META_API_KEY"):
        raise SystemExit("META_API_KEY is not set")
    work = ROOT / ".tmp" / "redaction_probe"
    work.mkdir(parents=True, exist_ok=True)
    small = clip_util.sam_ready(args.clip, work / "sam_input.mp4",
                                max_s=args.seconds, fps=args.fps)
    if small is None:
        raise SystemExit(f"could not prepare {args.clip}")

    result: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True,
                                 cwd=ROOT).stdout.strip(),
        "model": SAM_MODEL,
        "clip": str(args.clip.relative_to(ROOT)),
        "clip_seconds": args.seconds,
        "sampled_fps": args.fps,
        "source": {
            "dataset": "CAVIAR",
            "credit": "CAVIAR test case scenarios, INRIA/University of Edinburgh",
            "license": "CC BY-SA",
            "url": "https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/",
            "note": "Clip is not redistributed; only this summary is committed.",
        },
        "failures": [],
    }
    try:
        client = MetaClient(os.environ["META_API_KEY"])
        text, usage, nbytes = client.sam_video(small, "person")
        objects = parse_sam(text)
        features = track_features(objects)

        dims: Counter = Counter()
        coverage: list[float] = []
        decoded = 0
        headers: list[str] = []
        frame_ids: set[int] = set()
        for obj in objects:
            raster = obj.raster()
            if raster is None:
                continue
            width, height, bits = raster
            ones = sum(bits)
            dims[f"{width}x{height}"] += 1
            coverage.append(round(ones / max(1, width * height), 4))
            decoded += 1
            frame_ids.add(obj.frame)
            if len(headers) < 3 and obj.mask_data:
                headers.append(",".join(obj.mask_data.split(",", 2)[:2]))
        result["sam"] = {
            "objects": len(objects),
            "tracks": len(features.get("track_ids") or []),
            "person_count": features.get("person_count"),
            "frames_present": features.get("frames_present"),
            "latency_ms": usage.latency_ms,
            "response_bytes": nbytes,
        }
        result["masks"] = {
            "total": len(objects),
            "decoded": decoded,
            "failed": len(objects) - decoded,
            "dimensions": dict(dims),
            "wire_headers": headers,
            "coverage_min": min(coverage) if coverage else None,
            "coverage_max": max(coverage) if coverage else None,
            "coverage_mean": (round(sum(coverage) / len(coverage), 4)
                              if coverage else None),
        }

        manifest = render.render(small, work / "redacted.mp4", objects,
                                 timestamp="", trail=False, redact=True,
                                 annotate=False)
        result["render"] = manifest
        still = work / "redacted.jpg"
        result["outputs"] = {
            "redacted_mp4_bytes": (work / "redacted.mp4").stat().st_size
            if (work / "redacted.mp4").exists() else 0,
            "redacted_mp4_sha256": sha256(work / "redacted.mp4")
            if (work / "redacted.mp4").exists() else None,
            "redacted_jpg_sha256": sha256(still) if still.exists() else None,
            "frames_covered": len(frame_ids),
        }
        checks = {
            "multiple_people_tracked": (features.get("person_count") or 0) > 1,
            "every_mask_decoded": len(objects) > 0 and decoded == len(objects),
            "mask_redaction_used": int(manifest.get("masks_decoded") or 0) > 0,
            "redacted_pixels_counted": int(manifest.get("mask_pixels_redacted") or 0) > 0,
            "no_box_fallback": int(manifest.get("box_fallbacks") or 0) == 0,
            "redacted_still_written": still.exists(),
        }
        result["checks"] = checks
        result["failures"] = [name for name, ok in checks.items() if not ok]

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items()
                          if k in ("sam", "masks", "render", "outputs",
                                   "checks", "failures")}, indent=2))
        print(f"wrote {args.out}")
        if result["failures"]:
            raise SystemExit(f"redaction probe failed: {result['failures']}")
    except Exception as exc:  # noqa: BLE001
        result["failures"].append(f"{type(exc).__name__}: {str(exc)[:200]}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
