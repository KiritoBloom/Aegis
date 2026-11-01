# Aegis

**A privacy-first care system for an elderly parent who lives alone.** A small
in-home process watches for a short list of dangerous transitions, keeps raw
video on the household's own machine, and contacts the family only when an
event survives both a deterministic safety floor and an evidence check.

Built solo for the **Meta Global AI Developer Hackathon** (Indie Developer
track), 27 Oct – 4 Nov 2026. Every number below links to an artifact in
`evals/`; claims without a run are not made here.

---

## The product

The choice on the market is constant surveillance or no coverage. Aegis watches
**transitions**, not people:

- a person goes from upright to lying and does not get up;
- the front door opens at night;
- a hot plate is on with nobody in the kitchen;
- a cry for help comes from a room whose television is also on;
- a camera stops checking in and the house goes blind;
- nothing happens — in which case it sends nothing.

Silence is a product feature, not a failure. The system is an event notifier:
it does not diagnose, identify anyone, give medical advice, or dispatch help.

## How an event moves through the system

```
in-home feed
   │  local YOLO + pose + tracker (no cloud call on a quiet frame)
   ▼
deterministic rule ──► held candidate (`verifying`, invisible to the family)
   │                            │
   │                            ├─ SAM 3.1: track + pixel masks for a 5–7s clip
   │                            ├─ Muse Spark: read-only evidence tool loop
   │                            │   (SAM, household memory, recent events, audio,
   │                            │    at most one still from that clip)
   │                            ▼
   │                     release only if corroborated, or fail open on the
   │                     local safety floor if verification is unavailable
   ▼
family alert (template or verified decision) ──► spoken check-in ──► escalation
   │
   └─ no trigger: raw frames stay in RAM; nothing is uploaded
```

**The verification gate.** A wall fall is first a *candidate*, not an incident.
It is stored as `verifying` and is absent from the family list, the incident
detail route and the media route. The console keeps the feed live while the
clip is cut from the in-memory buffer. SAM tracks the person and returns masks;
Muse Spark runs a bounded, tool-using evidence pass and returns
escalate/reject. On the console wall, only a verified release can page the
family. A rejection keeps an audit row and sends nothing. A failed verifier
fails **open** on the local floor: a cloud outage must not drop a possible fall.
The edge process is different by design: it pages from the local floor
immediately and never waits for a cloud model (see "The edge is optional").

**Crash-safe release.** The release claim is an atomic `verifying → releasing`
transition that stores a 120-second lease. If the API dies after the claim, the
sweep retries the row; if a page was already claimed, the durable notify phase
prevents a second page. A recovery that arrives during verification closes the
row, and a late model verdict cannot re-arm the wall's ten-minute cooldown over
the next real fall.

**The spoken check-in.** A fall starts a short question rendered by the local
OS voice: *"Margaret, it's Aegis. I noticed a fall. Are you okay? Say I'm okay,
or say help."* In the voice path, the answer is transcribed by Muse Voice
Transcribe and read by Muse Spark against the camera evidence. Affirmative
words plus a seen upright, moving body is the only path down. "I'm fine" from
the floor is a contradiction and leaves the alert standing. Silence at the
15-second deadline (configurable) escalates one step. In two live word-list
misses, Spark raised the answer to help where the deterministic classifier did
not (`evals/checkin_read.json`). The resident kiosk at `/checkin/{id}` is a
button fallback for someone who cannot speak: it posts "I'm okay" or "I need
help" literally and exposes only the prompt, timer and two answers, never
family history. It does not claim to be speech recognition.

**The camera watchdog.** The edge heartbeats every ten seconds. A camera that
has connected before and then misses 90 seconds becomes a real `camera_offline`
incident at the urgent policy floor — once per outage, not once per sweep. A
camera that never connected is an installation fact, not an alarm. The producer
is covered by tests; it is not only a policy rule waiting for a fixture.

**The edge is a separate deployment path.** The console wall holds a fall for
SAM/Muse verification; the edge process does not. It applies the same local
rules and safety floor, then pages immediately, because a real deployment must
not make a cloud model a single point of failure for the first alert. The clip
and Muse enrichment follow. The two paths share the policy floor, not a
pre-alert cloud gate.

