"""Two input-only sanity checks for packed motion; no new SAM requests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from packed_motion import ROOT, OUT, SCHEMA, PROMPT, MetaClient, load_env, masks_for, parse_sam, stamp


def main():
    load_env(ROOT/'.env')
    source = json.loads((ROOT/'evals/motion_capability.json').read_text())
    case = next(c for c in source['cases'] if c['case_id']==0)
    cap = cv2.VideoCapture(str(OUT/'case-0.mp4'))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    masks, _ = masks_for(frames, parse_sam(case['sam']['raw_text']))
    bitmaps = []
    for index in case['sampled_indices']:
        bitmap = (cv2.resize(masks[index].astype(float),(32,18),interpolation=cv2.INTER_AREA)>=.25).astype(np.uint8)
        bitmaps.append([row.tobytes().hex() for row in np.packbits(bitmap,axis=1,bitorder='big')])
    conditions = {'reversed':list(reversed(bitmaps)), 'frozen_final':[bitmaps[-1] for _ in bitmaps]}

    def check(item):
        name, values = item
        # New uniformly increasing timestamps; no manipulation label is sent.
        packet = {'width':32,'height':18,'observations':[{'frame':i+1,'seconds':round(i*(len(frames)-1)/6/(len(values)-1),3),'rows_hex':v} for i,v in enumerate(values)]}
        key=hashlib.sha256(json.dumps({'packet':packet,'prompt':PROMPT,'schema':SCHEMA},sort_keys=True).encode()).hexdigest()
        cache=OUT/f'control-{name}.json'
        if cache.exists():
            old=json.loads(cache.read_text())
            if old.get('request_sha256')==key:return old
        result={'condition':name,'request_sha256':key,'packet':packet,'prompt':PROMPT}
        client=MetaClient(os.environ['META_API_KEY'],timeout=120)
        try:
            result['parsed'], _=client.decide(packet,PROMPT,SCHEMA,'low',max_tokens=5000)
        except Exception as exc:
            result['error']=f'{type(exc).__name__}: {exc}'
        finally:
            client.close()
        cache.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(name,result.get('parsed',result.get('error')),flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(check,conditions.items()))
    report={'provenance':stamp(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source':'gmd-sub3-fall-05','scope':'Input-order and no-motion sanity checks; not proof of internal reasoning or accuracy. Manipulation labels withheld.',
        'expected_before_inference':{'reversed':'getting_up','frozen_final':'uncertain'},'conditions':results}
    (ROOT/'evals/motion_order_control.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
