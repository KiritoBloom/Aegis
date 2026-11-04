# URFD local-candidate regression

Paired replay on 2026-11-04: the saved pre-fix tracker and current tracker receive
the same local scene detections at 4 fps. Exact detections are cached using source,
weight and package fingerprints; each replay reruns tracking. Frames are scaled
to at most 768 pixels wide and JPEG-round-tripped at quality 82.

## Results

| Recordings | Count | Before: clips with candidates | After: clips with candidates |
|---|---:|---:|---:|
| Original URFD falls | 6 | 0 | 6 |
| URFD demo reels | 4 | 3 | 4 |
| External floor falls | 57 | 51 | 53 |
| External bed falls | 5 | 0 | 0 |
| External activities of daily living (ADL) | 48 | 3 | 5 |

These are **local candidates**, not verified falls or family notifications.
Reels duplicate original footage. External labels come from
[`corpus_manifest.json`](corpus_manifest.json); the unrelated, unlabelled external
`reel.mp4` is outside the manifest and excluded. The intended replay contains
120 files: six originals, four reels and all 110 labelled external clips.

All original URFD clips now trigger: `fall-01` at 4.50 s, `fall-02` at 2.00 s,
`fall-03` at 3.25 s, `fall-04` at 1.50 s, `fall-05` at 4.50 s and `fall-06` at
2.00 s. External floor-fall additions are `gmd-sub2-fall-15.mp4` and
`gmd-sub3-fall-01.mp4`. No previously detected clip loses its candidate.

The sensitivity gain also adds ADL candidates on `gmd-sub2-adl-08.mp4`
(2.75 s and 7.50 s) and `gmd-sub3-adl-06.mp4` (4.50 s). Four external floor falls
still have no candidate: `gmd-sub2-fall-24.mp4`, `gmd-sub3-fall-04.mp4`,
`gmd-sub3-fall-18.mp4`, and `gmd-sub3-fall-21.mp4`. The full SAM/Muse/resident
gate was not replayed over these external additions, so this report does not
establish their notification outcomes. It is a different run from the historical
2-fps pilot and should not be combined with that pilot's gate results.

## Change under test

`backend/app/wall.py` now computes body-box aspect in physical pixels rather
than depending on camera canvas aspect. It also accepts confidently inverted
full-body evidence from overhead views and preserves a previously corroborated
lying track during a brief turn. A fresh narrow lying pose cannot use that
continuity rule. Geometry-transition thresholds retain their original 16:9
calibration. Unit tests cover canvas padding, overhead inversion versus forward
bending, low-confidence feet and same-track continuity.

## Evidence and reproduction

[`urfd_regression.json`](urfd_regression.json) records per-clip trigger times,
source and baseline hashes, package provenance, the manifest hash, expected case
count and an explicit `complete` flag. A growing report with `complete: false`
is not a finished run.

Current tracker only:

```powershell
.tmp/submission-venv/Scripts/python.exe -X utf8 evals/experiments/urfd_regression.py
```

Paired command used in this workspace (requires the saved local pre-fix module):

```powershell
.tmp/submission-venv/Scripts/python.exe -X utf8 evals/experiments/urfd_regression.py --baseline .tmp/wall-before-urid.py
```

The runner makes no cloud calls. The live short-clip demonstration of independent
image and hex Muse reviews is documented in
[`motion_integration.md`](motion_integration.md).