## Meta models

| Model | What it does that nothing else does |
|---|---|
| **SAM 3.1** (`sam-3.1`) | Turns `person` into per-frame tracks and pixel masks. Those masks drive the annotated clip and the blur applied to non-resident people, and the track geometry feeds the evidence gate. |
| **Muse Spark** (`muse-spark-1.3`) | Runs the read-only Evidence Agent for wall verification and phase-2 decisions, reads ambiguous check-in answers, arbitrates audio context, writes the night note, and refreshes expiring household memory every six hours. |
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | Turns a cry or a spoken answer into text. The raw WAV is deleted after transcription; the transcript is evidence. |

The check-in question uses the local OS voice, not a model: it must work when
the uplink does not, and its wording is fixed so it cannot acquire a medical
claim. Muse Image is intentionally unused — nothing shown next to a real
incident is generated.

## Safety invariants

The model may **raise** an alarm, never lower one below the deterministic floor.

| Invariant | How it is enforced |
|---|---|
| Severity never goes below the policy floor | The only severity writer is a pure merge; the validator rejects model output below the floor |
| A model may veto a candidate, never downgrade an incident | Rejection only applies while the row is `verifying`; after release the floor bounds every write |
| Every evidence token resolves to a real packet field | The evidence gate runs before any decision row is written |
| Only a fixed action vocabulary reaches the UI | Actions are enum-checked; model text is never rendered as a button |
| No live feed reaches the reasoning path | The agent gets structured records plus at most one still from the incident clip, and every outbound image is ledgered |
| A held candidate cannot leak | `verifying`/`releasing` rows are excluded from the family list, detail and media routes |
| A crash cannot strand a fall | The release lease is swept; a stale `releasing` row is recovered and the notify-phase claim prevents a double page |
| A late verdict cannot silence the next fall | Late SAM/Muse completion passes `arm_cooldown=False` when the incident was already recovered |
| Silence is not recovery | Only a seen upright body closes an incident; absence alone does not |

## Privacy

- **Raw video does not go to a third party during quiet periods.** Quiet frames
  remain in the in-home process's memory and are never written to disk.
- **Only escalation evidence leaves.** A short clip goes to SAM with
  `expires_after: 3600` and the uploaded file is deleted immediately after the
  call; at most one still is sent to Muse, and the ledger records its bytes.
- **Redaction fails closed.** If SAM cannot prove a single-person clip, raw
  media is not signed. With more than one tracked person, every non-resident is
  blurred using SAM masks; if no redacted copy exists, the visual tool is not
  offered to the model.
- **The masks are real, not a box blur.** Current-HEAD live probe: 240/240 SAM
  masks decoded, 7 tracks, 192 mask blurs covering 503,794 pixels across 48
  frames, zero box fallbacks (`evals/meta/sam_redaction.json`).
- **Audio is event-only.** A sustained loudness event writes one WAV; only that
  WAV is transcribed. The raw audio and WAV are deleted after the assessment.
  No microphone stream is uploaded.
- **Local retention is enforced in code.** Media directories are removed after
  seven days; outbound ledger byte counts expire on their stated window; alert
  metadata is purged after 90 days.
- **No face recognition.** No identity claims, no biometric templates.
- **Pausing is absolute at the backend while it is on.** New frames, audio and
  clips are refused, check-ins are cancelled, verification releases are
  suppressed, and notifications stop. A provider call already on the wire
  cannot be recalled. The edge process may keep its local camera loop alive,
  but every ingest and alert path is closed.
- **Consent gates resume.** Revoking consent pauses monitoring; resume is
  refused until a new consent record exists.
- **Delete-all also deletes the browser copy** and pauses monitoring.
- **The console binds to loopback by default.** `npm run dev` / `npm run start`
  listen on `127.0.0.1:3000`; the API binds `127.0.0.1:8000`. Exposing either to
  a LAN requires an explicit hostname change, and family access should then be
  protected with `HOUSEHOLD_PIN` and `SESSION_SECRET`.
