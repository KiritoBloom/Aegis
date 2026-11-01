# Continuous wall performance

Measured on 2026-10-31 at the current working tree with
`scripts/bench_wall.py`, on four external GMDCSA24 clips at 768 px. These are
wall-clock timings on a shared CPU, not model datasheet numbers. Run-to-run
variance is real; the table below reports three consecutive runs rather than a
single best sample.

## Current measurement

| Mode | Run | Frames | Mean | Aggregate | Per feed ×4 |
|---|---|---:|---:|---:|---:|
| Dynamic external footage | 1 | 24 | **92.4 ms/frame** | 10.82 fps | 2.70 fps |
| Dynamic external footage | 2 | 24 | **95.0 ms/frame** | 10.52 fps | 2.63 fps |
| Dynamic external footage | 3 | 24 | **95.8 ms/frame** | 10.43 fps | 2.61 fps |
| Held / repeated frame | 1 | 16 | **11.4 ms/frame** | 87.7 fps | 21.9 fps (upper bound) |
| Held / repeated frame | 2 | 16 | **11.5 ms/frame** | 86.9 fps | 21.7 fps (upper bound) |
| Held / repeated frame | 3 | 16 | **11.2 ms/frame** | 89.1 fps | 22.3 fps (upper bound) |

The static mode includes one full inference (the first frame) and fifteen
reused frames; its median reused frame is **0.2–0.5 ms**.

Earlier runs on the same machine measured **107 ms/frame** and a strict
reviewer measured **126 ms/frame** under heavier CPU load. Use the range
**roughly 92–126 ms/frame** for planning; do not quote a single sample as a
constant. The browser console is slower than this benchmark because it also
JPEG-encodes, uploads and sleeps between samples; a live four-feed session was
observed around **1.2–1.3 fps per feed** during QA, and the console displays its
own measured rate.

## Cost breakdown (dynamic runs)

| Cost | Per call | Count | Mean |
|---|---:|---:|---:|
| Pose, full frame, 448 px | 46.8–48.8 ms | 24 | ~48 ms |
| YOLO, 320 px, person-only | 33.7–35.0 ms | 18 | ~34 ms |
| YOLO, 416 px, all classes | 48.1–50.7 ms | 6 | ~49 ms |
| Pose, 256 px crop | 37.5–41.5 ms | 4 | ~39 ms |
| Tracker | ~0.1 ms | 24 | <1% |
| Descriptor | ~0.3 ms | 27 | <1% |

Inference is ~98% of the backend cost. The steady state is model-bound; the only
safe wins are avoiding duplicate model work, not weakening perception.

## Safe wins kept

1. **Warm both models at startup.** `scene.warmup()` runs one 64×64 prediction
   through YOLO and pose. The first analysed frame dropped from ~3.04 s to
   ~0.17–0.22 s in the original measurement; the one-time cost moved to the
   background startup task.
2. **Exact identical-frame reuse.** A pixel-identical upload reuses the previous
   scene analysis, including tracking/lying timers, without re-running the
   models. The check is `np.array_equal`, not a perceptual threshold: a slow
   slide must never be skipped. On a held frame, mean cost falls to ~11–12 ms;
   the first frame still pays full inference.

## Optimization attempted and rejected

Reusing an empty object cache (running the cheaper 320 px person-only path when
no furniture was cached) looked good on an empty-room benchmark. On the 110-clip
corpus it lost a real floor-fall candidate and turned one bed fall and two ADLs
into candidates. That is a safety regression, so the empty-cache shortcut was
reverted. Object-free feeds keep checking for furniture on every sample; people
are still detected every sample at 320 px when an object cache exists.

## Reproduce

```sh
python scripts/bench_wall.py --mode dynamic --frames 24
python scripts/bench_wall.py --mode static --frames 16
python scripts/bench_wall.py --mode empty --frames 24
python scripts/bench_wall.py --mode cold
python scripts/bench_wall.py --mode dynamic --profile
```

Absolute rates are machine-specific and the four-feed estimate is a ceiling
because inference is serialized by one lock.

## Deliberately not done

Lowering pose resolution/frequency, dropping the person pass when pose sees
someone, a perceptual frame-change gate, or cross-feed batching. Each trades
recall, adds a scheduler, or assumes a GPU; the wall is a safety path, not a
video renderer.
