# Tracking and evidence refinement — 4 November 2026

## Local frame-processing benchmark

Command before and after the changes:

```sh
python scripts/bench_wall.py --mode dynamic --frames 24 --json .tmp/tracking-after.json
```

Same machine, warmed models, same 24 sampled frames from four external clips.
23 frames contained a detected person in both runs.

| Measurement | Before | After |
|---|---:|---:|
| Mean processing | 119.8 ms | 105.6 ms |
| p95 processing | 339.3 ms | 185.8 ms |
| Aggregate throughput | 8.35 fps | 9.47 fps |
| Full-frame pose calls | 27 | 24 |

Mean frame time decreased 11.9%; p95 decreased 45.2%. These are backend-local
wall-clock measurements, not browser frame rate, multifeed throughput, or
SAM/Muse latency. Inference still accounts for 97.8% of the final frame cost.

The first refinement removed redundant pose calls but exposed previously ignored
high-resolution retry sizes, increasing worst-case cost. Bounding retries to
one per missed sample, rotating 448/640/960 inputs across observations, produced
the final numbers above while retaining the recent-person recovery window.

## Continuous tracking check

```sh
python scripts/eval_wall_tracking.py --json .tmp/tracking-evaluation.json
```

Actual local models, five sampled frames per second, each clip in a fresh tracker,
without cloud requests. The evaluator writes frame-by-frame observations for
inspection and does not treat a local candidate as a completed family alert.

| Clip | Samples | Person coverage | Primary ID changes | Local candidates |
|---|---:|---:|---:|---|
| `gmd-sub3-fall-05.mp4` | 50 | 100% | 0 | Possible fall at 4.4 s |
| `gmd-sub2-adl-01.mp4` | 56 | 100% | 0 | None |
| `gmd-sub2-adl-05.mp4` | 54 | 100% | 0 | None |

Each clip used one primary identity. This three-clip check is a targeted
regression check, not a replacement for the earlier 110-clip pilot or a
real-home accuracy study. It does not establish bystander/occlusion performance.

## Engineering changes behind the results

- Maximum-total-score person association using motion, geometry and normalized
  appearance similarity. Stable feet anchor a collapsing body without predicting
  its next position below the floor.
- Per-person posture memory and sustained-down timers; another person cannot
  inherit a candidate's timer or recover their incident.
- Smoothed display boxes, observed joint overlays and motion trails. Raw boxes
  remain the policy input; stale live observations are hidden.
- Reused full-frame pose observations within rechecks/crop fallbacks, reused
  capture canvases, and duplicate video-time capture suppression.
- Bounded, time-retained evidence buffer so faster sampling does not shorten
  the event window.
- SAM geometry associated with the candidate rather than automatically choosing
  the largest bystander; measured trajectory shown in the ordinary camera view.
- Concurrent independent read-only Muse tools; conditional follow-up planning
  for ambiguous visual evidence. Final evidence judgment still runs.
- Persistent, camera-attributed pipeline steps, including partial model progress,
  tool questions, durations, check-in deadlines and delivery outcomes.

## Verification

- **271 backend tests passed**, including per-track timing, global association,
  collapse continuity, bounded rechecks, evidence retention, SAM target selection,
  concurrent tools, durable pipeline attribution and metadata deletion.
- Next.js production build, lint and TypeScript checks passed.
- Isolated browser flow passed setup/consent, synthetic camera capture, measured
  overlays, inline SAM/Muse evidence, resident-screen link, single SSE connection,
  capture across navigation, failed-ack retry, successful response, searchable
  saved steps and JSON export, plus desktop/mobile/dark layouts with no runtime
  errors. Browser model/API responses were fixtures; local footage evaluation
  above used the real local vision models.

There is no dedicated demonstration route. Cameras, alert details and Logs all
use the same evidence trail that monitoring produces.
