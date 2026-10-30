# Aegis

**It watches the moments that matter, and never the moments that don't.**

A privacy-first care system for an elderly parent who lives alone. A small device in the home keeps raw video inside the house, and speaks only when something actually needs you.

> **Status:** Day 3 of 9 — built 27 Oct – 4 Nov 2026 for the Meta Global AI Developer Hackathon (Indie Developer track).
> The pipeline is in progress. **Every number in this file is labelled as a target until it is produced by `evals/report.md`.** Nothing here is claimed without a run behind it.

---

## The problem

You install a camera to check on your mother. It is always on, the footage goes to a cloud, and within a week nobody watches it. The moments that matter — the door at 3 AM, the fall in the hallway, the hot plate left on — are a sliver of the feed.

The choice on the market today is **constant surveillance or no coverage**. Both are bad, and both have been sold to millions of families.

## The idea

You do not need to watch a person to know they are okay. You need to watch **transitions**: leaving a safe zone, failing to get up, leaving a heat source unattended. Those are rare, discrete, and cheap to reason about. Everything else can stay on the device.

> **Lying down is normal on a bed, and abnormal in a corridor.**

That single sentence generalises into the whole product: posture × place × time, computed on-device, with no new model and no new API cost.

## What it watches

| | Scene | The system's job |
|---|---|---|
| **1** | **Night door** — the resident walks to the front door at 3:12 AM | Track the approach, confirm the door state, alert the family, explain why |
| **2** | **Fall** — a slow slide down the wall, no impact, she does not get up | Alert in ~3s, then confirm with the clip and the reason |
| **3** | **Stove** — a hot plate on, nobody in the kitchen | One alert after a window, not forty |
| **4** | **Nothing** — she bends to tie a shoe, the dog crosses the room, she prays | **Send nothing.** This is the feature. |
| **5** | **Night presence** — a second person is in the house after dark | Alert the family with the clip, time and door state. Aegis does not identify anyone and does not dispatch anyone. |
| **6** | **Pacing / restlessness** — repeated back-and-forth with many turns | A quiet care signal. The Evidence Agent checks it against household memory and recent events; it reports the pattern, never a diagnosis. |

The fourth row is the product. A care system that is right most of the time and annoying the rest of it gets muted — and a muted system is worse than no system.

## Why this is not another fall detector

| | Versions judges have already seen | Aegis |
|---|---|---|
| **A** | YOLO fall detector → Telegram. One model, one threshold, one channel | Edge gate + zero-shot tracking + deterministic policy floor + four-channel evidence |
| **B** | Webcam frames → LLM → "the person appears to have fallen" | The LLM never sees a frame. It sees a 4 KB structured packet, *after* the geometry exists |
| **C** | Health chatbot that gives advice | No advice, no diagnosis, no medical claim. An event notifier with a disclaimer |
| **What all three lack** | A reason **not** to alert | Silence is the core output, and it is measured |

## How it works

```
 THE HOME (trusted)                                  THE CLOUD (exception path)
┌────────────────────────────────────┐
│ camera → privacy masks → motion    │   on trigger only
│ gate → person check → tracking →   │   ──────────────────►  SAM 3.1
│ zones + posture → ring buffer      │                         (track + masks)
│                                    │   ◄──────────────────  annotated clip
│ 12s buffer lives in RAM.           │
│ Never written to disk.             │   ──────────────────►  Muse Spark
│ 0 bytes uploaded while idle.       │                         (decision + words)
└────────────────────────────────────┘   ◄──────────────────  severity, message
                  │
                  ▼  ~3 MB per escalation (1-hour expiry at the API)
          family phone · Web Push · one alert, the clip, the "why", three buttons
```

**Two-phase alerting.** Phase 1 ships a template alert plus a still in 3–7 seconds, before any enrichment. Phase 2 replaces it (same push tag, no second buzz) with the annotated clip and the agent's reasoning in 10–25s. A slow model can never delay a safety alert.

**The spoken check-in.** A fall, or a possible cry for help, starts a short question in the resident's own room: *"Margaret, it's Aegis. I noticed a fall. Are you okay? Say I'm okay, or say help."* The voice is rendered locally, so it does not wait on the uplink, and it runs **alongside** phase 1 — the first alert is never delayed by the question. Her answer is transcribed by Muse Voice Transcribe, read by Muse Spark against the camera's evidence, and then put through the same deterministic floor. Silence at the deadline escalates one step and sends a phase-3 update. The check-in can never lower an alert below the floor: if she says "I'm fine" while the camera still sees her on the floor, the alert stands. A resident-facing screen at `/checkin/{id}` offers two large buttons — **I'm okay** and **I need help** — for someone who cannot or does not want to speak; the tapped answer goes through the same classifier, perception merge and notification path. The resident screen never shows the alert history or the family's controls.

