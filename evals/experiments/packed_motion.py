"""Test action understanding from SAM occupancy bitmaps through text-only Muse.

Requires the cached, independently labelled eight-case motion_capability run.
No images, video, action labels, tool results, or earlier verdicts go to Muse.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motion_capability import ROOT, OUT, SCHEMA, MetaClient, load_env, masks_for, parse_sam, stamp

PROMPT = """Classify one observed person's movement from a sequence of binary occupancy bitmaps.
This input contains no image attachment. Each bitmap is 32 columns by 18 rows.
Rows are top-to-bottom; each 8-character hexadecimal row encodes 32 bits, most
significant bit on the left. A 1 means observed person, 0 means everything else.
The camera is fixed; all frames share the same coordinates. Read the time order.
Infer the movement from changing body occupancy, not from any presumed room,
person appearance, intent, medical condition, or hidden furniture.
fall = loss-of-balance/collapse descent; controlled_lowering = apparently
controlled sitting/kneeling/lying down; getting_up = rising from low posture;
exercise = bending/stretching with maintained control; walking = walking.
Use uncertain if occupancy evidence cannot distinguish these. A low final
posture alone does not prove a fall. Describe observed changes and cite frame
numbers. No code execution or external tool is available or needed."""


def run(case):
    cap = cv2.VideoCapture(str(OUT / f'case-{case["case_id"]}.mp4'))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    masks, _ = masks_for(frames, parse_sam(case['sam']['raw_text']))
    observations = []
    for order, index in enumerate(case['sampled_indices']):
        bitmap = (cv2.resize(masks[index].astype(float), (32,18), interpolation=cv2.INTER_AREA) >= .25).astype(np.uint8)
        packed = np.packbits(bitmap, axis=1, bitorder='big')
        # Verify encoding is lossless before requesting model interpretation.
        assert np.array_equal(np.unpackbits(packed, axis=1, bitorder='big'), bitmap)
        observations.append({'frame': order+1, 'seconds': round(index/6,3), 'rows_hex': [row.tobytes().hex() for row in packed]})
    packet = {'width':32, 'height':18, 'observations': observations}
    fingerprint = hashlib.sha256(json.dumps({'packet':packet,'prompt':PROMPT,'schema':SCHEMA},sort_keys=True).encode()).hexdigest()
    cache = OUT / f'case-{case["case_id"]}-packed.json'
    if cache.exists():
        old = json.loads(cache.read_text())
        if old.get('request_sha256') == fingerprint:
            return old
    result = {'case_id':case['case_id'], 'source':case['source'], 'reference_action':case['reference_action'],
        'request_sha256':fingerprint, 'packet':packet, 'system_prompt':PROMPT,
        'binary_occupancy_bytes':len(observations)*72, 'json_input_bytes':len(json.dumps(packet,separators=(',',':')).encode()),
        'image_attachments':0, 'video_attachments':0, 'tools':0}
    client = MetaClient(os.environ['META_API_KEY'], timeout=120)
    start = time.monotonic()
    try:
        result['parsed'], usage = client.decide(packet, PROMPT, SCHEMA, 'low', max_tokens=5000)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        client.close()
    result['latency_s'] = round(time.monotonic()-start,3)
    cache.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(case['source'], result.get('parsed',result.get('error')), flush=True)
    return result


def main():
    load_env(ROOT/'.env')
    source = json.loads((ROOT/'evals/motion_capability.json').read_text())
    report = {'provenance':stamp(), 'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'One text-only Muse call for each of eight preselected acted recordings. Source labels and prior verdicts excluded. Raw footage already processed by hosted SAM; this does not establish anonymity or clinical reliability.',
        'cases':[]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run,case) for case in source['cases'] if 'conditions' in case]
        for future in as_completed(futures):
            report['cases'].append(future.result())
    cases = report['cases']
    report['summary'] = {
        'total':len(cases), 'completed':sum('parsed' in c for c in cases),
        'exact_action_correct':sum(c.get('parsed',{}).get('action')==c['reference_action'] for c in cases),
        'falls_found':sum(c.get('parsed',{}).get('action')=='fall' for c in cases if c['reference_action']=='fall'),
        'false_falls':sum(c.get('parsed',{}).get('action')=='fall' for c in cases if c['reference_action']!='fall'),
        'uncertain':sum(c.get('parsed',{}).get('action')=='uncertain' for c in cases),
    }
    (ROOT/'evals/packed_motion.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],indent=2),flush=True)


if __name__ == '__main__':
    main()
