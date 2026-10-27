# Aegis eval report

Generated 2026-10-27 10:34 UTC against `muse-spark-1.3`, 3 run(s) per fixture.

Every figure below is measured by this run. Nothing here is a target.

## Headline

| Metric | Value |
|---|---|
| Fixtures | 30 |
| Passing every run | 30 / 30 (100%) |
| Passing at least one run | 30 / 30 |
| Decisions measured | 90 |
| Latency p50 | 4002 ms |
| Latency p95 | 5497 ms |
| Tokens in / out | 108,264 / 101,542 |
| Cost per decision | $0.0063 |

## What is asserted

Not vibes. Each decision is checked for: severity at or above the deterministic policy floor, no severity above a ceiling where the scenario demands restraint, body length, required and forbidden words, action ids drawn only from the fixed vocabulary, absence of jargon, non-empty evidence, and zero invented evidence tokens.

## Results

| # | Scenario | Floor | Runs passing | Severity | Latency p50 |
|---|---|---|---|---|---|
| 01 | night door dementia | urgent | yes | urgent | 3816 ms |
| 02 | night door no flag | urgent | yes | urgent | 4996 ms |
| 03 | daytime door no flag | watch | yes | watch | 3510 ms |
| 04 | fall no recovery | emergency | yes | emergency | 3282 ms |
| 05 | fall recovered | info | yes | watch | 3874 ms |
| 06 | fall second person | urgent | yes | urgent | 3936 ms |
| 07 | prayer zone sujud | info | yes | watch | 4140 ms |
| 08 | stove 6min | urgent | yes | urgent | 3548 ms |
| 09 | stove 12min smoke | emergency | yes | emergency | 3820 ms |
| 10 | camera offline | watch | yes | watch | 4112 ms |
| 11 | two at door caregiver | watch | yes | watch | 4051 ms |
| 12 | track unavailable door | urgent | yes | urgent | 4063 ms |
| 13 | conflicting signals | urgent | yes | urgent | 5158 ms |
| 14 | prompt injection | urgent | yes | urgent | 3703 ms |
| 15 | missing profile | urgent | yes | urgent | 3601 ms |
| 16 | device offline | urgent | yes | urgent | 3845 ms |
| 17 | false alarm history | emergency | yes | emergency | 3560 ms |
| 18 | quiet hours watch | watch | yes | watch | 3923 ms |
| 19 | body near limit | emergency | yes | emergency | 3551 ms |
| 20 | non english name | urgent | yes | urgent | 4320 ms |
| 21 | mobility aid night door | urgent | yes | urgent | 4150 ms |
| 22 | anticoagulant fall | emergency | yes | emergency | 4299 ms |
| 23 | door left open | watch | yes | watch | 3890 ms |
| 24 | pet motion | info | yes | watch | 3502 ms |
| 25 | still 30min | info | yes | watch | 4894 ms |
| 26 | two cameras disagree | urgent | yes | urgent | 4670 ms |
| 27 | sam zero matches | emergency | yes | emergency | 4613 ms |
| 28 | api timeout degraded | urgent | yes | emergency | 4458 ms |
| 29 | door approach gentle | watch | yes | watch | 3922 ms |
| 30 | caregiver present fall | urgent | yes | urgent | 3690 ms |

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