**A cry for help is not a diagnosis.** A scream or a call for help is first checked against the room: a television or radio that could explain the sound, a person in frame, a posture, and whether she is on the floor. If no media source explains it, the family is notified. If the television is on and she is in frame, Muse Spark reads the ambiguous context and may raise it to urgent; the spoken check-in then breaks the tie. If nobody is in frame and the television is on, Aegis stays quiet. It never names a medical cause — no "heart attack", no stroke — because the system is an event notifier, not a clinician.

**A second person after dark is a family check, not a burglar verdict.** The edge notices a second tracked body at night. If the household has not marked a visitor as expected, Aegis pages the family with the clip, the time and the door state. It does not recognise faces, guess intent, or dispatch anyone. A family can mark a caregiver expected for two hours or until morning; the second person is still written into the night record, but the rule stays quiet. The `Night watch` page is the morning view of that record: what crossed the door, what stayed quiet, and what Aegis could not see.

**Object context is sampled, not a second always-on model.** Every couple of seconds the edge names a small vocabulary of things in the room — bed, couch, chair, table, television, appliances, phone — and carries the latest scene with the heartbeat and any incident. The camera page shows the labels and their confidence; an incident records whether a bed or couch was actually under the person as a `support_surface`. The labels are room context for Muse and the family, not identity, and a bed merely present in the frame is not a bed under her.

**Installation is a first-class flow, not a settings form.** `/setup` adds up to six feeds (a local recording, a device camera, or a lab clip), describes every person in the house with flexible names, ages, relationships and self-reported conditions, and records the household's watch preferences. Once every feed is mapped, Muse Spark proposes a watch plan from **structured labels and questionnaire answers only** — no image, no audio and no face data. The user approves or disapproves that proposal in a modal; approving applies the modes, disapproving leaves the user's own choices in place. Activation is blocked until every feed has a local map and the configuration is approved. Reloading setup resumes at the first unfinished step.

**Mapping happens on this machine.** The setup scan posts one frame per camera to the local backend, runs the same local YOLO/pose pass used by the edge, returns labels and boxes, and keeps neither the frame nor a derivative. The browser stores a preview in memory for the duration of the page. Nothing in the mapping step calls Meta. The privacy line in the setup rail says exactly this.

**The camera wall runs the same pipeline continuously, one independent loop per feed.** `/` is the household console for up to six local feeds — video files, a device camera, or the demo clips. Press **Activate system** and every mapped feed keeps posting frames to the local backend, which decodes them in memory and tracks people, posture, support surfaces and objects as fast as this machine sustains (the measured rate is shown per feed; on this laptop it is around 1–2 fps per feed under CPU load, not a pretend 30 fps). The pose pass is load-bearing: on our own fall clip the person box detector lost the man once he was on the floor, while pose still found him at 0.75. A bed or couch under a lying person is support, not a fall. Two consecutive unsupported-lying samples over about a second raise a possible fall; the other feeds never pause.

**No frame is ever frozen.** The live tile keeps playing while a possible fall is verified; an amber state strip and the verification state appear over the live picture. Focusing a feed opens the same continuous stream with its recent local log, not a paused still. `/events` shows the family-facing incident record with the clip and the agent trace, `/logs` shows one local telemetry stream per camera, and `/settings` manages feeds, the watch plan, household context, the family contact and retention. The console replaces the old Today, camera-wall and alert-list surfaces; `/alerts/{id}` remains the full alert detail.

That finding opens a **held** `fall_suspected` incident, not a family alert. The console keeps that feed live while it collects a 5–7 second evidence clip from the in-memory buffer, sends the clip to SAM for person masks and tracking, and asks the Muse Evidence Agent to corroborate it against SAM, household memory, recent events and any audio context. Only a corroborated result releases the family alert, the spoken check-in, the no-answer escalation and the recovery evidence. The agent’s tool trace is stored and visible in Events and the focused feed. A rejected finding sends no family alert or check-in. A verification failure fails open for a possible fall: it escalates on the local floor rather than drop a fall because a cloud model was slow. One ongoing situation produces one incident; a verified feed is held out of a new alert for ten minutes. Every family-facing fall message names the resident and says they may have fallen — the deterministic copy pass runs after verification, so a model cannot leave a lock screen reading only “Possible fall”.

