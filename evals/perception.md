# Aegis perception report

Generated 2026-10-27 10:47 UTC.

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
| Median cost per frame | 101.2 ms |
| Effective throughput | 9.9 fps |

## Per clip

| Clip | Label | Source | Person frames | Motion | Max conf | ms/frame |
|---|---|---|---|---|---|---|
| urfd_fall-01.mp4 | fall | URFD fall sequence 01 | 19/80 (24%) | 50% | 0.854 | 120.5 |
| urfd_fall-02.mp4 | fall | URFD fall sequence 02 | 23/54 (43%) | 85% | 0.882 | 152.6 |
| urfd_fall-03.mp4 | fall | URFD fall sequence 03 | 68/108 (63%) | 87% | 0.922 | 214.4 |
| urfd_fall-04.mp4 | fall | URFD fall sequence 04 | 31/48 (65%) | 79% | 0.845 | 105.4 |
| cctv_fall.mp4 | fall | CAVIAR Rest_FallOnFloor (lobby, fixed camera) | 15/150 (10%) | 35% | 0.804 | 39.4 |
| cctv_corridor.mp4 | adl | CAVIAR OneStopEnter1cor (walking, stopping) | 166/180 (92%) | 92% | 0.932 | 101.2 |
| cctv_corridor_wide.mp4 | adl | CAVIAR Walk1 (two people walking) | 29/178 (16%) | 64% | 0.895 | 65.1 |
| cctv_entrance.mp4 | adl | CAVIAR EnterExitCrossingPaths1cor (crossing paths) | 84/105 (80%) | 80% | 0.911 | 86.8 |
| cctv_shopfront.mp4 | adl | CAVIAR OneLeaveShop1cor (leaving a shop) | 69/83 (83%) | 83% | 0.908 | 81.5 |

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