- **A resident kiosk link is signed and short-lived.** With a PIN set, the
  alert page includes a 30-minute resident link; possession of the link is the
  resident credential, and the kiosk exposes no family data.
- **Turning push off removes the subscription server-side**, so a later alert
  cannot target a dead endpoint.

## Measured results

All artifacts are in `evals/`. The pilot is a 110-clip external corpus
(GMDCSA24, three actors/rooms), not a field trial. Labels come from the dataset
description: 57 floor falls, 5 bed falls, 48 ADL clips.

| Claim | Result | Artifact / command |
|---|---|---|
| Backend regression suite | **233 / 233 passed** | `python -m pytest -q` |
| Web typecheck + production build | **clean** | `npx tsc --noEmit`, `npm run build` |
| Decision fixtures against Muse Spark | see `evals/report.md` (fresh run) | `python scripts/run_evals.py --runs 1` |
| Floor falls that produced a wall candidate | **49 / 57** at 2 fps (person-lost recheck; was 45/57) | `evals/fall_pilot_recheck.json` |
| Floor falls that paged the family after the full SAM + Muse gate | **49 / 57 (86%)**; 45 verified before the recheck, 3 of the 4 rescued verified and 1 failed open on the local floor. All 45 pre-recheck candidates were already escalate. | `evals/gate_pilot.json`, `evals/gate_recheck_rescued.json` |
| Missed floor falls that produced a candidate at 8 fps | **3 / 12** | `evals/fall_pilot_8fps.json` |
| ADL clips that paged the family | **2 / 48** after the bed-fall fix (2 of 5 candidates escalated, 3 rejected). Before the fix: 0 / 48. | `evals/gate_targeted_after_fix.json` |
| Bed-fall clips that paged the family | **2 / 5** after the bed-fall fix (both candidates escalated; 3 had no candidate). Before the fix: 0 / 5. | `evals/gate_targeted_after_fix.json` |
| SAM mask decode + redaction at current HEAD | **240 / 240 masks, 7 tracks, 192 mask blurs, 503,794 pixels, 0 box fallbacks** | `evals/meta/sam_redaction.json` |
| Check-in de-escalation suite (real ASR output) | **5 / 5 cases** | `evals/checkin.md` |
| Spark check-in reading on word-list misses | **2 / 2 raised to help** | `evals/checkin_read.json` |
| Privacy properties | **10 / 10**; idle egress 0 bytes in 3 s; every ledger row has retention | `evals/privacy.json` |
| Wall cost, external footage | **91–96 ms/frame** dynamic; reused frame **0.2–0.5 ms median** (first frame pays full inference); 4-feed ceiling **2.6–2.7 fps/feed** in the recorded runs | `evals/performance.md`, `evals/performance.json` |
| Verification state machine | held rows hidden; release lease retry; late verdict does not re-arm cooldown | `tests/test_verification_lifecycle.py`, `tests/test_wall.py` |
| Camera watchdog | one urgent incident per outage; never-connected cameras ignored | `tests/test_camera_watchdog.py` |

The earlier 107 ms and 126 ms wall measurements were taken on the same machine
under different CPU load; a strict review run measured **121 ms/frame**. The
benchmark is wall-clock and machine-specific; use the **~92–126 ms** range for
planning. The browser console is slower than the benchmark because it also
encodes, uploads and sleeps between samples; the console displays its own
measured rate per feed instead of claiming the backend number.

## Limitations

- **Not a medical device and not an emergency service.** It notifies family; it
  does not dispatch anyone, diagnose, or monitor vitals.
- **The fall pilot is small and not the target study.** It is 110 clips across
  three actors/rooms. The person-lost recheck lifted floor-fall candidates from
  45/57 to 49/57 with no new ADL candidates; 8 floor falls still produce no
  candidate — most are people the camera genuinely cannot see after the fall
  (occluded or out of frame). The bed-fall fix catches the two bed-fall
  candidates that the gate used to reject, but it also pages two of the 48 ADL
  clips where someone is lying on a bed: SAM geometry alone cannot always tell
  "fell onto the bed" from "lay down on the bed." Recall and restraint will
  differ in a real home.
