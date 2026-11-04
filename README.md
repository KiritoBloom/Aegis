# Aegis

### A check-in before an alarm.

Your parent lives alone. You want to know when they need help. You don't want
their living room to become a livestream.

Aegis uses an ordinary camera to notice a possible fall, check what happened,
and ask the resident if they're okay. If they need help or don't respond, it
alerts the family with the evidence.

**No wearable. No continuous cloud video. A reason for every alert.**

Built solo in Cairo for the **Meta Global AI Developer Hackathon**, Indie
Developer track, October 27–November 4, 2026.

[The Muse Spark finding](#the-finding-muse-spark-can-read-motion-as-text) ·
[Run it](#run-it) · [Try the demo](#try-the-demo) ·
[Results](#what-weve-tested) · [Setup help](docs/SETUP.md)

## The finding: Muse Spark can read motion as text

**We gave Muse Spark numbers instead of images. It could still tell a fall
from someone getting up.**

SAM turns the person in a clip into a sequence of silhouettes. We shrink each
one to a **32×18 grid**, encode it as hexadecimal text, and send that sequence
to Muse Spark. That call gets **no images, no video, no tools and no prior
verdict**—just the changing masks, timestamps and an explanation of the format.

```text
Video → SAM silhouettes → tiny binary grids → hex text → Muse Spark's motion read
```

Then we tested whether the sequence mattered:

| What we sent | What Muse Spark returned |
|---|---|
| Original fall sequence | **Fall** |
| The same sequence, reversed | **Getting up** |
| The final low pose, repeated | **Uncertain: no movement** |

The frozen case matters: being low to the floor was not enough for Muse to
call it a fall. Its answer changed with the movement, not just the final pose.

Across **eight selected acted clips**, the text-only read matched **7/8 action
labels**, including all three falls. The miss was someone lowering themselves
onto a bed. Twenty-four masks contain just **1,728 bytes of occupancy data**,
before JSON and prompt overhead. SAM still receives the source video; the
text-only Muse call does not.

**You can inspect this in the app.** The expanded camera and **A deeper look**
show an image-based read beside the text-only read, with their exact inputs and
answers. When they disagree, both remain visible. These experimental reads sit
alongside the resident-first alert workflow.

[Read the experiment and its limits →](evals/packed_motion.md) ·
[Inspect the order-control responses →](evals/motion_order_control.json) ·
[See the integrated demo →](evals/motion_integration.md)

## What makes it worth building

A camera can tell you that something moved. A fall detector can tell you that
someone is down. A family needs a more useful answer: **do I need to act?**

Aegis works through that question before interrupting anyone:

- **Look at the movement.** Local detection follows the person over time, rather
  than treating every low posture as a fall.
- **Check the evidence.** SAM tracks the person through a short event clip.
  Muse considers that movement, the room, recent events and the household's care
  settings.
- **Ask the person.** A spoken check-in gives them a chance to respond. “I'm
  okay” only clears the fall when the camera also sees recovery.
- **Show the family why.** An alert includes what was observed, what was checked
  and whether the resident answered. The console keeps the actual requests,
  responses and delivery outcome available for inspection.

The project also includes night-time door rules, unattended-stove rules, audio
distress checks, a camera-offline watchdog and a morning summary. The camera
fall workflow is the clearest way to try the complete system. Door and stove
inputs in the replay demo are simulated.

## Run it

**You'll need:** 64-bit **Python 3.13**, **Node.js 22 or 24 LTS** with npm,
**ffmpeg** with ffprobe, and a current Chrome or Edge browser. Local detection
runs on the CPU; a GPU is not required. Start with one camera.

[Install the prerequisites for your OS →](docs/SETUP.md#1-install-the-prerequisites)

```bash
git clone https://github.com/KiritoBloom/Aegis.git
cd Aegis
```

**Windows — PowerShell**

```powershell
py -3.13 run.py setup --demo
```

**Linux / macOS**

```bash
python3.13 run.py setup --demo
```

This creates `.venv` and `.env`, installs the pinned Python and web dependencies,
downloads and checks both YOLO models, runs a prediction through each, and
downloads six sample clips directly from the URFD dataset website. The first
install includes PyTorch, so allow time and several GB of disk space.

Open the new **`.env`** file and fill in **`META_API_KEY`** with your
[Meta Model API](https://dev.meta.ai/docs/overview) key. It needs access to
**SAM 3.1**, **Muse Spark** and **Muse Voice Transcribe**. These are hosted
models; there are no local SAM or Muse weights to install.

Then start both services:

```powershell
py -3.13 run.py
```

On Linux/macOS, use `python3.13 run.py`. The runner opens
**http://127.0.0.1:3000/setup** when the API and console are ready. Keep the
terminal open; **Ctrl+C stops both**. No venv activation or separate worker is
needed. Logs are in `.tmp/api.log` and `.tmp/web.log`.

**No Meta key?** You can still map a camera, run local detection, approve your
own watch configuration and run the tests. SAM, Muse reviews and transcription
will be unavailable. That is a local test, not the complete model demo.

### Where are the missing files?

| Not stored in Git | How you get it |
|---|---|
| `yolov8n.pt`, `yolov8n-pose.pt` | Setup downloads the exact official release assets and verifies their SHA-256 hashes. They go in the repository root. |
| Python packages and `web/node_modules/` | Setup installs `requirements.txt` and runs `npm ci` against the committed lockfile. |
| `.env` and API credentials | Setup creates the file; you supply your own Meta key. Existing `.env` files are kept. |
| Demo recordings | `--demo` downloads six original URFD clips into `data/new-footage/`. You can also use your own recording or webcam. |
| Database, incident clips and history | Created locally as you use Aegis. A fresh checkout has no recorded incident history. |

The optional `yolov8m.pt` experiment is **not required**. The default pipeline
uses the two models installed by setup. Sample footage has its own
[non-commercial dataset terms](https://fenix.ur.edu.pl/mkepski/ds/uf.html).
Omit `--demo` to use only your own inputs.

## Try the demo

1. In **Setup → Cameras → Add lab footage**, choose **`fall-06.mp4`**.
   It is the exact public clip used in our recorded image-versus-hex demo.
2. Add a demo resident, map the camera, review the watch settings, approve the
   configuration and complete the consent step for your test setup. Without a
   Meta key, choose **Approve my configuration**.
3. Open the camera console, turn **Loop off**, and start monitoring. Expand the
   camera while the recording plays. Leave the page open after the clip ends;
   the cloud review continues after playback stops.
4. Follow the investigation: local candidate → SAM evidence → Muse review →
   resident check-in, if the candidate proceeds. Open the resident screen in a
   new tab to try its answer buttons while camera capture continues.
5. Open **A deeper look**. Inspect the protected image input, the text-only
   motion input and the two independent Muse responses.

That last comparison is deliberate. One Muse call sees a protected sequence
of images. Another sees the same person's SAM masks as hexadecimal occupancy
data, with **no image or video attached**. In our recorded run, the image read
said “fall” and the hex read said “getting up.” Both are shown. Neither changes
the main alert decision. [See the actual run and input hashes →](evals/motion_integration.md)

This is live inference over a recording, not a prerecorded UI. Timing and model
responses can vary. In-app delivery works locally; phone push, SMS and webhooks
need their own configuration. [Full walkthrough and troubleshooting →](docs/SETUP.md)

## How it works

```text
Camera / recording
    ↓
Local YOLO + pose + per-person tracking
    ↓ possible fall
Short event clip → SAM 3.1 tracks and masks
    ↓
Muse Spark checks the evidence
    ├─ rejected candidate → keep an audit record, no family alert
    └─ corroborated, or verifier unavailable → ask the resident
           ├─ okay + visible recovery → resolve
           └─ help / no answer → family alert and escalation
```

| Model | Its job |
|---|---|
| **YOLO detection + pose** · local | Detect people, estimate posture and follow movement without sending quiet frames to the cloud. |
| **SAM 3.1** · Meta API | Track the person across the event clip; provide pixel masks for movement evidence and bystander redaction. |
| **Muse Spark** · Meta API | Review evidence through bounded, read-only tools; interpret check-in answers; propose watch settings and write summaries. |
| **Muse Voice Transcribe** · Meta API | Transcribe an event-triggered audio clip or spoken response. |

The spoken question uses the local OS voice. A button-based resident screen is
also available. The optional **edge agent** is a separate deployment path: it
alerts from local rules immediately, then adds cloud evidence. The browser
console uses the resident-first flow above.

[Architecture, alert rules and privacy details →](docs/ARCHITECTURE.md)

## What leaves the house

Quiet camera frames stay in local memory. A possible event can send a short
**raw clip to SAM**; Muse receives structured evidence and bounded protected
images. The uploaded SAM file is deleted after the call and has a one-hour
expiry. Event audio is deleted after transcription and assessment.

There is no face recognition. Local incident media expires after seven days.
The console exposes outbound requests, supports pausing and deletion, and
binds to localhost by default. [Read the implementation details →](docs/ARCHITECTURE.md#privacy)

## What we've tested

| Check | Recorded result |
|---|---|
| [Fresh-checkout setup](evals/onboarding_verification.json) | Windows install, verified models and six samples, local launch/mapping/shutdown, **306 backend tests**, **7 web tests** and production build passed. |
| [Backend motion integration](evals/motion_integration.md) | **301 tests passed** at the recorded integration revision. |
| [Web integration](evals/motion_web_verification.json) | **7 tests passed**, plus production build and type validation; Next 15.5.29, React 19.1.10. |
| [Paired local tracker replay](evals/urfd_regression.md) | Candidates on **6/6 URFD originals** and **53/57 external floor falls**. Also **5/48 ADL candidates** and **0/5 bed-fall candidates**. |
| [Muse decision fixtures](evals/report.md) | **90/90 passed** across three runs of 30 fixtures. |
| [Live SAM redaction probe](evals/meta/sam_redaction.json) | **240/240 masks decoded**, with **zero box fallbacks** in that run. |

Candidate counts are not family-alert counts. The tracker change found two
additional external floor falls and also added two ADL candidates. The full
cloud/resident gate was not rerun across that corpus. These are tests on acted
footage and fixtures, not evidence of performance in occupied homes.

Run the local suite after setup with `python run.py test` (use `py -3.13` on
Windows). It checks Python dependencies, runs backend and frontend tests, and
builds the web app. No API key or dataset is needed for these tests. Stop the
app first. [All reports, dates and reproduction commands →](evals/README.md)

## Where it stands

Aegis is a working hackathon prototype. We have exercised local detection,
real Meta API calls, resident responses and local delivery. We have not
established real-home outcomes or delivery to a real phone. Occlusion, bed
falls and the difference between lying down and falling remain difficult.
It notifies family; it does not diagnose or dispatch emergency services.

## Inside the repository

| Path | Contents |
|---|---|
| [`run.py`](run.py) | Setup, start, installation checks, demo downloads and tests. |
| [`web/`](web/) | Camera console, setup, investigations, family alerts and resident screen. |
| [`backend/app/`](backend/app/) | FastAPI, local tracking, Meta integration, alert rules, privacy and SQLite. |
| [`edge/aegis_edge/`](edge/aegis_edge/) | Optional camera/microphone agent, buffering and signed uploads. |
| [`tests/`](tests/) · [`evals/`](evals/) | Regression tests and recorded experiments. |
| [`docs/SETUP.md`](docs/SETUP.md) | OS prerequisites, manual setup, configuration and troubleshooting. |

## Credits

[Meta Model API](https://dev.meta.ai/docs/overview) ·
[Ultralytics YOLO](https://github.com/ultralytics/ultralytics) · OpenCV · ffmpeg ·
`meta-sam-parser`. Evaluation footage comes from URFD (Kwolek & Kepski, 2014),
CAVIAR and GMDCSA24; original recordings remain outside Git and retain their
dataset licences. The project code is [MIT licensed](LICENSE); model weights,
dependencies and datasets retain their own licences.

Built for families who cannot be there every minute, and parents who still
want a home of their own.
