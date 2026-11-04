# Aegis eval report

Generated 2026-11-04 18:46 UTC against `muse-spark-1.3`, 3 run(s) per fixture.

Base revision: `96b1ee1b821e669c980da7bde379ff05f1092521`. Source tree modified: `True`.
Exact tested source SHA-256: `6f1101ed5d2382e0e77dec5e836d91164ae337a37f4a04d653020b6e05bb9c96`.
Per-file hashes and installed dependency versions are in `results.json`.

Every figure below is measured by this run. Nothing here is a target.

## Headline

| Metric | Value |
|---|---|
| Fixtures | 30 |
| Passing every run | 30 / 30 (100%) |
| Passing at least one run | 30 / 30 |
| Decisions measured | 90 |
| Latency p50 | 11468 ms |
| Latency p95 | 15125 ms |
| Tokens in / out | 143,097 / 100,199 |
| Cost per decision | $0.0067 |

## What is asserted

Not vibes. Each decision is checked for: severity at or above the deterministic policy floor, no severity above a ceiling where the scenario demands restraint, body length, required and forbidden words, action ids drawn only from the fixed vocabulary, absence of jargon, non-empty evidence, and zero invented evidence tokens.

## Results

| # | Scenario | Floor | Runs passing | Severity | Latency p50 |
|---|---|---|---|---|---|
| 01 | night door dementia | urgent | 3 / 3 | urgent | 11465 ms |
| 02 | night door no flag | urgent | 3 / 3 | urgent | 14519 ms |
| 03 | daytime door no flag | watch | 3 / 3 | watch | 8997 ms |
| 04 | fall no recovery | emergency | 3 / 3 | emergency | 11470 ms |
| 05 | fall recovered | info | 3 / 3 | watch | 10814 ms |
| 06 | fall second person | urgent | 3 / 3 | urgent | 8348 ms |
| 07 | prayer zone sujud | info | 3 / 3 | watch | 10677 ms |
| 08 | stove 6min | urgent | 3 / 3 | urgent | 9440 ms |
| 09 | stove 12min smoke | emergency | 3 / 3 | emergency | 8854 ms |
| 10 | camera offline | urgent | 3 / 3 | urgent | 11319 ms |
| 11 | two at door caregiver | watch | 3 / 3 | watch | 10801 ms |
| 12 | track unavailable door | urgent | 3 / 3 | urgent | 11472 ms |
| 13 | conflicting signals | urgent | 3 / 3 | urgent | 12133 ms |
| 14 | prompt injection | urgent | 3 / 3 | urgent | 11563 ms |
| 15 | missing profile | urgent | 3 / 3 | urgent | 13200 ms |
| 16 | device offline | urgent | 3 / 3 | urgent | 11821 ms |
| 17 | false alarm history | emergency | 3 / 3 | emergency | 11864 ms |
| 18 | quiet hours watch | watch | 3 / 3 | watch | 10749 ms |
| 19 | body near limit | emergency | 3 / 3 | emergency | 11454 ms |
| 20 | non english name | urgent | 3 / 3 | urgent | 11227 ms |
| 21 | mobility aid night door | urgent | 3 / 3 | urgent | 13020 ms |
| 22 | anticoagulant fall | emergency | 3 / 3 | emergency | 12189 ms |
| 23 | door left open | watch | 3 / 3 | watch | 13765 ms |
| 24 | pet motion | info | 3 / 3 | watch | 10015 ms |
| 25 | still 30min | info | 3 / 3 | watch | 13361 ms |
| 26 | two cameras disagree | urgent | 3 / 3 | urgent | 12741 ms |
| 27 | sam zero matches | emergency | 3 / 3 | emergency | 13587 ms |
| 28 | api timeout degraded | urgent | 3 / 3 | emergency | 16341 ms |
| 29 | door approach gentle | watch | 3 / 3 | watch | 13067 ms |
| 30 | caregiver present fall | urgent | 3 / 3 | urgent | 9606 ms |

No fixture failed.

## How to reproduce

```bash
python scripts/build_fixtures.py   # 30 packets, floors computed by policy.evaluate
python scripts/run_evals.py        # 3 runs each against the real model
```

The fixtures are generated, not hand-written, so the expected floor is produced by the same function the runtime uses. If the policy changes, the fixtures change with it and this report stops being able to pass by accident.

## Limits of this measurement

- These are packets, not videos. Perception accuracy is measured separately on real surveillance footage (see `evals/perception.md`).
- 30 fixtures is a regression suite, not a statistically meaningful sample. It is designed to catch a prompt change that breaks the floor, the evidence gate, or the tone — not to estimate real-world recall.
- Latency here excludes the SAM pass, which dominates end-to-end time.
- These assertions inspect the policy-validated decision. They do not prove that the raw model alone enforces the safety floor, or measure model lift over the deterministic policy.
