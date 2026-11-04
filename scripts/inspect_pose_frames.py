"""Save real model overlays from the reported recordings, without cloud calls."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".vendor"), str(ROOT / "backend")]
import cv2
from app import scene

scene.warmup()
out = ROOT / ".tmp" / "pose-inspection"
out.mkdir(parents=True, exist_ok=True)
report = []
for name in ("gmdcsa24-adl-04", "gmd-sub3-fall-05"):
    cap = cv2.VideoCapture(str(ROOT / "data/footage/external" / f"{name}.mp4"))
    count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    if not count or not fps:
        raise RuntimeError(f"Cannot read {name}")
    for index in [0, count // 2, count - 1]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = cap.read()
        if not ok:
            raise RuntimeError(f"Cannot read {name} frame {index}")
        h, w = image.shape[:2]
        image = cv2.resize(image, (768, round(h * 768 / w)))
        result = scene.analyze_frame_array(image)
        annotated = image.copy()
        h, w = image.shape[:2]
        for p in result["persons"]:
            a, b, c, d = p["box"]
            cv2.rectangle(annotated, (round(a*w), round(b*h)), (round(c*w), round(d*h)), (150, 220, 160), 1)
            cv2.putText(annotated, p["posture"], (round(a*w), max(14, round(b*h)-5)), cv2.FONT_HERSHEY_SIMPLEX, .5, (150, 220, 160), 1)
            for a, b in [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]:
                points = p.get("skeleton", [])
                if len(points) > max(a,b) and min(points[a][2], points[b][2]) >= .5:
                    cv2.line(annotated, (round(points[a][0]*w), round(points[a][1]*h)), (round(points[b][0]*w), round(points[b][1]*h)), (150,220,160), 2)
        cv2.imwrite(str(out / f"{name}-{index}.jpg"), annotated)
        report.append({"clip": name, "frame": index, "at": round(index/fps, 2), "people": result["persons"], "timing": result["timing"]})
    cap.release()
(out / "observations.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps([{**row, "people": [{k:v for k,v in p.items() if k != "skeleton"} for p in row["people"]]} for row in report], indent=2))
