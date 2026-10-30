# Aegis eval report

Generated 2026-10-29 15:39 UTC against `muse-spark-1.3`, 1 run(s) per fixture.

Every figure below is measured by this run. Nothing here is a target.

## Headline

| Metric | Value |
|---|---|
| Fixtures | 30 |
| Passing every run | 30 / 30 (100%) |
| Passing at least one run | 30 / 30 |
| Decisions measured | 30 |
| Latency p50 | 4402 ms |
| Latency p95 | 5514 ms |
| Tokens in / out | 43,168 / 33,710 |
| Cost per decision | $0.0066 |

## What is asserted

Not vibes. Each decision is checked for: severity at or above the deterministic policy floor, no severity above a ceiling where the scenario demands restraint, body length, required and forbidden words, action ids drawn only from the fixed vocabulary, absence of jargon, non-empty evidence, and zero invented evidence tokens.

## Results

| # | Scenario | Floor | Runs passing | Severity | Latency p50 |
|---|---|---|---|---|---|
| 01 | night door dementia | urgent | yes | urgent | 4932 ms |
| 02 | night door no flag | urgent | yes | urgent | 4942 ms |
| 03 | daytime door no flag | watch | yes | watch | 6029 ms |
| 04 | fall no recovery | emergency | yes | emergency | 4982 ms |
| 05 | fall recovered | info | yes | watch | 4268 ms |
| 06 | fall second person | urgent | yes | urgent | 3544 ms |
| 07 | prayer zone sujud | info | yes | watch | 4106 ms |
| 08 | stove 6min | urgent | yes | urgent | 4218 ms |
| 09 | stove 12min smoke | emergency | yes | emergency | 5116 ms |
| 10 | camera offline | watch | yes | watch | 5514 ms |
| 11 | two at door caregiver | watch | yes | watch | 3825 ms |
| 12 | track unavailable door | urgent | yes | urgent | 5483 ms |
| 13 | conflicting signals | urgent | yes | urgent | 5861 ms |
| 14 | prompt injection | urgent | yes | urgent | 4171 ms |
| 15 | missing profile | urgent | yes | urgent | 4519 ms |
| 16 | device offline | urgent | yes | urgent | 5172 ms |
| 17 | false alarm history | emergency | yes | emergency | 3572 ms |
| 18 | quiet hours watch | watch | yes | watch | 3781 ms |
| 19 | body near limit | emergency | yes | emergency | 4808 ms |
| 20 | non english name | urgent | yes | urgent | 5243 ms |
| 21 | mobility aid night door | urgent | yes | urgent | 4456 ms |
| 22 | anticoagulant fall | emergency | yes | emergency | 5362 ms |
| 23 | door left open | watch | yes | watch | 3750 ms |
| 24 | pet motion | info | yes | watch | 3858 ms |
| 25 | still 30min | info | yes | watch | 4348 ms |
| 26 | two cameras disagree | urgent | yes | urgent | 4129 ms |
| 27 | sam zero matches | emergency | yes | emergency | 3804 ms |
| 28 | api timeout degraded | urgent | yes | emergency | 5221 ms |
| 29 | door approach gentle | watch | yes | watch | 3989 ms |
| 30 | caregiver present fall | urgent | yes | urgent | 4055 ms |

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

## Evidence Agent checks — 2026-10-29

These are live checks against the Meta Model API and the local fall clip, not a statistical
benchmark. They exercise the tool-using Spark agent and the wall’s pre-escalation gate.

| Check | Observed |
|---|---|
| Wall candidate held before family contact | provisional incident `verifying`; 0 notifications; 0 check-ins |
| Evidence clip | 6.0–7.0 s, SAM 48–50 frames, 1 person |
| Agent trace | household memory (5 facts), recent events (8 rows), SAM observation, plan, decision |
| Real fall verdict | escalate; one phase-2 family alert after corroboration; check-in asking |
| Verification before any family alert | 0 pre-verification notifications |
| Audio path | 4.0 s loud WAV accepted; Muse Transcribe + agent audio_context; floor urgent; check-in asking |
| False two-person frames | 0 on `home-bed-sit` after raising the pose floor to 0.40 |
| Automated tests | 104 passed |

The earlier 30-fixture decision suite above remains the measured language/policy benchmark. This
section measures the new agent and media paths end to end; it is not a fall-recall study.

The restlessness rule has unit coverage for pacing versus a straight walk and for the prayer
exclusion; the sim scenarios exercise the same policy floor and Evidence Agent. It remains a
movement heuristic, not a diagnostic classifier.
