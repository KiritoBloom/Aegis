# Evidence index

Every claim in `README.md` points here. An artifact is either **local**
(reproducible without model credentials) or **live** (requires
`META_API_KEY`). Dates are UTC. Test counts and file hashes can change with a
new commit; the reports record the date of their run.

## Decision and agent evidence

| Artifact | What it measures | Generated | Kind |
|---|---|---|---|
| `report.md`, `results.json` | 30 safety fixtures through the real Muse Spark decision schema and validator | 2026-11-01 | live |
| `fixtures/*.json` | The 30 packets; `policy_floor` is computed by `policy.evaluate` at generation time | regenerated 2026-11-01 | local |
| `agent_live_trace.json` | One live Evidence Agent trace that escalates a real external fall clip | 2026-10-31 | live |
| `agent_live_reject.json` | One live trace where the agent rejects a candidate | 2026-10-31 | live |
| `gate_escalate_fall01.json` | The full wall gate on a real fall: SAM features, agent trace, one phase-2 dispatch and a check-in | 2026-10-31 | live |
| `gate_reject_adl03.json` | The full gate rejecting an ADL candidate: zero dispatches | 2026-10-31 | live |
| `gate_pilot.json` | Outcome of the gate for every pilot candidate (52 clips), before the bed-fall prompt change | 2026-10-31 | live |
| `gate_targeted_after_fix.json` | The two bed-fall and five ADL candidates re-run after SAM geometry and the bed-fall prompt change | 2026-11-01 | live |
| `fall_pilot.json` | Local candidate results for all 110 external clips at 2 fps (pre-recheck) | 2026-10-31 | local |
| `fall_pilot_recheck.json` | Same corpus after the person-lost recheck: 49/57 floor-fall candidates, no new ADL candidates | 2026-11-01 | local |
| `fall_pilot_yolov8m.json` | Opt-in `AEGIS_RECHECK_MODEL=yolov8m.pt` recheck: 49/57 floor falls (one swapped), 6/48 ADL candidates | 2026-11-01 | local |
| `gate_adl08_yolov8m.json` | The extra ADL candidate under the v8m recheck escalated — the measured reason it is opt-in, not default | 2026-11-01 | live |
| `gate_recheck_rescued.json` | The four rescued floor falls through the full gate: 3 verified escalations, 1 fail-open | 2026-11-01 | live |
| `fall_pilot_8fps.json` | The 12 floor-fall misses re-sampled at 8 fps | 2026-10-31 | local |
| `corpus_manifest.json` | Source labels: 57 floor falls, 5 bed falls, 48 ADL | 2026-10-31 | local |

The gate artifacts predate the release-lease and held-row privacy changes, but
those changes do not alter the local wall decision or the SAM/Muse verdict;
they change who can see a row while it is held and how a crash is recovered.
The current mask evidence is in `meta/sam_redaction.json`.

## Perception, privacy and performance

| Artifact | What it measures | Generated | Kind |
|---|---|---|---|
| `meta/sam_redaction.json` | Live SAM on a multi-person entrance reel: 240/240 masks decoded, 7 tracks, 192 mask blurs, 503,794 pixels, 0 box fallbacks, output hashes; generated at `26076cd` | 2026-11-01 | live |
| `privacy.json` | Idle egress, retention coverage, private-zone masking, delete-all, no face recognition, pause/resume | 2026-10-31 | local |
| `performance.md`, `performance.json` | Wall benchmark: dynamic, static and empty-room cost with raw output | 2026-11-01 | local |
| `perception.md`, `perception.json` | Person detection on real CAVIAR/URFD footage | 2026-11-01 | local |
| `checkin.md`, `checkin.json` | Five real Muse Voice Transcribe outputs through the de-escalation table | 2026-10-27 | live |
| `checkin_read.json` | Two live Spark reads where the word list missed distress and the safety merge raised the answer to help; generated at `26076cd` | 2026-11-01 | live |

The perception report is a person-detection check, not fall accuracy, and is
not used to support a recall claim. The fall pilot is the recall evidence.

## What the numbers do not prove

- The external pilot is 110 clips from three actors/rooms, not a field trial.
- The check-in suite is clean synthetic speech, not a noisy-room ASR benchmark.
- The decision fixtures are a regression suite, not a statistical sample.
- Performance is wall-clock on one shared-CPU laptop; absolute rates are
  machine-specific.
- No source footage is committed. `data/` is ignored, and the artifacts record
  hashes and measurements instead of distributing licensed media.

## Re-run

```bash
python -m pytest -q
cd web && npx tsc --noEmit && npm run build
python scripts/privacy_tests.py
python scripts/bench_wall.py --mode dynamic --frames 24
python scripts/probe_redaction.py
python scripts/run_fall_pilot.py --fps 2 --json evals/fall_pilot.json
python scripts/run_gate_pilot.py --pilot evals/fall_pilot.json --out evals/gate_pilot.json
python scripts/run_evals.py --runs 1
```