The SAM call is real and load-bearing on the wall: the incident ends with the 7-second clip, SAM's track/mask features, and an annotated/redacted copy. Without an alert, the frame buffer is never written to disk. The wall shows the verification state, the check-in countdown, the no-answer escalation, the SAM evidence and the family-facing decision; the resident screen is one link away. Muse is not called for quiet frames at all — only for the verification and decision of an incident. The household memory it reads is rebuilt from structured rows by Spark every six hours. The edge path remains phase-1 immediate; this verification gate is specific to the demo wall.

## The models

Three models, three modalities. The rule: remove one and a capability disappears.

| Model | Modality | The one job nothing else can do |
|---|---|---|
| **SAM 3.1** | video → geometry | Turn the noun `person` into a stable per-frame track with pixel masks — so we can answer *"did she cross the door line, and for how long?"* without training anything. The masks are then reused to redact people who did not consent to being filmed. When a check-in has a clip, SAM supplies the posture and person-count evidence the answer is read against. |
| **Muse Spark** | structured judgment + tools | Runs the Evidence Agent: a fixed, read-only tool loop over SAM evidence, household memory, recent events and audio context, then returns the verified escalation/decision. It also reads ambiguous check-in answers, decides whether a cry over a television is a person or the room, writes the short morning note, and maintains the expiring household memory. The policy floor still bounds every decision. |
| **Muse Voice Transcribe** | audio → text | Hear the cry for help and the resident's answer in her own language. Audio is deleted after transcription; the transcript is evidence, never a command. |

The check-in question itself is rendered by the local operating-system voice, not by a model: the
question must be available when the network is not, and its wording is deliberately fixed so it
cannot acquire a medical claim. Muse Spark writes the *reading* of the answer and the family-facing
decision, which are the parts that need judgment.

**Muse Image is deliberately unused.** Nothing a family sees next to a real incident should be generated. Every pixel came from their own camera.

## Safety: the model can raise the alarm, never lower it

The policy floor is deterministic code, evaluated before the model and merged after it. A fall is urgent. A night door opening for a person with a cognitive condition is at least urgent. The model may add urgency; it may not lower an admitted incident.

The wall’s Evidence Agent can decline to admit a candidate before it becomes an incident: that is how a false camera finding is rejected without paging the family. A completed rejection means no family alert and no check-in. A verification failure fails open for a possible fall. This distinction is deliberate: the model may veto a **candidate**, never downgrade an **incident**.

Four invariants are enforced in the hot path, not merely asserted in tests:

| # | Invariant | Enforced by |
|---|---|---|
| I1 | No severity below the policy floor | The only writer of `severity` is a pure merge function |
| I2 | Every evidence token resolves to a real packet field | Validator runs on every model output before the row is written |
| I3 | Only a fixed action vocabulary reaches the UI | Enum validation; the UI never renders model-authored text as a button |
| I4 | No raw media in the reasoning path | The agent receives a packet, never a file handle |

Read-only tools, a fixed action set, and a 20s timeout. If the model fails, times out, or returns invalid JSON, the floor plus a template still goes out.

## Privacy

- **Raw video never leaves the home.** The default state uploads zero bytes.
- **Only escalation clips leave** — ~3 MB, with `expires_after: 3600` on the API side, deleted immediately after parsing.
- **Privacy masks are applied at capture, before the ring buffer.** Those pixels never exist in RAM.
- **Audio is event-only and locally gated.** The edge keeps a short PCM ring in memory; a sustained loudness event writes one WAV, and only that WAV is sent for transcription. The microphone is never streamed. Enable the real path with `--audio` or `AEGIS_AUDIO=on` on a machine with a microphone. The raw audio is deleted after transcription; the ledger records that it left.
- **The spoken question is local.** The prompt is rendered on the machine that holds the media, so it works offline and adds no third party to the room at 3 AM.
- **Third-party redaction:** if more than one person is tracked, everyone except the resident is blurred in the clip the family receives. They did not consent to being filmed.
- **No face recognition.** No identity claims, no biometric templates.
- **No cameras in bathrooms or bedrooms** — enforced by placement and policy, not just settings.
- **The resident can pause, absolutely**, and the family cannot override it. There is no covert mode.
- **Every outbound request is logged**: destination, purpose, bytes, retention. The dashboard calls it *What left your home*.

**The claim we make:** raw video never leaves the home, and only escalation clips are sent. **The claim we do not make:** "nothing ever leaves your home." Clips do. The ledger shows them.

## What is measured

Every figure below is produced by a run and linked to `evals/report.md`. A row with no measurement stays blank — the README does not soften a claim, it drops it.

