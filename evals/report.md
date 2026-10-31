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
| Automated tests | 110 passed (29 Oct); 162 passed after the multimodal, privacy, edge, PIN, race, geometry and full-corpus batch (31 Oct) |

The earlier 30-fixture decision suite above remains the measured language/policy benchmark. This
section measures the new agent and media paths end to end; it is not a fall-recall study.

## Multimodal evidence tool — 2026-10-31

One live run against `muse-spark-1.3` and the external GMDCSA24 fall-01 clip, using the same
`EvidenceAgent` code path and a still from that clip.

| Check | Observed |
|---|---|
| Plan call | requested `household_memory`, `recent_events`, and `inspect_frame` in one bounded plan |
| Model-written visual question | “Is the person lying on the floor or on furniture in this still?” |
| Muse observation | person 1, posture lying, support floor, no others, confidence 0.90 |
| Final decision | escalate / urgent; rationale cites SAM plus the still |
| End-to-end latency | 13.7 s (plan 4.3 s, vision 4.5 s, decision 5.5 s) |
| Outbound ledger | still bytes recorded as `agent_frame` with 1-hour retention |

A second live run (same clip, `support_surface` reported as couch) used two planning calls:
the first requested memory, recent events and a floor-versus-furniture question; after the
observation returned floor/0.90, the follow-up planner requested no further call and the
decision was escalate/urgent in 13.3 s. A separate vision probe on the same frame returned
lying/floor at 0.95 confidence in 4.5 s. The visual tool is model-requested, not automatic;
quiet feeds never send a still, and a private clip only offers the redacted still.

Saved agent traces: `evals/agent_live_trace.json` (escalate) and
`evals/agent_live_reject.json` (bed false candidate rejected). The **full gate** artifacts
`evals/gate_escalate_fall01.json` and `evals/gate_reject_adl03.json` include real SAM frames,
the Muse tool trace and dispatch calls (one phase-2 page for the fall; zero for the reject).
Reproduce with `python scripts/probe_wall_gate.py …` and `python scripts/run_fall_pilot.py --fps 2`.

## External corpus pilot — 2026-10-31

The local gate in `scripts/run_fall_pilot.py` was run over the 110 external GMDCSA24
clips (Subjects 1–3: 62 fall files and 48 ADL files), sampled at 2 fps through the same
stateful wall tracker the console uses. Labels come from the dataset CSV descriptions:
57 are falls to the floor (including one from a bed to the floor), 5 are falls onto a
bed, and 48 are ADL. The full candidate list is in `evals/fall_pilot.json`; the source
labels are in `evals/corpus_manifest.json`.

| Metric | Measured |
|---|---|
| Floor-fall clips with a possible-fall candidate | **45 / 57** at 2 fps; 3 of the 12 misses triggered at 8 fps |
| Floor-fall clips with a family alert after the full SAM + Muse gate | **45 / 57 (79%)** |
| ADL clips with a family alert | **0 / 48**; 5 candidates, all rejected by the gate |
| Bed-fall clips with a family alert | **0 / 5**; 2 candidates, both rejected because the bed is support |
| Multi-person redaction | real entrance clip: SAM found multiple tracks, the raw clip was refused and the family still/clip were redacted |
| Gate artifacts | `evals/gate_pilot.json` (all 52 candidates), `evals/gate_escalate_fall01.json`, `evals/gate_reject_adl03.json` |

The 12 misses were not gate rejections. Most were frames where the person was lost
or occluded after the fall (several clips end at the moment of floor contact), or where
a large bed box made a floor fall look supported. Three of the twelve were caught when
sampled at 8 fps, so sampling rate is part of the ceiling. Dispatch and check-in calls
were instrumented rather than sent to a phone. This is a 110-clip, three-actor/room
test, not the 25-fall/60-negative target study or a multi-night field trial.

Reproduce with:

```bash
python scripts/run_fall_pilot.py --fps 2 --json evals/fall_pilot.json
python scripts/run_gate_pilot.py --pilot evals/fall_pilot.json --out evals/gate_pilot.json
```

The restlessness rule has unit coverage for pacing versus a straight walk and for the prayer
exclusion; the sim scenarios exercise the same policy floor and Evidence Agent. It remains a
movement heuristic, not a diagnostic classifier.
