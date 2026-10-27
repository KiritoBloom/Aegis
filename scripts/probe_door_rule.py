"""What does the door rule actually receive?

A rule that never fires is hard to debug from the outside: the pipeline looks
healthy, frames flow, people are detected, and nothing is emitted. This prints
the exact features the rule reads, frame by frame, so the miss is visible.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "edge"))

import cv2  # noqa: E402

from aegis_edge import config as edge_config  # noqa: E402
from aegis_edge import geometry, vision  # noqa: E402

cfg = edge_config.load(str(ROOT / "config" / "edge_cctv_demo.yaml"))
zone = cfg.zone(cfg.door.zone)
print(f"door.zone = {cfg.door.zone!r} -> {zone.name if zone else None} "
      f"type={zone.type if zone else '-'} dwell_s={cfg.door.dwell_s}")
if zone is None:
    sys.exit("the configured door zone does not exist; the rule can never fire")

det = vision.PersonDetector(cfg)
gate = vision.MotionGate(cfg)
cap = cv2.VideoCapture(str(ROOT / "data/reels/cctv_corridor.mp4"))
fps = cap.get(cv2.CAP_PROP_FPS) or 15.0

idx = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    idx += 1
    moving, ratio = gate.update(frame)
    dets = det.track(frame, cfg.person.conf_day) if moving else []
    for d in dets:
        # TrackHistory needs samples over time; use a one-frame view to show
        # whether the foot point is even inside the polygon at all.
        inside = geometry.point_in_polygon(d.foot, zone.points)
        if idx % 15 == 0:
            print(f"frame {idx:4d} moving={moving} ratio={ratio:.4f} "
                  f"det={d.track_id} foot=({d.foot[0]:.2f},{d.foot[1]:.2f}) inside={inside}")
cap.release()

print("\nzone polygon:", zone.points)