| Metric | Target | Measured |
|---|---|---|
| Automated regression suite | all pass | **106 / 106** |
| Wall evidence clip before a family alert | 5–7 s | **6.0–7.0 s; SAM 48–50 frames** |
| Family alerts sent before verification | 0 | **0** |
| Duplicate-person frames on the bed-sit clip | 0 | **0 after the 40% pose floor** |
| Audio event path | real WAV → Transcribe | **4.0 s WAV; floor urgent; agent used audio_context** |
| Household memory | ordinary routines only | **5 facts from door/still rows; no emergency facts** |
| Restraint rate (events seen − alerts sent) / events seen | ≥ 85% | — |
| Fall recall / precision (25 falls, 60 negatives) | ≥ 0.90 / ≥ 0.80 | — |
| Alert latency, phase 1 p50 / p95 | ≤ 8s / ≤ 20s | — |
| Enrichment latency p95 | ≤ 30s | — |
| Idle upload, 60s | **0 B** | — |
| Idle CPU, edge process | ≤ 8% | — |
| Cost per alert | ≤ $0.05 | — |
| 6-hour soak | no crash | — |

## Limitations

- **Not a medical device.** No vitals, no diagnosis, no advice.
- **Not an emergency service.** It notifies family. It does not dispatch anyone.
- **The camera input in the demo is licensed stock footage** replayed through the live pipeline, labelled `REPLAY` on the dashboard. The pipeline is real; the input is licensed.
- **Door and stove sensors in replay mode are simulated** and labelled as such.
- **Audio distress is a heuristic, not a classifier of medical events.** It combines a short transcript, word signals, and loudness; the television/radio state is a context input the edge supplies. The real path is loudness-gated (`--audio`), tested with a 4s WAV and Muse Voice Transcribe. A real deployment still needs a multilingual/noisy-room evaluation and a hardware microphone-placement policy.
- **The check-in's speech path is tested on clean synthesized speech, not a noisy room.** It is a regression suite for the de-escalation table, not an ASR benchmark. The check-in does not delay phase 1, and silence never lowers an alert below the floor.
- **The context arbiter and the answer reader are newer than the 30-fixture suite.** They were exercised end to end on an ambiguous cry and a spoken "I'm okay", but they are not yet a statistical benchmark. The 30-decision fixtures and the 5-case ASR suite remain the measured artifacts.
- **Night presence is not a burglar alarm.** It reports a second tracked person after dark and the door state; it does not identify the person, assess intent, detect forced entry, or call anyone. A camera cannot see every room or every entrance. The night page says what was recorded, never that the dark was empty.
- **The object vocabulary is small and sampled.** It is COCO classes relevant to a care room, not a safety-grade scene understanding system. A missed object or a low-confidence label is possible; the model is told to treat labels as context, and the person check remains the primary evidence.
- **Restlessness is a movement heuristic, not a diagnosis.** It sees a long path, many turns and little net displacement; it cannot know why. Exercise, cooking, prayer, worry or pain can all look alike. The memory is there to stop a familiar pattern from becoming a new alert, and the message never names a cause.
- **The morning note is a summary of rows, not a guarantee.** It is generated on request by Muse Spark from the night's events; it can say what was noted and what stayed quiet, but it cannot say what the camera did not see. The page labels the model and the writing time.
- It does not identify people, and it will not tell you *who* is at the door.

## Built during the hackathon

Written 27 Oct – 4 Nov 2026. Third-party components are named, not absorbed.

| Component | Origin |
|---|---|
| Edge pipeline, state machine, posture/place rules engine | Original |
| Policy floor, merge, evidence validator | Original |
| SAM integration, track features, overlay, redaction | Original |
| Muse Spark prompt, decision schema, tool loop | Original |
| Dashboard, ledger, hosted replay | Original |
| Ultralytics YOLO, ByteTrack, OpenCV, ffmpeg, `meta-sam-parser` | Third-party (Apache-2.0 / MIT / per their licenses) |
| Stock footage (Pexels), fall test clips (URFD, CC BY-NC-SA 4.0) | Third-party, credited. **Testing only — never redistributed in this repo** |

The commit history from 27 Oct is the authorship record. Coding assistance is disclosed and was reviewed line by line.

## Credits

- [Meta Model API](https://dev.meta.ai/docs/overview) — SAM 3.1, Muse Spark, Muse Voice Transcribe
- [UR Fall Detection Dataset](https://fenix.ur.edu.pl/mkepski/ds/uf.html) — Kwolek & Kepski, CC BY-NC-SA 4.0
- [Pexels](https://www.pexels.com/license/) — licensed footage
- Ultralytics YOLO · ByteTrack · OpenCV · ffmpeg

## License

MIT — see [LICENSE](LICENSE).

---

Built in Cairo, for the families who can't be there every minute, and the parents who don't want to be watched.
