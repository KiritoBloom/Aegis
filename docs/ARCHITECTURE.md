# How Aegis makes an alert

The browser console is the main demo path. An optional edge agent supports a
separate deployment mode. Both use local rules; they differ in when the first
alert is released.

## Browser camera wall

The browser sends sampled camera or recording frames to the local FastAPI
service. YOLO detection, pose estimation and tracking run on the household
machine. A person's posture history and down timer belong to that track,
not to the whole room. Each displayed frame is paired with its own detections.

A possible fall becomes a held `verifying` row. It is absent from the family
incident list, detail and media routes. Installation diagnostics show its
progress while the camera continues running.

1. A short clip is cut from the in-memory event buffer.
2. SAM 3.1 returns tracks and pixel masks for the clip.
3. Muse Spark runs a bounded, read-only evidence loop. Available evidence
   includes movement geometry, household memory, recent events, audio context
   and protected-still inspections. Independent tool reads can run concurrently.
4. A rejected candidate leaves an audit record and sends no family alert.
5. A corroborated candidate enters the resident check-in. If the cloud verifier
   is unavailable, the local safety rule still leads to that check-in.

The resident question is spoken locally. Voice responses can be transcribed
by Muse Voice Transcribe and read by Spark; the signed resident page is a
separate button fallback. “I'm okay” plus observed physical recovery can close
the event. Help or no answer releases the family alert. The wall's no-answer
deadline is 30 seconds from the check-in. Absence from view is not recovery.

## Independent motion comparison

The expanded camera and **A deeper look** show two additional Muse calls from
the same SAM target observations:

- A protected chronological contact sheet, with room context.
- A 32×18 binary occupancy sequence, packed into hexadecimal text. That call
  has no image/video attachment, tools or access to the other call's verdict.

The UI preserves the exact requests, input hashes, responses, confidence,
latency and cited observations. Missing masks remain missing rather than
being replaced by bounding boxes. Failure on one read does not fabricate a
result for the other. Completed reads are reused; retry resumes unfinished
reads. Their results are demonstrations of motion interpretation and do not
modify the incident decision or send notifications.

Implementation: [`motion_review.py`](../backend/app/motion_review.py),
[`api.py`](../backend/app/api.py),
[`muse-comparison.tsx`](../web/components/muse-comparison.tsx).

## Rules and recovery

- Once an incident is released, model output cannot lower its severity below
  the deterministic policy floor. Candidate rejection is a separate pre-release
  operation.
- The policy validator checks evidence references and a fixed action
  vocabulary before writing the decision.
- Release uses an atomic `verifying → releasing` claim with a 120-second
  lease. A sweep recovers a stalled claim; a durable notification phase
  prevents a second page.
- A visible recovery arriving during verification can close the event. Late
  cloud results cannot re-arm the wall cooldown over a subsequent fall.
- A previously connected edge camera that misses 90 seconds of heartbeats
  creates one urgent offline incident per outage. A camera that never connected
  is treated as incomplete installation, not a new outage.

## Optional edge path

The edge process owns camera capture, a ring buffer, local rules, signed
uploads and a retry queue. It pages from the local safety floor immediately;
clip upload and cloud enrichment follow. Its check-in deadline defaults to
15 seconds and is configurable. It does not use the wall's pre-alert cloud
gate. Configuration lives in [`config/edge.yaml`](../config/edge.yaml).

The API runs background workers by default. Set `AEGIS_WORKER=off` only when
running `scripts/worker.py` separately. Do not start a duplicate worker as part
of the normal browser demo.

## Privacy

- **Quiet periods:** raw camera frames remain in local memory and are not
  uploaded or saved as a continuous recording by Aegis.
- **Event evidence:** a short raw clip goes to hosted SAM with a one-hour file
  expiry; the upload is deleted after the call. Muse receives structured data
  and protected images, including the independent motion contact sheet.
- **Bystanders:** decoded SAM masks drive blur. If the system cannot establish
  a single-person clip, raw media is not signed; a multi-person visual tool
  requires a redacted copy.
- **Audio:** event-triggered WAVs are transcribed and removed after assessment.
  There is no continuous cloud microphone stream.
- **Retention:** local incident media is removed after seven days; alert
  metadata expires after 90 days. Outbound ledger entries have retention
  windows and identify what was sent.
- **Controls:** pause refuses new ingest and suppresses releases and
  notifications. A provider request already sent cannot be recalled. Revoking
  consent pauses monitoring; resuming requires consent. Delete-all clears
  local and browser data and pauses the system.
- **Access:** the services bind to loopback. Optional household PIN/session
  protection guards family data; resident links are signed, short-lived and
  expose only the check-in. Evidence export omits signed resident/audio links.
- **Identity:** no face recognition, biometric templates or identity inference.

## Source and evidence map

| Area | Start here |
|---|---|
| Local perception and tracking | `backend/app/scene.py`, `backend/app/wall.py` |
| Policy floor and evidence validation | `backend/app/policy.py` |
| API, lifecycle and resident gate | `backend/app/api.py` |
| Meta requests | `backend/app/meta.py` |
| Privacy rendering | `backend/app/render.py` |
| Frontend investigation state | `web/lib/inspection.ts`, `web/components/investigation.tsx` |
| Tests and experiment provenance | `tests/`, [`evals/README.md`](../evals/README.md) |

Recorded reports identify their input files and tested source revision. The
external fall/ADL pilots are acted datasets, not a clinical or in-home study.

[Back to the project →](../README.md)
