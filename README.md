# Aegis

**It watches the moments that matter, and never the moments that don't.**

A privacy-first care system for an elderly parent who lives alone. A small device in the home keeps raw video inside the house, and speaks only when something actually needs you.

> **Status:** Day 1 of 9 — built 27 Oct – 4 Nov 2026 for the Meta Global AI Developer Hackathon (Indie Developer track).
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

## The models

Four models, four modalities, four jobs. The rule: remove one and a capability disappears.

| Model | Modality | The one job nothing else can do |
|---|---|---|
| **SAM 3.1** | video → geometry | Turn the noun `person` into a stable per-frame track with pixel masks — so we can answer *"did she cross the door line, and for how long?"* without training anything. The masks are then reused to redact people who did not consent to being filmed. |
| **Muse Spark** | structured judgment | Fuse care profile + track features + sensor state + recent history into one severity, one message, one rationale — under a policy floor it cannot lower. |
| **Muse Voice Transcribe** | audio → text | Let the resident answer the check-in in her own language, so "I'm fine" becomes *evidence* rather than a guess. |
| **Muse Wonder** | 7 days → prose | The weekly letter: a long-context read over a week of incidents and non-events, so a family can see what a normal week looks like. |

**Muse Image is deliberately unused.** Nothing a family sees next to a real incident should be generated. Every pixel came from their own camera.

## Safety: the model can raise the alarm, never lower it

The policy floor is deterministic code, evaluated before the model and merged after it. A fall is urgent. A night door opening for a person with a cognitive condition is at least urgent. The model may add urgency; it may not remove it.

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

- [Meta Model API](https://dev.meta.ai/docs/overview) — SAM 3.1, Muse Spark, Muse Voice Transcribe, Muse Wonder
- [UR Fall Detection Dataset](https://fenix.ur.edu.pl/mkepski/ds/uf.html) — Kwolek & Kepski, CC BY-NC-SA 4.0
- [Pexels](https://www.pexels.com/license/) — licensed footage
- Ultralytics YOLO · ByteTrack · OpenCV · ffmpeg

## License

MIT — see [LICENSE](LICENSE).

---

Built in Cairo, for the families who can't be there every minute, and the parents who don't want to be watched.