- **The check-in suite is five clean synthetic utterances.** It is a regression
  suite for the de-escalation table, not an ASR benchmark in a noisy room.
- **Audio distress is a heuristic**, not a medical classifier. It combines a
  transcript, word signals and loudness; it still needs a multilingual,
  noisy-room evaluation and a microphone-placement policy.
- **The object vocabulary is small and sampled.** COCO classes are context, not
  safety-grade scene understanding; the person check remains the primary
  evidence.
- **Night presence is not a burglar alarm.** It reports a second tracked person
  after dark with the door state; it does not identify anyone or assess intent.
- **Restlessness is a movement heuristic**, not a diagnosis. The message never
  names a cause.
- **Camera loss is detected only after a camera has connected.** The watchdog
  does not page for a camera that was never installed, and it cannot see a
  camera that fails before its first heartbeat.
- **The demo input is licensed surveillance footage** replayed through the live
  pipeline and labelled `REPLAY`. The pipeline is real; the input is licensed
  and is not redistributed in this repository.
- **Door and stove sensors in replay mode are simulated** and labelled as such.

## Run it

**Prerequisites:** Python 3.11+, Node 20+, `ffmpeg` on `PATH`, and the two YOLO
weights (`yolov8n.pt`, `yolov8n-pose.pt`) in the repository root.

```bash
cp .env.example .env              # set META_API_KEY; add VAPID keys for push
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/serve.py           # API on 127.0.0.1:8000

cd web
npm install
npm run dev                       # console on 127.0.0.1:3000
```

Open `http://localhost:3000/setup`, add up to six feeds (a recording, a browser
camera, or a lab clip), map each feed, review Muse's proposed watch plan, then
activate. Activation requires every mapped feed and an approved configuration.
The console stores feed choices in the browser; incidents, consent and watch
decisions live in the local SQLite database.

Optional edge process:

```bash
python scripts/worker.py
PYTHONPATH=edge python -m aegis_edge --config config/edge.yaml
```

Edge audio is opt-in (`AEGIS_AUDIO=on` or `--audio`). The lab footage picker
reads `FOOTAGE_ROOT` (default `data/new-footage`).

### Tests and evidence

```bash
python -m pytest -q
cd web && npx tsc --noEmit && npm run build && cd ..
python scripts/privacy_tests.py
python scripts/bench_wall.py --mode dynamic --frames 24
python scripts/probe_redaction.py     # live SAM, requires META_API_KEY
python scripts/run_fall_pilot.py --fps 2 --json evals/fall_pilot.json
python scripts/run_gate_pilot.py --pilot evals/fall_pilot.json --out evals/gate_pilot.json
```

See `evals/README.md` for what each artifact measures, when it was generated,
and which artifacts depend on live model calls.

## Repository map

```
backend/app/     FastAPI service: policy, wall, SAM/Muse gate, check-in,
                 notifications, privacy, push, media, SQLite
edge/aegis_edge/ deployable edge agent: camera, ring buffer, state machine,
                 signed uploads and recovery
web/             Next.js console, alert detail, resident kiosk, setup flow
evals/           measured artifacts and reports
scripts/         runners for evals, probes, the API and the edge
tests/           backend regression suite
config/          edge configuration and zones
```

## Credits and licensing

- [Meta Model API](https://dev.meta.ai/docs/overview) — SAM 3.1, Muse Spark,
  Muse Voice Transcribe.
- CAVIAR (INRIA/University of Edinburgh, CC BY-SA) and URFD (Kwolek & Kepski
  2014, CC BY-NC-SA 4.0) — testing only, never redistributed.
- GMDCSA24 — external fall/ADL corpus used for the pilot; testing only.
- Ultralytics YOLO, ByteTrack, OpenCV, ffmpeg and `meta-sam-parser` per their
  respective licences.

The repository code is MIT — see [LICENSE](LICENSE). Coding assistance was
used and reviewed; the commit history is the authorship record.

---

Built in Cairo, for families who cannot be there every minute, and for parents
who do not want to be watched.
