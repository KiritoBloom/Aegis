"""Does the drawn geometry actually sit on the person in real CCTV?

A zone polygon that looks right in a still frame can miss every foot point once
the tracker runs, and the failure is silent: no trigger, no error, and the demo
looks like a system that sees nothing. This walks a clip and reports where the
foot points actually land relative to each zone.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "edge"))

import cv2  # noqa: E402

from aegis_edge import config as edge_config  # noqa: E402
from aegis_edge import geometry, vision  # noqa: E402

clip = sys.argv[1] if len(sys.argv) > 1 else "data/reels/cctv_corridor.mp4"
cfg = edge_config.load(str(ROOT / "config" / "edge_cctv.yaml"))
det = vision.PersonDetector(cfg)

cap = cv2.VideoCapture(str(ROOT / clip))
fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
print(f"clip {clip}  fps {fps:.0f}  detector {det.mode}")
print(f"zones: {[(z.name, z.type) for z in cfg.zones]}")

idx = 0
hits: dict[str, int] = {z.name: 0 for z in cfg.zones}
feet_seen = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    idx += 1
    if idx % 5:
        continue
    for d in det.track(frame, cfg.person.conf_day):
        feet_seen += 1
        fx, fy = d.foot
        inside = [z.name for z in cfg.zones
                  if geometry.point_in_polygon((fx, fy), z.points)]
        for name in inside:
            hits[name] += 1
        if idx % 40 == 0:
            print(f"  frame {idx:4d} foot=({fx:.2f},{fy:.2f}) conf={d.conf:.2f} zones={inside}")
cap.release()

print(f"\nfeet sampled {feet_seen}")
for name, n in hits.items():
    print(f"  {name:<18} {n}")
