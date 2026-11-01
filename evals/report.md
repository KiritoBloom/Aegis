# Aegis eval report

Generated 2026-11-01 14:48 UTC against `muse-spark-1.3`, 1 run per fixture.
Every figure in the decision section is measured by that run. Perception,
gate, privacy and performance measurements come from the artifacts named beside
them. Nothing here is a target.

## Headline

| Metric | Value |
|---|---|
| Fixtures | 30 |
| Passing every run | 30 / 30 (100%) |
| Passing at least one run | 30 / 30 |
| Decisions measured | 30 |
| Latency p50 | 4363 ms |
| Latency p95 | 5812 ms |
| Tokens in / out | 47,699 / 34,375 |
| Cost per decision | $0.0069 |

## What is asserted

Each decision is checked for: severity at or above the deterministic policy
floor, no severity above a ceiling where the scenario demands restraint, body
length, required and forbidden words, action ids drawn only from the fixed
vocabulary, absence of jargon, non-empty evidence, and zero invented evidence
tokens. The fixtures expose the camera-offline floor as `urgent` because the
heartbeat watchdog now produces real offline incidents.

## Results

| # | Scenario | Floor | Runs passing | Severity | Latency p50 |
|---|---|---|---|---|---|
| 01 | night door dementia | urgent | yes | urgent | 5812 ms |
| 02 | night door no flag | urgent | yes | urgent | 5586 ms |
| 03 | daytime door no flag | watch | yes | watch | 4255 ms |
| 04 | fall no recovery | emergency | yes | emergency | 4126 ms |
| 05 | fall recovered | info | yes | watch | 4118 ms |
| 06 | fall second person | urgent | yes | urgent | 4141 ms |
| 07 | prayer zone sujud | info | yes | watch | 3808 ms |
| 08 | stove 6min | urgent | yes | urgent | 4044 ms |
| 09 | stove 12min smoke | emergency | yes | emergency | 2844 ms |
| 10 | camera offline | urgent | yes | urgent | 5078 ms |
| 11 | two at door caregiver | watch | yes | watch | 4369 ms |
| 12 | track unavailable door | urgent | yes | urgent | 5187 ms |
| 13 | conflicting signals | urgent | yes | urgent | 4197 ms |
| 14 | prompt injection | urgent | yes | urgent | 6159 ms |
| 15 | missing profile | urgent | yes | urgent | 4225 ms |
| 16 | device offline | urgent | yes | urgent | 5409 ms |
| 17 | false alarm history | emergency | yes | emergency | 4133 ms |
| 18 | quiet hours watch | watch | yes | watch | 4512 ms |
| 19 | body near limit | emergency | yes | emergency | 3776 ms |
| 20 | non english name | urgent | yes | urgent | 5361 ms |
| 21 | mobility aid night door | urgent | yes | urgent | 4357 ms |
| 22 | anticoagulant fall | emergency | yes | emergency | 4716 ms |
| 23 | door left open | watch | yes | watch | 3616 ms |
| 24 | pet motion | info | yes | watch | 3761 ms |
| 25 | still 30min | info | yes | watch | 5093 ms |
| 26 | two cameras disagree | urgent | yes | urgent | 4775 ms |
| 27 | sam zero matches | emergency | yes | emergency | 3132 ms |
| 28 | api timeout degraded | urgent | yes | urgent | 8587 ms |
| 29 | door approach gentle | watch | yes | watch | 4814 ms |
| 30 | caregiver present fall | urgent | yes | urgent | 4921 ms |

No fixture failed.

The fixtures are generated, not hand-written: `policy_floor` comes from
`policy.evaluate` and the packet from the same builder the runtime uses.
`tests/test_fixtures_current.py` recomputes every floor and fails if a fixture
drifts from the current policy.

## Live gate, agent and redaction evidence

| Check | Observed | Artifact |
|---|---|---|
| A wall candidate is held before family contact | provisional `verifying` row; family list/detail/media return nothing; 0 notifications | `tests/test_verification_lifecycle.py` |
| Release survives a crash | the claim stores a 120s lease; an expired lease is retried; a durable notify phase prevents a second page | same |
| Late verdict after recovery | no 600s cooldown re-arm; the next fall can trigger | `tests/test_wall.py` |
| Camera loss has a producer | heartbeat watchdog: one urgent incident per outage after 90s; never-connected cameras ignored | `tests/test_camera_watchdog.py` |
| Real fall, full gate | SAM found one person; Muse escalated; one phase-2 dispatch; check-in started | `gate_escalate_fall01.json` |
| ADL candidate, full gate | Muse rejected; zero dispatches | `gate_reject_adl03.json` |
| SAM mask decode and redaction at current HEAD | 240/240 masks decoded on a multi-person entrance reel, 7 tracks, 192 mask blurs, 503,806 pixels, 0 box fallbacks, 48 frames | `meta/sam_redaction.json` |
| Check-in de-escalation with real ASR | 5/5 cases | `checkin.md` |
| Spark check-in reading on word-list misses | 2/2 raised to help | `checkin_read.json` |

The older 203/203 mask observation is superseded by the current probe, which
also counts how many masks were actually used to blur pixels. The gate
artifacts predate the release-lease and held-row privacy changes; those changes
do not alter the local decision or the SAM/Muse verdict, only who can see a
held row and how a crash is recovered.

## External corpus pilot

