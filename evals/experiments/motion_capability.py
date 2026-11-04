"""Blind hosted-model scout: can Muse read actions from tiny SAM silhouettes?

Raw footage goes to hosted SAM. Only the binary condition removes source-image
appearance before Muse; this is not end-to-end anonymous or on-device analysis.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts')]
from app.meta import MetaClient, output_text, parse_sam
from evidence_stamp import stamp
from serve import load_env

OUT = ROOT / '.tmp' / 'motion-capability'
CASES = [
    ('gmd-sub3-fall-05', 'fall'),
    ('gmdcsa24-fall-10', 'fall'),
    ('gmd-sub2-fall-03', 'fall'),
    ('gmdcsa24-adl-01', 'controlled_lowering'),
    ('gmdcsa24-adl-08', 'walking'),
    ('gmd-sub3-adl-05', 'exercise'),
    ('gmd-sub3-adl-08', 'controlled_lowering'),
    ('gmd-sub3-adl-09', 'getting_up'),
]
SCHEMA = {'type': 'object', 'properties': {
    'action': {'type': 'string', 'enum': ['fall', 'controlled_lowering', 'getting_up', 'walking', 'exercise', 'uncertain']},
    'confidence': {'type': 'number'}, 'observation': {'type': 'string'},
    'evidence_frames': {'type': 'array', 'items': {'type': 'integer'}},
}, 'required': ['action', 'confidence', 'observation', 'evidence_frames'], 'additionalProperties': False}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def masks_for(frames, objects):
    h, w = frames[0].shape[:2]
    masks = [np.zeros((h, w), np.uint8) for _ in frames]
    decoded = 0
    for obj in objects:
        if not 0 <= obj.frame < len(frames):
            continue
        raster = obj.raster()
        if not raster:
            continue
        mw, mh, bits = raster
        crop = np.frombuffer(bits, np.uint8).reshape(mh, mw)
        x1, y1, x2, y2 = (round(v) for v in (obj.x1, obj.y1, obj.x2, obj.y2))
        if x2 <= x1 or y2 <= y1:
            continue
        canvas = np.zeros((int(obj.img_h), int(obj.img_w)), np.uint8)
        left, top, right, bottom = max(0, x1), max(0, y1), min(x2, canvas.shape[1]), min(y2, canvas.shape[0])
        resized = cv2.resize(crop, (x2-x1, y2-y1), interpolation=cv2.INTER_NEAREST)
        canvas[top:bottom, left:right] = resized[top-y1:bottom-y1, left-x1:right-x1]
        masks[obj.frame] |= cv2.resize(canvas, (w, h), interpolation=cv2.INTER_NEAREST)
        decoded += 1
    return masks, decoded


def sheet(frames, indexes, fps):
    tiles = []
    for order, index in enumerate(indexes):
        frame = frames[index]
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        tile = np.zeros((218, 320, 3), np.uint8)
        tile[:180] = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_NEAREST)
        cv2.putText(tile, f'{order+1:02d} | {index/fps:.2f}s', (8, 205), cv2.FONT_HERSHEY_SIMPLEX, .6, (220,220,220), 1)
        tiles.append(tile)
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    return np.vstack([np.hstack(tiles[i:i+4]) for i in range(0, len(tiles), 4)])


def classify(path, kind, frame_count):
    prompt = (
        f'This contact sheet shows {frame_count} time-ordered observations of ONE person, read left-to-right then top-to-bottom. '
        'Numbers and timestamps identify observations. Classify the observed movement, not appearance or presumed context. '
        'fall = loss-of-balance/collapse descent; controlled_lowering = apparently controlled sitting/kneeling/lying down; '
        'getting_up = rising from low posture; exercise = bending/stretching with maintained control; walking = walking. '
        'Use uncertain when temporal/body evidence cannot distinguish these. A low final posture alone does not prove a fall. '
        'Do not infer intent, identity, medical condition or hidden furniture. Describe only the observed movement and cite frames. '
    )
    if kind != 'raw':
        prompt += 'These are binary body-occupancy masks: white marks observed person, black is all other space. No source RGB pixels, room appearance or computed action labels are present. '
    payload = {'model': 'muse-spark-1.3', 'reasoning': {'effort': 'low'}, 'max_output_tokens': 2500,
        'input': [{'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': prompt},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()}]}],
        'text': {'format': {'type': 'json_schema', 'name': 'observed_motion', 'schema': SCHEMA, 'strict': True}}}
    key = sha(json.dumps(payload, sort_keys=True).encode())
    cache = path.with_suffix('.response.json')
    if cache.exists():
        old = json.loads(cache.read_text())
        if old.get('request_sha256') == key:
            return old
    client = MetaClient(os.environ['META_API_KEY'], timeout=120)
    result = {'request_sha256': key, 'prompt': prompt, 'image_sha256': sha(path.read_bytes()), 'image': path.name}
    start = time.monotonic()
    try:
        result['response'] = client._post('/responses', payload, retries=0)
        result['parsed'] = json.loads(output_text(result['response']))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        client.close()
    result['latency_s'] = round(time.monotonic()-start, 3)
    save(cache, result)
    return result


def run_case(index, name, label):
    source = ROOT / 'data' / 'footage' / 'external' / (name + '.mp4')
    prepared = OUT / f'case-{index}.mp4'
    if not prepared.exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-t', '12', '-vf', 'fps=6,scale=768:-2',
            '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(prepared)], check=True, timeout=90)
    cache = OUT / f'case-{index}-sam.json'
    video_hash = sha(prepared.read_bytes())
    sam = json.loads(cache.read_text()) if cache.exists() else {}
    if sam.get('input_sha256') != video_hash:
        client = MetaClient(os.environ['META_API_KEY'], timeout=180)
        start = time.monotonic()
        try:
            text, usage, nbytes = client.sam_video(prepared, 'person')
            sam = {'input_sha256': video_hash, 'raw_text': text, 'uploaded_bytes': nbytes, 'latency_s': round(time.monotonic()-start,3)}
        finally:
            client.close()
        save(cache, sam)
    cap = cv2.VideoCapture(str(prepared))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    masks, decoded = masks_for(frames, parse_sam(sam['raw_text']))
    indexes = list(dict.fromkeys(np.linspace(0, len(frames)-1, min(24,len(frames)), dtype=int).tolist()))
    variants = {'raw': frames}
    for width in (64, 32):
        variants[f'binary{width}'] = [(cv2.resize(mask.astype(float), (width, round(width*mask.shape[0]/mask.shape[1])), interpolation=cv2.INTER_AREA) >= .25).astype(np.uint8)*255 for mask in masks]
    paths = {}
    for kind, values in variants.items():
        path = OUT / f'case-{index}-{kind}.png'
        cv2.imwrite(str(path), sheet(values, indexes, 6))
        paths[kind] = path
    results = {}
    for kind, path in paths.items():
        results[kind] = classify(path, kind, len(indexes))
        print(name, kind, results[kind].get('parsed', results[kind].get('error')), flush=True)
    row = {'case_id': index, 'source': name, 'source_sha256': sha(source.read_bytes()), 'reference_action': label,
        'reference_is_acted_dataset_label': True, 'sam': sam, 'frames': len(frames), 'decoded_masks': decoded,
        'frames_with_masks': sum(bool(np.any(m)) for m in masks), 'sampled_indices': indexes,
        'conditions': results}
    save(OUT / f'case-{index}.json', row)
    return row


def main():
    load_env(ROOT / '.env')
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'provenance': stamp(), 'script_sha256': sha(Path(__file__).read_bytes()),
        'scope': 'Eight preselected acted corpus clips; raw vs binary masks. One blind Muse call per condition; no policy validation or model training. SAM receives original video; Muse binary conditions receive only masks/timestamps. No biometric anonymity guarantee.',
        'cases': []}
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(run_case, i, name, label): name for i,(name,label) in enumerate(CASES)}
        for task in as_completed(pending):
            try:
                report['cases'].append(task.result())
            except Exception as exc:
                report['cases'].append({'source': pending[task], 'error': f'{type(exc).__name__}: {exc}'})
            save(OUT / 'result.json', report)
    report['summary'] = {}
    for kind in ('raw','binary64','binary32'):
        valid = [c for c in report['cases'] if 'conditions' in c]
        report['summary'][kind] = {
            'completed_cases': len(valid),
            'exact_action_correct': sum(c['conditions'][kind].get('parsed',{}).get('action') == c['reference_action'] for c in valid),
            'total': len(CASES),
            'falls_found': sum(c['conditions'][kind].get('parsed',{}).get('action') == 'fall' for c in valid if c['reference_action']=='fall'),
            'false_falls': sum(c['conditions'][kind].get('parsed',{}).get('action') == 'fall' for c in valid if c['reference_action']!='fall'),
            'uncertain': sum(c['conditions'][kind].get('parsed',{}).get('action') == 'uncertain' for c in valid),
        }
    save(OUT / 'result.json', report)
    save(ROOT / 'evals' / 'motion_capability.json', report)
    print(json.dumps(report['summary'], indent=2), flush=True)
    parts = ['<!doctype html><meta charset="utf-8"><title>Blind motion reading</title><style>body{font:16px system-ui;margin:32px}section{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}img{width:100%}pre{white-space:pre-wrap}</style><h1>Can Muse read motion without source-image appearance?</h1><p>Acted recordings. Labels withheld from all inference. Hosted SAM still receives raw video.</p>']
    for c in sorted(report['cases'],key=lambda c:c.get('case_id',999)):
        if 'conditions' not in c:
            continue
        parts.append('<h2>'+html.escape(c['source']+' — reference: '+c['reference_action'])+'</h2><section>')
        for kind, result in c['conditions'].items():
            parts.append(f'<article><h3>{kind}</h3><img src="{result["image"]}"><pre>'+html.escape(json.dumps(result.get('parsed',result.get('error')),indent=2))+'</pre></article>')
        parts.append('</section>')
    (OUT/'index.html').write_text('\n'.join(parts),encoding='utf-8')


if __name__ == '__main__':
    main()
