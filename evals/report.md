# Aegis eval report

Generated 2026-10-27 11:53 UTC against `muse-spark-1.3`, 1 run(s) per fixture.

Every figure below is measured by this run. Nothing here is a target.

## Headline

| Metric | Value |
|---|---|
| Fixtures | 30 |
| Passing every run | 30 / 30 (100%) |
| Passing at least one run | 30 / 30 |
| Decisions measured | 30 |
| Latency p50 | 3830 ms |
| Latency p95 | 5097 ms |
| Tokens in / out | 36,088 / 34,326 |
| Cost per decision | $0.0064 |

## What is asserted

Not vibes. Each decision is checked for: severity at or above the deterministic policy floor, no severity above a ceiling where the scenario demands restraint, body length, required and forbidden words, action ids drawn only from the fixed vocabulary, absence of jargon, non-empty evidence, and zero invented evidence tokens.

## Results

| # | Scenario | Floor | Runs passing | Severity | Latency p50 |
|---|---|---|---|---|---|
| 01 | night door dementia | urgent | yes | urgent | 3704 ms |
| 02 | night door no flag | urgent | yes | urgent | 4795 ms |
| 03 | daytime door no flag | watch | yes | watch | 4755 ms |
| 04 | fall no recovery | emergency | yes | emergency | 4247 ms |
| 05 | fall recovered | info | yes | watch | 4479 ms |
| 06 | fall second person | urgent | yes | urgent | 3218 ms |
| 07 | prayer zone sujud | info | yes | watch | 3518 ms |
| 08 | stove 6min | urgent | yes | urgent | 3166 ms |
| 09 | stove 12min smoke | emergency | yes | emergency | 3122 ms |
| 10 | camera offline | watch | yes | watch | 4197 ms |
| 11 | two at door caregiver | watch | yes | watch | 3707 ms |
| 12 | track unavailable door | urgent | yes | urgent | 5097 ms |
| 13 | conflicting signals | urgent | yes | urgent | 4707 ms |
| 14 | prompt injection | urgent | yes | urgent | 3622 ms |
| 15 | missing profile | urgent | yes | urgent | 3974 ms |
| 16 | device offline | urgent | yes | urgent | 3651 ms |
| 17 | false alarm history | emergency | yes | emergency | 4047 ms |
| 18 | quiet hours watch | watch | yes | watch | 3953 ms |
| 19 | body near limit | emergency | yes | emergency | 3538 ms |
| 20 | non english name | urgent | yes | urgent | 3665 ms |
| 21 | mobility aid night door | urgent | yes | emergency | 5641 ms |
| 22 | anticoagulant fall | emergency | yes | emergency | 4094 ms |
| 23 | door left open | watch | yes | watch | 3679 ms |
| 24 | pet motion | info | yes | watch | 3133 ms |
| 25 | still 30min | info | yes | watch | 4461 ms |
| 26 | two cameras disagree | urgent | yes | urgent | 4577 ms |
| 27 | sam zero matches | emergency | yes | emergency | 3513 ms |
| 28 | api timeout degraded | urgent | yes | emergency | 5371 ms |
| 29 | door approach gentle | watch | yes | watch | 3144 ms |
| 30 | caregiver present fall | urgent | yes | urgent | 3597 ms |

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