110 external GMDCSA24 clips (Subjects 1–3), sampled at 2 fps through the same
stateful wall tracker as the console. Labels are from the dataset description:
57 floor falls, 5 bed falls, 48 ADL.

| Metric | Measured | Artifact |
|---|---|---|
| Floor-fall clips with a possible-fall candidate | **45 / 57** | `fall_pilot.json` |
| Floor-fall clips with a family alert after the full gate | **45 / 57 (79%)**; measured before the bed-fall prompt change; all 45 were already escalate | `gate_pilot.json` |
| Floor-fall misses re-sampled at 8 fps | **3 / 12** produced a candidate | `fall_pilot_8fps.json` |
| ADL clips with a family alert | **2 / 48** after the bed-fall fix; 2 of 5 candidates escalated, 3 rejected. Before: 0 / 48. | `gate_targeted_after_fix.json` |
| Bed-fall clips with a family alert | **2 / 5** after the bed-fall fix; both candidates escalated, 3 had no candidate. Before: 0 / 5. | `gate_targeted_after_fix.json` |

The 12 misses at 2 fps were not gate rejections; all 45 candidates released.
Most misses were person loss or occlusion after the fall, or a large bed box
making a floor fall look supported. Dispatch and check-in calls were
instrumented rather than sent to a phone.

## Defects fixed in this verification pass

| Defect found by the strict review | Fix | Evidence |
|---|---|---|
| `verifying` candidates appeared in the family list and could have media signed before release | held states excluded from list, detail and media routes | lifecycle tests |
| A rejected wall candidate was still readable by detail and media | `hidden_from_family` covers held and rejected rows | lifecycle tests |
| `verifying → releasing` cleared the only deadline; a crash could strand a fall | release stores a lease; sweep includes `releasing`; completion clears it | lease tests |
| A crash after the phase-2 claim lost the verified page | the decision is stored before dispatch; the retry delivers it if no notification row exists | `test_a_stored_decision_is_delivered_after_a_crashed_release` |
| A crash after the phase-1 claim lost the first page | a claim with no notification row is finished instead of skipped | `test_phase_one_finishes_a_claimed_but_unsent_page` |
| A late SAM/Muse verdict re-armed the 600s cooldown after recovery | `arm_cooldown` is false when the incident is already closed or releasing | wall test |
| The verifier could reject a visible person with no sight evidence | reject without an `inspect_frame` observation fails open | wall gate path |
| SAM geometry never reached the Evidence Agent, so bed falls could not be distinguished from bed rest | track geometry is in the gate packet; the prompt no longer treats support alone as contradiction | `gate_targeted_after_fix.json`, full pilot |
| `camera_offline` was declared urgent but had no producer | heartbeat watchdog creates one incident per outage; a reconnect closes it | watchdog tests |
| A decision fixture still expected `watch` for camera offline while runtime said `urgent` | fixtures regenerated; drift test added | `test_fixtures_current.py` |
| The PIN lockout could be bypassed by rotating X-Forwarded-For | a process-wide failure bucket bounds total attempts | `test_rotating_addresses_cannot_bypass_the_global_lockout` |
| The check-in artifact did not include Spark's read, and the kiosk was described as ASR | live two-case read artifact; kiosk documented as a button fallback | `checkin_read.json` |
| README and report carried stale counts and unverifiable claims | both rewritten from current artifacts; SAM evidence regenerated at current HEAD | this report |

## Full verification

- Backend: **233 tests passed** (`python -m pytest -q`), 2026-11-01.
- Web: TypeScript clean and Next.js production build clean.
- Privacy: 10/10 properties; idle egress **0 bytes / 0 rows in 3s**; 120 ledger
  rows, all with retention; private zone excluded; delete-all verified
  (`privacy.json`).
- Performance: dynamic runs **91.2 / 92.4 / 95.0 / 95.8 ms per frame**;
  reused-frame median **0.2–0.5 ms** (the first frame pays full inference);
  **2.6–2.7 fps/feed** in the recorded runs. A strict review run measured
  121.4 ms and earlier samples measured 107 and 126 ms on the same machine
  under different CPU load; use **~91–126 ms** for planning
  (`performance.md`, `performance.json`).

## Reproduce

```bash
python scripts/build_fixtures.py
python scripts/run_evals.py --runs 1
python -m pytest -q
python scripts/privacy_tests.py
python scripts/bench_wall.py --mode dynamic --frames 24
python scripts/probe_redaction.py
python scripts/run_fall_pilot.py --fps 2 --json evals/fall_pilot.json
python scripts/run_gate_pilot.py --pilot evals/fall_pilot.json --out evals/gate_pilot.json
```

## Limits

- The fixtures are packets, not videos. Fall recall is measured separately on
  real footage; this suite measures decisions given structured evidence.
- Thirty fixtures is a regression suite, not a statistically meaningful sample.
- Latency here excludes the SAM pass and check-in speech.
- The pilot is 110 clips from three actors/rooms, not a multi-night field trial.
- The check-in suite is clean synthetic speech through the deterministic
  classifier and de-escalation table; it does not measure Spark's check-in read,
  and the kiosk buttons are a separate fallback.
- The SAM redaction probe uses a real multi-person surveillance reel but one
  model call and one model build; it proves the codec and render path, not
  redaction accuracy on every camera.

The restlessness rule has unit coverage for pacing versus a straight walk and
for the prayer exclusion. It remains a movement heuristic, not a diagnostic
classifier.
