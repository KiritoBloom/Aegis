# Continuous wall performance

Measured on 2026-10-31 with `scripts/bench_wall.py` on four external GMDCSA24
clips at 768 px. These are wall-clock timings, not model datasheet numbers.

## Current measurement

| Mode | Frames | Mean | Aggregate | Per feed ×4 |
|---|---:|---:|---:|---:|
| Dynamic external footage | 24 | **107.0 ms/frame** | 9.35 fps | **2.34 fps** |
| Held / repeated frame | 16 | **12.0 ms/frame** | 83.1 fps | 20.8 fps (upper bound) |

The browser console is slower than this benchmark because it also JPEG-encodes,
uploads and sleeps between samples; a live four-feed session was observed at
about **1.2–1.3 fps per feed** during QA. Inference is 98% of the backend cost;
Python tracking, descriptors and support checks are about 2 ms/frame.

## Cost breakdown

| Cost | Per call | Frequency | Share |
|---|---|---|---|
| Pose, full frame, 448 px | 45–57 ms | every sample | ~54% |
| YOLO, 320 px, person-only | 31–36 ms | every sample when object cache is warm | ~38% |
| YOLO, 416 px, all classes | 38–50 ms | every 5 s on object feeds; every sample on object-free feeds | ~5%+ |
| Pose, 256 px crop | 26–38 ms | only for YOLO-only people | ~4% |
| Tracker + descriptor | ~0.4 ms | every sample | <1% |

The steady state is model-bound. The only safe wins are avoiding duplicate
model work, not weakening perception.

## Safe wins kept

1. **Warm both models at startup.** `scene.warmup()` runs one 64×64 prediction
   through YOLO and pose. The first analysed frame dropped from ~3.04 s to
   ~0.17–0.22 s; the one-time cost moved to the background startup task.
2. **Exact identical-frame reuse.** A pixel-identical upload reuses the previous
   scene analysis, including tracking/lying timers, without re-running the
   models. The check is `np.array_equal`, not a perceptual threshold: a slow
   slide must never be skipped. On a held frame, mean cost fell from ~84 ms to
   ~12 ms; the first frame still pays full inference.

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

Run-to-run variance on a shared CPU is 10–15%; use multiple runs. Absolute rates
are machine-specific. The four-feed estimate is a ceiling because inference is
serialized by one lock.

## Deliberately not done

Lowering pose resolution/frequency, dropping the person pass when pose sees
someone, a perceptual frame-change gate, or cross-feed batching. Each trades
recall, adds a scheduler, or assumes a GPU; the wall is a safety path, not a
video renderer.
