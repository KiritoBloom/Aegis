# Aegis perception report

Generated 2026-11-01 14:50 UTC.

Detector in use: **yolo**. Every clip below is real fixed-camera
surveillance footage (CAVIAR, URFD) — not cinematic stock. Stock footage
does not match the framing the rules were calibrated on, so it cannot
validate them.

## What this measures

The motion gate and the person check, run over each clip frame by frame
through the same `MotionGate` and `PersonDetector` classes the edge agent
uses. It measures **whether the perception layer sees a person at all** —
the precondition for every rule downstream. It does not measure fall
classification accuracy, because these corpora ship no per-frame fall
annotations in the form this repo consumes.

| Metric | Value |
|---|---|
| Clips | 9 |
| Fall clips with a person detected | 5 / 5 |
| Frames analysed | 986 |
| Motion frames | 704 (71.4%) |
| Median cost per frame | 78.6 ms |
| Effective throughput | 12.7 fps |

## Per clip

| Clip | Label | Source | Person frames | Motion | Max conf | ms/frame |
|---|---|---|---|---|---|---|
| urfd_fall-01.mp4 | fall | URFD fall sequence 01 | 33/80 (41%) | 50% | 0.936 | 123.8 |
| urfd_fall-02.mp4 | fall | URFD fall sequence 02 | 32/54 (59%) | 85% | 0.882 | 100.0 |
| urfd_fall-03.mp4 | fall | URFD fall sequence 03 | 77/108 (71%) | 87% | 0.922 | 87.0 |
| urfd_fall-04.mp4 | fall | URFD fall sequence 04 | 33/48 (69%) | 79% | 0.845 | 77.8 |
| cctv_fall.mp4 | fall | CAVIAR Rest_FallOnFloor (lobby, fixed camera) | 28/150 (19%) | 35% | 0.804 | 47.1 |
| cctv_corridor.mp4 | adl | CAVIAR OneStopEnter1cor (walking, stopping) | 166/180 (92%) | 92% | 0.932 | 73.9 |
| cctv_corridor_wide.mp4 | adl | CAVIAR Walk1 (two people walking) | 41/178 (23%) | 64% | 0.895 | 91.5 |
| cctv_entrance.mp4 | adl | CAVIAR EnterExitCrossingPaths1cor (crossing paths) | 84/105 (80%) | 80% | 0.911 | 78.6 |
| cctv_shopfront.mp4 | adl | CAVIAR OneLeaveShop1cor (leaving a shop) | 69/83 (83%) | 83% | 0.908 | 69.8 |

## A defect this measurement found

The first run of this report, at `imgsz: 320`, found a person in only
**64.3%** of sampled frames and found **nothing at all** in the CAVIAR
lobby fall clip. That clip is a ceiling fisheye where a person is about
70px tall, and at 320px inference the model simply could not see her.

Sampled across all clips:

| imgsz | Frames with a person | ms/frame |
|---|---|---|
| 320 | 64.3% | 73 |
| 480 | 68.0% | 93 |
| 640 | 70.8% | 162 |
| 960 | 82.7% | 266 |

`config/edge.yaml` now runs at **640** — the knee of that curve, still
comfortably inside the 8fps budget — and `min_area_ratio` dropped from
0.012 to 0.004 so a person at the far end of a corridor is not filtered
out by an area threshold meant for a close-up. After the change every
fall clip detects a person. This is the kind of error that is invisible
in a demo and fatal in a hallway.

## Honest limits

- These clips are short (3–12s). They test that the pipeline sees people
  in real security-camera framing; they do not establish recall over a
  night.
- The fall label is per clip from the source corpus, not per frame. A
  'fall clip' is one that contains a fall, so a detector that sees the
  person standing before the fall still counts as seeing her.
- CAVIAR is a lobby and corridor corpus: wide-angle, fisheye, daylight,
  and 384x288. URFD is a single room at night with IR. Between them they
  cover the two conditions this product cares about, but neither is
  Margaret's hallway.
- This report is deliberately separate from `evals/report.md`, which
  measures the judgment layer on packets. Mixing a perception number and
  a decision number into one pass rate would make both meaningless.

## Licensing

CAVIAR (INRIA/University of Edinburgh, CC BY-SA) and URFD (Kwolek &
Kepski 2014, CC BY-NC-SA 4.0) are used for testing only and are not
redistributed: `data/` is gitignored and `data/reels/manifest.json`
records each clip's licence.
