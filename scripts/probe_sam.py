"""Dump one raw SAM response so the mask encoding can be inspected, not guessed."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / ".vendor"), str(ROOT / "backend")]
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ.setdefault("TMPDIR", str(ROOT / ".tmp"))

from app import clip as clip_util
from app.meta import MetaClient, parse_sam

src = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/reels/fall-01.mp4")
dst = ROOT / ".tmp/sam_probe.mp4"
small = clip_util.sam_ready(src, dst, max_s=8.0, fps=8)
print("upload:", small, small.stat().st_size if small else None)

c = MetaClient(os.environ["META_API_KEY"])
text, usage, nbytes = c.sam_video(small, "person")
(ROOT / ".tmp/sam_raw.txt").write_text(text, encoding="utf-8")
print("bytes:", nbytes, "chars:", len(text), "latency:", usage.latency_ms)

objs = parse_sam(text)
print("objects:", len(objs))
if objs:
    print("fields:", objs[0])
print("--- first 1200 chars ---")
print(text[:1200])
